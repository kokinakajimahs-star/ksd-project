"""工数の API の試験（設計書 6.2 の API-EFF-001〜007、4.2・5.8、9 章。要求仕様書 EFF-01・02・06〜10、BR-10・11）。

SQLite の一時ファイルの DB と TestClient で、正常系と 401・403・404・409・400・422 のうち該当するものを確かめる。
プロジェクトの開始日は 2026-12-01（火）、カレンダーは既定の月〜金。見積（API-EST-001 は api:EST の範囲）は表に直接置く。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infra.db import Base
from app.infra.security import generate_token, hash_token
from app.models.chg import CorrectiveAction
from app.models.cmn import AuthSession, User
from app.models.eff import TimeEntry, VarianceNote
from app.models.sch import Activity
from app.models.wbs import WbsElement
from app.services.clock import get_now
from app.services.effort import CorrectiveActionsTableAbsent, eac_by_element

NOW = datetime(2026, 12, 10, 9, 0, tzinfo=UTC)
MISSING = 9999
ROLES = (("pm", "PM", None), ("planner", "PLANNER", "企画部"), ("executor", "EXECUTOR", "開発部"),
         ("progress", "PROGRESS_MANAGER", None), ("viewer", "VIEWER", None), ("contractor", "CONTRACTOR", "協力会社"))
VALUE_KEYS = ("planned", "baseline", "actual", "etc", "eac")


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
    """管理者が作ったプロジェクト（開始日 2026-12-01）と 6 つのロールのメンバー・メンバーでない利用者。

    WBS は 成果物 D の下に WP1（活動 A：5 日・B：3 日）と WP2（活動なし）。
    """

    def __init__(self, client: TestClient, session: Session, code: str = "PRJ-E") -> None:
        self.client = client
        self.session = session
        self.admin = make_user(session, f"admin-{code}", is_system_admin=True)
        self.admin_h = login(session, self.admin)
        body = {"code": code, "name": "工数", "start_date": "2026-12-01", "owner_user_id": self.admin.id}
        response = client.post("/api/v1/projects", json=body, headers=self.admin_h)
        assert response.status_code == 201, response.text
        self.pid: int = response.json()["id"]
        self.h: dict[str, dict[str, str]] = {}
        self.users: dict[str, User] = {}
        for name, role, organization in ROLES:
            user = make_user(session, f"{name}-{code}")
            put = client.put(f"/api/v1/projects/{self.pid}/members/{user.id}",
                             json={"roles": [role], "organization": organization}, headers=self.admin_h)
            assert put.status_code == 200, put.text
            self.users[name] = user
            self.h[name] = login(session, user)
        self.users["outsider"] = make_user(session, f"outsider-{code}")
        self.h["outsider"] = login(session, self.users["outsider"])
        self.d = self.element("成果物", None, "DELIVERABLE")
        self.wp1 = self.element("WP1", self.d, "WORK_PACKAGE")
        self.wp2 = self.element("WP2", self.d, "WORK_PACKAGE")
        self.a = self.add("A", duration_days=5)
        self.b = self.add("B", duration_days=3)

    def uid(self, name: str) -> int:
        return self.users[name].id

    def element(self, name: str, parent_id: int | None, kind: str) -> int:
        response = self.client.post(self.url("/wbs/elements"), json={"parent_id": parent_id, "kind": kind, "name": name},
                                    headers=self.h["planner"])
        assert response.status_code == 201, response.text
        return int(response.json()["id"])

    def add(self, name: str, **body: Any) -> int:
        response = self.client.post(self.url("/activities"), json={"work_package_id": self.wp1, "name": name, **body},
                                    headers=self.h["planner"])
        assert response.status_code == 201, response.text
        return int(response.json()["id"])

    def url(self, path: str) -> str:
        return f"/api/v1/projects/{self.pid}{path}"

    def estimate(self, activity_id: int, hours: str) -> None:
        self.session.expire_all()
        activity = self.session.get(Activity, activity_id)
        assert activity is not None
        activity.estimate_hours = Decimal(hours)
        self.session.commit()

    def own_estimate(self, element_id: int, hours: str) -> None:
        self.session.expire_all()
        element = self.session.get(WbsElement, element_id)
        assert element is not None
        element.own_estimate_hours = Decimal(hours)
        self.session.commit()

    def assign(self, activity_id: int, rows: list[dict[str, Any]], who: str = "planner", expect: int = 200) -> Any:
        response = self.client.put(self.url(f"/activities/{activity_id}/assignments"), json={"assignments": rows},
                                   headers=self.h[who])
        assert response.status_code == expect, response.text
        return response.json()

    def enter(self, who: str, entries: list[dict[str, Any]], expect: int = 200) -> Any:
        response = self.client.put("/api/v1/time-entries", json={"entries": entries}, headers=self.h[who])
        assert response.status_code == expect, response.text
        return response.json()

    def report(self, report_date: str) -> int:
        response = self.client.post(self.url("/status-reports"), json={"report_date": report_date}, headers=self.h["pm"])
        assert response.status_code == 201, response.text
        return int(response.json()["id"])

    def progress(self, report_id: int, activity_id: int, **body: Any) -> None:
        response = self.client.put(self.url(f"/status-reports/{report_id}/progress/{activity_id}"), json=body,
                                   headers=self.h["progress"])
        assert response.status_code == 200, response.text

    def baseline(self) -> int:
        response = self.client.post(self.url("/schedule-baselines"), json={"name": "基準"}, headers=self.h["pm"])
        assert response.status_code == 201, response.text
        return int(response.json()["id"])


@pytest.fixture()
def world(client: TestClient, db_session: Session) -> World:
    return World(client, db_session)


def error_code(response: Any) -> str:
    code: str = response.json()["error"]["code"]
    return code


def cell(activity_id: int, work_date: str, hours: float, note: str | None = None) -> dict[str, Any]:
    return {"activity_id": activity_id, "work_date": work_date, "hours": hours, "note": note}


# ---------------------------------------------------------------------------
# API-EFF-001・002 割当（EFF-01・WBS-10）
# ---------------------------------------------------------------------------


def test_replace_assignments_compares_total_with_estimate(world: World, client: TestClient) -> None:
    world.estimate(world.a, "40")
    body = world.assign(world.a, [{"user_id": world.uid("executor"), "planned_hours": 30},
                                  {"user_id": world.uid("contractor"), "planned_hours": 20}])
    assert [(r["user_id"], r["planned_hours"]) for r in body["assignments"]] == [
        (world.uid("executor"), 30.0), (world.uid("contractor"), 20.0)]
    assert (body["estimate_hours"], body["planned_hours_total"], body["difference_hours"]) == (40.0, 50.0, 10.0)
    assert body["assignments"][0]["display_name"] == "executor-PRJ-Eさん" and body["assignments"][0]["activity_name"] == "A"

    # 載せなかった担当は外し、残る担当の割当工数を変える。割当工数の空は「決めていない」
    body = world.assign(world.a, [{"user_id": world.uid("executor"), "planned_hours": 25},
                                  {"user_id": world.uid("planner"), "planned_hours": None}])
    assert {r["user_id"]: r["planned_hours"] for r in body["assignments"]} == {
        world.uid("executor"): 25.0, world.uid("planner"): None}
    assert (body["planned_hours_total"], body["difference_hours"]) == (25.0, -15.0)
    # 見積が空の活動は差を出さない
    assert world.assign(world.b, [{"user_id": world.uid("contractor"), "planned_hours": 8}])["difference_hours"] is None
    # 空の配列は担当を全て外す
    assert world.assign(world.b, [])["assignments"] == []

    url = world.url(f"/activities/{world.a}/assignments")
    for rows, field in (
        ([{"user_id": world.uid("outsider")}], "user_id"),  # メンバーでない
        ([{"user_id": world.uid("executor")}, {"user_id": world.uid("executor")}], "user_id"),  # 重複
        ([{"user_id": world.uid("executor"), "planned_hours": -1}], "planned_hours"),
        ([{"user_id": world.uid("executor"), "planned_hours": 1.005}], "planned_hours"),
    ):
        response = client.put(url, json={"assignments": rows}, headers=world.h["planner"])
        assert response.status_code == 400 and error_code(response) == "VALIDATION_ERROR", response.text
        assert response.json()["error"]["details"][-1]["loc"][-1] == field
    assert client.put(url, json={"assignments": []}, headers=world.h["executor"]).status_code == 403
    assert client.put(world.url(f"/activities/{MISSING}/assignments"), json={"assignments": []},
                      headers=world.h["planner"]).status_code == 404
    assert client.put(url, json={"assignments": []}).status_code == 401
    # 拒んだ書き込みは何も変えていない
    listed = client.get(world.url("/assignments"), params={"activity_id": world.a}, headers=world.h["viewer"]).json()
    assert {r["user_id"] for r in listed["items"]} == {world.uid("executor"), world.uid("planner")}


def test_list_assignments_filters_and_contractor_scope(world: World, client: TestClient) -> None:
    world.assign(world.a, [{"user_id": world.uid("executor"), "planned_hours": 10},
                           {"user_id": world.uid("planner"), "planned_hours": 5}])
    world.assign(world.b, [{"user_id": world.uid("contractor"), "planned_hours": 8}])
    listed = client.get(world.url("/assignments"), headers=world.h["viewer"])
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["total"] == 3
    assert [(r["activity_id"], r["user_id"]) for r in body["items"]] == sorted(
        [(world.a, world.uid("executor")), (world.a, world.uid("planner")), (world.b, world.uid("contractor"))],
        key=lambda p: (p[0] != world.a, p[1]))
    assert [r["wbs_code"] for r in body["items"]] == ["1.1.1", "1.1.1", "1.1.2"]  # 活動の番号（CR-028）
    by_user = client.get(world.url("/assignments"), params={"user_id": world.uid("executor")}, headers=world.h["pm"]).json()
    assert [(r["activity_id"], r["planned_hours"]) for r in by_user["items"]] == [(world.a, 10.0)]
    by_activity = client.get(world.url("/assignments"), params={"activity_id": world.b}, headers=world.h["pm"]).json()
    assert [r["user_id"] for r in by_activity["items"]] == [world.uid("contractor")]
    paged = client.get(world.url("/assignments"), params={"limit": 1, "offset": 2}, headers=world.h["pm"]).json()
    assert len(paged["items"]) == 1 and paged["total"] == 3
    # 委託先は自分の割当の活動の分だけ（9.2 の注記）
    own = client.get(world.url("/assignments"), headers=world.h["contractor"]).json()
    assert [(r["activity_id"], r["user_id"]) for r in own["items"]] == [(world.b, world.uid("contractor"))]
    assert own["items"][0]["wbs_code"] == "1.1.2"  # 番号は読めない活動も数える
    assert client.get(world.url("/assignments"), headers=world.h["outsider"]).status_code == 403
    assert client.get(f"/api/v1/projects/{MISSING}/assignments", headers=world.admin_h).status_code == 404


# ---------------------------------------------------------------------------
# API-EFF-003 実績工数を書く（EFF-02・RULE-EFF-005）
# ---------------------------------------------------------------------------


def stored(session: Session, user_id: int) -> list[tuple[int, str, float, str | None]]:
    session.expire_all()
    rows = session.scalars(select(TimeEntry).where(TimeEntry.user_id == user_id)
                           .order_by(TimeEntry.work_date, TimeEntry.activity_id))
    return [(r.activity_id, r.work_date.isoformat(), float(r.hours), r.note) for r in rows]


def test_put_time_entries_writes_own_assigned_cells(world: World, client: TestClient, db_session: Session) -> None:
    world.assign(world.a, [{"user_id": world.uid("executor")}])
    me = world.uid("executor")
    body = world.enter("executor", [cell(world.a, "2026-12-01", 7.5, "設計"), cell(world.a, "2026-12-02", 8)])
    assert body["changed"] == 2 and body["user_id"] == me
    assert [(e["work_date"], e["hours"], e["note"], e["wbs_code"], e["project_id"]) for e in body["entries"]] == [
        ("2026-12-01", 7.5, "設計", "1.1.1", world.pid), ("2026-12-02", 8.0, None, "1.1.1", world.pid)]
    assert body["day_totals"] == [{"work_date": "2026-12-01", "hours": 7.5}, {"work_date": "2026-12-02", "hours": 8.0}]
    # 同じ値で送り直しても変えない。0 は行を消す。メモだけの変更も書く
    assert world.enter("executor", [cell(world.a, "2026-12-01", 7.5, "設計")])["changed"] == 0
    assert world.enter("executor", [cell(world.a, "2026-12-02", 0), cell(world.a, "2026-12-01", 7.5, "レビュー")])[
        "changed"] == 2
    assert stored(db_session, me) == [(world.a, "2026-12-01", 7.5, "レビュー")]

    # 自分の割当でない活動は 403（何も書かない）
    rejected = client.put("/api/v1/time-entries", json={"entries": [cell(world.a, "2026-12-03", 1), cell(world.b, "2026-12-03", 1)]},
                          headers=world.h["executor"])
    assert rejected.status_code == 403 and error_code(rejected) == "FORBIDDEN"
    assert [(d["code"], d["activity_id"]) for d in rejected.json()["error"]["details"]] == [("NOT_ASSIGNED", world.b)]
    assert stored(db_session, me) == [(world.a, "2026-12-01", 7.5, "レビュー")]
    # effort.enter_own を持たない（閲覧者）は 403、無い活動は 404、認証なしは 401
    assert client.put("/api/v1/time-entries", json={"entries": [cell(world.a, "2026-12-01", 1)]},
                      headers=world.h["viewer"]).status_code == 403
    assert client.put("/api/v1/time-entries", json={"entries": [cell(MISSING, "2026-12-01", 1)]},
                      headers=world.h["executor"]).status_code == 404
    assert client.put("/api/v1/time-entries", json={"entries": [cell(world.a, "2026-12-01", 1)]}).status_code == 401
    assert client.put("/api/v1/time-entries", json={"entries": []}, headers=world.h["executor"]).status_code == 400


def test_put_time_entries_gives_contractor_same_404_for_hidden_and_missing_activities(
    world: World, client: TestClient, db_session: Session
) -> None:
    """API-EFF-003（9.2 の注記。CR-011）：委託先に見えない活動（自分の割当でない）は、無い活動と同じ 404
    （``code``・``message``・``details`` が同じ形。自分の割当かの 403 より先）。全て読める利用者の 403 は変わらない。"""
    world.assign(world.a, [{"user_id": world.uid("contractor")}])
    me = world.uid("contractor")

    def put(who: str, *cells: dict[str, Any]) -> Any:
        return client.put("/api/v1/time-entries", json={"entries": list(cells)}, headers=world.h[who])

    hidden, missing = put("contractor", cell(world.b, "2026-12-03", 1)), put("contractor", cell(MISSING, "2026-12-03", 1))
    assert (hidden.status_code, missing.status_code) == (404, 404)
    assert hidden.json()["error"] == {**missing.json()["error"], "details": [{"activity_ids": [world.b]}]}
    assert missing.json()["error"] == {"code": "NOT_FOUND", "message": "活動が見つかりません",
                                       "details": [{"activity_ids": [MISSING]}]}
    # 見える活動と混ぜても、見えない活動は無い活動と並べて 404（何も書かない）
    mixed = put("contractor", cell(world.a, "2026-12-03", 1), cell(world.b, "2026-12-03", 1), cell(MISSING, "2026-12-03", 1))
    assert mixed.status_code == 404 and mixed.json()["error"]["details"] == [{"activity_ids": [world.b, MISSING]}]
    assert stored(db_session, me) == []
    assert world.enter("contractor", [cell(world.a, "2026-12-03", 2)])["changed"] == 1

    # 全て読める利用者（実行担当者）の自分の割当でない活動は 403 NOT_ASSIGNED のまま
    executor = put("executor", cell(world.b, "2026-12-03", 1))
    assert executor.status_code == 403 and error_code(executor) == "FORBIDDEN"
    assert [d["code"] for d in executor.json()["error"]["details"]] == ["NOT_ASSIGNED"]


def test_put_time_entries_gives_non_member_same_404_as_missing_activity(
    world: World, client: TestClient, db_session: Session
) -> None:
    """API-EFF-003（6.1・CR-019）：メンバーでない（システム管理者でもない）人が他のプロジェクトの活動を指すと、無い活動と同じ
    404（``code``・``message``・``details`` の形が同じで、プロジェクトの ID を載せない。活動の有無を明かさない）。
    メンバーでないシステム管理者は読めるので 404 にしない（今どおり）。読めるプロジェクトで effort.enter_own を持たない閲覧者は 403。"""
    other = World(client, db_session, code="PRJ-E2")
    other.assign(other.a, [{"user_id": other.uid("executor")}])
    world.assign(world.a, [{"user_id": world.uid("executor")}])
    me = world.uid("executor")

    def put(headers: dict[str, str], *cells: dict[str, Any]) -> Any:
        return client.put("/api/v1/time-entries", json={"entries": list(cells)}, headers=headers)

    foreign, missing = put(world.h["executor"], cell(other.a, "2026-12-03", 1)), put(world.h["executor"],
                                                                                  cell(MISSING, "2026-12-03", 1))
    assert (foreign.status_code, missing.status_code) == (404, 404)
    assert foreign.json()["error"] == {**missing.json()["error"], "details": [{"activity_ids": [other.a]}]}
    # 自分の活動・無い活動と混ぜても、他のプロジェクトの活動は無い活動と並べて 404（何も書かない）
    mixed = put(world.h["executor"], cell(world.a, "2026-12-03", 1), cell(other.a, "2026-12-03", 1),
                cell(MISSING, "2026-12-03", 1))
    assert mixed.status_code == 404 and mixed.json()["error"]["details"] == [{"activity_ids": [other.a, MISSING]}]
    assert stored(db_session, me) == []
    # どのプロジェクトのメンバーでもない人も同じ
    outsider = put(world.h["outsider"], cell(world.a, "2026-12-03", 1))
    assert outsider.status_code == 404 and outsider.json()["error"]["details"] == [{"activity_ids": [world.a]}]

    # 読めるプロジェクト：メンバーでないシステム管理者は 404 にならない（今どおり）。閲覧者は effort.enter_own を持たない 403
    assert put(world.admin_h, cell(world.a, "2026-12-03", 1)).status_code == 200
    viewer = put(world.h["viewer"], cell(world.a, "2026-12-03", 1))
    assert viewer.status_code == 403 and viewer.json()["error"]["details"] == [{"project_id": world.pid}]
    assert world.enter("executor", [cell(world.a, "2026-12-03", 2)])["changed"] == 1


def test_put_time_entries_rejects_invalid_values_and_day_total_over_24(world: World, client: TestClient,
                                                                        db_session: Session) -> None:
    world.assign(world.a, [{"user_id": world.uid("executor")}])
    world.assign(world.b, [{"user_id": world.uid("executor")}])
    me = world.uid("executor")
    for entries, code in (
        ([cell(world.a, "2026-12-01", -1)], "NEGATIVE_HOURS"),
        ([cell(world.a, "2026-12-01", 1.005)], "INVALID_HOURS"),
        ([cell(world.a, "2026-12-01", 1), cell(world.a, "2026-12-01", 2)], "DUPLICATE"),
        ([cell(world.a, "2026-12-01", 0, "メモ")], "INVALID_HOURS"),
    ):
        response = client.put("/api/v1/time-entries", json={"entries": entries}, headers=world.h["executor"])
        assert response.status_code == 400 and error_code(response) == "VALIDATION_ERROR", response.text
        assert code in [d["code"] for d in response.json()["error"]["details"]]
    # 1 日の合計 24 時間を超えると 422（EFF-02 の受入基準）。ちょうど 24 は通す
    over = client.put("/api/v1/time-entries", json={"entries": [cell(world.a, "2026-12-01", 16), cell(world.b, "2026-12-01", 8.01)]},
                      headers=world.h["executor"])
    assert over.status_code == 422 and error_code(over) == "DAY_TOTAL_EXCEEDED"
    assert over.json()["error"]["details"][0]["work_date"] == "2026-12-01"
    assert stored(db_session, me) == []
    assert world.enter("executor", [cell(world.a, "2026-12-01", 16), cell(world.b, "2026-12-01", 8)])["day_totals"] == [
        {"work_date": "2026-12-01", "hours": 24.0}]
    # 既にある行と合わせて数える
    response = client.put("/api/v1/time-entries", json={"entries": [cell(world.b, "2026-12-01", 8.5)]},
                          headers=world.h["executor"])
    assert response.status_code == 422


def test_put_time_entries_rejects_days_through_finalized_report(world: World, client: TestClient) -> None:
    world.assign(world.a, [{"user_id": world.uid("executor")}])
    world.enter("executor", [cell(world.a, "2026-12-01", 8)])
    report = world.report("2026-12-03")
    finalized = client.post(world.url(f"/status-reports/{report}/finalize"), headers=world.h["pm"])
    assert finalized.status_code == 200, finalized.text
    for entries in ([cell(world.a, "2026-12-02", 6)], [cell(world.a, "2026-12-01", 8, "後から")],
                    [cell(world.a, "2026-12-03", 2)]):
        locked = client.put("/api/v1/time-entries", json={"entries": entries}, headers=world.h["executor"])
        assert locked.status_code == 409 and error_code(locked) == "INVALID_STATE", locked.text
        assert locked.json()["error"]["details"][0]["code"] == "LOCKED_DATE"
    # 変えないマスと、報告日の翌日以降は書ける（報告日の当日は締める。設計書 1.1・CR-005）
    assert world.enter("executor", [cell(world.a, "2026-12-01", 8), cell(world.a, "2026-12-04", 2)])["changed"] == 1


# ---------------------------------------------------------------------------
# API-EFF-004 実績工数を読む
# ---------------------------------------------------------------------------


def test_list_time_entries_own_or_effort_view(world: World, client: TestClient) -> None:
    world.assign(world.a, [{"user_id": world.uid("executor")}, {"user_id": world.uid("planner")}])
    world.enter("executor", [cell(world.a, "2026-12-01", 3), cell(world.a, "2026-12-02", 4)])
    world.enter("planner", [cell(world.a, "2026-12-01", 2)])

    def read(who: str, **params: Any) -> Any:
        return client.get("/api/v1/time-entries", params=params, headers=world.h[who])

    own = read("executor")
    assert own.status_code == 200, own.text
    assert [(e["user_id"], e["hours"]) for e in own.json()["items"]] == [(world.uid("executor"), 3.0), (world.uid("executor"), 4.0)]
    # effort.view を持つプロジェクトは全ての利用者の行
    every = read("pm", project_id=world.pid).json()
    assert [(e["work_date"], e["user_id"]) for e in every["items"]] == [  # 作業日→活動→利用者の順
        ("2026-12-01", min(world.uid("executor"), world.uid("planner"))),
        ("2026-12-01", max(world.uid("executor"), world.uid("planner"))), ("2026-12-02", world.uid("executor"))]
    assert every["total"] == 3
    ranged = read("planner", **{"from": "2026-12-02", "to": "2026-12-02"}).json()
    assert [(e["user_id"], e["hours"]) for e in ranged["items"]] == [(world.uid("executor"), 4.0)]
    by_user = read("pm", user_id=world.uid("planner")).json()
    assert [e["hours"] for e in by_user["items"]] == [2.0]
    # 他人の行は effort.view が要る。どちらの権限も無ければ 403
    assert read("executor", user_id=world.uid("planner")).status_code == 403
    assert read("viewer").status_code == 403
    assert read("outsider", project_id=world.pid).status_code == 403
    assert read("pm", project_id=MISSING).status_code == 404
    assert client.get("/api/v1/time-entries").status_code == 401


# ---------------------------------------------------------------------------
# API-EFF-005 多軸集計（EFF-07〜09。RULE-EFF-001〜003）
# ---------------------------------------------------------------------------


def plan_with_effort(world: World) -> dict[str, int]:
    """見積：A 40・B 24・WP2（活動なし）16 で基準を取り、A を 48 に見直す。割当：A を executor 30・planner 10、B は未割当。
    実績：executor が A に 12/1・12/2 に 8 時間ずつ、planner が A に 12/1 に 4 時間。報告日 12/1・12/2、12/2 の A の進捗率 50%。"""
    world.estimate(world.a, "40")
    world.estimate(world.b, "24")
    world.own_estimate(world.wp2, "16")
    baseline = world.baseline()
    world.estimate(world.a, "48")
    world.assign(world.a, [{"user_id": world.uid("executor"), "planned_hours": 30},
                           {"user_id": world.uid("planner"), "planned_hours": 10}])
    world.enter("executor", [cell(world.a, "2026-12-01", 8), cell(world.a, "2026-12-02", 8)])
    world.enter("planner", [cell(world.a, "2026-12-01", 4)])
    first = world.report("2026-12-01")
    second = world.report("2026-12-02")
    world.progress(second, world.a, percent_complete=50)
    return {"baseline": baseline, "first": first, "second": second}


def values_sum(rows: list[dict[str, Any]], key: str) -> float:
    return round(sum(r["values"][key] or 0 for r in rows), 2)


def test_summary_every_axis_sums_to_project_total(world: World, client: TestClient, db_session: Session) -> None:
    ids = plan_with_effort(world)

    def summary(**params: Any) -> Any:
        response = client.get(world.url("/effort/summary"), params=params, headers=world.h["planner"])
        assert response.status_code == 200, response.text
        return response.json()

    wbs = summary(axis="wbs")
    # 計画 48+24+16、基準 40+24+16、実績 20、ETC：A は 48×(1−0.5)=24・B は未算定で max(24−0,0)=24・WP2 は 16、EAC＝実績＋ETC
    assert wbs["total"] == {"planned": 88.0, "baseline": 80.0, "actual": 20.0, "etc": 64.0, "eac": 84.0}
    assert (wbs["report"]["id"], wbs["report_specified"], wbs["baseline"]["id"]) == (ids["second"], False, ids["baseline"])
    rows = {r["element_id"]: r for r in wbs["rows"]}
    assert [r["wbs_code"] for r in wbs["rows"]] == ["1.0", "1.1", "1.2"]  # 第 1 階層の補完（4.1 の pad_first_level の既定）
    assert rows[world.wp1]["values"] == {"planned": 72.0, "baseline": 64.0, "actual": 20.0, "etc": 48.0, "eac": 68.0}
    assert rows[world.wp2]["values"] == {"planned": 16.0, "baseline": 16.0, "actual": 0.0, "etc": 16.0, "eac": 16.0}
    assert rows[world.d]["values"] == wbs["total"]

    member = summary(axis="member")
    by_user = {r["user_id"]: r["values"] for r in member["rows"]}
    # 計画・ETC・EAC は割当工数の比（30:10）で分け、実績は入力した人。割当の無い B と WP2 は「未割当」
    assert by_user[world.uid("executor")] == {"planned": 36.0, "baseline": 30.0, "actual": 16.0, "etc": 18.0, "eac": 33.0}
    assert by_user[world.uid("planner")] == {"planned": 12.0, "baseline": 10.0, "actual": 4.0, "etc": 6.0, "eac": 11.0}
    assert by_user[None] == {"planned": 40.0, "baseline": 40.0, "actual": 0.0, "etc": 40.0, "eac": 40.0}
    organization = summary(axis="organization")
    assert {(r["bucket_kind"], r["name"]) for r in organization["rows"]} == {
        ("ORGANIZATION", "開発部"), ("ORGANIZATION", "企画部"), ("UNASSIGNED", None)}
    week = summary(axis="week")
    month = summary(axis="month")
    remaining = next(r for r in month["rows"] if r["bucket_kind"] == "REMAINING")
    assert (remaining["values"]["etc"], remaining["values"]["actual"]) == (64.0, 0.0)
    for body in (member, organization, week, month):
        for key in VALUE_KEYS:
            assert values_sum(body["rows"], key) == body["total"][key], (body["axis"], key)
        assert body["total"] == wbs["total"]
    assert any(r["bucket_kind"] == "PERIOD" and r["period_start"] == "2026-11-30" for r in week["rows"])

    # 報告日を指定すると、実績は報告日以前の分・進捗率はその報告日の入力（12/1 は入力なし＝未算定：A の ETC は 48−12）
    first = summary(axis="wbs", report_id=ids["first"])
    assert first["report_specified"] is True
    assert (first["total"]["actual"], first["total"]["etc"], first["total"]["eac"]) == (12.0, 76.0, 88.0)
    # 入力した残工数（ETC）が優先される（EFF-06）
    world.progress(ids["second"], world.a, percent_complete=50, remaining_hours=40)
    assert summary(axis="wbs")["total"]["eac"] == 100.0
    # 横断照会（API-WBS-023）が読む EAC は同じ集計
    assert eac_by_element(db_session, world.pid)[world.wp1] == Decimal("84")

    assert client.get(world.url("/effort/summary"), headers=world.h["executor"]).status_code == 403
    assert client.get(world.url("/effort/summary"), headers=world.h["viewer"]).status_code == 403
    assert client.get(world.url("/effort/summary"), params={"axis": "team"}, headers=world.h["pm"]).status_code == 400
    assert client.get(world.url("/effort/summary"), params={"baseline_id": MISSING}, headers=world.h["pm"]).status_code == 404
    assert client.get(world.url("/effort/summary"), params={"report_id": MISSING}, headers=world.h["pm"]).status_code == 404
    assert client.get(f"/api/v1/projects/{MISSING}/effort/summary", headers=world.admin_h).status_code == 404


def test_summary_for_finalized_report_keeps_percent_of_finalization(world: World, client: TestClient) -> None:
    """API-EFF-005 多軸集計：確定済みの報告日を指定すると、進捗率は確定時の値（5.8・BR-13）。後で算定項目を変えても動かない。"""
    world.estimate(world.a, "40")
    method = client.put(world.url(f"/activities/{world.a}/progress-method"), json={"progress_method": "CHECK_ITEM_RATIO"},
                        headers=world.h["pm"])
    assert method.status_code == 200, method.text
    items_url = world.url(f"/activities/{world.a}/progress-items")
    half = client.put(items_url, json={"items": [{"item_type": "CHECK", "title": "単体試験", "done": True},
                                                 {"item_type": "CHECK", "title": "結合試験"}]}, headers=world.h["pm"])
    assert half.status_code == 200 and half.json()["percent_complete"] == 50.0, half.text
    report = world.report("2026-12-01")
    finalized = client.post(world.url(f"/status-reports/{report}/finalize"), headers=world.h["pm"])
    assert finalized.status_code == 200, finalized.text

    def etc_of_a() -> float:
        response = client.get(world.url("/effort/summary"), params={"axis": "wbs", "report_id": report},
                              headers=world.h["pm"])
        assert response.status_code == 200, response.text
        return float(response.json()["total"]["etc"])

    # A は 40×(1−0.5)=20、B は見積なしで 0
    assert etc_of_a() == 20.0
    done = client.put(items_url, json={"items": [{"item_type": "CHECK", "title": "単体試験", "done": True},
                                                 {"item_type": "CHECK", "title": "結合試験", "done": True}]},
                      headers=world.h["pm"])
    assert done.status_code == 200 and done.json()["percent_complete"] == 100.0, done.text
    assert etc_of_a() == 20.0  # 確定時の 50% のまま（今の項目の 100% で求め直さない）


def test_summary_without_baseline_keeps_baseline_empty(world: World, client: TestClient) -> None:
    world.estimate(world.a, "10")
    body = client.get(world.url("/effort/summary"), headers=world.h["pm"]).json()
    assert body["baseline"] is None and body["report"] is None
    assert body["total"] == {"planned": 10.0, "baseline": None, "actual": 0.0, "etc": 10.0, "eac": 10.0}


def test_summary_puts_removed_baseline_in_its_own_bucket(world: World, client: TestClient) -> None:
    """API-EFF-005：版にあって今は無い活動・WP の基準は、どの軸でも最後の桶 BASELINE_REMOVED に置き、どの軸でも基準の合計は
    版の合計になる（5.8・EFF-09。設計書 1.1・CR-007）。"""
    world.estimate(world.a, "40")
    world.estimate(world.b, "10")
    world.own_estimate(world.wp2, "8")
    world.baseline()
    # 版の後に活動 B と WP2 を消す（CR-002：版の写しは残る）
    assert client.delete(world.url(f"/activities/{world.b}"), headers=world.h["planner"]).status_code == 204
    assert client.delete(world.url(f"/wbs/elements/{world.wp2}"), headers=world.h["planner"]).status_code == 204

    def summary(axis: str) -> Any:
        response = client.get(world.url("/effort/summary"), params={"axis": axis}, headers=world.h["pm"])
        assert response.status_code == 200, response.text
        return response.json()

    wbs = summary("wbs")
    assert wbs["total"] == {"planned": 40.0, "baseline": 58.0, "actual": 0.0, "etc": 40.0, "eac": 40.0}
    rows = wbs["rows"]
    # WP1 は版の WORK_PACKAGE の項目 50（消した B の 10 を含む）。消した WP2 の項目 8 はプロジェクトの直下の桶
    assert [(r["bucket_kind"], r["element_id"], r["parent_id"], r["values"]["baseline"]) for r in rows] == [
        ("ELEMENT", world.d, None, 50.0), ("ELEMENT", world.wp1, world.d, 50.0), ("BASELINE_REMOVED", None, None, 8.0)]
    assert rows[-1]["values"] == {"planned": 0.0, "baseline": 8.0, "actual": 0.0, "etc": 0.0, "eac": 0.0}
    assert values_sum([rows[0], rows[-1]], "baseline") == wbs["total"]["baseline"]
    for axis in ("member", "organization", "week", "month"):
        body = summary(axis)
        assert body["total"] == wbs["total"]
        # 担当者・組織・期間の軸の桶：消した B の項目 10 と、版に活動の項目を持たない消した WP2 の項目 8
        assert (body["rows"][-1]["bucket_kind"], body["rows"][-1]["values"]["baseline"]) == ("BASELINE_REMOVED", 18.0)
        assert values_sum(body["rows"], "baseline") == 58.0
        assert values_sum(body["rows"], "planned") == body["total"]["planned"]


# ---------------------------------------------------------------------------
# API-EFF-006・007 差異の分析と理由（EFF-10。RULE-EFF-004・BR-11）
# ---------------------------------------------------------------------------


def test_variance_extracts_elements_over_both_thresholds(world: World, client: TestClient) -> None:
    no_baseline = client.get(world.url("/effort/variance"), headers=world.h["pm"])
    assert no_baseline.status_code == 409 and error_code(no_baseline) == "INVALID_STATE"
    ids = plan_with_effort(world)
    # A の ETC を 40 と入力：A の EAC＝20＋40＝60（基準 40）。WP1＝60＋24＝84（基準 64、差 20・31%）、D＝100（基準 80、差 20・25%）
    world.progress(ids["second"], world.a, percent_complete=50, remaining_hours=40)
    wp3 = world.element("WP3", world.d, "WORK_PACKAGE")  # 基準の後に足した要素は「追加」

    def analyse(**params: Any) -> Any:
        response = client.get(world.url("/effort/variance"), params=params, headers=world.h["progress"])
        assert response.status_code == 200, response.text
        return response.json()

    body = analyse()
    assert (body["rate_threshold"], body["hours_threshold"], body["baseline"]["id"]) == (0.1, 8.0, ids["baseline"])
    assert [(r["element_id"], r["status"], r["baseline"], r["eac"], r["variance"]) for r in body["items"]] == [
        (world.d, "EXCEEDED", 80.0, 100.0, 20.0), (world.wp1, "EXCEEDED", 64.0, 84.0, 20.0),
        (wp3, "ADDED", None, 0.0, None)]
    assert body["items"][1]["rate"] == pytest.approx(0.3125) and body["items"][2]["rate"] is None
    # 差異が閾値以内の WP2（16 対 16）は出ない。絶対値の閾値 8 時間ちょうどは「超えていない」（差異率が超えていても出ない）
    world.progress(ids["second"], world.a, percent_complete=50, remaining_hours=28)  # A＝48、WP1＝72（基準 64、差 8・12.5%）、D＝88（基準 80、差 8・10%）
    assert [r["element_id"] for r in analyse()["items"]] == [wp3]

    # 差異の理由を書く（報告日ごとに 1 行を上書きする）
    url = world.url(f"/effort/variance/{world.wp1}/note")
    written = client.put(url, json={"status_report_id": ids["second"], "reason": "仕様追加"}, headers=world.h["progress"])
    assert written.status_code == 200, written.text
    note = written.json()
    assert (note["element_id"], note["wbs_code"], note["status_report_id"], note["reason"], note["corrective_action_id"]) == (
        world.wp1, "1.1", ids["second"], "仕様追加", None)
    assert note["created_by"] == world.uid("progress") and note["created_at"].startswith("2026-12-10T09:00:00")
    again = client.put(url, json={"status_report_id": ids["second"], "reason": "仕様追加と手戻り"}, headers=world.h["pm"]).json()
    assert again["id"] == note["id"] and again["reason"] == "仕様追加と手戻り" and again["created_by"] == world.uid("pm")
    world.progress(ids["second"], world.a, percent_complete=50, remaining_hours=40)
    with_report = analyse(report_id=ids["second"])
    assert with_report["report_specified"] is True
    assert next(r for r in with_report["items"] if r["element_id"] == world.wp1)["note"]["reason"] == "仕様追加と手戻り"
    # 報告日を指定しない分析の理由は別の行
    assert next(r for r in analyse()["items"] if r["element_id"] == world.wp1)["note"] is None

    # 是正措置（DB-CHG-002）を 1 つも登録していないので、紐付けは 404
    linked = client.put(url, json={"reason": "x", "corrective_action_id": 1}, headers=world.h["pm"])
    assert linked.status_code == 404 and linked.json()["error"]["details"] == [{"corrective_action_id": 1}]
    assert client.put(url, json={"reason": "x"}, headers=world.h["planner"]).status_code == 403
    assert client.put(url, json={"reason": "   "}, headers=world.h["pm"]).status_code == 400
    assert client.put(url, json={"reason": ""}, headers=world.h["pm"]).status_code == 400
    assert client.put(url, json={"reason": "x", "status_report_id": MISSING}, headers=world.h["pm"]).status_code == 404
    assert client.put(world.url(f"/effort/variance/{MISSING}/note"), json={"reason": "x"},
                      headers=world.h["pm"]).status_code == 404
    assert client.get(world.url("/effort/variance"), headers=world.h["executor"]).status_code == 403
    assert client.get(world.url("/effort/variance"), params={"baseline_id": MISSING},
                      headers=world.h["pm"]).status_code == 404


def test_corrective_actions_port_is_wired_once_table_exists(app: FastAPI) -> None:
    """是正措置の表（corrective_actions。DB-CHG-002）ができたら、アプリは表を読む口を載せていること。"""
    port = app.state.effort_links.corrective_actions
    if "corrective_actions" in Base.metadata.tables:
        assert not isinstance(port, CorrectiveActionsTableAbsent), (
            "corrective_actions ができたので、app.main.create_app の app.state.effort_links の corrective_actions を差し替える"
        )
    else:
        assert isinstance(port, CorrectiveActionsTableAbsent)


def test_variance_note_links_corrective_action_of_same_project(
    world: World, client: TestClient, db_session: Session
) -> None:
    """API-EFF-007：差異の理由は同じプロジェクトの是正措置（corrective_actions。DB-CHG-002）に紐付く。別のプロジェクトのものは 404。"""
    other = World(client, db_session, code="PRJ-E2")
    own = CorrectiveAction(project_id=world.pid, element_id=world.wp1, cause="仕様追加", action="要員を足す")
    foreign = CorrectiveAction(project_id=other.pid, element_id=other.wp1, cause="仕様追加", action="要員を足す")
    db_session.add_all([own, foreign])
    db_session.commit()
    url = world.url(f"/effort/variance/{world.wp1}/note")

    linked = client.put(url, json={"reason": "仕様追加", "corrective_action_id": own.id}, headers=world.h["pm"])
    assert linked.status_code == 200, linked.text
    assert linked.json()["corrective_action_id"] == own.id
    db_session.expire_all()
    stored_note = db_session.scalars(select(VarianceNote).where(VarianceNote.element_id == world.wp1)).one()
    assert stored_note.corrective_action_id == own.id

    refused = client.put(url, json={"reason": "仕様追加", "corrective_action_id": foreign.id}, headers=world.h["pm"])
    assert refused.status_code == 404 and refused.json()["error"]["details"] == [{"corrective_action_id": foreign.id}]
    db_session.expire_all()
    assert db_session.get(VarianceNote, stored_note.id).corrective_action_id == own.id


def test_activity_with_time_entries_is_not_deleted(world: World, client: TestClient, db_session: Session) -> None:
    """実績工数のある活動を消す（API-SCH-005）と、実績を黙って消さずに 409 で拒む（OQ-26 (2)）。実績を 0 にすれば消せる。"""
    world.assign(world.a, [{"user_id": world.uid("executor")}])
    world.enter("executor", [cell(world.a, "2026-12-01", 3)])
    refused = client.delete(world.url(f"/activities/{world.a}"), headers=world.h["planner"])
    assert refused.status_code == 409 and error_code(refused) == "INVALID_STATE", refused.text
    assert refused.json()["error"]["details"] == [{"activity_id": world.a, "reason": "HAS_TIME_ENTRIES"}]
    assert stored(db_session, world.uid("executor")) == [(world.a, "2026-12-01", 3.0, None)]
    world.enter("executor", [cell(world.a, "2026-12-01", 0)])
    assert client.delete(world.url(f"/activities/{world.a}"), headers=world.h["planner"]).status_code == 204
