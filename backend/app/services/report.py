"""レポート・共有（RPT）のユースケース（設計書 6.2 の API-RPT-001〜004、4.1・4.2・5.1・7.4、9 章。
要求仕様書 RPT-01・RPT-03、NFR-04、BR-13）。

- 権限（RULE-AUTH-001）：会議レポートを読むは project.view、共有リンクを作る・失効させるは share.manage。
  共有リンクで閲覧する（API-RPT-004）だけは認証を要らず、トークンを RULE-AUTH-002 で検証する
- 会議レポート（RULE-RPT-001）の進捗の値の源は API-PRG-006・009 と同じ：確定済みの報告日はスナップショットの写しだけ（BR-13）、
  確定前は今の計画とその報告日の入力から確定と同じ計算で求めた値（保存しない）。計画の値（ガントのバー、マイルストーンの
  予定日・見込み日・ずれ）は確定済みかどうかにかかわらず表示した時点の最新計画（API-SCH-010 と同じ。7.4・設計書 1.2・CR-013）。
  ガントは今の計画を、その報告日の進捗（確定済みは写しの進捗率・実績日付）で描く。工数（EAC・基準）は RULE-EFF-002 の
  報告日以前の実績で求め、是正措置は今の状態（A-35・OQ-32 (3)）
- 委託先だけで読む利用者（9.2 の注記）には、自分の割当の活動の行・頂点・遅延・マイルストーンだけを返し、プロジェクト全体の値の
  KPI は返さない（``restricted``）
- 共有リンクのトークンは作った応答でだけ返し、DB には SHA-256 のハッシュだけを保存する（4.2・NFR-04）。閲覧はガント
  （``GANTT``）か、4.2 の最新の報告日の会議レポート（``REPORT``）。どちらもプロジェクトの全ての活動を見せる（読むだけ）
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.authz import ShareLinkRecord, ViewLevel, hash_token, project_view_level, verify_share_link
from app.domain.effort import EffortValues
from app.domain.gantt import GanttChart
from app.domain.lightning import Judgement
from app.domain.progress import round_percent
from app.domain.report import (
    REPORT_WINDOW,
    CorrectiveActionDue,
    MeetingReport,
    ProgressNote,
    ReportError,
    build_meeting_report,
)
from app.domain.scheduling import ScheduleActivity, ScheduleCycleError
from app.domain.snapshot import Snapshot, SnapshotActivity, SnapshotItem, StatusReportStatus
from app.errors import AppError
from app.infra.security import generate_token
from app.models.chg import CorrectiveAction
from app.models.cmn import ShareLink as ShareLink  # API 層は共有リンクの型をここから取る（1.3：API はモデルを直接 import しない）
from app.models.cmn import User
from app.models.prg import ProgressEntry, ProgressSnapshot, ProgressSnapshotItem, StatusReport
from app.models.prj import Project
from app.models.sch import Activity, ScheduleBaseline
from app.services.auth import authorize_project, principal_of
from app.services.effort import load_basis
from app.services.progress import entries_of_report, project_settings, report_exact_percents
from app.services.project_access import ProjectAccess
from app.services.schedule import (
    ActivityProgress,
    LatestProgress,
    Plan,
    ScheduleLinks,
    load_plan,
    plan_gantt,
)

PROJECT_VIEW = "project.view"
SHARE_MANAGE = "share.manage"
SOURCE_CALCULATED = "CALCULATED"
SOURCE_SNAPSHOT = "SNAPSHOT"
SCOPE_GANTT = "GANTT"
SCOPE_REPORT = "REPORT"

# 4.2 の「最新」の報告日を選ぶ順（確定していない最新の報告日、無ければ最新の確定済みの報告日）
_LATEST_REPORT_STATUS_ORDER = (StatusReportStatus.OPEN.value, StatusReportStatus.FINALIZED.value)


def _decimal(value: object) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _invalid_state(message: str, details: list[Any] | None = None) -> AppError:
    return AppError(409, "INVALID_STATE", message, details=details)


def _report_of(session: Session, project_id: int, report_id: int) -> StatusReport:
    report = session.get(StatusReport, report_id)
    if report is None or report.project_id != project_id:
        raise AppError(404, "NOT_FOUND", "報告日が見つかりません", details=[{"status_report_id": report_id}])
    return report


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
    return frozenset(a.id for a in activities if a.id in assigned)


# ---------------------------------------------------------------------------
# 会議レポート（API-RPT-001）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MeetingReportView:
    report: StatusReport
    source: str
    project_start: date
    report_data: MeetingReport
    baseline: ScheduleBaseline | None
    restricted: bool


def _snapshot_of(session: Session, report: StatusReport) -> Snapshot:
    """確定済みの報告日のスナップショット（4.1 の progress_snapshots・progress_snapshot_items）をドメインの形にする。"""
    snapshot = session.scalar(select(ProgressSnapshot).where(ProgressSnapshot.status_report_id == report.id))
    if snapshot is None:  # 確定と同じトランザクションで作るので、確定済みなら必ずある
        raise RuntimeError(f"確定済みの報告日 {report.id} にスナップショットが無い")
    rows = session.scalars(
        select(ProgressSnapshotItem).where(ProgressSnapshotItem.snapshot_id == snapshot.id)
        .order_by(ProgressSnapshotItem.row_order)
    )
    # 写しの後に消した活動の行は activity_id が空のまま渡す（今のガントに無い頂点として外れる。RULE-PRG-005）
    items = tuple(
        SnapshotItem(
            activity_id=i.activity_id,  # type: ignore[arg-type]
            row_order=i.row_order, wbs_code=i.wbs_code, name=i.name,
            start_date=i.start_date, finish_date=i.finish_date,  # type: ignore[arg-type]
            duration_days=i.duration_days,  # type: ignore[arg-type]
            is_milestone=i.is_milestone, is_critical=i.is_critical, percent_complete=i.percent_complete,
            actual_start=i.actual_start, actual_finish=i.actual_finish, actual_hours=i.actual_hours,
            etc_hours=i.etc_hours, point=i.point, deviation=i.deviation,
            # DB の文字列を判定の列挙へ直す（KPI の遅れの件数は列挙で数える。7.4・CR-013 の進捗の値は写しのまま）
            judgement=Judgement(i.judgement),
        )
        for i in rows
    )
    return Snapshot(report_date=snapshot.report_date, report_line=snapshot.report_line,
                    tolerance_days=snapshot.tolerance_days, extract_days=snapshot.extract_days,
                    plan_basis=snapshot.plan_basis, items=items)


def _filter_snapshot(snapshot: Snapshot, visible: frozenset[int]) -> Snapshot:
    shown = tuple(i for i in snapshot.items if i.activity_id is not None and i.activity_id in visible)
    return Snapshot(report_date=snapshot.report_date, report_line=snapshot.report_line,
                    tolerance_days=snapshot.tolerance_days, extract_days=snapshot.extract_days,
                    plan_basis=snapshot.plan_basis, items=shown)


def _require_schedule(activities: Sequence[Activity], *, with_ids: bool = True) -> None:
    """全ての活動に日程の計算結果（開始日・終了日）があること。無ければ 409（API-SCH-008 で計算し直す）。

    ``details`` に活動の ID を載せる。認証の無い共有リンクの閲覧（``with_ids`` が偽）は載せない（文言は同じ）。
    """
    missing = [a.id for a in activities if a.start_date is None or a.finish_date is None]
    if missing:
        raise _invalid_state("日程が計算されていない活動があります（日程を計算し直してください）",
                             details=[{"activity_ids": missing}] if with_ids else [])


def _effort_of(session: Session, project_id: int, report: StatusReport) -> EffortValues:
    """プロジェクトの工数の合計（RULE-EFF-002。報告日の進捗率・残工数、報告日以前の実績、最新の基準）。"""
    return load_basis(session, project_id, report_id=report.id).rollup.project


def _corrective_actions(session: Session, project_id: int) -> list[CorrectiveActionDue]:
    return [
        CorrectiveActionDue(corrective_action_id=a.id, due_date=a.due_date, status=a.status)
        for a in session.scalars(select(CorrectiveAction).where(CorrectiveAction.project_id == project_id)
                                 .order_by(CorrectiveAction.id))
    ]


def _build(
    session: Session, links: ScheduleLinks, plan: Plan, report: StatusReport, *, visible: frozenset[int] | None,
    shared: bool = False,
) -> MeetingReportView:
    """報告日の会議レポートを組み立てる（RULE-RPT-001。権限は呼び手が確かめる）。

    ``shared`` は認証の無い共有リンクの閲覧（API-RPT-004）：日程が計算されていない活動の 409 の ``details`` に ID を載せない。
    """
    project = plan.project
    entries: dict[int, ProgressEntry] = entries_of_report(session, report.id)
    ordered = [a for a in plan.ordered_activities() if visible is None or a.id in visible]
    settings = project_settings(project)
    snapshot: Snapshot | None = None
    activities: list[SnapshotActivity] = []
    gantt_progress: dict[int, ActivityProgress] = {}
    # 計画の値は確定済みでも最新計画から求めるので、どちらの報告日も最新計画の日程が要る（7.4。設計書 1.2・CR-013）
    _require_schedule(ordered, with_ids=not shared)
    if report.status == StatusReportStatus.FINALIZED.value:
        source = SOURCE_SNAPSHOT
        snapshot = _snapshot_of(session, report)
        if visible is not None:
            snapshot = _filter_snapshot(snapshot, visible)
        for item in snapshot.items:
            if item.activity_id is not None:
                gantt_progress[item.activity_id] = ActivityProgress(
                    percent_complete=item.percent_complete, actual_start=item.actual_start,
                    actual_finish=item.actual_finish)
    else:
        source = SOURCE_CALCULATED
        exact = report_exact_percents(session, project, ordered, entries)  # P は丸める前の値から（5.6）
        percents = {k: round_percent(v) for k, v in exact.items()}
        elements = plan.element_by_id
        for a in ordered:
            entry = entries.get(a.id)
            activities.append(
                SnapshotActivity(
                    activity_id=a.id, wbs_code=elements[a.work_package_id].wbs_code, name=a.name,
                    start_date=a.start_date, finish_date=a.finish_date,  # type: ignore[arg-type]
                    duration_days=a.duration_days, is_milestone=a.is_milestone, is_critical=a.is_critical,
                    percent_complete=exact[a.id],
                    actual_start=entry.actual_start if entry is not None else None,
                    actual_finish=entry.actual_finish if entry is not None else None,
                )
            )
            gantt_progress[a.id] = ActivityProgress(
                percent_complete=percents[a.id],
                actual_start=entry.actual_start if entry is not None else None,
                actual_finish=entry.actual_finish if entry is not None else None,
            )

    # 非稼働日は会議レポートの表示範囲（報告日の前後 4 週。7.4）の分を受け取る（設計書 1.1・CR-008）
    chart, baseline = plan_gantt(
        session, links, plan, visible=visible,
        progress=LatestProgress(report_date=report.report_date, activities=gantt_progress),
        range_start=report.report_date - REPORT_WINDOW, range_end=report.report_date + REPORT_WINDOW,
    )
    restricted = visible is not None
    try:
        data = build_meeting_report(
            status=report.status,
            report_date=report.report_date,
            calendar=plan.calendar,
            project_start=project.start_date,
            gantt=chart,
            schedule_activities=[
                ScheduleActivity(id=a.id, duration_days=a.duration_days, is_milestone=a.is_milestone,
                                 requested_start=a.requested_start)
                for a in plan.activities
            ],
            dependencies=plan.pairs,
            snapshot=snapshot,
            activities=activities,
            tolerance_days=_decimal(settings["delay_tolerance_days"]),
            extract_days=_decimal(settings["delay_extract_days"]),
            progress_notes=[
                ProgressNote(activity_id=e.activity_id, delay_reason=e.delay_reason, forecast_finish=e.forecast_finish)
                for e in entries.values()
                if visible is None or e.activity_id in visible
            ],
            estimate_hours={a.id: a.estimate_hours for a in plan.activities},
            # プロジェクト全体の値は委託先だけで読む利用者に返さないので求めない
            effort=None if restricted else _effort_of(session, project.id, report),
            corrective_actions=() if restricted else _corrective_actions(session, project.id),
        )
    except ScheduleCycleError as exc:  # 依存の追加が循環を拒むので起きないが、起きたら日程計算と同じエラーにする
        raise AppError(422, "SCHEDULE_CYCLE", "依存が循環しています",
                       details=[{"activity_ids": list(exc.activity_ids)}]) from exc
    except ReportError as exc:
        raise AppError(422, "REPORT_INVALID", f"会議レポートを組み立てられません: {exc}") from exc
    return MeetingReportView(report=report, source=source, project_start=project.start_date, report_data=data,
                             baseline=baseline, restricted=restricted)


def meeting_report(
    session: Session, access: ProjectAccess, actor: User, links: ScheduleLinks, project_id: int, report_id: int,
) -> MeetingReportView:
    """報告日の定例会議レポートのデータ（project.view。RPT-01。RULE-RPT-001）。

    無いプロジェクト・報告日（他のプロジェクトの報告日を含む）は 404。日程が計算されていない活動があれば 409（計画の値は
    確定済みの報告日でも最新計画から求めるため。CR-013）。
    """
    table = authorize_project(session, access, actor, project_id, PROJECT_VIEW)
    report = _report_of(session, project_id, report_id)
    plan = load_plan(session, project_id)
    visible = _visible_activity_ids(session, access, actor, project_id, table, plan.activities)
    return _build(session, links, plan, report, visible=visible)


# ---------------------------------------------------------------------------
# 共有リンク（API-RPT-002・003）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CreatedShareLink:
    link: ShareLink
    token: str


def create_share_link(
    session: Session, access: ProjectAccess, actor: User, project_id: int, *, scope: str, expires_at: datetime,
    now: datetime,
) -> CreatedShareLink:
    """共有リンクを作る（share.manage。RPT-03）。トークンは戻り値でだけ渡し、DB にはハッシュだけを書く（4.2・NFR-04）。

    ``expires_at`` が今以前なら 400（作った時点で見られないリンクは作らない）。
    """
    authorize_project(session, access, actor, project_id, SHARE_MANAGE)
    if expires_at <= now:
        raise AppError(400, "VALIDATION_ERROR", "入力に誤りがあります",
                       details=[{"loc": ["body", "expires_at"], "message": "有効期限は今より後の時刻にします"}])
    token = generate_token()
    # 時刻は UTC で保存し、応答も保存した値で返す（2.4）
    link = ShareLink(project_id=project_id, token_hash=hash_token(token), scope=scope,
                     expires_at=expires_at.astimezone(UTC), created_by=actor.id, created_at=now)
    session.add(link)
    session.commit()
    return CreatedShareLink(link=link, token=token)


def list_share_links(
    session: Session, access: ProjectAccess, actor: User, project_id: int, *, limit: int, offset: int,
) -> tuple[list[ShareLink], int]:
    """共有リンクの一覧（share.manage。RPT-03。API-RPT-005。設計書 1.1・CR-004）。

    新しい順（ID の降順）。失効させたリンク・期限の過ぎたリンクも含める（状態は ``revoked_at``・``expires_at`` で分かる）。
    トークンは DB に無い（ハッシュだけ）ので返せない。
    """
    authorize_project(session, access, actor, project_id, SHARE_MANAGE)
    total = session.scalar(select(func.count()).select_from(ShareLink).where(ShareLink.project_id == project_id))
    links = list(session.scalars(
        select(ShareLink).where(ShareLink.project_id == project_id)
        .order_by(ShareLink.id.desc()).limit(limit).offset(offset)
    ))
    return links, int(total or 0)


def revoke_share_link(
    session: Session, access: ProjectAccess, actor: User, project_id: int, link_id: int, *, now: datetime,
) -> ShareLink:
    """共有リンクを失効させる（share.manage。RPT-03）。行は消さず ``revoked_at`` に今を入れる。

    無いリンク・他のプロジェクトのリンクは 404、既に失効させたリンクは 409 ``INVALID_STATE``（失効の時刻を書き換えない）。
    """
    authorize_project(session, access, actor, project_id, SHARE_MANAGE)
    link = session.get(ShareLink, link_id)
    if link is None or link.project_id != project_id:
        raise AppError(404, "NOT_FOUND", "共有リンクが見つかりません", details=[{"share_link_id": link_id}])
    if link.revoked_at is not None:
        raise _invalid_state("共有リンクは既に失効しています", details=[{"share_link_id": link_id}])
    link.revoked_at = now
    session.commit()
    return link


# ---------------------------------------------------------------------------
# 共有リンクで閲覧する（API-RPT-004）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SharedView:
    link: ShareLink
    project: Project
    gantt: GanttChart | None
    gantt_report_date: date | None
    gantt_baseline: ScheduleBaseline | None
    meeting_report: MeetingReportView | None


def shared_view(session: Session, links: ScheduleLinks, token: str, *, now: datetime) -> SharedView:
    """共有リンクのトークンで、リンクの範囲を読むだけで見せる（認証なし。RPT-03。RULE-AUTH-002）。

    トークンが無い・合わない・失効した・期限が切れたリンクは、どれも同じ 404（状態・コード・メッセージ・``details`` が
    全て同じ。どれに当たったかは返さない。リンクの有無・失効・期限を外から見分けさせない）。
    ``GANTT`` は最新の進捗・最新の基準のガント（API-SCH-010 と同じ）、
    ``REPORT`` は 4.2 の最新の報告日の会議レポート（API-RPT-001 と同じ。報告日が無ければ空）。日程が計算されていない活動が
    あれば API-RPT-001 と同じ 409 だが、``details`` は空（認証の無い閲覧者に活動の ID を見せない）。
    """
    row = session.scalar(select(ShareLink).where(ShareLink.token_hash == hash_token(token))) if token else None
    record = None
    if row is not None:
        record = ShareLinkRecord(project_id=row.project_id, token_hash=row.token_hash, scope=row.scope,
                                 expires_at=row.expires_at, revoked_at=row.revoked_at)
    verdict = verify_share_link(token, record, now=now)
    if not verdict.valid or row is None:
        raise AppError(404, "NOT_FOUND", "共有リンクが見つからないか、失効・期限切れです")
    plan = load_plan(session, row.project_id)
    if verdict.can_view(SCOPE_GANTT):
        progress = links.latest_progress(session, row.project_id)
        chart, baseline = plan_gantt(session, links, plan, visible=None, progress=progress)
        return SharedView(link=row, project=plan.project, gantt=chart, gantt_report_date=progress.report_date,
                          gantt_baseline=baseline, meeting_report=None)
    if verdict.can_view(SCOPE_REPORT):
        report = _latest_report(session, row.project_id)
        view = None if report is None else _build(session, links, plan, report, visible=None, shared=True)
        return SharedView(link=row, project=plan.project, gantt=None, gantt_report_date=None, gantt_baseline=None,
                          meeting_report=view)
    raise RuntimeError(f"共有リンク {row.id} の範囲 {row.scope!r} を見せる口が無い")  # 4.1 の CHECK 制約で起きない
