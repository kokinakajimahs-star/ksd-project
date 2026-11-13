"""見積の API の試験（設計書 6.2 の API-EST-001〜003、4.1・5.8、9 章。要求仕様書 EST-01・02・08）。

SQLite の一時ファイルの DB と TestClient で、正常系と 401・403・404・409・400 のうち該当するものを確かめる。
WBS は 成果物 D の下に WP1（活動 A・B）と WP2（活動なし）、管理 M の下に WP3（活動 C。委託先の割当）。
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

from app.infra.security import generate_token, hash_token
from app.models.chg import AuditLog
from app.models.cmn import AuthSession, User
from app.models.est import EstimateRecord
from app.models.prj import Calendar, Project
from app.models.sch import Activity
from app.models.wbs import WbsElement
from app.services.clock import get_now

NOW = datetime(2026, 12, 10, 9, 0, tzinfo=UTC)
MISSING = 9999
ROLES = (("pm", "PM"), ("planner", "PLANNER"), ("executor", "EXECUTOR"), ("viewer", "VIEWER"),
         ("contractor", "CONTRACTOR"))


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
    """管理者が作ったプロジェクト（開始日 2026-12-01）と 5 つのロールのメンバー・メンバーでない利用者。"""

    def __init__(self, client: TestClient, session: Session, code: str = "PRJ-EST") -> None:
        self.client = client
        self.session = session
        self.admin = make_user(session, f"admin-{code}", is_system_admin=True)
        self.admin_h = login(session, self.admin)
        body = {"code": code, "name": "見積", "start_date": "2026-12-01", "owner_user_id": self.admin.id}
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
        self.d = self.element("成果物", None, "DELIVERABLE")
        self.wp1 = self.element("WP1", self.d, "WORK_PACKAGE")
        self.wp2 = self.element("WP2", self.d, "WORK_PACKAGE")
        self.m = self.element("管理", None, "MANAGEMENT")
        self.wp3 = self.element("WP3", self.m, "WORK_PACKAGE")
        self.a = self.add(self.wp1, "A", 5)
        self.b = self.add(self.wp1, "B", 3)
        self.c = self.add(self.wp3, "C", 2)
        assigned = client.put(self.url(f"/activities/{self.c}/assignments"),
                              json={"assignments": [{"user_id": self.uid("contractor")}]}, headers=self.h["planner"])
        assert assigned.status_code == 200, assigned.text

    def uid(self, name: str) -> int:
        return self.users[name].id

    def url(self, path: str) -> str:
        return f"/api/v1/projects/{self.pid}{path}"

    def element(self, name: str, parent_id: int | None, kind: str) -> int:
        response = self.client.post(self.url("/wbs/elements"), json={"parent_id": parent_id, "kind": kind, "name": name},
                                    headers=self.h["planner"])
        assert response.status_code == 201, response.text
        return int(response.json()["id"])

    def add(self, wp: int, name: str, duration_days: int) -> int:
        response = self.client.post(self.url("/activities"),
                                    json={"work_package_id": wp, "name": name, "duration_days": duration_days},
                                    headers=self.h["planner"])
        assert response.status_code == 201, response.text
        return int(response.json()["id"])

    def put(self, target_type: str, target_id: int, body: dict[str, Any], who: str = "planner",
            expect: int = 200) -> Any:
        response = self.client.put(self.url(f"/estimates/{target_type}/{target_id}"), json=body, headers=self.h[who])
        assert response.status_code == expect, response.text
        return response.json()

    def history(self, target_type: str, target_id: int, who: str = "viewer", expect: int = 200,
                **params: Any) -> Any:
        response = self.client.get(self.url(f"/estimates/{target_type}/{target_id}/history"), params=params,
                                   headers=self.h[who])
        assert response.status_code == expect, response.text
        return response.json()

    def rollup(self, who: str = "viewer", expect: int = 200) -> Any:
        response = self.client.get(self.url("/estimates/rollup"), headers=self.h[who])
        assert response.status_code == expect, response.text
        return response.json()

    def set_wbs_status(self, status: str) -> None:
        self.session.expire_all()
        project = self.session.get(Project, self.pid)
        assert project is not None
        project.wbs_status = status
        self.session.commit()

    def wbs_status(self) -> str:
        self.session.expire_all()
        project = self.session.get(Project, self.pid)
        assert project is not None
        return project.wbs_status


@pytest.fixture()
def world(client: TestClient, db_session: Session) -> World:
    return World(client, db_session)


# ---------------------------------------------------------------------------
# API-EST-001 見積を書く
# ---------------------------------------------------------------------------


def test_put_activity_estimate_in_hours_records_and_updates_current(world: World) -> None:
    """API-EST-001 見積を書く：人時の入力はそのまま記録し、活動の今の見積（activities.estimate_hours）を同じ値にする（EST-01・EST-08）。"""
    body = {"input_value": "12.5", "input_unit": "HOUR", "method": "THREE_POINT",
            "assumptions": "既存の画面を流用する", "references": "類似案件 X の実績"}
    written = world.put("ACTIVITY", world.a, body)
    record = written["record"]
    assert (record["target_type"], record["target_id"], record["project_id"]) == ("ACTIVITY", world.a, world.pid)
    assert (record["hours"], record["input_value"], record["input_unit"]) == (12.5, 12.5, "HOUR")
    assert (record["method"], record["assumptions"], record["references"]) == (
        "THREE_POINT", "既存の画面を流用する", "類似案件 X の実績")
    assert (record["entered_by"], record["entered_by_name"]) == (world.uid("planner"), "planner-PRJ-ESTさん")
    assert record["entered_at"].startswith("2026-12-10T09:00:00")
    assert written["current_hours"] == 12.5
    assert written["project_estimate_hours"] == 12.5
    world.session.expire_all()
    activity = world.session.get(Activity, world.a)
    assert activity is not None and activity.estimate_hours == Decimal("12.50")
    rows = list(world.session.scalars(select(EstimateRecord).where(EstimateRecord.target_id == world.a)))
    assert len(rows) == 1 and rows[0].id == record["id"] and rows[0].hours == Decimal("12.50")


def test_put_estimate_in_days_converts_with_calendar_hours_per_day(world: World) -> None:
    """API-EST-001 見積を書く：人日は稼働カレンダーの 1 日の稼働時間を掛けて人時へ換算し、入力の値と単位はそのまま残す（RULE-EST-002）。"""
    written = world.put("ACTIVITY", world.b, {"input_value": "2.5", "input_unit": "DAY"})
    assert (written["record"]["hours"], written["record"]["input_value"], written["record"]["input_unit"]) == (
        20.0, 2.5, "DAY")
    assert written["record"]["method"] is None and written["record"]["assumptions"] is None
    world.session.expire_all()
    calendar = world.session.scalar(select(Calendar).where(Calendar.project_id == world.pid))
    assert calendar is not None
    calendar.hours_per_day = Decimal("7.5")
    world.session.commit()
    again = world.put("ACTIVITY", world.b, {"input_value": "3", "input_unit": "DAY"})
    assert again["record"]["hours"] == 22.5 and again["current_hours"] == 22.5


def test_put_element_estimate_updates_own_estimate_and_wbs_status(world: World) -> None:
    """API-EST-001 見積を書く：WBS要素の見積は own_estimate_hours へ反映し、基準化の後なら WBS を DRAFT へ戻す（RULE-WBS-006 の EDIT）。"""
    world.set_wbs_status("BASELINED")
    written = world.put("ELEMENT", world.wp2, {"input_value": "16", "input_unit": "HOUR", "method": "ANALOGOUS"})
    assert written["current_hours"] == 16.0 and written["wbs_status"] == "DRAFT"
    assert written["project_estimate_hours"] == 16.0  # 活動の無い WP は自分の見積が集計値（5.8）
    world.session.expire_all()
    element = world.session.get(WbsElement, world.wp2)
    assert element is not None and element.own_estimate_hours == Decimal("16.00")
    assert world.wbs_status() == "DRAFT"


def test_put_activity_estimate_keeps_wbs_status(world: World) -> None:
    """API-EST-001 見積を書く：活動の見積は WBS の状態を変えない（承認待ちでも書ける）。"""
    world.set_wbs_status("PENDING_APPROVAL")
    written = world.put("ACTIVITY", world.a, {"input_value": "8", "input_unit": "HOUR"})
    assert written["wbs_status"] == "PENDING_APPROVAL"
    assert world.wbs_status() == "PENDING_APPROVAL"


def test_put_element_estimate_while_pending_approval_is_409(world: World) -> None:
    """API-EST-001 見積を書く：承認待ちの間は WBS要素の見積を書けない（409 INVALID_STATE）。記録も今の見積も変えない。"""
    world.set_wbs_status("PENDING_APPROVAL")
    error = world.put("ELEMENT", world.wp2, {"input_value": "16", "input_unit": "HOUR"}, expect=409)
    assert error["error"]["code"] == "INVALID_STATE"
    world.session.expire_all()
    element = world.session.get(WbsElement, world.wp2)
    assert element is not None and element.own_estimate_hours is None
    assert world.session.scalar(select(EstimateRecord.id).where(EstimateRecord.project_id == world.pid)) is None


@pytest.mark.parametrize(
    "body",
    [
        {"input_value": "-1", "input_unit": "HOUR"},  # 負の値（EST-01）
        {"input_value": "1.234", "input_unit": "HOUR"},  # 小数 3 桁
        {"input_value": "1", "input_unit": "WEEK"},  # 単位の誤り
        {"input_value": "1", "input_unit": "HOUR", "method": "GUESS"},  # 手法の誤り
        {"input_unit": "HOUR"},  # 値が無い
        {"input_value": "1", "input_unit": "HOUR", "note": "x"},  # 知らない項目
        {"input_value": "99999999.99", "input_unit": "DAY"},  # 人時へ換算すると Numeric(10,2) に入らない
    ],
)
def test_put_estimate_rejects_invalid_values_with_400(world: World, body: dict[str, Any]) -> None:
    """API-EST-001 見積を書く：負の値・桁・単位・手法の誤りは 400 VALIDATION_ERROR で、何も書かない。"""
    error = world.put("ACTIVITY", world.a, body, expect=400)
    assert error["error"]["code"] == "VALIDATION_ERROR"
    world.session.expire_all()
    assert world.session.scalar(select(EstimateRecord.id).where(EstimateRecord.project_id == world.pid)) is None


def test_put_estimate_zero_is_allowed(world: World) -> None:
    """API-EST-001 見積を書く：0 は負ではないので書ける（境界）。"""
    written = world.put("ACTIVITY", world.a, {"input_value": "0", "input_unit": "DAY"})
    assert written["record"]["hours"] == 0.0 and written["current_hours"] == 0.0


def test_put_estimate_unknown_target_type_is_400_and_missing_target_is_404(world: World, client: TestClient) -> None:
    """API-EST-001 見積を書く：対象の種類の誤りは 400、そのプロジェクトに無い対象・プロジェクトは 404。"""
    body = {"input_value": "1", "input_unit": "HOUR"}
    world.put("SCOPE", world.a, body, expect=400)
    assert world.put("ACTIVITY", MISSING, body, expect=404)["error"]["code"] == "NOT_FOUND"
    assert world.put("ELEMENT", MISSING, body, expect=404)["error"]["code"] == "NOT_FOUND"
    other = World(client, world.session, code="PRJ-OTHER")
    assert world.put("ACTIVITY", other.a, body, expect=404)["error"]["code"] == "NOT_FOUND"
    assert world.put("ELEMENT", other.wp2, body, expect=404)["error"]["code"] == "NOT_FOUND"
    response = client.put(f"/api/v1/projects/{MISSING}/estimates/ACTIVITY/{world.a}", json=body,
                          headers=world.admin_h)
    assert response.status_code == 404


@pytest.mark.parametrize("who", ["executor", "viewer", "contractor", "outsider"])
def test_put_estimate_without_estimate_edit_is_403(world: World, who: str) -> None:
    """API-EST-001 見積を書く：estimate.edit を持たない利用者（9.2：PM・PLANNER だけが持つ）は 403。"""
    error = world.put("ACTIVITY", world.c, {"input_value": "1", "input_unit": "HOUR"}, who=who, expect=403)
    assert error["error"]["code"] == "FORBIDDEN"


def test_put_estimate_by_pm_is_allowed_and_requires_login(world: World) -> None:
    """API-EST-001 見積を書く：PM も書ける。トークンが無ければ 401。"""
    world.put("ACTIVITY", world.c, {"input_value": "1", "input_unit": "HOUR"}, who="pm")
    response = world.client.put(world.url(f"/estimates/ACTIVITY/{world.c}"),
                                json={"input_value": "1", "input_unit": "HOUR"})
    assert response.status_code == 401


def test_put_estimate_is_recorded_in_audit_logs(world: World) -> None:
    """API-EST-001 見積を書く：記録の追加と今の見積の更新が、変更者つきで変更履歴に残る（6.1・NFR-05）。"""
    world.put("ACTIVITY", world.a, {"input_value": "4", "input_unit": "HOUR"})
    world.session.expire_all()
    logs = list(world.session.scalars(select(AuditLog).where(AuditLog.project_id == world.pid,
                                                             AuditLog.entity_type.in_(["estimate_records",
                                                                                       "activities"]),
                                                             AuditLog.occurred_at >= NOW)))
    created = [log for log in logs if log.entity_type == "estimate_records"]
    updated = [log for log in logs if log.entity_type == "activities" and log.entity_id == world.a
               and log.action == "UPDATE" and (log.after or {}).get("estimate_hours") is not None]
    assert len(created) == 1 and created[0].action == "CREATE" and created[0].user_id == world.uid("planner")
    assert len(updated) == 1 and updated[0].user_id == world.uid("planner")


# ---------------------------------------------------------------------------
# API-EST-002 見積の履歴
# ---------------------------------------------------------------------------


def test_history_lists_records_oldest_first_with_paging(world: World) -> None:
    """API-EST-002 見積の履歴：見積値を変えた履歴が古い順に残る（EST-08）。limit・offset で区切れ、total は全件。"""
    world.put("ACTIVITY", world.a, {"input_value": "10", "input_unit": "HOUR", "method": "EXPERT"})
    world.put("ACTIVITY", world.a, {"input_value": "2", "input_unit": "DAY", "method": "BOTTOM_UP",
                                    "assumptions": "詳細化した"}, who="pm")
    world.put("ACTIVITY", world.b, {"input_value": "1", "input_unit": "HOUR"})
    history = world.history("ACTIVITY", world.a)
    assert (history["target_type"], history["target_id"], history["target_exists"]) == ("ACTIVITY", world.a, True)
    assert history["current_hours"] == 16.0
    assert history["total"] == 2 and (history["limit"], history["offset"]) == (100, 0)
    assert [(r["hours"], r["input_unit"], r["method"], r["entered_by"]) for r in history["items"]] == [
        (10.0, "HOUR", "EXPERT", world.uid("planner")), (16.0, "DAY", "BOTTOM_UP", world.uid("pm"))]
    assert history["items"][1]["assumptions"] == "詳細化した"
    page = world.history("ACTIVITY", world.a, limit=1, offset=1)
    assert page["total"] == 2 and [r["hours"] for r in page["items"]] == [16.0]
    world.history("ACTIVITY", world.a, limit=0, expect=400)
    world.history("ACTIVITY", world.a, limit=1001, expect=400)


def test_history_of_target_without_records_is_empty(world: World) -> None:
    """API-EST-002 見積の履歴：記録の無い対象は空の一覧（今の見積も空）。"""
    history = world.history("ELEMENT", world.wp2)
    assert history["items"] == [] and history["total"] == 0 and history["current_hours"] is None


def test_history_survives_deleted_target_and_missing_is_404(world: World) -> None:
    """API-EST-002 見積の履歴：対象を消しても記録は残って読める（target_exists が偽）。対象も記録も無ければ 404。"""
    world.put("ACTIVITY", world.b, {"input_value": "3", "input_unit": "HOUR"})
    deleted = world.client.delete(world.url(f"/activities/{world.b}"), headers=world.h["planner"])
    assert deleted.status_code == 204, deleted.text
    history = world.history("ACTIVITY", world.b)
    assert history["target_exists"] is False and history["current_hours"] is None
    assert [r["hours"] for r in history["items"]] == [3.0]
    assert world.history("ACTIVITY", MISSING, expect=404)["error"]["code"] == "NOT_FOUND"
    world.history("PROJECT", world.a, expect=400)


def test_history_for_contractor_is_limited_to_own_assignment_path(world: World) -> None:
    """API-EST-002 見積の履歴：委託先は自分の割当の活動とその WBS の経路だけ読める（9.2 の注記・RULE-AUTH-001）。他は 404。"""
    world.put("ACTIVITY", world.c, {"input_value": "5", "input_unit": "HOUR"})
    world.put("ACTIVITY", world.a, {"input_value": "5", "input_unit": "HOUR"})
    assert world.history("ACTIVITY", world.c, who="contractor")["total"] == 1
    assert world.history("ELEMENT", world.wp3, who="contractor")["total"] == 0
    assert world.history("ELEMENT", world.m, who="contractor")["total"] == 0
    world.history("ACTIVITY", world.a, who="contractor", expect=404)
    world.history("ELEMENT", world.wp1, who="contractor", expect=404)


def test_history_requires_project_view(world: World) -> None:
    """API-EST-002 見積の履歴：メンバーでない利用者は 403、トークンが無ければ 401。"""
    assert world.history("ACTIVITY", world.a, who="outsider", expect=403)["error"]["code"] == "FORBIDDEN"
    response = world.client.get(world.url(f"/estimates/ACTIVITY/{world.a}/history"))
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# API-EST-003 ボトムアップの集計
# ---------------------------------------------------------------------------


def test_rollup_sums_activity_to_wp_to_upper_to_project(world: World) -> None:
    """API-EST-003 ボトムアップの集計：活動→WP→上位→プロジェクトへ合計し、活動の無い WP は自分の見積を使う（5.8・EST-02）。"""
    world.put("ACTIVITY", world.a, {"input_value": "10", "input_unit": "HOUR"})
    world.put("ACTIVITY", world.b, {"input_value": "1", "input_unit": "DAY"})  # 8 人時
    world.put("ELEMENT", world.wp1, {"input_value": "100", "input_unit": "HOUR"})  # 活動のある WP の自分の見積は使わない
    world.put("ELEMENT", world.wp2, {"input_value": "6", "input_unit": "HOUR"})
    world.put("ELEMENT", world.m, {"input_value": "50", "input_unit": "HOUR"})  # WP 以外の要素は子の合計（OQ-13）
    rollup = world.rollup()
    assert rollup["project_id"] == world.pid and rollup["restricted"] is False
    by_id = {e["element_id"]: e for e in rollup["elements"]}
    assert [e["element_id"] for e in rollup["elements"]] == [world.d, world.wp1, world.wp2, world.m, world.wp3]
    assert [e["depth"] for e in rollup["elements"]] == [1, 2, 2, 1, 2]
    assert (by_id[world.wp1]["hours"], by_id[world.wp1]["source"], by_id[world.wp1]["lower_hours"],
            by_id[world.wp1]["own_estimate_hours"]) == (18.0, "ACTIVITIES", 18.0, 100.0)
    assert (by_id[world.wp2]["hours"], by_id[world.wp2]["source"], by_id[world.wp2]["lower_hours"]) == (
        6.0, "OWN_ESTIMATE", None)
    assert (by_id[world.d]["hours"], by_id[world.d]["source"], by_id[world.d]["lower_hours"]) == (24.0, "CHILDREN", 24.0)
    assert (by_id[world.wp3]["hours"], by_id[world.wp3]["source"]) == (0.0, "ACTIVITIES")  # 未見積の活動は 0
    assert (by_id[world.m]["hours"], by_id[world.m]["own_estimate_hours"]) == (0.0, 50.0)
    assert rollup["project_estimate_hours"] == 24.0
    assert [(a["activity_id"], a["hours"], a["estimate_hours"]) for a in rollup["activities"]] == [
        (world.a, 10.0, 10.0), (world.b, 8.0, 8.0), (world.c, 0.0, None)]
    # 下位を変えると上位の値が変わる（EST-02 の受入基準）
    world.put("ACTIVITY", world.c, {"input_value": "4", "input_unit": "HOUR"})
    after = world.rollup()
    after_by_id = {e["element_id"]: e for e in after["elements"]}
    assert (after_by_id[world.wp3]["hours"], after_by_id[world.m]["hours"], after["project_estimate_hours"]) == (
        4.0, 4.0, 28.0)
    tree = world.client.get(world.url("/wbs"), headers=world.h["viewer"]).json()
    assert tree["project_estimate_hours"] == after["project_estimate_hours"]  # API-WBS-001 と同じ値


def test_rollup_of_empty_project_is_zero(world: World, client: TestClient) -> None:
    """API-EST-003 ボトムアップの集計：要素の無いプロジェクトは 0（境界）。"""
    admin_h = world.admin_h
    created = client.post("/api/v1/projects", json={"code": "PRJ-EMPTY", "name": "空", "start_date": "2026-12-01",
                                                    "owner_user_id": world.admin.id}, headers=admin_h)
    assert created.status_code == 201, created.text
    response = client.get(f"/api/v1/projects/{created.json()['id']}/estimates/rollup", headers=admin_h)
    assert response.status_code == 200, response.text
    assert response.json()["project_estimate_hours"] == 0.0 and response.json()["elements"] == []


def test_rollup_for_contractor_is_restricted(world: World) -> None:
    """API-EST-003 ボトムアップの集計：委託先は自分の割当の活動とその経路の要素だけ（restricted が真）。"""
    world.put("ACTIVITY", world.a, {"input_value": "10", "input_unit": "HOUR"})
    world.put("ACTIVITY", world.c, {"input_value": "3", "input_unit": "HOUR"})
    rollup = world.rollup(who="contractor")
    assert rollup["restricted"] is True
    assert [e["element_id"] for e in rollup["elements"]] == [world.m, world.wp3]
    assert [a["activity_id"] for a in rollup["activities"]] == [world.c]
    assert rollup["project_estimate_hours"] == 3.0
    tree = world.client.get(world.url("/wbs"), headers=world.h["contractor"]).json()
    assert tree["project_estimate_hours"] == rollup["project_estimate_hours"]


def test_rollup_for_contractor_sums_only_visible_activities(world: World) -> None:
    """API-EST-003・API-WBS-001（9.2 の注記。CR-011）：委託先の集計値は見える要素・活動だけから求める。

    WP3 には委託先の割当の無い活動 D（6 時間）もある。委託先の WP3・管理・プロジェクトの値は C（3 時間）だけ。
    全て読める利用者の値は D を含む。
    """
    d = world.add(world.wp3, "D", 1)
    world.put("ACTIVITY", world.c, {"input_value": "3", "input_unit": "HOUR"})
    world.put("ACTIVITY", d, {"input_value": "6", "input_unit": "HOUR"})
    full = world.rollup()
    assert {e["element_id"]: e["hours"] for e in full["elements"]}[world.wp3] == 9.0
    assert full["project_estimate_hours"] == 9.0

    rollup = world.rollup(who="contractor")
    assert [(e["element_id"], e["hours"], e["lower_hours"]) for e in rollup["elements"]] == [
        (world.m, 3.0, 3.0), (world.wp3, 3.0, 3.0)]
    assert [a["activity_id"] for a in rollup["activities"]] == [world.c]
    assert rollup["project_estimate_hours"] == 3.0
    tree = world.client.get(world.url("/wbs"), headers=world.h["contractor"]).json()
    assert [(e["id"], e["rollup_hours"]) for e in tree["elements"]] == [(world.m, 3.0), (world.wp3, 3.0)]
    assert tree["project_estimate_hours"] == 3.0


def test_rollup_requires_project_view(world: World, client: TestClient) -> None:
    """API-EST-003 ボトムアップの集計：メンバーでない利用者は 403、無いプロジェクトは 404、トークンが無ければ 401。"""
    assert world.rollup(who="outsider", expect=403)["error"]["code"] == "FORBIDDEN"
    assert client.get(f"/api/v1/projects/{MISSING}/estimates/rollup", headers=world.admin_h).status_code == 404
    assert client.get(world.url("/estimates/rollup")).status_code == 401
