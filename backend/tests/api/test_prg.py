"""進捗の API の試験（設計書 6.2 の API-PRG-001〜009、4.2・5.4・5.6・5.7・5.10、9 章）。

SQLite の一時ファイルの DB と TestClient で、正常系と 401・403・404・409・400 のうち該当するものを確かめる。
プロジェクトの開始日は 2026-12-01（火）。5.6 の試験の基準（稲妻チャート.txt §6 の例）は全ての曜日を稼働日にして確かめ、
土日を非稼働にした場合（PRG-10）は既定の月〜金のカレンダーで確かめる。
"""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infra.db import Base
from app.infra.security import generate_token, hash_token
from app.models.cmn import AuthSession, User
from app.models.eff import TimeEntry
from app.models.prg import ProgressEntry, ProgressSnapshot, ProgressSnapshotItem
from app.services.clock import get_now
from app.services.progress import ComputedEtcEffort, EffortTablesAbsent, TimeEntriesEffort
from app.services.project_access import ProjectMembersTable

NOW = datetime(2026, 12, 10, 9, 0, tzinfo=UTC)
MISSING = 9999
ROLES = (("pm", "PM"), ("planner", "PLANNER"), ("executor", "EXECUTOR"), ("progress", "PROGRESS_MANAGER"),
         ("viewer", "VIEWER"), ("contractor", "CONTRACTOR"))


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


class FakeAssigned:
    """割当の活動（project_access の口の代わり。割当の表 DB-EFF-001 は db:EFF が作る）。"""

    def __init__(self, by_user: dict[int, set[int]]) -> None:
        self.by_user = by_user

    def activity_ids(self, session: Session, project_id: int, user_id: int) -> frozenset[int]:
        return frozenset(self.by_user.get(user_id, set()))


class World:
    """管理者が作ったプロジェクト（開始日 2026-12-01）と 6 つのロールのメンバー・メンバーでない利用者。WBS は 成果物 1 の下に WP 1.1。"""

    def __init__(self, client: TestClient, session: Session, code: str = "PRJ-P") -> None:
        self.client = client
        self.session = session
        self.admin = make_user(session, f"admin-{code}", is_system_admin=True)
        self.admin_h = login(session, self.admin)
        body = {"code": code, "name": "進捗", "start_date": "2026-12-01", "owner_user_id": self.admin.id}
        response = client.post("/api/v1/projects", json=body, headers=self.admin_h)
        assert response.status_code == 201, response.text
        self.pid: int = response.json()["id"]
        self.h: dict[str, dict[str, str]] = {}
        self.users: dict[str, User] = {}
        for name, role in ROLES:
            user = make_user(session, f"{name}-{code}")
            put = client.put(f"/api/v1/projects/{self.pid}/members/{user.id}", json={"roles": [role]},
                             headers=self.admin_h)
            assert put.status_code == 200, put.text
            self.users[name] = user
            self.h[name] = login(session, user)
        self.users["outsider"] = make_user(session, f"outsider-{code}")
        self.h["outsider"] = login(session, self.users["outsider"])
        deliverable = self.element("成果物", None, "DELIVERABLE")
        self.wp = self.element("WP1", deliverable, "WORK_PACKAGE")

    def element(self, name: str, parent_id: int | None, kind: str) -> int:
        response = self.client.post(self.url("/wbs/elements"), json={"parent_id": parent_id, "kind": kind, "name": name},
                                    headers=self.h["planner"])
        assert response.status_code == 201, response.text
        return int(response.json()["id"])

    def url(self, path: str) -> str:
        return f"/api/v1/projects/{self.pid}{path}"

    def all_days_working(self) -> None:
        calendar = {"working_weekdays": "1111111", "hours_per_day": 8, "holidays": []}
        response = self.client.put(self.url("/calendar"), json=calendar, headers=self.h["pm"])
        assert response.status_code == 200, response.text

    def add(self, name: str, **body: Any) -> int:
        response = self.client.post(self.url("/activities"), json={"work_package_id": self.wp, "name": name, **body},
                                    headers=self.h["planner"])
        assert response.status_code == 201, response.text
        return int(response.json()["id"])

    def depend(self, p: int, s: int) -> None:
        response = self.client.post(self.url("/dependencies"), json={"predecessor_id": p, "successor_id": s},
                                    headers=self.h["planner"])
        assert response.status_code == 201, response.text

    def report(self, report_date: str, who: str = "pm", expect: int = 201) -> dict[str, Any]:
        response = self.client.post(self.url("/status-reports"), json={"report_date": report_date}, headers=self.h[who])
        assert response.status_code == expect, response.text
        result: dict[str, Any] = response.json()
        return result

    def progress(self, report_id: int, activity_id: int, who: str = "progress", expect: int = 200,
                 **body: Any) -> dict[str, Any]:
        response = self.client.put(self.url(f"/status-reports/{report_id}/progress/{activity_id}"), json=body,
                                   headers=self.h[who])
        assert response.status_code == expect, response.text
        result: dict[str, Any] = response.json()
        return result

    def line(self, report_id: int, who: str = "viewer") -> dict[str, Any]:
        response = self.client.get(self.url(f"/status-reports/{report_id}/lightning-line"), headers=self.h[who])
        assert response.status_code == 200, response.text
        result: dict[str, Any] = response.json()
        return result

    def finalize(self, report_id: int, who: str = "pm", expect: int = 200) -> dict[str, Any]:
        response = self.client.post(self.url(f"/status-reports/{report_id}/finalize"), headers=self.h[who])
        assert response.status_code == expect, response.text
        result: dict[str, Any] = response.json()
        return result


