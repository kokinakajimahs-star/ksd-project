"""レポート・共有の API の試験（設計書 6.2 の API-RPT-001〜004、4.2・5.1・7.4、9 章。要求仕様書 RPT-01・RPT-03）。

SQLite の一時ファイルの DB と TestClient で、正常系と 401・403・404・409・400 のうち該当するものを確かめる。
プロジェクトの開始日は 2026-12-01（火）。全ての曜日を稼働日にし、A（10 日）→ B（5 日）→ M（マイルストーン）の網で確かめる。
会議レポートの値は、同じ報告日のイナズマ線（API-PRG-006）・遅延（API-PRG-009）・工数の集計（RULE-EFF-002）と食い違わないことを見る。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infra.security import generate_token, hash_token
from app.models.chg import AuditLog, CorrectiveAction
from app.models.cmn import AuthSession, ShareLink, User
from app.models.sch import Activity
from app.services.clock import get_now
from app.services.effort import load_basis
from app.services.project_access import ProjectMembersTable

NOW = datetime(2026, 12, 5, 9, 0, tzinfo=UTC)
MISSING = 9999
ROLES = (("pm", "PM"), ("planner", "PLANNER"), ("executor", "EXECUTOR"), ("progress", "PROGRESS_MANAGER"),
         ("approver", "APPROVER"), ("viewer", "VIEWER"), ("contractor", "CONTRACTOR"))


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
    """割当の活動（project_access の口の代わり。委託先の絞り込みを確かめる）。"""

    def __init__(self, by_user: dict[int, set[int]]) -> None:
        self.by_user = by_user

    def activity_ids(self, session: Session, project_id: int, user_id: int) -> frozenset[int]:
        return frozenset(self.by_user.get(user_id, set()))


class World:
    """管理者が作ったプロジェクト（開始日 2026-12-01、全ての曜日が稼働日）と 7 つのロールのメンバー・メンバーでない利用者。

    WBS は 成果物 1 の下に WP 1.1。活動は A（10 日。見積 80 人時）→ B（5 日。見積 40 人時）→ M（マイルストーン）。
    """

    def __init__(self, client: TestClient, session: Session, code: str = "PRJ-R") -> None:
        self.client = client
        self.session = session
        self.admin = make_user(session, f"admin-{code}", is_system_admin=True)
        self.admin_h = login(session, self.admin)
        body = {"code": code, "name": "会議", "start_date": "2026-12-01", "owner_user_id": self.admin.id}
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
        calendar = {"working_weekdays": "1111111", "hours_per_day": 8, "holidays": []}
        assert client.put(self.url("/calendar"), json=calendar, headers=self.h["pm"]).status_code == 200
        deliverable = self.element("成果物", None, "DELIVERABLE")
        self.wp = self.element("WP1", deliverable, "WORK_PACKAGE")
        self.a = self.add("A", duration_days=10)
        self.b = self.add("B", duration_days=5)
        self.m = self.add("M", duration_days=0, is_milestone=True)
        self.depend(self.a, self.b)
        self.depend(self.b, self.m)
        for activity_id, hours in ((self.a, 80), (self.b, 40)):
            activity = session.get(Activity, activity_id)
            assert activity is not None
            activity.estimate_hours = Decimal(hours)
        session.commit()

    def element(self, name: str, parent_id: int | None, kind: str) -> int:
        response = self.client.post(self.url("/wbs/elements"), json={"parent_id": parent_id, "kind": kind, "name": name},
                                    headers=self.h["planner"])
        assert response.status_code == 201, response.text
        return int(response.json()["id"])

    def url(self, path: str) -> str:
        return f"/api/v1/projects/{self.pid}{path}"

    def add(self, name: str, **body: Any) -> int:
        response = self.client.post(self.url("/activities"), json={"work_package_id": self.wp, "name": name, **body},
                                    headers=self.h["planner"])
        assert response.status_code == 201, response.text
        return int(response.json()["id"])

    def depend(self, p: int, s: int) -> None:
        response = self.client.post(self.url("/dependencies"), json={"predecessor_id": p, "successor_id": s},
                                    headers=self.h["planner"])
        assert response.status_code == 201, response.text

    def report(self, report_date: str) -> int:
        response = self.client.post(self.url("/status-reports"), json={"report_date": report_date}, headers=self.h["pm"])
        assert response.status_code == 201, response.text
        return int(response.json()["id"])

    def progress(self, report_id: int, activity_id: int, **body: Any) -> None:
        response = self.client.put(self.url(f"/status-reports/{report_id}/progress/{activity_id}"), json=body,
                                   headers=self.h["progress"])
        assert response.status_code == 200, response.text

    def delayed_report(self) -> int:
        """報告日 2026-12-05：A は 20%（遅れ。遅延理由・完了見込み 2026-12-14 を入力）、B・M は 0%。"""
        report = self.report("2026-12-05")
        self.progress(report, self.a, percent_complete=20, actual_start="2026-12-01", delay_reason="要員不足",
                      forecast_finish="2026-12-14")
        self.progress(report, self.b, percent_complete=0)
        self.progress(report, self.m, percent_complete=0)
        return report

    def meeting(self, report_id: int, who: str = "viewer") -> dict[str, Any]:
        response = self.client.get(self.url(f"/status-reports/{report_id}/meeting-report"), headers=self.h[who])
        assert response.status_code == 200, response.text
        result: dict[str, Any] = response.json()
        return result

    def get(self, path: str, who: str = "viewer") -> dict[str, Any]:
        response = self.client.get(self.url(path), headers=self.h[who])
        assert response.status_code == 200, response.text
        result: dict[str, Any] = response.json()
        return result

    def share(self, scope: str, who: str = "pm", expect: int = 201, **extra: Any) -> dict[str, Any]:
        body = {"scope": scope, "expires_at": "2026-12-31T00:00:00+09:00", **extra}
        response = self.client.post(self.url("/share-links"), json=body, headers=self.h[who])
        assert response.status_code == expect, response.text
        result: dict[str, Any] = response.json()
        return result


@pytest.fixture()
def world(client: TestClient, db_session: Session) -> World:
    return World(client, db_session)


def error_code(response: Any) -> str:
    code: str = response.json()["error"]["code"]
    return code


def vertices_of(report: dict[str, Any]) -> list[tuple[Any, ...]]:
    return [(v["activity_id"], v["point"]["position"], v["deviation"], v["judgement"])
            for v in report["lightning"]["vertices"]]


# ---------------------------------------------------------------------------
# API-RPT-001 定例会議用レポートのデータ（RPT-01・7.4）
# ---------------------------------------------------------------------------


def test_meeting_report_of_open_report_is_calculated(world: World, client: TestClient, db_session: Session) -> None:
    report = world.delayed_report()
    db_session.add_all([
        CorrectiveAction(project_id=world.pid, cause="要員不足", action="要員を足す", due_date=date(2026, 12, 4),
                         status="NOT_STARTED"),  # 期限 < 報告日・未完了 → 超過
        CorrectiveAction(project_id=world.pid, cause="要員不足", action="残業", due_date=date(2026, 12, 5),
                         status="IN_PROGRESS"),  # 期限ちょうどは超過ではない
        CorrectiveAction(project_id=world.pid, cause="仕様追加", action="調整", due_date=date(2026, 12, 1),
                         status="DONE"),  # 完了は超過ではない
    ])
    db_session.commit()

    body = world.meeting(report)
    assert (body["report_id"], body["report_date"], body["status"], body["source"]) == (report, "2026-12-05", "OPEN",
                                                                                         "CALCULATED")
    assert body["project_start"] == "2026-12-01" and body["restricted"] is False
    # 上段：表示の範囲は報告日の前後 4 週。ガントは今の計画の全ての行、実績のバーは報告日の入力
    assert body["window"] == {"start": "2026-11-07", "end": "2027-01-02"}
    rows = body["gantt"]["rows"]
    assert [r["name"] for r in rows] == ["A", "B", "M"] and body["gantt"]["report_date"] == "2026-12-05"
    assert rows[0]["percent_complete"] == 20.0 and rows[0]["actual"]["start_date"] == "2026-12-01"
    # イナズマ線は同じ報告日の API-PRG-006 と同じ値
    line = world.get(f"/status-reports/{report}/lightning-line")
    expected = [(r["activity_id"], r["point"]["position"], r["deviation"], r["judgement"]) for r in line["rows"]]
    assert vertices_of(body) == expected
    assert [v["row_index"] for v in body["lightning"]["vertices"]] == [0, 1, 2]
    assert body["lightning"]["missing_activity_ids"] == []
    assert body["lightning"]["report_line"]["position"] == line["report_line"]["position"]

    # 下段左：遅延一覧は API-PRG-009 と同じ活動。遅延理由・入力した完了見込み
    delays = world.get(f"/status-reports/{report}/delays")
    assert [d["activity_id"] for d in body["delays"]] == [d["activity_id"] for d in delays["delays"]] == [world.a]
    delay = body["delays"][0]
    assert (delay["wbs_code"], delay["name"], delay["planned_finish"]) == ("1.1", "A", "2026-12-10")
    assert (delay["delay_reason"], delay["forecast_finish"], delay["forecast_entered"]) == ("要員不足", "2026-12-14", True)
    assert delay["deviation"] < 0 and delay["percent_complete"] == 20.0 and delay["is_critical"] is True

    # 下段中：マイルストーンの見込みは遅延の見込みを全て当てた日（API-PRG-009 の全体の影響と同じ）
    slips = {s["activity_id"]: s for s in delays["combined_milestone_slips"]}
    (milestone,) = body["milestones"]
    assert (milestone["activity_id"], milestone["name"]) == (world.m, "M")
    assert milestone["forecast_date"] == slips[world.m]["after_date"]
    assert milestone["slip_days"] == slips[world.m]["slip_days"] > 0
    assert milestone["planned_date"] == rows[2]["planned"]["end_date"]

    # 下段右：主要 KPI
    kpi = body["kpi"]
    assert kpi["overall_progress"] == 13.33  # (80×20 + 40×0) / 120
    assert kpi["behind_count"] == sum(1 for r in line["rows"] if r["judgement"] == "BEHIND") >= 1
    project = load_basis(db_session, world.pid, report_id=report).rollup.project
    assert kpi["eac_hours"] == float(project.eac)
    assert (kpi["baseline_hours"], kpi["eac_variance_hours"]) == (None, None)  # 基準の版が無い
    assert kpi["min_critical_float"] == 0
    assert kpi["overdue_action_count"] == 1


def test_meeting_report_gantt_returns_non_working_days_of_window(world: World, client: TestClient) -> None:
    """API-RPT-001：上段のガントの非稼働日は表示の範囲（報告日の前後 4 週）の分（7.4。設計書 1.1・CR-008）。"""
    calendar = {"working_weekdays": "1111111", "hours_per_day": 8,
                "holidays": [{"holiday_date": d} for d in ("2026-11-06", "2026-11-10", "2026-12-31", "2027-01-03")]}
    assert client.put(world.url("/calendar"), json=calendar, headers=world.h["pm"]).status_code == 200
    report = world.delayed_report()
    body = world.meeting(report)
    assert body["window"] == {"start": "2026-11-07", "end": "2027-01-02"}
    gantt = body["gantt"]
    # バーの範囲（12/01〜）の外でも、表示の範囲の中の休日は返す。範囲の外（11/06・2027-01-03）は返さない
    assert gantt["non_working_days"] == ["2026-11-10", "2026-12-31"]
    assert gantt["range_start"] == "2026-12-01"


def test_meeting_report_of_finalized_report_uses_snapshot(world: World, client: TestClient) -> None:
    report = world.delayed_report()
    before = world.meeting(report)
    baseline = client.post(world.url("/schedule-baselines"), json={"name": "初版"},
                           headers=world.h["pm"])
    assert baseline.status_code == 201, baseline.text
    finalized = client.post(world.url(f"/status-reports/{report}/finalize"), headers=world.h["pm"])
    assert finalized.status_code == 200, finalized.text
    # 確定の後に計画を変えても、報告日の値（線・遅延の予定の終了日）は写しのまま（BR-13）
    patched = client.patch(world.url(f"/activities/{world.a}"), json={"duration_days": 20}, headers=world.h["planner"])
    assert patched.status_code == 200, patched.text

    after = world.meeting(report)
    assert (after["status"], after["source"]) == ("FINALIZED", "SNAPSHOT")
    assert vertices_of(after) == vertices_of(before)
    assert after["delays"][0]["planned_finish"] == "2026-12-10"
    # ガントは今の計画（A は 20 日に延びた）。基準のバーは最新の版
    row_a = after["gantt"]["rows"][0]
    assert row_a["planned"]["end_date"] == "2026-12-20" and row_a["baseline"]["end_date"] == "2026-12-10"
    assert after["gantt"]["baseline"]["name"] == "初版"
    kpi = after["kpi"]
    assert kpi["baseline_hours"] == 120.0 and kpi["eac_variance_hours"] == kpi["eac_hours"] - 120.0


def test_meeting_report_permissions_and_errors(app: FastAPI, world: World, client: TestClient,
                                               db_session: Session) -> None:
    report = world.delayed_report()
    url = world.url(f"/status-reports/{report}/meeting-report")
    for who in ("pm", "planner", "executor", "progress", "approver", "viewer"):
        assert client.get(url, headers=world.h[who]).status_code == 200, who
    outsider = client.get(url, headers=world.h["outsider"])
    assert outsider.status_code == 403 and error_code(outsider) == "FORBIDDEN"
    assert client.get(url).status_code == 401
    missing = client.get(world.url(f"/status-reports/{MISSING}/meeting-report"), headers=world.h["viewer"])
    assert missing.status_code == 404 and error_code(missing) == "NOT_FOUND"
    assert client.get(f"/api/v1/projects/{MISSING}/status-reports/{report}/meeting-report",
                      headers=world.admin_h).status_code == 404
    other = World(client, db_session, code="PRJ-O")
    foreign = client.get(other.url(f"/status-reports/{report}/meeting-report"), headers=other.h["viewer"])
    assert foreign.status_code == 404  # 他のプロジェクトの報告日

    # 委託先だけで読む利用者：自分の割当の活動（B）だけ。A の遅延・M のマイルストーン・KPI は見せない
    app.state.project_access = ProjectMembersTable(FakeAssigned({world.users["contractor"].id: {world.b}}))
    body = world.meeting(report, who="contractor")
    assert body["restricted"] is True and body["kpi"] is None
    assert [r["activity_id"] for r in body["gantt"]["rows"]] == [world.b] and body["gantt"]["restricted"] is True
    assert [v["activity_id"] for v in body["lightning"]["vertices"]] == [world.b]
    assert [v["row_index"] for v in body["lightning"]["vertices"]] == [0]
    assert body["delays"] == [] and body["milestones"] == []

    # 確定前の報告日で日程が計算されていない活動があれば 409
    activity = db_session.get(Activity, world.b)
    assert activity is not None
    activity.start_date = None
    db_session.commit()
    unscheduled = client.get(url, headers=world.h["viewer"])
    assert unscheduled.status_code == 409 and error_code(unscheduled) == "INVALID_STATE"


# ---------------------------------------------------------------------------
# API-RPT-002〜004 共有リンク（RPT-03）
# ---------------------------------------------------------------------------


def test_create_share_link_returns_token_once_and_stores_hash(world: World, client: TestClient,
                                                              db_session: Session) -> None:
    created = world.share("GANTT")
    token = created["token"]
    assert (created["project_id"], created["scope"], created["revoked_at"]) == (world.pid, "GANTT", None)
    assert created["created_by"] == world.users["pm"].id and created["created_at"].startswith("2026-12-05T09:00:00")
    assert created["expires_at"].startswith("2026-12-30T15:00:00")  # UTC で保存する
    row = db_session.get(ShareLink, created["id"])
    assert row is not None and row.token_hash == hashlib.sha256(token.encode("utf-8")).hexdigest()
    assert token not in row.token_hash
    # 変更履歴には残るが、トークンそのものは書かない（4.2・NFR-04）
    logs = db_session.scalars(select(AuditLog).where(AuditLog.entity_type == "share_links")).all()
    assert [(log.action, log.entity_id) for log in logs] == [("CREATE", created["id"])]
    assert token not in str(logs[0].after)
    assert world.share("REPORT", who="progress")["scope"] == "REPORT"  # 進捗管理担当者も share.manage を持つ

    for who in ("planner", "executor", "approver", "viewer", "contractor", "outsider"):
        assert error_code(client.post(world.url("/share-links"), json={"scope": "GANTT",
                                                                       "expires_at": "2026-12-31T00:00:00Z"},
                                      headers=world.h[who])) == "FORBIDDEN", who
    assert client.post(world.url("/share-links"), json={"scope": "GANTT",
                                                        "expires_at": "2026-12-31T00:00:00Z"}).status_code == 401
    assert client.post(f"/api/v1/projects/{MISSING}/share-links", json={"scope": "GANTT",
                                                                        "expires_at": "2026-12-31T00:00:00Z"},
                       headers=world.admin_h).status_code == 404
    past = world.share("GANTT", expect=400, expires_at="2026-12-05T09:00:00Z")  # 今ちょうどは見られないので作らない
    assert past["error"]["code"] == "VALIDATION_ERROR"
    assert world.share("GANTT", expect=400, expires_at="2026-12-31T00:00:00")["error"]["code"] == "VALIDATION_ERROR"
    assert world.share("ALL", expect=400)["error"]["code"] == "VALIDATION_ERROR"
    assert world.share("GANTT", expect=400, token="x")["error"]["code"] == "VALIDATION_ERROR"


def test_shared_view_shows_scope_without_login(world: World, client: TestClient) -> None:
    gantt_token = world.share("GANTT")["token"]
    report_token = world.share("REPORT")["token"]

    gantt = client.get(f"/api/v1/shared/{gantt_token}")  # 認証なし
    assert gantt.status_code == 200, gantt.text
    body = gantt.json()
    assert (body["scope"], body["project"]) == ("GANTT", {"id": world.pid, "code": "PRJ-R", "name": "会議"})
    assert body["expires_at"].startswith("2026-12-30T15:00:00") and body["meeting_report"] is None
    assert [r["name"] for r in body["gantt"]["rows"]] == ["A", "B", "M"] and body["gantt"]["restricted"] is False

    empty = client.get(f"/api/v1/shared/{report_token}").json()
    assert (empty["scope"], empty["gantt"], empty["meeting_report"]) == ("REPORT", None, None)  # 報告日がまだ無い

    report = world.delayed_report()
    shared = client.get(f"/api/v1/shared/{report_token}").json()
    assert shared["gantt"] is None
    assert shared["meeting_report"] == world.meeting(report)  # 最新の報告日の会議レポートと同じもの
    # ガントは最新の進捗（その報告日の入力）で描く
    assert client.get(f"/api/v1/shared/{gantt_token}").json()["gantt"]["rows"][0]["percent_complete"] == 20.0
    # 読むだけ：共有リンクの道の書き込みは無い
    assert client.post(f"/api/v1/shared/{gantt_token}").status_code == 405


def test_revoked_expired_and_unknown_links_cannot_be_viewed(app: FastAPI, world: World, client: TestClient,
                                                           db_session: Session) -> None:
    link = world.share("GANTT")
    token, link_id = link["token"], link["id"]
    url = world.url(f"/share-links/{link_id}")
    assert error_code(client.delete(url, headers=world.h["planner"])) == "FORBIDDEN"
    assert client.delete(url).status_code == 401
    other = World(client, db_session, code="PRJ-O")
    assert client.delete(other.url(f"/share-links/{link_id}"), headers=other.h["pm"]).status_code == 404
    assert client.delete(world.url(f"/share-links/{MISSING}"), headers=world.h["pm"]).status_code == 404
    assert client.get(f"/api/v1/shared/{token}").status_code == 200  # 失効させる前は見られる

    revoked = client.delete(url, headers=world.h["progress"])
    assert revoked.status_code == 204, revoked.text
    row = db_session.get(ShareLink, link_id)
    assert row is not None
    db_session.refresh(row)
    assert row.revoked_at == NOW  # 行は消さない
    again = client.delete(url, headers=world.h["pm"])
    assert again.status_code == 409 and error_code(again) == "INVALID_STATE"
    gone = client.get(f"/api/v1/shared/{token}")
    assert gone.status_code == 404 and gone.json()["error"]["details"] == [{"reason": "REVOKED"}]

    unknown = client.get(f"/api/v1/shared/{generate_token()}")
    assert unknown.status_code == 404 and unknown.json()["error"]["details"] == [{"reason": "NOT_FOUND"}]

    # 有効期限の時刻ちょうどで見られなくなる（RULE-AUTH-002）
    expiring = world.share("REPORT", expires_at="2026-12-06T09:00:00Z")["token"]
    app.dependency_overrides[get_now] = lambda: datetime(2026, 12, 6, 8, 59, 59, tzinfo=UTC)
    assert client.get(f"/api/v1/shared/{expiring}").status_code == 200
    app.dependency_overrides[get_now] = lambda: datetime(2026, 12, 6, 9, 0, tzinfo=UTC)
    expired = client.get(f"/api/v1/shared/{expiring}")
    assert expired.status_code == 404 and expired.json()["error"]["details"] == [{"reason": "EXPIRED"}]


def test_list_share_links_without_tokens(world: World, client: TestClient, db_session: Session) -> None:
    """API-RPT-005 共有リンクの一覧（トークンは返さない。設計書 1.1・CR-004）。新しい順、失効したリンクも含む。"""
    first = world.share("GANTT")
    second = world.share("REPORT", who="progress")
    assert client.delete(world.url(f"/share-links/{first['id']}"), headers=world.h["pm"]).status_code == 204
    other = World(client, db_session, code="PRJ-O")
    other.share("GANTT")

    response = client.get(world.url("/share-links"), headers=world.h["progress"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["project_id"], body["total"], body["limit"], body["offset"]) == (world.pid, 2, 100, 0)
    assert [i["id"] for i in body["items"]] == [second["id"], first["id"]]  # 他のプロジェクトのリンクは含まない
    assert body["items"][1]["revoked_at"].startswith("2026-12-05T09:00:00")
    assert body["items"][0] == {k: v for k, v in second.items() if k != "token"}
    for token in (first["token"], second["token"]):
        assert token not in response.text
    assert all("token" not in i and "token_hash" not in i for i in body["items"])

    paged = client.get(world.url("/share-links"), params={"limit": 1, "offset": 1}, headers=world.h["pm"]).json()
    assert (paged["total"], [i["id"] for i in paged["items"]]) == (2, [first["id"]])
    assert client.get(world.url("/share-links"), params={"limit": 0}, headers=world.h["pm"]).status_code == 400
    for who in ("planner", "executor", "approver", "viewer", "contractor", "outsider"):
        assert error_code(client.get(world.url("/share-links"), headers=world.h[who])) == "FORBIDDEN", who
    assert client.get(world.url("/share-links")).status_code == 401
    assert client.get(f"/api/v1/projects/{MISSING}/share-links", headers=world.admin_h).status_code == 404
