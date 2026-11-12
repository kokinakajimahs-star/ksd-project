"""変更管理の API の試験（設計書 6.2 の API-CHG-001〜010、5.9・5.10、9 章。要求仕様書 CHG-01〜04・06・09、BR-12）。

SQLite の一時ファイルの DB と TestClient で、正常系と 401・403・404・409・400・422 のうち該当するものを確かめる。
プロジェクトの開始日は 2026-12-01（火）、カレンダーは既定の月〜金。今は 2026-12-10 09:00 UTC に固定する。
WBS は 成果物 D の下に WP1（活動 A：5 日 → B：3 日の依存）と WP2（活動なし）。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.infra.security import generate_token, hash_token
from app.models.chg import AuditLog, ChangeRequest
from app.models.cmn import AuthSession, User
from app.models.sch import Activity, ScheduleBaseline, ScheduleBaselineItem
from app.models.wbs import ApprovalRequest, ScopeBaseline, ScopeBaselineElement, WbsElement, WbsNumberingRule
from app.services.change import ChangeRequestStateMachine
from app.services.clock import get_now

NOW = datetime(2026, 12, 10, 9, 0, tzinfo=UTC)
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


class World:
    def __init__(self, client: TestClient, session: Session, code: str = "PRJ-C") -> None:
        self.client = client
        self.session = session
        self.admin = make_user(session, f"admin-{code}", is_system_admin=True)
        self.admin_h = login(session, self.admin)
        body = {"code": code, "name": "変更", "start_date": "2026-12-01", "owner_user_id": self.admin.id}
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
        self.d, self.d_code = self.element("成果物", None, "DELIVERABLE")
        self.wp1, self.wp1_code = self.element("WP1", self.d, "WORK_PACKAGE")
        self.wp2, self.wp2_code = self.element("WP2", self.d, "WORK_PACKAGE")
        self.a = self.activity("A", 5, self.wp1)
        self.b = self.activity("B", 3, self.wp1)
        dep = client.post(self.url("/dependencies"), json={"predecessor_id": self.a, "successor_id": self.b},
                          headers=self.h["planner"])
        assert dep.status_code == 201, dep.text

    def uid(self, name: str) -> int:
        return self.users[name].id

    def url(self, path: str) -> str:
        return f"/api/v1/projects/{self.pid}{path}"

    def element(self, name: str, parent_id: int | None, kind: str) -> tuple[int, str]:
        response = self.client.post(self.url("/wbs/elements"), json={"parent_id": parent_id, "kind": kind, "name": name},
                                    headers=self.h["planner"])
        assert response.status_code == 201, response.text
        return int(response.json()["id"]), str(response.json()["wbs_code"])

    def activity(self, name: str, duration_days: int, work_package_id: int) -> int:
        response = self.client.post(self.url("/activities"),
                                    json={"work_package_id": work_package_id, "name": name,
                                          "duration_days": duration_days},
                                    headers=self.h["planner"])
        assert response.status_code == 201, response.text
        return int(response.json()["id"])

    def add_c_after_b(self) -> list[dict[str, Any]]:
        """WP1 に活動 C（2 日・16 時間）を足し、B → C の依存を張る変更内容（5.9）。"""
        return [
            {"op": "add_activity", "wp_code": self.wp1_code, "name": "C", "duration_days": 2, "estimate_hours": 16,
             "ref": "c"},
            {"op": "add_dependency", "predecessor": self.b, "successor": "c"},
        ]

    def create(self, who: str = "planner", expect: int = 201, **overrides: Any) -> Any:
        body: dict[str, Any] = {"title": "C を足す", "reason": "顧客の追加要望", "target_codes": [self.wp1_code],
                                "operations": self.add_c_after_b()}
        body.update(overrides)
        response = self.client.post(self.url("/change-requests"), json=body, headers=self.h[who])
        assert response.status_code == expect, response.text
        return response.json()

    def submit(self, cr_id: int, who: str = "planner", expect: int = 200) -> Any:
        response = self.client.post(self.url(f"/change-requests/{cr_id}/submit"), headers=self.h[who])
        assert response.status_code == expect, response.text
        return response.json()

    def decide(self, approval_id: int, decision: str, who: str = "approver", expect: int = 200) -> Any:
        response = self.client.post(f"/api/v1/approval-requests/{approval_id}/decision", json={"decision": decision},
                                    headers=self.h[who])
        assert response.status_code == expect, response.text
        return response.json()

    def approved(self, **overrides: Any) -> Any:
        created = self.create(**overrides)
        submitted = self.submit(created["id"])
        self.decide(submitted["approval_request_id"], "APPROVE")
        return created

    def baseline_wbs(self) -> Any:
        """WBS を承認して BASELINED にする（5.10：完了条件を書き、申請し、承認する。スコープの版 1 ＝ INITIAL_APPROVAL）。"""
        for element_id in (self.d, self.wp1, self.wp2):
            put = self.client.put(self.url(f"/wbs/elements/{element_id}/dictionary"),
                                  json={"deliverable": "成果物", "completion_criteria": "承認済み"},
                                  headers=self.h["planner"])
            assert put.status_code == 200, put.text
        submitted = self.client.post(self.url("/wbs/approval-requests"), headers=self.h["planner"])
        assert submitted.status_code == 201, submitted.text
        decided = self.decide(submitted.json()["id"], "APPROVE")
        assert decided["wbs_status"] == "BASELINED"
        assert (decided["scope_baseline"]["version"], decided["scope_baseline"]["reason"]) == (1, "INITIAL_APPROVAL")
        return decided

    def apply(self, cr_id: int, who: str = "pm", expect: int = 200) -> Any:
        response = self.client.post(self.url(f"/change-requests/{cr_id}/apply"), headers=self.h[who])
        assert response.status_code == expect, response.text
        return response.json()

    def action(self, who: str = "progress", expect: int = 201, **body: Any) -> Any:
        payload: dict[str, Any] = {"cause": "要員の不足", "action": "応援を入れる"}
        payload.update(body)
        response = self.client.post(self.url("/corrective-actions"), json=payload, headers=self.h[who])
        assert response.status_code == expect, response.text
        return response.json()

    def count(self, model: Any) -> int:
        self.session.expire_all()
        return int(self.session.scalar(select(func.count()).select_from(model)) or 0)


@pytest.fixture()
def world(client: TestClient, db_session: Session) -> World:
    return World(client, db_session)


def error_code(response: Any) -> str:
    return str(response.json()["error"]["code"])


# ---------------------------------------------------------------------------
# API-CHG-001 変更要求を作る
# ---------------------------------------------------------------------------


def test_create_change_request_numbers_in_project_and_starts_as_draft(world: World) -> None:
    """API-CHG-001 変更要求を作る：番号はプロジェクトの中の連番、状態は DRAFT、本計画は変えない（CHG-01）。"""
    activities_before = world.count(Activity)
    first = world.create()
    assert first["number"] == 1
    assert first["status"] == "DRAFT"
    assert first["target_codes"] == [world.wp1_code]
    assert first["operations"][0]["op"] == "add_activity"
    assert first["created_by"] == world.uid("planner")
    assert first["impact"] is None and first["approval_request_id"] is None
    second = world.create(who="pm", target_codes=[world.d_code, world.wp2_code])
    assert second["number"] == 2
    assert world.count(Activity) == activities_before  # 起票は計画を変えない


def test_create_change_request_rejects_bad_input_and_roles(world: World) -> None:
    """API-CHG-001 変更要求を作る：権限（change.create）・対象の WBS ID・変更内容（RULE-CHG-001）の検査。"""
    world.create(who="executor", expect=403)
    world.create(who="viewer", expect=403)
    world.create(who="outsider", expect=403)
    assert world.client.post(world.url("/change-requests"), json={}).status_code == 401
    missing_target = world.create(expect=422, target_codes=["9.9"])
    assert missing_target["error"]["code"] == "REFERENCE_NOT_FOUND"
    unknown_op = world.create(expect=400, operations=[{"op": "rename_everything"}])
    assert unknown_op["error"]["code"] == "VALIDATION_ERROR"
    assert unknown_op["error"]["details"][0]["index"] == 0
    unknown_wp = world.create(expect=422, operations=[
        {"op": "add_activity", "wp_code": "9.9", "name": "X", "duration_days": 1, "estimate_hours": 1, "ref": "x"}])
    assert unknown_wp["error"]["code"] == "REFERENCE_NOT_FOUND"
    cycle = world.create(expect=422, operations=[{"op": "add_dependency", "predecessor": world.b, "successor": world.a}])
    assert cycle["error"]["code"] == "SCHEDULE_CYCLE"
    world.create(expect=400, title="")
    world.create(expect=400, target_codes=[])
    missing = world.client.post(f"/api/v1/projects/{MISSING}/change-requests",
                                json={"title": "t", "reason": "r", "target_codes": ["1"]}, headers=world.h["planner"])
    assert missing.status_code == 404
    assert world.count(ChangeRequest) == 0


# ---------------------------------------------------------------------------
# API-CHG-002 変更要求の一覧
# ---------------------------------------------------------------------------


def test_list_change_requests_filters_by_status_and_contractor_scope(world: World) -> None:
    """API-CHG-002 変更要求の一覧（状態で絞れる）：project.view、番号の順、委託先は自分の WBS の経路の変更要求だけ。"""
    first = world.create()
    world.create(target_codes=[world.wp2_code], operations=[])
    world.submit(first["id"])
    listed = world.client.get(world.url("/change-requests"), headers=world.h["viewer"])
    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] == 2 and [i["number"] for i in body["items"]] == [1, 2]
    submitted = world.client.get(world.url("/change-requests"), params={"status": "SUBMITTED"},
                                 headers=world.h["viewer"]).json()
    assert [i["number"] for i in submitted["items"]] == [1]
    assert submitted["items"][0]["approval_request_id"] is not None
    paged = world.client.get(world.url("/change-requests"), params={"limit": 1, "offset": 1},
                             headers=world.h["viewer"]).json()
    assert paged["total"] == 2 and [i["number"] for i in paged["items"]] == [2]
    assert world.client.get(world.url("/change-requests"), params={"status": "OPEN"},
                            headers=world.h["viewer"]).status_code == 400
    assert world.client.get(world.url("/change-requests"), headers=world.h["outsider"]).status_code == 403
    # 委託先：活動 A の担当にすると、WP1（と D）を対象にした変更要求だけが見える
    assign = world.client.put(world.url(f"/activities/{world.a}/assignments"),
                              json={"assignments": [{"user_id": world.uid("contractor"), "planned_hours": 8}]},
                              headers=world.h["planner"])
    assert assign.status_code == 200, assign.text
    seen = world.client.get(world.url("/change-requests"), headers=world.h["contractor"]).json()
    assert [i["number"] for i in seen["items"]] == [1]


# ---------------------------------------------------------------------------
# API-CHG-003 変更要求を直す
# ---------------------------------------------------------------------------


def test_update_change_request_only_while_draft(world: World) -> None:
    """API-CHG-003 変更要求を直す（DRAFT のときだけ）：載せた項目だけ変わり、提出の後は 409 INVALID_STATE。"""
    created = world.create()
    url = world.url(f"/change-requests/{created['id']}")
    patched = world.client.patch(url, json={"title": "C と D を足す", "target_codes": [world.d_code]},
                                 headers=world.h["planner"])
    assert patched.status_code == 200, patched.text
    assert patched.json()["title"] == "C と D を足す"
    assert patched.json()["target_codes"] == [world.d_code]
    assert patched.json()["reason"] == "顧客の追加要望"
    assert len(patched.json()["operations"]) == 2
    bad = world.client.patch(url, json={"operations": [{"op": "remove_activity", "activity_id": MISSING}]},
                             headers=world.h["planner"])
    assert bad.status_code == 422 and error_code(bad) == "REFERENCE_NOT_FOUND"
    assert world.client.patch(url, json={"title": "x"}, headers=world.h["executor"]).status_code == 403
    assert world.client.patch(world.url(f"/change-requests/{MISSING}"), json={"title": "x"},
                              headers=world.h["planner"]).status_code == 404
    world.submit(created["id"])
    late = world.client.patch(url, json={"title": "提出の後"}, headers=world.h["planner"])
    assert late.status_code == 409 and error_code(late) == "INVALID_STATE"


# ---------------------------------------------------------------------------
# API-CHG-004 影響分析
# ---------------------------------------------------------------------------


def test_impact_analysis_reports_differences_without_writing(world: World) -> None:
    """API-CHG-004 影響分析を流す（本計画は変えない）：5.9 の形で終了日・クリティカルパス・総工数・要素の差を返す。"""
    world.session.expire_all()
    for activity_id, hours in ((world.a, "40"), (world.b, "24")):
        row = world.session.get(Activity, activity_id)
        assert row is not None
        row.estimate_hours = Decimal(hours)
    world.session.commit()
    created = world.create(operations=[
        *world.add_c_after_b(),
        {"op": "add_element", "parent_code": world.d_code, "kind": "WORK_PACKAGE", "name": "WP3", "ref": "wp3"},
        {"op": "update_element", "code": world.wp2_code, "fields": {"name": "WP2 改"}},
    ])
    activities_before = world.count(Activity)
    response = world.client.post(world.url(f"/change-requests/{created['id']}/impact"), headers=world.h["viewer"])
    assert response.status_code == 200, response.text
    impact = response.json()
    assert impact["change_request_id"] == created["id"]
    # A：12/1〜12/7（5 稼働日）→ B：12/8〜12/10 → C：12/11〜12/14（金・月）。終了は 2 稼働日の遅れ
    assert impact["project_finish"] == {"before": "2026-12-10", "after": "2026-12-14", "shift_days": 2}
    assert impact["critical_path"]["added"] == [{"activity_id": None, "ref": "c", "name": "C"}]
    assert impact["critical_path"]["removed"] == []
    assert impact["total_estimate_hours"] == {"before": "64.00", "after": "80.00"}
    assert [e["name"] for e in impact["elements"]["added"]] == ["WP3"]
    assert impact["elements"]["updated"][0]["changes"]["name"] == {"before": "WP2", "after": "WP2 改"}
    assert impact["milestones"] == []
    # 本計画にも変更要求にも書かない
    assert world.count(Activity) == activities_before
    world.session.expire_all()
    row = world.session.get(ChangeRequest, created["id"])
    assert row is not None and row.impact is None and row.status == "DRAFT"
    assert world.client.post(world.url(f"/change-requests/{created['id']}/impact"),
                             headers=world.h["outsider"]).status_code == 403
    assert world.client.post(world.url(f"/change-requests/{MISSING}/impact"),
                             headers=world.h["viewer"]).status_code == 404


def test_impact_analysis_hides_change_requests_outside_contractor_scope(world: World) -> None:
    """API-CHG-004 影響分析を流す：委託先には、一覧に出ない変更要求（自分の WBS の経路の外が対象）を 404 で返す（9.2）。"""
    inside = world.create()  # 対象は WP1（活動 A の WP）
    outside = world.create(target_codes=[world.wp2_code], operations=[])
    assign = world.client.put(world.url(f"/activities/{world.a}/assignments"),
                              json={"assignments": [{"user_id": world.uid("contractor"), "planned_hours": 8}]},
                              headers=world.h["planner"])
    assert assign.status_code == 200, assign.text
    hidden = world.client.post(world.url(f"/change-requests/{outside['id']}/impact"), headers=world.h["contractor"])
    assert hidden.status_code == 404, hidden.text
    assert hidden.json()["error"]["code"] == "NOT_FOUND"
    seen = world.client.post(world.url(f"/change-requests/{inside['id']}/impact"), headers=world.h["contractor"])
    assert seen.status_code == 200, seen.text
    # 全て読めるロールには、同じ変更要求の影響分析を返す
    assert world.client.post(world.url(f"/change-requests/{outside['id']}/impact"),
                             headers=world.h["viewer"]).status_code == 200


# ---------------------------------------------------------------------------
# API-CHG-005 提出・承認（API-WBS-019 と RULE-CHG-003）
# ---------------------------------------------------------------------------


def test_submit_saves_impact_and_creates_approval_request(world: World) -> None:
    """API-CHG-005 提出する（影響分析・承認申請）：SUBMITTED、影響分析を保存、承認申請（PENDING）を作る。"""
    created = world.create()
    world.submit(created["id"], who="executor", expect=403)
    submitted = world.submit(created["id"])
    assert submitted["status"] == "SUBMITTED"
    assert submitted["submitted_at"].startswith("2026-12-10T09:00:00")
    assert submitted["impact"]["project_finish"]["shift_days"] == 2
    world.session.expire_all()
    approval = world.session.get(ApprovalRequest, submitted["approval_request_id"])
    assert approval is not None
    assert (approval.target_type, approval.target_id, approval.status, approval.requested_by) == (
        "CHANGE_REQUEST", created["id"], "PENDING", world.uid("planner"))
    again = world.client.post(world.url(f"/change-requests/{created['id']}/submit"), headers=world.h["planner"])
    assert again.status_code == 409 and error_code(again) == "INVALID_STATE"


def test_approval_decision_moves_change_request_and_forbids_self_approval(world: World) -> None:
    """API-CHG-005 提出する（影響分析・承認申請）：判断（API-WBS-019）が RULE-CHG-003 で変更要求の状態を動かす。

    提出した人は承認の権限を持っていても判断できない（403 SELF_APPROVAL_FORBIDDEN）。承認・却下は change.approve。
    """
    assert isinstance(world.client.app.state.change_request_approvals, ChangeRequestStateMachine)  # type: ignore[attr-defined]
    own = world.create(who="pm")
    approval_id = world.submit(own["id"], who="pm")["approval_request_id"]
    self_decision = world.decide(approval_id, "APPROVE", who="pm", expect=403)
    assert self_decision["error"]["code"] == "SELF_APPROVAL_FORBIDDEN"
    world.decide(approval_id, "APPROVE", who="planner", expect=403)  # change.approve が無い
    world.decide(approval_id, "APPROVE")
    listed = world.client.get(world.url("/change-requests"), headers=world.h["viewer"]).json()["items"]
    assert listed[0]["status"] == "APPROVED"
    rejected = world.create()
    reject_id = world.submit(rejected["id"])["approval_request_id"]
    world.decide(reject_id, "REJECT", who="pm")
    statuses = {i["id"]: i["status"] for i in
                world.client.get(world.url("/change-requests"), headers=world.h["viewer"]).json()["items"]}
    assert statuses == {own["id"]: "APPROVED", rejected["id"]: "REJECTED"}
    closed = world.client.post(world.url(f"/change-requests/{rejected['id']}/impact"), headers=world.h["viewer"])
    assert closed.status_code == 409


# ---------------------------------------------------------------------------
# API-CHG-006 反映と再基準化
# ---------------------------------------------------------------------------


def test_apply_writes_plan_recalculates_and_rebaselines(world: World) -> None:
    """API-CHG-006 承認済みの変更を反映して再基準化する：計画へ当て、日程を計算し直し、新しい版を作る（旧版は残す。BR-12）。"""
    world.baseline_wbs()
    manual = world.client.post(world.url("/schedule-baselines"), json={"name": "初版"}, headers=world.h["pm"])
    assert manual.status_code == 201, manual.text
    draft = world.create()
    not_yet = world.apply(draft["id"], expect=409)
    assert not_yet["error"]["code"] == "INVALID_STATE"
    world.submit(draft["id"])
    world.apply(draft["id"], expect=409)  # SUBMITTED も反映できない
    approval_id = world.client.get(world.url("/change-requests"), headers=world.h["pm"]).json()["items"][0][
        "approval_request_id"]
    world.decide(approval_id, "APPROVE")
    world.apply(draft["id"], who="planner", expect=403)  # change.apply は PM だけ

    applied = world.apply(draft["id"])
    assert applied["change_request"]["status"] == "APPLIED"
    assert applied["change_request"]["applied_by"] == world.uid("pm")
    assert applied["schedule_baseline"]["version"] == 2 and applied["schedule_baseline"]["reason"] == "CHANGE_REQUEST"
    assert applied["scope_baseline"]["version"] == 2 and applied["scope_baseline"]["reason"] == "CHANGE_REQUEST"

    world.session.expire_all()
    c = world.session.scalar(select(Activity).where(Activity.project_id == world.pid, Activity.name == "C"))
    assert c is not None
    assert (c.work_package_id, c.duration_days, c.estimate_hours) == (world.wp1, 2, Decimal("16.00"))
    assert (str(c.start_date), str(c.finish_date), c.is_critical) == ("2026-12-11", "2026-12-14", True)
    activities = world.client.get(world.url("/activities"), headers=world.h["viewer"]).json()["items"]
    c_row = next(a for a in activities if a["name"] == "C")
    assert world.b in c_row["predecessor_ids"]
    versions = world.session.scalars(select(ScheduleBaseline.version).where(ScheduleBaseline.project_id == world.pid)
                                     .order_by(ScheduleBaseline.version)).all()
    assert list(versions) == [1, 2]  # 手動の版は残る
    new_schedule = world.session.get(ScheduleBaseline, applied["schedule_baseline"]["id"])
    assert new_schedule is not None and new_schedule.change_request_id == draft["id"]
    copied = world.session.scalars(select(ScheduleBaselineItem.name).where(
        ScheduleBaselineItem.schedule_baseline_id == new_schedule.id, ScheduleBaselineItem.item_type == "ACTIVITY"))
    assert sorted(copied) == ["A", "B", "C"]
    scope = world.session.get(ScopeBaseline, applied["scope_baseline"]["id"])
    assert scope is not None and scope.change_request_id == draft["id"]
    scope_count = world.session.scalar(select(func.count()).select_from(ScopeBaselineElement)
                                       .where(ScopeBaselineElement.scope_baseline_id == scope.id))
    assert scope_count == 3  # D・WP1・WP2（変更は活動だけを足す）
    project = world.client.get(world.url(""), headers=world.h["viewer"]).json()
    assert project["wbs_status"] == "BASELINED"
    again = world.apply(draft["id"], expect=409)
    assert again["error"]["code"] == "INVALID_STATE"


def test_apply_adds_first_level_element_without_reusing_numbers(world: World) -> None:
    """API-CHG-006：第 1 階層への追加は数え（top_last_child_seq）+ 1 で採番し、数えを進める（設計書 1.1 の 5.2。CR-003）。"""
    extra, _ = world.element("消す成果物", None, "DELIVERABLE")
    deleted = world.client.delete(world.url(f"/wbs/elements/{extra}"), headers=world.h["planner"])
    assert deleted.status_code == 204, deleted.text
    world.baseline_wbs()
    draft = world.approved(operations=[{"op": "add_element", "parent_code": "", "kind": "DELIVERABLE", "name": "追加の成果物",
                                        "ref": "d2"}])
    world.apply(draft["id"])

    world.session.expire_all()
    added = world.session.scalar(select(WbsElement).where(WbsElement.project_id == world.pid,
                                                          WbsElement.name == "追加の成果物"))
    assert added is not None and (added.wbs_code, added.sibling_seq, added.parent_id) == ("3.0", 3, None)
    rule = world.session.scalar(select(WbsNumberingRule).where(WbsNumberingRule.project_id == world.pid))
    assert rule is not None and rule.top_last_child_seq == 3


def test_apply_requires_baselined_wbs(world: World) -> None:
    """API-CHG-006：WBS が BASELINED（承認済みの版がある）でなければ 409 INVALID_STATE、何も書かない。

    5.10 で BASELINED へ入るのは承認だけ。承認を経ていない WBS（DRAFT・IN_REVIEW）や基準の後に直接変えた WBS（DRAFT）を、
    反映で BASELINED にしない（レビューの指摘。A-47）。
    """
    approved = world.approved()
    activities_before = world.count(Activity)
    refused = world.apply(approved["id"], expect=409)
    assert refused["error"]["code"] == "INVALID_STATE"
    assert refused["error"]["details"] == [{"wbs_status": "DRAFT"}]
    world.session.expire_all()
    assert world.count(Activity) == activities_before
    assert world.count(ScopeBaseline) == 0
    assert world.client.get(world.url(""), headers=world.h["viewer"]).json()["wbs_status"] == "DRAFT"

    world.baseline_wbs()
    renamed = world.client.patch(world.url(f"/wbs/elements/{world.wp2}"), json={"name": "直接変えた"},
                                 headers=world.h["planner"])
    assert renamed.status_code == 200, renamed.text
    assert world.client.get(world.url(""), headers=world.h["viewer"]).json()["wbs_status"] == "DRAFT"
    assert world.apply(approved["id"], expect=409)["error"]["details"] == [{"wbs_status": "DRAFT"}]
    assert world.count(ScopeBaseline) == 1


def test_apply_removes_baselined_element_and_keeps_old_versions(world: World) -> None:
    """API-CHG-006 承認済みの変更を反映して再基準化する：版に入った要素も消せ、旧版の写しは変わらない（設計書 1.1 の 4.2。CR-002）。

    新しい版には消した要素が無い。旧版の写しは消した要素の内部キーをそのまま持つ（WBS-22・BR-12）。
    """
    world.baseline_wbs()
    first = world.approved()
    applied_first = world.apply(first["id"])  # 全ての要素がスコープの版に入る
    removing = world.approved(target_codes=[world.wp2_code],
                              operations=[{"op": "remove_element", "code": world.wp2_code}])
    applied = world.apply(removing["id"])
    assert applied["change_request"]["status"] == "APPLIED"

    world.session.expire_all()
    assert world.session.get(WbsElement, world.wp2) is None
    old_copy = world.session.scalars(select(ScopeBaselineElement.element_id).where(
        ScopeBaselineElement.scope_baseline_id == applied_first["scope_baseline"]["id"]))
    assert world.wp2 in set(old_copy)
    new_copy = world.session.scalars(select(ScopeBaselineElement.element_id).where(
        ScopeBaselineElement.scope_baseline_id == applied["scope_baseline"]["id"]))
    assert world.wp2 not in set(new_copy)
    old_items = world.session.scalars(select(ScheduleBaselineItem.element_id).where(
        ScheduleBaselineItem.schedule_baseline_id == applied_first["schedule_baseline"]["id"]))
    assert world.wp2 in set(old_items)


def test_apply_removes_activity_and_updates_values(world: World) -> None:
    """API-CHG-006 承認済みの変更を反映して再基準化する：活動の削除（依存も消える）と活動の値の変更を本計画へ書く。"""
    world.baseline_wbs()
    approved = world.approved(operations=[
        {"op": "remove_activity", "activity_id": world.a},
        {"op": "update_activity", "activity_id": world.b, "fields": {"duration_days": 4, "estimate_hours": 32}},
    ])
    world.apply(approved["id"])
    world.session.expire_all()
    assert world.session.get(Activity, world.a) is None
    b = world.session.get(Activity, world.b)
    assert b is not None
    assert (b.duration_days, b.estimate_hours, str(b.start_date), str(b.finish_date)) == (
        4, Decimal("32.00"), "2026-12-01", "2026-12-04")


# ---------------------------------------------------------------------------
# API-CHG-007〜009 是正措置
# ---------------------------------------------------------------------------


def test_direct_delete_refuses_rows_referenced_by_corrective_actions(world: World) -> None:
    """API-SCH-005・API-WBS-005 の直接の削除も、是正措置が指す活動・要素は 422（変更要求の反映と同じ。500 にしない）。

    スケジュールベースラインの写しが指す要素も消せ、写しは変わらない（設計書 1.1 の 4.2。CR-002）。
    """
    world.action(activity_id=world.b)
    refused = world.client.delete(world.url(f"/activities/{world.b}"), headers=world.h["planner"])
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["code"] == "ACTIVITY_REFERENCED"
    assert world.session.get(Activity, world.b) is not None
    world.action(element_id=world.wp2)
    element_refused = world.client.delete(world.url(f"/wbs/elements/{world.wp2}"), headers=world.h["planner"])
    assert element_refused.status_code == 422, element_refused.text
    assert element_refused.json()["error"]["code"] == "ELEMENT_REFERENCED"

    wp3, _ = world.element("WP3", world.d, "WORK_PACKAGE")
    saved = world.client.post(world.url("/schedule-baselines"), json={"name": "基準"}, headers=world.h["pm"])
    assert saved.status_code == 201, saved.text
    world.session.expire_all()
    item = world.session.scalar(select(ScheduleBaselineItem).where(ScheduleBaselineItem.element_id == wp3))
    assert item is not None
    removed = world.client.delete(world.url(f"/wbs/elements/{wp3}"), headers=world.h["planner"])
    assert removed.status_code == 204, removed.text
    world.session.expire_all()
    kept = world.session.get(ScheduleBaselineItem, item.id)
    assert kept is not None and kept.element_id == wp3


def test_create_corrective_action(world: World) -> None:
    """API-CHG-007 是正措置を登録する：action.manage、要素か活動への紐付け、状態は NOT_STARTED。"""
    created = world.action(activity_id=world.a, owner_user_id=world.uid("executor"), due_date="2026-12-15")
    assert created["status"] == "NOT_STARTED"
    assert created["wbs_code"] == world.wp1_code
    assert created["overdue"] is False and created["effect"] is None
    world.action(who="executor", expect=403, element_id=world.wp1)
    world.action(who="planner", expect=403, element_id=world.wp1)
    unlinked = world.action(expect=400)
    assert unlinked["error"]["code"] == "VALIDATION_ERROR"
    world.action(expect=400, element_id=MISSING)
    world.action(expect=400, element_id=world.wp1, owner_user_id=world.uid("outsider"))
    world.action(expect=400, element_id=world.wp1, cause="")


def test_list_corrective_actions_filters(world: World) -> None:
    """API-CHG-008 是正措置の一覧（状態・期限超過・WBS IDで絞れる）：期限超過は 期限 ＜ 今日 かつ 未完了（RULE-CHG-005）。"""
    late = world.action(activity_id=world.a, due_date="2026-12-09")
    today = world.action(element_id=world.wp2, due_date="2026-12-10")
    world.action(element_id=world.d)
    url = world.url("/corrective-actions")
    every = world.client.get(url, headers=world.h["viewer"]).json()
    assert every["total"] == 3
    overdue = world.client.get(url, params={"overdue": "true"}, headers=world.h["viewer"]).json()
    assert [i["id"] for i in overdue["items"]] == [late["id"]]
    not_overdue = world.client.get(url, params={"overdue": "false"}, headers=world.h["viewer"]).json()
    assert today["id"] in [i["id"] for i in not_overdue["items"]] and not_overdue["total"] == 2
    by_wp1 = world.client.get(url, params={"wbs_code": world.wp1_code}, headers=world.h["viewer"]).json()
    assert [i["id"] for i in by_wp1["items"]] == [late["id"]]
    by_d = world.client.get(url, params={"wbs_code": world.d_code}, headers=world.h["viewer"]).json()
    assert by_d["total"] == 3
    unknown = world.client.get(url, params={"wbs_code": "9.9"}, headers=world.h["viewer"]).json()
    assert unknown["total"] == 0
    patch = world.client.patch(world.url(f"/corrective-actions/{late['id']}"), json={"status": "IN_PROGRESS"},
                               headers=world.h["progress"])
    assert patch.status_code == 200
    started = world.client.get(url, params={"status": "IN_PROGRESS"}, headers=world.h["viewer"]).json()
    assert [i["id"] for i in started["items"]] == [late["id"]] and started["items"][0]["overdue"] is True
    assert world.client.get(url, headers=world.h["outsider"]).status_code == 403
    assert world.client.get(url, params={"status": "OPEN"}, headers=world.h["viewer"]).status_code == 400


def test_update_corrective_action_follows_transitions(world: World) -> None:
    """API-CHG-009 是正措置の状態・効果を更新する：NOT_STARTED → IN_PROGRESS → DONE、DONE → IN_PROGRESS だけ。"""
    created = world.action(element_id=world.wp1, due_date="2026-12-01")
    url = world.url(f"/corrective-actions/{created['id']}")
    skip = world.client.patch(url, json={"status": "DONE"}, headers=world.h["progress"])
    assert skip.status_code == 409 and error_code(skip) == "INVALID_STATE"
    assert world.client.patch(url, json={"status": "IN_PROGRESS"}, headers=world.h["progress"]).status_code == 200
    done = world.client.patch(url, json={"status": "DONE", "effect": "遅れを 2 日回復"}, headers=world.h["progress"])
    assert done.status_code == 200
    assert (done.json()["status"], done.json()["effect"], done.json()["overdue"]) == ("DONE", "遅れを 2 日回復", False)
    back = world.client.patch(url, json={"status": "NOT_STARTED"}, headers=world.h["progress"])
    assert back.status_code == 409
    reopened = world.client.patch(url, json={"status": "IN_PROGRESS"}, headers=world.h["pm"])
    assert reopened.status_code == 200 and reopened.json()["overdue"] is True
    assert world.client.patch(url, json={"effect": "x"}, headers=world.h["executor"]).status_code == 403
    assert world.client.patch(world.url(f"/corrective-actions/{MISSING}"), json={"effect": "x"},
                              headers=world.h["pm"]).status_code == 404
    assert world.client.patch(url, json={"status": "FINISHED"}, headers=world.h["pm"]).status_code == 400


# ---------------------------------------------------------------------------
# API-CHG-010 変更履歴の検索
# ---------------------------------------------------------------------------


def test_audit_logs_record_writes_and_search(world: World) -> None:
    """API-CHG-010 変更履歴の検索（期間・変更者・対象・WBS ID）：書き込みが変更者・前後の値つきで残り、条件で絞れる。"""
    created = world.create()
    url = world.url("/audit-logs")
    by_type = world.client.get(url, params={"entity_type": "change_requests"}, headers=world.h["pm"])
    assert by_type.status_code == 200, by_type.text
    rows = by_type.json()["items"]
    assert len(rows) == 1
    assert (rows[0]["action"], rows[0]["entity_id"], rows[0]["user_id"]) == ("CREATE", created["id"],
                                                                             world.uid("planner"))
    assert rows[0]["before"] is None and rows[0]["after"]["title"] == "C を足す"
    assert rows[0]["occurred_at"].startswith("2026-12-10T09:00:00")
    world.client.patch(world.url(f"/change-requests/{created['id']}"), json={"title": "直した"},
                       headers=world.h["pm"])
    updated = world.client.get(url, params={"entity_type": "change_requests", "user_id": world.uid("pm")},
                               headers=world.h["approver"]).json()["items"]
    assert len(updated) == 1
    assert (updated[0]["action"], updated[0]["before"], updated[0]["after"]) == (
        "UPDATE", {"title": "C を足す"}, {"title": "直した"})
    by_code = world.client.get(url, params={"wbs_code": world.wp1_code}, headers=world.h["pm"]).json()
    assert {r["entity_type"] for r in by_code["items"]} >= {"wbs_elements", "activities"}
    assert all(r["wbs_code"] == world.wp1_code for r in by_code["items"])
    by_entity = world.client.get(url, params={"entity_type": "activities", "entity_id": world.a},
                                 headers=world.h["pm"]).json()
    assert by_entity["total"] >= 1 and all(r["entity_id"] == world.a for r in by_entity["items"])
    in_range = world.client.get(url, params={"date_from": "2026-12-10", "date_to": "2026-12-10"},
                                headers=world.h["pm"]).json()
    assert in_range["total"] >= 3
    before_range = world.client.get(url, params={"date_to": "2026-12-09"}, headers=world.h["pm"]).json()
    assert before_range["total"] == 0
    reversed_range = world.client.get(url, params={"date_from": "2026-12-11", "date_to": "2026-12-10"},
                                      headers=world.h["pm"])
    assert reversed_range.status_code == 400
    assert world.client.get(url, headers=world.h["executor"]).status_code == 403
    assert world.client.get(url, headers=world.h["planner"]).status_code == 403
    # 追記だけ：履歴の行数は書き込みのたびに増えるだけで、検索は何も書かない
    count = world.count(AuditLog)
    world.client.get(url, headers=world.h["pm"])
    assert world.count(AuditLog) == count
