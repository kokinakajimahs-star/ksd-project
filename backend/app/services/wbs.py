"""WBS（WBS）のユースケース（設計書 6.2 の API-WBS-001〜023、5.2・5.3・5.10、9 章）。

- 読む操作は project.view、書く操作は wbs.edit（RULE-AUTH-001 を ``authorize_project`` で通す。無いプロジェクトは 404 を先に返す）。
  委託先だけで読む利用者は、自分の割当の活動とその WBS の経路（``own_assignment_scope``）に絞る
- WBS を変える操作は、同じトランザクションで ``projects.wbs_status`` を RULE-WBS-006 の ``EDIT`` で動かす
  （基準化の後なら DRAFT へ戻る。承認待ちの間は 409 ``INVALID_STATE``。OQ-17）
- 採番・移動・改番は RULE-WBS-001・002（``app.domain.wbs_code``）、木の整合は RULE-WBS-003、集計は RULE-EST-001、
  検査は RULE-WBS-004・005、責任分担表は RULE-WBS-007、横断照会は RULE-WBS-008、承認は RULE-WBS-006 を使う
- 承認申請（API-WBS-018〜020）は WBS と変更要求で共通の表（``approval_requests``）。判断は wbs.approve か change.approve

**別の群の表を読む口・書く口**（どれも ``app.main.create_app`` で ``app.state`` に載せる。A-17・A-18）：

- ``ActivitiesReader``（``app.state.wbs_activities``）：活動（``activities``。DB-SCH-001）。表を読む ``ActivitiesTable``（db:SCH で差し替えた。A-19）。
  ``ActivitiesTableAbsent``（活動なし）は表の無い間の口
- ``CrossGroupReaders``（``app.state.wbs_readers``）：割当（DB-EFF-001）・見積の記録（DB-EST-001）・実績工数（DB-EFF-002）・
  変更要求（DB-CHG-001）・是正措置（DB-CHG-002）・EAC（RULE-EFF-002）。既定はどれも「表がまだ無い」（0 件・EAC なし）。
  割当はスケジュールの ``AssignmentsStore.assignments``、実績工数は ``time_entries_table`` へ差し替えた（db:EFF。A-27）。
  EAC は ``app.services.effort.eac_by_element`` へ差し替えた（api:EFF。A-30）。
  変更要求は ``change_requests_table``、是正措置は ``corrective_actions_table`` へ差し替えた（db:CHG。A-31）。
  見積の記録は ``estimate_records_table`` へ差し替えた（db:EST。A-32）
- ``ChangeRequestApprovals``（``app.state.change_request_approvals``）：変更要求の承認申請への判断を変更要求へ反映する。
  ``ChangeRequestsTableAbsent`` は表の無い間の口（判断を拒む。fail fast）。表を作った db:CHG で ``ChangeRequestTransitionNotWired``
  （申請の指す変更要求を読み、状態遷移の実装が無いので止める）へ差し替えた（A-31）。api:CHG が RULE-CHG-003 を通す実装へ差し替える

**表を作るスコープは読む実装を書き、``app.state`` を差し替える。** 差し替え忘れは
``backend/tests/api/test_wbs.py`` の ``test_*_wired_once_*`` が落とす。

第 1 階層の順番の数えは ``wbs_numbering_rules.top_last_child_seq``（設計書 1.1 の 4.1・5.2。CR-003）に置く。新規・移動・改番で進め、減らさない。
採番規則の行はプロジェクトを作るとき（API-PRJ-001）に作り、読むだけの操作では作らない（無ければ既定の値で読む。A-49 の 3）。

**同時実行**（A-49 の 3）：WBS の状態（``projects.wbs_status``）・レビュー・要素を変える操作と承認の判断は、``projects`` の行を
``SELECT … FOR UPDATE``（``app.infra.db.lock_row``）で読み直してから判定する。承認の判断は、申請の対象の行（WBS は ``projects``、
変更要求は ``change_requests``）を先に、申請の行を後にロックする（変更要求の提出・取り下げと同じ順。デッドロックを避ける）。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.domain.authz import ViewLevel, VisibleScope, authorize, own_assignment_scope, project_view_level
from app.domain.estimate import ActivityEstimate, EstimateError, EstimateRollup, WbsNode, rollup_estimates
from app.domain.wbs_code import (
    CodedElement,
    CodeHistoryEntry,
    NumberingRule,
    WbsCodeError,
    move_element,
    new_element_code,
    renumber,
    resolve_code,
    subtree_ids,
)
from app.domain.wbs_rules import (
    ActivityRef,
    AssignmentRef,
    ChangeRequestRef,
    CorrectiveActionRef,
    DictionaryRef,
    EstimateRecordRef,
    EstimateTarget,
    FindingLevel,
    HundredPercentFinding,
    MissingCompletion,
    ResponsibilityElement,
    ResponsibilityMatrix,
    ScopeItemRef,
    ScopeKind,
    ScopeLinkRef,
    TimeEntryRef,
    WbsRuleError,
    WbsTrace,
    build_responsibility_matrix,
    check_hundred_percent,
    ensure_hierarchy,
    find_missing_completion,
    trace_wbs_code,
)
from app.domain.workflow import (
    ApprovalDecision,
    ApprovalStatus,
    WbsAction,
    WbsReadiness,
    WorkflowError,
    WorkflowErrorCode,
    decide_approval,
    transition_wbs,
)
from app.errors import AppError
from app.infra.db import lock_row, lock_rows
from app.models.chg import ChangeRequest, CorrectiveAction
from app.models.cmn import User
from app.models.eff import TimeEntry, VarianceNote
from app.models.est import EstimateRecord
from app.models.prj import Project, ProjectMember, ScopeItem
from app.models.sch import Activity
from app.models.wbs import (  # API 層は as を付けた型をここから取る（1.3：API はモデルを直接 import しない）
    COMMENT_STATUS_INITIAL,
    MIN_DIGITS_DEFAULT,
    PAD_FIRST_LEVEL_DEFAULT,
    PREFIX_DEFAULT,
    REVIEW_STATUS_CLOSED,
    REVIEW_STATUS_INITIAL,
    SEPARATOR_DEFAULT,
    ApprovalRequest,
    ScopeBaseline as ScopeBaseline,
    ScopeBaselineElement,
    WbsCodeHistory,
    WbsDictionaryEntry as WbsDictionaryEntry,
    WbsElement as WbsElement,
    WbsNumberingRule as WbsNumberingRule,
    WbsReview as WbsReview,
    WbsReviewComment as WbsReviewComment,
    WbsScopeLink,
)
from app.services.auth import authorize_project, load_role_permission_table, principal_of
from app.services.project_access import ProjectAccess

# PATCH で変えられる列（API-WBS-003）と、そのうち空にできない列
UPDATABLE_FIELDS = frozenset({"name", "kind", "own_estimate_hours", "responsible_user_id", "responsible_team"})
_NON_NULLABLE_FIELDS = frozenset({"name", "kind"})
DICTIONARY_FIELDS = ("deliverable", "work_scope", "completion_criteria", "assumptions")


# ---------------------------------------------------------------------------
# 別の群の表を読む口
# ---------------------------------------------------------------------------


class ActivitiesReader(Protocol):
    """プロジェクトの活動（``activities``。DB-SCH-001）のうち WBS の判定・集計に要る列を読む。"""

    def activities(self, session: Session, project_id: int) -> list[ActivityEstimate]:
        """活動の ID・置き場の WP・見積（人時）。"""
        ...


class ActivitiesTableAbsent:
    """活動の表（DB-SCH-001）がまだ無い間の ``ActivitiesReader``。活動は 1 つも無い（今のスキーマでの事実）。"""

    def activities(self, session: Session, project_id: int) -> list[ActivityEstimate]:
        return []


class ActivitiesTable:
    """活動の表（``activities``。DB-SCH-001）を読む ``ActivitiesReader``（db:SCH で差し替えた。A-19）。

    プロジェクトの活動の ID・置き場の WP・見積（人時。空は ``None``）を活動の ID の順に返す。
    """

    def activities(self, session: Session, project_id: int) -> list[ActivityEstimate]:
        rows = session.execute(
            select(Activity.id, Activity.work_package_id, Activity.estimate_hours)
            .where(Activity.project_id == project_id)
            .order_by(Activity.id)
        ).all()
        return [
            ActivityEstimate(id=row.id, work_package_id=row.work_package_id, estimate_hours=row.estimate_hours)
            for row in rows
        ]


def no_assignments(session: Session, project_id: int) -> list[AssignmentRef]:
    """担当・割当の表（DB-EFF-001）がまだ無い間の既定。割当は 1 つも無い。"""
    return []


def no_estimate_records(session: Session, project_id: int) -> list[EstimateRecordRef]:
    """見積の記録の表（DB-EST-001）がまだ無い間の既定。記録は 1 つも無い。"""
    return []


def estimate_records_table(session: Session, project_id: int) -> list[EstimateRecordRef]:
    """見積の記録の表（``estimate_records``。DB-EST-001）から、プロジェクトの全ての記録の対象を ID の順に読む（db:EST で差し替えた。A-32）。

    同じ対象の古い記録（見積の履歴）も全て渡す（横断照会は見積の記録を全て返す。RULE-WBS-008）。
    """
    rows = session.execute(
        select(EstimateRecord.id, EstimateRecord.target_type, EstimateRecord.target_id)
        .where(EstimateRecord.project_id == project_id)
        .order_by(EstimateRecord.id)
    ).all()
    return [
        EstimateRecordRef(id=r.id, target_type=EstimateTarget(r.target_type), target_id=r.target_id) for r in rows
    ]


def no_time_entries(session: Session, project_id: int) -> list[TimeEntryRef]:
    """実績工数の表（DB-EFF-002）がまだ無い間の既定。実績は 1 つも無い。"""
    return []


def time_entries_table(session: Session, project_id: int) -> list[TimeEntryRef]:
    """実績工数の表（``time_entries``。DB-EFF-002）から、プロジェクトの活動の全ての実績を読む（db:EFF で差し替えた。A-27）。"""
    rows = session.execute(
        select(TimeEntry.id, TimeEntry.activity_id, TimeEntry.hours)
        .join(Activity, Activity.id == TimeEntry.activity_id)
        .where(Activity.project_id == project_id)
        .order_by(TimeEntry.id)
    ).all()
    return [TimeEntryRef(id=r.id, activity_id=r.activity_id, hours=r.hours) for r in rows]


def no_change_requests(session: Session, project_id: int) -> list[ChangeRequestRef]:
    """変更要求の表（DB-CHG-001）がまだ無い間の既定。変更要求は 1 つも無い。"""
    return []


def no_corrective_actions(session: Session, project_id: int) -> list[CorrectiveActionRef]:
    """是正措置の表（DB-CHG-002）がまだ無い間の既定。是正措置は 1 つも無い。"""
    return []


def change_requests_table(session: Session, project_id: int) -> list[ChangeRequestRef]:
    """変更要求の表（``change_requests``。DB-CHG-001）から、プロジェクトの全ての変更要求の対象の WBS ID を読む（db:CHG で差し替えた。A-31）。

    対象の WBS ID は起票した時点の並びのまま渡す（旧 ID の解決は RULE-WBS-008 の担当）。状態では絞らない（照会は全ての変更要求を返す）。
    """
    rows = session.execute(
        select(ChangeRequest.id, ChangeRequest.target_codes)
        .where(ChangeRequest.project_id == project_id)
        .order_by(ChangeRequest.id)
    ).all()
    return [ChangeRequestRef(id=r.id, target_codes=tuple(str(code) for code in (r.target_codes or ()))) for r in rows]


def corrective_actions_table(session: Session, project_id: int) -> list[CorrectiveActionRef]:
    """是正措置の表（``corrective_actions``。DB-CHG-002）から、プロジェクトの全ての是正措置の紐付け（要素・活動）を読む（db:CHG。A-31）。"""
    rows = session.execute(
        select(CorrectiveAction.id, CorrectiveAction.element_id, CorrectiveAction.activity_id)
        .where(CorrectiveAction.project_id == project_id)
        .order_by(CorrectiveAction.id)
    ).all()
    return [CorrectiveActionRef(id=r.id, element_id=r.element_id, activity_id=r.activity_id) for r in rows]


def eac_not_available(session: Session, project_id: int) -> Mapping[int, Decimal] | None:
    """EAC の集計（RULE-EFF-002）を読む口がまだ無い間の既定。EAC は返さない（``None``。0 とは言わない）。"""
    return None


@dataclass(frozen=True)
class CrossGroupReaders:
    """責任分担表（API-WBS-011）と横断照会（API-WBS-023）が読む、別の群の表（どれもプロジェクトの全件）。

    既定は「表がまだ無い」（0 件・EAC なし）。表を作るスコープが読む関数へ差し替える（``app.state.wbs_readers``）。
    """

    assignments: Callable[[Session, int], Sequence[AssignmentRef]] = field(default=no_assignments)
    estimate_records: Callable[[Session, int], Sequence[EstimateRecordRef]] = field(default=no_estimate_records)
    time_entries: Callable[[Session, int], Sequence[TimeEntryRef]] = field(default=no_time_entries)
    change_requests: Callable[[Session, int], Sequence[ChangeRequestRef]] = field(default=no_change_requests)
    corrective_actions: Callable[[Session, int], Sequence[CorrectiveActionRef]] = field(default=no_corrective_actions)
    eac_hours: Callable[[Session, int], Mapping[int, Decimal] | None] = field(default=eac_not_available)


class ChangeRequestApprovals(Protocol):
    """変更要求の承認申請（``target_type = CHANGE_REQUEST``）への判断を、変更要求（DB-CHG-001）へ反映する口。"""

    def decide(
        self, session: Session, approval: ApprovalRequest, status: ApprovalStatus, actor: User, now: datetime
    ) -> None:
        """変更要求の状態を RULE-CHG-003 で動かす（SUBMITTED → APPROVED / REJECTED）。コミットは呼び手が行う。"""
        ...


class ChangeRequestsTableAbsent:
    """変更要求の表（DB-CHG-001）がまだ無い間の ``ChangeRequestApprovals``。

    変更要求の承認申請は提出（API-CHG-005）でしか作られず、表が無い間は存在しないはずなので、来たら止める（fail fast）。
    """

    def decide(
        self, session: Session, approval: ApprovalRequest, status: ApprovalStatus, actor: User, now: datetime
    ) -> None:
        raise RuntimeError(
            f"承認申請 {approval.id} は変更要求のものだが、変更要求の表（DB-CHG-001）を扱う実装が載っていない"
            "（api:CHG のスコープが app.state.change_request_approvals を差し替える）"
        )


class ChangeRequestTransitionNotWired:
    """変更要求の表（DB-CHG-001）はあるが、状態遷移（RULE-CHG-003）を変更要求へ通す実装（api:CHG）がまだ無い間の
    ``ChangeRequestApprovals``（db:CHG で差し替えた。A-31。A-19 の ``SchedulingNotWired`` と同じ形）。

    申請の指す変更要求を表から読み、無ければ申請と変更要求の食い違いとして、あれば状態を動かす実装が無いので止める（fail fast）。
    どちらでも変更要求の状態は変えない（判断だけが残って変更要求が SUBMITTED のまま、という食い違いを作らない）。
    変更要求の承認申請は提出（API-CHG-005）でしか作られないので、api:CHG が載るまでは来ないはず。差し替え忘れは
    ``backend/tests/api/test_wbs.py::test_change_request_approvals_apply_transition_once_change_api_exists`` が落とす。
    """

    def decide(
        self, session: Session, approval: ApprovalRequest, status: ApprovalStatus, actor: User, now: datetime
    ) -> None:
        change_request = session.scalar(
            select(ChangeRequest).where(
                ChangeRequest.id == approval.target_id, ChangeRequest.project_id == approval.project_id
            )
        )
        if change_request is None:
            raise RuntimeError(
                f"承認申請 {approval.id} の指す変更要求 {approval.target_id} が、プロジェクト {approval.project_id} の"
                "変更要求の表（DB-CHG-001）に無い（申請と変更要求の食い違い）"
            )
        raise RuntimeError(
            f"承認申請 {approval.id} は変更要求 {change_request.id}（DB-CHG-001）のものだが、変更要求の状態遷移（RULE-CHG-003）を"
            "通す実装が載っていない（api:CHG のスコープが app.state.change_request_approvals を差し替える）"
        )


# ---------------------------------------------------------------------------
# 共通の部品
# ---------------------------------------------------------------------------


def _validation_error(details: list[dict[str, Any]], message: str = "入力に誤りがあります") -> AppError:
    return AppError(400, "VALIDATION_ERROR", message, details=details)


def _element_not_found(element_id: int) -> AppError:
    return AppError(404, "NOT_FOUND", "WBS 要素が見つかりません", details=[{"element_id": element_id}])


def _hours(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


@dataclass(frozen=True)
class WbsContext:
    """1 つの操作で読む WBS の全体（要素は ID の順、活動、採番規則の行）。"""

    project: Project
    elements: list[WbsElement]
    activities: list[ActivityEstimate]
    rule_row: WbsNumberingRule

    @property
    def by_id(self) -> dict[int, WbsElement]:
        return {element.id: element for element in self.elements}

    @property
    def rule(self) -> NumberingRule:
        row = self.rule_row
        return NumberingRule(
            separator=row.separator, min_digits=row.min_digits, prefix=row.prefix, pad_first_level=row.pad_first_level
        )


def numbering_rule_row(session: Session, project_id: int, *, for_update: bool = False) -> WbsNumberingRule:
    """プロジェクトの採番規則の行（DB-WBS-002）。行はプロジェクトを作るとき（API-PRJ-001）に作る。

    行が無い（API-PRJ-001 を通らずに作られ、移行でもまだ埋めていない）ときは、4.1 の既定の値と、第 1 階層の数えを
    今ある第 1 階層の順番の最大から始めた行（移行 0009 の埋め方と同じ値）を使う。

    - 読むだけ（既定）：行が無ければ**書かずに**、その値の行をセッションに載せずに返す（GET で行を作らない。A-49 の 3）
    - 書く（``for_update``）：行を ``SELECT … FOR UPDATE`` で読み直す。無ければその値で作る（flush）。同時に 2 つ作られると
      一意制約（``project_id``）の違反になり、409 ``CONFLICT``（``app.main``）
    """
    query = select(WbsNumberingRule).where(WbsNumberingRule.project_id == project_id)
    row = session.scalar(lock_rows(query) if for_update else query)
    if row is not None:
        return row
    top = session.scalar(
        select(func.max(WbsElement.sibling_seq)).where(WbsElement.project_id == project_id, WbsElement.parent_id.is_(None))
    )
    row = WbsNumberingRule(project_id=project_id, separator=SEPARATOR_DEFAULT, min_digits=MIN_DIGITS_DEFAULT,
                           prefix=PREFIX_DEFAULT, pad_first_level=PAD_FIRST_LEVEL_DEFAULT, top_last_child_seq=top or 0)
    if for_update:
        session.add(row)
        session.flush()
    return row


def load_wbs_context(
    session: Session, project_id: int, activities: ActivitiesReader, *, for_update: bool = False
) -> WbsContext:
    """WBS の全体を読む。書く操作は ``for_update``：プロジェクトの行をロックして読み直し（状態の判定はその値で行う）、
    その後に要素を読み、採番規則の行もロックする（無ければ作る）。読むだけの操作は何も書かない。"""
    project = lock_row(session, Project, project_id) if for_update else session.get(Project, project_id)
    if project is None:  # authorize_project が先に 404 を返すので、ここへは来ない
        raise AppError(404, "NOT_FOUND", "プロジェクトが見つかりません", details=[{"project_id": project_id}])
    elements = list(session.scalars(select(WbsElement).where(WbsElement.project_id == project_id).order_by(WbsElement.id)))
    return WbsContext(
        project=project,
        elements=elements,
        activities=activities.activities(session, project_id),
        rule_row=numbering_rule_row(session, project_id, for_update=for_update),
    )


def coded_element(element: WbsElement, **overrides: Any) -> CodedElement:
    values: dict[str, Any] = {
        "id": element.id,
        "parent_id": element.parent_id,
        "wbs_code": element.wbs_code,
        "sibling_seq": element.sibling_seq,
        "last_child_seq": element.last_child_seq,
        "sort_order": element.sort_order,
        "needs_renumber": element.needs_renumber,
    }
    values.update(overrides)
    return CodedElement(**values)


def _node(element: WbsElement, **overrides: Any) -> WbsNode:
    values: dict[str, Any] = {
        "id": element.id,
        "parent_id": element.parent_id,
        "kind": element.kind,
        "own_estimate_hours": element.own_estimate_hours,
    }
    values.update(overrides)
    return WbsNode(**values)


def _ensure_tree(nodes: Sequence[WbsNode], activities: Sequence[ActivityEstimate]) -> None:
    """操作した後の木を RULE-WBS-003 で確かめる。不整合は 422（コードは最初の不整合の種類）。"""
    try:
        ensure_hierarchy(nodes, [ActivityRef(id=a.id, work_package_id=a.work_package_id) for a in activities])
    except WbsRuleError as exc:
        details = [
            {"code": issue.code.value, "element_ids": list(issue.element_ids), "activity_id": issue.activity_id,
             "message": issue.message}
            for issue in exc.details
        ]
        raise AppError(422, exc.code, str(exc), details=details) from exc


def mark_wbs_edited(project: Project) -> None:
    """WBS を変えたときの状態（RULE-WBS-006 の EDIT）。承認待ちの間は 409 ``INVALID_STATE``。"""
    try:
        transition = transition_wbs(project.wbs_status, WbsAction.EDIT)
    except WorkflowError as exc:
        raise AppError(409, exc.code.value, "承認待ちの間は WBS を変えられません（差戻しの後に変えます）",
                       details=[{"wbs_status": project.wbs_status}]) from exc
    project.wbs_status = transition.to_status.value


def _check_responsible(session: Session, project_id: int, user_id: int | None, loc: list[Any]) -> None:
    """責任者はそのプロジェクトのメンバーである有効な利用者（WBS-10）。そうでなければ 400。"""
    if user_id is None:
        return
    user = session.get(User, user_id)
    member = session.scalar(
        select(ProjectMember.id).where(ProjectMember.project_id == project_id, ProjectMember.user_id == user_id)
    )
    if user is None or not user.is_active or member is None:
        raise _validation_error([{"loc": loc, "message": "責任者はプロジェクトのメンバーである有効な利用者にします"}])


def _element_of(context: WbsContext, element_id: int) -> WbsElement:
    element = context.by_id.get(element_id)
    if element is None:
        raise _element_not_found(element_id)
    return element


def _visible_scope(
    session: Session, access: ProjectAccess, actor: User, context: WbsContext, table: Mapping[str, frozenset[str]]
) -> VisibleScope | None:
    """project.view で読める活動と要素。全て読めるなら ``None``。委託先は自分の割当の活動とその WBS の経路（9.2 の注記）。"""
    membership = access.membership(session, context.project.id, actor.id)
    level = project_view_level(principal_of(actor), role_permissions=table, membership=membership)
    if level is ViewLevel.ALL:
        return None
    assigned = membership.assigned_activity_ids if membership is not None else frozenset()
    activity_elements = {a.id: a.work_package_id for a in context.activities}
    return own_assignment_scope(
        [a for a in assigned if a in activity_elements],
        activity_elements=activity_elements,
        element_parents={element.id: element.parent_id for element in context.elements},
    )


def _visible_element_ids(
    session: Session, access: ProjectAccess, actor: User, context: WbsContext, table: Mapping[str, frozenset[str]]
) -> frozenset[int] | None:
    """project.view で読める要素。全て読めるなら ``None``。委託先は自分の割当の活動とその WBS の経路（9.2 の注記）。"""
    scope = _visible_scope(session, access, actor, context, table)
    return None if scope is None else scope.element_ids


def _visible_context(context: WbsContext, scope: VisibleScope) -> WbsContext:
    """見える要素・活動だけの文脈（9.2 の注記：委託先に返す集計値・件数は見える要素・活動だけから求める）。

    見える要素は親について閉じ、見える活動の WP は見える要素なので、絞った後も木として集計できる。
    """
    return replace(
        context,
        elements=[e for e in context.elements if e.id in scope.element_ids],
        activities=[a for a in context.activities if a.id in scope.activity_ids],
    )


def preorder_elements(elements: Sequence[WbsElement]) -> list[tuple[WbsElement, int]]:
    """先行順（兄弟は表示順）と深さ（最上位が 1）。"""
    children: dict[int | None, list[WbsElement]] = {}
    for element in elements:
        children.setdefault(element.parent_id, []).append(element)
    for kids in children.values():
        kids.sort(key=lambda e: (e.sort_order, e.sibling_seq, e.id))
    result: list[tuple[WbsElement, int]] = []
    stack: list[tuple[WbsElement, int]] = [(e, 1) for e in reversed(children.get(None, []))]
    while stack:
        element, depth = stack.pop()
        result.append((element, depth))
        stack.extend((kid, depth + 1) for kid in reversed(children.get(element.id, [])))
    return result


def _rollup(context: WbsContext) -> EstimateRollup:
    try:
        return rollup_estimates([_node(e) for e in context.elements], context.activities)
    except EstimateError as exc:  # 保存した木が壊れている（WP の子など）。推し量らずに止める
        raise RuntimeError(f"プロジェクト {context.project.id} の WBS を集計できない: {exc}") from exc


# ---------------------------------------------------------------------------
# 検査（API-WBS-012。API-WBS-001 の件数にも使う）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidationResult:
    tolerance: Decimal
    findings: tuple[HundredPercentFinding, ...]
    missing: tuple[MissingCompletion, ...]

    @property
    def errors(self) -> int:
        return sum(1 for f in self.findings if f.level is FindingLevel.ERROR)

    @property
    def warnings(self) -> int:
        return sum(1 for f in self.findings if f.level is FindingLevel.WARNING)


def _tolerance(project: Project) -> Decimal:
    value = (project.settings or {}).get("hundred_percent_tolerance")
    if value is None:
        raise RuntimeError(f"projects(id={project.id}).settings に hundred_percent_tolerance が無い（4.2）")
    return Decimal(str(value))


def validate(session: Session, context: WbsContext) -> ValidationResult:
    """100% ルール（RULE-WBS-004。許容幅は 4.2 の ``hundred_percent_tolerance``）と完了条件（RULE-WBS-005）。"""
    project_id = context.project.id
    element_ids = [e.id for e in context.elements]
    items = session.scalars(select(ScopeItem).where(ScopeItem.project_id == project_id).order_by(ScopeItem.sort_order,
                                                                                                    ScopeItem.id))
    links = session.scalars(
        select(WbsScopeLink).where(WbsScopeLink.element_id.in_(element_ids)).order_by(WbsScopeLink.id)
    ) if element_ids else []
    entries = session.scalars(
        select(WbsDictionaryEntry).where(WbsDictionaryEntry.element_id.in_(element_ids))
    ) if element_ids else []
    nodes = [_node(e) for e in context.elements]
    tolerance = _tolerance(context.project)
    findings = check_hundred_percent(
        nodes,
        context.activities,
        [ScopeItemRef(id=item.id, kind=ScopeKind(item.kind)) for item in items],
        [ScopeLinkRef(element_id=link.element_id, scope_item_id=link.scope_item_id) for link in links],
        tolerance,
    )
    missing = find_missing_completion(
        nodes,
        [DictionaryRef(element_id=e.element_id, deliverable=e.deliverable, completion_criteria=e.completion_criteria)
         for e in entries],
    )
    return ValidationResult(tolerance=tolerance, findings=findings, missing=missing)


def get_validation(
    session: Session, access: ProjectAccess, actor: User, activities: ActivitiesReader, project_id: int
) -> tuple[WbsContext, ValidationResult, frozenset[int] | None]:
    """100% ルールと完了条件の検査の結果（project.view）。

    委託先には見える要素・活動だけで検査し（子の集計も見えるものだけ。9.2 の注記）、見える要素の結果だけを返す。
    返す文脈も見える要素・活動だけに絞る（木の集計値に使う）。
    """
    table = authorize_project(session, access, actor, project_id, "project.view")
    context = load_wbs_context(session, project_id, activities)
    scope = _visible_scope(session, access, actor, context, table)
    visible = None if scope is None else scope.element_ids
    if scope is not None:
        context = _visible_context(context, scope)
    result = validate(session, context)
    if visible is not None:
        result = ValidationResult(
            tolerance=result.tolerance,
            findings=tuple(f for f in result.findings if f.element_id is not None and f.element_id in visible),
            missing=tuple(m for m in result.missing if m.element_id in visible),
        )
    return context, result, visible


# ---------------------------------------------------------------------------
# 木（API-WBS-001）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TreeView:
    context: WbsContext
    rows: list[tuple[WbsElement, int]]
    rollup: EstimateRollup
    validation: ValidationResult
    restricted: bool


def get_tree(
    session: Session, access: ProjectAccess, actor: User, activities: ActivitiesReader, project_id: int
) -> TreeView:
    """WBS の木（先行順）と集計値・改番待ち・検査の件数（project.view）。

    委託先には見える要素の行だけを返し、集計値・件数も見える要素・活動だけから求める（9.2 の注記）。
    """
    context, validation, visible = get_validation(session, access, actor, activities, project_id)
    rows = preorder_elements(context.elements)
    if visible is not None:
        rows = [(element, depth) for element, depth in rows if element.id in visible]
    return TreeView(context=context, rows=rows, rollup=_rollup(context), validation=validation,
                    restricted=visible is not None)


def element_summary(session: Session, activities: ActivitiesReader, element: WbsElement) -> tuple[int, Decimal, str]:
    """要素 1 件の深さ（最上位が 1）・集計値・集計の元（書いた後の応答に使う。木と同じ求め方）。"""
    context = load_wbs_context(session, element.project_id, activities)
    depth = {e.id: d for e, d in preorder_elements(context.elements)}[element.id]
    result = _rollup(context).elements[element.id]
    return depth, result.hours, result.source.value


# ---------------------------------------------------------------------------
# 要素（API-WBS-002〜005）
# ---------------------------------------------------------------------------


def _editing(
    session: Session, access: ProjectAccess, actor: User, activities: ActivitiesReader, project_id: int
) -> WbsContext:
    authorize_project(session, access, actor, project_id, "wbs.edit")
    # WBS の状態（RULE-WBS-006 の EDIT）と親の数えはプロジェクトの行をロックした後の値で判定・採番する（A-49 の 3）
    return load_wbs_context(session, project_id, activities, for_update=True)


def add_element(
    session: Session,
    access: ProjectAccess,
    actor: User,
    activities: ActivitiesReader,
    project_id: int,
    *,
    parent_id: int | None,
    kind: str,
    name: str,
    own_estimate_hours: Decimal | None = None,
    responsible_user_id: int | None = None,
    responsible_team: str | None = None,
) -> WbsElement:
    """要素を足す（wbs.edit）。順番と WBS ID は RULE-WBS-002 の新規（親の last_child_seq + 1）と RULE-WBS-001 で決める。

    親がこのプロジェクトに無ければ 400、WP の下に足すのは 422 ``WORK_PACKAGE_HAS_CHILDREN``（RULE-WBS-003）。表示順は兄弟の末尾。
    """
    context = _editing(session, access, actor, activities, project_id)
    parent = None
    if parent_id is not None:
        parent = context.by_id.get(parent_id)
        if parent is None:
            raise _validation_error([{"loc": ["body", "parent_id"], "message": "このプロジェクトの WBS 要素ではありません"}])
    _check_responsible(session, project_id, responsible_user_id, ["body", "responsible_user_id"])
    new_id = -1  # 仮の ID（木の検査だけに使う）
    _ensure_tree([*(_node(e) for e in context.elements),
                  WbsNode(id=new_id, parent_id=parent_id, kind=kind, own_estimate_hours=own_estimate_hours)],  # type: ignore[arg-type]
                 context.activities)
    try:
        code = new_element_code(
            None if parent is None else coded_element(parent),
            context.rule,
            top_last_child_seq=context.rule_row.top_last_child_seq if parent is None else None,
        )
    except WbsCodeError as exc:
        raise AppError(422, "WBS_CODE_INVALID", str(exc)) from exc
    siblings = [e for e in context.elements if e.parent_id == parent_id]
    element = WbsElement(
        project_id=project_id,
        parent_id=parent_id,
        kind=kind,
        name=name,
        wbs_code=code.wbs_code,
        sibling_seq=code.sibling_seq,
        last_child_seq=0,
        sort_order=max((e.sort_order for e in siblings), default=0) + 1,
        own_estimate_hours=own_estimate_hours,
        responsible_user_id=responsible_user_id,
        responsible_team=responsible_team,
        needs_renumber=code.needs_renumber,
    )
    if parent is None:
        context.rule_row.top_last_child_seq = code.parent_last_child_seq
    else:
        parent.last_child_seq = code.parent_last_child_seq
    mark_wbs_edited(context.project)
    session.add(element)
    session.commit()
    return element


def update_element(
    session: Session,
    access: ProjectAccess,
    actor: User,
    activities: ActivitiesReader,
    project_id: int,
    element_id: int,
    changes: Mapping[str, Any],
) -> WbsElement:
    """名称・区分・責任者・担当チーム・自分の見積を変える（wbs.edit）。載せた列だけを変える。WBS ID は変えない。

    名称・区分の null は 400。区分を変えた後の木が RULE-WBS-003 に合わなければ 422（WP に子がある・活動のある要素を WP 以外にする）。
    """
    context = _editing(session, access, actor, activities, project_id)
    element = _element_of(context, element_id)
    unknown = set(changes) - UPDATABLE_FIELDS
    if unknown:
        raise ValueError(f"変えられない列: {sorted(unknown)}")
    nulls = [name for name in sorted(_NON_NULLABLE_FIELDS & set(changes)) if changes[name] is None]
    if nulls:
        raise _validation_error([{"loc": ["body", name], "message": "空にできません"} for name in nulls])
    if "responsible_user_id" in changes and changes["responsible_user_id"] != element.responsible_user_id:
        _check_responsible(session, project_id, changes["responsible_user_id"], ["body", "responsible_user_id"])
    if "kind" in changes:
        _ensure_tree([_node(e, kind=changes["kind"]) if e.id == element_id else _node(e) for e in context.elements],
                     context.activities)
    for name, value in changes.items():
        setattr(element, name, value)
    mark_wbs_edited(context.project)
    session.commit()
    return element


def move(
    session: Session,
    access: ProjectAccess,
    actor: User,
    activities: ActivitiesReader,
    project_id: int,
    element_id: int,
    *,
    parent_id: int | None,
    position: int | None,
) -> WbsElement:
    """要素を移動する（wbs.edit）。WBS ID は変えない（RULE-WBS-002）。形の合わない要素と配下を改番待ちにする。

    移動先が無ければ 400、自分か配下の下へは 422 ``PARENT_CYCLE``、WP の下へは 422 ``WORK_PACKAGE_HAS_CHILDREN``。
    表示順は移動先の兄弟の ``position`` 番目（0 から。省けば末尾）に入れ、兄弟の表示順を 1 から振り直す。
    """
    context = _editing(session, access, actor, activities, project_id)
    element = _element_of(context, element_id)
    by_id = context.by_id
    if parent_id is not None and parent_id not in by_id:
        raise _validation_error([{"loc": ["body", "parent_id"], "message": "このプロジェクトの WBS 要素ではありません"}])
    if parent_id is not None and parent_id in subtree_ids([coded_element(e) for e in context.elements], element_id):
        raise AppError(422, "PARENT_CYCLE", "要素を自分か配下の下へは移せません",
                       details=[{"element_id": element_id, "parent_id": parent_id}])
    _ensure_tree([_node(e, parent_id=parent_id) if e.id == element_id else _node(e) for e in context.elements],
                 context.activities)
    try:
        result = move_element(
            [coded_element(e) for e in context.elements],
            element_id,
            parent_id,
            context.rule,
            top_last_child_seq=context.rule_row.top_last_child_seq
            if parent_id is None and element.parent_id is not None
            else None,
        )
    except WbsCodeError as exc:
        raise AppError(422, "WBS_CODE_INVALID", str(exc)) from exc

    if result.parent_last_child_seq is not None:
        if parent_id is None:
            context.rule_row.top_last_child_seq = result.parent_last_child_seq
        else:
            by_id[parent_id].last_child_seq = result.parent_last_child_seq
    element.parent_id = parent_id
    element.sibling_seq = result.sibling_seq
    for node_id, flag in result.needs_renumber.items():
        by_id[node_id].needs_renumber = flag
    siblings = sorted((e for e in context.elements if e.parent_id == parent_id and e.id != element_id),
                      key=lambda e: (e.sort_order, e.sibling_seq, e.id))
    index = len(siblings) if position is None else min(position, len(siblings))
    siblings.insert(index, element)
    for order, sibling in enumerate(siblings, start=1):
        sibling.sort_order = order
    mark_wbs_edited(context.project)
    session.commit()
    return element


def remove_element(
    session: Session, access: ProjectAccess, actor: User, activities: ActivitiesReader, project_id: int, element_id: int
) -> None:
    """要素を配下ごと消す（wbs.edit）。他の要素の WBS ID・順番は変えない（RULE-WBS-002）。

    - 配下の辞書・スコープの紐付け・新旧対応を消し、指摘は ``element_id`` を空にして残す（A-15）
    - ベースラインの版の写しは変えない（写しの列は外部キーを張らない写しの値。設計書 1.1 の 4.2。CR-002）
    - 配下の WP に活動があれば 422 ``ELEMENT_HAS_ACTIVITIES``（活動の削除は SCH の群の担当。OQ-18）
    - 是正措置・差異の理由が指す要素を含むなら 422 ``ELEMENT_REFERENCED``（変更要求の反映と同じ。外部キー違反の 500 にしない。A-47）
    """
    context = _editing(session, access, actor, activities, project_id)
    _element_of(context, element_id)
    ids = list(subtree_ids([coded_element(e) for e in context.elements], element_id))
    with_activities = sorted({a.work_package_id for a in context.activities if a.work_package_id in set(ids)})
    if with_activities:
        raise AppError(422, "ELEMENT_HAS_ACTIVITIES", "活動のある WP は消せません（先に活動を消します）",
                       details=[{"element_ids": with_activities}])
    referenced = sorted(
        {i for i in session.scalars(select(CorrectiveAction.element_id).where(CorrectiveAction.element_id.in_(ids)))
         if i is not None}
        | {i for i in session.scalars(select(VarianceNote.element_id).where(VarianceNote.element_id.in_(ids)))
           if i is not None}
    )
    if referenced:
        raise AppError(422, "ELEMENT_REFERENCED", "是正措置・差異の理由が指す要素は消せません",
                       details=[{"element_ids": referenced}])
    session.execute(update(WbsReviewComment).where(WbsReviewComment.element_id.in_(ids)).values(element_id=None))
    session.execute(delete(WbsScopeLink).where(WbsScopeLink.element_id.in_(ids)))
    session.execute(delete(WbsDictionaryEntry).where(WbsDictionaryEntry.element_id.in_(ids)))
    session.execute(delete(WbsCodeHistory).where(WbsCodeHistory.element_id.in_(ids)))
    for node_id in reversed(ids):  # 子から消す（親子の外部キー）
        session.execute(delete(WbsElement).where(WbsElement.id == node_id))
    mark_wbs_edited(context.project)
    session.commit()


# ---------------------------------------------------------------------------
# 採番規則・改番・新旧対応（API-WBS-006〜008）
# ---------------------------------------------------------------------------


def replace_numbering_rule(
    session: Session,
    access: ProjectAccess,
    actor: User,
    project_id: int,
    *,
    separator: str,
    min_digits: int,
    prefix: str,
    pad_first_level: bool,
) -> WbsNumberingRule:
    """採番規則を変える（wbs.edit）。今の要素の ID は変えない（新しい規則は次の採番と改番から使う。WBS-05）。

    RULE-WBS-001 が受けない規則（空・数字を含む区切り文字）と、``/`` を含む区切り文字・接頭辞（WBS ID は API-WBS-023 のパスに入る）は 400。
    """
    authorize_project(session, access, actor, project_id, "wbs.edit")
    errors: list[dict[str, Any]] = []
    try:
        NumberingRule(separator=separator, min_digits=min_digits, prefix=prefix, pad_first_level=pad_first_level)
    except WbsCodeError as exc:
        errors.append({"loc": ["body"], "message": str(exc)})
    for name, value in (("separator", separator), ("prefix", prefix)):
        if "/" in value:
            errors.append({"loc": ["body", name], "message": "「/」は使えません（WBS ID を URL のパスに入れるため）"})
    if errors:
        raise _validation_error(errors)
    row = numbering_rule_row(session, project_id, for_update=True)
    row.separator, row.min_digits, row.prefix, row.pad_first_level = separator, min_digits, prefix, pad_first_level
    session.commit()
    return row


@dataclass(frozen=True)
class RenumberView:
    renumbered_at: datetime | None
    changes: list[tuple[int, str, str]]


def renumber_wbs(
    session: Session,
    access: ProjectAccess,
    actor: User,
    activities: ActivitiesReader,
    project_id: int,
    *,
    now: datetime,
) -> RenumberView:
    """改番する（wbs.edit）。兄弟の表示順に 1 から振り直し、今の採番規則で全要素の ID を作り直す（RULE-WBS-002）。

    変わった要素ごとに ``wbs_code_history`` へ新旧を残し、改番待ちを全て解く。何も変わらなければ書かない（状態も動かさない）。
    """
    context = _editing(session, access, actor, activities, project_id)
    try:
        result = renumber([coded_element(e) for e in context.elements], context.rule,
                          top_last_child_seq=context.rule_row.top_last_child_seq)
    except WbsCodeError as exc:
        raise AppError(422, "WBS_CODE_INVALID", str(exc)) from exc
    pending = any(e.needs_renumber for e in context.elements)
    reseq = any(result.sibling_seqs[e.id] != e.sibling_seq for e in context.elements)
    if not result.changes and not pending and not reseq:
        return RenumberView(renumbered_at=None, changes=[])
    context.rule_row.top_last_child_seq = result.top_last_child_seq
    for element in context.elements:
        element.wbs_code = result.codes[element.id]
        element.sibling_seq = result.sibling_seqs[element.id]
        element.last_child_seq = result.last_child_seqs[element.id]
        element.needs_renumber = False
    for change in result.changes:
        session.add(WbsCodeHistory(project_id=project_id, element_id=change.element_id, old_code=change.old_code,
                                   new_code=change.new_code, renumbered_at=now, renumbered_by=actor.id))
    mark_wbs_edited(context.project)
    session.commit()
    return RenumberView(renumbered_at=now, changes=[(c.element_id, c.old_code, c.new_code) for c in result.changes])


def list_code_history(
    session: Session,
    access: ProjectAccess,
    actor: User,
    activities: ActivitiesReader,
    project_id: int,
    *,
    limit: int,
    offset: int,
) -> tuple[list[tuple[WbsCodeHistory, str]], int]:
    """WBS ID の新旧対応（新しい順）と総数（project.view）。委託先には見える要素の対応だけを返す。"""
    table = authorize_project(session, access, actor, project_id, "project.view")
    context = load_wbs_context(session, project_id, activities)
    visible = _visible_element_ids(session, access, actor, context, table)
    condition = [WbsCodeHistory.project_id == project_id]
    if visible is not None:
        condition.append(WbsCodeHistory.element_id.in_(sorted(visible)))
    total = session.scalar(select(func.count()).select_from(WbsCodeHistory).where(*condition)) or 0
    rows = session.scalars(
        select(WbsCodeHistory).where(*condition)
        .order_by(WbsCodeHistory.renumbered_at.desc(), WbsCodeHistory.id.desc()).limit(limit).offset(offset)
    )
    codes = {e.id: e.wbs_code for e in context.elements}
    return [(row, codes[row.element_id]) for row in rows], total


# ---------------------------------------------------------------------------
# 辞書・スコープの紐付け（API-WBS-009・010・013）
# ---------------------------------------------------------------------------


def get_dictionary(
    session: Session, access: ProjectAccess, actor: User, activities: ActivitiesReader, project_id: int, element_id: int
) -> tuple[WbsElement, WbsDictionaryEntry | None]:
    """要素の WBS 辞書（project.view）。辞書の行が無ければ 4 項目は空。委託先に見えない要素は 404。"""
    table = authorize_project(session, access, actor, project_id, "project.view")
    context = load_wbs_context(session, project_id, activities)
    element = _element_of(context, element_id)
    visible = _visible_element_ids(session, access, actor, context, table)
    if visible is not None and element_id not in visible:
        raise _element_not_found(element_id)
    entry = session.scalar(select(WbsDictionaryEntry).where(WbsDictionaryEntry.element_id == element_id))
    return element, entry


def put_dictionary(
    session: Session,
    access: ProjectAccess,
    actor: User,
    activities: ActivitiesReader,
    project_id: int,
    element_id: int,
    values: Mapping[str, str | None],
) -> tuple[WbsElement, WbsDictionaryEntry]:
    """WBS 辞書を書く（wbs.edit）。4 項目を載せた値で置き換える。"""
    context = _editing(session, access, actor, activities, project_id)
    element = _element_of(context, element_id)
    entry = session.scalar(select(WbsDictionaryEntry).where(WbsDictionaryEntry.element_id == element_id))
    if entry is None:
        entry = WbsDictionaryEntry(element_id=element_id)
        session.add(entry)
    for name in DICTIONARY_FIELDS:
        setattr(entry, name, values.get(name))
    mark_wbs_edited(context.project)
    session.commit()
    return element, entry


def get_scope_links(
    session: Session, access: ProjectAccess, actor: User, activities: ActivitiesReader, project_id: int, element_id: int
) -> list[int]:
    """要素とスコープ項目の紐付け（project.view。項目の ID の順）。委託先に見えない要素は 404（API-WBS-026。設計書 1.1・CR-004）。"""
    table = authorize_project(session, access, actor, project_id, "project.view")
    context = load_wbs_context(session, project_id, activities)
    _element_of(context, element_id)
    visible = _visible_element_ids(session, access, actor, context, table)
    if visible is not None and element_id not in visible:
        raise _element_not_found(element_id)
    return list(session.scalars(
        select(WbsScopeLink.scope_item_id).where(WbsScopeLink.element_id == element_id).order_by(WbsScopeLink.scope_item_id)
    ))


def replace_scope_links(
    session: Session,
    access: ProjectAccess,
    actor: User,
    activities: ActivitiesReader,
    project_id: int,
    element_id: int,
    scope_item_ids: Sequence[int],
) -> list[int]:
    """要素とスコープ項目の紐付けを置き換える（wbs.edit）。他のプロジェクトの・無い項目、同じ項目の重複は 400。"""
    context = _editing(session, access, actor, activities, project_id)
    _element_of(context, element_id)
    own_items = set(session.scalars(select(ScopeItem.id).where(ScopeItem.project_id == project_id)))
    errors: list[dict[str, Any]] = []
    seen: set[int] = set()
    for index, item_id in enumerate(scope_item_ids):
        if item_id in seen:
            errors.append({"loc": ["body", "scope_item_ids", index], "message": "同じ項目が 2 回書かれています"})
        elif item_id not in own_items:
            errors.append({"loc": ["body", "scope_item_ids", index], "message": "このプロジェクトのスコープ項目ではありません"})
        seen.add(item_id)
    if errors:
        raise _validation_error(errors)
    session.execute(delete(WbsScopeLink).where(WbsScopeLink.element_id == element_id))
    for item_id in scope_item_ids:
        session.add(WbsScopeLink(element_id=element_id, scope_item_id=item_id))
    mark_wbs_edited(context.project)
    session.commit()
    return list(scope_item_ids)


# ---------------------------------------------------------------------------
# レビュー（API-WBS-014〜017）
# ---------------------------------------------------------------------------


def _review_of(session: Session, project_id: int, review_id: int, *, for_update: bool = False) -> WbsReview:
    review = lock_row(session, WbsReview, review_id) if for_update else session.get(WbsReview, review_id)
    if review is None or review.project_id != project_id:
        raise AppError(404, "NOT_FOUND", "レビューが見つかりません", details=[{"review_id": review_id}])
    return review


def _ensure_review_open(review: WbsReview) -> None:
    if review.status != REVIEW_STATUS_INITIAL:
        raise AppError(409, "INVALID_STATE", "閉じたレビューの指摘は書けません", details=[{"review_id": review.id}])


def start_review(
    session: Session, access: ProjectAccess, actor: User, project_id: int, reviewer_ids: Sequence[int], *, now: datetime
) -> WbsReview:
    """レビューを始める（wbs.edit）。WBS は DRAFT → IN_REVIEW（RULE-WBS-006。他の状態は 409 ``INVALID_STATE``）。

    レビューを頼む人はメンバー（無い・メンバーでない・重複は 400）。
    """
    authorize_project(session, access, actor, project_id, "wbs.edit")
    members = set(session.scalars(select(ProjectMember.user_id).where(ProjectMember.project_id == project_id)))
    errors = [
        {"loc": ["body", "reviewer_ids", index], "message": "メンバーでないか、2 回書かれています"}
        for index, user_id in enumerate(reviewer_ids)
        if user_id not in members or list(reviewer_ids).index(user_id) != index
    ]
    if errors:
        raise _validation_error(errors)
    project = lock_row(session, Project, project_id)  # 状態はロックして読み直した値で判定する（A-49 の 3）
    assert project is not None
    try:
        transition = transition_wbs(project.wbs_status, WbsAction.START_REVIEW)
    except WorkflowError as exc:
        raise AppError(409, exc.code.value, f"WBS が {project.wbs_status} のときはレビューを始められません",
                       details=[{"wbs_status": project.wbs_status}]) from exc
    project.wbs_status = transition.to_status.value
    review = WbsReview(project_id=project_id, requested_by=actor.id, requested_at=now, reviewer_ids=list(reviewer_ids))
    session.add(review)
    session.commit()
    return review


def list_reviews(
    session: Session, access: ProjectAccess, actor: User, project_id: int, *, limit: int, offset: int
) -> tuple[list[WbsReview], int]:
    """レビューの一覧（project.view。新しい順＝ID の降順。API-WBS-025。設計書 1.1・CR-004）。指摘は API-WBS-015 で読む。"""
    authorize_project(session, access, actor, project_id, "project.view")
    total = session.scalar(select(func.count()).select_from(WbsReview).where(WbsReview.project_id == project_id))
    reviews = list(session.scalars(
        select(WbsReview).where(WbsReview.project_id == project_id).order_by(WbsReview.id.desc()).limit(limit).offset(offset)
    ))
    return reviews, int(total or 0)


def close_review(
    session: Session, access: ProjectAccess, actor: User, project_id: int, review_id: int, *, now: datetime
) -> WbsReview:
    """レビューを閉じる（wbs.edit。5.10 の IN_REVIEW → DRAFT。RULE-WBS-006 の CLOSE_REVIEW。API-WBS-024。設計書 1.1・CR-004）。

    OPEN のレビューを CLOSED にし（``closed_at``）、WBS を DRAFT に戻す。指摘はそのまま残る。
    閉じたレビュー、WBS が IN_REVIEW でないときは 409 ``INVALID_STATE``。無いレビューは 404。
    """
    authorize_project(session, access, actor, project_id, "wbs.edit")
    # プロジェクトの行を先にロックし、レビューと WBS の状態はその後に読み直した値で判定する（A-49 の 3）
    project = lock_row(session, Project, project_id)
    assert project is not None
    review = _review_of(session, project_id, review_id, for_update=True)
    if review.status != REVIEW_STATUS_INITIAL:
        raise AppError(409, "INVALID_STATE", "閉じたレビューは閉じられません",
                       details=[{"review_id": review.id, "status": review.status}])
    try:
        transition = transition_wbs(project.wbs_status, WbsAction.CLOSE_REVIEW)
    except WorkflowError as exc:
        raise AppError(409, exc.code.value, f"WBS が {project.wbs_status} のときはレビューを閉じられません",
                       details=[{"wbs_status": project.wbs_status}]) from exc
    review.status = REVIEW_STATUS_CLOSED
    review.closed_at = now
    project.wbs_status = transition.to_status.value
    session.commit()
    return review


def get_review(
    session: Session, access: ProjectAccess, actor: User, activities: ActivitiesReader, project_id: int, review_id: int
) -> tuple[WbsReview, list[WbsReviewComment]]:
    """レビューと指摘（ID の順。project.view）。委託先には見える要素への指摘だけを返す。"""
    table = authorize_project(session, access, actor, project_id, "project.view")
    review = _review_of(session, project_id, review_id)
    comments = list(session.scalars(
        select(WbsReviewComment).where(WbsReviewComment.review_id == review_id).order_by(WbsReviewComment.id)
    ))
    visible = _visible_element_ids(session, access, actor, load_wbs_context(session, project_id, activities), table)
    if visible is not None:
        comments = [c for c in comments if c.element_id is not None and c.element_id in visible]
    return review, comments


def add_comment(
    session: Session,
    access: ProjectAccess,
    actor: User,
    project_id: int,
    review_id: int,
    *,
    element_id: int | None,
    category: str,
    body: str,
) -> WbsReviewComment:
    """指摘を書く（wbs.review）。閉じたレビューは 409、このプロジェクトに無い要素は 400。書いた人が ``author_id``。"""
    authorize_project(session, access, actor, project_id, "wbs.review")
    # レビューを閉じる・申請（開いているレビューを閉じる）と競わせない。開いているかはロックの後に読み直して判定する（A-49 の 3）
    lock_row(session, Project, project_id)
    review = _review_of(session, project_id, review_id, for_update=True)
    _ensure_review_open(review)
    if element_id is not None:
        element = session.get(WbsElement, element_id)
        if element is None or element.project_id != project_id:
            raise _validation_error([{"loc": ["body", "element_id"], "message": "このプロジェクトの WBS 要素ではありません"}])
    comment = WbsReviewComment(review_id=review_id, element_id=element_id, category=category, body=body,
                               author_id=actor.id)
    session.add(comment)
    session.commit()
    return comment


def update_comment(
    session: Session, access: ProjectAccess, actor: User, project_id: int, review_id: int, comment_id: int,
    changes: Mapping[str, Any],
) -> WbsReviewComment:
    """指摘の状態・対応を変える（wbs.edit）。載せた項目だけ。状態の null は 400。

    閉じたレビューの指摘も変えられる：閉じても指摘はそのまま残り（5.10。API-WBS-024）、申請の前提の「未対応の指摘」に数えるので、
    閉じた後に対応できないと申請が塞がる（設計書 1.1・CR-004。A-48 (7)）。新しい指摘を書くのは開いているレビューだけ。
    """
    authorize_project(session, access, actor, project_id, "wbs.edit")
    # 指摘の状態は申請の前提（未対応の指摘）に数えるので、申請と競わせない（A-49 の 3）
    lock_row(session, Project, project_id)
    review = _review_of(session, project_id, review_id)
    comment = session.get(WbsReviewComment, comment_id)
    if comment is None or comment.review_id != review_id:
        raise AppError(404, "NOT_FOUND", "指摘が見つかりません", details=[{"comment_id": comment_id}])
    if "status" in changes and changes["status"] is None:
        raise _validation_error([{"loc": ["body", "status"], "message": "空にできません"}])
    for name in ("status", "resolution"):
        if name in changes:
            setattr(comment, name, changes[name])
    session.commit()
    return comment


# ---------------------------------------------------------------------------
# スコープベースライン（API-WBS-021・022）
# ---------------------------------------------------------------------------


def list_baselines(
    session: Session,
    access: ProjectAccess,
    actor: User,
    activities: ActivitiesReader,
    project_id: int,
    *,
    limit: int,
    offset: int,
) -> tuple[list[tuple[ScopeBaseline, int]], int]:
    """スコープベースラインの版（版の順）と、版ごとの要素の数（project.view）。

    委託先には今見える要素の写しだけを数える（API-WBS-022 で返す写しと同じ範囲。9.2 の注記）。
    """
    table = authorize_project(session, access, actor, project_id, "project.view")
    visible = _visible_element_ids(session, access, actor, load_wbs_context(session, project_id, activities), table)
    total = session.scalar(select(func.count()).select_from(ScopeBaseline).where(ScopeBaseline.project_id == project_id))
    counted = select(ScopeBaselineElement.scope_baseline_id, func.count().label("n"))
    if visible is not None:
        counted = counted.where(ScopeBaselineElement.element_id.in_(visible))
    counts = counted.group_by(ScopeBaselineElement.scope_baseline_id).subquery()
    rows = session.execute(
        select(ScopeBaseline, func.coalesce(counts.c.n, 0))
        .outerjoin(counts, counts.c.scope_baseline_id == ScopeBaseline.id)
        .where(ScopeBaseline.project_id == project_id)
        .order_by(ScopeBaseline.version).limit(limit).offset(offset)
    ).all()
    return [(baseline, int(n)) for baseline, n in rows], int(total or 0)


def get_baseline(
    session: Session, access: ProjectAccess, actor: User, activities: ActivitiesReader, project_id: int, baseline_id: int
) -> tuple[ScopeBaseline, list[ScopeBaselineElement], bool]:
    """スコープベースラインの 1 版（写した要素と辞書。project.view）。委託先には今見える要素の写しだけを返す。"""
    table = authorize_project(session, access, actor, project_id, "project.view")
    baseline = session.get(ScopeBaseline, baseline_id)
    if baseline is None or baseline.project_id != project_id:
        raise AppError(404, "NOT_FOUND", "スコープベースラインが見つかりません", details=[{"baseline_id": baseline_id}])
    elements = list(session.scalars(
        select(ScopeBaselineElement).where(ScopeBaselineElement.scope_baseline_id == baseline_id)
        .order_by(ScopeBaselineElement.id)
    ))
    visible = _visible_element_ids(session, access, actor, load_wbs_context(session, project_id, activities), table)
    if visible is not None:
        elements = [e for e in elements if e.element_id in visible]
    return baseline, elements, visible is not None


# ---------------------------------------------------------------------------
# 責任分担表（API-WBS-011）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MatrixView:
    """責任分担表（RULE-WBS-007）と、行の要素・列の利用者。"""

    matrix: ResponsibilityMatrix
    elements: Mapping[int, WbsElement]
    users: Mapping[int, User]
    restricted: bool


def responsibility_matrix(
    session: Session,
    access: ProjectAccess,
    actor: User,
    activities: ActivitiesReader,
    readers: CrossGroupReaders,
    project_id: int,
) -> MatrixView:
    """WP × メンバーの責任分担表と責任者の無い WP（project.view。RULE-WBS-007）。

    行は WP を木の先行順（兄弟は表示順）、列はメンバー（加えた順）。委託先には見える WP の行だけを返し、
    担当・割当工数・列も見える要素・活動とその割当だけから求める（9.2 の注記）。
    """
    table = authorize_project(session, access, actor, project_id, "project.view")
    context = load_wbs_context(session, project_id, activities)
    scope = _visible_scope(session, access, actor, context, table)
    visible = None if scope is None else scope.element_ids
    assignments = readers.assignments(session, project_id)
    if scope is not None:
        context = _visible_context(context, scope)
        assignments = [a for a in assignments if a.activity_id in scope.activity_ids]
    member_ids = list(session.scalars(
        select(ProjectMember.user_id).where(ProjectMember.project_id == project_id).order_by(ProjectMember.id)
    ))
    ordered = [element for element, _ in preorder_elements(context.elements)]
    try:
        matrix = build_responsibility_matrix(
            [
                ResponsibilityElement(id=e.id, kind=e.kind, responsible_user_id=e.responsible_user_id,  # type: ignore[arg-type]
                                      responsible_team=e.responsible_team)
                for e in ordered
            ],
            [ActivityRef(id=a.id, work_package_id=a.work_package_id) for a in context.activities],
            assignments,
            member_ids,
        )
    except WbsRuleError as exc:  # 保存した木・割当が壊れている。推し量らずに止める
        raise RuntimeError(f"プロジェクト {project_id} の責任分担表を組み立てられない: {exc}") from exc
    if visible is not None:
        matrix = replace(
            matrix,
            rows=tuple(row for row in matrix.rows if row.element_id in visible),
            without_responsible=tuple(element_id for element_id in matrix.without_responsible if element_id in visible),
        )
    users = {
        user.id: user for user in session.scalars(select(User).where(User.id.in_(matrix.user_ids)))
    } if matrix.user_ids else {}
    return MatrixView(matrix=matrix, elements=context.by_id, users=users, restricted=visible is not None)


# ---------------------------------------------------------------------------
# 横断照会（API-WBS-023）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TraceView:
    """WBS ID による横断照会（RULE-WBS-008）と、照会の範囲の要素（先行順）。"""

    trace: WbsTrace
    elements: list[WbsElement]
    restricted: bool


def _resolves_into(code: str, elements: Sequence[CodedElement], history: Sequence[CodeHistoryEntry],
                   element_ids: frozenset[int]) -> bool:
    try:
        hit = resolve_code(code, elements, history)
    except WbsCodeError:
        return False
    return hit is not None and hit.element_id in element_ids


def trace_code(
    session: Session,
    access: ProjectAccess,
    actor: User,
    activities: ActivitiesReader,
    readers: CrossGroupReaders,
    project_id: int,
    wbs_code: str,
) -> TraceView:
    """1 つの WBS ID（旧 ID も可）から配下の要素・活動・見積・実績・EAC・変更要求・是正措置を集める（project.view。RULE-WBS-008）。

    - 解決できない ID は 404。今の ID を 2 つ以上の要素が持つなど決められなければ 422 ``WBS_CODE_INVALID``
    - 委託先は、見える要素へ解決した ID だけを照会できる（見えなければ 404）。活動は自分の割当、見積・是正措置・変更要求は
      見える要素か自分の割当の活動に当たるものだけ。EAC は配下の見えない分を含むので返さない
    """
    table = authorize_project(session, access, actor, project_id, "project.view")
    context = load_wbs_context(session, project_id, activities)
    visible = _visible_element_ids(session, access, actor, context, table)
    coded = [coded_element(e) for e in context.elements]
    history = [
        CodeHistoryEntry(id=row.id, element_id=row.element_id, old_code=row.old_code, new_code=row.new_code,
                         renumbered_at=row.renumbered_at)
        for row in session.scalars(select(WbsCodeHistory).where(WbsCodeHistory.project_id == project_id))
    ]
    activity_refs = [ActivityRef(id=a.id, work_package_id=a.work_package_id) for a in context.activities]
    records = list(readers.estimate_records(session, project_id))
    entries = list(readers.time_entries(session, project_id))
    changes = list(readers.change_requests(session, project_id))
    actions = list(readers.corrective_actions(session, project_id))
    eac = None if visible is not None else readers.eac_hours(session, project_id)
    if visible is not None:
        membership = access.membership(session, project_id, actor.id)
        assigned = membership.assigned_activity_ids if membership is not None else frozenset()
        activity_refs = [a for a in activity_refs if a.id in assigned]
        records = [r for r in records
                   if r.target_id in (visible if r.target_type == EstimateTarget.ELEMENT else assigned)]
        actions = [a for a in actions
                   if (a.element_id is not None and a.element_id in visible)
                   or (a.activity_id is not None and a.activity_id in assigned)]
        changes = [c for c in changes if any(_resolves_into(code, coded, history, visible) for code in c.target_codes)]
    try:
        trace = trace_wbs_code(
            wbs_code, elements=coded, history=history, activities=activity_refs, estimate_records=records,
            time_entries=entries, change_requests=changes, corrective_actions=actions, eac_hours_by_element=eac,
        )
    except WbsCodeError as exc:
        raise AppError(422, "WBS_CODE_INVALID", str(exc), details=[{"wbs_code": wbs_code}]) from exc
    except WbsRuleError as exc:  # 別の群の表から読んだ値が壊れている。推し量らずに止める
        raise RuntimeError(f"プロジェクト {project_id} の WBS ID {wbs_code!r} を照会できない: {exc}") from exc
    if trace is None or (visible is not None and trace.resolution.element_id not in visible):
        raise AppError(404, "NOT_FOUND", "WBS ID が見つかりません", details=[{"wbs_code": wbs_code}])
    if visible is not None:
        trace = replace(trace, element_ids=tuple(i for i in trace.element_ids if i in visible))
    in_scope = set(trace.element_ids)
    return TraceView(
        trace=trace,
        elements=[element for element, _ in preorder_elements(context.elements) if element.id in in_scope],
        restricted=visible is not None,
    )


# ---------------------------------------------------------------------------
# 承認の申請・判断・承認待ちの一覧（API-WBS-018〜020）
# ---------------------------------------------------------------------------

_WORKFLOW_STATUS = {
    WorkflowErrorCode.INVALID_STATE: 409,
    WorkflowErrorCode.WBS_NOT_READY: 422,
    WorkflowErrorCode.SELF_APPROVAL_FORBIDDEN: 403,  # 9.2 の注記：権限を持っていても自分の申請は判断できない
}

TARGET_WBS = "WBS"
TARGET_CHANGE_REQUEST = "CHANGE_REQUEST"
_DECIDE_PERMISSION = {TARGET_WBS: "wbs.approve", TARGET_CHANGE_REQUEST: "change.approve"}


def _workflow_error(exc: WorkflowError, message: str, details: list[dict[str, Any]]) -> AppError:
    return AppError(_WORKFLOW_STATUS[exc.code], exc.code.value, message, details=details)


def readiness_of(session: Session, context: WbsContext) -> WbsReadiness:
    """承認申請の前提の件数（5.10）：完了条件の欠け（RULE-WBS-005）・未対応の指摘・100% ルールの ERROR（RULE-WBS-004）。"""
    result = validate(session, context)
    open_comments = session.scalar(
        select(func.count()).select_from(WbsReviewComment)
        .join(WbsReview, WbsReview.id == WbsReviewComment.review_id)
        .where(WbsReview.project_id == context.project.id, WbsReviewComment.status == COMMENT_STATUS_INITIAL)
    ) or 0
    return WbsReadiness(missing_completion=len(result.missing), open_comments=int(open_comments),
                        hundred_percent_errors=result.errors)


def submit_wbs(
    session: Session,
    access: ProjectAccess,
    actor: User,
    activities: ActivitiesReader,
    project_id: int,
    *,
    now: datetime,
) -> tuple[ApprovalRequest, str]:
    """WBS の承認を申請する（wbs.edit。RULE-WBS-006 の SUBMIT）。作った申請と、申請の後の WBS の状態を返す。

    - DRAFT・IN_REVIEW でなければ 409 ``INVALID_STATE``。前提（完了条件の欠け・未対応の指摘・100% ルールの ERROR が 0 件）を
      満たさなければ 422 ``WBS_NOT_READY``（``details`` は満たしていない前提とその件数）
    - 通れば WBS は PENDING_APPROVAL、承認申請（``target_type = WBS``・``target_id`` はプロジェクトの ID。A-15）を作り、
      開いているレビューを閉じる（5.10 の IN_REVIEW → PENDING_APPROVAL。レビューを閉じる API-WBS-024 を呼ばずに申請しても
      レビューが開いたまま残らないように。OQ-18 (2)・A-18）
    """
    authorize_project(session, access, actor, project_id, "wbs.edit")
    # 二重の申請を防ぐ：状態と前提はプロジェクトの行をロックして読み直した後の値で判定する（A-49 の 3）
    context = load_wbs_context(session, project_id, activities, for_update=True)
    project = context.project
    readiness = readiness_of(session, context)
    try:
        transition = transition_wbs(project.wbs_status, WbsAction.SUBMIT, readiness=readiness)
    except WorkflowError as exc:
        if exc.code is WorkflowErrorCode.WBS_NOT_READY:
            details = [{"condition": name, "count": getattr(readiness, name)} for name in readiness.unmet()]
            raise _workflow_error(exc, "承認を申請する前提を満たしていません", details) from exc
        raise _workflow_error(exc, f"WBS が {project.wbs_status} のときは承認を申請できません",
                              [{"wbs_status": project.wbs_status}]) from exc
    session.execute(
        update(WbsReview).where(WbsReview.project_id == project_id, WbsReview.status == REVIEW_STATUS_INITIAL)
        .values(status=REVIEW_STATUS_CLOSED, closed_at=now)
    )
    project.wbs_status = transition.to_status.value
    approval = ApprovalRequest(project_id=project_id, target_type=TARGET_WBS, target_id=project_id,
                               requested_by=actor.id, requested_at=now)
    session.add(approval)
    session.commit()
    return approval, project.wbs_status


@dataclass(frozen=True)
class DecisionView:
    """承認・差戻しの結果。WBS の申請なら WBS の状態と、承認で作った版（差戻しなら ``None``）。"""

    approval: ApprovalRequest
    wbs_status: str | None
    baseline: ScopeBaseline | None
    baseline_element_count: int


def _snapshot_scope(session: Session, baseline: ScopeBaseline, elements: Sequence[WbsElement]) -> int:
    """その時点の WBS と辞書を版の写し（``scope_baseline_elements``）に固定する（WBS-22）。写した要素の数を返す。"""
    ids = [element.id for element in elements]
    entries = {
        entry.element_id: entry
        for entry in session.scalars(select(WbsDictionaryEntry).where(WbsDictionaryEntry.element_id.in_(ids)))
    } if ids else {}
    for element in elements:
        entry = entries.get(element.id)
        session.add(ScopeBaselineElement(
            scope_baseline_id=baseline.id, element_id=element.id, parent_element_id=element.parent_id,
            wbs_code=element.wbs_code, kind=element.kind, name=element.name,
            own_estimate_hours=element.own_estimate_hours,
            dictionary=None if entry is None else {name: getattr(entry, name) for name in DICTIONARY_FIELDS},
        ))
    return len(elements)


def decide(
    session: Session,
    access: ProjectAccess,
    actor: User,
    activities: ActivitiesReader,
    change_requests: ChangeRequestApprovals,
    approval_id: int,
    *,
    decision: str,
    comment: str | None,
    now: datetime,
) -> DecisionView:
    """承認申請を承認・差戻しする（WBS は wbs.approve、変更要求は change.approve）。

    - 無い申請は 404。PENDING でなければ 409 ``INVALID_STATE``、申請者本人は 403 ``SELF_APPROVAL_FORBIDDEN``（5.10・9.2）
    - WBS の承認は RULE-WBS-006 で PENDING_APPROVAL → BASELINED とし、スコープベースラインの新しい版（初めてなら 1・INITIAL_APPROVAL、
      あれば次の版・RE_APPROVAL）にその時点の WBS と辞書を写す。差戻しは DRAFT へ戻す
    - 変更要求の申請は ``change_requests``（RULE-CHG-003。CHG の群）へ判断を渡す
    - 申請に判断した人・日時・コメントを残す（WBS-21）
    - 同時の判断・取り下げは行のロックで 1 つずつ流す：待っている間に判断された申請は 409、取り下げで消えた申請は 404（A-49 の 3）
    """
    approval = session.get(ApprovalRequest, approval_id)
    if approval is None:
        raise AppError(404, "NOT_FOUND", "承認申請が見つかりません", details=[{"approval_id": approval_id}])
    authorize_project(session, access, actor, approval.project_id, _DECIDE_PERMISSION[approval.target_type])
    # 同時の判断・取り下げと競わせない（A-49 の 3）：申請の対象の行（WBS は projects、変更要求は change_requests）を先に、
    # 申請の行を後にロックし（提出・取り下げと同じ順）、どちらもロックして読み直した値で判定する。
    # 対象・プロジェクト・申請者は申請の行で変わらない値なので、ロックの前に読んだ値で権限とロックする行を決めてよい
    context: WbsContext | None = None
    if approval.target_type == TARGET_WBS:
        context = load_wbs_context(session, approval.project_id, activities, for_update=True)
    else:
        lock_row(session, ChangeRequest, approval.target_id)
    locked = lock_row(session, ApprovalRequest, approval_id)
    if locked is None:  # 待っている間に取り下げられた（変更要求の取り下げは申請の行を消す。5.10）
        raise AppError(404, "NOT_FOUND", "承認申請が見つかりません", details=[{"approval_id": approval_id}])
    approval = locked
    try:
        status = decide_approval(approval.status, decision, requested_by=approval.requested_by, decided_by=actor.id)
    except WorkflowError as exc:
        raise _workflow_error(exc, str(exc), [{"approval_id": approval.id, "status": approval.status}]) from exc

    wbs_status: str | None = None
    baseline: ScopeBaseline | None = None
    count = 0
    if context is not None:
        project = context.project
        latest = session.scalar(select(func.max(ScopeBaseline.version)).where(ScopeBaseline.project_id == project.id))
        action = WbsAction.APPROVE if ApprovalDecision(decision) is ApprovalDecision.APPROVE else WbsAction.SEND_BACK
        try:
            transition = transition_wbs(project.wbs_status, action, requested_by=approval.requested_by,
                                        decided_by=actor.id, latest_baseline_version=latest)
        except WorkflowError as exc:
            raise _workflow_error(exc, f"WBS が {project.wbs_status} なので、この申請は判断できません",
                                  [{"wbs_status": project.wbs_status}]) from exc
        project.wbs_status = wbs_status = transition.to_status.value
        if transition.baseline is not None:
            baseline = ScopeBaseline(project_id=project.id, version=transition.baseline.version,
                                     reason=transition.baseline.reason.value, approval_request_id=approval.id,
                                     created_by=actor.id, created_at=now)
            session.add(baseline)
            session.flush()
            count = _snapshot_scope(session, baseline, context.elements)
    else:
        change_requests.decide(session, approval, status, actor, now)

    approval.status = status.value
    approval.decided_by = actor.id
    approval.decided_at = now
    approval.comment = comment
    session.commit()
    return DecisionView(approval=approval, wbs_status=wbs_status, baseline=baseline, baseline_element_count=count)


@dataclass(frozen=True)
class PendingApproval:
    """承認待ちの一覧の 1 件（申請と、そのプロジェクト・申請者）。"""

    approval: ApprovalRequest
    project: Project
    requester: User


def list_pending_approvals(
    session: Session, access: ProjectAccess, actor: User, *, limit: int, offset: int
) -> tuple[list[PendingApproval], int]:
    """自分が判断できる承認待ち（ログイン）：PENDING の申請のうち、そのプロジェクトで判断の権限（WBS は wbs.approve、
    変更要求は change.approve。RULE-AUTH-001）を持ち、自分の申請でないもの（5.10）。申請の古い順と総数。
    """
    principal = principal_of(actor)
    memberships = access.memberships(session, actor.id)
    tables: dict[int, Mapping[str, frozenset[str]]] = {}
    rows = session.execute(
        select(ApprovalRequest, Project, User)
        .join(Project, Project.id == ApprovalRequest.project_id)
        .join(User, User.id == ApprovalRequest.requested_by)
        .where(ApprovalRequest.status == ApprovalStatus.PENDING.value, ApprovalRequest.requested_by != actor.id)
        .order_by(ApprovalRequest.requested_at, ApprovalRequest.id)
    ).all()
    allowed: list[PendingApproval] = []
    for approval, project, requester in rows:
        if project.id not in tables:
            tables[project.id] = load_role_permission_table(session, project.id)
        decision = authorize(principal, _DECIDE_PERMISSION[approval.target_type], role_permissions=tables[project.id],
                             membership=memberships.get(project.id))
        if decision.allowed:
            allowed.append(PendingApproval(approval=approval, project=project, requester=requester))
    return allowed[offset:offset + limit], len(allowed)
