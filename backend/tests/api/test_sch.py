"""スケジュールの API の試験（設計書 6.2 の API-SCH-001〜013、5.4・5.5・7.3、9 章）。

SQLite の一時ファイルの DB と TestClient で、正常系と 401・403・404・409・422・400 のうち該当するものを確かめる。
プロジェクトの開始日は 2026-12-01（火）、カレンダーは既定の月〜金。稼働日の添字は 12-01＝0、12-02＝1、12-03＝2、
12-04（金）＝3、12-07（月）＝4、12-08＝5、12-09＝6 である。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.domain.wbs_rules import AssignmentRef
from app.infra.db import Base
from app.infra.security import generate_token, hash_token
from app.models.cmn import AuthSession, User
from app.models.eff import Assignment
from app.models.sch import Activity, ActivityDependency, ScheduleBaselineItem
from app.services.clock import get_now
from app.services.project_access import ProjectMembersTable
from app.services.schedule import (
    ActivityProgress,
    AssignmentsTableAbsent,
    LatestProgress,
    ScheduleCalculator,
    ScheduleLinks,
    no_progress,
)

NOW = datetime(2026, 11, 11, 9, 0, tzinfo=UTC)
MISSING = 9999


@pytest.fixture(autouse=True)
def fixed_now(app: FastAPI) -> Iterator[None]:
    app.dependency_overrides[get_now] = lambda: NOW
    yield
    app.dependency_overrides.pop(get_now, None)


def make_user(session: Session, login_id: str, *, is_system_admin: bool = False) -> User:
    user = User(login_id=login_id, display_name=f"{login_id}さん", password_hash="unusable",
                is_system_admin=is_system_admin, is_active=True)
    session.add(user)
    session.commit()
    return user


def login(session: Session, user: User) -> dict[str, str]:
    token = generate_token()
    session.add(AuthSession(user_id=user.id, token_hash=hash_token(token), expires_at=NOW + timedelta(hours=12),
                            created_at=NOW))
    session.commit()
    return {"Authorization": f"Bearer {token}"}


class World:
    """管理者が作ったプロジェクト（開始日 2026-12-01）と、PM・PLANNER・VIEWER・CONTRACTOR・メンバーでない利用者。
    WBS は 成果物 1.0 の下に WP 1.1 と WP 1.2。"""

    def __init__(self, client: TestClient, session: Session, code: str = "PRJ-S") -> None:
        self.client = client
        self.session = session
        self.admin = make_user(session, f"admin-{code}", is_system_admin=True)
        self.admin_h = login(session, self.admin)
        body = {"code": code, "name": "日程", "start_date": "2026-12-01", "owner_user_id": self.admin.id}
        response = client.post("/api/v1/projects", json=body, headers=self.admin_h)
        assert response.status_code == 201, response.text
        self.pid: int = response.json()["id"]
        self.h: dict[str, dict[str, str]] = {}
        self.users: dict[str, User] = {}
        for name, role in (("pm", "PM"), ("planner", "PLANNER"), ("viewer", "VIEWER"), ("contractor", "CONTRACTOR")):
            user = make_user(session, f"{name}-{code}")
            put = client.put(f"/api/v1/projects/{self.pid}/members/{user.id}", json={"roles": [role]},
                             headers=self.admin_h)
            assert put.status_code == 200, put.text
            self.users[name] = user
            self.h[name] = login(session, user)
        self.users["outsider"] = make_user(session, f"outsider-{code}")
        self.h["outsider"] = login(session, self.users["outsider"])
        self.deliverable = self.element("成果物", None, "DELIVERABLE")
        self.wp = self.element("WP1", self.deliverable, "WORK_PACKAGE")
        self.wp2 = self.element("WP2", self.deliverable, "WORK_PACKAGE")

    def element(self, name: str, parent_id: int | None, kind: str) -> int:
        response = self.client.post(f"/api/v1/projects/{self.pid}/wbs/elements",
                                    json={"parent_id": parent_id, "kind": kind, "name": name}, headers=self.h["planner"])
        assert response.status_code == 201, response.text
        return int(response.json()["id"])

    def url(self, path: str) -> str:
        return f"/api/v1/projects/{self.pid}{path}"

    def add(self, name: str, *, wp: int | None = None, expect: int = 201, **body: Any) -> dict[str, Any]:
        response = self.client.post(self.url("/activities"), json={"work_package_id": wp or self.wp, "name": name, **body},
                                    headers=self.h["planner"])
        assert response.status_code == expect, response.text
        result: dict[str, Any] = response.json()
        return result

    def depend(self, p: int, s: int, *, expect: int = 201, **extra: Any) -> dict[str, Any]:
        response = self.client.post(self.url("/dependencies"), json={"predecessor_id": p, "successor_id": s, **extra},
                                    headers=self.h["planner"])
        assert response.status_code == expect, response.text
        result: dict[str, Any] = response.json()
        return result

    def activities(self, who: str = "viewer") -> dict[int, dict[str, Any]]:
        response = self.client.get(self.url("/activities"), headers=self.h[who])
        assert response.status_code == 200, response.text
        return {a["id"]: a for a in response.json()["items"]}


@pytest.fixture()
def world(client: TestClient, db_session: Session) -> World:
    return World(client, db_session)


def chain(world: World) -> tuple[int, int, int, int]:
    """A（3 日）→ B（2 日）→ M（マイルストーン）と、依存の無い C（1 日）。"""
    a = world.add("A", duration_days=3)["id"]
    b = world.add("B", duration_days=2)["id"]
    m = world.add("M", is_milestone=True)["id"]
    c = world.add("C", duration_days=1)["id"]
    world.depend(a, b)
    world.depend(b, m)
    return a, b, m, c


def dates(activity: dict[str, Any]) -> tuple[str, str]:
    return activity["start_date"], activity["finish_date"]


class FakeAssignments:
    """割当の表の代わり（db:EFF が作る前に、担当の読み書きの口を確かめる）。"""

    def __init__(self) -> None:
        self.rows: dict[int, dict[int, Decimal | None]] = {}

    def assignments(self, session: Session, project_id: int) -> list[AssignmentRef]:
        return [AssignmentRef(a, u, h) for a, users in sorted(self.rows.items()) for u, h in sorted(users.items())]

    def replace_assignees(self, session: Session, activity_id: int, user_ids: frozenset[int]) -> None:
        kept = self.rows.get(activity_id, {})
        self.rows[activity_id] = {u: kept.get(u) for u in user_ids}

    def remove_activity(self, session: Session, activity_id: int) -> None:
        self.rows.pop(activity_id, None)


class FakeAssigned:
    """委託先の割当の活動（project_access の口の代わり）。"""

    def __init__(self, by_user: dict[int, set[int]]) -> None:
        self.by_user = by_user

    def activity_ids(self, session: Session, project_id: int, user_id: int) -> frozenset[int]:
        return frozenset(self.by_user.get(user_id, set()))


# ---------------------------------------------------------------------------
# API-SCH-001・002 活動の一覧・追加と自動の日程計算（SCH-06・09・10、5.5）
# ---------------------------------------------------------------------------


def test_add_activities_schedules_forward_and_backward(world: World) -> None:
    a, b, m, c = chain(world)
    rows = world.activities()
    assert dates(rows[a]) == ("2026-12-01", "2026-12-03")
    assert dates(rows[b]) == ("2026-12-04", "2026-12-07")  # 金曜の始業から、土日をまたいで月曜の終業まで
    assert dates(rows[m]) == ("2026-12-07", "2026-12-07")  # マイルストーンは先行の終わりの日（期間 0。SCH-09）
    assert rows[m]["is_milestone"] is True and rows[m]["duration_days"] == 0
    assert dates(rows[c]) == ("2026-12-01", "2026-12-01")
    assert [rows[i]["is_critical"] for i in (a, b, m, c)] == [True, True, True, False]
    assert [rows[i]["total_float"] for i in (a, b, m, c)] == [0, 0, 0, 4]
    assert (rows[c]["late_start"], rows[c]["late_finish"]) == ("2026-12-07", "2026-12-07")
    assert rows[b]["predecessor_ids"] == [a] and rows[b]["successor_ids"] == [m]
    assert rows[a]["wbs_code"] == "1.1" and rows[a]["work_package_name"] == "WP1"
    assert [r["sort_order"] for r in (rows[a], rows[b], rows[m], rows[c])] == [1, 2, 3, 4]


def test_list_orders_by_wbs_then_sort_order_and_pages(world: World) -> None:
    later_wp = world.add("WP2の活動", wp=world.wp2, duration_days=1)["id"]
    first = world.add("WP1の活動1", duration_days=1)["id"]
    second = world.add("WP1の活動2", duration_days=1)["id"]
    a = world.add("X", duration_days=1)["id"]
    world.depend(first, second)
    response = world.client.get(world.url("/activities"), params={"limit": 2, "offset": 1}, headers=world.h["viewer"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert [x["id"] for x in body["items"]] == [second, a]
    assert body["total"] == 4 and body["restricted"] is False
    assert [(d["predecessor_id"], d["successor_id"], d["dep_type"], d["lag_days"]) for d in body["dependencies"]] == [
        (first, second, "FS", 0)
    ]
    full = world.client.get(world.url("/activities"), headers=world.h["viewer"]).json()
    assert [x["id"] for x in full["items"]] == [first, second, a, later_wp]


def test_activity_dates_two_of_three(world: World) -> None:
    """SCH-03：開始日・期間・終了日のうち 2 つから残りを求める（RULE-CAL-001。開始は次の稼働日へ、終了は前の稼働日へ寄せる）。"""
    by_range = world.add("開始と終了", requested_start="2026-12-02", requested_finish="2026-12-08")
    assert (by_range["requested_start"], by_range["duration_days"]) == ("2026-12-02", 5)
    by_finish = world.add("終了と期間", requested_finish="2026-12-08", duration_days=2)
    assert (by_finish["requested_start"], dates(by_finish)) == ("2026-12-07", ("2026-12-07", "2026-12-08"))
    rolled = world.add("土曜の開始", requested_start="2026-12-05", duration_days=1)
    assert rolled["requested_start"] == "2026-12-07"
    milestone = world.add("日曜のマイルストーン", is_milestone=True, requested_finish="2026-12-06")
    assert (milestone["requested_start"], dates(milestone)) == ("2026-12-04", ("2026-12-04", "2026-12-04"))
    zero = world.add("期間 0", duration_days=0)
    assert zero["is_milestone"] is True

    for body in (
        {"requested_start": "2026-12-02", "requested_finish": "2026-12-08", "duration_days": 3},  # 3 つ
        {"requested_finish": "2026-12-08"},  # 終了日だけ
        {"requested_start": "2026-12-02"},  # 期間が無い
        {"requested_start": "2026-12-08", "requested_finish": "2026-12-02"},  # 終了が開始より前
        {"is_milestone": True, "duration_days": 3},
        {"is_milestone": False, "duration_days": 0},
        {"duration_days": -1},
    ):
        response = world.client.post(world.url("/activities"), json={"work_package_id": world.wp, "name": "x", **body},
                                     headers=world.h["planner"])
        assert response.status_code == 400, (body, response.text)
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_add_activity_rejections(world: World, client: TestClient) -> None:
    response = client.post(world.url("/activities"), json={"work_package_id": world.deliverable, "name": "x",
                                                           "duration_days": 1}, headers=world.h["planner"])
    assert response.status_code == 422 and response.json()["error"]["code"] == "ACTIVITY_NOT_ON_WORK_PACKAGE"
    other = World(client, world.session, code="PRJ-T")
    response = client.post(world.url("/activities"), json={"work_package_id": other.wp, "name": "x",
                                                           "duration_days": 1}, headers=world.h["planner"])
    assert response.status_code == 400
    body = {"work_package_id": world.wp, "name": "x", "duration_days": 1}
    assert client.post(world.url("/activities"), json=body, headers=world.h["viewer"]).status_code == 403
    assert client.post(world.url("/activities"), json=body, headers=world.h["contractor"]).status_code == 403
    assert client.post(world.url("/activities"), json=body).status_code == 401
    assert client.post(f"/api/v1/projects/{MISSING}/activities", json=body, headers=world.admin_h).status_code == 404
    assert client.get(world.url("/activities"), headers=world.h["outsider"]).status_code == 403
    assert client.post(world.url("/activities"), json={**body, "name": ""}, headers=world.h["planner"]).status_code == 400
    assert world.activities() == {}


def test_contractor_sees_only_assigned_activities(app: FastAPI, world: World) -> None:
    a, b, m, c = chain(world)
    app.state.project_access = ProjectMembersTable(FakeAssigned({world.users["contractor"].id: {b}}))
    body = world.client.get(world.url("/activities"), headers=world.h["contractor"]).json()
    assert [x["id"] for x in body["items"]] == [b] and body["restricted"] is True and body["dependencies"] == []
    cp = world.client.get(world.url("/schedule/critical-path"), headers=world.h["contractor"]).json()
    assert [x["activity_id"] for x in cp["activities"]] == [b] and cp["critical_path"] == [b]
    assert cp["project_finish_date"] is None and cp["restricted"] is True
    gantt = world.client.get(world.url("/gantt"), headers=world.h["contractor"]).json()
    assert [r["activity_id"] for r in gantt["rows"]] == [b] and gantt["links"] == [] and gantt["restricted"] is True


def test_contractor_dependency_ids_and_baseline_counts_use_only_visible_activities(
    app: FastAPI, world: World, client: TestClient
) -> None:
    """9.2 の注記（CR-011）：委託先には先行・後続の相手を読める活動だけ返し（API-SCH-001）、
    スケジュールベースラインの項目の数も読める活動とその WP の分だけで数える（API-SCH-012）。全て読める利用者は変わらない。"""
    a, b, m, c = chain(world)
    full = world.activities()
    assert (full[b]["predecessor_ids"], full[b]["successor_ids"]) == ([a], [m])
    saved = client.post(world.url("/schedule-baselines"), json={"name": "当初"}, headers=world.h["pm"])
    assert saved.status_code == 201, saved.text
    assert saved.json()["item_count"] == 6  # 活動 A・B・M・C と WP1・WP2

    app.state.project_access = ProjectMembersTable(FakeAssigned({world.users["contractor"].id: {b, m}}))
    seen = world.activities("contractor")
    assert sorted(seen) == sorted([b, m])
    assert (seen[b]["predecessor_ids"], seen[b]["successor_ids"]) == ([], [m])
    assert (seen[m]["predecessor_ids"], seen[m]["successor_ids"]) == ([b], [])
    listed = client.get(world.url("/schedule-baselines"), headers=world.h["contractor"]).json()
    assert [x["item_count"] for x in listed["items"]] == [3]  # 活動 B・M と、その WP1
    assert [x["item_count"] for x in client.get(world.url("/schedule-baselines"),
                                               headers=world.h["viewer"]).json()["items"]] == [6]
    assert world.activities()[b]["predecessor_ids"] == [a]


# ---------------------------------------------------------------------------
# API-SCH-003 活動を変える
# ---------------------------------------------------------------------------


def test_update_activity_reschedules_successors(world: World, client: TestClient) -> None:
    a, b, m, c = chain(world)
    response = client.patch(world.url(f"/activities/{a}"), json={"duration_days": 5, "name": "A2"},
                            headers=world.h["planner"])
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "A2" and dates(response.json()) == ("2026-12-01", "2026-12-07")
    rows = world.activities()
    assert dates(rows[b]) == ("2026-12-08", "2026-12-09")  # 先行の変更が後続へ自動で反映される（SCH-06）
    assert dates(rows[m]) == ("2026-12-09", "2026-12-09")
    assert rows[c]["total_float"] == 6

    # 終了日だけなら期間を保って開始日を動かす（SCH-03）
    moved = client.patch(world.url(f"/activities/{c}"), json={"requested_finish": "2026-12-04"}, headers=world.h["pm"])
    assert moved.status_code == 200, moved.text
    assert (moved.json()["requested_start"], moved.json()["duration_days"]) == ("2026-12-04", 1)
    # 開始日と終了日の組なら期間を求める
    ranged = client.patch(world.url(f"/activities/{c}"), json={"requested_start": "2026-12-01",
                                                                "requested_finish": "2026-12-02"}, headers=world.h["pm"])
    assert ranged.json()["duration_days"] == 2
    # マイルストーンにする・やめる
    assert client.patch(world.url(f"/activities/{c}"), json={"is_milestone": True},
                        headers=world.h["pm"]).json()["duration_days"] == 0
    assert client.patch(world.url(f"/activities/{c}"), json={"is_milestone": False},
                        headers=world.h["pm"]).status_code == 400
    back = client.patch(world.url(f"/activities/{c}"), json={"is_milestone": False, "duration_days": 2},
                        headers=world.h["pm"])
    assert back.json()["is_milestone"] is False and back.json()["duration_days"] == 2
    # 希望開始日を空にする
    cleared = client.patch(world.url(f"/activities/{c}"), json={"requested_start": None}, headers=world.h["pm"])
    assert cleared.json()["requested_start"] is None and cleared.json()["start_date"] == "2026-12-01"

    assert client.patch(world.url(f"/activities/{a}"), json={"name": None}, headers=world.h["pm"]).status_code == 400
    assert client.patch(world.url(f"/activities/{a}"), json={"duration_days": None},
                        headers=world.h["pm"]).status_code == 400
    assert client.patch(world.url(f"/activities/{MISSING}"), json={"name": "x"},
                        headers=world.h["pm"]).status_code == 404
    assert client.patch(world.url(f"/activities/{a}"), json={"name": "x"}, headers=world.h["viewer"]).status_code == 403
    other = World(client, world.session, code="PRJ-T")
    assert client.patch(other.url(f"/activities/{a}"), json={"name": "x"}, headers=other.h["pm"]).status_code == 404


# ---------------------------------------------------------------------------
# API-SCH-004 活動をまとめて変える（SCH-02）
# ---------------------------------------------------------------------------


def test_bulk_update_applies_all_or_nothing(world: World, client: TestClient) -> None:
    a = world.add("A", duration_days=3)["id"]
    b = world.add("B", duration_days=2)["id"]
    response = client.patch(world.url("/activities"), json={"changes": [
        {"activity_id": a, "duration_days": 2, "name": "A2"},
        {"activity_id": b, "predecessor_ids": [a], "requested_start": "2026-12-01"},
    ]}, headers=world.h["planner"])
    assert response.status_code == 200, response.text
    assert [x["id"] for x in response.json()["items"]] == [a, b]
    rows = world.activities()
    assert rows[a]["name"] == "A2" and dates(rows[a]) == ("2026-12-01", "2026-12-02")
    assert rows[b]["predecessor_ids"] == [a] and dates(rows[b]) == ("2026-12-03", "2026-12-04")

    # 1 件でも不正なら何も保存しない（不正は全て返す）
    bad = client.patch(world.url("/activities"), json={"changes": [
        {"activity_id": a, "name": "保存されない"},
        {"activity_id": MISSING, "duration_days": 1},
        {"activity_id": b, "assignee_user_ids": [world.users["outsider"].id]},
    ]}, headers=world.h["planner"])
    assert bad.status_code == 422, bad.text
    error = bad.json()["error"]
    assert error["code"] == "BULK_UPDATE_INVALID"
    assert [(d["index"], d["code"]) for d in error["details"]] == [(1, "ACTIVITY_NOT_FOUND"), (2, "ASSIGNEE_NOT_ASSIGNABLE")]
    assert world.activities()[a]["name"] == "A2"

    cycle = client.patch(world.url("/activities"), json={"changes": [{"activity_id": a, "predecessor_ids": [b]}]},
                         headers=world.h["planner"])
    assert cycle.status_code == 422 and cycle.json()["error"]["code"] == "SCHEDULE_CYCLE"
    assert cycle.json()["error"]["details"][0]["related_ids"] == [a, b]
    assert world.activities()[a]["predecessor_ids"] == []

    # 先行を外す
    removed = client.patch(world.url("/activities"), json={"changes": [{"activity_id": b, "predecessor_ids": []}]},
                           headers=world.h["planner"])
    assert removed.status_code == 200 and removed.json()["items"][0]["start_date"] == "2026-12-01"
    assert client.patch(world.url("/activities"), json={"changes": []}, headers=world.h["planner"]).status_code == 400
    assert client.patch(world.url("/activities"), json={"changes": [{"activity_id": a, "name": None}]},
                        headers=world.h["planner"]).status_code == 400
    assert client.patch(world.url("/activities"), json={"changes": [{"activity_id": a, "name": "x"}]},
                        headers=world.h["viewer"]).status_code == 403


def test_requested_start_before_project_start_is_422_and_writes_nothing(
    world: World, client: TestClient, db_session: Session
) -> None:
    """API-SCH-002・003・004：書いた後の希望開始日がプロジェクトの開始日（2026-12-01）より前なら 422 ``BEFORE_PROJECT_START``
    で何も書かない（5.5・CR-018）。開始日ちょうどは通す。終了日から求めた開始日も同じに判定する。
    今の値のまま（この版より前に保存された、開始日より前の希望開始日）なら、他の項目を変えても拒まない（5.5 の負の位置）。
    """
    def refused(response: Any) -> list[dict[str, Any]]:
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "BEFORE_PROJECT_START"
        details: list[dict[str, Any]] = response.json()["error"]["details"]
        return details

    add = {"work_package_id": world.wp, "name": "早い", "duration_days": 1}
    details = refused(client.post(world.url("/activities"), json={**add, "requested_start": "2026-11-30"},
                                  headers=world.h["planner"]))
    assert details == [{"loc": ["body", "requested_start"], "requested_start": "2026-11-30", "project_start": "2026-12-01",
                        "message": "希望開始日 2026-11-30 がプロジェクトの開始日 2026-12-01 より前です"}]
    # 終了日 12-02 と期間 3 から求めた開始日は 11-30（月）
    refused(client.post(world.url("/activities"), json={**add, "duration_days": 3, "requested_finish": "2026-12-02"},
                        headers=world.h["planner"]))
    assert world.activities() == {}
    a = world.add("A", duration_days=1, requested_start="2026-12-01")["id"]  # 開始日ちょうどは通す

    url = world.url(f"/activities/{a}")
    assert refused(client.patch(url, json={"requested_start": "2026-11-27"},
                                headers=world.h["planner"]))[0]["loc"] == ["body", "requested_start"]
    by_finish = refused(client.patch(url, json={"requested_finish": "2026-11-30"}, headers=world.h["planner"]))
    assert (by_finish[0]["loc"], by_finish[0]["requested_start"]) == (["body", "requested_finish"], "2026-11-30")
    assert world.activities()[a]["requested_start"] == "2026-12-01"

    b = world.add("B", duration_days=2)["id"]
    details = refused(client.patch(world.url("/activities"), json={"changes": [
        {"activity_id": a, "name": "保存されない"},
        {"activity_id": b, "requested_start": "2026-11-30"},
    ]}, headers=world.h["planner"]))
    assert [(d["index"], d["activity_id"], d["field"], d["requested_start"]) for d in details] == [
        (1, b, "requested_start", "2026-11-30")]
    rows = world.activities()
    assert (rows[a]["name"], rows[b]["requested_start"]) == ("A", None)

    # この版より前に保存された開始日より前の希望開始日（11-27）は、変えなければ他の項目を変えられる
    db_session.execute(update(Activity).where(Activity.id == a).values(requested_start=date(2026, 11, 27)))
    db_session.commit()
    kept = client.patch(url, json={"name": "A 改", "duration_days": 2}, headers=world.h["planner"])
    assert kept.status_code == 200, kept.text
    assert (kept.json()["requested_start"], kept.json()["duration_days"]) == ("2026-11-27", 2)
    bulk = client.patch(world.url("/activities"), json={"changes": [
        {"activity_id": a, "name": "A 改 2", "requested_start": "2026-11-27"}]}, headers=world.h["planner"])
    assert bulk.status_code == 200, bulk.text
    refused(client.patch(url, json={"requested_start": "2026-11-30"}, headers=world.h["planner"]))  # 別の早い値へは変えられない
    assert world.activities()[a]["requested_start"] == "2026-11-27"


def test_bulk_update_assignees(app: FastAPI, world: World, client: TestClient) -> None:
    a = world.add("A", duration_days=1)["id"]
    executor = world.users["contractor"].id
    # 割当の表（DB-EFF-001）が無かった間の口：担当を置こうとしたら止める（保存したふりをしない）。空にするのは今と同じ。
    # アプリの既定は db:EFF で表を読み書きする口に変わった（A-27）ので、表の無い口を明示して載せる
    app.state.schedule_links = ScheduleLinks(assignments=AssignmentsTableAbsent())
    assert client.patch(world.url("/activities"), json={"changes": [{"activity_id": a, "assignee_user_ids": []}]},
                        headers=world.h["planner"]).status_code == 200
    with pytest.raises(RuntimeError, match="DB-EFF-001"):
        client.patch(world.url("/activities"), json={"changes": [{"activity_id": a, "assignee_user_ids": [executor]}]},
                     headers=world.h["planner"])

    fake = FakeAssignments()
    app.state.schedule_links = ScheduleLinks(assignments=fake)
    response = client.patch(world.url("/activities"), json={"changes": [
        {"activity_id": a, "assignee_user_ids": [executor, world.users["planner"].id]}]}, headers=world.h["planner"])
    assert response.status_code == 200, response.text
    assert sorted(fake.rows[a]) == sorted([executor, world.users["planner"].id])
    listed = world.activities()[a]["assignees"]
    assert [(x["user_id"], x["display_name"]) for x in listed] == sorted(
        [(executor, "contractor-PRJ-Sさん"), (world.users["planner"].id, "planner-PRJ-Sさん")]
    )
    gantt = client.get(world.url("/gantt"), headers=world.h["viewer"]).json()
    assert sorted(gantt["rows"][0]["assignees"]) == ["contractor-PRJ-Sさん", "planner-PRJ-Sさん"]


def test_default_assignments_port_reads_and_writes_assignments_table(
    world: World, client: TestClient, db_session: Session
) -> None:
    """アプリの既定の割当の口（db:EFF。A-27）は assignments（DB-EFF-001）を読み書きする。

    一括更新（API-SCH-004）は担当を置き換え（外れた担当の行を消し、残る担当の割当工数は変えない）、一覧の担当・委託先の絞り込み
    （9.2 の注記）はその行を読み、活動を消す（API-SCH-005）と割当の行も消える。
    """
    a = world.add("A", duration_days=1)["id"]
    b = world.add("B", duration_days=1)["id"]
    contractor, planner, pm = (world.users[n].id for n in ("contractor", "planner", "pm"))

    def assign(activity_id: int, user_ids: list[int]) -> None:
        response = client.patch(world.url("/activities"), headers=world.h["planner"],
                                json={"changes": [{"activity_id": activity_id, "assignee_user_ids": user_ids}]})
        assert response.status_code == 200, response.text

    def rows() -> list[tuple[int, int, Decimal | None]]:
        db_session.expire_all()
        found = db_session.scalars(select(Assignment).order_by(Assignment.activity_id, Assignment.user_id))
        return [(r.activity_id, r.user_id, r.planned_hours) for r in found]

    assign(a, [contractor, planner])
    assert rows() == sorted([(a, contractor, None), (a, planner, None)])
    # 割当工数は API-EFF-001 が書く。ここでは表に直接置いて、担当の置き換えで残ることを確かめる
    db_session.execute(update(Assignment).where(Assignment.user_id == planner).values(planned_hours=Decimal("6")))
    db_session.commit()
    assign(a, [planner, pm])
    assert rows() == sorted([(a, planner, Decimal("6.00")), (a, pm, None)])
    listed = world.activities()[a]["assignees"]
    assert [(x["user_id"], x["planned_hours"]) for x in listed] == sorted([(planner, 6.0), (pm, None)])

    assign(b, [contractor])
    body = client.get(world.url("/activities"), headers=world.h["contractor"]).json()
    assert [x["id"] for x in body["items"]] == [b] and body["restricted"] is True

    assert client.delete(world.url(f"/activities/{a}"), headers=world.h["planner"]).status_code == 204
    assert rows() == [(b, contractor, None)]


# ---------------------------------------------------------------------------
# API-SCH-005 活動を消す
# ---------------------------------------------------------------------------


def test_delete_activity_removes_dependencies_and_keeps_baseline_copy(
    app: FastAPI, world: World, client: TestClient, db_session: Session
) -> None:
    a, b, m, c = chain(world)
    fake = FakeAssignments()
    fake.rows[a] = {world.users["contractor"].id: None}
    app.state.schedule_links = ScheduleLinks(assignments=fake)
    assert client.post(world.url("/schedule-baselines"), json={"name": "当初"}, headers=world.h["pm"]).status_code == 201
    assert client.delete(world.url(f"/activities/{a}"), headers=world.h["viewer"]).status_code == 403
    assert client.delete(world.url(f"/activities/{a}"), headers=world.h["planner"]).status_code == 204
    rows = world.activities()
    assert a not in rows and rows[b]["predecessor_ids"] == [] and dates(rows[b]) == ("2026-12-01", "2026-12-02")
    assert a not in fake.rows
    db_session.expire_all()
    assert db_session.scalars(select(ActivityDependency).where(ActivityDependency.predecessor_id == a)).all() == []
    # 写しは変えない（活動の ID も写しの値のまま。設計書 1.1 の 4.2。CR-002）
    copies = db_session.scalars(select(ScheduleBaselineItem).where(ScheduleBaselineItem.name == "A")).all()
    assert [(i.activity_id, i.start_date) for i in copies] == [(a, date(2026, 12, 1))]
    assert client.delete(world.url(f"/activities/{a}"), headers=world.h["planner"]).status_code == 404


# ---------------------------------------------------------------------------
# API-SCH-006・007 依存（SCH-04）
# ---------------------------------------------------------------------------


def test_dependencies_reject_cycles_and_reschedule(world: World, client: TestClient) -> None:
    a, b, m, c = chain(world)
    cycle = client.post(world.url("/dependencies"), json={"predecessor_id": m, "successor_id": a},
                        headers=world.h["planner"])
    assert cycle.status_code == 422, cycle.text
    assert cycle.json()["error"]["code"] == "SCHEDULE_CYCLE"
    assert cycle.json()["error"]["details"] == [{"activity_ids": [a, b, m]}]
    assert world.depend(a, a, expect=422)["error"]["code"] == "SCHEDULE_CYCLE"
    assert world.depend(a, b, expect=409)["error"]["code"] == "DUPLICATE_DEPENDENCY"
    assert world.depend(a, c, expect=422, dep_type="SS")["error"]["code"] == "UNSUPPORTED_DEPENDENCY"
    assert world.depend(a, c, expect=422, lag_days=1)["error"]["code"] == "UNSUPPORTED_DEPENDENCY"
    assert world.depend(a, MISSING, expect=400)["error"]["code"] == "VALIDATION_ERROR"
    assert client.post(world.url("/dependencies"), json={"predecessor_id": a, "successor_id": c},
                       headers=world.h["viewer"]).status_code == 403

    added = world.depend(m, c)
    assert (added["predecessor_id"], added["successor_id"], added["dep_type"], added["lag_days"]) == (m, c, "FS", 0)
    assert dates(world.activities()[c]) == ("2026-12-08", "2026-12-08")  # マイルストーンの翌稼働日の始業から（5.5）

    assert client.delete(world.url(f"/dependencies/{added['id']}"), headers=world.h["viewer"]).status_code == 403
    assert client.delete(world.url(f"/dependencies/{added['id']}"), headers=world.h["planner"]).status_code == 204
    assert dates(world.activities()[c]) == ("2026-12-01", "2026-12-01")
    assert client.delete(world.url(f"/dependencies/{added['id']}"), headers=world.h["planner"]).status_code == 404


# ---------------------------------------------------------------------------
# API-SCH-008・009 日程計算・クリティカルパス（SCH-06・10）
# ---------------------------------------------------------------------------


def test_calculate_and_critical_path(world: World, client: TestClient, db_session: Session) -> None:
    a, b, m, c = chain(world)
    db_session.execute(Activity.__table__.update().values(start_date=None, total_float=None))  # 計算の結果を消しておく
    db_session.commit()
    assert client.post(world.url("/schedule/calculate"), headers=world.h["viewer"]).status_code == 403
    response = client.post(world.url("/schedule/calculate"), headers=world.h["planner"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["project_finish_date"] == "2026-12-07" and body["critical_path"] == [a, b, m]
    assert [x["activity_id"] for x in body["activities"]] == [a, b, m, c]
    assert body["min_total_float"] == 0
    assert world.activities()[a]["start_date"] == "2026-12-01"  # 保存した

    cp = client.get(world.url("/schedule/critical-path"), headers=world.h["viewer"])
    assert cp.status_code == 200, cp.text
    assert cp.json()["critical_path"] == [a, b, m]
    assert {x["activity_id"]: x["total_float"] for x in cp.json()["activities"]} == {a: 0, b: 0, m: 0, c: 4}
    assert client.get(world.url("/schedule/critical-path"), headers=world.h["outsider"]).status_code == 403

    # DB に直接入った循環は計算せずに 422（5.5）
    db_session.add(ActivityDependency(project_id=world.pid, predecessor_id=m, successor_id=a))
    db_session.commit()
    for response in (client.post(world.url("/schedule/calculate"), headers=world.h["planner"]),
                     client.get(world.url("/schedule/critical-path"), headers=world.h["viewer"])):
        assert response.status_code == 422 and response.json()["error"]["code"] == "SCHEDULE_CYCLE"
        assert response.json()["error"]["details"] == [{"activity_ids": [a, b, m]}]


def test_calendar_change_recalculates_schedule(app: FastAPI, world: World, client: TestClient) -> None:
    """API-PRJ-008 のカレンダーの変更は、同じトランザクションで日程を計算し直す（A-19 の差し替え）。"""
    assert isinstance(app.state.schedule_recalculator, ScheduleCalculator)
    a, b, m, c = chain(world)
    calendar = {"working_weekdays": "1111111", "hours_per_day": 8, "holidays": [{"holiday_date": "2026-12-02"}]}
    response = client.put(world.url("/calendar"), json=calendar, headers=world.h["pm"])
    assert response.status_code == 200, response.text
    rows = world.activities()
    assert dates(rows[a]) == ("2026-12-01", "2026-12-04")  # 12-02 は休日、土日は稼働日
    assert dates(rows[b]) == ("2026-12-05", "2026-12-06")
    moved = client.patch(world.url(""), json={"start_date": "2026-12-03"}, headers=world.h["pm"])
    assert moved.status_code == 200, moved.text
    assert dates(world.activities()[a]) == ("2026-12-03", "2026-12-05")


# ---------------------------------------------------------------------------
# API-SCH-010 ガント（SCH-11・12・13、7.3）
# ---------------------------------------------------------------------------


def test_gantt_rows_bars_links_and_progress(app: FastAPI, world: World, client: TestClient) -> None:
    a, b, m, c = chain(world)
    response = client.get(world.url("/gantt"), headers=world.h["viewer"])
    assert response.status_code == 200, response.text
    g = response.json()
    assert [r["activity_id"] for r in g["rows"]] == [a, b, m, c]
    assert g["baseline"] is None and g["report_date"] is None and g["project_start"] == "2026-12-01"
    row = g["rows"][0]
    assert row["planned"]["start"] == {"position": 0.0, "calendar": {"date": "2026-12-01", "fraction": 0.0}}
    assert row["planned"]["end"] == {"position": 3.0, "calendar": {"date": "2026-12-03", "fraction": 1.0}}
    assert row["actual"] is None and row["baseline"] is None and row["depth"] == 2 and row["wbs_code"] == "1.1"
    milestone = g["rows"][2]["planned"]
    assert milestone["is_milestone"] is True and milestone["start"] == milestone["end"]
    assert milestone["end"]["calendar"] == {"date": "2026-12-07", "fraction": 1.0}
    assert [(x["predecessor_id"], x["successor_id"], x["from_row"], x["to_row"]) for x in g["links"]] == [
        (a, b, 0, 1), (b, m, 1, 2)]
    assert g["links"][0]["start"] == row["planned"]["end"]
    assert (g["range_start"], g["range_end"]) == ("2026-12-01", "2026-12-07")
    assert g["non_working_days"] == ["2026-12-05", "2026-12-06"]

    progress = LatestProgress(report_date=date(2026, 12, 3), activities={
        a: ActivityProgress(Decimal("50"), date(2026, 12, 1), None)})
    app.state.schedule_links = ScheduleLinks(latest_progress=lambda session, project_id: progress)
    g = client.get(world.url("/gantt"), headers=world.h["viewer"]).json()
    actual = g["rows"][0]["actual"]
    assert actual["open_ended"] is True and (actual["start_date"], actual["end_date"]) == ("2026-12-01", "2026-12-03")
    assert g["rows"][0]["percent_complete"] == 50.0 and g["report_date"] == "2026-12-03"

    assert client.get(world.url("/gantt"), headers=world.h["outsider"]).status_code == 403
    assert client.get(world.url("/gantt"), params={"baseline_id": MISSING}, headers=world.h["viewer"]).status_code == 404


def test_gantt_non_working_days_follow_display_range(world: World, client: TestClient) -> None:
    """API-SCH-010：``range_start``・``range_end`` を渡すと、その表示範囲の非稼働日を返す（設計書 1.1・CR-008）。

    応答の ``range_start``・``range_end`` はバーの範囲のまま。省いた端はバーの範囲の端。始まりが終わりより後・長すぎる範囲・
    日付でない値は 400。
    """
    chain(world)
    url = world.url("/gantt")
    wide = client.get(url, params={"range_start": "2026-11-28", "range_end": "2026-12-14"}, headers=world.h["viewer"])
    assert wide.status_code == 200, wide.text
    g = wide.json()
    assert (g["range_start"], g["range_end"]) == ("2026-12-01", "2026-12-07")
    assert g["non_working_days"] == ["2026-11-28", "2026-11-29", "2026-12-05", "2026-12-06", "2026-12-12",
                                     "2026-12-13"]
    end_only = client.get(url, params={"range_end": "2026-12-13"}, headers=world.h["viewer"]).json()
    assert end_only["non_working_days"] == ["2026-12-05", "2026-12-06", "2026-12-12", "2026-12-13"]
    reversed_range = client.get(url, params={"range_start": "2026-12-10", "range_end": "2026-12-09"},
                                headers=world.h["viewer"])
    assert reversed_range.status_code == 400 and reversed_range.json()["error"]["code"] == "VALIDATION_ERROR"
    assert [e["loc"] for e in reversed_range.json()["error"]["details"]] == [["query", "range_start"], ["query", "range_end"]]
    too_long = client.get(url, params={"range_start": "2000-01-01", "range_end": "2026-12-09"}, headers=world.h["viewer"])
    assert too_long.status_code == 400 and too_long.json()["error"]["code"] == "VALIDATION_ERROR"
    end_before_bars = client.get(url, params={"range_end": "2026-11-30"}, headers=world.h["viewer"])  # バーは 12/01 から
    assert end_before_bars.status_code == 400
    assert [e["loc"] for e in end_before_bars.json()["error"]["details"]] == [["query", "range_end"]]
    assert client.get(url, params={"range_start": "12/01"}, headers=world.h["viewer"]).status_code == 400


# ---------------------------------------------------------------------------
# API-SCH-011〜013 スケジュールベースライン（SCH-16・17・18）
# ---------------------------------------------------------------------------


def test_save_and_list_baselines(world: World, client: TestClient, db_session: Session) -> None:
    a, b, m, c = chain(world)
    db_session.execute(Activity.__table__.update().where(Activity.id == a).values(estimate_hours=Decimal("8")))
    db_session.execute(Activity.__table__.update().where(Activity.id == b).values(estimate_hours=Decimal("4")))
    db_session.commit()
    assert client.post(world.url("/schedule-baselines"), json={"name": "x"}, headers=world.h["planner"]).status_code == 403
    assert client.post(world.url("/schedule-baselines"), json={"name": ""}, headers=world.h["pm"]).status_code == 400
    first = client.post(world.url("/schedule-baselines"), json={"name": "当初"}, headers=world.h["pm"])
    assert first.status_code == 201, first.text
    body = first.json()
    assert (body["version"], body["name"], body["reason"], body["item_count"]) == (1, "当初", "MANUAL", 6)
    assert body["created_by"] == world.users["pm"].id and body["created_at"].startswith("2026-11-11T09:00:00")
    items = db_session.scalars(select(ScheduleBaselineItem).where(ScheduleBaselineItem.schedule_baseline_id == body["id"])
                               .order_by(ScheduleBaselineItem.id)).all()
    assert [(i.item_type, i.name, i.start_date, i.finish_date, i.duration_days, i.estimate_hours) for i in items] == [
        ("ACTIVITY", "A", date(2026, 12, 1), date(2026, 12, 3), 3, Decimal("8.00")),
        ("ACTIVITY", "B", date(2026, 12, 4), date(2026, 12, 7), 2, Decimal("4.00")),
        ("ACTIVITY", "M", date(2026, 12, 7), date(2026, 12, 7), 0, None),
        ("ACTIVITY", "C", date(2026, 12, 1), date(2026, 12, 1), 1, None),
        ("WORK_PACKAGE", "WP1", date(2026, 12, 1), date(2026, 12, 7), None, Decimal("12.00")),
        ("WORK_PACKAGE", "WP2", None, None, None, Decimal("0.00")),
    ]
    second = client.post(world.url("/schedule-baselines"), json={"name": "再計画"}, headers=world.h["pm"]).json()
    assert second["version"] == 2

    listed = client.get(world.url("/schedule-baselines"), headers=world.h["viewer"])
    assert listed.status_code == 200, listed.text
    assert [(x["version"], x["name"], x["item_count"]) for x in listed.json()["items"]] == [(1, "当初", 6), (2, "再計画", 6)]
    assert listed.json()["total"] == 2
    paged = client.get(world.url("/schedule-baselines"), params={"offset": 1}, headers=world.h["viewer"]).json()
    assert [x["version"] for x in paged["items"]] == [2]
    assert client.get(world.url("/schedule-baselines"), headers=world.h["outsider"]).status_code == 403

    # ガントの基準のバーは、省けば最新の版、指定すればその版
    g = client.get(world.url("/gantt"), headers=world.h["viewer"]).json()
    assert g["baseline"]["version"] == 2 and g["rows"][0]["baseline"]["start_date"] == "2026-12-01"
    g1 = client.get(world.url("/gantt"), params={"baseline_id": body["id"]}, headers=world.h["viewer"]).json()
    assert g1["baseline"] == {"id": body["id"], "version": 1, "name": "当初"}


def test_variance_against_baseline(world: World, client: TestClient, db_session: Session) -> None:
    a, b, m, c = chain(world)
    db_session.execute(Activity.__table__.update().where(Activity.id == a).values(estimate_hours=Decimal("8")))
    db_session.commit()
    baseline = client.post(world.url("/schedule-baselines"), json={"name": "当初"}, headers=world.h["pm"]).json()
    assert client.patch(world.url(f"/activities/{a}"), json={"duration_days": 5},
                        headers=world.h["planner"]).status_code == 200
    db_session.execute(Activity.__table__.update().where(Activity.id == a).values(estimate_hours=Decimal("10.5")))
    db_session.commit()
    assert client.delete(world.url(f"/activities/{c}"), headers=world.h["planner"]).status_code == 204
    d = world.add("D", duration_days=1)["id"]

    response = client.get(world.url(f"/schedule-baselines/{baseline['id']}/variance"), headers=world.h["viewer"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["baseline"] == {"id": baseline["id"], "version": 1, "name": "当初"}
    rows = [(r["status"], r["activity_id"], r["name"], r["start_variance_days"], r["finish_variance_days"],
             r["estimate_variance_hours"]) for r in body["rows"]]
    assert rows == [
        ("MATCHED", a, "A", 0, 2, 2.5),
        ("MATCHED", b, "B", 2, 2, None),
        ("MATCHED", m, "M", 2, 2, None),
        ("ADDED", d, "D", None, None, None),
        ("DELETED", c, "C", None, None, None),  # 写しは消した活動の ID を写しの値のまま持つ（設計書 1.1 の 4.2）
    ]
    assert client.get(world.url(f"/schedule-baselines/{MISSING}/variance"), headers=world.h["viewer"]).status_code == 404
    other = World(client, world.session, code="PRJ-T")
    assert client.get(other.url(f"/schedule-baselines/{baseline['id']}/variance"),
                      headers=other.h["viewer"]).status_code == 404
    assert client.get(world.url(f"/schedule-baselines/{baseline['id']}/variance"),
                      headers=world.h["outsider"]).status_code == 403


# ---------------------------------------------------------------------------
# 別の群の口の差し替え忘れ（A-21）
# ---------------------------------------------------------------------------


def test_assignments_port_is_wired_once_assignments_table_exists(app: FastAPI) -> None:
    """割当の表（assignments。DB-EFF-001）ができたら、スケジュールの API はそれを読み書きする実装を載せていること。"""
    if "assignments" in Base.metadata.tables:
        assert not isinstance(app.state.schedule_links.assignments, AssignmentsTableAbsent), (
            "assignments ができたので、app.main.create_app の app.state.schedule_links の assignments を差し替える"
        )
    else:
        assert isinstance(app.state.schedule_links.assignments, AssignmentsTableAbsent)


def test_progress_reader_is_wired_once_progress_table_exists(app: FastAPI) -> None:
    """進捗の入力の表（progress_entries。DB-PRG-002）ができたら、ガントは最新の進捗を読む実装を載せていること。"""
    if "progress_entries" in Base.metadata.tables:
        assert app.state.schedule_links.latest_progress is not no_progress, (
            "progress_entries ができたので、app.main.create_app の app.state.schedule_links の latest_progress を差し替える"
        )
    else:
        assert app.state.schedule_links.latest_progress is no_progress