@pytest.fixture()
def world(client: TestClient, db_session: Session) -> World:
    return World(client, db_session)


def error_code(response: Any) -> str:
    code: str = response.json()["error"]["code"]
    return code


def row_summary(line: dict[str, Any]) -> list[tuple[Any, ...]]:
    return [(r["name"], r["point"]["position"], r["display_date"], r["judgement"], r["deviation"]) for r in line["rows"]]


# ---------------------------------------------------------------------------
# API-PRG-001・002 報告日を作る・一覧（PRG-01）
# ---------------------------------------------------------------------------


def test_create_and_list_status_reports(world: World, client: TestClient) -> None:
    first = world.report("2026-12-10")
    assert (first["report_date"], first["status"], first["entry_count"], first["snapshot_id"]) == ("2026-12-10", "OPEN", 0, None)
    assert first["created_by"] == world.users["pm"].id and first["created_at"].startswith("2026-12-10T09:00:00")
    assert (first["finalized_by"], first["finalized_at"]) == (None, None)
    world.report("2026-12-03", who="progress")  # 進捗管理担当者も status_report.manage を持つ
    duplicate = client.post(world.url("/status-reports"), json={"report_date": "2026-12-10"}, headers=world.h["pm"])
    assert duplicate.status_code == 409 and error_code(duplicate) == "DUPLICATE_STATUS_REPORT"
    assert world.report("2026-12-17", who="planner", expect=403)["error"]["code"] == "FORBIDDEN"
    assert client.post(world.url("/status-reports"), json={}, headers=world.h["pm"]).status_code == 400
    assert client.post(f"/api/v1/projects/{MISSING}/status-reports", json={"report_date": "2026-12-10"},
                       headers=world.admin_h).status_code == 404
    assert client.post(world.url("/status-reports"), json={"report_date": "2026-12-11"}).status_code == 401

    listed = client.get(world.url("/status-reports"), headers=world.h["viewer"])
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert [r["report_date"] for r in body["items"]] == ["2026-12-03", "2026-12-10"] and body["total"] == 2
    paged = client.get(world.url("/status-reports"), params={"offset": 1, "limit": 1}, headers=world.h["viewer"]).json()
    assert [r["report_date"] for r in paged["items"]] == ["2026-12-10"] and paged["limit"] == 1
    assert client.get(world.url("/status-reports"), headers=world.h["outsider"]).status_code == 403


def test_new_report_carries_forward_actuals_but_not_assessments(world: World, client: TestClient) -> None:
    a = world.add("A", duration_days=5)
    b = world.add("B", duration_days=5)
    first = world.report("2026-12-03")
    world.progress(first["id"], a, percent_complete=40, actual_start="2026-12-01", remaining_hours=5,
                   delay_reason="要員不足", forecast_finish="2026-12-09")
    second = world.report("2026-12-10")
    assert second["entry_count"] == 1
    row = next(r for r in world.line(second["id"])["rows"] if r["activity_id"] == a)
    assert (row["percent_complete"], row["actual_start"], row["actual_finish"]) == (40.0, "2026-12-01", None)
    assert (row["remaining_hours"], row["delay_reason"], row["forecast_finish"]) == (None, None, None)
    # 前の報告日より前の日付で作った報告日は何も写さない
    earlier = world.report("2026-12-02")
    assert earlier["entry_count"] == 0
    assert next(r for r in world.line(earlier["id"])["rows"] if r["activity_id"] == b)["judgement"] == "NOT_COMPUTED"


# ---------------------------------------------------------------------------
# API-PRG-003 進捗を書く（PRG-02・03、EFF-06、BR-06・13）
# ---------------------------------------------------------------------------


def test_put_progress_validates_and_derives_percent(world: World, client: TestClient, db_session: Session) -> None:
    a = world.add("A", duration_days=5)
    report = world.report("2026-12-03")["id"]
    body = world.progress(report, a, percent_complete=30, actual_start="2026-12-01", remaining_hours=12.5,
                          delay_reason="仕様の確認待ち", forecast_finish="2026-12-09")
    assert (body["percent_complete"], body["progress_method"], body["remaining_hours"], body["remaining_hours_source"]) == (
        30.0, "DIRECT", 12.5, "ENTERED")
    assert (body["delay_reason"], body["forecast_finish"], body["entered_by"]) == (
        "仕様の確認待ち", "2026-12-09", world.users["progress"].id)
    # PUT は全ての項目を置き換える。実績終了日を入れると進捗率は 100（PRG-02）。残工数の空は自動
    body = world.progress(report, a, percent_complete=60, actual_start="2026-12-01", actual_finish="2026-12-03")
    assert (body["percent_complete"], body["remaining_hours"], body["remaining_hours_source"], body["delay_reason"]) == (
        100.0, None, "AUTO", None)
    stored = db_session.scalars(select(ProgressEntry).where(ProgressEntry.activity_id == a)).all()
    assert len(stored) == 1 and stored[0].percent_complete == Decimal("100.00")

    bad = world.progress(report, a, expect=400, actual_start="2026-12-03", actual_finish="2026-12-02")
    assert bad["error"]["code"] == "VALIDATION_ERROR"
    assert world.progress(report, a, expect=400, percent_complete=101)["error"]["code"] == "VALIDATION_ERROR"
    assert world.progress(report, a, expect=400, remaining_hours=-1)["error"]["code"] == "VALIDATION_ERROR"
    assert world.progress(report, a, expect=400, unknown=1)["error"]["code"] == "VALIDATION_ERROR"
    assert world.progress(report, MISSING, expect=404)["error"]["code"] == "NOT_FOUND"
    assert world.progress(MISSING, a, expect=404)["error"]["code"] == "NOT_FOUND"
    other = World(client, db_session, code="PRJ-Q")
    other_activity = other.add("X", duration_days=1)
    assert world.progress(report, other_activity, expect=404)["error"]["code"] == "NOT_FOUND"

    # 直接入力以外の方式の活動は進捗率を入れられない（RULE-PRG-001 が求める）
    assert client.put(world.url(f"/activities/{a}/progress-method"), json={"progress_method": "ZERO_HUNDRED"},
                      headers=world.h["planner"]).status_code == 200
    assert world.progress(report, a, expect=400, percent_complete=50)["error"]["details"][0]["loc"] == [
        "body", "percent_complete"]
    assert world.progress(report, a, actual_start="2026-12-01")["percent_complete"] == 0.0


