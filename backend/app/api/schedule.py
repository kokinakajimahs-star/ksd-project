"""スケジュールの API（設計書 6.2 の API-SCH-001〜013）：活動・依存・日程計算・クリティカルパス・ガント・スケジュールベースライン。"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_project_access
from app.domain.gantt import AxisPoint, GanttBar, GanttChart
from app.models.sch import ActivityDependency, ScheduleBaseline
from app.schemas.schedule import (
    ActivityBulkUpdate,
    ActivityCreate,
    ActivityList,
    ActivityOut,
    ActivityUpdate,
    AssigneeOut,
    AxisPointOut,
    BaselineCreate,
    BaselineList,
    BaselineOut,
    BaselineRef,
    BulkUpdateOut,
    CalendarPointOut,
    DependencyCreate,
    DependencyOut,
    GanttBarOut,
    GanttLinkOut,
    GanttOut,
    GanttRowOut,
    ScheduledActivityOut,
    ScheduleOut,
    VarianceOut,
    VarianceRowOut,
)
from app.services import get_session
from app.services import schedule as schedule_service
from app.services.auth import User
from app.services.clock import get_now
from app.services.project_access import ProjectAccess
from app.services.schedule import ActivityView, BaselineView, ScheduleLinks, ScheduleView

router = APIRouter(tags=["schedule"])


def get_schedule_links(request: Request) -> ScheduleLinks:
    """アプリに載せた別の群の表（割当・最新の進捗）の口（``app.services.schedule``）。"""
    links: ScheduleLinks = request.app.state.schedule_links
    return links


def _num(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _activity_out(view: ActivityView) -> ActivityOut:
    a = view.activity
    return ActivityOut(
        id=a.id,
        project_id=a.project_id,
        work_package_id=a.work_package_id,
        wbs_code=view.work_package.wbs_code,
        work_package_name=view.work_package.name,
        name=a.name,
        sort_order=a.sort_order,
        requested_start=a.requested_start,
        duration_days=a.duration_days,
        is_milestone=a.is_milestone,
        estimate_hours=_num(a.estimate_hours),
        progress_method=a.progress_method,
        start_date=a.start_date,
        finish_date=a.finish_date,
        early_start=a.early_start,
        early_finish=a.early_finish,
        late_start=a.late_start,
        late_finish=a.late_finish,
        total_float=a.total_float,
        is_critical=a.is_critical,
        predecessor_ids=list(view.predecessor_ids),
        successor_ids=list(view.successor_ids),
        assignees=[AssigneeOut(user_id=v.user_id, display_name=v.display_name, planned_hours=_num(v.planned_hours))
                   for v in view.assignees],
    )


def _dependency_out(d: ActivityDependency) -> DependencyOut:
    return DependencyOut(id=d.id, predecessor_id=d.predecessor_id, successor_id=d.successor_id, dep_type=d.dep_type,
                         lag_days=d.lag_days)


def _schedule_out(view: ScheduleView) -> ScheduleOut:
    elements = view.plan.element_by_id
    rows = []
    for a in view.activities:
        s = view.schedule.activities[a.id]
        rows.append(
            ScheduledActivityOut(
                activity_id=a.id, name=a.name, wbs_code=elements[a.work_package_id].wbs_code,
                is_milestone=a.is_milestone, start_date=s.start_date, finish_date=s.finish_date,
                early_start=s.early_start, early_finish=s.early_finish, late_start=s.late_start,
                late_finish=s.late_finish, total_float=s.total_float, is_critical=s.is_critical,
            )
        )
    return ScheduleOut(
        project_finish_date=view.schedule.project_finish_date if not view.restricted else None,
        critical_path=[a.id for a in view.critical_path],
        min_total_float=min((r.total_float for r in rows), default=None),
        activities=rows,
        restricted=view.restricted,
    )


def _point(p: AxisPoint | None) -> AxisPointOut | None:
    if p is None:
        return None
    return AxisPointOut(position=float(p.position),
                        calendar=CalendarPointOut(date=p.calendar.date, fraction=float(p.calendar.fraction)))


def _bar(bar: GanttBar | None) -> GanttBarOut | None:
    if bar is None:
        return None
    start, end = _point(bar.start), _point(bar.end)
    assert start is not None and end is not None
    return GanttBarOut(kind=bar.kind.value, is_milestone=bar.is_milestone, start=start, end=end,
                       start_date=bar.start_date, end_date=bar.end_date, open_ended=bar.open_ended)


def _baseline_ref(b: ScheduleBaseline) -> BaselineRef:
    return BaselineRef(id=b.id, version=b.version, name=b.name)


def _baseline_out(view: BaselineView) -> BaselineOut:
    b = view.baseline
    return BaselineOut(id=b.id, project_id=b.project_id, version=b.version, name=b.name, reason=b.reason,
                       change_request_id=b.change_request_id, created_by=b.created_by, created_at=b.created_at,
                       item_count=view.item_count)


@router.get("/projects/{project_id}/activities", response_model=ActivityList)
def list_activities(
    project_id: int,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
    links: ScheduleLinks = Depends(get_schedule_links),
) -> ActivityList:
    """API-SCH-001 活動の一覧（日程・依存・担当付き）。

    権限は project.view（委託先は自分の割当の活動だけ。``restricted`` が真）。行の順は WBS の並び → WP の中の表示順。
    ``dependencies`` は読める活動どうしの依存（API-SCH-007 で消すときの ID を持つ）。``limit``・``offset``（6.1）。
    """
    view = schedule_service.list_activities(session, access, user, links, project_id, limit=limit, offset=offset)
    return ActivityList(items=[_activity_out(v) for v in view.items], total=view.total, limit=limit, offset=offset,
                        dependencies=[_dependency_out(d) for d in view.dependencies], restricted=view.restricted)


@router.post("/projects/{project_id}/activities", response_model=ActivityOut, status_code=201)
def create_activity(
    project_id: int,
    body: ActivityCreate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
    links: ScheduleLinks = Depends(get_schedule_links),
) -> ActivityOut:
    """API-SCH-002 活動を足す（WPの配下。日程を再計算する）。

    権限は schedule.edit。開始日・期間・終了日は 2 つから残りを求める（SCH-03。RULE-CAL-001）。期間 0 はマイルストーン。
    WP 以外の要素の下は 422 ``ACTIVITY_NOT_ON_WORK_PACKAGE``（RULE-WBS-003）。足した後に全ての活動の日程を計算し直す。
    """
    view = schedule_service.add_activity(
        session, access, user, links, project_id,
        work_package_id=body.work_package_id, name=body.name, requested_start=body.requested_start,
        requested_finish=body.requested_finish, duration_days=body.duration_days, is_milestone=body.is_milestone,
    )
    return _activity_out(view)


@router.patch("/projects/{project_id}/activities/{activity_id}", response_model=ActivityOut)
def update_activity(
    project_id: int,
    activity_id: int,
    body: ActivityUpdate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
    links: ScheduleLinks = Depends(get_schedule_links),
) -> ActivityOut:
    """API-SCH-003 活動を変える（日程を再計算する）。

    権限は schedule.edit。載せた項目だけを変える。終了日だけなら期間を保って開始日を動かす（SCH-03）。
    """
    view = schedule_service.update_activity(
        session, access, user, links, project_id, activity_id, body.model_dump(exclude_unset=True)
    )
    return _activity_out(view)


@router.patch("/projects/{project_id}/activities", response_model=BulkUpdateOut)
def bulk_update_activities(
    project_id: int,
    body: ActivityBulkUpdate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
    links: ScheduleLinks = Depends(get_schedule_links),
) -> BulkUpdateOut:
    """API-SCH-004 活動をまとめて変える（全件か無しか）。

    権限は schedule.edit。RULE-SCH-006 で全件を検査し、1 件でも不正なら何も保存せず 422（``details`` に全ての不正）。
    """
    changes = [c.model_dump(exclude_unset=True) | {"activity_id": c.activity_id} for c in body.changes]
    views = schedule_service.bulk_update_activities(session, access, user, links, project_id, changes)
    return BulkUpdateOut(items=[_activity_out(v) for v in views])


@router.delete("/projects/{project_id}/activities/{activity_id}", status_code=204)
def delete_activity(
    project_id: int,
    activity_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
    links: ScheduleLinks = Depends(get_schedule_links),
) -> Response:
    """API-SCH-005 活動を消す。

    権限は schedule.edit。依存と割当も消し、ベースラインの写しは残す（紐付けだけ空にする。SCH-18 の削除された活動）。日程を計算し直す。
    """
    schedule_service.remove_activity(session, access, user, links, project_id, activity_id)
    return Response(status_code=204)


@router.post("/projects/{project_id}/dependencies", response_model=DependencyOut, status_code=201)
def create_dependency(
    project_id: int,
    body: DependencyCreate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
) -> DependencyOut:
    """API-SCH-006 依存を足す（循環を拒む）。

    権限は schedule.edit。循環するなら 422 ``SCHEDULE_CYCLE``（循環の活動を返す。RULE-SCH-001）、同じ組は 409、
    FS・ラグ 0 以外は 422 ``UNSUPPORTED_DEPENDENCY``（R1）。足した後に日程を計算し直す。
    """
    dependency = schedule_service.add_dependency(
        session, access, user, project_id, predecessor_id=body.predecessor_id, successor_id=body.successor_id,
        dep_type=body.dep_type, lag_days=body.lag_days,
    )
    return _dependency_out(dependency)


@router.delete("/projects/{project_id}/dependencies/{dependency_id}", status_code=204)
def delete_dependency(
    project_id: int,
    dependency_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
) -> Response:
    """API-SCH-007 依存を消す。

    権限は schedule.edit。このプロジェクトに無ければ 404。消した後に日程を計算し直す。
    """
    schedule_service.remove_dependency(session, access, user, project_id, dependency_id)
    return Response(status_code=204)


@router.post("/projects/{project_id}/schedule/calculate", response_model=ScheduleOut)
def calculate_schedule(
    project_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
) -> ScheduleOut:
    """API-SCH-008 日程を計算し直す。

    権限は schedule.edit。前進・後退計算（RULE-SCH-002・003）の結果を保存して返す。循環があれば 422 ``SCHEDULE_CYCLE``。
    """
    return _schedule_out(schedule_service.recalculate(session, access, user, project_id))


@router.get("/projects/{project_id}/schedule/critical-path", response_model=ScheduleOut)
def get_critical_path(
    project_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
) -> ScheduleOut:
    """API-SCH-009 クリティカルパスとフロート。

    権限は project.view。``critical_path`` はフロート 0 以下の活動（トポロジカル順。RULE-SCH-003）、``activities`` は行の順の全ての活動の
    フロート（稼働日）。委託先は自分の割当の活動だけ（プロジェクトの終了日は返さない）。
    """
    return _schedule_out(schedule_service.critical_path(session, access, user, project_id))


@router.get("/projects/{project_id}/gantt", response_model=GanttOut)
def get_gantt(
    project_id: int,
    baseline_id: int | None = Query(None),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
    links: ScheduleLinks = Depends(get_schedule_links),
) -> GanttOut:
    """API-SCH-010 ガントの描画データ（`baseline_id` 任意）。

    権限は project.view。RULE-SCH-005 の行・バー（予定・実績・基準）・依存線・ラベル・非稼働日。``baseline_id`` を省けば最新の版
    （版が無ければ基準のバーは無い）。無い版・他のプロジェクトの版は 404。
    """
    view = schedule_service.gantt(session, access, user, links, project_id, baseline_id)
    return gantt_out(view.chart, project_start=view.project_start, report_date=view.report_date,
                     baseline=view.baseline, restricted=view.restricted)


def gantt_out(chart: GanttChart, *, project_start: date, report_date: date | None, baseline: ScheduleBaseline | None,
              restricted: bool) -> GanttOut:
    """ガントの描画データ（RULE-SCH-005 の結果）を応答の形にする。会議レポート・共有ビュー（app.api.report）も使う。"""
    return GanttOut(
        project_start=project_start,
        report_date=report_date,
        baseline=None if baseline is None else _baseline_ref(baseline),
        range_start=chart.range_start,
        range_end=chart.range_end,
        non_working_days=list(chart.non_working_days),
        rows=[
            GanttRowOut(
                row_index=r.row_index, activity_id=r.activity_id, name=r.name, work_package_id=r.work_package_id,
                wbs_code=r.wbs_code, work_package_name=r.work_package_name, depth=r.depth, is_milestone=r.is_milestone,
                is_critical=r.is_critical, total_float=r.total_float, planned=_bar(r.planned), actual=_bar(r.actual),
                baseline=_bar(r.baseline), assignees=list(r.assignees), percent_complete=_num(r.percent_complete),
            )
            for r in chart.rows
        ],
        links=[
            GanttLinkOut(predecessor_id=link.predecessor_id, successor_id=link.successor_id, from_row=link.from_row,
                         to_row=link.to_row, start=_point(link.start), end=_point(link.end))
            for link in chart.links
        ],
        restricted=restricted,
    )


@router.post("/projects/{project_id}/schedule-baselines", response_model=BaselineOut, status_code=201)
def create_schedule_baseline(
    project_id: int,
    body: BaselineCreate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
    now: datetime = Depends(get_now),
) -> BaselineOut:
    """API-SCH-011 スケジュールベースラインを保存する。

    権限は baseline.manage。今の全ての活動の開始日・終了日・期間・見積と、WP の見積の集計を新しい版（連番）に固定する（SCH-16・BR-12）。
    """
    view = schedule_service.save_baseline(session, access, user, project_id, name=body.name, now=now)
    return _baseline_out(view)


@router.get("/projects/{project_id}/schedule-baselines", response_model=BaselineList)
def list_schedule_baselines(
    project_id: int,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
) -> BaselineList:
    """API-SCH-012 スケジュールベースラインの版の一覧。

    権限は project.view。版の順。``limit``・``offset``（6.1）。
    """
    views, total = schedule_service.list_baselines(session, access, user, project_id, limit=limit, offset=offset)
    return BaselineList(items=[_baseline_out(v) for v in views], total=total, limit=limit, offset=offset)


@router.get("/projects/{project_id}/schedule-baselines/{baseline_id}/variance", response_model=VarianceOut)
def get_schedule_variance(
    project_id: int,
    baseline_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
) -> VarianceOut:
    """API-SCH-013 基準と最新計画の差異。

    権限は project.view。RULE-SCH-004 で活動ごとの開始・終了の差（稼働日）と見積の差（人時）、追加・削除の区別（SCH-18）。
    無い版・他のプロジェクトの版は 404。
    """
    view = schedule_service.variance(session, access, user, project_id, baseline_id)
    return VarianceOut(
        baseline=_baseline_ref(view.baseline),
        rows=[
            VarianceRowOut(
                status=r.status.value, activity_id=r.activity_id, wbs_code=r.wbs_code, name=r.name,
                baseline_start=r.baseline_start, baseline_finish=r.baseline_finish, current_start=r.current_start,
                current_finish=r.current_finish, start_variance_days=r.start_variance_days,
                finish_variance_days=r.finish_variance_days, baseline_estimate_hours=_num(r.baseline_estimate_hours),
                current_estimate_hours=_num(r.current_estimate_hours),
                estimate_variance_hours=_num(r.estimate_variance_hours),
            )
            for r in view.rows
        ],
        restricted=view.restricted,
    )
