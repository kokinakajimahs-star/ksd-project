"""スケジュール（SCH）のユースケース（設計書 6.2 の API-SCH-001〜013、5.4・5.5・7.3、9 章。要求仕様書 SCH-01〜04・06・09〜13・16〜18）。

- 読む操作は project.view、活動・依存・日程計算を変える操作は schedule.edit、ベースラインの保存は baseline.manage
  （RULE-AUTH-001 を ``authorize_project`` で通す。無いプロジェクトは 404 を先に返す）。
  委託先だけで読む利用者は、自分の割当の活動に絞る（9.2 の注記。``own_assignment_scope`` と同じ判定）
- 活動・依存を変えたら、同じトランザクションの最後にプロジェクトの全ての活動の日程を計算し直して保存する
  （RULE-SCH-001〜003。``calculate_and_store``。SCH-06 の自動反映）。カレンダー・開始日の変更（API-PRJ-004・008）からは
  ``ScheduleCalculator``（``app.state.schedule_recalculator``）として同じ計算を呼ぶ
- 開始日・期間・終了日は 2 つから残りを RULE-CAL-001 で求める（SCH-03）。保存するのは希望開始日と期間（4.2）。
  期間 0 の活動がマイルストーン（BR-04・SCH-09）
- 希望開始日はプロジェクトの開始日以降に限る（5.5・CR-018）。書いた後の希望開始日が開始日より前になる書き込みは
  422 ``BEFORE_PROJECT_START``（``starts_before_project``・``before_project_start``。取込・変更要求の反映・開始日の変更も使う）
- 一括更新は RULE-SCH-006、ガントは RULE-SCH-005、差異は RULE-SCH-004 を使う

**別の群の表を読む口・書く口**（``app.state.schedule_links``。A-14・A-17・A-18 と同じ形）：

- ``ActivityAssignments``：担当・割当（``assignments``。DB-EFF-001）。アプリには表を読み書きする ``AssignmentsStore`` を載せた
  （db:EFF。A-27）。``AssignmentsTableAbsent``（割当は 1 つも無い。担当を置こうとしたら止める＝書いたふりをしない）は表の無かった間の口
- ``latest_progress``：活動の最新の進捗（``progress_entries``。DB-PRG-002。4.2 の「最新の値」）と、その報告日。
  表がまだ無い間の既定は ``no_progress``（進捗の入力は無い）。db:PRG で表を作り、アプリには表を読む
  ``progress_entries_latest`` を載せた（A-22）

差し替え忘れは ``backend/tests/api/test_sch.py`` の ``test_*_wired_once_*`` が落とす。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session

from app.domain.authz import ViewLevel, project_view_level
from app.domain.baseline import (
    ActivityVariance,
    BaselineActivityItem,
    BaselineError,
    BaselineVersion,
    PlanActivity,
    compare_with_baseline,
    select_baseline,
)
from app.domain.calendar import CalendarError, WorkCalendar
from app.domain.estimate import ActivityEstimate, WbsNode, rollup_estimates
from app.domain.gantt import BaselineBarInput, GanttActivity, GanttChart, GanttElement, GanttRangeError, build_gantt
from app.domain.scheduling import (
    DEPENDENCY_LAG_R1,
    DEPENDENCY_TYPE_FS,
    UNSET,
    ActivityChange,
    BulkIssueCode,
    BulkUpdateError,
    DependencyGraph,
    EditableActivity,
    Schedule,
    ScheduleActivity,
    ScheduleCycleError,
    calculate_schedule,
    check_bulk_update,
)
from app.domain.wbs_code import CodedElement, display_preorder
from app.domain.wbs_rules import ActivityRef, AssignmentRef, WbsRuleError, ensure_hierarchy
from app.errors import AppError
from app.infra.db import lock_row
from app.models.chg import CorrectiveAction
from app.models.cmn import User
from app.models.eff import Assignment, TimeEntry
from app.models.prg import ProgressEntry, ProgressItem, StatusReport
from app.models.prj import WORKING_WEEKDAYS_DEFAULT, Calendar, CalendarHoliday, Project, ProjectMember
from app.models.sch import (  # API 層は依存・スケジュールベースラインの型をここから取る（1.3：API はモデルを直接 import しない）
    Activity,
    ActivityDependency as ActivityDependency,
    ScheduleBaseline as ScheduleBaseline,
    ScheduleBaselineItem,
)
from app.models.wbs import WbsElement
from app.services.auth import authorize_project, principal_of
from app.services.project_access import ProjectAccess

# PATCH（API-SCH-003）で受ける項目。``requested_finish`` は保存しない（開始日・期間を求めるのに使う。SCH-03）
UPDATABLE_FIELDS = frozenset({"name", "requested_start", "requested_finish", "duration_days", "is_milestone"})
_NON_NULLABLE_FIELDS = frozenset({"name", "duration_days", "is_milestone"})
WORK_PACKAGE = "WORK_PACKAGE"
BASELINE_REASON_MANUAL = "MANUAL"


# ---------------------------------------------------------------------------
# 別の群の表を読む口・書く口
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ActivityProgress:
    """活動の最新の進捗の入力（4.1 の ``progress_entries`` のうちガントに要る列）。進捗率の ``None`` は未算定。"""

    percent_complete: Decimal | None = None
    actual_start: date | None = None
    actual_finish: date | None = None


@dataclass(frozen=True)
class LatestProgress:
    """プロジェクトの最新の進捗（4.2：確定していない最新の報告日、無ければ最新の確定済みの報告日の行）とその報告日。"""

    report_date: date | None = None
    activities: Mapping[int, ActivityProgress] = field(default_factory=dict)


def no_progress(session: Session, project_id: int) -> LatestProgress:
    """進捗の表（DB-PRG-001・002）がまだ無い間の既定。報告日も進捗の入力も無い。"""
    return LatestProgress()


# 最新の報告日を選ぶ順（4.2：確定していない最新の報告日、無ければ最新の確定済みの報告日）
_LATEST_REPORT_STATUS_ORDER = ("OPEN", "FINALIZED")


def progress_entries_latest(session: Session, project_id: int) -> LatestProgress:
    """進捗の表（DB-PRG-001・002）から、プロジェクトの最新の進捗を読む（4.2）。

    報告日は「確定していない（OPEN）最新の報告日、無ければ最新の確定済み（FINALIZED）の報告日」の 1 つで、その報告日の
    行だけを返す（その報告日に行の無い活動は進捗の入力が無い。A-22・OQ-23）。報告日が 1 つも無ければ ``LatestProgress()``。
    """
    for status in _LATEST_REPORT_STATUS_ORDER:
        report = session.scalars(
            select(StatusReport)
            .where(StatusReport.project_id == project_id, StatusReport.status == status)
            .order_by(StatusReport.report_date.desc())
            .limit(1)
        ).first()
        if report is not None:
            break
    else:
        return LatestProgress()
    entries = session.scalars(select(ProgressEntry).where(ProgressEntry.status_report_id == report.id)).all()
    return LatestProgress(
        report_date=report.report_date,
        activities={
            entry.activity_id: ActivityProgress(
                percent_complete=entry.percent_complete,
                actual_start=entry.actual_start,
                actual_finish=entry.actual_finish,
            )
            for entry in entries
        },
    )


class ActivityAssignments(Protocol):
    """活動の担当・割当（``assignments``。DB-EFF-001）を読み書きする。"""

    def assignments(self, session: Session, project_id: int) -> Sequence[AssignmentRef]:
        """プロジェクトの全ての割当。"""
        ...

    def replace_assignees(self, session: Session, activity_id: int, user_ids: frozenset[int]) -> None:
        """活動の担当を ``user_ids`` に置き換える（残る担当の割当工数は変えない。コミットは呼び手が行う）。"""
        ...

    def remove_activity(self, session: Session, activity_id: int) -> None:
        """消す活動の割当を消す（コミットは呼び手が行う）。"""
        ...


class AssignmentsTableAbsent:
    """割当の表（DB-EFF-001）がまだ無い間の ``ActivityAssignments``。割当は 1 つも無い（今のスキーマでの事実）。

    担当を置く変更は書く先が無いので止める（fail fast。保存したふりをしない）。担当を空にする変更は今と同じなので何もしない。
    """

    def assignments(self, session: Session, project_id: int) -> Sequence[AssignmentRef]:
        return []

    def replace_assignees(self, session: Session, activity_id: int, user_ids: frozenset[int]) -> None:
        if user_ids:
            raise RuntimeError(
                f"活動 {activity_id} の担当を書く先（assignments。DB-EFF-001）がまだ無い"
                "（db:EFF のスコープが app.state.schedule_links の assignments を差し替える）"
            )

    def remove_activity(self, session: Session, activity_id: int) -> None:
        return None


class AssignmentsStore:
    """割当の表（``assignments``。DB-EFF-001）を読み書きする ``ActivityAssignments``（db:EFF で差し替えた。A-27）。

    担当の置き換えは、外れた担当の行を消し、新しい担当の行を割当工数なし（NULL）で足す。残る担当の割当工数は変えない。
    """

    def assignments(self, session: Session, project_id: int) -> Sequence[AssignmentRef]:
        rows = session.execute(
            select(Assignment.activity_id, Assignment.user_id, Assignment.planned_hours)
            .join(Activity, Activity.id == Assignment.activity_id)
            .where(Activity.project_id == project_id)
            .order_by(Assignment.activity_id, Assignment.user_id)
        ).all()
        return [AssignmentRef(r.activity_id, r.user_id, r.planned_hours) for r in rows]

    def replace_assignees(self, session: Session, activity_id: int, user_ids: frozenset[int]) -> None:
        current = set(session.scalars(select(Assignment.user_id).where(Assignment.activity_id == activity_id)))
        removed = current - user_ids
        if removed:
            session.execute(
                delete(Assignment).where(Assignment.activity_id == activity_id, Assignment.user_id.in_(removed))
            )
        for user_id in sorted(user_ids - current):
            session.add(Assignment(activity_id=activity_id, user_id=user_id))

    def remove_activity(self, session: Session, activity_id: int) -> None:
        session.execute(delete(Assignment).where(Assignment.activity_id == activity_id))


@dataclass(frozen=True)
class ScheduleLinks:
    """スケジュールの API が使う、別の群の表の口（``app.state.schedule_links``）。既定はどれも「表がまだ無い」。"""

    assignments: ActivityAssignments = field(default_factory=AssignmentsTableAbsent)
    latest_progress: Callable[[Session, int], LatestProgress] = field(default=no_progress)


# ---------------------------------------------------------------------------
# 共通の部品
# ---------------------------------------------------------------------------


def _validation_error(details: list[dict[str, Any]], message: str = "入力に誤りがあります") -> AppError:
    return AppError(400, "VALIDATION_ERROR", message, details=details)


def _invalid(field_name: str, message: str) -> AppError:
    return _validation_error([{"loc": ["body", field_name], "message": message}])


def starts_before_project(project_start: date, new_start: date | None, old_start: date | None) -> bool:
    """書いた後の希望開始日がプロジェクトの開始日より前になるか（5.5・CR-018）。

    今の値のままなら問わない（この版より前に保存された、開始日より前の希望開始日は、変えない限りそのまま残せる。5.5）。
    """
    return new_start is not None and new_start < project_start and new_start != old_start


def before_project_start(
    project_start: date, early: Iterable[tuple[Mapping[str, Any], date]], message: str | None = None
) -> AppError:
    """開始日より前の希望開始日になる書き込みを拒む 422 ``BEFORE_PROJECT_START``（何も書かない。5.5・CR-018）。

    ``early`` は（どこか、開始日より前の希望開始日）の組。``details`` はどこかに希望開始日・開始日・理由を足したもの。
    """
    details = [
        {**where, "requested_start": start.isoformat(), "project_start": project_start.isoformat(),
         "message": f"希望開始日 {start.isoformat()} がプロジェクトの開始日 {project_start.isoformat()} より前です"}
        for where, start in early
    ]
    return AppError(422, "BEFORE_PROJECT_START",
                    message or f"希望開始日はプロジェクトの開始日（{project_start.isoformat()}）以降にします",
                    details=details)


def _cycle_error(activity_ids: Iterable[int]) -> AppError:
    ids = list(activity_ids)
    return AppError(422, "SCHEDULE_CYCLE", "依存が循環しています", details=[{"activity_ids": ids}])


@dataclass(frozen=True)
class Plan:
    """1 つの操作で読む計画の全体（WBS 要素・活動・依存は ID の順）。"""

    project: Project
    calendar: WorkCalendar
    elements: list[WbsElement]
    activities: list[Activity]
    dependencies: list[ActivityDependency]

    @property
    def element_by_id(self) -> dict[int, WbsElement]:
        return {e.id: e for e in self.elements}

    @property
    def activity_by_id(self) -> dict[int, Activity]:
        return {a.id: a for a in self.activities}

    @property
    def pairs(self) -> list[tuple[int, int]]:
        return [(d.predecessor_id, d.successor_id) for d in self.dependencies]

    def ordered_activities(self) -> list[Activity]:
        """行の順：WBS の並び（先行順・兄弟は表示順）→ WP の中の ``sort_order`` → id（RULE-SCH-005 の行の順と同じ）。"""
        preorder = display_preorder(
            CodedElement(id=e.id, parent_id=e.parent_id, wbs_code=e.wbs_code, sibling_seq=e.sibling_seq,
                         sort_order=e.sort_order)
            for e in self.elements
        )
        rank = {element_id: i for i, element_id in enumerate(preorder)}
        return sorted(self.activities, key=lambda a: (rank[a.work_package_id], a.sort_order, a.id))


def work_calendar(session: Session, project_id: int) -> WorkCalendar:
    """プロジェクトの稼働カレンダー（4.1 の calendars・calendar_holidays）。行が無ければ 4.1 の既定（月〜金）。"""
    calendar = session.scalar(select(Calendar).where(Calendar.project_id == project_id))
    if calendar is None:
        return WorkCalendar.of(WORKING_WEEKDAYS_DEFAULT)
    holidays = session.scalars(select(CalendarHoliday.holiday_date).where(CalendarHoliday.calendar_id == calendar.id))
    return WorkCalendar.of(calendar.working_weekdays, holidays)


def load_plan(session: Session, project_id: int) -> Plan:
    project = session.get(Project, project_id)
    if project is None:  # authorize_project が先に 404 を返すので、ここへは来ない
        raise AppError(404, "NOT_FOUND", "プロジェクトが見つかりません", details=[{"project_id": project_id}])
    return Plan(
        project=project,
        calendar=work_calendar(session, project_id),
        elements=list(session.scalars(select(WbsElement).where(WbsElement.project_id == project_id).order_by(WbsElement.id))),
        activities=list(session.scalars(select(Activity).where(Activity.project_id == project_id).order_by(Activity.id))),
        dependencies=list(
            session.scalars(
                select(ActivityDependency).where(ActivityDependency.project_id == project_id).order_by(ActivityDependency.id)
            )
        ),
    )


def _schedule_of(plan: Plan) -> Schedule:
    """計画の日程を計算する（RULE-SCH-001〜003。保存しない）。循環があれば 422 ``SCHEDULE_CYCLE``。"""
    try:
        return calculate_schedule(
            [
                ScheduleActivity(id=a.id, duration_days=a.duration_days, is_milestone=a.is_milestone,
                                 requested_start=a.requested_start)
                for a in plan.activities
            ],
            plan.pairs,
            calendar=plan.calendar,
            project_start=plan.project.start_date,
        )
    except ScheduleCycleError as exc:
        raise _cycle_error(exc.activity_ids) from exc


def calculate_and_store(session: Session, project_id: int) -> tuple[Plan, Schedule]:
    """プロジェクトの全ての活動の日程を計算し直し、4.1 の計算結果の列へ書く（コミットは呼び手が行う。SCH-06）。"""
    session.flush()
    plan = load_plan(session, project_id)
    schedule = _schedule_of(plan)
    for activity in plan.activities:
        s = schedule.activities[activity.id]
        activity.start_date = s.start_date
        activity.finish_date = s.finish_date
        activity.early_start = s.early_start
        activity.early_finish = s.early_finish
        activity.late_start = s.late_start
        activity.late_finish = s.late_finish
        activity.total_float = s.total_float
        activity.is_critical = s.is_critical
    session.flush()
    return plan, schedule


class ScheduleCalculator:
    """日程を計算して保存する ``ScheduleRecalculator``（``app.services.projects``。API-PRJ-004・008 の自動反映。A-19 の差し替え）。"""

    def recalculate(self, session: Session, project_id: int) -> None:
        calculate_and_store(session, project_id)


def _visible_activity_ids(
    session: Session, access: ProjectAccess, actor: User, project_id: int, table: Mapping[str, frozenset[str]],
    activities: Sequence[Activity],
) -> frozenset[int] | None:
    """project.view で読める活動。全て読めるなら ``None``。委託先だけで読む利用者は自分の割当の活動（9.2 の注記）。"""
    membership = access.membership(session, project_id, actor.id)
    level = project_view_level(principal_of(actor), role_permissions=table, membership=membership)
    if level is ViewLevel.ALL:
        return None
    assigned = membership.assigned_activity_ids if membership is not None else frozenset()
    existing = {a.id for a in activities}
    return frozenset(a for a in assigned if a in existing)


def _activity_of(session: Session, project_id: int, activity_id: int) -> Activity:
    activity = session.get(Activity, activity_id)
    if activity is None or activity.project_id != project_id:
        raise AppError(404, "NOT_FOUND", "活動が見つかりません", details=[{"activity_id": activity_id}])
    return activity


@dataclass(frozen=True)
class AssigneeView:
    user_id: int
    display_name: str
    planned_hours: Decimal | None


def _assignees_by_activity(session: Session, links: ScheduleLinks, project_id: int) -> dict[int, tuple[AssigneeView, ...]]:
    refs = list(links.assignments.assignments(session, project_id))
    user_ids = {r.user_id for r in refs}
    names = (
        {u.id: u.display_name for u in session.scalars(select(User).where(User.id.in_(user_ids)))} if user_ids else {}
    )
    grouped: dict[int, list[AssigneeView]] = {}
    for r in sorted(refs, key=lambda r: (r.activity_id, r.user_id)):
        grouped.setdefault(r.activity_id, []).append(AssigneeView(r.user_id, names[r.user_id], r.planned_hours))
    return {activity_id: tuple(views) for activity_id, views in grouped.items()}


@dataclass(frozen=True)
class ActivityView:
    """活動 1 件の出力の元（置き場の WP・先行と後続・担当）。"""

    activity: Activity
    work_package: WbsElement
    predecessor_ids: tuple[int, ...]
    successor_ids: tuple[int, ...]
    assignees: tuple[AssigneeView, ...]


def _views(
    session: Session, links: ScheduleLinks, plan: Plan, activities: Sequence[Activity],
    visible: frozenset[int] | None = None,
) -> list[ActivityView]:
    """活動の出力の元。``visible`` を渡したら、先行・後続の相手は読める活動だけ（9.2 の注記）。"""
    elements = plan.element_by_id
    assignees = _assignees_by_activity(session, links, plan.project.id)
    preds: dict[int, list[int]] = {}
    succs: dict[int, list[int]] = {}
    for p, s in plan.pairs:
        if visible is not None and (p not in visible or s not in visible):
            continue
        preds.setdefault(s, []).append(p)
        succs.setdefault(p, []).append(s)
    return [
        ActivityView(
            activity=a,
            work_package=elements[a.work_package_id],
            predecessor_ids=tuple(sorted(preds.get(a.id, ()))),
            successor_ids=tuple(sorted(succs.get(a.id, ()))),
            assignees=assignees.get(a.id, ()),
        )
        for a in activities
    ]


# ---------------------------------------------------------------------------
# 開始日・期間・終了日（SCH-03・RULE-CAL-001）
# ---------------------------------------------------------------------------


def resolve_activity_dates(
    calendar: WorkCalendar,
    *,
    start: date | None,
    finish: date | None,
    duration: int | None,
    is_milestone: bool | None,
) -> tuple[date | None, int]:
    """入力から、保存する希望開始日と期間を求める（SCH-03・BR-04）。

    - マイルストーン（``is_milestone`` が真）は期間 0。日付は開始日か終了日の 1 つ（両方は 400）
    - 期間 0 はマイルストーン（``is_milestone`` を偽と明示して期間 0 は 400）
    - 終了日を与えたら、開始日か期間のどちらか 1 つと組にして残りを求める（3 つ全ては 400。終了日だけは 400）
    - 終了日が無ければ期間が要る。開始日は空でよい（プロジェクトの開始日と先行から決まる。5.5）
    - 開始日は次の稼働日へ、終了日は前の稼働日へ寄せる。寄せた終了日が開始日より前なら 400
    """
    if is_milestone is True:
        if duration not in (None, 0):
            raise _invalid("duration_days", "マイルストーンの期間は 0 です")
        duration = 0
    elif is_milestone is False and duration == 0:
        raise _invalid("duration_days", "期間 0 の活動はマイルストーンです（is_milestone を真にします）")
    if duration is not None and duration < 0:
        raise _invalid("duration_days", "期間（稼働日）は 0 以上です")
    try:
        if finish is None:
            if duration is None:
                raise _invalid("duration_days", "期間（稼働日）を指定します（または開始日と終了日）")
            if start is None:
                return None, duration
            return calendar.resolve_dates(start=start, duration=duration).start, duration
        if start is not None and duration is not None:
            raise _invalid("requested_finish", "開始日・期間・終了日のうち 2 つを指定します（3 つは指定しません）")
        if start is None and duration is None:
            raise _invalid("requested_finish", "終了日だけでは決まりません（開始日か期間も指定します）")
        resolved = calendar.resolve_dates(start=start, duration=duration, end=finish)
    except CalendarError as exc:
        raise _invalid("requested_finish", f"日付を決められません: {exc}") from exc
    return resolved.start, resolved.duration


# ---------------------------------------------------------------------------
# 活動（API-SCH-001〜005）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ActivityListView:
    items: list[ActivityView]
    total: int
    dependencies: list[ActivityDependency]
    restricted: bool


def list_activities(
    session: Session, access: ProjectAccess, actor: User, links: ScheduleLinks, project_id: int, *, limit: int,
    offset: int,
) -> ActivityListView:
    """活動の一覧（project.view）。行の順は RULE-SCH-005 と同じ。依存・先行と後続の相手は読める活動どうしのもの。"""
    table = authorize_project(session, access, actor, project_id, "project.view")
    plan = load_plan(session, project_id)
    visible = _visible_activity_ids(session, access, actor, project_id, table, plan.activities)
    ordered = [a for a in plan.ordered_activities() if visible is None or a.id in visible]
    page = ordered[offset:offset + limit]
    dependencies = [
        d for d in plan.dependencies
        if visible is None or (d.predecessor_id in visible and d.successor_id in visible)
    ]
    return ActivityListView(items=_views(session, links, plan, page, visible), total=len(ordered),
                            dependencies=dependencies, restricted=visible is not None)


def _ensure_on_work_package(plan: Plan, activity_refs: Sequence[ActivityRef]) -> None:
    """活動は WP にだけ置ける（RULE-WBS-003）。違えば 422 ``ACTIVITY_NOT_ON_WORK_PACKAGE``。"""
    nodes = [WbsNode(id=e.id, parent_id=e.parent_id, kind=e.kind, own_estimate_hours=e.own_estimate_hours)  # type: ignore[arg-type]
             for e in plan.elements]
    try:
        ensure_hierarchy(nodes, activity_refs)
    except WbsRuleError as exc:
        details = [{"code": i.code.value, "element_ids": list(i.element_ids), "activity_id": i.activity_id,
                    "message": i.message} for i in exc.details]
        raise AppError(422, exc.code, str(exc), details=details) from exc


def add_activity(
    session: Session,
    access: ProjectAccess,
    actor: User,
    links: ScheduleLinks,
    project_id: int,
    *,
    work_package_id: int,
    name: str,
    requested_start: date | None = None,
    requested_finish: date | None = None,
    duration_days: int | None = None,
    is_milestone: bool | None = None,
) -> ActivityView:
    """活動を WP の配下に足し（schedule.edit）、日程を計算し直す。

    置き場がこのプロジェクトの WBS 要素でなければ 400、WP でなければ 422 ``ACTIVITY_NOT_ON_WORK_PACKAGE``（RULE-WBS-003）。
    表示順は WP の中の末尾。次の稼働日へ寄せた希望開始日がプロジェクトの開始日より前なら 422 ``BEFORE_PROJECT_START``（CR-018）。
    """
    authorize_project(session, access, actor, project_id, "schedule.edit")
    # プロジェクトの開始日（希望開始日の下限。CR-018）を、開始日の変更（API-PRJ-004）と同時に変えないようロックしてから読む（A-49 の 3）
    lock_row(session, Project, project_id)
    plan = load_plan(session, project_id)
    if work_package_id not in plan.element_by_id:
        raise _invalid("work_package_id", "このプロジェクトの WBS 要素ではありません")
    _ensure_on_work_package(plan, [*(ActivityRef(id=a.id, work_package_id=a.work_package_id) for a in plan.activities),
                                   ActivityRef(id=-1, work_package_id=work_package_id)])
    start, duration = resolve_activity_dates(plan.calendar, start=requested_start, finish=requested_finish,
                                             duration=duration_days, is_milestone=is_milestone)
    if start is not None and starts_before_project(plan.project.start_date, start, None):
        raise before_project_start(plan.project.start_date, [({"loc": ["body", "requested_start"]}, start)])
    siblings = [a.sort_order for a in plan.activities if a.work_package_id == work_package_id]
    activity = Activity(
        project_id=project_id,
        work_package_id=work_package_id,
        name=name,
        sort_order=max(siblings, default=0) + 1,
        requested_start=start,
        duration_days=duration,
        is_milestone=duration == 0,
    )
    session.add(activity)
    plan, _ = calculate_and_store(session, project_id)
    session.commit()
    return _views(session, links, plan, [activity])[0]


def update_activity(
    session: Session,
    access: ProjectAccess,
    actor: User,
    links: ScheduleLinks,
    project_id: int,
    activity_id: int,
    changes: Mapping[str, Any],
) -> ActivityView:
    """活動の名称・日付・期間・マイルストーンを変え（schedule.edit）、日程を計算し直す。載せた項目だけを変える。

    終了日だけを載せたら期間を保って開始日を動かす。開始日か期間と組なら残りを求める（SCH-03）。
    求めた希望開始日が今と違い、プロジェクトの開始日より前なら 422 ``BEFORE_PROJECT_START``（CR-018）。
    """
    authorize_project(session, access, actor, project_id, "schedule.edit")
    # プロジェクトの開始日（希望開始日の下限。CR-018）を、開始日の変更（API-PRJ-004）と同時に変えないようロックしてから読む（A-49 の 3）
    lock_row(session, Project, project_id)
    activity = _activity_of(session, project_id, activity_id)
    unknown = set(changes) - UPDATABLE_FIELDS
    if unknown:
        raise ValueError(f"変えられない項目: {sorted(unknown)}")
    nulls = [n for n in sorted(_NON_NULLABLE_FIELDS & set(changes)) if changes[n] is None]
    if nulls:
        raise _validation_error([{"loc": ["body", n], "message": "空にできません"} for n in nulls])
    if {"requested_start", "requested_finish", "duration_days", "is_milestone"} & set(changes):
        is_milestone = changes.get("is_milestone")
        if "duration_days" in changes:
            duration: int | None = changes["duration_days"]
        elif is_milestone is True:
            duration = 0
        elif is_milestone is False and activity.duration_days == 0:
            duration = None  # マイルストーンをやめるなら期間が要る
        else:
            duration = activity.duration_days
        finish = changes.get("requested_finish")
        if finish is not None:
            start = changes.get("requested_start")
            if "requested_start" in changes and "duration_days" not in changes and is_milestone is None:
                duration = None  # 開始日と終了日の組
        else:
            start = changes["requested_start"] if "requested_start" in changes else activity.requested_start
        calendar = work_calendar(session, project_id)
        new_start, new_duration = resolve_activity_dates(calendar, start=start, finish=finish, duration=duration,
                                                         is_milestone=is_milestone)
        project = session.get(Project, project_id)
        assert project is not None  # authorize_project が無いプロジェクトを 404 にしてある
        if new_start is not None and starts_before_project(project.start_date, new_start, activity.requested_start):
            field_name = "requested_start" if "requested_start" in changes else "requested_finish"
            raise before_project_start(project.start_date, [({"loc": ["body", field_name]}, new_start)])
        activity.requested_start = new_start
        activity.duration_days = new_duration
        activity.is_milestone = new_duration == 0
    if "name" in changes:
        activity.name = changes["name"]
    plan, _ = calculate_and_store(session, project_id)
    session.commit()
    return _views(session, links, plan, [activity])[0]


def _assignable_user_ids(session: Session, project_id: int) -> frozenset[int]:
    """担当にできる利用者：そのプロジェクトのメンバーである有効な利用者（EFF-01・WBS-10）。"""
    rows = session.scalars(
        select(ProjectMember.user_id)
        .join(User, User.id == ProjectMember.user_id)
        .where(ProjectMember.project_id == project_id, User.is_active.is_(True))
    )
    return frozenset(rows)


def bulk_update_activities(
    session: Session,
    access: ProjectAccess,
    actor: User,
    links: ScheduleLinks,
    project_id: int,
    changes: Sequence[Mapping[str, Any]],
) -> list[ActivityView]:
    """複数の活動の名称・希望開始日・期間・先行・担当をまとめて変える（schedule.edit。SCH-02）。

    RULE-SCH-006 で全てを検査してから適用する。1 件でも不正なら何も保存せず 422（循環だけなら ``SCHEDULE_CYCLE``、
    それ以外は ``BULK_UPDATE_INVALID``。``details`` は見つけた不正の全て）。通った後、希望開始日を今と違う値にして
    プロジェクトの開始日より前になる活動があれば何も保存せず 422 ``BEFORE_PROJECT_START``（``details`` はその全て。CR-018）。
    通れば 1 回で保存し、日程を計算し直す。
    """
    authorize_project(session, access, actor, project_id, "schedule.edit")
    # プロジェクトの開始日（希望開始日の下限。CR-018）を、開始日の変更（API-PRJ-004）と同時に変えないようロックしてから読む（A-49 の 3）
    lock_row(session, Project, project_id)
    plan = load_plan(session, project_id)
    preds: dict[int, set[int]] = {a.id: set() for a in plan.activities}
    for p, s in plan.pairs:
        preds[s].add(p)
    assignees: dict[int, set[int]] = {a.id: set() for a in plan.activities}
    for ref in links.assignments.assignments(session, project_id):
        assignees.setdefault(ref.activity_id, set()).add(ref.user_id)
    current = [
        EditableActivity(id=a.id, name=a.name, requested_start=a.requested_start, duration_days=a.duration_days,
                         predecessor_ids=frozenset(preds[a.id]), assignee_user_ids=frozenset(assignees[a.id]))
        for a in plan.activities
    ]
    domain_changes = [
        ActivityChange(
            activity_id=c["activity_id"],
            name=c.get("name", UNSET),
            requested_start=c.get("requested_start", UNSET),
            duration_days=c.get("duration_days", UNSET),
            predecessor_ids=c.get("predecessor_ids", UNSET),
            assignee_user_ids=c.get("assignee_user_ids", UNSET),
        )
        for c in changes
    ]
    try:
        result = check_bulk_update(current, domain_changes, assignable_user_ids=_assignable_user_ids(session, project_id))
    except BulkUpdateError as exc:
        only_cycles = all(i.code is BulkIssueCode.SCHEDULE_CYCLE for i in exc.issues)
        details = [
            {"index": i.index, "activity_id": i.activity_id, "field": i.field, "code": i.code.value,
             "message": i.message, "related_ids": list(i.related_ids)}
            for i in exc.issues
        ]
        raise AppError(422, "SCHEDULE_CYCLE" if only_cycles else "BULK_UPDATE_INVALID",
                       "一括更新に不正があるため、全件を保存しませんでした", details=details) from exc

    by_id = plan.activity_by_id
    index_of = {c["activity_id"]: index for index, c in enumerate(changes)}
    early = [
        ({"index": index_of[e.id], "activity_id": e.id, "field": "requested_start"}, e.requested_start)
        for e in result.activities
        if e.requested_start is not None
        and starts_before_project(plan.project.start_date, e.requested_start, by_id[e.id].requested_start)
    ]
    if early:
        raise before_project_start(plan.project.start_date, early)
    for edited in result.activities:
        activity = by_id[edited.id]
        activity.name = edited.name
        activity.requested_start = edited.requested_start
        activity.duration_days = edited.duration_days
        activity.is_milestone = edited.is_milestone
    for p, s in result.removed_dependencies:
        session.execute(
            delete(ActivityDependency).where(
                ActivityDependency.project_id == project_id,
                ActivityDependency.predecessor_id == p,
                ActivityDependency.successor_id == s,
            )
        )
    for p, s in result.added_dependencies:
        session.add(ActivityDependency(project_id=project_id, predecessor_id=p, successor_id=s))
    for c in changes:
        if "assignee_user_ids" in c:
            links.assignments.replace_assignees(session, c["activity_id"], frozenset(c["assignee_user_ids"]))
    plan, _ = calculate_and_store(session, project_id)
    session.commit()
    refreshed = plan.activity_by_id
    return _views(session, links, plan, [refreshed[e.id] for e in result.activities])


def remove_activity(
    session: Session, access: ProjectAccess, actor: User, links: ScheduleLinks, project_id: int, activity_id: int
) -> None:
    """活動を消し（schedule.edit）、日程を計算し直す。

    依存の行と割当を消す。ベースライン・スナップショットの写しは変えない（写しの値。設計書 1.1 の 4.2。CR-002）。
    今の計画に無い活動の ID が SCH-18 の「削除された活動」になる。進捗の算定項目・入力も消す（A-26）。
    実績工数（``time_entries``）のある活動は消さずに 409 ``INVALID_STATE``（実績を黙って消すと EFF-02 の集計が変わる。OQ-26 (2)・A-30）。
    是正措置が指す活動は 422 ``ACTIVITY_REFERENCED``（変更要求の反映と同じ。外部キー違反の 500 にしない。A-47）。
    """
    authorize_project(session, access, actor, project_id, "schedule.edit")
    activity = _activity_of(session, project_id, activity_id)
    if session.scalar(select(TimeEntry.id).where(TimeEntry.activity_id == activity_id).limit(1)) is not None:
        raise AppError(409, "INVALID_STATE", "実績工数のある活動は消せません",
                       details=[{"activity_id": activity_id, "reason": "HAS_TIME_ENTRIES"}])
    if session.scalar(select(CorrectiveAction.id).where(CorrectiveAction.activity_id == activity_id).limit(1)) is not None:
        raise AppError(422, "ACTIVITY_REFERENCED", "是正措置が指す活動は消せません", details=[{"activity_ids": [activity_id]}])
    session.execute(
        delete(ActivityDependency).where(
            or_(ActivityDependency.predecessor_id == activity_id, ActivityDependency.successor_id == activity_id)
        )
    )
    # 進捗：算定項目と各報告日の入力を消す。スナップショットの写しは変えない
    # （過去の線は写しだけから描く。BR-13。設計書 1.1 の 4.2、docs/decisions/ASSUMPTIONS.md A-26、OQ-23 (3)）
    session.execute(delete(ProgressItem).where(ProgressItem.activity_id == activity_id))
    session.execute(delete(ProgressEntry).where(ProgressEntry.activity_id == activity_id))
    links.assignments.remove_activity(session, activity_id)
    session.delete(activity)
    calculate_and_store(session, project_id)
    session.commit()


# ---------------------------------------------------------------------------
# 依存（API-SCH-006・007）
# ---------------------------------------------------------------------------


def add_dependency(
    session: Session,
    access: ProjectAccess,
    actor: User,
    project_id: int,
    *,
    predecessor_id: int,
    successor_id: int,
    dep_type: str = DEPENDENCY_TYPE_FS,
    lag_days: int = DEPENDENCY_LAG_R1,
) -> ActivityDependency:
    """依存（終了→開始）を足し（schedule.edit）、日程を計算し直す（SCH-04）。

    両端がこのプロジェクトの活動でなければ 400、FS・ラグ 0 以外は 422 ``UNSUPPORTED_DEPENDENCY``（R1。OQ-04）、
    同じ組が既にあれば 409 ``DUPLICATE_DEPENDENCY``、足すと循環するなら 422 ``SCHEDULE_CYCLE``（RULE-SCH-001。循環の活動を返す）。
    """
    authorize_project(session, access, actor, project_id, "schedule.edit")
    plan = load_plan(session, project_id)
    ids = set(plan.activity_by_id)
    missing = [
        {"loc": ["body", loc], "message": "このプロジェクトの活動ではありません"}
        for loc, value in (("predecessor_id", predecessor_id), ("successor_id", successor_id)) if value not in ids
    ]
    if missing:
        raise _validation_error(missing)
    if dep_type != DEPENDENCY_TYPE_FS or lag_days != DEPENDENCY_LAG_R1:
        raise AppError(422, "UNSUPPORTED_DEPENDENCY", "R1 の依存は終了→開始（FS）・ラグ 0 だけです",
                       details=[{"dep_type": dep_type, "lag_days": lag_days}])
    if (predecessor_id, successor_id) in set(plan.pairs):
        raise AppError(409, "DUPLICATE_DEPENDENCY", "同じ依存が既にあります",
                       details=[{"predecessor_id": predecessor_id, "successor_id": successor_id}])
    cycle = DependencyGraph(ids, plan.pairs).cycle_if_added(predecessor_id, successor_id)
    if cycle:
        raise _cycle_error(cycle)
    dependency = ActivityDependency(project_id=project_id, predecessor_id=predecessor_id, successor_id=successor_id)
    session.add(dependency)
    calculate_and_store(session, project_id)
    session.commit()
    return dependency


def remove_dependency(session: Session, access: ProjectAccess, actor: User, project_id: int, dependency_id: int) -> None:
    """依存を消し（schedule.edit）、日程を計算し直す。このプロジェクトに無ければ 404。"""
    authorize_project(session, access, actor, project_id, "schedule.edit")
    dependency = session.get(ActivityDependency, dependency_id)
    if dependency is None or dependency.project_id != project_id:
        raise AppError(404, "NOT_FOUND", "依存が見つかりません", details=[{"dependency_id": dependency_id}])
    session.delete(dependency)
    calculate_and_store(session, project_id)
    session.commit()


# ---------------------------------------------------------------------------
# 日程計算・クリティカルパス（API-SCH-008・009）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScheduleView:
    """日程計算の結果（活動は行の順。``critical_path`` はクリティカルな活動のトポロジカル順）。"""

    plan: Plan
    schedule: Schedule
    activities: list[Activity]
    critical_path: list[Activity]
    restricted: bool


def _schedule_view(plan: Plan, schedule: Schedule, visible: frozenset[int] | None) -> ScheduleView:
    by_id = plan.activity_by_id
    return ScheduleView(
        plan=plan,
        schedule=schedule,
        restricted=visible is not None,
        activities=[a for a in plan.ordered_activities() if visible is None or a.id in visible],
        critical_path=[by_id[i] for i in schedule.critical_path if visible is None or i in visible],
    )


def recalculate(session: Session, access: ProjectAccess, actor: User, project_id: int) -> ScheduleView:
    """日程を計算し直して保存する（schedule.edit。SCH-06・SCH-10）。循環があれば 422 ``SCHEDULE_CYCLE``。"""
    authorize_project(session, access, actor, project_id, "schedule.edit")
    plan, schedule = calculate_and_store(session, project_id)
    session.commit()
    return _schedule_view(plan, schedule, None)


def critical_path(session: Session, access: ProjectAccess, actor: User, project_id: int) -> ScheduleView:
    """クリティカルパスとフロート（project.view。SCH-10）。今の入力から計算する（保存しない）。"""
    table = authorize_project(session, access, actor, project_id, "project.view")
    plan = load_plan(session, project_id)
    schedule = _schedule_of(plan)
    visible = _visible_activity_ids(session, access, actor, project_id, table, plan.activities)
    return _schedule_view(plan, schedule, visible)


# ---------------------------------------------------------------------------
# ガント（API-SCH-010）
# ---------------------------------------------------------------------------


def _baselines_of(session: Session, project_id: int) -> list[ScheduleBaseline]:
    return list(
        session.scalars(select(ScheduleBaseline).where(ScheduleBaseline.project_id == project_id)
                        .order_by(ScheduleBaseline.version))
    )


def _baseline_not_found(baseline_id: int) -> AppError:
    return AppError(404, "NOT_FOUND", "スケジュールベースラインが見つかりません", details=[{"baseline_id": baseline_id}])


def _activity_items(session: Session, baseline_id: int) -> list[ScheduleBaselineItem]:
    return list(
        session.scalars(
            select(ScheduleBaselineItem)
            .where(ScheduleBaselineItem.schedule_baseline_id == baseline_id, ScheduleBaselineItem.item_type == "ACTIVITY")
            .order_by(ScheduleBaselineItem.id)
        )
    )


@dataclass(frozen=True)
class GanttView:
    chart: GanttChart
    project_start: date
    baseline: ScheduleBaseline | None
    report_date: date | None
    restricted: bool


def gantt(
    session: Session, access: ProjectAccess, actor: User, links: ScheduleLinks, project_id: int,
    baseline_id: int | None = None, *, range_start: date | None = None, range_end: date | None = None,
) -> GanttView:
    """ガントの描画データ（project.view。SCH-11・12・13）。

    基準のバーは ``baseline_id`` の版（省けば最新の版。版が無ければ出さない。SCH-17）。他のプロジェクトの版・無い版は 404。
    実績のバーと進捗率は最新の進捗（``latest_progress``）、未完了の実績はその報告日まで描く。
    非稼働日は表示範囲 ``range_start``〜``range_end`` の分（省いた端はバーの範囲の端。設計書 1.1・CR-008）。
    """
    table = authorize_project(session, access, actor, project_id, "project.view")
    plan = load_plan(session, project_id)
    visible = _visible_activity_ids(session, access, actor, project_id, table, plan.activities)
    progress = links.latest_progress(session, project_id)
    chart, baseline = plan_gantt(session, links, plan, visible=visible, progress=progress, baseline_id=baseline_id,
                                 range_start=range_start, range_end=range_end)
    return GanttView(chart=chart, project_start=plan.project.start_date, baseline=baseline,
                     report_date=progress.report_date, restricted=visible is not None)


def plan_gantt(
    session: Session, links: ScheduleLinks, plan: Plan, *, visible: frozenset[int] | None, progress: LatestProgress,
    baseline_id: int | None = None, range_start: date | None = None, range_end: date | None = None,
) -> tuple[GanttChart, ScheduleBaseline | None]:
    """計画のガント（RULE-SCH-005）と基準のバーを取った版（権限は呼び手が確かめる）。

    ``visible`` は見せる活動（``None`` は全て）。実績のバー・進捗率は ``progress``（その報告日まで描く）。
    基準は ``baseline_id`` の版（省けば最新の版。版が無ければ基準のバーは無い）。無い版・他のプロジェクトの版は 404。
    非稼働日は表示範囲 ``range_start``〜``range_end``（省いた端はバーの範囲の端）の分。範囲が不正なら 400（CR-008）。
    """
    project_id = plan.project.id
    baselines = _baselines_of(session, project_id)
    try:
        chosen = select_baseline([BaselineVersion(id=b.id, version=b.version) for b in baselines], baseline_id)
    except BaselineError as exc:
        raise _baseline_not_found(baseline_id or 0) from exc
    baseline = next((b for b in baselines if chosen is not None and b.id == chosen.id), None)
    bars: dict[int, BaselineBarInput] = {}
    if baseline is not None:
        for item in _activity_items(session, baseline.id):
            if item.activity_id is not None:
                bars[item.activity_id] = BaselineBarInput(item.start_date, item.finish_date, item.is_milestone)

    assignees = _assignees_by_activity(session, links, project_id)
    activities = [
        GanttActivity(
            id=a.id,
            work_package_id=a.work_package_id,
            name=a.name,
            sort_order=a.sort_order,
            is_milestone=a.is_milestone,
            start_date=a.start_date,
            finish_date=a.finish_date,
            is_critical=a.is_critical,
            total_float=a.total_float,
            assignees=tuple(v.display_name for v in assignees.get(a.id, ())),
            percent_complete=progress.activities.get(a.id, ActivityProgress()).percent_complete,
            actual_start=progress.activities.get(a.id, ActivityProgress()).actual_start,
            actual_finish=progress.activities.get(a.id, ActivityProgress()).actual_finish,
        )
        for a in plan.activities
        if visible is None or a.id in visible
    ]
    shown = {a.id for a in activities}
    try:
        chart = build_gantt(
            [GanttElement(id=e.id, parent_id=e.parent_id, wbs_code=e.wbs_code, name=e.name, sort_order=e.sort_order,
                          sibling_seq=e.sibling_seq) for e in plan.elements],
            activities,
            [(p, s) for p, s in plan.pairs if p in shown and s in shown],
            calendar=plan.calendar,
            project_start=plan.project.start_date,
            baseline=bars if baseline is not None else None,
            report_date=progress.report_date,
            range_start=range_start,
            range_end=range_end,
        )
    except GanttRangeError as exc:
        raise _validation_error([{"loc": ["query", name], "message": str(exc)} for name in exc.fields]) from exc
    return chart, baseline


# ---------------------------------------------------------------------------
# スケジュールベースライン（API-SCH-011〜013）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BaselineView:
    baseline: ScheduleBaseline
    item_count: int


def save_baseline(
    session: Session, access: ProjectAccess, actor: User, project_id: int, *, name: str, now: datetime
) -> BaselineView:
    """今の日程・見積を新しい版として固定する（baseline.manage。SCH-16・17、BR-12）。

    版はプロジェクトの中の連番（最大＋1。上書きしない）。理由は MANUAL。項目は全ての活動（開始日・終了日・期間・見積）と、
    全ての WP（見積は RULE-EST-001 の WP の集計値、日付は配下の活動の最も早い開始日〜最も遅い終了日。活動が無ければ空）。
    """
    authorize_project(session, access, actor, project_id, "baseline.manage")
    plan = load_plan(session, project_id)
    latest = session.scalar(select(func.max(ScheduleBaseline.version)).where(ScheduleBaseline.project_id == project_id))
    baseline = ScheduleBaseline(project_id=project_id, version=(latest or 0) + 1, name=name,
                                reason=BASELINE_REASON_MANUAL, created_by=actor.id, created_at=now)
    session.add(baseline)
    session.flush()

    elements = plan.element_by_id
    ordered = plan.ordered_activities()
    items: list[ScheduleBaselineItem] = [
        ScheduleBaselineItem(
            schedule_baseline_id=baseline.id, item_type="ACTIVITY", activity_id=a.id, element_id=a.work_package_id,
            wbs_code=elements[a.work_package_id].wbs_code, name=a.name, start_date=a.start_date,
            finish_date=a.finish_date, duration_days=a.duration_days, is_milestone=a.is_milestone,
            estimate_hours=a.estimate_hours,
        )
        for a in ordered
    ]
    rollup = rollup_estimates(
        [WbsNode(id=e.id, parent_id=e.parent_id, kind=e.kind, own_estimate_hours=e.own_estimate_hours)  # type: ignore[arg-type]
         for e in plan.elements],
        [ActivityEstimate(id=a.id, work_package_id=a.work_package_id, estimate_hours=a.estimate_hours)
         for a in plan.activities],
    )
    preorder = display_preorder(
        CodedElement(id=e.id, parent_id=e.parent_id, wbs_code=e.wbs_code, sibling_seq=e.sibling_seq,
                     sort_order=e.sort_order)
        for e in plan.elements
    )
    for element_id in preorder:
        wp = elements[element_id]
        if wp.kind != WORK_PACKAGE:
            continue
        children = [a for a in plan.activities if a.work_package_id == wp.id]
        starts = [a.start_date for a in children if a.start_date is not None]
        finishes = [a.finish_date for a in children if a.finish_date is not None]
        items.append(
            ScheduleBaselineItem(
                schedule_baseline_id=baseline.id, item_type="WORK_PACKAGE", activity_id=None, element_id=wp.id,
                wbs_code=wp.wbs_code, name=wp.name, start_date=min(starts, default=None),
                finish_date=max(finishes, default=None), duration_days=None, is_milestone=False,
                estimate_hours=rollup.hours_of(wp.id),
            )
        )
    session.add_all(items)
    session.commit()
    return BaselineView(baseline=baseline, item_count=len(items))


def list_baselines(
    session: Session, access: ProjectAccess, actor: User, project_id: int, *, limit: int, offset: int
) -> tuple[list[BaselineView], int]:
    """スケジュールベースラインの版の一覧（project.view。版の順。SCH-17）。

    委託先だけで読む利用者には、版の項目の数を読める活動の項目とその WP の項目だけで数える（9.2 の注記）。
    """
    table = authorize_project(session, access, actor, project_id, "project.view")
    baselines = _baselines_of(session, project_id)
    page = baselines[offset:offset + limit]
    activities = list(session.scalars(select(Activity).where(Activity.project_id == project_id)))
    visible = _visible_activity_ids(session, access, actor, project_id, table, activities)
    counts: dict[int, int] = {}
    if page:
        counted = (
            select(ScheduleBaselineItem.schedule_baseline_id, func.count(ScheduleBaselineItem.id))
            .where(ScheduleBaselineItem.schedule_baseline_id.in_([b.id for b in page]))
        )
        if visible is not None:
            work_packages = {a.work_package_id for a in activities if a.id in visible}
            counted = counted.where(or_(
                and_(ScheduleBaselineItem.item_type == "ACTIVITY", ScheduleBaselineItem.activity_id.in_(visible)),
                and_(ScheduleBaselineItem.item_type == "WORK_PACKAGE", ScheduleBaselineItem.element_id.in_(work_packages)),
            ))
        counts = dict(
            session.execute(  # type: ignore[arg-type]
                counted.group_by(ScheduleBaselineItem.schedule_baseline_id)
            ).all()
        )
    return [BaselineView(baseline=b, item_count=counts.get(b.id, 0)) for b in page], len(baselines)


@dataclass(frozen=True)
class VarianceView:
    baseline: ScheduleBaseline
    rows: list[ActivityVariance]
    restricted: bool


def variance(
    session: Session, access: ProjectAccess, actor: User, project_id: int, baseline_id: int
) -> VarianceView:
    """基準と最新計画の差異（project.view。SCH-18）。RULE-SCH-004 で活動ごとの開始・終了の差（稼働日）と見積の差、追加・削除。

    行は最新の計画の行の順、その後に削除された活動。委託先には自分の割当の活動の行だけ。
    """
    table = authorize_project(session, access, actor, project_id, "project.view")
    baseline = session.get(ScheduleBaseline, baseline_id)
    if baseline is None or baseline.project_id != project_id:
        raise _baseline_not_found(baseline_id)
    plan = load_plan(session, project_id)
    elements = plan.element_by_id
    rows = compare_with_baseline(
        [
            BaselineActivityItem(activity_id=i.activity_id, wbs_code=i.wbs_code, name=i.name, start_date=i.start_date,
                                 finish_date=i.finish_date, is_milestone=i.is_milestone, estimate_hours=i.estimate_hours)
            for i in _activity_items(session, baseline.id)
        ],
        [
            PlanActivity(activity_id=a.id, wbs_code=elements[a.work_package_id].wbs_code, name=a.name,
                         start_date=a.start_date, finish_date=a.finish_date, is_milestone=a.is_milestone,
                         estimate_hours=a.estimate_hours)
            for a in plan.ordered_activities()
        ],
        calendar=plan.calendar,
    )
    visible = _visible_activity_ids(session, access, actor, project_id, table, plan.activities)
    shown = [r for r in rows if visible is None or (r.activity_id is not None and r.activity_id in visible)]
    return VarianceView(baseline=baseline, rows=shown, restricted=visible is not None)
