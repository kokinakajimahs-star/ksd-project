"""変更管理（CHG）の表を読む口の試験（db:CHG で差し替えた口。docs/decisions/ASSUMPTIONS.md A-31）。

- ``app.services.wbs.change_requests_table``：横断照会（API-WBS-023）が読む変更要求の対象の WBS ID
- ``app.services.wbs.corrective_actions_table``：横断照会が読む是正措置の紐付け（要素・活動）
- ``app.services.effort.CorrectiveActionsTable``：差異の理由（API-EFF-007）に紐付ける是正措置があるか
- ``app.services.wbs.ChangeRequestTransitionNotWired``：変更要求の承認申請への判断（API-WBS-019）。状態遷移の実装が載るまで止める

どれも**別のプロジェクトの行を混ぜない**ことを、プロジェクトを 2 つ置いて確かめる。
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy.orm import Session

from app.domain.wbs_rules import ChangeRequestRef, CorrectiveActionRef
from app.domain.workflow import ApprovalStatus
from app.models.chg import ChangeRequest, CorrectiveAction
from app.models.cmn import User
from app.models.prj import Project
from app.models.sch import Activity
from app.models.wbs import ApprovalRequest, WbsElement
from app.services.effort import CorrectiveActionsTable
from app.services.wbs import ChangeRequestTransitionNotWired, change_requests_table, corrective_actions_table

NOW = datetime(2026, 12, 10, 9, 0, tzinfo=UTC)
MISSING = 999_999


def _user(session: Session, login_id: str) -> User:
    user = User(login_id=login_id, display_name="利用者", password_hash="scrypt$dummy")
    session.add(user)
    session.flush()
    return user


def _project(session: Session, code: str, owner: User) -> tuple[Project, WbsElement, Activity]:
    project = Project(code=code, name="プロジェクト", start_date=date(2026, 12, 1), owner_user_id=owner.id)
    session.add(project)
    session.flush()
    wp = WbsElement(project_id=project.id, kind="WORK_PACKAGE", name="WP", wbs_code="1", sibling_seq=1, sort_order=1)
    session.add(wp)
    session.flush()
    activity = Activity(project_id=project.id, work_package_id=wp.id, name="資料作成", sort_order=1, duration_days=5)
    session.add(activity)
    session.flush()
    return project, wp, activity


def _change_request(session: Session, project: Project, creator: User, number: int, codes: list[str],
                    status: str = "DRAFT") -> ChangeRequest:
    # 状態に合わせて提出・反映の時刻と人を置く（DB の検査 submitted_at・applied_pair。5.10。A-49 の 6）
    times: dict[str, object] = {} if status == "DRAFT" else {"submitted_at": NOW}
    if status == "APPLIED":
        times.update(applied_by=creator.id, applied_at=NOW)
    change_request = ChangeRequest(project_id=project.id, number=number, title=f"変更 {number}", reason="要望",
                                   target_codes=codes, status=status, created_by=creator.id, **times)
    session.add(change_request)
    session.flush()
    return change_request


def test_change_requests_table_reads_target_codes_of_the_project(db_session: Session) -> None:
    """変更要求の対象の WBS ID を、そのプロジェクトの全ての変更要求（状態を問わない）から ID の順に読む。対象の無い変更要求は空の並び。"""
    creator = _user(db_session, "planner")
    project, _wp, _a = _project(db_session, "P-1", creator)
    other, _owp, _oa = _project(db_session, "P-2", creator)
    first = _change_request(db_session, project, creator, 1, ["1.1", "2.0"])
    _change_request(db_session, other, creator, 1, ["1.1"])
    second = _change_request(db_session, project, creator, 2, [], status="APPLIED")
    db_session.commit()

    assert change_requests_table(db_session, project.id) == [
        ChangeRequestRef(id=first.id, target_codes=("1.1", "2.0")),
        ChangeRequestRef(id=second.id, target_codes=()),
    ]
    assert change_requests_table(db_session, MISSING) == []


def test_corrective_actions_table_reads_links_of_the_project(db_session: Session) -> None:
    """是正措置の紐付け（要素・活動）を、そのプロジェクトの全ての是正措置から ID の順に読む。紐付けの無いものは空のまま渡す。"""
    owner = _user(db_session, "pm")
    project, wp, activity = _project(db_session, "P-1", owner)
    other, other_wp, _oa = _project(db_session, "P-2", owner)
    on_element = CorrectiveAction(project_id=project.id, element_id=wp.id, cause="c", action="a")
    foreign = CorrectiveAction(project_id=other.id, element_id=other_wp.id, cause="c", action="a")
    on_activity = CorrectiveAction(project_id=project.id, activity_id=activity.id, cause="c", action="a")
    unlinked = CorrectiveAction(project_id=project.id, cause="c", action="a")
    db_session.add_all([on_element, foreign, on_activity, unlinked])
    db_session.commit()

    assert corrective_actions_table(db_session, project.id) == [
        CorrectiveActionRef(id=on_element.id, element_id=wp.id),
        CorrectiveActionRef(id=on_activity.id, activity_id=activity.id),
        CorrectiveActionRef(id=unlinked.id),
    ]
    assert corrective_actions_table(db_session, MISSING) == []


def test_corrective_actions_table_exists_only_in_the_same_project(db_session: Session) -> None:
    """差異の理由に紐付ける是正措置は、同じプロジェクトにあれば有る。別のプロジェクトのもの・無い ID は無い。"""
    owner = _user(db_session, "pm")
    project, _wp, _a = _project(db_session, "P-1", owner)
    other, _owp, _oa = _project(db_session, "P-2", owner)
    action = CorrectiveAction(project_id=project.id, cause="c", action="a")
    db_session.add(action)
    db_session.commit()

    port = CorrectiveActionsTable()
    assert port.exists(db_session, project.id, action.id) is True
    assert port.exists(db_session, other.id, action.id) is False
    assert port.exists(db_session, project.id, MISSING) is False


def _approval(session: Session, project: Project, requester: User, target_id: int) -> ApprovalRequest:
    approval = ApprovalRequest(project_id=project.id, target_type="CHANGE_REQUEST", target_id=target_id,
                               requested_by=requester.id, requested_at=NOW)
    session.add(approval)
    session.flush()
    return approval


def test_transition_not_wired_stops_and_leaves_change_request_untouched(db_session: Session) -> None:
    """状態遷移（RULE-CHG-003）の実装が無い間は、申請の指す変更要求があっても止め（RuntimeError）、変更要求の状態を変えない。"""
    planner, pm = _user(db_session, "planner"), _user(db_session, "pm")
    project, _wp, _a = _project(db_session, "P-1", pm)
    change_request = _change_request(db_session, project, planner, 1, ["1"], status="SUBMITTED")
    approval = _approval(db_session, project, planner, change_request.id)
    db_session.commit()

    with pytest.raises(RuntimeError, match="RULE-CHG-003"):
        ChangeRequestTransitionNotWired().decide(db_session, approval, ApprovalStatus.APPROVED, pm, NOW)
    db_session.expire_all()
    assert db_session.get(ChangeRequest, change_request.id).status == "SUBMITTED"  # type: ignore[union-attr]


def test_transition_not_wired_reports_missing_change_request(db_session: Session) -> None:
    """申請の指す変更要求が無い（別のプロジェクトのものを含む）ときは、申請と変更要求の食い違いとして止める。"""
    planner, pm = _user(db_session, "planner"), _user(db_session, "pm")
    project, _wp, _a = _project(db_session, "P-1", pm)
    other, _owp, _oa = _project(db_session, "P-2", pm)
    foreign = _change_request(db_session, other, planner, 1, ["1"], status="SUBMITTED")
    db_session.commit()

    for target_id in (MISSING, foreign.id):
        approval = _approval(db_session, project, planner, target_id)
        with pytest.raises(RuntimeError, match="DB-CHG-001.*食い違い"):
            ChangeRequestTransitionNotWired().decide(db_session, approval, ApprovalStatus.REJECTED, pm, NOW)
    db_session.expire_all()
    assert db_session.get(ChangeRequest, foreign.id).status == "SUBMITTED"  # type: ignore[union-attr]
