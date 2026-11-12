"""進捗（PRG）のユースケース（設計書 6.2 の API-PRG-001〜009、4.2・5.4・5.6・5.7・5.10、9 章。
要求仕様書 PRG-01〜03・05・08〜13・17〜19、EFF-06、BR-06・08・09・13）。

- 権限（RULE-AUTH-001）：報告日を作る・確定するは status_report.manage、進捗・算定項目を書くは progress.enter_own
  （自分の割当の活動。progress.enter_all を持てば全ての活動）、算定方式を変えるは schedule.edit、読むは project.view。
  委託先だけで読む利用者は自分の割当の活動に絞る（9.2 の注記。``restricted`` が真）
- 進捗率は読むたびに RULE-PRG-001 で求める（方式・算定項目・その報告日の入力から）。``progress_entries.percent_complete`` には
  書いた時点の算定値を保存し（直接入力は入力値。実績終了日があれば 100）、算定項目・算定方式を変えたら OPEN の報告日の行を
  求め直す（ガントが読む 4.2 の「最新の値」を食い違わせない）
- イナズマ線・遅延は、OPEN の報告日なら今の計画（``activities`` の日程の列。OQ-03）とその報告日の入力から計算し、
  FINALIZED の報告日ならスナップショットの写しだけから描く（BR-13）
- 確定（RULE-PRG-007）は全ての活動をガントの行の順（RULE-SCH-005）でスナップショットに写し、報告日を FINALIZED にする。
  確定した報告日への入力は 409 ``INVALID_STATE``

**別の群の表を読む口**（``app.state.progress_links``。A-14・A-17・A-21 と同じ形）：

- ``SnapshotEffort``：スナップショットに写す実績工数・ETC（BR-13）。実績工数の表（``time_entries``。DB-EFF-002）と
  ETC の計算（RULE-EFF-001）がまだ無い間は ``EffortTablesAbsent``（実績工数は空、ETC は入力した残工数だけ）。
  db:EFF で実績工数を表から読む ``TimeEntriesEffort`` へ（ETC は入力した残工数だけ。A-27）、rule:EFF で ETC を RULE-EFF-001 で
  求める ``ComputedEtcEffort`` へ差し替えた（A-28）。差し替え忘れは ``backend/tests/api/test_prg.py`` の
  ``test_effort_port_is_wired_once_time_entries_table_exists``・``test_effort_port_computes_etc_once_effort_rules_exist`` が落とす
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from fractions import Fraction
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.authz import Membership, ViewLevel, authorize, effective_permissions, project_view_level
from app.domain.delay import DelayAnalysis, DelayInput, analyze_delays
from app.domain.effort import compute_etc
from app.domain.lightning import (
    LightningActivity,
    LightningError,
    LightningLine,
    LightningVertex,
    OverlaidLine,
    RecordedPoint,
    build_lightning_line,
    overlay_lightning_lines,
    restore_lightning_line,
)
from app.domain.progress import ProgressError, ProgressMethod, exact_percent_complete, round_percent
from app.domain.progress import ProgressItem as RuleProgressItem
from app.domain.scheduling import ScheduleActivity, ScheduleCycleError
from app.domain.snapshot import (
    SnapshotActivity,
    SnapshotError,
    SnapshotErrorCode,
    StatusReportStatus,
    ensure_report_open,
    finalize_status_report,
)
from app.errors import AppError
from app.models.cmn import User
from app.models.eff import TimeEntry
from app.models.prg import ProgressEntry, ProgressItem, ProgressSnapshot, ProgressSnapshotItem, StatusReport
from app.models.prj import Project, default_project_settings
from app.models.sch import Activity
from app.services.auth import authorize_project, forbidden, load_role_permission_table, principal_of, project_not_found
from app.services.project_access import ProjectAccess
from app.services.schedule import Plan, load_plan, progress_entries_latest

ENTER_OWN = "progress.enter_own"
ENTER_ALL = "progress.enter_all"
SOURCE_CALCULATED = "CALCULATED"
SOURCE_SNAPSHOT = "SNAPSHOT"


# ---------------------------------------------------------------------------
# 別の群の表を読む口
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ActivityEffort:
    """スナップショットに写す活動の実績工数と ETC（人時。空は値が無い）。"""

    actual_hours: Decimal | None = None
    etc_hours: Decimal | None = None


class SnapshotEffort(Protocol):
    """報告日の時点の活動の実績工数・ETC を読む（BR-13 の写しの ETC。RULE-EFF-001・DB-EFF-002）。"""

    def effort(
        self, session: Session, project_id: int, report_date: date, entries: Mapping[int, ProgressEntry],
        activity_ids: Sequence[int], percents: Mapping[int, Decimal | None] | None = None,
    ) -> Mapping[int, ActivityEffort]:
        """``activity_ids`` の活動ごとの実績工数・ETC。``entries`` はその報告日の進捗の入力（活動の ID → 行）、
        ``percents`` はその報告日の進捗率（RULE-PRG-001。活動の ID → 値。``None`` は未算定。渡さなければ入力の行の値）。"""
        ...


class EffortTablesAbsent:
    """実績工数の表（``time_entries``。DB-EFF-002）と ETC の計算（RULE-EFF-001）がまだ無い間の ``SnapshotEffort``。

    実績工数は空（表が無いという今のスキーマでの事実）。ETC は担当者が入力した残工数（EFF-06 の「入力した ETC が優先」）だけを写し、
    入力が無ければ空にする（自動の ETC は RULE-EFF-001 の式で、まだ無い。値を作らない）。
    """

    def effort(
        self, session: Session, project_id: int, report_date: date, entries: Mapping[int, ProgressEntry],
        activity_ids: Sequence[int], percents: Mapping[int, Decimal | None] | None = None,
    ) -> Mapping[int, ActivityEffort]:
        return {a: ActivityEffort(actual_hours=None, etc_hours=_entered_etc(entries, a)) for a in activity_ids}


def _entered_etc(entries: Mapping[int, ProgressEntry], activity_id: int) -> Decimal | None:
    """担当者が入力した残工数（EFF-06 の「入力した ETC が優先」）。入力が無ければ空。"""
    entry = entries.get(activity_id)
    return entry.remaining_hours if entry is not None else None


class TimeEntriesEffort:
    """実績工数の表（``time_entries``。DB-EFF-002）を読む ``SnapshotEffort``（db:EFF で差し替えた。A-27）。

    - 実績工数は、その活動の ``time_entries`` の報告日以前の分の合計（5.8）。行が無ければ 0（表があり、実績が無いという事実）
    - ETC は ``EffortTablesAbsent`` と同じく入力した残工数だけを写し、入力が無ければ空にする。自動の ETC（RULE-EFF-001 の式）は
      まだ無いので値を作らない。**RULE-EFF-001（``app/domain/effort.py``）を作るスコープは、ETC を計算する実装へ差し替える**。
      差し替え忘れは ``backend/tests/api/test_prg.py::test_effort_port_computes_etc_once_effort_rules_exist`` が落とす
    """

    def effort(
        self, session: Session, project_id: int, report_date: date, entries: Mapping[int, ProgressEntry],
        activity_ids: Sequence[int], percents: Mapping[int, Decimal | None] | None = None,
    ) -> Mapping[int, ActivityEffort]:
        actual: dict[int, Decimal] = {}
        if activity_ids:
            rows = session.execute(
                select(TimeEntry.activity_id, func.sum(TimeEntry.hours))
                .where(TimeEntry.activity_id.in_(activity_ids), TimeEntry.work_date <= report_date)
                .group_by(TimeEntry.activity_id)
            ).all()
            actual = {activity_id: _decimal(total) for activity_id, total in rows}
        return {
            a: ActivityEffort(actual_hours=actual.get(a, Decimal("0")), etc_hours=_entered_etc(entries, a))
            for a in activity_ids
        }


class ComputedEtcEffort:
    """実績工数を表から読み、ETC を RULE-EFF-001 で求める ``SnapshotEffort``（rule:EFF で差し替えた。A-28）。

    - 実績工数は ``TimeEntriesEffort`` と同じ（その活動の ``time_entries`` の報告日以前の分の合計。行が無ければ 0）
    - ETC は RULE-EFF-001（``app.domain.effort.compute_etc``）：完了（進捗率 100）は 0、担当者の入力した残工数があればそれ、
      無ければ 見積（``activities.estimate_hours``）×（1−進捗率）、進捗率が未算定なら max(見積−実績, 0)
    - 進捗率は ``percents``（確定が RULE-PRG-001 で求めた値）。渡されなければその報告日の入力の行の値（行が無ければ未算定）
    """

    def effort(
        self, session: Session, project_id: int, report_date: date, entries: Mapping[int, ProgressEntry],
        activity_ids: Sequence[int], percents: Mapping[int, Decimal | None] | None = None,
    ) -> Mapping[int, ActivityEffort]:
        actuals = TimeEntriesEffort().effort(session, project_id, report_date, entries, activity_ids)
        estimates: dict[int, Decimal | None] = {}
        if activity_ids:
            rows = session.execute(
                select(Activity.id, Activity.estimate_hours)
                .where(Activity.project_id == project_id, Activity.id.in_(activity_ids))
            ).all()
            estimates = {activity_id: None if hours is None else _decimal(hours) for activity_id, hours in rows}
        result: dict[int, ActivityEffort] = {}
        for a in activity_ids:
            entry = entries.get(a)
            if percents is not None and a in percents:
                percent = percents[a]
            else:
                percent = entry.percent_complete if entry is not None else None
            actual = actuals[a].actual_hours or Decimal("0")
            etc = compute_etc(
                estimate_hours=estimates.get(a),
                percent_complete=None if percent is None else _decimal(percent),
                actual_hours=actual,
                entered_hours=_entered_etc(entries, a),
            )
            result[a] = ActivityEffort(actual_hours=actual, etc_hours=etc.hours)
        return result


@dataclass(frozen=True)
class ProgressLinks:
    """進捗の API が使う、別の群の表の口（``app.state.progress_links``）。既定は「表がまだ無い」。"""

    effort: SnapshotEffort = field(default_factory=EffortTablesAbsent)


# ---------------------------------------------------------------------------
# 共通の部品
# ---------------------------------------------------------------------------


def _invalid(loc: list[Any], message: str) -> AppError:
    return AppError(400, "VALIDATION_ERROR", "入力に誤りがあります", details=[{"loc": loc, "message": message}])


def _invalid_state(message: str, details: list[Any] | None = None) -> AppError:
    return AppError(409, "INVALID_STATE", message, details=details)


def project_settings(project: Project) -> dict[str, Any]:
    """``projects.settings`` に 4.2 の既定を重ねたもの（項目が欠けていれば 4.2 の既定値）。"""
    return {**default_project_settings(), **(project.settings or {})}


def _decimal(value: object) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def effective_method(project: Project, activity: Activity) -> ProgressMethod:
    """活動に効いている算定方式（活動の方式、空ならプロジェクトの既定。4.1）。"""
    return ProgressMethod(activity.progress_method or project_settings(project)["progress_method_default"])


def _report_of(session: Session, project_id: int, report_id: int) -> StatusReport:
    report = session.get(StatusReport, report_id)
    if report is None or report.project_id != project_id:
        raise AppError(404, "NOT_FOUND", "報告日が見つかりません", details=[{"status_report_id": report_id}])
    return report


def _activity_of(session: Session, project_id: int, activity_id: int) -> Activity:
    activity = session.get(Activity, activity_id)
    if activity is None or activity.project_id != project_id:
        raise AppError(404, "NOT_FOUND", "活動が見つかりません", details=[{"activity_id": activity_id}])
    return activity


def _project_of(session: Session, project_id: int) -> Project:
    project = session.get(Project, project_id)
    if project is None:  # 権限の確認が先に 404 を返すので、ここへは来ない
        raise project_not_found(project_id)
    return project


@dataclass(frozen=True)
class _EntryPermission:
    """進捗を書く権限の確認の途中の値（自分の割当の判定に使う）。"""

    table: Mapping[str, frozenset[str]]
    membership: Membership | None


def _check_entry_permission(session: Session, access: ProjectAccess, actor: User, project_id: int) -> _EntryPermission:
    """progress.enter_own か progress.enter_all を持つか（活動を問わない粗い確認）。無いプロジェクトは 404、持たなければ 403。

    活動の有無を権限の無い利用者に見せないため、活動を読む前に確かめる。自分の割当かは ``_check_own_activity`` で見る。
    """
    if not access.project_exists(session, project_id):
        raise project_not_found(project_id)
    table = load_role_permission_table(session, project_id)
    membership = access.membership(session, project_id, actor.id)
    granted = effective_permissions(principal_of(actor), role_permissions=table, membership=membership)
    if not {ENTER_OWN, ENTER_ALL} & granted:
        raise forbidden()
    return _EntryPermission(table=table, membership=membership)


def _check_own_activity(actor: User, permission: _EntryPermission, activity_id: int) -> None:
    """progress.enter_own の活動の判定（RULE-AUTH-001）。自分の割当でなく progress.enter_all も無ければ 403。"""
    decision = authorize(principal_of(actor), ENTER_OWN, role_permissions=permission.table,
                         membership=permission.membership, activity_id=activity_id)
    if not decision.allowed:
        raise forbidden()


def _visible_activity_ids(
    session: Session, access: ProjectAccess, actor: User, project_id: int, table: Mapping[str, frozenset[str]],
    activities: Iterable[Activity],
) -> frozenset[int] | None:
    """project.view で読める活動。全て読めるなら ``None``。委託先だけで読む利用者は自分の割当の活動（9.2 の注記）。"""
    membership = access.membership(session, project_id, actor.id)
    level = project_view_level(principal_of(actor), role_permissions=table, membership=membership)
    if level is ViewLevel.ALL:
        return None
    assigned = membership.assigned_activity_ids if membership is not None else frozenset()
    return frozenset(a.id for a in activities if a.id in assigned)


def _items_of(session: Session, activity_ids: Iterable[int]) -> dict[int, list[ProgressItem]]:
    ids = list(activity_ids)
    grouped: dict[int, list[ProgressItem]] = {a: [] for a in ids}
    if not ids:
        return grouped
    for item in session.scalars(select(ProgressItem).where(ProgressItem.activity_id.in_(ids)).order_by(ProgressItem.id)):
        grouped[item.activity_id].append(item)
    return grouped


def _rule_items(items: Iterable[ProgressItem]) -> list[RuleProgressItem]:
    return [RuleProgressItem(item_type=i.item_type, done=i.done, approved=i.approved, weight=i.weight)  # type: ignore[arg-type]
            for i in items]


def _exact_percent(
    method: ProgressMethod, items: Iterable[ProgressItem], *, entered: Decimal | None, actual_start: date | None,
    actual_finish: date | None,
) -> Fraction | None:
    """RULE-PRG-001 の丸める前の進捗率（進捗ポイント・判定・見込みの終了に使う。5.6）。入力が不正なら 400。"""
    try:
        return exact_percent_complete(method, entered_percent=entered, items=_rule_items(items),
                                      actual_start=actual_start, actual_finish=actual_finish)
    except ProgressError as exc:
        raise _invalid(["body"], str(exc)) from exc


def _percent(
    method: ProgressMethod, items: Iterable[ProgressItem], *, entered: Decimal | None, actual_start: date | None,
    actual_finish: date | None,
) -> Decimal | None:
    """RULE-PRG-001 の進捗率の保存・表示の値（4.1 の精度：小数 2 桁）。入力が不正なら 400。"""
    return round_percent(_exact_percent(method, items, entered=entered, actual_start=actual_start,
                                        actual_finish=actual_finish))


def _entry_exact_percent(
    project: Project, activity: Activity, entry: ProgressEntry | None, items: Iterable[ProgressItem],
) -> Fraction | None:
    """報告日の入力（無ければ入力なし）から求めた活動の丸める前の進捗率（RULE-PRG-001。5.6）。"""
    return _exact_percent(
        effective_method(project, activity), items,
        entered=entry.percent_complete if entry is not None else None,
        actual_start=entry.actual_start if entry is not None else None,
        actual_finish=entry.actual_finish if entry is not None else None,
    )


def _entry_percent(project: Project, activity: Activity, entry: ProgressEntry | None, items: Iterable[ProgressItem]) -> Decimal | None:
    """報告日の入力（無ければ入力なし）から求めた活動の進捗率（RULE-PRG-001。保存・表示の値）。"""
    return round_percent(_entry_exact_percent(project, activity, entry, items))


def report_exact_percents(
    session: Session, project: Project, activities: Iterable[Activity], entries: Mapping[int, ProgressEntry],
) -> dict[int, Fraction | None]:
    """報告日の入力から求めた活動ごとの丸める前の進捗率（RULE-PRG-001。イナズマ線・見込みの終了に使う。5.6・5.7）。"""
    acts = list(activities)
    items = _items_of(session, [a.id for a in acts])
    return {a.id: _entry_exact_percent(project, a, entries.get(a.id), items[a.id]) for a in acts}


def report_percents(
    session: Session, project: Project, activities: Iterable[Activity], entries: Mapping[int, ProgressEntry],
) -> dict[int, Decimal | None]:
    """報告日の入力（``entries``：活動の ID → 行）から求めた活動ごとの進捗率（RULE-PRG-001。``None`` は未算定）。

    工数の集計（5.8 の「進捗率・ETC の入力値は対象の報告日の progress_entries を使う」）が、イナズマ線と同じ値を使うための口。
    値は 4.1 の精度（小数 2 桁）へ丸めたもの（確定の写しと同じ）。
    """
    return {k: round_percent(v) for k, v in report_exact_percents(session, project, activities, entries).items()}


def percents_at_report(
    session: Session, project: Project, activities: Iterable[Activity], report: StatusReport,
    entries: Mapping[int, ProgressEntry],
) -> dict[int, Decimal | None]:
    """報告日の時点の活動ごとの進捗率（5.8 の「対象の報告日の progress_entries を使う」）。

    OPEN の報告日は入力から求め直す（``report_percents``）。確定済み（FINALIZED）の報告日は、確定の後に算定項目・算定方式を
    変えても値を動かさないよう、確定時に求めた写しの値を使う（BR-13。イナズマ線・会議レポートと同じ値。A-47）。
    確定の後に足した活動は写しに無く、その報告日に入力も無いので未算定（``None``）。
    """
    acts = list(activities)
    if report.status != StatusReportStatus.FINALIZED.value:
        return report_percents(session, project, acts, entries)
    snapshot = session.scalar(select(ProgressSnapshot).where(ProgressSnapshot.status_report_id == report.id))
    if snapshot is None:  # 確定と同じトランザクションで作るので、確定済みなら必ずある
        raise RuntimeError(f"確定済みの報告日 {report.id} にスナップショットが無い")
    frozen = {
        i.activity_id: i.percent_complete
        for i in session.scalars(select(ProgressSnapshotItem).where(ProgressSnapshotItem.snapshot_id == snapshot.id))
        if i.activity_id is not None
    }
    return {a.id: frozen.get(a.id) for a in acts}


def entries_of_report(session: Session, report_id: int) -> dict[int, ProgressEntry]:
    """報告日の進捗の入力（活動の ID → 行）。"""
    return _entries_of(session, report_id)


def _refresh_open_entries(session: Session, project: Project, activity: Activity) -> None:
    """算定項目・算定方式を変えた活動の、OPEN の報告日の入力の進捗率を求め直す（確定済みの入力は変えない。BR-13）。"""
    items = _items_of(session, [activity.id])[activity.id]
    entries = session.scalars(
        select(ProgressEntry)
        .join(StatusReport, StatusReport.id == ProgressEntry.status_report_id)
        .where(ProgressEntry.activity_id == activity.id, StatusReport.status == StatusReportStatus.OPEN.value)
    ).all()
    for entry in entries:
        entry.percent_complete = _entry_percent(project, activity, entry, items)


def refresh_open_entries_of_project(session: Session, project: Project) -> None:
    """プロジェクトの既定の算定方式を変えたとき、既定に従う活動（自分の方式が空）の OPEN の報告日の入力の進捗率を求め直す。

    活動ごとに方式を変えたとき（``set_progress_method``）と同じ扱い。ガント（保存値を読む）とイナズマ線（求め直す）の値を
    揃える（確定済みの入力は変えない。BR-13。A-47）。
    """
    activities = session.scalars(
        select(Activity).where(Activity.project_id == project.id, Activity.progress_method.is_(None)).order_by(Activity.id)
    ).all()
    for activity in activities:
        _refresh_open_entries(session, project, activity)


def _entries_of(session: Session, report_id: int) -> dict[int, ProgressEntry]:
    return {e.activity_id: e for e in session.scalars(select(ProgressEntry).where(ProgressEntry.status_report_id == report_id))}


def _require_schedule(activities: Iterable[Activity]) -> None:
    """全ての活動に日程の計算結果（開始日・終了日）があること。無ければ 409（API-SCH-008 で計算し直す）。"""
    missing = [a.id for a in activities if a.start_date is None or a.finish_date is None]
    if missing:
        raise _invalid_state("日程が計算されていない活動があります（日程を計算し直してください）",
                             details=[{"activity_ids": missing}])


# ---------------------------------------------------------------------------
# 報告日（API-PRG-001・002）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReportView:
    report: StatusReport
    entry_count: int
    snapshot_id: int | None


def _report_views(session: Session, reports: Sequence[StatusReport]) -> list[ReportView]:
    ids = [r.id for r in reports]
    counts: dict[int, int] = {}
    snapshots: dict[int, int] = {}
    if ids:
        counts = {
            report_id: count
            for report_id, count in session.execute(
                select(ProgressEntry.status_report_id, func.count(ProgressEntry.id))
                .where(ProgressEntry.status_report_id.in_(ids))
                .group_by(ProgressEntry.status_report_id)
            ).tuples()
        }
        snapshots = {
            report_id: snapshot_id
            for report_id, snapshot_id in session.execute(
                select(ProgressSnapshot.status_report_id, ProgressSnapshot.id).where(ProgressSnapshot.status_report_id.in_(ids))
            ).tuples()
        }
    return [ReportView(report=r, entry_count=counts.get(r.id, 0), snapshot_id=snapshots.get(r.id)) for r in reports]


def create_status_report(
    session: Session, access: ProjectAccess, actor: User, project_id: int, *, report_date: date, now: datetime
) -> ReportView:
    """報告日を作る（status_report.manage。PRG-01）。状態は OPEN。同じ日付の報告日が既にあれば 409 ``DUPLICATE_STATUS_REPORT``。

    入力の枠（PRG-01）として、この日付より前の最も新しい報告日の入力のうち、引き継ぐ値（進捗率・実績開始日・実績終了日）を写す。
    残工数・遅延理由・完了見込みはその報告日の時点の評価なので写さない（残工数は自動に戻る。docs/decisions/ASSUMPTIONS.md A-26）。
    """
    authorize_project(session, access, actor, project_id, "status_report.manage")
    existing = session.scalar(
        select(StatusReport.id).where(StatusReport.project_id == project_id, StatusReport.report_date == report_date)
    )
    if existing is not None:
        raise AppError(409, "DUPLICATE_STATUS_REPORT", "同じ日付の報告日が既にあります",
                       details=[{"report_date": report_date.isoformat(), "status_report_id": existing}])
    report = StatusReport(project_id=project_id, report_date=report_date, status=StatusReportStatus.OPEN.value,
                          created_by=actor.id, created_at=now)
    session.add(report)
    session.flush()
    previous = session.scalars(
        select(StatusReport)
        .where(StatusReport.project_id == project_id, StatusReport.report_date < report_date)
        .order_by(StatusReport.report_date.desc())
        .limit(1)
    ).first()
    if previous is not None:
        for entry in sorted(_entries_of(session, previous.id).values(), key=lambda e: e.activity_id):
            session.add(
                ProgressEntry(
                    status_report_id=report.id, activity_id=entry.activity_id, percent_complete=entry.percent_complete,
                    actual_start=entry.actual_start, actual_finish=entry.actual_finish, entered_by=actor.id,
                    entered_at=now,
                )
            )
    session.commit()
    return _report_views(session, [report])[0]


def list_status_reports(
    session: Session, access: ProjectAccess, actor: User, project_id: int, *, limit: int, offset: int
) -> tuple[list[ReportView], int]:
    """報告日の一覧（project.view。報告日の古い順）。"""
    authorize_project(session, access, actor, project_id, "project.view")
    reports = list(
        session.scalars(select(StatusReport).where(StatusReport.project_id == project_id).order_by(StatusReport.report_date))
    )
    return _report_views(session, reports[offset:offset + limit]), len(reports)


# ---------------------------------------------------------------------------
# 進捗の入力（API-PRG-003）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EntryView:
    entry: ProgressEntry
    method: ProgressMethod


def put_progress(
    session: Session,
    access: ProjectAccess,
    actor: User,
    project_id: int,
    report_id: int,
    activity_id: int,
    *,
    percent_complete: Decimal | None,
    actual_start: date | None,
    actual_finish: date | None,
    remaining_hours: Decimal | None,
    delay_reason: str | None,
    forecast_finish: date | None,
    now: datetime,
) -> EntryView:
    """報告日の活動の進捗を書く（progress.enter_own か progress.enter_all。PRG-02・03、EFF-06）。全ての項目を置き換える。

    確かめる順：プロジェクト 404 → 権限 403 → 報告日・活動 404 → 自分の割当 403 → 報告日が OPEN でなければ 409 ``INVALID_STATE``
    （BR-13）→ 入力 400（直接入力以外の方式で進捗率を入れた・実績開始日より前の実績終了日）。
    保存する進捗率は RULE-PRG-001 の算定値（直接入力は入力値。実績終了日があれば 100）。
    """
    permission = _check_entry_permission(session, access, actor, project_id)
    report = _report_of(session, project_id, report_id)
    activity = _activity_of(session, project_id, activity_id)
    _check_own_activity(actor, permission, activity_id)
    try:
        ensure_report_open(report.status)
    except SnapshotError as exc:
        raise _invalid_state(str(exc), details=[{"status_report_id": report.id, "status": report.status}]) from exc
    project = _project_of(session, project_id)
    method = effective_method(project, activity)
    if percent_complete is not None and method is not ProgressMethod.DIRECT:
        raise _invalid(["body", "percent_complete"],
                       f"この活動の進捗率は算定方式 {method.value} で求めます（直接入力 DIRECT の活動だけが入力できます）")
    items = _items_of(session, [activity_id])[activity_id]
    percent = _percent(method, items, entered=percent_complete, actual_start=actual_start, actual_finish=actual_finish)
    entry = session.scalar(
        select(ProgressEntry).where(ProgressEntry.status_report_id == report.id, ProgressEntry.activity_id == activity_id)
    )
    if entry is None:
        entry = ProgressEntry(status_report_id=report.id, activity_id=activity_id)
        session.add(entry)
    entry.percent_complete = percent
    entry.actual_start = actual_start
    entry.actual_finish = actual_finish
    entry.remaining_hours = remaining_hours
    entry.delay_reason = delay_reason
    entry.forecast_finish = forecast_finish
    entry.entered_by = actor.id
    entry.entered_at = now
    session.commit()
    return EntryView(entry=entry, method=method)


# ---------------------------------------------------------------------------
# 算定項目・算定方式（API-PRG-004・005）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ItemsView:
    activity_id: int
    method: ProgressMethod
    percent_complete: Decimal | None
    items: list[ProgressItem]


def _items_view(session: Session, project: Project, activity: Activity) -> ItemsView:
    """算定項目と、4.2 の最新の報告日の入力で求めた今の進捗率（入力が無ければ入力なしで求める）。"""
    items = _items_of(session, [activity.id])[activity.id]
    latest = progress_entries_latest(session, project.id).activities.get(activity.id)
    method = effective_method(project, activity)
    percent = _percent(
        method, items,
        entered=latest.percent_complete if latest is not None else None,
        actual_start=latest.actual_start if latest is not None else None,
        actual_finish=latest.actual_finish if latest is not None else None,
    )
    return ItemsView(activity_id=activity.id, method=method, percent_complete=percent, items=items)


def replace_progress_items(
    session: Session,
    access: ProjectAccess,
    actor: User,
    project_id: int,
    activity_id: int,
    items: Sequence[Mapping[str, Any]],
    *,
    now: datetime,
) -> ItemsView:
    """活動の算定項目を置き換える（progress.enter_own か progress.enter_all。PRG-05・BR-06）。

    ``id`` を載せた項目はその項目を変え、載せない項目は足し、載せなかった既存の項目は消す。``id`` がこの活動の項目でない・
    同じ ``id`` が 2 つは 400。完了の時刻は、完了になったときに「今」、完了のままなら保ち、完了でなければ空。
    置き換えた後、OPEN の報告日の入力の進捗率を求め直す。
    """
    permission = _check_entry_permission(session, access, actor, project_id)
    activity = _activity_of(session, project_id, activity_id)
    _check_own_activity(actor, permission, activity_id)
    existing = {i.id: i for i in _items_of(session, [activity_id])[activity_id]}
    seen: set[int] = set()
    for index, item in enumerate(items):
        item_id = item.get("id")
        if item_id is None:
            continue
        if item_id not in existing:
            raise _invalid(["body", "items", index, "id"], "この活動の算定項目ではありません")
        if item_id in seen:
            raise _invalid(["body", "items", index, "id"], "同じ算定項目が 2 回あります")
        seen.add(item_id)

    for item in items:
        done = bool(item.get("done", False))
        target = existing.get(item["id"]) if item.get("id") is not None else None
        if target is None:
            target = ProgressItem(activity_id=activity_id)
            session.add(target)
            was_done = False
        else:
            was_done = target.done
        target.item_type = item["item_type"]
        target.title = item["title"]
        target.weight = _decimal(item.get("weight", 1))
        target.approved = bool(item.get("approved", False))
        target.done = done
        if not done:
            target.done_at = None
        elif not was_done or target.done_at is None:
            target.done_at = now
    for item_id, old in existing.items():
        if item_id not in seen:
            session.delete(old)
    session.flush()
    project = _project_of(session, project_id)
    _refresh_open_entries(session, project, activity)
    session.commit()
    return _items_view(session, project, activity)


@dataclass(frozen=True)
class MethodView:
    activity: Activity
    effective_method: ProgressMethod
    project_default: ProgressMethod


def set_progress_method(
    session: Session, access: ProjectAccess, actor: User, project_id: int, activity_id: int,
    progress_method: str | None,
) -> MethodView:
    """活動の進捗率の算定方式を変える（schedule.edit。PRG-05）。``None`` はプロジェクトの既定に従う（4.1）。

    変えた後、OPEN の報告日の入力の進捗率を新しい方式で求め直す（直接入力へ変えたときは、それまでの算定値を入力値として引き継ぐ）。
    """
    authorize_project(session, access, actor, project_id, "schedule.edit")
    activity = _activity_of(session, project_id, activity_id)
    project = _project_of(session, project_id)
    activity.progress_method = None if progress_method is None else ProgressMethod(progress_method).value
    session.flush()
    _refresh_open_entries(session, project, activity)
    session.commit()
    return MethodView(
        activity=activity,
        effective_method=effective_method(project, activity),
        project_default=ProgressMethod(project_settings(project)["progress_method_default"]),
    )


# ---------------------------------------------------------------------------
# イナズマ線（API-PRG-006・008）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LightningRowView:
    """イナズマ線の 1 行。計画・進捗の値は CALCULATED なら今の活動と入力、SNAPSHOT なら写しの値。"""

    vertex: LightningVertex
    wbs_code: str
    name: str
    is_milestone: bool
    is_critical: bool
    start_date: date | None
    finish_date: date | None
    duration_days: int | None
    progress_method: ProgressMethod | None
    percent_complete: Decimal | None
    actual_start: date | None
    actual_finish: date | None
    entry: ProgressEntry | None


@dataclass(frozen=True)
class LightningView:
    report: ReportView
    source: str
    project_start: date
    line: LightningLine
    rows: list[LightningRowView]
    restricted: bool


@dataclass(frozen=True)
class _Calculated:
    """OPEN の報告日の計算：行の順の活動・その進捗率（表示の値と丸める前の値）・線。"""

    activities: list[Activity]
    percents: dict[int, Decimal | None]
    exact: dict[int, Fraction | None]
    line: LightningLine


def _calculate_line(session: Session, plan: Plan, report: StatusReport, entries: Mapping[int, ProgressEntry],
                    activities: Sequence[Activity]) -> _Calculated:
    """今の計画とその報告日の入力から線を組み立てる（RULE-PRG-001〜005。PRG-01：その報告日の値だけで計算する）。"""
    _require_schedule(activities)
    items = _items_of(session, [a.id for a in activities])
    exact = {a.id: _entry_exact_percent(plan.project, a, entries.get(a.id), items[a.id]) for a in activities}
    percents = {k: round_percent(v) for k, v in exact.items()}
    tolerance = _decimal(project_settings(plan.project)["delay_tolerance_days"])
    try:
        line = build_lightning_line(
            [
                LightningActivity(
                    activity_id=a.id, start_date=a.start_date, finish_date=a.finish_date,  # type: ignore[arg-type]
                    duration_days=a.duration_days, is_milestone=a.is_milestone, percent_complete=exact[a.id],
                    actual_start=entries[a.id].actual_start if a.id in entries else None,
                    actual_finish=entries[a.id].actual_finish if a.id in entries else None,
                )
                for a in activities
            ],
            calendar=plan.calendar,
            project_start=plan.project.start_date,
            report_date=report.report_date,
            tolerance_days=tolerance,
        )
    except LightningError as exc:
        raise _invalid_state(f"イナズマ線を組み立てられません: {exc}") from exc
    return _Calculated(activities=list(activities), percents=percents, exact=exact, line=line)


def _snapshot_of(session: Session, report: StatusReport) -> tuple[ProgressSnapshot, list[ProgressSnapshotItem]]:
    snapshot = session.scalar(select(ProgressSnapshot).where(ProgressSnapshot.status_report_id == report.id))
    if snapshot is None:  # 確定と同じトランザクションで作るので、確定済みなら必ずある
        raise RuntimeError(f"確定済みの報告日 {report.id} にスナップショットが無い")
    items = list(
        session.scalars(
            select(ProgressSnapshotItem).where(ProgressSnapshotItem.snapshot_id == snapshot.id)
            .order_by(ProgressSnapshotItem.row_order)
        )
    )
    return snapshot, items


def _restore(plan: Plan, report: StatusReport, snapshot: ProgressSnapshot, items: Sequence[ProgressSnapshotItem]) -> LightningLine:
    """写しの値だけから過去の線を作る（BR-13。RULE-PRG-005）。"""
    return restore_lightning_line(
        report_date=report.report_date,
        report_line=snapshot.report_line,
        tolerance_days=snapshot.tolerance_days,
        points=[RecordedPoint(activity_id=i.activity_id, row_order=i.row_order, point=i.point, deviation=i.deviation,
                              judgement=i.judgement) for i in items],  # type: ignore[arg-type]
        calendar=plan.calendar,
        project_start=plan.project.start_date,
    )


def lightning_line(session: Session, access: ProjectAccess, actor: User, project_id: int, report_id: int) -> LightningView:
    """報告日のイナズマ線（project.view。PRG-08〜13）。

    OPEN は今の計画とその報告日の入力から計算し（CALCULATED）、FINALIZED はスナップショットの写しだけから描く（SNAPSHOT。BR-13）。
    委託先は自分の割当の活動の行だけ（行の番号は見せる行の中で 0 から）。
    """
    table = authorize_project(session, access, actor, project_id, "project.view")
    report = _report_of(session, project_id, report_id)
    plan = load_plan(session, project_id)
    visible = _visible_activity_ids(session, access, actor, project_id, table, plan.activities)
    entries = _entries_of(session, report.id)
    rows: list[LightningRowView] = []
    if report.status == StatusReportStatus.FINALIZED.value:
        snapshot, items = _snapshot_of(session, report)
        shown = [i for i in items if visible is None or (i.activity_id is not None and i.activity_id in visible)]
        line = _restore(plan, report, snapshot, shown)
        source = SOURCE_SNAPSHOT
        for item, vertex in zip(shown, line.vertices, strict=True):
            rows.append(
                LightningRowView(
                    vertex=vertex, wbs_code=item.wbs_code, name=item.name, is_milestone=item.is_milestone,
                    is_critical=item.is_critical, start_date=item.start_date, finish_date=item.finish_date,
                    duration_days=item.duration_days, progress_method=None, percent_complete=item.percent_complete,
                    actual_start=item.actual_start, actual_finish=item.actual_finish,
                    entry=entries.get(item.activity_id) if item.activity_id is not None else None,
                )
            )
    else:
        ordered = [a for a in plan.ordered_activities() if visible is None or a.id in visible]
        calculated = _calculate_line(session, plan, report, entries, ordered)
        line = calculated.line
        source = SOURCE_CALCULATED
        elements = plan.element_by_id
        for a, vertex in zip(calculated.activities, line.vertices, strict=True):
            entry = entries.get(a.id)
            rows.append(
                LightningRowView(
                    vertex=vertex, wbs_code=elements[a.work_package_id].wbs_code, name=a.name,
                    is_milestone=a.is_milestone, is_critical=a.is_critical, start_date=a.start_date,
                    finish_date=a.finish_date, duration_days=a.duration_days,
                    progress_method=effective_method(plan.project, a), percent_complete=calculated.percents[a.id],
                    actual_start=entry.actual_start if entry is not None else None,
                    actual_finish=entry.actual_finish if entry is not None else None, entry=entry,
                )
            )
    return LightningView(report=_report_views(session, [report])[0], source=source,
                         project_start=plan.project.start_date, line=line, rows=rows, restricted=visible is not None)


@dataclass(frozen=True)
class OverlaidView:
    report_id: int
    overlaid: OverlaidLine


@dataclass(frozen=True)
class LinesView:
    project_start: date
    lines: list[OverlaidView]
    restricted: bool


def lightning_lines(
    session: Session, access: ProjectAccess, actor: User, project_id: int, report_ids: Sequence[int]
) -> LinesView:
    """確定済みの複数の報告日の線を、今のガントの行へ置き直して重ねる（project.view。PRG-18）。

    ``report_ids`` が空・重複は 400、無い報告日・他のプロジェクトの報告日は 404、確定していない報告日は 409 ``INVALID_STATE``
    （PRG-18 は確定済みの報告日を選ぶ）。線は報告日の古い順（線の種類 ``style_index``）。委託先は自分の割当の活動の頂点だけ。
    """
    table = authorize_project(session, access, actor, project_id, "project.view")
    if not report_ids:
        raise _invalid(["query", "report_ids"], "報告日を 1 つ以上指定します")
    if len(set(report_ids)) != len(report_ids):
        raise _invalid(["query", "report_ids"], "同じ報告日が 2 回指定されています")
    reports = [_report_of(session, project_id, r) for r in report_ids]
    open_ids = [r.id for r in reports if r.status != StatusReportStatus.FINALIZED.value]
    if open_ids:
        raise _invalid_state("確定していない報告日は重ねられません（確定済みの報告日を選びます）",
                             details=[{"status_report_ids": open_ids}])
    plan = load_plan(session, project_id)
    visible = _visible_activity_ids(session, access, actor, project_id, table, plan.activities)
    ordered = [a for a in plan.ordered_activities() if visible is None or a.id in visible]
    row_of = {a.id: i for i, a in enumerate(ordered)}
    lines: list[LightningLine] = []
    report_by_date: dict[date, int] = {}
    for report in reports:
        snapshot, items = _snapshot_of(session, report)
        shown = [i for i in items if visible is None or (i.activity_id is not None and i.activity_id in visible)]
        lines.append(_restore(plan, report, snapshot, shown))
        report_by_date[report.report_date] = report.id
    overlaid = overlay_lightning_lines(lines, row_of)
    return LinesView(
        project_start=plan.project.start_date,
        lines=[OverlaidView(report_id=report_by_date[o.line.report_date], overlaid=o) for o in overlaid],
        restricted=visible is not None,
    )


# ---------------------------------------------------------------------------
# 確定（API-PRG-007）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FinalizeView:
    report: ReportView
    snapshot: ProgressSnapshot
    item_count: int


def finalize(
    session: Session, access: ProjectAccess, actor: User, links: ProgressLinks, project_id: int, report_id: int, *,
    now: datetime,
) -> FinalizeView:
    """報告日を確定してスナップショットを作る（status_report.manage。PRG-17・BR-13。RULE-PRG-007）。

    確定済みなら 409 ``INVALID_STATE``（戻さない・二度しない）。全ての活動をガントの行の順で、その時点の計画日付・期間・進捗率
    （RULE-PRG-001）・実績日付・実績工数・ETC（``links.effort``）と、進捗ポイント・ずれ・判定（RULE-PRG-005 と同じ計算）を写す。
    許容幅・抽出の閾値は今の ``projects.settings`` を写す。
    """
    authorize_project(session, access, actor, project_id, "status_report.manage")
    report = _report_of(session, project_id, report_id)
    if report.status != StatusReportStatus.OPEN.value:
        raise _invalid_state("確定済みの報告日は確定できません（確定は戻さない）",
                             details=[{"status_report_id": report.id, "status": report.status}])
    plan = load_plan(session, project_id)
    ordered = plan.ordered_activities()
    _require_schedule(ordered)
    entries = _entries_of(session, report.id)
    items = _items_of(session, [a.id for a in ordered])
    exact = {a.id: _entry_exact_percent(plan.project, a, entries.get(a.id), items[a.id]) for a in ordered}
    percents = {k: round_percent(v) for k, v in exact.items()}
    efforts = links.effort.effort(session, project_id, report.report_date, entries, [a.id for a in ordered],
                                  percents=percents)
    elements = plan.element_by_id
    settings = project_settings(plan.project)
    activities = []
    for a in ordered:
        entry = entries.get(a.id)
        effort = efforts.get(a.id, ActivityEffort())
        activities.append(
            SnapshotActivity(
                activity_id=a.id, wbs_code=elements[a.work_package_id].wbs_code, name=a.name,
                start_date=a.start_date, finish_date=a.finish_date,  # type: ignore[arg-type]
                duration_days=a.duration_days, is_milestone=a.is_milestone, is_critical=a.is_critical,
                percent_complete=exact[a.id],  # 写しには小数 2 桁へ丸めた値が入る（RULE-PRG-007。5.6）
                actual_start=entry.actual_start if entry is not None else None,
                actual_finish=entry.actual_finish if entry is not None else None,
                actual_hours=effort.actual_hours, etc_hours=effort.etc_hours,
            )
        )
    try:
        result = finalize_status_report(
            status=report.status, report_date=report.report_date, activities=activities, calendar=plan.calendar,
            project_start=plan.project.start_date, tolerance_days=_decimal(settings["delay_tolerance_days"]),
            extract_days=_decimal(settings["delay_extract_days"]),
        )
    except SnapshotError as exc:
        if exc.code is SnapshotErrorCode.INVALID_STATE:
            raise _invalid_state(str(exc)) from exc
        raise AppError(422, "SNAPSHOT_INVALID", str(exc)) from exc
    snap = result.snapshot
    snapshot = ProgressSnapshot(status_report_id=report.id, report_date=snap.report_date, report_line=snap.report_line,
                                tolerance_days=snap.tolerance_days, extract_days=snap.extract_days,
                                plan_basis=snap.plan_basis, created_at=now)
    session.add(snapshot)
    session.flush()
    session.add_all(
        ProgressSnapshotItem(
            snapshot_id=snapshot.id, activity_id=i.activity_id, row_order=i.row_order, wbs_code=i.wbs_code, name=i.name,
            start_date=i.start_date, finish_date=i.finish_date, duration_days=i.duration_days,
            is_milestone=i.is_milestone, is_critical=i.is_critical, percent_complete=i.percent_complete,
            actual_start=i.actual_start, actual_finish=i.actual_finish, actual_hours=i.actual_hours,
            etc_hours=i.etc_hours, point=i.point, deviation=i.deviation, judgement=i.judgement.value,
        )
        for i in snap.items
    )
    report.status = result.status.value
    report.finalized_by = actor.id
    report.finalized_at = now
    session.commit()
    return FinalizeView(report=_report_views(session, [report])[0], snapshot=snapshot, item_count=len(snap.items))


# ---------------------------------------------------------------------------
# 遅延（API-PRG-009）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DelaysView:
    report: ReportView
    source: str
    analysis: DelayAnalysis
    plan: Plan
    entries: Mapping[int, ProgressEntry]
    visible: frozenset[int] | None

    @property
    def restricted(self) -> bool:
        return self.visible is not None


def delays(session: Session, access: ProjectAccess, actor: User, project_id: int, report_id: int) -> DelaysView:
    """遅延活動と影響（project.view。PRG-19・PRG-13。RULE-PRG-006）。

    判定とずれは API-PRG-006 と同じ元（OPEN は今の計画とその報告日の入力から計算、FINALIZED は写しの値。写しの活動のうち
    今の計画に無いものは除く）。影響は今の計画（日程の入力と依存）で前進計算をやり直す。抽出の閾値は OPEN なら今の
    ``projects.settings``、FINALIZED なら写した値。完了見込み日はその報告日の入力（PRG-03）。
    委託先には自分の割当の遅延活動だけを返す（影響の一覧も自分の割当の活動に絞り、プロジェクトの終了のずれは返さない）。
    """
    table = authorize_project(session, access, actor, project_id, "project.view")
    report = _report_of(session, project_id, report_id)
    plan = load_plan(session, project_id)
    visible = _visible_activity_ids(session, access, actor, project_id, table, plan.activities)
    entries = _entries_of(session, report.id)
    activity_ids = set(plan.activity_by_id)
    judged: list[DelayInput] = []
    if report.status == StatusReportStatus.FINALIZED.value:
        snapshot, items = _snapshot_of(session, report)
        extract = snapshot.extract_days
        source = SOURCE_SNAPSHOT
        for i in items:
            if i.activity_id is None or i.activity_id not in activity_ids:
                continue
            entry = entries.get(i.activity_id)
            judged.append(DelayInput(activity_id=i.activity_id, deviation=i.deviation, judgement=i.judgement,  # type: ignore[arg-type]
                                     percent_complete=i.percent_complete,
                                     forecast_finish=entry.forecast_finish if entry is not None else None))
    else:
        calculated = _calculate_line(session, plan, report, entries, plan.ordered_activities())
        extract = _decimal(project_settings(plan.project)["delay_extract_days"])
        source = SOURCE_CALCULATED
        for vertex in calculated.line.vertices:
            assert vertex.activity_id is not None
            entry = entries.get(vertex.activity_id)
            judged.append(DelayInput(activity_id=vertex.activity_id, deviation=vertex.deviation,
                                     judgement=vertex.judgement, percent_complete=calculated.exact[vertex.activity_id],
                                     forecast_finish=entry.forecast_finish if entry is not None else None))
    try:
        analysis = analyze_delays(
            judged,
            [ScheduleActivity(id=a.id, duration_days=a.duration_days, is_milestone=a.is_milestone,
                              requested_start=a.requested_start) for a in plan.activities],
            plan.pairs,
            calendar=plan.calendar,
            project_start=plan.project.start_date,
            report_date=report.report_date,
            extract_days=extract,
        )
    except ScheduleCycleError as exc:  # 依存の追加が循環を拒むので起きないが、起きたら日程計算と同じエラーにする
        raise AppError(422, "SCHEDULE_CYCLE", "依存が循環しています", details=[{"activity_ids": list(exc.activity_ids)}]) from exc
    return DelaysView(report=_report_views(session, [report])[0], source=source, analysis=analysis, plan=plan,
                      entries=entries, visible=visible)
