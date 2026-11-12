"""変更管理の API（設計書 6.2 の API-CHG-001〜010）：変更要求・影響分析・提出・反映（再基準化）・是正措置・変更履歴の検索。

承認・却下は承認申請への判断（API-WBS-019。WBS と共通）で行い、変更要求の状態は ``app.services.change.ChangeRequestStateMachine``
が RULE-CHG-003 で動かす。
"""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_project_access
from app.schemas.change import (
    AppliedOut,
    AuditLogList,
    AuditLogOut,
    BaselineVersionOut,
    ChangeRequestCreate,
    ChangeRequestList,
    ChangeRequestOut,
    ChangeRequestPatch,
    ChangeRequestStatusCode,
    CorrectiveActionCreate,
    CorrectiveActionList,
    CorrectiveActionOut,
    CorrectiveActionPatch,
    CorrectiveActionStatusCode,
    ImpactOut,
)
from app.services import change as change_service
from app.services import get_session
from app.services.auth import User
from app.services.change import ChangeRequestView, CorrectiveActionView
from app.services.clock import get_now
from app.services.project_access import ProjectAccess

router = APIRouter(tags=["change"])


def get_today(now: datetime = Depends(get_now)) -> date:
    """是正措置の期限超過（RULE-CHG-005）の「今日」。``now``（UTC）の日付（docs/open-questions.md OQ-33 (1)）。"""
    return now.date()


def _change_request_out(view: ChangeRequestView) -> ChangeRequestOut:
    c = view.change_request
    return ChangeRequestOut(
        id=c.id, project_id=c.project_id, number=c.number, title=c.title, reason=c.reason,
        target_codes=list(c.target_codes or []), operations=list(c.operations or []), status=c.status,  # type: ignore[arg-type]
        impact=c.impact, created_by=c.created_by, submitted_at=c.submitted_at, applied_by=c.applied_by,
        applied_at=c.applied_at, approval_request_id=view.approval_request_id,
    )


def _action_out(view: CorrectiveActionView) -> CorrectiveActionOut:
    a = view.action
    return CorrectiveActionOut(
        id=a.id, project_id=a.project_id, element_id=a.element_id, activity_id=a.activity_id,
        status_report_id=a.status_report_id, wbs_code=view.wbs_code, cause=a.cause, action=a.action,
        owner_user_id=a.owner_user_id, due_date=a.due_date, status=a.status, effect=a.effect,  # type: ignore[arg-type]
        overdue=view.overdue,
    )


@router.post("/projects/{project_id}/change-requests", response_model=ChangeRequestOut, status_code=201)
def create_change_request(
    project_id: int,
    body: ChangeRequestCreate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
) -> ChangeRequestOut:
    """API-CHG-001 変更要求を作る。

    権限は change.create。番号はプロジェクトの中の連番、状態は DRAFT。対象の WBS ID（旧 ID も可）が解決できなければ 422
    ``REFERENCE_NOT_FOUND``。変更内容（5.9）は今の計画へ当てて検査する（形の誤りは 400、参照・業務ルールの違反は 422。本計画は変えない）。
    """
    view = change_service.create_change_request(session, access, user, project_id, title=body.title,
                                                reason=body.reason, target_codes=body.target_codes,
                                                operations=body.operations)
    return _change_request_out(view)


@router.get("/projects/{project_id}/change-requests", response_model=ChangeRequestList)
def list_change_requests(
    project_id: int,
    status: ChangeRequestStatusCode | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
) -> ChangeRequestList:
    """API-CHG-002 変更要求の一覧（状態で絞れる）。

    権限は project.view。番号の順。委託先は自分の割当の活動の WBS の経路に入る変更要求だけ。
    """
    views, total = change_service.list_change_requests(session, access, user, project_id, status=status, limit=limit,
                                                       offset=offset)
    return ChangeRequestList(items=[_change_request_out(v) for v in views], total=total, limit=limit, offset=offset)


@router.patch("/projects/{project_id}/change-requests/{change_request_id}", response_model=ChangeRequestOut)
def update_change_request(
    project_id: int,
    change_request_id: int,
    body: ChangeRequestPatch,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
) -> ChangeRequestOut:
    """API-CHG-003 変更要求を直す（DRAFT のときだけ）。

    権限は change.create。DRAFT でなければ 409 ``INVALID_STATE``。載せた項目だけを変え、検査は起票と同じ。
    """
    changes = body.model_dump(exclude_unset=True)
    for name in ("title", "reason", "target_codes", "operations"):
        if name in changes and changes[name] is None:
            changes.pop(name)
    view = change_service.update_change_request(session, access, user, project_id, change_request_id, changes)
    return _change_request_out(view)


@router.post("/projects/{project_id}/change-requests/{change_request_id}/impact", response_model=ImpactOut)
def analyze_change_request(
    project_id: int,
    change_request_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
) -> ImpactOut:
    """API-CHG-004 影響分析を流す（本計画は変えない）。

    権限は project.view。今の計画の写しに変更内容を当てて再計算し、5.9 の形で前後の差を返す（RULE-CHG-002）。
    本計画にも変更要求にも書かない。反映済み・却下の変更要求は 409 ``INVALID_STATE``。
    """
    impact = change_service.analyze_change_request(session, access, user, project_id, change_request_id)
    return ImpactOut(change_request_id=change_request_id, **impact)


