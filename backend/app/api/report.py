"""レポート・共有の API（設計書 6.2 の API-RPT-001〜004）：定例会議用レポートのデータ・共有リンクを作る・失効させる・共有リンクで閲覧する。"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_project_access
from app.api.schedule import gantt_out, get_schedule_links
from app.schemas.progress import OverlaidVertexOut
from app.schemas.report import (
    MeetingReportOut,
    ReportDelayOut,
    ReportKpiOut,
    ReportLightningOut,
    ReportMilestoneOut,
    ReportWindowOut,
    ShareLinkCreate,
    ShareLinkCreated,
    ShareLinkList,
    ShareLinkOut,
    SharedProjectOut,
    SharedViewOut,
)
from app.schemas.schedule import AxisPointOut, CalendarPointOut
from app.services import get_session
from app.services import report as report_service
from app.services.auth import User
from app.services.clock import get_now
from app.services.project_access import ProjectAccess
from app.services.report import MeetingReportView, ShareLink
from app.services.schedule import ScheduleLinks

router = APIRouter(tags=["report"])


def _num(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _meeting_report_out(view: MeetingReportView) -> MeetingReportOut:
    data = view.report_data
    line = data.lightning
    kpi = data.kpi
    return MeetingReportOut(
        report_id=view.report.id,
        report_date=data.report_date,
        status=view.report.status,
        source=view.source,
        project_start=view.project_start,
        window=ReportWindowOut(start=data.window.start, end=data.window.end),
        gantt=gantt_out(data.gantt, project_start=view.project_start, report_date=data.report_date,
                        baseline=view.baseline, restricted=view.restricted),
        lightning=ReportLightningOut(
            report_date=line.report_date,
            report_line=AxisPointOut(position=float(line.report_line),
                                     calendar=CalendarPointOut(date=line.report_line_coordinate.date,
                                                               fraction=float(line.report_line_coordinate.fraction))),
            tolerance_days=float(line.tolerance_days),
            vertices=[
                OverlaidVertexOut(
                    row_index=v.row_index, activity_id=v.activity_id,
                    point=AxisPointOut(position=float(v.point),
                                       calendar=CalendarPointOut(date=v.coordinate.date,
                                                                 fraction=float(v.coordinate.fraction))),
                    deviation=float(v.deviation), judgement=v.judgement.value, display_date=v.display_date,
                )
                for v in line.vertices
            ],
            missing_activity_ids=list(data.missing_activity_ids),
        ),
        delays=[
            ReportDelayOut(
                activity_id=d.activity_id, wbs_code=d.wbs_code, name=d.name, deviation=float(d.deviation),
                percent_complete=_num(d.percent_complete), planned_finish=d.planned_finish,
                forecast_finish=d.forecast_finish, forecast_entered=d.forecast_entered, delay_reason=d.delay_reason,
                is_critical=d.is_critical,
            )
            for d in data.delays
        ],
        milestones=[
            ReportMilestoneOut(activity_id=m.activity_id, wbs_code=m.wbs_code, name=m.name, planned_date=m.planned_date,
                               forecast_date=m.forecast_date, slip_days=m.slip_days)
            for m in data.milestones
        ],
        kpi=None if view.restricted else ReportKpiOut(
            overall_progress=_num(kpi.overall_progress), behind_count=kpi.behind_count,
            eac_hours=_num(kpi.eac_hours), baseline_hours=_num(kpi.baseline_hours),
            eac_variance_hours=_num(kpi.eac_variance_hours), min_critical_float=kpi.min_critical_float,
            overdue_action_count=kpi.overdue_action_count,
        ),
        restricted=view.restricted,
    )


def _share_link_out(link: ShareLink) -> ShareLinkOut:
    return ShareLinkOut(id=link.id, project_id=link.project_id, scope=link.scope, expires_at=link.expires_at,
                        revoked_at=link.revoked_at, created_by=link.created_by, created_at=link.created_at)


@router.get("/projects/{project_id}/status-reports/{report_id}/meeting-report", response_model=MeetingReportOut)
def get_meeting_report(
    project_id: int,
    report_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
    links: ScheduleLinks = Depends(get_schedule_links),
) -> MeetingReportOut:
    """API-RPT-001 定例会議用レポートのデータ。

    権限は project.view。7.4 の 5 つの欄（上段：ガント＋イナズマ線と表示の範囲＝報告日の前後 4 週、下段：遅延一覧・
    マイルストーンの状況・主要 KPI）を RULE-RPT-001 で組み立てて返す。進捗の値は、確定済みの報告日は写しの値（``SNAPSHOT``。
    BR-13）、確定前は計算値（``CALCULATED``）。計画の値（ガントのバー、マイルストーンの予定日・見込み日・ずれ）はどちらも
    表示した時点の最新計画（7.4。設計書 1.2・CR-013）。無い報告日・他のプロジェクトの報告日は 404、日程が計算されていない活動が
    あれば 409。
    委託先だけで読む利用者には自分の割当の活動だけを返し、KPI は返さない（``restricted``）。
    """
    view = report_service.meeting_report(session, access, user, links, project_id, report_id)
    return _meeting_report_out(view)


@router.post("/projects/{project_id}/share-links", response_model=ShareLinkCreated, status_code=201)
def create_share_link(
    project_id: int,
    body: ShareLinkCreate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
    now: datetime = Depends(get_now),
) -> ShareLinkCreated:
    """API-RPT-002 共有リンクを作る（トークンはこの応答でだけ返す）。

    権限は share.manage。``scope`` は GANTT か REPORT、``expires_at`` は時差つきで今より後（有効期限付き。RPT-03）。
    DB にはトークンの SHA-256 のハッシュだけを保存する（4.2・NFR-04）。
    """
    created = report_service.create_share_link(session, access, user, project_id, scope=body.scope,
                                               expires_at=body.expires_at, now=now)
    out = _share_link_out(created.link)
    return ShareLinkCreated(**out.model_dump(), token=created.token)


@router.get("/projects/{project_id}/share-links", response_model=ShareLinkList)
def list_share_links(
    project_id: int,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
) -> ShareLinkList:
    """API-RPT-005 共有リンクの一覧（トークンは返さない）。

    権限は share.manage。新しい順（ID の降順）。失効・期限切れのリンクも含む。``limit``・``offset``（6.1。設計書 1.1・CR-004）。
    """
    links, total = report_service.list_share_links(session, access, user, project_id, limit=limit, offset=offset)
    return ShareLinkList(project_id=project_id, items=[_share_link_out(link) for link in links],
                         total=total, limit=limit, offset=offset)


@router.delete("/projects/{project_id}/share-links/{link_id}", status_code=204)
def revoke_share_link(
    project_id: int,
    link_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
    now: datetime = Depends(get_now),
) -> Response:
    """API-RPT-003 共有リンクを失効させる。

    権限は share.manage。行は消さずに失効の時刻を入れ、以後このリンクでは閲覧できない（RPT-03）。
    無いリンク・他のプロジェクトのリンクは 404、既に失効させたリンクは 409 ``INVALID_STATE``。
    """
    report_service.revoke_share_link(session, access, user, project_id, link_id, now=now)
    return Response(status_code=204)


@router.get("/shared/{token}", response_model=SharedViewOut)
def get_shared(
    token: str,
    session: Session = Depends(get_session),
    links: ScheduleLinks = Depends(get_schedule_links),
    now: datetime = Depends(get_now),
) -> SharedViewOut:
    """API-RPT-004 共有リンクで閲覧する（認証なし・読むだけ）。

    認証を要らない唯一の API（6.1）。トークンを RULE-AUTH-002 で検証し、リンクの範囲だけを見せる：GANTT は最新の進捗・
    最新の基準のガント、REPORT は 4.2 の最新の報告日の会議レポート（報告日が無ければ null）。トークンが無い・合わない・
    失効した・期限が切れたリンクは、どれも同じ 404（状態・コード・メッセージ・``details`` が同じ。理由は返さない）。
    """
    view = report_service.shared_view(session, links, token, now=now)
    project = view.project
    return SharedViewOut(
        scope=view.link.scope,
        expires_at=view.link.expires_at,
        project=SharedProjectOut(id=project.id, code=project.code, name=project.name),
        gantt=None if view.gantt is None else gantt_out(view.gantt, project_start=project.start_date,
                                                        report_date=view.gantt_report_date,
                                                        baseline=view.gantt_baseline, restricted=False),
        meeting_report=None if view.meeting_report is None else _meeting_report_out(view.meeting_report),
    )
