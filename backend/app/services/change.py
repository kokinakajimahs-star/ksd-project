"""変更管理のサービス（設計書 6.2 の API-CHG-001〜010、5.9・5.10、9 章。要求仕様書 CHG-01〜04・06・09）。

- 変更要求（``change_requests``）：起票・一覧・修正（DRAFT のときだけ）・影響分析（本計画は変えない）・提出（影響分析を保存し、
  承認申請を作る）・反映（RULE-CHG-001 で本計画へ当て、日程を計算し直し、RULE-CHG-004 で新しい版を作る）。
  状態は RULE-CHG-003（``transition_change_request``）だけが動かす。承認・却下は承認申請への判断（API-WBS-019）から
  ``ChangeRequestStateMachine`` が受ける（``app.state.change_request_approvals``）
- 是正措置（``corrective_actions``）：登録・一覧（状態・期限超過・WBS ID）・状態と効果の更新（RULE-CHG-005）
- 変更履歴（``audit_logs``）：検索（期間・変更者・対象・WBS ID）。記録は infra のイベント（``app.infra.audit_listener``）が行う

権限は 9 章（RULE-AUTH-001）を ``authorize_project`` で判定する。委託先だけで読む利用者（9.2 の注記）には、自分の割当の活動と
その WBS の経路に関わる変更要求・是正措置だけを見せる。1 つの操作を 1 つのトランザクションにする（1.3）。
判断の記録は docs/decisions/ASSUMPTIONS.md A-37。

**同時実行**（A-49 の 3）：変更要求の状態を判定して変える操作（修正・提出・取り下げ・判断・反映）は、``change_requests`` の行を
``SELECT … FOR UPDATE``（``app.infra.db.lock_row``）で読み直してから判定する。関わる承認申請の行はその後にロックする
（承認の判断の API-WBS-019 と同じ順）。反映は続けて ``projects``・採番規則の行をロックする。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from app.domain.authz import ViewLevel, project_view_level
from app.domain.baseline import BaselineError, RebaselineActivity, RebaselineElement, plan_rebaseline
from app.domain.change import (
    AppliedChange,
    ChangeError,
    ChangeErrorCode,
    ImpactAnalysis,
    PlanActivity,
    PlanElement,
    PlanModel,
    analyze_impact,
    apply_operations,
    roll_requested_starts,
)
from app.domain.estimate import ElementKind
from app.domain.scheduling import ScheduleCycleError
from app.domain.wbs_code import CodedElement, CodeHistoryEntry, NumberingRule, WbsCodeError, resolve_code
from app.domain.workflow import (
    ApprovalStatus,
    ChangeRequestAction,
    WbsStatus,
    WorkflowError,
    WorkflowErrorCode,
    ensure_change_request_editable,
    is_corrective_action_overdue,
    transition_change_request,
    transition_corrective_action,
)
from app.errors import AppError
from app.infra.db import lock_row, lock_rows
from app.models.chg import AuditLog, ChangeRequest, CorrectiveAction
from app.models.eff import Assignment, TimeEntry, VarianceNote
from app.models.prg import ProgressEntry, ProgressItem, StatusReport
from app.models.prj import Project
from app.models.sch import Activity, ActivityDependency, ScheduleBaseline, ScheduleBaselineItem
from app.models.wbs import (
    ApprovalRequest,
    ScopeBaseline,
    ScopeBaselineElement,
    WbsCodeHistory,
    WbsDictionaryEntry,
    WbsElement,
    WbsNumberingRule,
    WbsReviewComment,
    WbsScopeLink,
)
from app.services.auth import User, authorize_project, principal_of
from app.services.project_access import ProjectAccess
from app.services.schedule import calculate_and_store, load_plan, work_calendar
from app.services.wbs import DICTIONARY_FIELDS, TARGET_CHANGE_REQUEST, numbering_rule_row

# 4.1 の schedule_baselines.name の列の長さ（A-19）
_BASELINE_NAME_LIMIT = 200

_WORKFLOW_STATUS = {
    WorkflowErrorCode.INVALID_STATE: 409,
    WorkflowErrorCode.WBS_NOT_READY: 422,
    WorkflowErrorCode.SELF_APPROVAL_FORBIDDEN: 403,  # 9.2 の注記：権限を持っていても自分の提出は判断できない
}


# ---------------------------------------------------------------------------
# 共通の部品
# ---------------------------------------------------------------------------


def _workflow_error(exc: WorkflowError, details: list[dict[str, Any]]) -> AppError:
    return AppError(_WORKFLOW_STATUS[exc.code], exc.code.value, str(exc), details=details)


def _change_error(exc: ChangeError) -> AppError:
    """RULE-CHG-001 の拒否を 6.1 のエラーにする。操作の形の誤りは 400、参照・業務ルールの違反は 422（コードはそのまま）。"""
    detail: dict[str, Any] = {"index": exc.index, "message": str(exc)}
    values = [d if isinstance(d, int | str) else str(d) for d in exc.details]
    if values:
        detail["values"] = values
    status = 400 if exc.code is ChangeErrorCode.VALIDATION_ERROR else 422
    return AppError(status, exc.code.value, "変更内容を計画へ当てられません", details=[detail])


def _cycle_error(exc: ScheduleCycleError) -> AppError:
    return AppError(422, "SCHEDULE_CYCLE", "依存が循環しています", details=[{"activity_ids": list(exc.activity_ids)}])


def _change_request_of(
    session: Session, project_id: int, change_request_id: int, *, for_update: bool = False
) -> ChangeRequest:
    """変更要求（無い・他のプロジェクトのものは 404）。状態を変える操作は ``for_update``（行をロックして読み直す。A-49 の 3）。"""
    change_request = (lock_row(session, ChangeRequest, change_request_id) if for_update
                      else session.get(ChangeRequest, change_request_id))
    if change_request is None or change_request.project_id != project_id:
        raise AppError(404, "NOT_FOUND", "変更要求が見つかりません", details=[{"change_request_id": change_request_id}])
    return change_request


def _elements(session: Session, project_id: int) -> list[WbsElement]:
    return list(session.scalars(select(WbsElement).where(WbsElement.project_id == project_id).order_by(WbsElement.id)))


def _code_history(session: Session, project_id: int) -> list[CodeHistoryEntry]:
    rows = session.scalars(select(WbsCodeHistory).where(WbsCodeHistory.project_id == project_id))
    return [CodeHistoryEntry(id=r.id, element_id=r.element_id, old_code=r.old_code, new_code=r.new_code,
                             renumbered_at=r.renumbered_at) for r in rows]


def _coded(elements: Iterable[WbsElement]) -> list[CodedElement]:
    return [CodedElement(id=e.id, parent_id=e.parent_id, wbs_code=e.wbs_code, sibling_seq=e.sibling_seq,
                         last_child_seq=e.last_child_seq, sort_order=e.sort_order, needs_renumber=e.needs_renumber)
            for e in elements]


def _resolve(code: str, coded: Sequence[CodedElement], history: Sequence[CodeHistoryEntry]) -> int | None:
    """WBS ID（旧 ID でもよい。RULE-WBS-002）を今の要素の ID へ。無ければ ``None``。"""
    try:
        resolution = resolve_code(code, coded, history)
    except WbsCodeError as exc:
        raise AppError(422, "WBS_CODE_INVALID", str(exc)) from exc
    return None if resolution is None else resolution.element_id


def _subtree(element_id: int, children: Mapping[int | None, list[int]]) -> set[int]:
    found: set[int] = set()
    stack = [element_id]
    while stack:
        current = stack.pop()
        if current in found:
            continue
        found.add(current)
        stack.extend(children.get(current, ()))
    return found


def _children(elements: Iterable[WbsElement]) -> dict[int | None, list[int]]:
    children: dict[int | None, list[int]] = {}
    for element in elements:
        children.setdefault(element.parent_id, []).append(element.id)
    return children


@dataclass(frozen=True)
class _Visible:
    """委託先だけで読む利用者に見せる範囲（自分の割当の活動と、その WP から最上位までの経路の要素。9.2 の注記）。"""

    activity_ids: frozenset[int]
    element_ids: frozenset[int]


def _visible(
    session: Session, access: ProjectAccess, actor: User, project_id: int, table: Mapping[str, frozenset[str]]
) -> _Visible | None:
    """project.view で全て読めるなら ``None``。委託先だけで読むなら見せる範囲（RULE-AUTH-001）。"""
    membership = access.membership(session, project_id, actor.id)
    if project_view_level(principal_of(actor), role_permissions=table, membership=membership) is ViewLevel.ALL:
        return None
    assigned = membership.assigned_activity_ids if membership is not None else frozenset()
    activities = list(session.scalars(select(Activity).where(Activity.project_id == project_id,
                                                             Activity.id.in_(assigned)))) if assigned else []
    parents = {e.id: e.parent_id for e in _elements(session, project_id)}
    path: set[int] = set()
    for activity in activities:
        current: int | None = activity.work_package_id
        while current is not None and current not in path:
            path.add(current)
            current = parents.get(current)
    return _Visible(activity_ids=frozenset(a.id for a in activities), element_ids=frozenset(path))


# ---------------------------------------------------------------------------
# 計画のモデル（RULE-CHG-001・002 へ渡す純粋なデータ）
# ---------------------------------------------------------------------------


def _plan_model(session: Session, project_id: int) -> PlanModel:
    """今の計画（要素・活動・依存・採番規則・新旧対応）を ``PlanModel`` にする（DB の写しではない純粋なデータ。5.9）。"""
    elements = _elements(session, project_id)
    activities = session.scalars(select(Activity).where(Activity.project_id == project_id).order_by(Activity.id))
    dependencies = session.scalars(
        select(ActivityDependency).where(ActivityDependency.project_id == project_id).order_by(ActivityDependency.id)
    )
    rule_row = session.scalar(select(WbsNumberingRule).where(WbsNumberingRule.project_id == project_id))
    rule = NumberingRule() if rule_row is None else NumberingRule(
        separator=rule_row.separator, min_digits=rule_row.min_digits, prefix=rule_row.prefix,
        pad_first_level=rule_row.pad_first_level,
    )
    return PlanModel(
        elements=tuple(
            PlanElement(id=e.id, parent_id=e.parent_id, kind=ElementKind(e.kind), name=e.name, wbs_code=e.wbs_code,
                        sibling_seq=e.sibling_seq, last_child_seq=e.last_child_seq, sort_order=e.sort_order,
                        own_estimate_hours=e.own_estimate_hours, responsible_user_id=e.responsible_user_id,
                        needs_renumber=e.needs_renumber)
            for e in elements
        ),
        activities=tuple(
            PlanActivity(id=a.id, work_package_id=a.work_package_id, name=a.name, duration_days=a.duration_days,
                         is_milestone=a.is_milestone, estimate_hours=a.estimate_hours,
                         requested_start=a.requested_start, sort_order=a.sort_order)
            for a in activities
        ),
        dependencies=tuple((d.predecessor_id, d.successor_id) for d in dependencies),
        numbering_rule=rule,
        # 第 1 階層で今まで使った最大の順番（設計書 1.1 の 4.1・5.2。CR-003）。行が無ければ今ある第 1 階層の最大から
        top_last_child_seq=max((e.sibling_seq for e in elements if e.parent_id is None), default=0)
        if rule_row is None
        else rule_row.top_last_child_seq,
        code_history=tuple(_code_history(session, project_id)),
    )


def _apply(plan: PlanModel, operations: Sequence[Mapping[str, Any]]) -> AppliedChange:
    try:
        return apply_operations(plan, operations)
    except ChangeError as exc:
        raise _change_error(exc) from exc


def _roll_starts(session: Session, project_id: int, operations: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """変更内容の希望開始日を、起票・更新の時点のカレンダーで次の稼働日へ寄せる（BR-04。API-SCH-002・003 が入力を寄せて
    保存するのと同じ。承認する内容と反映する内容を一致させる。OQ-42 (16)）。"""
    try:
        return roll_requested_starts(operations, work_calendar(session, project_id))
    except ChangeError as exc:
        raise _change_error(exc) from exc


def _analyze(session: Session, project: Project, operations: Sequence[Mapping[str, Any]]) -> ImpactAnalysis:
    try:
        return analyze_impact(_plan_model(session, project.id), operations,
                              calendar=work_calendar(session, project.id), project_start=project.start_date)
    except ChangeError as exc:
        raise _change_error(exc) from exc
    except ScheduleCycleError as exc:
        raise _cycle_error(exc) from exc


def _check_targets(session: Session, project_id: int, target_codes: Sequence[str]) -> list[str]:
    """対象の WBS ID（複数可。CHG-01）は今の ID か旧 ID で要素に解決できること。できなければ 422 ``REFERENCE_NOT_FOUND``。"""
    coded = _coded(_elements(session, project_id))
    history = _code_history(session, project_id)
    missing = [code for code in target_codes if _resolve(code, coded, history) is None]
    if missing:
        raise AppError(422, "REFERENCE_NOT_FOUND", "対象の WBS ID が見つかりません", details=[{"wbs_codes": missing}])
    return list(target_codes)


# ---------------------------------------------------------------------------
# 変更要求（API-CHG-001〜006）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChangeRequestView:
    change_request: ChangeRequest
    approval_request_id: int | None


def _latest_approval_id(session: Session, change_request: ChangeRequest) -> int | None:
    return session.scalar(
        select(ApprovalRequest.id)
        .where(ApprovalRequest.project_id == change_request.project_id,
               ApprovalRequest.target_type == TARGET_CHANGE_REQUEST,
               ApprovalRequest.target_id == change_request.id)
        .order_by(ApprovalRequest.id.desc())
        .limit(1)
    )


def _view(session: Session, change_request: ChangeRequest) -> ChangeRequestView:
    return ChangeRequestView(change_request=change_request,
                             approval_request_id=_latest_approval_id(session, change_request))


def create_change_request(
    session: Session,
    access: ProjectAccess,
    actor: User,
    project_id: int,
    *,
    title: str,
    reason: str,
    target_codes: Sequence[str],
    operations: Sequence[Mapping[str, Any]],
) -> ChangeRequestView:
    """変更要求を作る（change.create。CHG-01）。番号はプロジェクトの中の連番、状態は DRAFT。

    対象の WBS ID は今の ID か旧 ID で解決できること（422 ``REFERENCE_NOT_FOUND``）。変更内容は今の計画へ当てて検査する
    （RULE-CHG-001。形の誤りは 400、参照・業務ルールの違反は 422。本計画には書かない）。希望開始日は次の稼働日へ寄せて
    保存する（BR-04。API-SCH-002・003 と同じ）。
    """
    authorize_project(session, access, actor, project_id, "change.create")
    codes = _check_targets(session, project_id, target_codes)
    ops = [dict(op) for op in operations]
    _apply(_plan_model(session, project_id), ops)
    ops = _roll_starts(session, project_id, ops)
    latest = session.scalar(select(func.max(ChangeRequest.number)).where(ChangeRequest.project_id == project_id))
    change_request = ChangeRequest(project_id=project_id, number=(latest or 0) + 1, title=title, reason=reason,
                                   target_codes=codes, operations=ops, created_by=actor.id)
    session.add(change_request)
    session.commit()
    return ChangeRequestView(change_request=change_request, approval_request_id=None)


def _visible_change_request(change_request: ChangeRequest, visible: _Visible, coded: Sequence[CodedElement],
                            history: Sequence[CodeHistoryEntry]) -> bool:
    for code in change_request.target_codes or ():
        element_id = _resolve(str(code), coded, history)
        if element_id is not None and element_id in visible.element_ids:
            return True
    return False


def list_change_requests(
    session: Session,
    access: ProjectAccess,
    actor: User,
    project_id: int,
    *,
    status: str | None,
    limit: int,
    offset: int,
) -> tuple[list[ChangeRequestView], int]:
    """変更要求の一覧（project.view。状態で絞れる。番号の順。CHG-01）。

    委託先だけで読む利用者には、対象の WBS ID が自分の割当の活動の WBS の経路に入る変更要求だけ（9.2 の注記）。
    """
    table = authorize_project(session, access, actor, project_id, "project.view")
    query = select(ChangeRequest).where(ChangeRequest.project_id == project_id)
    if status is not None:
        query = query.where(ChangeRequest.status == status)
    rows = list(session.scalars(query.order_by(ChangeRequest.number)))
    visible = _visible(session, access, actor, project_id, table)
    if visible is not None:
        coded = _coded(_elements(session, project_id))
        history = _code_history(session, project_id)
        rows = [r for r in rows if _visible_change_request(r, visible, coded, history)]
    page = rows[offset:offset + limit]
    return [_view(session, r) for r in page], len(rows)


def update_change_request(
    session: Session,
    access: ProjectAccess,
    actor: User,
    project_id: int,
    change_request_id: int,
    changes: Mapping[str, Any],
) -> ChangeRequestView:
    """変更要求を直す（change.create。DRAFT のときだけ。それ以外は 409 ``INVALID_STATE``。CHG-01）。

    載せた項目（title・reason・target_codes・operations）だけを変える。検査は起票と同じ。
    """
    authorize_project(session, access, actor, project_id, "change.create")
    change_request = _change_request_of(session, project_id, change_request_id, for_update=True)
    try:
        ensure_change_request_editable(change_request.status)
    except WorkflowError as exc:
        raise _workflow_error(exc, [{"change_request_id": change_request.id, "status": change_request.status}]) from exc
    if "target_codes" in changes:
        change_request.target_codes = _check_targets(session, project_id, changes["target_codes"])
    if "operations" in changes:
        ops = [dict(op) for op in changes["operations"]]
        _apply(_plan_model(session, project_id), ops)
        change_request.operations = _roll_starts(session, project_id, ops)
    for name in ("title", "reason"):
        if name in changes:
            setattr(change_request, name, changes[name])
    session.commit()
    return _view(session, change_request)


_IMPACT_CLOSED = ("APPLIED", "REJECTED")


def analyze_change_request(
    session: Session, access: ProjectAccess, actor: User, project_id: int, change_request_id: int
) -> dict[str, Any]:
    """影響分析を流す（project.view。RULE-CHG-002。CHG-02）。**本計画にも変更要求にも書き込まない。**

    今の計画の写しに保存した変更内容を当て、5.9 の形（終了日・マイルストーン・クリティカルパス・総工数・要素）を返す。
    反映済み（APPLIED）・却下（REJECTED）の変更要求は当てる先が無いので 404 ではなく 409 ``INVALID_STATE``（A-37）。
    委託先だけで読む利用者には、一覧に出ない変更要求（対象が自分の割当の WBS の経路に入らないもの）を見つからないものとして
    扱う（9.2 の注記。一覧と同じ 404。A-47）。
    """
    table = authorize_project(session, access, actor, project_id, "project.view")
    change_request = _change_request_of(session, project_id, change_request_id)
    visible = _visible(session, access, actor, project_id, table)
    if visible is not None and not _visible_change_request(
        change_request, visible, _coded(_elements(session, project_id)), _code_history(session, project_id)
    ):
        raise AppError(404, "NOT_FOUND", "変更要求が見つかりません", details=[{"change_request_id": change_request_id}])
    if change_request.status in _IMPACT_CLOSED:
        raise AppError(409, "INVALID_STATE", f"変更要求が {change_request.status} のときは影響分析を流せません",
                       details=[{"change_request_id": change_request.id, "status": change_request.status}])
    project = session.get(Project, project_id)
    assert project is not None  # authorize_project が先に 404 を返す
    return _analyze(session, project, change_request.operations or []).to_json()


def submit_change_request(
    session: Session, access: ProjectAccess, actor: User, project_id: int, change_request_id: int, *, now: datetime
) -> ChangeRequestView:
    """提出する（change.create。RULE-CHG-003 の SUBMIT：DRAFT → SUBMITTED。CHG-03）。

    影響分析（RULE-CHG-002）を流して ``impact`` に保存し、承認申請（``target_type = CHANGE_REQUEST``）を作る。
    申請者は提出した人（承認・却下は別の人だけ。5.10）。DRAFT でなければ 409、変更内容を当てられなければ 400・422。
    """
    authorize_project(session, access, actor, project_id, "change.create")
    change_request = _change_request_of(session, project_id, change_request_id, for_update=True)
    try:
        transition = transition_change_request(change_request.status, ChangeRequestAction.SUBMIT)
    except WorkflowError as exc:
        raise _workflow_error(exc, [{"change_request_id": change_request.id, "status": change_request.status}]) from exc
    project = session.get(Project, project_id)
    assert project is not None
    impact = _analyze(session, project, change_request.operations or [])
    change_request.impact = impact.to_json()
    change_request.status = transition.to_status.value
    change_request.submitted_at = now
    approval = ApprovalRequest(project_id=project_id, target_type=TARGET_CHANGE_REQUEST, target_id=change_request.id,
                               requested_by=actor.id, requested_at=now)
    session.add(approval)
    session.commit()
    return ChangeRequestView(change_request=change_request, approval_request_id=approval.id)


def withdraw_change_request(
    session: Session, access: ProjectAccess, actor: User, project_id: int, change_request_id: int
) -> ChangeRequestView:
    """提出を取り下げる（change.create。RULE-CHG-003 の WITHDRAW：SUBMITTED → DRAFT。5.10・CR-004）。

    保留中（PENDING）の承認申請（``target_type = CHANGE_REQUEST``）の行を消す（承認申請の状態に値を足さない。
    消したことは変更履歴に残る）。保存した影響分析は残し、提出し直すときに流し直す。提出日時は空に戻す（A-48）。
    SUBMITTED でなければ 409。
    """
    authorize_project(session, access, actor, project_id, "change.create")
    # 承認の判断と競わせない（OQ-42 (24)）：変更要求の行を先に、保留中の承認申請の行を後にロックして読み直す（A-49 の 3）
    change_request = _change_request_of(session, project_id, change_request_id, for_update=True)
    try:
        transition = transition_change_request(change_request.status, ChangeRequestAction.WITHDRAW)
    except WorkflowError as exc:
        raise _workflow_error(exc, [{"change_request_id": change_request.id, "status": change_request.status}]) from exc
    pending = session.scalars(lock_rows(
        select(ApprovalRequest)
        .where(ApprovalRequest.project_id == project_id,
               ApprovalRequest.target_type == TARGET_CHANGE_REQUEST,
               ApprovalRequest.target_id == change_request.id,
               ApprovalRequest.status == ApprovalStatus.PENDING.value)
        .order_by(ApprovalRequest.id)
    )).all()
    for approval in pending:
        session.delete(approval)
    change_request.status = transition.to_status.value
    change_request.submitted_at = None
    session.commit()
    return _view(session, change_request)


class ChangeRequestStateMachine:
    """変更要求の承認申請への判断（API-WBS-019）を、RULE-CHG-003 で変更要求の状態へ通す ``ChangeRequestApprovals``
    （``app.services.wbs``。api:CHG で ``ChangeRequestTransitionNotWired`` から差し替えた。A-37）。

    SUBMITTED → APPROVED／REJECTED。提出者（申請者）と判断する人が同じなら 403 ``SELF_APPROVAL_FORBIDDEN``、
    変更要求が SUBMITTED でなければ 409 ``INVALID_STATE``。コミットは呼び手が行う。
    """

    def decide(
        self, session: Session, approval: ApprovalRequest, status: ApprovalStatus, actor: User, now: datetime
    ) -> None:
        # 状態はロックして読み直した値で判定する（呼び手の API-WBS-019 が申請の行より先にこの行をロックしている。A-49 の 3）
        change_request = lock_row(session, ChangeRequest, approval.target_id)
        if change_request is None or change_request.project_id != approval.project_id:
            # 変更要求の承認申請は提出（API-CHG-005）でしか作られない。指す先が無いのは申請と変更要求の食い違い（fail fast）
            raise RuntimeError(
                f"承認申請 {approval.id} の指す変更要求 {approval.target_id} が、プロジェクト {approval.project_id} の"
                "変更要求の表（DB-CHG-001）に無い（申請と変更要求の食い違い）"
            )
        action = ChangeRequestAction.APPROVE if status is ApprovalStatus.APPROVED else ChangeRequestAction.REJECT
        try:
            transition = transition_change_request(change_request.status, action,
                                                   requested_by=approval.requested_by, decided_by=actor.id)
        except WorkflowError as exc:
            raise _workflow_error(exc, [{"change_request_id": change_request.id,
                                         "status": change_request.status}]) from exc
        change_request.status = transition.to_status.value


@dataclass(frozen=True)
class AppliedView:
    change_request: ChangeRequest
    approval_request_id: int | None
    scope_baseline: ScopeBaseline
    schedule_baseline: ScheduleBaseline


def _refuse_removing_elements(session: Session, removed: Sequence[int]) -> None:
    """是正措置・差異の理由が指す要素は消せない（外部キー。A-37）。

    基準の写しは外部キーを張らない写しの値なので、版に入った要素も消せる（設計書 1.1 の 4.2。CR-002）。
    """
    referenced = sorted(
        set(session.scalars(select(CorrectiveAction.element_id).where(CorrectiveAction.element_id.in_(removed))))
        | set(session.scalars(select(VarianceNote.element_id).where(VarianceNote.element_id.in_(removed))))
    )
    referenced = [i for i in referenced if i is not None]
    if referenced:
        raise AppError(422, "ELEMENT_REFERENCED", "是正措置・差異の理由が指す要素は消せません",
                       details=[{"element_ids": referenced}])


def _refuse_removing_activities(session: Session, removed: Sequence[int]) -> None:
    """実績工数のある活動は消さない（API-SCH-005 と同じ 409。A-30）。是正措置が指す活動も消せない（外部キー。A-37）。"""
    with_entries = sorted(set(session.scalars(select(TimeEntry.activity_id).where(TimeEntry.activity_id.in_(removed)))))
    if with_entries:
        raise AppError(409, "INVALID_STATE", "実績工数のある活動は消せません",
                       details=[{"activity_ids": with_entries, "reason": "HAS_TIME_ENTRIES"}])
    referenced = sorted({i for i in session.scalars(
        select(CorrectiveAction.activity_id).where(CorrectiveAction.activity_id.in_(removed))) if i is not None})
    if referenced:
        raise AppError(422, "ACTIVITY_REFERENCED", "是正措置が指す活動は消せません", details=[{"activity_ids": referenced}])


def _write_plan(session: Session, project_id: int, applied: AppliedChange) -> None:
    """RULE-CHG-001 で当てた後の計画を本計画（DB）へ書く（追加・変更・削除。仮の ID は作った行の ID へ置き換える）。"""
    before_elements = {e.id: e for e in applied.before.elements}
    after_elements = {e.id: e for e in applied.plan.elements}
    before_activities = {a.id: a for a in applied.before.activities}
    after_activities = {a.id: a for a in applied.plan.activities}
    removed_elements = [i for i in before_elements if i not in after_elements]
    removed_activities = [i for i in before_activities if i not in after_activities]
    if removed_activities:
        _refuse_removing_activities(session, removed_activities)
    if removed_elements:
        _refuse_removing_elements(session, removed_elements)

    rows = {e.id: e for e in _elements(session, project_id)}
    ids: dict[int, int] = {}

    def real(element_id: int | None) -> int | None:
        return None if element_id is None else ids.get(element_id, element_id)

    # 要素：作った順（仮の ID -1, -2, …）に足す。親は先に作られている（親子の外部キー）
    for element in sorted((e for e in applied.plan.elements if e.id < 0), key=lambda e: -e.id):
        row = WbsElement(project_id=project_id, parent_id=real(element.parent_id), kind=element.kind.value,
                         name=element.name, wbs_code=element.wbs_code, sibling_seq=element.sibling_seq,
                         last_child_seq=element.last_child_seq, sort_order=element.sort_order,
                         own_estimate_hours=element.own_estimate_hours, responsible_user_id=element.responsible_user_id,
                         needs_renumber=element.needs_renumber)
        session.add(row)
        session.flush()
        ids[element.id] = row.id
    for element in applied.plan.elements:
        if element.id < 0:
            continue
        row = rows[element.id]
        row.parent_id = real(element.parent_id)
        row.kind = element.kind.value
        row.name = element.name
        row.last_child_seq = element.last_child_seq
        row.sort_order = element.sort_order
        row.own_estimate_hours = element.own_estimate_hours
        row.responsible_user_id = element.responsible_user_id
        row.needs_renumber = element.needs_renumber
    # 第 1 階層の数え（設計書 1.1 の 4.1・5.2。第 1 階層へ足したら進む。減らさない）
    if applied.plan.top_last_child_seq is not None:
        rule_row = numbering_rule_row(session, project_id, for_update=True)
        rule_row.top_last_child_seq = max(rule_row.top_last_child_seq, applied.plan.top_last_child_seq)

    # 活動：足す・変える（日程の計算結果の列は後で計算し直す）
    activity_rows = {a.id: a for a in session.scalars(select(Activity).where(Activity.project_id == project_id))}
    for activity in sorted((a for a in applied.plan.activities if a.id < 0), key=lambda a: -a.id):
        work_package_id = real(activity.work_package_id)
        assert work_package_id is not None
        new_row = Activity(project_id=project_id, work_package_id=work_package_id, name=activity.name,
                           sort_order=activity.sort_order, requested_start=activity.requested_start,
                           duration_days=activity.duration_days, is_milestone=activity.is_milestone,
                           estimate_hours=activity.estimate_hours)
        session.add(new_row)
        session.flush()
        ids[activity.id] = new_row.id
    for activity in applied.plan.activities:
        if activity.id < 0:
            continue
        row_a = activity_rows[activity.id]
        row_a.name = activity.name
        row_a.duration_days = activity.duration_days
        row_a.is_milestone = activity.is_milestone
        row_a.estimate_hours = activity.estimate_hours
        row_a.requested_start = activity.requested_start
        row_a.work_package_id = real(activity.work_package_id) or row_a.work_package_id

    # 依存：組の差だけ消す・足す
    after_pairs = {(real(p), real(s)) for p, s in applied.plan.dependencies}
    for dependency in session.scalars(select(ActivityDependency).where(ActivityDependency.project_id == project_id)):
        if (dependency.predecessor_id, dependency.successor_id) not in after_pairs:
            session.delete(dependency)
    before_pairs = set(applied.before.dependencies)
    for predecessor, successor in sorted((p, s) for p, s in applied.plan.dependencies if (p, s) not in before_pairs):
        session.add(ActivityDependency(project_id=project_id, predecessor_id=real(predecessor),
                                       successor_id=real(successor)))
    session.flush()

    # 活動の削除（API-SCH-005 と同じ後始末：割当・進捗を消す。基準・スナップショットの写しは変えない。4.2・A-26）
    if removed_activities:
        session.execute(delete(ActivityDependency).where(or_(ActivityDependency.predecessor_id.in_(removed_activities),
                                                             ActivityDependency.successor_id.in_(removed_activities))))
        session.execute(delete(ProgressItem).where(ProgressItem.activity_id.in_(removed_activities)))
        session.execute(delete(ProgressEntry).where(ProgressEntry.activity_id.in_(removed_activities)))
        session.execute(delete(Assignment).where(Assignment.activity_id.in_(removed_activities)))
        for activity_id in removed_activities:
            session.delete(activity_rows[activity_id])
        session.flush()
    # 要素の削除（API-WBS-005 と同じ後始末。子から消す）
    if removed_elements:
        session.execute(update(WbsReviewComment).where(WbsReviewComment.element_id.in_(removed_elements))
                        .values(element_id=None))
        session.execute(delete(WbsScopeLink).where(WbsScopeLink.element_id.in_(removed_elements)))
        session.execute(delete(WbsDictionaryEntry).where(WbsDictionaryEntry.element_id.in_(removed_elements)))
        session.execute(delete(WbsCodeHistory).where(WbsCodeHistory.element_id.in_(removed_elements)))
        depth = {i: 0 for i in removed_elements}
        for element_id in removed_elements:
            current = before_elements[element_id].parent_id
            while current is not None and current in before_elements:
                depth[element_id] += 1
                current = before_elements[current].parent_id
        for element_id in sorted(removed_elements, key=lambda i: -depth[i]):
            session.delete(rows[element_id])
            session.flush()


def _write_baselines(
    session: Session, project_id: int, change_request: ChangeRequest, actor: User, now: datetime
) -> tuple[ScopeBaseline, ScheduleBaseline]:
    """RULE-CHG-004 で、反映した後の WBS と日程からスコープとスケジュールの新しい版を作る（旧版は残す。BR-12）。"""
    plan = load_plan(session, project_id)
    ids = [e.id for e in plan.elements]
    entries = {
        entry.element_id: entry
        for entry in session.scalars(select(WbsDictionaryEntry).where(WbsDictionaryEntry.element_id.in_(ids)))
    } if ids else {}
    name = f"変更要求 #{change_request.number} {change_request.title}"[:_BASELINE_NAME_LIMIT]
    try:
        rebaseline = plan_rebaseline(
            change_request_id=change_request.id,
            scope_versions=session.scalars(select(ScopeBaseline.version).where(ScopeBaseline.project_id == project_id)),
            schedule_versions=session.scalars(
                select(ScheduleBaseline.version).where(ScheduleBaseline.project_id == project_id)),
            schedule_name=name,
            elements=[
                RebaselineElement(
                    id=e.id, parent_id=e.parent_id, wbs_code=e.wbs_code, kind=e.kind, name=e.name,
                    own_estimate_hours=e.own_estimate_hours, sibling_seq=e.sibling_seq, sort_order=e.sort_order,
                    dictionary=None if e.id not in entries else {
                        field: getattr(entries[e.id], field) for field in DICTIONARY_FIELDS},
                )
                for e in plan.elements
            ],
            activities=[
                RebaselineActivity(id=a.id, work_package_id=a.work_package_id, name=a.name, start_date=a.start_date,
                                   finish_date=a.finish_date, duration_days=a.duration_days,
                                   is_milestone=a.is_milestone, estimate_hours=a.estimate_hours)
                for a in plan.ordered_activities()
            ],
        )
    except BaselineError as exc:
        raise AppError(422, "BASELINE_INVALID", str(exc)) from exc

    scope = ScopeBaseline(project_id=project_id, version=rebaseline.scope.version, reason=rebaseline.scope.reason,
                          change_request_id=change_request.id, created_by=actor.id, created_at=now)
    schedule = ScheduleBaseline(project_id=project_id, version=rebaseline.schedule.version,
                                name=rebaseline.schedule.name, reason=rebaseline.schedule.reason,
                                change_request_id=change_request.id, created_by=actor.id, created_at=now)
    session.add_all([scope, schedule])
    session.flush()
    session.add_all(
        ScopeBaselineElement(scope_baseline_id=scope.id, element_id=c.element_id,
                             parent_element_id=c.parent_element_id, wbs_code=c.wbs_code, kind=c.kind, name=c.name,
                             own_estimate_hours=c.own_estimate_hours,
                             dictionary=None if c.dictionary is None else dict(c.dictionary))
        for c in rebaseline.scope.elements
    )
    session.add_all(
        ScheduleBaselineItem(schedule_baseline_id=schedule.id, item_type=i.item_type, activity_id=i.activity_id,
                             element_id=i.element_id, wbs_code=i.wbs_code, name=i.name, start_date=i.start_date,
                             finish_date=i.finish_date, duration_days=i.duration_days, is_milestone=i.is_milestone,
                             estimate_hours=i.estimate_hours)
        for i in rebaseline.schedule.items
    )
    return scope, schedule


def apply_change_request(
    session: Session, access: ProjectAccess, actor: User, project_id: int, change_request_id: int, *, now: datetime
) -> AppliedView:
    """承認済みの変更を反映して再基準化する（change.apply。CHG-04）。

    1. RULE-CHG-003 の APPLY（APPROVED → APPLIED。それ以外は 409 ``INVALID_STATE``）。WBS が ``BASELINED`` でなければ
       （承認を経ていない・承認待ち・基準の後に直接変えた）409 ``INVALID_STATE``。5.10 で ``BASELINED`` へ入るのは承認だけで、
       反映で承認を飛ばさない（A-47）
    2. RULE-CHG-001 で今の計画へ当て（当てられなければ 400・422）、本計画へ書く。日程を計算し直す（RULE-SCH-001〜003）
    3. RULE-CHG-004 でスコープとスケジュールの新しい版（理由 CHANGE_REQUEST）を作る。旧版は残す（BR-12）
    4. 計画は新しい版と一致するので、計画を書いて ``DRAFT`` に戻った WBS の状態を ``BASELINED`` に保つ

    基準の写しは変えない（版に入った要素・活動も消せる。設計書 1.1 の 4.2。CR-002）。どこかで止まれば何も書かない。
    """
    authorize_project(session, access, actor, project_id, "change.apply")
    # 二重の反映・WBS の承認の操作と競わせない：変更要求、プロジェクトの順に行をロックし、読み直した値で判定する（A-49 の 3）
    change_request = _change_request_of(session, project_id, change_request_id, for_update=True)
    try:
        transition = transition_change_request(change_request.status, ChangeRequestAction.APPLY)
    except WorkflowError as exc:
        raise _workflow_error(exc, [{"change_request_id": change_request.id, "status": change_request.status}]) from exc
    project = lock_row(session, Project, project_id)
    assert project is not None
    if project.wbs_status != WbsStatus.BASELINED.value:
        raise AppError(409, "INVALID_STATE", "WBS が承認済み（BASELINED）のときだけ変更を反映できます",
                       details=[{"wbs_status": project.wbs_status}])
    applied = _apply(_plan_model(session, project_id), change_request.operations or [])
    try:
        _write_plan(session, project_id, applied)
        calculate_and_store(session, project_id)
        scope, schedule = _write_baselines(session, project_id, change_request, actor, now)
    except AppError:
        session.rollback()
        raise
    project.wbs_status = WbsStatus.BASELINED.value
    change_request.status = transition.to_status.value
    change_request.applied_by = actor.id
    change_request.applied_at = now
    session.commit()
    return AppliedView(change_request=change_request, approval_request_id=_latest_approval_id(session, change_request),
                       scope_baseline=scope, schedule_baseline=schedule)


# ---------------------------------------------------------------------------
# 是正措置（API-CHG-007〜009）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CorrectiveActionView:
    action: CorrectiveAction
    wbs_code: str | None
    overdue: bool


def _invalid(loc: str, message: str) -> AppError:
    return AppError(400, "VALIDATION_ERROR", "入力に誤りがあります", details=[{"loc": ["body", loc], "message": message}])


def _check_links(session: Session, access: ProjectAccess, project_id: int, values: Mapping[str, Any]) -> None:
    """紐付け（要素・活動・報告日）と担当は、このプロジェクトのものであること（違えば 400）。"""
    checks: tuple[tuple[str, Any], ...] = (
        ("element_id", WbsElement), ("activity_id", Activity), ("status_report_id", StatusReport),
    )
    for name, model in checks:
        value = values.get(name)
        if value is None:
            continue
        row = session.get(model, value)
        if row is None or row.project_id != project_id:
            raise _invalid(name, "このプロジェクトのものではありません")
    owner = values.get("owner_user_id")
    if owner is not None and access.membership(session, project_id, owner) is None:
        raise _invalid("owner_user_id", "このプロジェクトのメンバーではありません")


def _action_views(session: Session, project_id: int, actions: Sequence[CorrectiveAction],
                  today: date) -> list[CorrectiveActionView]:
    codes = {e.id: e.wbs_code for e in _elements(session, project_id)}
    work_packages = dict(session.execute(  # type: ignore[arg-type]
        select(Activity.id, Activity.work_package_id).where(Activity.project_id == project_id)).all())
    views: list[CorrectiveActionView] = []
    for action in actions:
        element_id = action.element_id if action.element_id is not None else work_packages.get(action.activity_id)
        views.append(CorrectiveActionView(
            action=action, wbs_code=codes.get(element_id) if element_id is not None else None,
            overdue=is_corrective_action_overdue(action.due_date, action.status, today=today),
        ))
    return views


def create_corrective_action(
    session: Session, access: ProjectAccess, actor: User, project_id: int, values: Mapping[str, Any], *, today: date
) -> CorrectiveActionView:
    """是正措置を登録する（action.manage。CHG-06）。状態は NOT_STARTED。

    要素か活動の少なくとも一方に紐付ける（WBS ID で絞れるようにする。A-37）。報告日は任意。紐付けと担当はこのプロジェクトのもの。
    """
    authorize_project(session, access, actor, project_id, "action.manage")
    if values.get("element_id") is None and values.get("activity_id") is None:
        raise _invalid("element_id", "WBS 要素か活動の少なくとも一方に紐付けます")
    _check_links(session, access, project_id, values)
    action = CorrectiveAction(project_id=project_id, element_id=values.get("element_id"),
                              activity_id=values.get("activity_id"), status_report_id=values.get("status_report_id"),
                              cause=values["cause"], action=values["action"], owner_user_id=values.get("owner_user_id"),
                              due_date=values.get("due_date"))
    session.add(action)
    session.commit()
    return _action_views(session, project_id, [action], today)[0]


def list_corrective_actions(
    session: Session,
    access: ProjectAccess,
    actor: User,
    project_id: int,
    *,
    status: str | None,
    overdue: bool | None,
    wbs_code: str | None,
    today: date,
    limit: int,
    offset: int,
) -> tuple[list[CorrectiveActionView], int]:
    """是正措置の一覧（project.view。状態・期限超過・WBS ID で絞れる。登録の順。CHG-06）。

    - 期限超過は RULE-CHG-005（期限 ＜ 今日 かつ 未完了）
    - WBS ID（旧 ID でもよい）は、その要素と配下の要素、それらの WP の活動に紐付いた措置に絞る。解決できなければ空の一覧
    - 委託先だけで読む利用者には、自分の割当の活動とその WBS の経路に紐付いた措置だけ（9.2 の注記）
    """
    table = authorize_project(session, access, actor, project_id, "project.view")
    query = select(CorrectiveAction).where(CorrectiveAction.project_id == project_id)
    if status is not None:
        query = query.where(CorrectiveAction.status == status)
    actions = list(session.scalars(query.order_by(CorrectiveAction.id)))
    if wbs_code is not None:
        elements = _elements(session, project_id)
        root = _resolve(wbs_code, _coded(elements), _code_history(session, project_id))
        scope = _subtree(root, _children(elements)) if root is not None else set()
        activity_ids = set(session.scalars(select(Activity.id).where(Activity.work_package_id.in_(scope)))) if scope else set()
        actions = [a for a in actions if a.element_id in scope or a.activity_id in activity_ids]
    visible = _visible(session, access, actor, project_id, table)
    if visible is not None:
        actions = [a for a in actions
                   if a.activity_id in visible.activity_ids or a.element_id in visible.element_ids]
    views = _action_views(session, project_id, actions, today)
    if overdue is not None:
        views = [v for v in views if v.overdue is overdue]
    return views[offset:offset + limit], len(views)


def update_corrective_action(
    session: Session,
    access: ProjectAccess,
    actor: User,
    project_id: int,
    action_id: int,
    changes: Mapping[str, Any],
    *,
    today: date,
) -> CorrectiveActionView:
    """是正措置の状態・効果を更新する（action.manage。CHG-06）。

    状態は RULE-CHG-005（NOT_STARTED → IN_PROGRESS → DONE、DONE → IN_PROGRESS。それ以外は 409 ``INVALID_STATE``）。
    載せた項目（status・effect・owner_user_id・due_date）だけを変える。
    """
    authorize_project(session, access, actor, project_id, "action.manage")
    action = session.get(CorrectiveAction, action_id)
    if action is None or action.project_id != project_id:
        raise AppError(404, "NOT_FOUND", "是正措置が見つかりません", details=[{"action_id": action_id}])
    _check_links(session, access, project_id, {k: v for k, v in changes.items() if k == "owner_user_id"})
    if "status" in changes:
        try:
            action.status = transition_corrective_action(action.status, changes["status"]).value
        except WorkflowError as exc:
            raise _workflow_error(exc, [{"action_id": action.id, "status": action.status,
                                         "to_status": changes["status"]}]) from exc
    for name in ("effect", "owner_user_id", "due_date"):
        if name in changes:
            setattr(action, name, changes[name])
    session.commit()
    return _action_views(session, project_id, [action], today)[0]


# ---------------------------------------------------------------------------
# 変更履歴（API-CHG-010）
# ---------------------------------------------------------------------------


def search_audit_logs(
    session: Session,
    access: ProjectAccess,
    actor: User,
    project_id: int,
    *,
    date_from: date | None,
    date_to: date | None,
    user_id: int | None,
    entity_type: str | None,
    entity_id: int | None,
    wbs_code: str | None,
    limit: int,
    offset: int,
) -> tuple[list[AuditLog], int]:
    """変更履歴の検索（audit.view。期間・変更者・対象・WBS ID。新しい順。CHG-09）。

    期間は UTC の日付で両端を含む（``date_from`` の 0 時 〜 ``date_to`` の翌日 0 時の前。A-37）。WBS ID は記録した時点の ID と一致するもの。
    """
    authorize_project(session, access, actor, project_id, "audit.view")
    if date_from is not None and date_to is not None and date_from > date_to:
        raise AppError(400, "VALIDATION_ERROR", "入力に誤りがあります",
                       details=[{"loc": ["query", "date_from"], "message": "期間の始まりが終わりより後です"}])
    conditions: list[Any] = [AuditLog.project_id == project_id]
    if date_from is not None:
        conditions.append(AuditLog.occurred_at >= datetime.combine(date_from, time.min, tzinfo=UTC))
    if date_to is not None:
        conditions.append(AuditLog.occurred_at < datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=UTC))
    if user_id is not None:
        conditions.append(AuditLog.user_id == user_id)
    if entity_type is not None:
        conditions.append(AuditLog.entity_type == entity_type)
    if entity_id is not None:
        conditions.append(AuditLog.entity_id == entity_id)
    if wbs_code is not None:
        conditions.append(AuditLog.wbs_code == wbs_code)
    total = session.scalar(select(func.count()).select_from(AuditLog).where(*conditions)) or 0
    rows = list(session.scalars(select(AuditLog).where(*conditions)
                                .order_by(AuditLog.occurred_at.desc(), AuditLog.id.desc())
                                .limit(limit).offset(offset)))
    return rows, int(total)