def test_put_progress_permissions_follow_own_assignment(app: FastAPI, world: World, client: TestClient) -> None:
    a = world.add("A", duration_days=5)
    b = world.add("B", duration_days=5)
    report = world.report("2026-12-03")["id"]
    assert world.progress(report, a, who="viewer", expect=403)["error"]["code"] == "FORBIDDEN"
    assert world.progress(report, a, who="outsider", expect=403)["error"]["code"] == "FORBIDDEN"
    # 自分の割当の活動だけ（progress.enter_own）。割当の表が無い間は割当が無いので拒む
    assert world.progress(report, a, who="executor", expect=403)["error"]["code"] == "FORBIDDEN"
    app.state.project_access = ProjectMembersTable(FakeAssigned({
        world.users["executor"].id: {a}, world.users["contractor"].id: {b}}))
    assert world.progress(report, a, who="executor", percent_complete=10)["percent_complete"] == 10.0
    assert world.progress(report, b, who="executor", expect=403)["error"]["code"] == "FORBIDDEN"
    assert world.progress(report, b, who="contractor", percent_complete=20)["percent_complete"] == 20.0
    # 委託先に見えない活動（自分の割当でない a）は、無い活動と同じ 404（9.2 の注記。CR-011。403 より先）
    hidden = world.progress(report, a, who="contractor", expect=404)["error"]
    missing = world.progress(report, MISSING, who="contractor", expect=404)["error"]
    assert hidden == {**missing, "details": [{"activity_id": a}]} and missing["details"] == [{"activity_id": MISSING}]
    # progress.enter_all（PM・進捗管理担当者）は割当に関わらず書ける
    assert world.progress(report, b, who="pm", percent_complete=30)["percent_complete"] == 30.0
    # 権限の無い利用者には活動の有無を見せない（404 より先に 403）
    assert world.progress(report, MISSING, who="viewer", expect=403)["error"]["code"] == "FORBIDDEN"


def test_contractor_gets_same_404_for_hidden_and_missing_activities(app: FastAPI, world: World,
                                                                      client: TestClient) -> None:
    """9.2 の注記（CR-011）：委託先に見えない活動は、無い活動と同じ 404（``code``・``message``・``details`` が同じ形）。
    自分の割当かの 403 より先に判定しない。API-PRG-003・004・010 で確かめる。全て読める利用者の 403 は変わらない。"""
    a = world.add("A", duration_days=5)
    b = world.add("B", duration_days=5)
    report = world.report("2026-12-03")["id"]
    app.state.project_access = ProjectMembersTable(FakeAssigned({
        world.users["contractor"].id: {b}, world.users["executor"].id: {b}}))

    def put_items(activity_id: int, who: str) -> Any:
        return client.put(world.url(f"/activities/{activity_id}/progress-items"), json={"items": []}, headers=world.h[who])

    def get_items(activity_id: int, who: str) -> Any:
        return client.get(world.url(f"/activities/{activity_id}/progress-items"), headers=world.h[who])

    for call in (lambda x: client.put(world.url(f"/status-reports/{report}/progress/{x}"), json={"percent_complete": 10},
                                      headers=world.h["contractor"]),
                 lambda x: put_items(x, "contractor"), lambda x: get_items(x, "contractor")):
        hidden, missing = call(a), call(MISSING)
        assert (hidden.status_code, missing.status_code) == (404, 404)
        assert hidden.json()["error"] == {**missing.json()["error"], "details": [{"activity_id": a}]}
        assert missing.json()["error"] == {"code": "NOT_FOUND", "message": "活動が見つかりません",
                                           "details": [{"activity_id": MISSING}]}
    assert put_items(b, "contractor").status_code == 200

    # 全て読める利用者（実行担当者）の自分の割当でない活動は 403 のまま、無い活動は 404
    assert world.progress(report, a, who="executor", expect=403)["error"]["code"] == "FORBIDDEN"
    assert error_code(put_items(a, "executor")) == "FORBIDDEN"
    assert put_items(MISSING, "executor").status_code == 404


# ---------------------------------------------------------------------------
# API-PRG-004・005 算定項目・算定方式（PRG-05・BR-06）
# ---------------------------------------------------------------------------


