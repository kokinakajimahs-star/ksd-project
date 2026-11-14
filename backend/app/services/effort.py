"""工数（EFF）のユースケース（設計書 6.2 の API-EFF-001〜007、4.1・4.2・5.8、9 章。
要求仕様書 EFF-01・02・06〜10、WBS-10、BR-10・11・13）。

- 権限（RULE-AUTH-001）：割当を書くは schedule.edit、割当を読むは project.view（委託先だけで読む利用者は自分の割当の活動に絞る）、
  実績工数を書くは effort.enter_own（自分の割当の活動）、実績工数を読むは「本人の行は effort.enter_own、他人の行は effort.view を
  持つプロジェクトの分」、集計・差異を読むは effort.view、差異の理由を書くは variance.note
- 実績工数の書き込み（API-EFF-003）は日×活動のマスの置き換え。RULE-EFF-005 で全てのマスを検査し、1 件でも問題があれば全体を拒む
  （値の誤り・重複は 400、自分の割当でない活動は 403、確定した報告日以前の日は 409 ``INVALID_STATE``、1 日の合計 24 時間超は
  422 ``DAY_TOTAL_EXCEEDED``。問題は全て ``details`` に載せる）。0 時間のマスは行を持たない（行を消す）。
  読めないプロジェクト（メンバーでなく、システム管理者でもない）の活動は、無い活動と同じ 404（6.1・CR-019）
- 集計（API-EFF-005）と差異（API-EFF-006）は 5.8 のとおり：実績＝``time_entries`` の合計（報告日を指定したときは報告日以前の分）、
  進捗率・入力した ETC＝対象の報告日（指定しなければ 4.2 の「最新」）の ``progress_entries``、計画＝今の見積、
  基準＝選んだスケジュールベースライン（指定しなければ最新の版）の見積。ETC・EAC は RULE-EFF-001・002、軸は RULE-EFF-003、
  差異は RULE-EFF-004（閾値は ``projects.settings`` の ``variance_rate``・``variance_hours``。4.2）
- 差異の理由（API-EFF-007）は WBS要素・報告日（空は報告日を指定しない分析）ごとに 1 行を上書きする（PUT。OQ-26 (1)。A-30）

**別の群の表を読む口**（``app.state.effort_links``。A-18・A-26 と同じ形）：

- ``CorrectiveActions``：差異の理由に紐付ける是正措置（``corrective_actions``。DB-CHG-002）があるか。表がまだ無い間は
  ``CorrectiveActionsTableAbsent``（是正措置は 1 つも無い＝紐付けは 404）。db:CHG で表を読む ``CorrectiveActionsTable`` へ差し替えた（A-31）。
  差し替え忘れは ``backend/tests/api/test_eff.py::test_corrective_actions_port_is_wired_once_table_exists`` が落とす
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.orm import Session

from app.domain.authz import ViewLevel, authorize, effective_permissions, project_view_level
from app.domain.effort import (
    ActivityEffortInput,
    ActivityPeriod,
    AssignmentShare,
    BaselineItem,
    EffortAxes,
    EffortElement,
    EffortRollup,
    PeriodUnit,
    TimeEntryChange,
    TimeEntryProblem,
    TimeEntryProblemCode,
    TimeEntryValue,
    VarianceResult,
    aggregate_effort,
    check_time_entries,
    extract_variances,
    rollup_effort,
)
from app.domain.estimate import HOURS_QUANTUM, ElementKind
from app.domain.wbs_code import CodedElement, display_preorder
from app.errors import AppError
from app.models.chg import CorrectiveAction
from app.models.cmn import User
from app.models.eff import Assignment, TimeEntry, VarianceNote
from app.models.prg import StatusReport as StatusReport  # API 層は報告日の型をここから取る（1.3：API はモデルを直接 import しない）
from app.models.prj import Project, ProjectMember
from app.models.sch import (  # API 層はスケジュールベースラインの型をここから取る（1.3：API はモデルを直接 import しない）
    Activity,
    ScheduleBaseline as ScheduleBaseline,
    ScheduleBaselineItem,
)
from app.models.wbs import WbsElement
from app.services.auth import authorize_project, forbidden, load_role_permission_table, principal_of, project_not_found
from app.services.progress import entries_of_report, percents_at_report, project_settings, report_percents
from app.services.project_access import ProjectAccess
from app.services.schedule import Plan, activity_codes_of, load_plan

ENTER_OWN = "effort.enter_own"
EFFORT_VIEW = "effort.view"
STATUS_FINALIZED = "FINALIZED"
# 最新の報告日を選ぶ順（4.2：確定していない最新の報告日、無ければ最新の確定済みの報告日）
_LATEST_REPORT_STATUS_ORDER = ("OPEN", "FINALIZED")
_ZERO = Decimal(0)

# 集計の軸（6.2 の API-EFF-005 の ``axis``）
AXES = ("wbs", "member", "organization", "week", "month")


# ---------------------------------------------------------------------------
# 別の群の表を読む口
# ---------------------------------------------------------------------------


class CorrectiveActions(Protocol):
    """是正措置（``corrective_actions``。DB-CHG-002）を読む。"""

    def exists(self, session: Session, project_id: int, action_id: int) -> bool:
        """そのプロジェクトにその是正措置があるか。"""
        ...


class CorrectiveActionsTableAbsent:
    """是正措置の表（DB-CHG-002）がまだ無い間の ``CorrectiveActions``。是正措置は 1 つも無い（今のスキーマでの事実）。"""

    def exists(self, session: Session, project_id: int, action_id: int) -> bool:
        return False


class CorrectiveActionsTable:
    """是正措置の表（``corrective_actions``。DB-CHG-002）を読む ``CorrectiveActions``（db:CHG で差し替えた。A-31）。

    別のプロジェクトの是正措置は「無い」とみなす（差異の理由は同じプロジェクトの是正措置にだけ紐付ける）。
    """

    def exists(self, session: Session, project_id: int, action_id: int) -> bool:
        found = session.scalar(
            select(CorrectiveAction.id).where(CorrectiveAction.id == action_id, CorrectiveAction.project_id == project_id)
        )
        return found is not None


@dataclass(frozen=True)
class EffortLinks:
    """工数の API が使う、別の群の表の口（``app.state.effort_links``）。既定は「表がまだ無い」（アプリは表を読む口を載せる。A-31）。"""

    corrective_actions: CorrectiveActions = field(default_factory=CorrectiveActionsTableAbsent)


# ---------------------------------------------------------------------------
# 共通の部品
# ---------------------------------------------------------------------------


def _validation(details: list[dict[str, Any]], message: str = "入力に誤りがあります") -> AppError:
    return AppError(400, "VALIDATION_ERROR", message, details=details)


def _not_found(what: str, key: str, value: int) -> AppError:
    return AppError(404, "NOT_FOUND", f"{what}が見つかりません", details=[{key: value}])


def _decimal(value: object) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _activity_of(session: Session, project_id: int, activity_id: int) -> Activity:
    activity = session.get(Activity, activity_id)
    if activity is None or activity.project_id != project_id:
        raise _not_found("活動", "activity_id", activity_id)
    return activity


def _element_of(session: Session, project_id: int, element_id: int) -> WbsElement:
    element = session.get(WbsElement, element_id)
    if element is None or element.project_id != project_id:
        raise _not_found("WBS要素", "element_id", element_id)
    return element


def _report_of(session: Session, project_id: int, report_id: int) -> StatusReport:
    report = session.get(StatusReport, report_id)
    if report is None or report.project_id != project_id:
        raise _not_found("報告日", "status_report_id", report_id)
    return report


def _assignable_user_ids(session: Session, project_id: int) -> frozenset[int]:
    """担当にできる利用者：そのプロジェクトのメンバーである有効な利用者（EFF-01。API-SCH-004 と同じ判定）。"""
    rows = session.scalars(
        select(ProjectMember.user_id)
        .join(User, User.id == ProjectMember.user_id)
        .where(ProjectMember.project_id == project_id, User.is_active.is_(True))
    )
    return frozenset(rows)


def _display_names(session: Session, user_ids: Iterable[int]) -> dict[int, str]:
    ids = sorted(set(user_ids))
    if not ids:
        return {}
    return {u.id: u.display_name for u in session.scalars(select(User).where(User.id.in_(ids)))}


def _element_preorder(elements: Iterable[WbsElement]) -> list[int]:
    return display_preorder(
        CodedElement(id=e.id, parent_id=e.parent_id, wbs_code=e.wbs_code, sibling_seq=e.sibling_seq,
                     sort_order=e.sort_order)
        for e in elements
    )


# ---------------------------------------------------------------------------
# 割当（API-EFF-001・002）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AssignmentRow:
    assignment: Assignment
    activity: Activity
    wbs_code: str
    display_name: str


@dataclass(frozen=True)
class ActivityAssignmentsView:
    """活動の割当と、割当工数の合計・見積との差（EFF-01 の「割当工数の合計を見積と比べて表示する」）。"""

    activity: Activity
    rows: list[AssignmentRow]
    planned_total: Decimal
    difference: Decimal | None  # 割当工数の合計−見積（見積が空なら None）


def _assignment_rows(session: Session, project_id: int, activity_ids: Sequence[int] | None = None,
                     user_id: int | None = None) -> list[tuple[Assignment, Activity]]:
    query = (
        select(Assignment, Activity)
        .join(Activity, Activity.id == Assignment.activity_id)
        .where(Activity.project_id == project_id)
    )
    if activity_ids is not None:
        query = query.where(Assignment.activity_id.in_(activity_ids))
    if user_id is not None:
        query = query.where(Assignment.user_id == user_id)
    return [(a, act) for a, act in session.execute(query).all()]


def _activity_assignments_view(session: Session, activity: Activity) -> ActivityAssignmentsView:
    found = _assignment_rows(session, activity.project_id, [activity.id])
    names = _display_names(session, (a.user_id for a, _ in found))
    code = activity_codes_of(session, [activity.id])[activity.id]  # 活動の番号（5.2。CR-028）
    rows = [AssignmentRow(a, act, code, names.get(a.user_id, "")) for a, act in
            sorted(found, key=lambda r: r[0].user_id)]
    total = sum((_decimal(r.assignment.planned_hours) for r in rows if r.assignment.planned_hours is not None), _ZERO)
    estimate = None if activity.estimate_hours is None else _decimal(activity.estimate_hours)
    return ActivityAssignmentsView(activity=activity, rows=rows, planned_total=total,
                                   difference=None if estimate is None else total - estimate)


def replace_assignments(
    session: Session, access: ProjectAccess, actor: User, project_id: int, activity_id: int,
    assignments: Sequence[Mapping[str, Any]],
) -> ActivityAssignmentsView:
    """活動の担当と割当工数を置き換える（schedule.edit。EFF-01）。

    載せた担当の行を作る・割当工数を変え、載せなかった担当の行を消す。担当はそのプロジェクトのメンバーである有効な利用者
    （違えば 400）。同じ利用者を 2 回載せる・割当工数が負・小数 3 桁以降は 400。割当工数の空は「決めていない」（DB-EFF-001）。
    """
    authorize_project(session, access, actor, project_id, "schedule.edit")
    activity = _activity_of(session, project_id, activity_id)
    assignable = _assignable_user_ids(session, project_id)
    problems: list[dict[str, Any]] = []
    wanted: dict[int, Decimal | None] = {}
    for index, row in enumerate(assignments):
        user_id = int(row["user_id"])
        loc = ["body", "assignments", index]
        if user_id in wanted:
            problems.append({"loc": [*loc, "user_id"], "message": f"利用者 {user_id} を 2 回載せている"})
            continue
        if user_id not in assignable:
            problems.append({"loc": [*loc, "user_id"],
                             "message": f"利用者 {user_id} はこのプロジェクトの有効なメンバーではない（担当にできない）"})
            continue
        hours = row.get("planned_hours")
        planned = None if hours is None else _decimal(hours)
        if planned is not None and (planned < 0 or planned != planned.quantize(HOURS_QUANTUM)):
            problems.append({"loc": [*loc, "planned_hours"], "message": "割当工数は 0 以上・小数 2 桁までの人時"})
            continue
        wanted[user_id] = planned
    if problems:
        raise _validation(problems)

    current = {a.user_id: a for a in session.scalars(select(Assignment).where(Assignment.activity_id == activity_id))}
    removed = sorted(set(current) - set(wanted))
    if removed:
        session.execute(delete(Assignment).where(Assignment.activity_id == activity_id, Assignment.user_id.in_(removed)))
    for user_id, planned in sorted(wanted.items()):
        if user_id in current:
            current[user_id].planned_hours = planned
        else:
            session.add(Assignment(activity_id=activity_id, user_id=user_id, planned_hours=planned))
    session.commit()
    return _activity_assignments_view(session, activity)


def list_assignments(
    session: Session, access: ProjectAccess, actor: User, project_id: int, *, user_id: int | None,
    activity_id: int | None, limit: int, offset: int,
) -> tuple[list[AssignmentRow], int]:
    """割当の一覧（project.view。EFF-01・WBS-10）。担当者・活動で絞れる。並びはガントの行の順→利用者の ID。

    委託先だけで読む利用者には自分の割当の活動の割当だけを返す（9.2 の注記）。
    """
    table = authorize_project(session, access, actor, project_id, "project.view")
    membership = access.membership(session, project_id, actor.id)
    level = project_view_level(principal_of(actor), role_permissions=table, membership=membership)
    visible = None
    if level is not ViewLevel.ALL:
        visible = membership.assigned_activity_ids if membership is not None else frozenset()
    found = _assignment_rows(session, project_id, None if activity_id is None else [activity_id], user_id)
    if visible is not None:
        found = [r for r in found if r[1].id in visible]
    plan = load_plan(session, project_id)
    rank = {a.id: i for i, a in enumerate(plan.ordered_activities())}
    found.sort(key=lambda r: (rank.get(r[1].id, len(rank)), r[0].user_id))
    page = found[offset:offset + limit]
    names = _display_names(session, (a.user_id for a, _ in page))
    codes = plan.activity_codes()  # 活動の番号（5.2。CR-028）。読めない活動も数える
    return [AssignmentRow(a, act, codes[act.id], names.get(a.user_id, "")) for a, act in page], len(found)


# ---------------------------------------------------------------------------
# 実績工数（API-EFF-003・004）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TimeEntryCell:
    """書きたい 1 マス（日×活動）。``hours`` はその日・その活動の値で置き換える（0 は行を持たない）。"""

    activity_id: int
    work_date: date
    hours: Decimal
    note: str | None = None


@dataclass(frozen=True)
class TimeEntryRow:
    entry: TimeEntry
    project_id: int
    activity_name: str
    wbs_code: str


@dataclass(frozen=True)
class TimeEntriesWritten:
    user_id: int
    changed: int
    rows: list[TimeEntryRow]
    day_totals: Mapping[date, Decimal]


# 実績工数の入力検査（RULE-EFF-005）の問題の種類 → HTTP の状態（強い順に並べた。1 件でもあれば全体を拒み、最も前の状態で返す）
_PROBLEM_STATUS: tuple[tuple[frozenset[TimeEntryProblemCode], int, str, str], ...] = (
    (frozenset({TimeEntryProblemCode.INVALID_HOURS, TimeEntryProblemCode.NEGATIVE_HOURS, TimeEntryProblemCode.DUPLICATE}),
     400, "VALIDATION_ERROR", "入力に誤りがあるため、全件を保存しませんでした"),
    (frozenset({TimeEntryProblemCode.NOT_ASSIGNED}), 403, "FORBIDDEN", "自分の割当でない活動には実績工数を書けません"),
    (frozenset({TimeEntryProblemCode.LOCKED_DATE}), 409, "INVALID_STATE",
     "確定した報告日以前の日の実績工数は変えられません"),
    (frozenset({TimeEntryProblemCode.DAY_TOTAL_EXCEEDED}), 422, "DAY_TOTAL_EXCEEDED",
     "1 日の実績工数の合計が 24 時間を超えます"),
)


def _reject(problems: Sequence[TimeEntryProblem]) -> AppError:
    details = [{"code": p.code.value, "work_date": p.work_date.isoformat(), "activity_id": p.activity_id,
                "message": p.message} for p in problems]
    codes = {p.code for p in problems}
    for kinds, status, code, message in _PROBLEM_STATUS:
        if codes & kinds:
            return AppError(status, code, message, details=details)
    raise AssertionError(f"扱いの無い問題の種類: {sorted(codes)}")  # pragma: no cover - 種類を足したら表に足す


def _time_entry_rows(session: Session, entries: Iterable[TimeEntry]) -> list[TimeEntryRow]:
    rows = list(entries)
    ids = sorted({e.activity_id for e in rows})
    info: dict[int, tuple[int, str, str]] = {}
    if ids:
        found = session.execute(select(Activity.id, Activity.project_id, Activity.name).where(Activity.id.in_(ids))).all()
        codes = activity_codes_of(session, ids)  # 活動の番号（5.2。CR-028）。プロジェクトをまたぐ
        info = {r[0]: (r[1], r[2], codes[r[0]]) for r in found}
    return [TimeEntryRow(entry=e, project_id=info[e.activity_id][0], activity_name=info[e.activity_id][1],
                         wbs_code=info[e.activity_id][2]) for e in rows]


def put_time_entries(
    session: Session, access: ProjectAccess, actor: User, cells: Sequence[TimeEntryCell],
) -> TimeEntriesWritten:
    """自分の実績工数をまとめて書く（effort.enter_own。EFF-02）。日×活動のマスを置き換える。プロジェクトをまたいでよい。

    確かめる順：活動が無い 404 → 活動のプロジェクトで effort.enter_own を持たない 403 → RULE-EFF-005 の検査（全てのマス）。
    読めないプロジェクト（メンバーでなく、システム管理者でもない）の活動は無い活動と同じ 404 にする（6.1・CR-019：他のプロジェクトの
    活動の有無を明かさない。``details`` にプロジェクトの ID を載せない）。委託先だけで読むプロジェクト（view_level が
    OWN_ASSIGNMENTS）の自分の割当でない活動も見えないので、無い活動と同じ 404 にする（9.2 の注記。RULE-EFF-005 の
    「自分の割当でない」403 より先）。
    RULE-EFF-005 の「自分の割当の活動だけ」「確定した報告日以前の日は変えない」はマスのプロジェクトごとに（割当と確定した報告日は
    プロジェクトのもの）、「1 日の合計 24 時間以下」はプロジェクトをまたいだ全ての行で見る。メモだけを変えたマスも「変える」とみなす。
    1 件でも問題があれば何も書かない。
    """
    activity_ids = sorted({c.activity_id for c in cells})
    activities = {a.id: a for a in session.scalars(select(Activity).where(Activity.id.in_(activity_ids)))}
    principal = principal_of(actor)
    hidden: set[int] = set()
    for project_id in sorted({a.project_id for a in activities.values()}):
        membership = access.membership(session, project_id, actor.id)
        if membership is None and not principal.is_system_admin:
            hidden |= {a.id for a in activities.values() if a.project_id == project_id}
            continue
        level = project_view_level(principal, role_permissions=load_role_permission_table(session, project_id),
                                   membership=membership)
        if level is ViewLevel.OWN_ASSIGNMENTS:
            own = membership.assigned_activity_ids if membership is not None else frozenset()
            hidden |= {a.id for a in activities.values() if a.project_id == project_id and a.id not in own}
    missing = [a for a in activity_ids if a not in activities or a in hidden]
    if missing:
        raise AppError(404, "NOT_FOUND", "活動が見つかりません", details=[{"activity_ids": missing}])

    project_ids = sorted({a.project_id for a in activities.values()})
    assigned: set[int] = set()
    finalized: dict[int, list[date]] = {}
    for project_id in project_ids:
        table = load_role_permission_table(session, project_id)
        membership = access.membership(session, project_id, actor.id)
        if ENTER_OWN not in effective_permissions(principal, role_permissions=table, membership=membership):
            raise AppError(403, "FORBIDDEN", "この操作の権限がありません", details=[{"project_id": project_id}])
        for activity in activities.values():
            if activity.project_id == project_id and authorize(
                principal, ENTER_OWN, role_permissions=table, membership=membership, activity_id=activity.id
            ).allowed:
                assigned.add(activity.id)
        finalized[project_id] = list(session.scalars(
            select(StatusReport.report_date)
            .where(StatusReport.project_id == project_id, StatusReport.status == STATUS_FINALIZED)
        ))

    dates = sorted({c.work_date for c in cells})
    existing_rows = {(e.activity_id, e.work_date): e for e in session.scalars(
        select(TimeEntry).where(TimeEntry.user_id == actor.id, TimeEntry.work_date.in_(dates))
    )}
    existing = [TimeEntryValue(user_id=actor.id, activity_id=e.activity_id, work_date=e.work_date, hours=_decimal(e.hours))
                for e in existing_rows.values()]
    changes = [TimeEntryChange(activity_id=c.activity_id, work_date=c.work_date, hours=c.hours) for c in cells]

    problems: list[TimeEntryProblem] = []
    zero_with_note = [c for c in cells if c.note and c.hours == 0]
    for c in zero_with_note:
        problems.append(TimeEntryProblem(TimeEntryProblemCode.INVALID_HOURS, c.work_date, c.activity_id,
                                         "0 時間のマスにはメモを書けない（0 時間のマスは行を持たない）"))
    # メモだけを変えるマスは、今の行が無いものとして検査し、割当・確定の検査を当てる
    note_changed = {(c.activity_id, c.work_date) for c in cells
                    if (c.activity_id, c.work_date) in existing_rows
                    and (existing_rows[(c.activity_id, c.work_date)].note or None) != (c.note or None)}
    for project_id in project_ids:
        own_changes = [ch for ch in changes if activities[ch.activity_id].project_id == project_id]
        checked = check_time_entries(
            own_changes,
            existing=[e for e in existing if (e.activity_id, e.work_date) not in note_changed],
            assigned_activity_ids=assigned,
            finalized_report_dates=finalized[project_id],
        )
        problems.extend(p for p in checked.problems if p.code is not TimeEntryProblemCode.DAY_TOTAL_EXCEEDED)
    whole = check_time_entries(changes, existing=existing, assigned_activity_ids=activity_ids)
    problems.extend(p for p in whole.problems if p.code is TimeEntryProblemCode.DAY_TOTAL_EXCEEDED)
    if problems:
        raise _reject(problems)

    changed = 0
    for c in cells:
        key = (c.activity_id, c.work_date)
        row = existing_rows.get(key)
        if c.hours == 0:
            if row is not None:
                session.delete(row)
                changed += 1
            continue
        if row is None:
            session.add(TimeEntry(user_id=actor.id, activity_id=c.activity_id, work_date=c.work_date, hours=c.hours,
                                  note=c.note or None))
            changed += 1
        elif _decimal(row.hours) != c.hours or (row.note or None) != (c.note or None):
            row.hours = c.hours
            row.note = c.note or None
            changed += 1
    session.commit()
    written = session.scalars(
        select(TimeEntry).where(TimeEntry.user_id == actor.id, TimeEntry.work_date.in_(dates))
        .order_by(TimeEntry.work_date, TimeEntry.activity_id)
    )
    return TimeEntriesWritten(user_id=actor.id, changed=changed, rows=_time_entry_rows(session, written),
                              day_totals=whole.day_totals)


def list_time_entries(
    session: Session, access: ProjectAccess, actor: User, *, user_id: int | None, project_id: int | None,
    date_from: date | None, date_to: date | None, limit: int, offset: int,
) -> tuple[list[TimeEntryRow], int]:
    """実績工数を読む（本人の行は effort.enter_own、他人の行は effort.view を持つプロジェクトの分。EFF-02）。

    読める行は、プロジェクトごとに「effort.view を持てば全ての利用者の行、effort.enter_own だけなら自分の行」。``user_id`` はその上の絞り込み。
    ``project_id`` を指定してそこで読める行が無い・他人を指定して effort.view を持つプロジェクトが無い・どのプロジェクトでも
    どちらの権限も持たない、は 403。並びは作業日→活動→利用者。
    """
    principal = principal_of(actor)
    if project_id is not None:
        if not access.project_exists(session, project_id):
            raise project_not_found(project_id)
        candidates = [project_id]
    elif actor.is_system_admin:
        candidates = list(session.scalars(select(Project.id).order_by(Project.id)))
    else:
        candidates = sorted(access.memberships(session, actor.id))
    view_projects: list[int] = []
    own_projects: list[int] = []
    for pid in candidates:
        table = load_role_permission_table(session, pid)
        granted = effective_permissions(principal, role_permissions=table, membership=access.membership(session, pid, actor.id))
        if EFFORT_VIEW in granted:
            view_projects.append(pid)
        elif ENTER_OWN in granted:
            own_projects.append(pid)
    other_user = user_id is not None and user_id != actor.id
    if not view_projects and (other_user or not own_projects):
        raise forbidden()

    readable = or_(Activity.project_id.in_(view_projects),
                   and_(Activity.project_id.in_(own_projects), TimeEntry.user_id == actor.id))
    query = select(TimeEntry).join(Activity, Activity.id == TimeEntry.activity_id).where(readable)
    if user_id is not None:
        query = query.where(TimeEntry.user_id == user_id)
    if date_from is not None:
        query = query.where(TimeEntry.work_date >= date_from)
    if date_to is not None:
        query = query.where(TimeEntry.work_date <= date_to)
    found = list(session.scalars(query.order_by(TimeEntry.work_date, TimeEntry.activity_id, TimeEntry.user_id)))
    return _time_entry_rows(session, found[offset:offset + limit]), len(found)


# ---------------------------------------------------------------------------
# 集計の土台（5.8）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EffortBasis:
    """集計の入力と RULE-EFF-002 の結果。

    - ``report``：進捗率・入力した ETC を取った報告日（無ければ空）。``report_specified`` は報告日を指定したか
      （指定したときだけ実績を報告日以前の分に絞る。5.8）
    - ``baseline``：基準の値を取ったスケジュールベースライン（無ければ空。基準の値は全て「基準に無い」）。
      基準の値は版の項目から 5.8 の数え方で求める（RULE-EFF-002。設計書 1.1・CR-007）
    """

    plan: Plan
    report: StatusReport | None
    report_specified: bool
    baseline: ScheduleBaseline | None
    rollup: EffortRollup
    entries: list[TimeEntryValue]
    preorder: list[int]


def _latest_report(session: Session, project_id: int) -> StatusReport | None:
    for status in _LATEST_REPORT_STATUS_ORDER:
        report = session.scalars(
            select(StatusReport)
            .where(StatusReport.project_id == project_id, StatusReport.status == status)
            .order_by(StatusReport.report_date.desc())
            .limit(1)
        ).first()
        if report is not None:
            return report
    return None


def _baseline_of(session: Session, project_id: int, baseline_id: int | None) -> ScheduleBaseline | None:
    if baseline_id is None:
        return session.scalars(
            select(ScheduleBaseline).where(ScheduleBaseline.project_id == project_id)
            .order_by(ScheduleBaseline.version.desc()).limit(1)
        ).first()
    baseline = session.get(ScheduleBaseline, baseline_id)
    if baseline is None or baseline.project_id != project_id:
        raise _not_found("スケジュールベースライン", "baseline_id", baseline_id)
    return baseline


def load_basis(session: Session, project_id: int, *, report_id: int | None = None,
               baseline_id: int | None = None) -> EffortBasis:
    """5.8 の集計の入力を読み、RULE-EFF-002 で EAC と上位への集計を求める（権限は呼び手が確かめる）。"""
    plan = load_plan(session, project_id)
    report = _report_of(session, project_id, report_id) if report_id is not None else _latest_report(session, project_id)
    baseline = _baseline_of(session, project_id, baseline_id)
    baseline_items = None if baseline is None else [
        BaselineItem(item_type=item.item_type, element_id=item.element_id, activity_id=item.activity_id,
                     estimate_hours=None if item.estimate_hours is None else _decimal(item.estimate_hours),
                     start_date=item.start_date, finish_date=item.finish_date)
        for item in session.scalars(
            select(ScheduleBaselineItem).where(ScheduleBaselineItem.schedule_baseline_id == baseline.id)
            .order_by(ScheduleBaselineItem.id)
        )
    ]

    entries_of = entries_of_report(session, report.id) if report is not None else {}
    percents = (percents_at_report(session, plan.project, plan.activities, report, entries_of) if report is not None
                else report_percents(session, plan.project, plan.activities, entries_of))
    limit_date = report.report_date if report_id is not None and report is not None else None
    query = (select(TimeEntry).join(Activity, Activity.id == TimeEntry.activity_id)
             .where(Activity.project_id == project_id).order_by(TimeEntry.id))
    if limit_date is not None:
        query = query.where(TimeEntry.work_date <= limit_date)
    entries = [TimeEntryValue(user_id=e.user_id, activity_id=e.activity_id, work_date=e.work_date, hours=_decimal(e.hours))
               for e in session.scalars(query)]
    actual: dict[int, Decimal] = {}
    for e in entries:
        actual[e.activity_id] = actual.get(e.activity_id, _ZERO) + e.hours

    rollup = rollup_effort(
        [EffortElement(id=e.id, parent_id=e.parent_id, kind=ElementKind(e.kind),
                       own_estimate_hours=None if e.own_estimate_hours is None else _decimal(e.own_estimate_hours))
         for e in plan.elements],
        [ActivityEffortInput(
            id=a.id, work_package_id=a.work_package_id,
            estimate_hours=None if a.estimate_hours is None else _decimal(a.estimate_hours),
            actual_hours=actual.get(a.id, _ZERO),
            percent_complete=None if percents[a.id] is None else _decimal(percents[a.id]),
            entered_etc_hours=(None if a.id not in entries_of or entries_of[a.id].remaining_hours is None
                               else _decimal(entries_of[a.id].remaining_hours)),
        ) for a in plan.activities],
        baseline_items,
    )
    return EffortBasis(plan=plan, report=report, report_specified=report_id is not None, baseline=baseline,
                       rollup=rollup, entries=entries, preorder=_element_preorder(plan.elements))


def eac_by_element(session: Session, project_id: int) -> Mapping[int, Decimal]:
    """WBS要素ごとの EAC（RULE-EFF-002。最新の報告日の入力・全ての実績）。横断照会（API-WBS-023）が読む口（A-18）。"""
    basis = load_basis(session, project_id)
    return {element_id: e.values.eac for element_id, e in basis.rollup.elements.items()}


# ---------------------------------------------------------------------------
# 多軸集計（API-EFF-005）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SummaryView:
    axis: str
    basis: EffortBasis
    axes: EffortAxes
    display_names: Mapping[int, str]


def summary(
    session: Session, access: ProjectAccess, actor: User, project_id: int, *, axis: str, report_id: int | None,
    baseline_id: int | None,
) -> SummaryView:
    """計画・基準・実績・ETC・EAC を 1 つの軸で集計する（effort.view。EFF-07〜09。RULE-EFF-003）。"""
    authorize_project(session, access, actor, project_id, EFFORT_VIEW)
    if axis not in AXES:
        raise _validation([{"loc": ["query", "axis"], "message": f"axis は {'・'.join(AXES)} のいずれか"}])
    basis = load_basis(session, project_id, report_id=report_id, baseline_id=baseline_id)
    members = session.scalars(select(ProjectMember).where(ProjectMember.project_id == project_id)).all()
    shares = [AssignmentShare(activity_id=a.activity_id, user_id=a.user_id,
                              planned_hours=None if a.planned_hours is None else _decimal(a.planned_hours))
              for a, _ in _assignment_rows(session, project_id)]
    axes = aggregate_effort(
        basis.rollup,
        assignments=shares,
        time_entries=basis.entries,
        organizations={m.user_id: m.organization for m in members},
        schedules=[ActivityPeriod(activity_id=a.id, start_date=a.start_date, finish_date=a.finish_date)
                   for a in basis.plan.activities],
        calendar=basis.plan.calendar,
        period_unit=PeriodUnit.WEEK if axis == "week" else PeriodUnit.MONTH,
        report_date=basis.report.report_date if basis.report_specified and basis.report is not None else None,
    )
    names = _display_names(session, [u for u in axes.assignee if u is not None])
    return SummaryView(axis=axis, basis=basis, axes=axes, display_names=names)


# ---------------------------------------------------------------------------
# 差異の分析・理由（API-EFF-006・007）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VarianceRow:
    element: WbsElement
    result: VarianceResult
    note: VarianceNote | None


@dataclass(frozen=True)
class VarianceView:
    basis: EffortBasis
    rate_threshold: Decimal
    hours_threshold: Decimal
    rows: list[VarianceRow]


def _note_of(session: Session, element_id: int, status_report_id: int | None) -> VarianceNote | None:
    report_cond = (VarianceNote.status_report_id.is_(None) if status_report_id is None
                   else VarianceNote.status_report_id == status_report_id)
    return session.scalars(
        select(VarianceNote).where(VarianceNote.element_id == element_id, report_cond)
        .order_by(VarianceNote.created_at.desc(), VarianceNote.id.desc()).limit(1)
    ).first()


def variance(
    session: Session, access: ProjectAccess, actor: User, project_id: int, *, report_id: int | None,
    baseline_id: int | None,
) -> VarianceView:
    """差異（EAC−基準）が閾値を超えた要素と基準に無い要素の一覧（effort.view。EFF-10。RULE-EFF-004）。

    要素は WBS の並び（先行順）。閾値は ``projects.settings`` の ``variance_rate``・``variance_hours``（4.2）。
    スケジュールベースラインが 1 つも無ければ差異を求められないので 409 ``INVALID_STATE``。
    各要素には、同じ報告日（指定しなければ「報告日を指定しない分析」）の差異の理由を付ける。
    """
    authorize_project(session, access, actor, project_id, EFFORT_VIEW)
    basis = load_basis(session, project_id, report_id=report_id, baseline_id=baseline_id)
    if basis.baseline is None:
        raise AppError(409, "INVALID_STATE", "スケジュールベースラインが無いため、差異を求められません（基準を保存してください）")
    settings = project_settings(basis.plan.project)
    rate = _decimal(settings["variance_rate"])
    hours = _decimal(settings["variance_hours"])
    extracted = extract_variances(
        ((element_id, basis.rollup.elements[element_id].values) for element_id in basis.preorder),
        rate_threshold=rate, hours_threshold=hours,
    )
    elements = basis.plan.element_by_id
    rows = [VarianceRow(element=elements[element_id], result=result, note=_note_of(session, element_id, report_id))
            for element_id, result in extracted]
    return VarianceView(basis=basis, rate_threshold=rate, hours_threshold=hours, rows=rows)


@dataclass(frozen=True)
class NoteView:
    note: VarianceNote
    element: WbsElement


def put_variance_note(
    session: Session, access: ProjectAccess, actor: User, links: EffortLinks, project_id: int, element_id: int, *,
    status_report_id: int | None, reason: str, corrective_action_id: int | None, now: datetime,
) -> NoteView:
    """差異の理由と是正措置の紐付けを書く（variance.note。EFF-10）。

    WBS要素・報告日（空は報告日を指定しない分析）ごとに 1 行を上書きする（PUT。書いた人・時刻もその書き込みのもの。A-30）。
    要素・報告日が無ければ 404、紐付ける是正措置がそのプロジェクトに無ければ 404。理由が空白だけなら 400。
    """
    authorize_project(session, access, actor, project_id, "variance.note")
    element = _element_of(session, project_id, element_id)
    if status_report_id is not None:
        _report_of(session, project_id, status_report_id)
    if not reason.strip():
        raise _validation([{"loc": ["body", "reason"], "message": "差異の理由を書く"}])
    if corrective_action_id is not None and not links.corrective_actions.exists(session, project_id, corrective_action_id):
        raise _not_found("是正措置", "corrective_action_id", corrective_action_id)
    note = _note_of(session, element_id, status_report_id)
    if note is None:
        note = VarianceNote(project_id=project_id, element_id=element_id, status_report_id=status_report_id)
        session.add(note)
    note.reason = reason
    note.corrective_action_id = corrective_action_id
    note.created_by = actor.id
    note.created_at = now
    session.commit()
    return NoteView(note=note, element=element)
