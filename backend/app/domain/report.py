"""定例会議レポートの組み立て（設計書 5.1 の RULE-RPT-001、7.4。要求仕様書 RPT-01）。

報告日の値の源：確定済み（``FINALIZED``）の報告日はスナップショット（RULE-PRG-007）の写しだけ、確定前（``OPEN``）は
確定と同じ計算（RULE-PRG-007 の計算を状態を変えずに使う）で求めた値。この値からレポートの 5 つの欄をまとめる（7.4）::

    上段      ガント（RULE-SCH-005）＋イナズマ線（RULE-PRG-005）。表示の範囲は報告日の前後 4 週
    下段左    遅延一覧：ずれの大きい順（δ の小さい順）・遅延理由・完了見込み（RULE-PRG-006 の抽出と見込み）
    下段中    マイルストーンの状況：予定日・見込み日・ずれ（稼働日）
    下段右    主要 KPI：全体の進捗率（見積で重み付けした進捗率の平均）、遅れの件数、EAC と基準の差、
              クリティカルパスの最小フロート、是正措置の期限超過の件数

- イナズマ線は写しの値（ポイント・ずれ・判定）だけから描き、今のガントの行へ置き直す。今のガントに無い活動の頂点は外して
  ``missing_activity_ids`` に返す（RULE-PRG-005 の ``overlay_lightning_lines`` と同じ扱い）
- 遅延一覧・マイルストーンの見込みは、写しの判定とずれを RULE-PRG-006 へ渡して求める（依存の網は渡された日程計算の入力）
- 全体の進捗率 ``Σ(見積 × 進捗率) / Σ見積``（小数 2 桁、四捨五入）。進捗率が未算定の活動は分子・分母の両方から外す。
  見積の合計が 0 なら ``None``
- 期限超過は RULE-CHG-005 の判定を、報告日を「今日」として当てる（RPT-01「報告日時点の」）

判断の記録は docs/decisions/ASSUMPTIONS.md の A-35。純粋に保つ（1.3）。DB・HTTP・時刻・乱数に触れない。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Final

from app.domain.calendar import WorkCalendar, WorkdayAxis
from app.domain.delay import DelayError, DelayInput, analyze_delays
from app.domain.effort import EffortValues
from app.domain.gantt import GanttChart
from app.domain.lightning import (
    DEFAULT_TOLERANCE_DAYS,
    Judgement,
    LightningError,
    LightningLine,
    overlay_lightning_lines,
    restore_lightning_line,
)
from app.domain.scheduling import DependencyInput, ScheduleActivity
from app.domain.snapshot import (
    DEFAULT_EXTRACT_DAYS,
    Snapshot,
    SnapshotActivity,
    SnapshotError,
    StatusReportStatus,
    finalize_status_report,
)
from app.domain.workflow import CorrectiveActionStatus, is_corrective_action_overdue

# 上段のガントの表示の範囲：報告日の前後 4 週（7.4）
REPORT_WINDOW: Final = timedelta(weeks=4)
PERCENT_QUANTUM: Final = Decimal("0.01")
_ZERO: Final = Decimal(0)


class ReportError(ValueError):
    """レポートの組み立ての入力が不正（未定義の入力は推測せずに拒む）。"""


# ---------------------------------------------------------------------------
# 入力
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProgressNote:
    """報告日の進捗の入力のうち、遅延一覧に要る列（4.1 の ``progress_entries.delay_reason``・``forecast_finish``）。"""

    activity_id: int
    delay_reason: str | None = None
    forecast_finish: date | None = None


@dataclass(frozen=True)
class CorrectiveActionDue:
    """是正措置の期限と状態（4.1 の ``corrective_actions.due_date``・``status``）。"""

    corrective_action_id: int
    due_date: date | None
    status: CorrectiveActionStatus | str


# ---------------------------------------------------------------------------
# 出力
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReportWindow:
    """上段のガントの表示の範囲（暦日。両端を含む）。"""

    start: date
    end: date


@dataclass(frozen=True)
class DelayRow:
    """遅延一覧の 1 行。``forecast_finish`` は完了見込み（入力があればその日、無ければ 5.7 の 2 の式の日）。

    ``forecast_entered`` は完了見込み日が入力の値か（偽なら計算の値）。``is_critical`` は日程計算の入力でのクリティカル。
    """

    activity_id: int
    wbs_code: str
    name: str
    deviation: Decimal
    percent_complete: Decimal | None
    planned_finish: date
    forecast_finish: date
    forecast_entered: bool
    delay_reason: str | None
    is_critical: bool


@dataclass(frozen=True)
class MilestoneRow:
    """マイルストーンの状況の 1 行。``slip_days`` は見込み日−予定日（稼働日。終業の位置の差）。"""

    activity_id: int
    wbs_code: str
    name: str
    planned_date: date
    forecast_date: date
    slip_days: int


@dataclass(frozen=True)
class ReportKpi:
    """主要 KPI（7.4）。値を求められないものは ``None``。

    - ``overall_progress``：見積で重み付けした進捗率の平均（0〜100。小数 2 桁）
    - ``behind_count``：判定が遅れの活動の数
    - ``eac_hours``・``baseline_hours``・``eac_variance_hours``：プロジェクトの EAC・基準と、その差（EAC−基準）
    - ``min_critical_float``：クリティカルな活動のトータルフロートの最小（稼働日）
    - ``overdue_action_count``：期限超過の是正措置の数
    """

    overall_progress: Decimal | None
    behind_count: int
    eac_hours: Decimal | None
    baseline_hours: Decimal | None
    eac_variance_hours: Decimal | None
    min_critical_float: int | None
    overdue_action_count: int


@dataclass(frozen=True)
class MeetingReport:
    """定例会議レポートのデータ（API-RPT-001 が返す）。

    ``finalized`` が真なら値はスナップショットの写し、偽なら確定前の計算値。``snapshot`` はその値（確定前は保存しない）。
    ``lightning`` は今のガントの行へ置き直した線、``missing_activity_ids`` は今のガントに無いため外した頂点の活動。
    """

    report_date: date
    finalized: bool
    window: ReportWindow
    snapshot: Snapshot
    gantt: GanttChart
    lightning: LightningLine
    missing_activity_ids: tuple[int | None, ...]
    delays: tuple[DelayRow, ...]
    milestones: tuple[MilestoneRow, ...]
    kpi: ReportKpi


# ---------------------------------------------------------------------------
# 組み立て
# ---------------------------------------------------------------------------


def _status(value: StatusReportStatus | str) -> StatusReportStatus:
    try:
        return StatusReportStatus(value)
    except ValueError as exc:
        raise ReportError(f"報告日の状態が不正: {value!r}") from exc


def _hours(value: object, what: str) -> Decimal:
    if isinstance(value, bool):
        raise ReportError(f"{what}は数で渡す: {value!r}")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ReportError(f"{what}は数で渡す: {value!r}") from exc
    if not result.is_finite() or result < 0:
        raise ReportError(f"{what}は 0 以上の有限の数: {value!r}")
    return result


def _source_snapshot(
    status: StatusReportStatus,
    *,
    report_date: date,
    snapshot: Snapshot | None,
    activities: Sequence[SnapshotActivity],
    calendar: WorkCalendar,
    project_start: date,
    tolerance_days: Decimal | int | str,
    extract_days: Decimal | int | str,
) -> Snapshot:
    """レポートの値の源：確定済みは写し、確定前は確定と同じ計算の値（状態は変えない。保存はしない）。"""
    if status is StatusReportStatus.FINALIZED:
        if snapshot is None:
            raise ReportError("確定済みの報告日にスナップショットが無い")
        if activities:
            raise ReportError("確定済みの報告日は写しだけから組み立てる（今の活動の値は渡さない。BR-13）")
        if snapshot.report_date != report_date:
            raise ReportError(f"スナップショットの報告日 {snapshot.report_date} が報告日 {report_date} と違う")
        return snapshot
    if snapshot is not None:
        raise ReportError("確定前の報告日にスナップショットは無い（確定前は計算値で組み立てる）")
    try:
        return finalize_status_report(
            status=status,
            report_date=report_date,
            activities=activities,
            calendar=calendar,
            project_start=project_start,
            tolerance_days=tolerance_days,
            extract_days=extract_days,
        ).snapshot
    except SnapshotError as exc:
        raise ReportError(f"報告日の計算値を求められない: {exc}") from exc


def _overall_progress(snapshot: Snapshot, estimate_hours: Mapping[int, Decimal | int | str | None]) -> Decimal | None:
    weighted = _ZERO
    total = _ZERO
    for item in snapshot.items:
        raw = estimate_hours.get(item.activity_id)
        weight = _ZERO if raw is None else _hours(raw, f"活動 {item.activity_id} の見積")
        if item.percent_complete is None:
            continue  # 未算定は分子・分母の両方から外す
        weighted += weight * item.percent_complete
        total += weight
    if total == 0:
        return None
    return (weighted / total).quantize(PERCENT_QUANTUM, rounding=ROUND_HALF_UP)


def _overdue_count(actions: Iterable[CorrectiveActionDue], report_date: date) -> int:
    seen: set[int] = set()
    count = 0
    for action in actions:
        if action.corrective_action_id in seen:
            raise ReportError(f"是正措置 {action.corrective_action_id} が 2 つある")
        seen.add(action.corrective_action_id)
        try:
            overdue = is_corrective_action_overdue(action.due_date, action.status, today=report_date)
        except ValueError as exc:  # WorkflowError を含む（未定義の状態・日付でない期限）
            raise ReportError(f"是正措置 {action.corrective_action_id}：{exc}") from exc
        if overdue:
            count += 1
    return count


# RULE-RPT-001 会議レポートの組み立て
def build_meeting_report(
    *,
    status: StatusReportStatus | str,
    report_date: date,
    calendar: WorkCalendar,
    project_start: date,
    gantt: GanttChart,
    schedule_activities: Iterable[ScheduleActivity],
    dependencies: Iterable[DependencyInput],
    snapshot: Snapshot | None = None,
    activities: Sequence[SnapshotActivity] = (),
    tolerance_days: Decimal | int | str = DEFAULT_TOLERANCE_DAYS,
    extract_days: Decimal | int | str = DEFAULT_EXTRACT_DAYS,
    progress_notes: Iterable[ProgressNote] = (),
    estimate_hours: Mapping[int, Decimal | int | str | None] | None = None,
    effort: EffortValues | None = None,
    corrective_actions: Iterable[CorrectiveActionDue] = (),
) -> MeetingReport:
    """報告日の定例会議レポートを組み立てる（7.4・RPT-01）。

    - ``status``：報告日の状態。``FINALIZED`` は ``snapshot``（その報告日の写し）が要り、``activities`` は渡さない。
      ``OPEN`` は ``snapshot`` を渡さず、``activities``（全活動をガントの行の順に。RULE-PRG-007 と同じ入力）と
      ``tolerance_days``・``extract_days``（``projects.settings`` の今の値）から計算値を求める
    - ``gantt``：今の計画のガント（RULE-SCH-005。報告日を渡して組み立てたもの）。イナズマ線はこの行へ置き直す
    - ``schedule_activities``・``dependencies``：今の計画の日程計算の入力（RULE-PRG-006 の影響の計算に使う）
    - ``progress_notes``：報告日の遅延理由・完了見込み日。``estimate_hours``：活動の見積（人時。無ければ 0）
    - ``effort``：プロジェクトの工数の合計（RULE-EFF-002 の ``project``）。``corrective_actions``：プロジェクトの是正措置
    - 日程の循環は ``ScheduleCycleError``（RULE-SCH-001）のまま、それ以外の入力の誤りは ``ReportError``
    """
    state = _status(status)
    source = _source_snapshot(
        state,
        report_date=report_date,
        snapshot=snapshot,
        activities=activities,
        calendar=calendar,
        project_start=project_start,
        tolerance_days=tolerance_days,
        extract_days=extract_days,
    )
    by_activity = {item.activity_id: item for item in source.items}

    notes: dict[int, ProgressNote] = {}
    for note in progress_notes:
        if note.activity_id in notes:
            raise ReportError(f"活動 {note.activity_id} の進捗の入力が 2 つある")
        notes[note.activity_id] = note

    # 上段：写しの値だけから線を作り、今のガントの行へ置き直す
    try:
        line = restore_lightning_line(
            report_date=source.report_date,
            report_line=source.report_line,
            tolerance_days=source.tolerance_days,
            points=[item.recorded_point() for item in source.items],
            calendar=calendar,
            project_start=project_start,
        )
        (placed,) = overlay_lightning_lines([line], {row.activity_id: row.row_index for row in gantt.rows})
    except LightningError as exc:
        raise ReportError(f"イナズマ線を組み立てられない: {exc}") from exc

    # 下段左・中：写しの判定とずれから遅延を抽出し、見込みを当てる（RULE-PRG-006）
    schedule_list = list(schedule_activities)
    scheduled_ids = {a.id for a in schedule_list}
    try:
        analysis = analyze_delays(
            [
                DelayInput(
                    activity_id=item.activity_id,
                    deviation=item.deviation,
                    judgement=item.judgement,
                    percent_complete=item.percent_complete,
                    forecast_finish=notes[item.activity_id].forecast_finish if item.activity_id in notes else None,
                )
                for item in source.items
                if item.activity_id in scheduled_ids  # 写しの後に消した活動は今の網に無い
            ],
            schedule_list,
            dependencies,
            calendar=calendar,
            project_start=project_start,
            report_date=report_date,
            extract_days=source.extract_days,
        )
    except DelayError as exc:
        raise ReportError(f"遅延の影響を求められない: {exc}") from exc

    delays = tuple(
        DelayRow(
            activity_id=impact.activity_id,
            wbs_code=by_activity[impact.activity_id].wbs_code,
            name=by_activity[impact.activity_id].name,
            deviation=impact.deviation,
            percent_complete=impact.percent_complete,
            planned_finish=by_activity[impact.activity_id].finish_date,
            forecast_finish=impact.forecast_finish_date,
            forecast_entered=impact.activity_id in notes and notes[impact.activity_id].forecast_finish is not None,
            delay_reason=notes[impact.activity_id].delay_reason if impact.activity_id in notes else None,
            is_critical=impact.is_critical,
        )
        for impact in analysis.delays
    )

    axis = WorkdayAxis(calendar=calendar, origin=project_start)
    slipped = {s.activity_id: s.after_date for s in analysis.combined_milestone_slips}
    milestones: list[MilestoneRow] = []
    for item in sorted(source.items, key=lambda i: i.row_order):
        if not item.is_milestone:
            continue
        forecast = slipped.get(item.activity_id, item.finish_date)
        milestones.append(
            MilestoneRow(
                activity_id=item.activity_id,
                wbs_code=item.wbs_code,
                name=item.name,
                planned_date=item.finish_date,
                forecast_date=forecast,
                slip_days=axis.report_line(forecast) - axis.report_line(item.finish_date),
            )
        )

    # 下段右：主要 KPI
    critical_floats = [r.total_float for r in gantt.rows if r.is_critical and r.total_float is not None]
    baseline = None if effort is None else effort.baseline
    kpi = ReportKpi(
        overall_progress=_overall_progress(source, estimate_hours or {}),
        behind_count=sum(1 for item in source.items if item.judgement is Judgement.BEHIND),
        eac_hours=None if effort is None else effort.eac,
        baseline_hours=baseline,
        eac_variance_hours=None if effort is None or baseline is None else effort.eac - baseline,
        min_critical_float=min(critical_floats, default=None),
        overdue_action_count=_overdue_count(corrective_actions, report_date),
    )

    return MeetingReport(
        report_date=report_date,
        finalized=state is StatusReportStatus.FINALIZED,
        window=ReportWindow(start=report_date - REPORT_WINDOW, end=report_date + REPORT_WINDOW),
        snapshot=source,
        gantt=gantt,
        lightning=placed.line,
        missing_activity_ids=placed.missing_activity_ids,
        delays=delays,
        milestones=tuple(milestones),
        kpi=kpi,
    )
