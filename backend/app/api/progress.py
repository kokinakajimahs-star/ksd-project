"""進捗の API（設計書 6.2 の API-PRG-001〜009）：報告日・進捗の入力・算定項目と算定方式・イナズマ線・確定・遅延。"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_project_access
from app.domain.delay import FinishSlip, Slip
from app.domain.gantt import CalendarPoint
from app.schemas.progress import (
    DelayOut,
    DelaysOut,
    FinalizeOut,
    FinishSlipOut,
    LightningLineOut,
    LightningLinesOut,
    LightningRowOut,
    OverlaidLineOut,
    OverlaidVertexOut,
    ProgressEntryIn,
    ProgressEntryOut,
    ProgressItemOut,
    ProgressItemsOut,
    ProgressItemsReplace,
    ProgressMethodIn,
    ProgressMethodOut,
    SlipOut,
    StatusReportCreate,
    StatusReportList,
    StatusReportOut,
)
from app.schemas.schedule import AxisPointOut, CalendarPointOut
from app.services import get_session
from app.services import progress as progress_service
from app.services.auth import User
from app.services.clock import get_now
from app.services.progress import DelaysView, ItemsView, ProgressLinks, ReportView
from app.services.project_access import ProjectAccess

router = APIRouter(tags=["progress"])


def get_progress_links(request: Request) -> ProgressLinks:
    """アプリに載せた別の群の表（実績工数・ETC）の口（``app.services.progress``）。"""
    links: ProgressLinks = request.app.state.progress_links
    return links


def _num(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _decimal(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _axis(position: Decimal | int, calendar: CalendarPoint) -> AxisPointOut:
    return AxisPointOut(position=float(position),
                        calendar=CalendarPointOut(date=calendar.date, fraction=float(calendar.fraction)))


def _report_out(view: ReportView) -> StatusReportOut:
    r = view.report
    return StatusReportOut(id=r.id, project_id=r.project_id, report_date=r.report_date, status=r.status,
                           created_by=r.created_by, created_at=r.created_at, finalized_by=r.finalized_by,
                           finalized_at=r.finalized_at, entry_count=view.entry_count, snapshot_id=view.snapshot_id)


def _items_out(view: ItemsView) -> ProgressItemsOut:
    return ProgressItemsOut(
        activity_id=view.activity_id,
        progress_method=view.method.value,
        percent_complete=_num(view.percent_complete),
        items=[ProgressItemOut(id=i.id, item_type=i.item_type, title=i.title, weight=float(i.weight), done=i.done,
                               approved=i.approved, done_at=i.done_at) for i in view.items],
    )


@router.post("/projects/{project_id}/status-reports", response_model=StatusReportOut, status_code=201)
def create_status_report(
    project_id: int,
    body: StatusReportCreate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
    now: datetime = Depends(get_now),
) -> StatusReportOut:
    """API-PRG-001 報告日を作る。

    権限は status_report.manage。状態は OPEN。同じ日付の報告日が既にあれば 409 ``DUPLICATE_STATUS_REPORT``。
    この日付より前の最も新しい報告日の進捗率・実績日付を、入力の枠として写す（PRG-01）。
    """
    view = progress_service.create_status_report(session, access, user, project_id, report_date=body.report_date, now=now)
    return _report_out(view)


@router.get("/projects/{project_id}/status-reports", response_model=StatusReportList)
def list_status_reports(
    project_id: int,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
) -> StatusReportList:
    """API-PRG-002 報告日の一覧。

    権限は project.view。報告日の古い順。``limit``・``offset``（6.1）。委託先には ``entry_count`` を自分の割当の活動の行だけで数える。
    """
    views, total = progress_service.list_status_reports(session, access, user, project_id, limit=limit, offset=offset)
    return StatusReportList(items=[_report_out(v) for v in views], total=total, limit=limit, offset=offset)


@router.put("/projects/{project_id}/status-reports/{report_id}/progress/{activity_id}", response_model=ProgressEntryOut)
def put_progress(
    project_id: int,
    report_id: int,
    activity_id: int,
    body: ProgressEntryIn,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
    now: datetime = Depends(get_now),
) -> ProgressEntryOut:
    """API-PRG-003 進捗率・実績日付・残工数・遅延理由・完了見込みを書く。

    権限は progress.enter_own（自分の割当の活動）か progress.enter_all。委託先に見えない活動は無い活動と同じ 404（9.2 の注記）。
    確定済みの報告日は 409 ``INVALID_STATE``（BR-13）。実績開始日より前の実績終了日・直接入力以外の方式での進捗率は 400。保存する進捗率は RULE-PRG-001 の算定値
    （実績終了日があれば 100）。残工数の空は自動（EFF-06）。
    """
    view = progress_service.put_progress(
        session, access, user, project_id, report_id, activity_id,
        percent_complete=_decimal(body.percent_complete), actual_start=body.actual_start,
        actual_finish=body.actual_finish, remaining_hours=_decimal(body.remaining_hours),
        delay_reason=body.delay_reason, forecast_finish=body.forecast_finish, now=now,
    )
    e = view.entry
    return ProgressEntryOut(
        status_report_id=e.status_report_id, activity_id=e.activity_id, progress_method=view.method.value,
        percent_complete=_num(e.percent_complete), actual_start=e.actual_start, actual_finish=e.actual_finish,
        remaining_hours=_num(e.remaining_hours),
        remaining_hours_source="ENTERED" if e.remaining_hours is not None else "AUTO",
        delay_reason=e.delay_reason, forecast_finish=e.forecast_finish, entered_by=e.entered_by,
        entered_at=e.entered_at,
    )


@router.get("/projects/{project_id}/activities/{activity_id}/progress-items", response_model=ProgressItemsOut)
def get_progress_items(
    project_id: int,
    activity_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
) -> ProgressItemsOut:
    """API-PRG-010 進捗の算定項目を読む。

    権限は project.view。応答は API-PRG-004 と同じ形（算定方式・今の進捗率・項目）。委託先に見えない活動は 404（設計書 1.1・CR-004）。
    """
    return _items_out(progress_service.get_progress_items(session, access, user, project_id, activity_id))


@router.put("/projects/{project_id}/activities/{activity_id}/progress-items", response_model=ProgressItemsOut)
def replace_progress_items(
    project_id: int,
    activity_id: int,
    body: ProgressItemsReplace,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
    now: datetime = Depends(get_now),
) -> ProgressItemsOut:
    """API-PRG-004 進捗の算定項目を置き換える。

    権限は progress.enter_own（自分の割当の活動）か progress.enter_all。委託先に見えない活動は無い活動と同じ 404（9.2 の注記）。
    ``id`` を載せた項目は変え、載せない項目は足し、載せなかった既存の項目は消す（他の活動の項目の ``id`` は 400）。
    ``approved`` を変えるのは progress.enter_all を持つ人だけ（enter_own だけの人が変えると 403。足す項目を真にするのも変える。
    保存済みと同じ値を送るのは変えたことにしない）。OPEN の報告日の進捗率を求め直す（BR-06）。
    """
    view = progress_service.replace_progress_items(
        session, access, user, project_id, activity_id, [i.model_dump() for i in body.items], now=now
    )
    return _items_out(view)


@router.put("/projects/{project_id}/activities/{activity_id}/progress-method", response_model=ProgressMethodOut)
def set_progress_method(
    project_id: int,
    activity_id: int,
    body: ProgressMethodIn,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
) -> ProgressMethodOut:
    """API-PRG-005 活動の進捗率の算定方式を変える。

    権限は schedule.edit。BR-06 の 7 つの方式か null（プロジェクトの既定に従う）。OPEN の報告日の進捗率を新しい方式で求め直す。
    """
    view = progress_service.set_progress_method(session, access, user, project_id, activity_id, body.progress_method)
    return ProgressMethodOut(activity_id=view.activity.id, progress_method=view.activity.progress_method,
                             effective_method=view.effective_method.value, project_default=view.project_default.value)


@router.get("/projects/{project_id}/status-reports/{report_id}/lightning-line", response_model=LightningLineOut)
def get_lightning_line(
    project_id: int,
    report_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
) -> LightningLineOut:
    """API-PRG-006 報告日のイナズマ線（報告日線・ポイント・判定）。

    権限は project.view。OPEN の報告日は今の計画とその報告日の入力から計算し（RULE-CAL-002・RULE-PRG-001〜005）、
    確定済みの報告日はスナップショットの写しだけから描く（BR-13）。行はガントの行の順。委託先は自分の割当の活動だけ。
    """
    view = progress_service.lightning_line(session, access, user, project_id, report_id)
    line = view.line
    rows = []
    for r in view.rows:
        v = r.vertex
        entry = r.entry
        rows.append(
            LightningRowOut(
                row_index=v.row_index, activity_id=v.activity_id, wbs_code=r.wbs_code, name=r.name,
                is_milestone=r.is_milestone, is_critical=r.is_critical, start_date=r.start_date,
                finish_date=r.finish_date, duration_days=r.duration_days,
                progress_method=None if r.progress_method is None else r.progress_method.value,
                percent_complete=_num(r.percent_complete), actual_start=r.actual_start, actual_finish=r.actual_finish,
                remaining_hours=_num(entry.remaining_hours) if entry is not None else None,
                delay_reason=entry.delay_reason if entry is not None else None,
                forecast_finish=entry.forecast_finish if entry is not None else None,
                point=_axis(v.point, v.coordinate), deviation=float(v.deviation), judgement=v.judgement.value,
                counted_as=v.judgement.counted_as.value, display_date=v.display_date,
            )
        )
    return LightningLineOut(
        report=_report_out(view.report), source=view.source, project_start=view.project_start,  # type: ignore[arg-type]
        report_line=_axis(line.report_line, line.report_line_coordinate), tolerance_days=float(line.tolerance_days),
        rows=rows, restricted=view.restricted,
    )


@router.post("/projects/{project_id}/status-reports/{report_id}/finalize", response_model=FinalizeOut)
def finalize_status_report(
    project_id: int,
    report_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
    links: ProgressLinks = Depends(get_progress_links),
    now: datetime = Depends(get_now),
) -> FinalizeOut:
    """API-PRG-007 報告日を確定してスナップショットを作る。

    権限は status_report.manage。確定済みなら 409 ``INVALID_STATE``（OPEN → FINALIZED。戻さない）。全ての活動の計画日付・期間・
    進捗率・実績日付・実績工数・ETC・進捗ポイント・ずれ・判定を写す（RULE-PRG-007・BR-13）。確定の後はその報告日へ入力できない。
    """
    view = progress_service.finalize(session, access, user, links, project_id, report_id, now=now)
    s = view.snapshot
    return FinalizeOut(report=_report_out(view.report), snapshot_id=s.id, report_line=s.report_line,
                       tolerance_days=float(s.tolerance_days), extract_days=float(s.extract_days),
                       plan_basis=s.plan_basis, item_count=view.item_count)


@router.get("/projects/{project_id}/lightning-lines", response_model=LightningLinesOut)
def get_lightning_lines(
    project_id: int,
    report_ids: list[int] | None = Query(None),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
) -> LightningLinesOut:
    """API-PRG-008 確定済みの複数の報告日の線（`report_ids`）。

    権限は project.view。``report_ids`` は繰り返して渡す（``?report_ids=1&report_ids=2``）。空・重複は 400、無い報告日は 404、
    確定していない報告日は 409 ``INVALID_STATE``。写しの値だけから描き（BR-13）、今のガントの行へ置き直して報告日の古い順に重ねる（PRG-18）。
    """
    view = progress_service.lightning_lines(session, access, user, project_id, report_ids or [])
    lines = []
    for o in view.lines:
        line = o.overlaid.line
        lines.append(
            OverlaidLineOut(
                style_index=o.overlaid.style_index, report_id=o.report_id, report_date=line.report_date,
                report_line=_axis(line.report_line, line.report_line_coordinate),
                tolerance_days=float(line.tolerance_days),
                vertices=[OverlaidVertexOut(row_index=v.row_index, activity_id=v.activity_id,
                                            point=_axis(v.point, v.coordinate), deviation=float(v.deviation),
                                            judgement=v.judgement.value, display_date=v.display_date)
                          for v in line.vertices],
                missing_activity_ids=list(o.overlaid.missing_activity_ids),
            )
        )
    return LightningLinesOut(project_start=view.project_start, lines=lines, restricted=view.restricted)


def _slip_out(view: DelaysView, slip: Slip) -> SlipOut:
    return SlipOut(activity_id=slip.activity_id, name=view.plan.activity_by_id[slip.activity_id].name,
                   is_milestone=slip.is_milestone, before=slip.before, after=slip.after, before_date=slip.before_date,
                   after_date=slip.after_date, slip_days=slip.slip_days)


def _finish_out(view: DelaysView, finish: FinishSlip) -> FinishSlipOut | None:
    if view.restricted:
        return None
    return FinishSlipOut(before_date=finish.before_date, after_date=finish.after_date, slip_days=finish.slip_days)


@router.get("/projects/{project_id}/status-reports/{report_id}/delays", response_model=DelaysOut)
def get_delays(
    project_id: int,
    report_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
) -> DelaysOut:
    """API-PRG-009 遅延活動と影響。

    権限は project.view。``δ ≤ −遅延の抽出の閾値`` の活動をずれの小さい順に返し、見込みの終了から後続・クリティカルパス・
    マイルストーン・プロジェクトの終了への影響を返す（RULE-PRG-006・5.7）。遅延理由・完了見込み日はその報告日の入力（PRG-03）。
    委託先には自分の割当の活動だけを返し、プロジェクトの終了のずれは返さない。
    """
    view = progress_service.delays(session, access, user, project_id, report_id)
    a = view.analysis
    shown = view.visible
    activities = view.plan.activity_by_id
    elements = view.plan.element_by_id

    def visible(activity_id: int) -> bool:
        return shown is None or activity_id in shown

    delays = []
    for d in a.delays:
        if not visible(d.activity_id):
            continue
        activity = activities[d.activity_id]
        entry = view.entries.get(d.activity_id)
        delays.append(
            DelayOut(
                activity_id=d.activity_id, wbs_code=elements[activity.work_package_id].wbs_code, name=activity.name,
                deviation=float(d.deviation), percent_complete=_num(d.percent_complete),
                delay_reason=entry.delay_reason if entry is not None else None,
                forecast_finish=entry.forecast_finish if entry is not None else None,
                expected_finish=d.forecast_finish, expected_finish_date=d.forecast_finish_date,
                fixed_finish=_slip_out(view, d.fixed_finish), is_critical=d.is_critical,
                transitive_successor_ids=[s for s in d.transitive_successors if visible(s)],
                successor_slips=[_slip_out(view, s) for s in d.successor_slips if visible(s.activity_id)],
                milestone_slips=[_slip_out(view, s) for s in d.milestone_slips if visible(s.activity_id)],
                project_finish=_finish_out(view, d.project_finish),
            )
        )
    return DelaysOut(
        report=_report_out(view.report), source=view.source, report_line=a.report_line,  # type: ignore[arg-type]
        extract_days=float(a.extract_days), delays=delays,
        combined_milestone_slips=[_slip_out(view, s) for s in a.combined_milestone_slips if visible(s.activity_id)],
        combined_project_finish=_finish_out(view, a.combined_project_finish), restricted=view.restricted,
    )