@router.post("/projects/{project_id}/change-requests/{change_request_id}/submit", response_model=ChangeRequestOut)
def submit_change_request(
    project_id: int,
    change_request_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
    now: datetime = Depends(get_now),
) -> ChangeRequestOut:
    """API-CHG-005 提出する（影響分析・承認申請）。

    権限は change.create。DRAFT → SUBMITTED（RULE-CHG-003。それ以外は 409）。影響分析を保存し、承認申請を作る
    （応答の ``approval_request_id``。判断は API-WBS-019。提出した人は判断できない）。
    """
    view = change_service.submit_change_request(session, access, user, project_id, change_request_id, now=now)
    return _change_request_out(view)


@router.post("/projects/{project_id}/change-requests/{change_request_id}/apply", response_model=AppliedOut)
def apply_change_request(
    project_id: int,
    change_request_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
    now: datetime = Depends(get_now),
) -> AppliedOut:
    """API-CHG-006 承認済みの変更を反映して再基準化する。

    権限は change.apply。APPROVED → APPLIED（それ以外は 409）。変更内容を本計画へ当て（RULE-CHG-001）、日程を計算し直し、
    スコープとスケジュールの新しい版（理由 CHANGE_REQUEST）を作る（RULE-CHG-004。旧版は残す）。
    """
    view = change_service.apply_change_request(session, access, user, project_id, change_request_id, now=now)
    return AppliedOut(
        change_request=_change_request_out(ChangeRequestView(change_request=view.change_request,
                                                             approval_request_id=view.approval_request_id)),
        scope_baseline=BaselineVersionOut(id=view.scope_baseline.id, version=view.scope_baseline.version,
                                          reason=view.scope_baseline.reason),
        schedule_baseline=BaselineVersionOut(id=view.schedule_baseline.id, version=view.schedule_baseline.version,
                                             reason=view.schedule_baseline.reason),
    )


@router.post("/projects/{project_id}/corrective-actions", response_model=CorrectiveActionOut, status_code=201)
def create_corrective_action(
    project_id: int,
    body: CorrectiveActionCreate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
    today: date = Depends(get_today),
) -> CorrectiveActionOut:
    """API-CHG-007 是正措置を登録する。

    権限は action.manage。要素か活動の少なくとも一方に紐付ける（無ければ 400）。紐付けと担当はこのプロジェクトのもの。状態は NOT_STARTED。
    """
    view = change_service.create_corrective_action(session, access, user, project_id, body.model_dump(), today=today)
    return _action_out(view)


@router.get("/projects/{project_id}/corrective-actions", response_model=CorrectiveActionList)
def list_corrective_actions(
    project_id: int,
    status: CorrectiveActionStatusCode | None = Query(None),
    overdue: bool | None = Query(None),
    wbs_code: str | None = Query(None, min_length=1, max_length=100),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
    today: date = Depends(get_today),
) -> CorrectiveActionList:
    """API-CHG-008 是正措置の一覧（状態・期限超過・WBS IDで絞れる）。

    権限は project.view。期限超過は RULE-CHG-005。WBS ID（旧 ID も可）はその要素と配下・その WP の活動に紐付いた措置。
    """
    views, total = change_service.list_corrective_actions(session, access, user, project_id, status=status,
                                                          overdue=overdue, wbs_code=wbs_code, today=today,
                                                          limit=limit, offset=offset)
    return CorrectiveActionList(items=[_action_out(v) for v in views], total=total, limit=limit, offset=offset)


@router.patch("/projects/{project_id}/corrective-actions/{action_id}", response_model=CorrectiveActionOut)
def update_corrective_action(
    project_id: int,
    action_id: int,
    body: CorrectiveActionPatch,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
    today: date = Depends(get_today),
) -> CorrectiveActionOut:
    """API-CHG-009 是正措置の状態・効果を更新する。

    権限は action.manage。状態は RULE-CHG-005 の遷移だけ（それ以外は 409 ``INVALID_STATE``）。載せた項目だけを変える。
    """
    changes = body.model_dump(exclude_unset=True)
    if "status" in changes and changes["status"] is None:
        changes.pop("status")
    view = change_service.update_corrective_action(session, access, user, project_id, action_id, changes, today=today)
    return _action_out(view)


@router.get("/projects/{project_id}/audit-logs", response_model=AuditLogList)
def search_audit_logs(
    project_id: int,
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    user_id: int | None = Query(None),
    entity_type: str | None = Query(None, min_length=1, max_length=64),
    entity_id: int | None = Query(None),
    wbs_code: str | None = Query(None, min_length=1, max_length=100),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
) -> AuditLogList:
    """API-CHG-010 変更履歴の検索（期間・変更者・対象・WBS ID）。

    権限は audit.view。期間（``date_from``〜``date_to``。UTC の日付で両端を含む）・変更者（``user_id``）・対象（``entity_type``・
    ``entity_id``）・WBS ID（記録した時点の ID）で絞る。新しい順。履歴は追記だけで、更新・削除の API は無い（4.2・NFR-05）。
    """
    rows, total = change_service.search_audit_logs(session, access, user, project_id, date_from=date_from,
                                                   date_to=date_to, user_id=user_id, entity_type=entity_type,
                                                   entity_id=entity_id, wbs_code=wbs_code, limit=limit, offset=offset)
    items = [
        AuditLogOut(id=r.id, occurred_at=r.occurred_at, user_id=r.user_id, project_id=r.project_id,
                    entity_type=r.entity_type, entity_id=r.entity_id, wbs_code=r.wbs_code,
                    action=r.action, before=r.before, after=r.after)  # type: ignore[arg-type]
        for r in rows
    ]
    return AuditLogList(items=items, total=total, limit=limit, offset=offset)