def test_progress_items_and_method_drive_percent(world: World, client: TestClient) -> None:
    a = world.add("A", duration_days=4)
    b = world.add("B", duration_days=4)
    report = world.report("2026-12-03")["id"]
    world.progress(report, a, actual_start="2026-12-01")

    method = client.put(world.url(f"/activities/{a}/progress-method"), json={"progress_method": "CHECK_ITEM_RATIO"},
                        headers=world.h["pm"])
    assert method.status_code == 200, method.text
    assert method.json() == {"activity_id": a, "progress_method": "CHECK_ITEM_RATIO",
                             "effective_method": "CHECK_ITEM_RATIO", "project_default": "DIRECT"}
    assert client.put(world.url(f"/activities/{a}/progress-method"), json={"progress_method": "CHECK_ITEM_RATIO"},
                      headers=world.h["executor"]).status_code == 403  # schedule.edit が要る
    assert client.put(world.url(f"/activities/{a}/progress-method"), json={"progress_method": "BOGUS"},
                      headers=world.h["pm"]).status_code == 400
    assert client.put(world.url(f"/activities/{MISSING}/progress-method"), json={"progress_method": None},
                      headers=world.h["pm"]).status_code == 404
    # 項目が 0 件なら未算定（BR-06）
    assert world.line(report)["rows"][0]["judgement"] == "NOT_COMPUTED"

    items = [{"item_type": "CHECK", "title": "単体試験", "done": True}, {"item_type": "CHECK", "title": "結合試験"},
             {"item_type": "DELIVERABLE", "title": "設計書", "done": True}]
    response = client.put(world.url(f"/activities/{a}/progress-items"), json={"items": items}, headers=world.h["pm"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["progress_method"], body["percent_complete"]) == ("CHECK_ITEM_RATIO", 50.0)
    assert [(i["item_type"], i["title"], i["weight"], i["done"], i["done_at"] is not None) for i in body["items"]] == [
        ("CHECK", "単体試験", 1.0, True, True), ("CHECK", "結合試験", 1.0, False, False),
        ("DELIVERABLE", "設計書", 1.0, True, True)]
    row = world.line(report)["rows"][0]
    assert (row["progress_method"], row["percent_complete"], row["point"]["position"]) == ("CHECK_ITEM_RATIO", 50.0, 2.0)

    # id を載せた項目は変え（完了のままなら完了の時刻を保つ）、載せなかった項目は消す
    unit, integration, _ = (i["id"] for i in body["items"])
    later = NOW + timedelta(hours=1)  # トークンの有効期間（12 時間）の中
    client.app.dependency_overrides[get_now] = lambda: later  # type: ignore[attr-defined]
    replaced = client.put(world.url(f"/activities/{a}/progress-items"), json={"items": [
        {"id": unit, "item_type": "CHECK", "title": "単体試験", "done": True},
        {"id": integration, "item_type": "CHECK", "title": "結合試験", "done": True},
    ]}, headers=world.h["pm"]).json()
    done_at = {i["id"]: i["done_at"] for i in replaced["items"]}
    assert replaced["percent_complete"] == 100.0 and len(replaced["items"]) == 2
    assert done_at[unit].startswith("2026-12-10T09:00:00") and done_at[integration].startswith("2026-12-10T10:00:00")

    b_items = client.put(world.url(f"/activities/{b}/progress-items"),
                         json={"items": [{"item_type": "WORK", "title": "x"}]}, headers=world.h["pm"]).json()
    foreign = client.put(world.url(f"/activities/{a}/progress-items"),
                         json={"items": [{"id": b_items["items"][0]["id"], "item_type": "CHECK", "title": "x"}]},
                         headers=world.h["pm"])
    assert foreign.status_code == 400 and error_code(foreign) == "VALIDATION_ERROR"
    twice = client.put(world.url(f"/activities/{a}/progress-items"), json={"items": [
        {"id": unit, "item_type": "CHECK", "title": "x"}, {"id": unit, "item_type": "CHECK", "title": "y"}]},
        headers=world.h["pm"])
    assert twice.status_code == 400
    assert client.put(world.url(f"/activities/{a}/progress-items"), json={"items": [
        {"item_type": "CHECK", "title": "x", "weight": 0}]}, headers=world.h["pm"]).status_code == 400
    assert client.put(world.url(f"/activities/{a}/progress-items"), json={"items": []},
                      headers=world.h["viewer"]).status_code == 403

    # 方式を既定へ戻す（null）と、OPEN の報告日の進捗率は直接入力として引き継ぐ
    reset = client.put(world.url(f"/activities/{a}/progress-method"), json={"progress_method": None},
                       headers=world.h["pm"]).json()
    assert (reset["progress_method"], reset["effective_method"]) == (None, "DIRECT")
    assert world.line(report)["rows"][0]["percent_complete"] == 100.0


def test_project_default_method_change_refreshes_open_entries(world: World, client: TestClient,
                                                              db_session: Session) -> None:
    """API-PRJ-004 で算定方式の既定を変えると、既定に従う活動の OPEN の報告日の進捗率を求め直す（ガントとイナズマ線を揃える）。"""
    a = world.add("A", duration_days=4)
    report = world.report("2026-12-03")["id"]
    world.progress(report, a, percent_complete=30)
    items = [{"item_type": "CHECK", "title": "単体試験", "done": True}, {"item_type": "CHECK", "title": "結合試験", "done": True}]
    assert client.put(world.url(f"/activities/{a}/progress-items"), json={"items": items},
                      headers=world.h["pm"]).status_code == 200
    changed = client.patch(f"/api/v1/projects/{world.pid}", json={"settings": {"progress_method_default": "CHECK_ITEM_RATIO"}},
                           headers=world.h["pm"])
    assert changed.status_code == 200, changed.text
    db_session.expire_all()
    entry = db_session.scalar(select(ProgressEntry).where(ProgressEntry.status_report_id == report,
                                                          ProgressEntry.activity_id == a))
    assert entry is not None and entry.percent_complete == Decimal("100")  # ガントが読む保存値
    assert world.line(report)["rows"][0]["percent_complete"] == 100.0  # イナズマ線が求め直す値


# ---------------------------------------------------------------------------
# API-PRG-006 イナズマ線（PRG-08〜13。5.4・5.6）
# ---------------------------------------------------------------------------


def test_lightning_line_matches_basis_example(world: World) -> None:
    """5.6 の試験の基準（稲妻チャート.txt §6）：全ての曜日が稼働日、原点 2026-12-01、報告日 2026-12-10（L＝10）。"""
    world.all_days_working()
    docs = world.add("資料作成", requested_start="2026-12-01", duration_days=21)
    coordination = world.add("関係者調整", requested_start="2026-12-01", duration_days=21)
    release = world.add("公開準備", requested_start="2026-12-12", duration_days=7)
    setup = world.add("環境整備", requested_start="2026-12-01", duration_days=8)
    report = world.report("2026-12-10")["id"]
    world.progress(report, docs, percent_complete=25, actual_start="2026-12-01")
    world.progress(report, coordination, percent_complete=60, actual_start="2026-12-01")
    world.progress(report, release, percent_complete=0)
    world.progress(report, setup, percent_complete=100, actual_start="2026-12-01", actual_finish="2026-12-08")

    line = world.line(report)
    assert line["source"] == "CALCULATED" and line["restricted"] is False and line["tolerance_days"] == 0.5
    assert line["report_line"] == {"position": 10.0, "calendar": {"date": "2026-12-10", "fraction": 1.0}}
    assert row_summary(line) == [
        ("資料作成", 5.25, "2026-12-06", "BEHIND", -4.75),
        ("関係者調整", 12.6, "2026-12-13", "AHEAD", 2.6),
        ("公開準備", 10.0, "2026-12-10", "NOT_STARTED", 0.0),
        ("環境整備", 10.0, "2026-12-10", "COMPLETED", 0.0),
    ]
    assert [r["counted_as"] for r in line["rows"]] == ["BEHIND", "AHEAD", "ON_SCHEDULE", "ON_SCHEDULE"]
    first = line["rows"][0]
    assert (first["row_index"], first["wbs_code"], first["start_date"], first["finish_date"], first["duration_days"]) == (
        0, "1.1", "2026-12-01", "2026-12-21", 21)
    assert first["point"]["calendar"] == {"date": "2026-12-06", "fraction": 0.25}


def test_lightning_point_uses_unrounded_percent(world: World, client: TestClient) -> None:
    """API-PRG-006・007：P は丸める前の進捗率から求める（設計書 1.1 の 5.6・CR-009）。表示・写しの進捗率は小数 2 桁。

    3 項目中 2 完了（200/3 %）・d＝3、報告日 12/01（L＝1）：P＝2 ちょうど・表示の日付 12/02・ずれ 1。66.67% から求めると 2.0001・12/03。
    """
    world.all_days_working()
    a = world.add("検証", requested_start="2026-12-01", duration_days=3)
    report = world.report("2026-12-01")["id"]
    world.progress(report, a, actual_start="2026-12-01")
    assert client.put(world.url(f"/activities/{a}/progress-method"), json={"progress_method": "CHECK_ITEM_RATIO"},
                      headers=world.h["pm"]).status_code == 200
    items = [{"item_type": "CHECK", "title": t, "done": t != "C"} for t in ("A", "B", "C")]
    assert client.put(world.url(f"/activities/{a}/progress-items"), json={"items": items},
                      headers=world.h["pm"]).status_code == 200
    (row,) = world.line(report)["rows"]
    assert (row["point"]["position"], row["display_date"], row["deviation"], row["percent_complete"]) == (
        2.0, "2026-12-02", 1.0, 66.67)
    world.finalize(report)
    (frozen,) = world.line(report)["rows"]
    assert (frozen["point"]["position"], frozen["display_date"], frozen["percent_complete"]) == (2.0, "2026-12-02", 66.67)


def test_lightning_line_skips_weekends(world: World, client: TestClient) -> None:
    """土日を非稼働にした場合（PRG-10）：12-01（火）〜 5 稼働日、報告日 12-05（土）は金曜の終業（L＝4）。"""
    a = world.add("A", duration_days=5)
    report = world.report("2026-12-05")["id"]
    world.progress(report, a, percent_complete=50, actual_start="2026-12-01")
    line = world.line(report)
    assert line["report_line"] == {"position": 4.0, "calendar": {"date": "2026-12-04", "fraction": 1.0}}
    row = line["rows"][0]
    assert (row["finish_date"], row["point"]["position"], row["display_date"], row["deviation"], row["judgement"]) == (
        "2026-12-07", 2.5, "2026-12-03", -1.5, "BEHIND")
    assert client.get(world.url(f"/status-reports/{MISSING}/lightning-line"), headers=world.h["viewer"]).status_code == 404
    assert client.get(world.url(f"/status-reports/{report}/lightning-line"),
                      headers=world.h["outsider"]).status_code == 403


def test_get_progress_items_reads_current_items(app: FastAPI, world: World, client: TestClient) -> None:
    """API-PRG-010 進捗の算定項目を読む（設計書 1.1・CR-004）：今の項目・算定方式・進捗率を API-PRG-004 の応答と同じ形で返す。

    権限は project.view。無い活動・他のプロジェクトの活動は 404、委託先には自分の割当の活動だけ（他は 404）。
    """
    a = world.add("A", duration_days=4)
    b = world.add("B", duration_days=4)
    world.report("2026-12-03")
    url = world.url(f"/activities/{a}/progress-items")
    empty = client.get(url, headers=world.h["viewer"])
    assert empty.status_code == 200, empty.text
    assert (empty.json()["activity_id"], empty.json()["items"]) == (a, [])

    assert client.put(world.url(f"/activities/{a}/progress-method"), json={"progress_method": "CHECK_ITEM_RATIO"},
                      headers=world.h["pm"]).status_code == 200
    items = [{"item_type": "CHECK", "title": "単体試験", "done": True}, {"item_type": "CHECK", "title": "結合試験"}]
    put = client.put(url, json={"items": items}, headers=world.h["pm"])
    assert put.status_code == 200, put.text
    got = client.get(url, headers=world.h["viewer"])
    assert got.status_code == 200, got.text
    assert got.json() == put.json()  # 置き換えの応答と同じもの（項目・方式・進捗率）
    assert (got.json()["progress_method"], got.json()["percent_complete"]) == ("CHECK_ITEM_RATIO", 50.0)

    assert client.get(world.url(f"/activities/{MISSING}/progress-items"), headers=world.h["viewer"]).status_code == 404
    other = World(client, world.session, code="PRJ-Q")
    foreign = client.get(other.url(f"/activities/{a}/progress-items"), headers=other.h["viewer"])
    assert foreign.status_code == 404
    assert client.get(url, headers=world.h["outsider"]).status_code == 403
    assert client.get(url).status_code == 401

    app.state.project_access = ProjectMembersTable(FakeAssigned({world.users["contractor"].id: {b}}))
    assert client.get(url, headers=world.h["contractor"]).status_code == 404
    own = client.get(world.url(f"/activities/{b}/progress-items"), headers=world.h["contractor"])
    assert own.status_code == 200 and own.json()["activity_id"] == b


def test_contractor_sees_only_assigned_rows(app: FastAPI, world: World) -> None:
    a = world.add("A", duration_days=5)
    b = world.add("B", duration_days=5)
    report = world.report("2026-12-03")["id"]
    app.state.project_access = ProjectMembersTable(FakeAssigned({world.users["contractor"].id: {b}}))
    line = world.line(report, who="contractor")
    assert [(r["row_index"], r["activity_id"]) for r in line["rows"]] == [(0, b)] and line["restricted"] is True
    assert [r["activity_id"] for r in world.line(report, who="viewer")["rows"]] == [a, b]


def test_contractor_entry_count_counts_only_assigned_rows(app: FastAPI, world: World, client: TestClient) -> None:
    """9.2 の注記（CR-011）：委託先には報告日の ``entry_count`` を自分の割当の活動の行だけで数える
    （API-PRG-002 の一覧、API-PRG-006・009 の ``report``）。全て読める利用者は全ての行を数える。"""
    a = world.add("A", duration_days=5)
    b = world.add("B", duration_days=5)
    report = world.report("2026-12-03")["id"]
    world.progress(report, a, who="pm", percent_complete=10)
    world.progress(report, b, who="pm", percent_complete=20)
    delays = world.url(f"/status-reports/{report}/delays")
    listed = client.get(world.url("/status-reports"), headers=world.h["viewer"]).json()
    assert [r["entry_count"] for r in listed["items"]] == [2]
    assert world.line(report)["report"]["entry_count"] == 2

    app.state.project_access = ProjectMembersTable(FakeAssigned({world.users["contractor"].id: {b}}))
    restricted = client.get(world.url("/status-reports"), headers=world.h["contractor"]).json()
    assert [r["entry_count"] for r in restricted["items"]] == [1] and restricted["total"] == 1
    assert world.line(report, who="contractor")["report"]["entry_count"] == 1
    assert client.get(delays, headers=world.h["contractor"]).json()["report"]["entry_count"] == 1
    assert client.get(delays, headers=world.h["viewer"]).json()["report"]["entry_count"] == 2


# ---------------------------------------------------------------------------
# API-PRG-007 確定とスナップショット（PRG-17・BR-13）
# ---------------------------------------------------------------------------


def test_finalize_freezes_snapshot_and_rejects_later_input(world: World, client: TestClient, db_session: Session) -> None:
    world.all_days_working()
    a = world.add("A", requested_start="2026-12-01", duration_days=10)
    m = world.add("M", is_milestone=True)
    world.depend(a, m)
    report = world.report("2026-12-06")["id"]
    world.progress(report, a, percent_complete=20, actual_start="2026-12-01", remaining_hours=40)
    before = world.line(report)
    # 実績工数（DB-EFF-002）：報告日以前の分だけを写す（5.8）。報告日の後の 12-07 の行は入れない
    for who, day, hours in (("executor", 2, "3"), ("progress", 3, "1.25"), ("executor", 6, "2.5"), ("executor", 7, "4")):
        db_session.add(TimeEntry(user_id=world.users[who].id, activity_id=a, work_date=date(2026, 12, day),
                                 hours=Decimal(hours)))
    db_session.commit()

    assert world.finalize(report, who="planner", expect=403)["error"]["code"] == "FORBIDDEN"
    body = world.finalize(report)
    assert (body["report"]["status"], body["report"]["finalized_by"], body["item_count"]) == (
        "FINALIZED", world.users["pm"].id, 2)
    assert body["report"]["finalized_at"].startswith("2026-12-10T09:00:00") and body["report"]["snapshot_id"] == body["snapshot_id"]
    assert (body["report_line"], body["tolerance_days"], body["extract_days"], body["plan_basis"]) == (6, 0.5, 2.0, "LATEST")
    items = db_session.scalars(select(ProgressSnapshotItem).where(ProgressSnapshotItem.snapshot_id == body["snapshot_id"])
                               .order_by(ProgressSnapshotItem.row_order)).all()
    assert [(i.activity_id, i.row_order, i.wbs_code, i.percent_complete, i.point, i.deviation, i.judgement) for i in items] == [
        (a, 0, "1.1", Decimal("20.00"), Decimal("2.0000"), Decimal("-4.0000"), "BEHIND"),
        (m, 1, "1.1", None, Decimal("6.0000"), Decimal("0.0000"), "NOT_COMPUTED"),
    ]
    # 実績工数は表の報告日以前の合計（実績の無い活動は 0）。ETC は RULE-EFF-001（A-28）：A は入力した残工数が優先、
    # M は進捗率が未算定なので max(見積 0（未見積）− 実績 0, 0)＝0
    assert (items[0].actual_hours, items[0].etc_hours, items[1].actual_hours, items[1].etc_hours) == (
        Decimal("6.75"), Decimal("40.00"), Decimal("0"), Decimal("0"))
    assert db_session.scalar(select(ProgressSnapshot.report_date).where(ProgressSnapshot.id == body["snapshot_id"])) == date(2026, 12, 6)

    again = client.post(world.url(f"/status-reports/{report}/finalize"), headers=world.h["pm"])
    assert again.status_code == 409 and error_code(again) == "INVALID_STATE"
    late = world.progress(report, a, expect=409, percent_complete=90)
    assert late["error"]["code"] == "INVALID_STATE"

    # 確定の後に計画を変えても、確定済みの線は写しから描くので変わらない（BR-13）
    assert client.patch(world.url(f"/activities/{a}"), json={"duration_days": 20}, headers=world.h["planner"]).status_code == 200
    after = world.line(report)
    assert after["source"] == "SNAPSHOT" and row_summary(after) == row_summary(before)
    assert [(r["start_date"], r["finish_date"], r["duration_days"]) for r in after["rows"]] == [
        ("2026-12-01", "2026-12-10", 10), ("2026-12-10", "2026-12-10", 0)]
    assert after["rows"][0]["remaining_hours"] == 40.0 and after["rows"][0]["progress_method"] is None
    assert client.post(world.url(f"/status-reports/{MISSING}/finalize"), headers=world.h["pm"]).status_code == 404


# ---------------------------------------------------------------------------
# API-PRG-008 複数の報告日の線を重ねる（PRG-18）
# ---------------------------------------------------------------------------


def test_overlay_finalized_lines(world: World, client: TestClient) -> None:
    world.all_days_working()
    a = world.add("A", requested_start="2026-12-01", duration_days=10)
    c = world.add("C", requested_start="2026-12-01", duration_days=4)
    first = world.report("2026-12-03")["id"]
    world.progress(first, a, percent_complete=10, actual_start="2026-12-01")
    world.progress(first, c, percent_complete=50, actual_start="2026-12-01")
    world.finalize(first)
    second = world.report("2026-12-06")["id"]
    world.progress(second, a, percent_complete=30, actual_start="2026-12-01")
    open_report = world.report("2026-12-08")["id"]
    world.finalize(second)

    response = client.get(world.url("/lightning-lines"), params=[("report_ids", second), ("report_ids", first)],
                          headers=world.h["viewer"])
    assert response.status_code == 200, response.text
    lines = response.json()["lines"]
    assert [(x["style_index"], x["report_id"], x["report_date"], x["report_line"]["position"]) for x in lines] == [
        (0, first, "2026-12-03", 3.0), (1, second, "2026-12-06", 6.0)]
    assert [(v["row_index"], v["activity_id"], v["point"]["position"], v["judgement"]) for v in lines[0]["vertices"]] == [
        (0, a, 1.0, "BEHIND"), (1, c, 2.0, "BEHIND")]
    assert [v["point"]["position"] for v in lines[1]["vertices"]] == [3.0, 2.0]  # C は前の報告日の 50% を引き継いだ

    # 写した活動を消しても過去の線は残り、消した活動の頂点は外れる（BR-13）。写しは活動の ID を写しの値のまま持つ（設計書 1.1 の 4.2）
    assert client.delete(world.url(f"/activities/{c}"), headers=world.h["planner"]).status_code == 204
    lines = client.get(world.url("/lightning-lines"), params=[("report_ids", first)], headers=world.h["viewer"]).json()["lines"]
    assert [v["activity_id"] for v in lines[0]["vertices"]] == [a] and lines[0]["missing_activity_ids"] == [c]
    past = world.line(first)
    assert [(r["activity_id"], r["name"]) for r in past["rows"]] == [(a, "A"), (c, "C")]

    def get(params: list[tuple[str, int]]) -> Any:
        return client.get(world.url("/lightning-lines"), params=params, headers=world.h["viewer"])

    assert get([]).status_code == 400
    assert get([("report_ids", first), ("report_ids", first)]).status_code == 400
    assert get([("report_ids", MISSING)]).status_code == 404
    still_open = get([("report_ids", first), ("report_ids", open_report)])
    assert still_open.status_code == 409 and error_code(still_open) == "INVALID_STATE"
    assert client.get(world.url("/lightning-lines"), params=[("report_ids", first)],
                      headers=world.h["outsider"]).status_code == 403


# ---------------------------------------------------------------------------
# API-PRG-009 遅延活動と影響（PRG-19・PRG-13。5.7）
# ---------------------------------------------------------------------------


def test_delays_extract_and_impact(app: FastAPI, world: World, client: TestClient) -> None:
    """全ての曜日が稼働日。A（10 日）→ B（5 日）→ M と、依存の無い C（3 日）。報告日 12-06（L＝6）、抽出の閾値 2。"""
    world.all_days_working()
    a = world.add("A", requested_start="2026-12-01", duration_days=10)
    b = world.add("B", duration_days=5)
    m = world.add("M", is_milestone=True)
    c = world.add("C", requested_start="2026-12-01", duration_days=3)
    world.depend(a, b)
    world.depend(b, m)
    report = world.report("2026-12-06")["id"]
    world.progress(report, a, percent_complete=10, actual_start="2026-12-01", delay_reason="要員不足",
                   forecast_finish="2026-12-13")
    world.progress(report, b, percent_complete=0)
    world.progress(report, c, percent_complete=0)

    response = client.get(world.url(f"/status-reports/{report}/delays"), headers=world.h["viewer"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["source"], body["report_line"], body["extract_days"], body["restricted"]) == ("CALCULATED", 6, 2.0, False)
    assert [(d["activity_id"], d["deviation"]) for d in body["delays"]] == [(c, -6.0), (a, -5.0)]  # 遅れの大きい順
    delayed = body["delays"][1]
    assert (delayed["delay_reason"], delayed["forecast_finish"], delayed["expected_finish"], delayed["expected_finish_date"]) == (
        "要員不足", "2026-12-13", 13, "2026-12-13")
    assert (delayed["is_critical"], delayed["transitive_successor_ids"]) == (True, [b, m])
    assert (delayed["fixed_finish"]["before_date"], delayed["fixed_finish"]["after_date"]) == ("2026-12-10", "2026-12-13")
    assert [(s["activity_id"], s["name"], s["slip_days"], s["after_date"]) for s in delayed["successor_slips"]] == [
        (b, "B", 3, "2026-12-18")]
    assert [(s["activity_id"], s["slip_days"], s["before_date"], s["after_date"]) for s in delayed["milestone_slips"]] == [
        (m, 3, "2026-12-15", "2026-12-18")]
    assert delayed["project_finish"] == {"before_date": "2026-12-15", "after_date": "2026-12-18", "slip_days": 3}
    independent = body["delays"][0]
    assert (independent["is_critical"], independent["successor_slips"], independent["project_finish"]["slip_days"]) == (
        False, [], 0)
    assert independent["expected_finish"] == 9  # L＋ceil(3×(1−0))
    assert [(s["activity_id"], s["slip_days"]) for s in body["combined_milestone_slips"]] == [(m, 3)]
    assert body["combined_project_finish"]["slip_days"] == 3

    # 確定後は写しの判定・ずれで同じ結果になる
    world.finalize(report)
    frozen = client.get(world.url(f"/status-reports/{report}/delays"), headers=world.h["viewer"]).json()
    assert frozen["source"] == "SNAPSHOT" and frozen["delays"] == body["delays"]

    # 委託先には自分の割当の活動だけ（プロジェクトの終了のずれは返さない）
    app.state.project_access = ProjectMembersTable(FakeAssigned({world.users["contractor"].id: {a}}))
    limited = client.get(world.url(f"/status-reports/{report}/delays"), headers=world.h["contractor"]).json()
    assert [d["activity_id"] for d in limited["delays"]] == [a] and limited["restricted"] is True
    assert limited["delays"][0]["successor_slips"] == [] and limited["delays"][0]["transitive_successor_ids"] == []
    assert limited["delays"][0]["project_finish"] is None and limited["combined_project_finish"] is None
    assert client.get(world.url(f"/status-reports/{MISSING}/delays"), headers=world.h["viewer"]).status_code == 404
    assert client.get(world.url(f"/status-reports/{report}/delays"), headers=world.h["outsider"]).status_code == 403


# ---------------------------------------------------------------------------
# 別の群の口の差し替え忘れ（A-26）
# ---------------------------------------------------------------------------


def test_effort_port_is_wired_once_time_entries_table_exists(app: FastAPI) -> None:
    """実績工数の表（time_entries。DB-EFF-002）ができたら、確定はそれと RULE-EFF-001 を読む実装を載せていること。"""
    if "time_entries" in Base.metadata.tables:
        assert not isinstance(app.state.progress_links.effort, EffortTablesAbsent), (
            "time_entries ができたので、app.main.create_app の app.state.progress_links の effort を差し替える"
        )
    else:
        assert isinstance(app.state.progress_links.effort, EffortTablesAbsent)


def test_effort_port_computes_etc_once_effort_rules_exist(app: FastAPI) -> None:
    """ETC の計算（RULE-EFF-001。``app/domain/effort.py``）ができたら、確定はそれで ETC を求める実装を載せていること。

    db:EFF で載せた ``TimeEntriesEffort`` は実績工数を表から読むが、ETC は入力した残工数だけを写す（A-27）。
    """
    effort = app.state.progress_links.effort
    if importlib.util.find_spec("app.domain.effort") is not None:
        assert type(effort) is not TimeEntriesEffort, (
            "RULE-EFF-001 ができたので、app.main.create_app の app.state.progress_links の effort を ETC を計算する実装へ差し替える"
        )
        assert isinstance(effort, ComputedEtcEffort)
    elif "time_entries" in Base.metadata.tables:
        assert isinstance(effort, TimeEntriesEffort)
