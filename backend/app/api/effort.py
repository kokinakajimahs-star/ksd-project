"""工数の API（設計書 6.2 の API-EFF-001〜007）：割当・実績工数・多軸集計・差異の分析と理由。"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_project_access
from app.domain.effort import EffortValues, OrganizationKey, PeriodKey
from app.models.prg import StatusReport
from app.models.sch import ScheduleBaseline
from app.schemas.effort import (
    ActivityAssignmentsOut,
    AssignmentList,
    AssignmentOut,
    AssignmentsReplace,
    AxisCode,
    BaselineRefOut,
    DayTotalOut,
    EffortSummaryOut,
    EffortValuesOut,
    EffortVarianceOut,
    ReportRefOut,
    SummaryRowOut,
    TimeEntriesPut,
    TimeEntriesWrittenOut,
    TimeEntryList,
    TimeEntryOut,
    VarianceNoteIn,
    VarianceNoteOut,
    VarianceRowOut,
)
from app.services import effort as effort_service
from app.services import get_session
from app.services.auth import User
from app.services.clock import get_now
from app.services.effort import AssignmentRow, EffortLinks, NoteView, SummaryView, TimeEntryCell, TimeEntryRow
from app.services.project_access import ProjectAccess

router = APIRouter(tags=["effort"])


def get_effort_links(request: Request) -> EffortLinks:
    """アプリに載せた別の群の表（是正措置）の口（``app.services.effort``）。"""
    links: EffortLinks = request.app.state.effort_links
    return links


def _num(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _decimal(value: float) -> Decimal:
    return Decimal(str(value))


def _values(v: EffortValues) -> EffortValuesOut:
    return EffortValuesOut(planned=float(v.planned), baseline=_num(v.baseline), actual=float(v.actual), etc=float(v.etc),
                           eac=float(v.eac))


def _report_ref(report: StatusReport | None) -> ReportRefOut | None:
    return None if report is None else ReportRefOut(id=report.id, report_date=report.report_date, status=report.status)


def _baseline_ref(baseline: ScheduleBaseline) -> BaselineRefOut:
    return BaselineRefOut(id=baseline.id, version=baseline.version, name=baseline.name)


def _assignment_out(row: AssignmentRow) -> AssignmentOut:
    return AssignmentOut(activity_id=row.activity.id, activity_name=row.activity.name, wbs_code=row.wbs_code,
                         user_id=row.assignment.user_id, display_name=row.display_name,
                         planned_hours=_num(row.assignment.planned_hours))


def _entry_out(row: TimeEntryRow) -> TimeEntryOut:
    e = row.entry
    return TimeEntryOut(id=e.id, user_id=e.user_id, project_id=row.project_id, activity_id=e.activity_id,
                        activity_name=row.activity_name, wbs_code=row.wbs_code, work_date=e.work_date,
                        hours=float(e.hours), note=e.note)


def _note_out(view: NoteView) -> VarianceNoteOut:
    n = view.note
    return VarianceNoteOut(id=n.id, element_id=n.element_id, wbs_code=view.element.wbs_code,
                           status_report_id=n.status_report_id, reason=n.reason,
                           corrective_action_id=n.corrective_action_id, created_by=n.created_by,
                           created_at=n.created_at)


@router.put("/projects/{project_id}/activities/{activity_id}/assignments", response_model=ActivityAssignmentsOut)
def replace_assignments(
    project_id: int,
    activity_id: int,
    body: AssignmentsReplace,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
) -> ActivityAssignmentsOut:
    """API-EFF-001 活動の担当と割当工数を置き換える。

    権限は schedule.edit。載せた担当の行を作る・割当工数を変え、載せなかった担当を外す（1 つの活動に複数の担当。EFF-01）。
    担当はプロジェクトの有効なメンバーだけ（違えば 400）。応答に割当工数の合計と見積との差を付ける（EFF-01）。
    """
    view = effort_service.replace_assignments(session, access, user, project_id, activity_id,
                                              [a.model_dump() for a in body.assignments])
    return ActivityAssignmentsOut(
        activity_id=view.activity.id, estimate_hours=_num(view.activity.estimate_hours),
        planned_hours_total=float(view.planned_total), difference_hours=_num(view.difference),
        assignments=[_assignment_out(r) for r in view.rows],
    )


@router.get("/projects/{project_id}/assignments", response_model=AssignmentList)
def list_assignments(
    project_id: int,
    user_id: int | None = Query(None),
    activity_id: int | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
) -> AssignmentList:
    """API-EFF-002 割当の一覧（担当者・活動で絞れる）。

    権限は project.view。``user_id``・``activity_id`` で絞る。並びはガントの行の順→利用者。委託先は自分の割当の活動の分だけ。
    """
    rows, total = effort_service.list_assignments(session, access, user, project_id, user_id=user_id,
                                                  activity_id=activity_id, limit=limit, offset=offset)
    return AssignmentList(items=[_assignment_out(r) for r in rows], total=total, limit=limit, offset=offset)


@router.put("/time-entries", response_model=TimeEntriesWrittenOut)
def put_time_entries(
    body: TimeEntriesPut,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
) -> TimeEntriesWrittenOut:
    """API-EFF-003 自分の実績工数をまとめて書く（日×活動）。

    権限は effort.enter_own（自分の割当の活動）。マスごとにその日・その活動の値で置き換える（0 は行を消す）。RULE-EFF-005 で全ての
    マスを検査し、1 件でも問題があれば何も書かない：値の誤り・重複 400、自分の割当でない 403、確定した報告日より前の日 409
    ``INVALID_STATE``、1 日の合計 24 時間超 422 ``DAY_TOTAL_EXCEEDED``（EFF-02）。
    """
    cells = [TimeEntryCell(activity_id=c.activity_id, work_date=c.work_date, hours=_decimal(c.hours), note=c.note)
             for c in body.entries]
    written = effort_service.put_time_entries(session, access, user, cells)
    return TimeEntriesWrittenOut(
        user_id=written.user_id, changed=written.changed, entries=[_entry_out(r) for r in written.rows],
        day_totals=[DayTotalOut(work_date=d, hours=float(h)) for d, h in sorted(written.day_totals.items())],
    )


@router.get("/time-entries", response_model=TimeEntryList)
def list_time_entries(
    user_id: int | None = Query(None),
    project_id: int | None = Query(None),
    date_from: date | None = Query(None, alias="from"),
    date_to: date | None = Query(None, alias="to"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
) -> TimeEntryList:
    """API-EFF-004 実績工数を読む（本人、または effort.view を持つプロジェクト）。

    権限は effort.enter_own（自分の行）か effort.view（そのプロジェクトの全ての行）。``user_id``・``project_id``・``from``・``to``
    （作業日の範囲）で絞る。並びは作業日→活動→利用者。
    """
    rows, total = effort_service.list_time_entries(session, access, user, user_id=user_id, project_id=project_id,
                                                   date_from=date_from, date_to=date_to, limit=limit, offset=offset)
    return TimeEntryList(items=[_entry_out(r) for r in rows], total=total, limit=limit, offset=offset)


def _summary_rows(view: SummaryView) -> list[SummaryRowOut]:
    axes = view.axes
    plan = view.basis.plan
    if view.axis == "wbs":
        elements = plan.element_by_id
        return [
            SummaryRowOut(bucket_kind="ELEMENT", element_id=i, parent_id=elements[i].parent_id,
                          wbs_code=elements[i].wbs_code, name=elements[i].name, kind=elements[i].kind,
                          values=_values(axes.wbs[i]))
            for i in view.basis.preorder
        ]
    if view.axis == "member":
        keys = sorted(axes.assignee, key=lambda u: (u is None, u or 0))
        return [
            SummaryRowOut(bucket_kind="UNASSIGNED" if u is None else "MEMBER", user_id=u,
                          name=None if u is None else view.display_names.get(u, ""), values=_values(axes.assignee[u]))
            for u in keys
        ]
    if view.axis == "organization":
        org_keys: list[OrganizationKey] = sorted(axes.organization, key=lambda k: (k.kind.value, k.name or ""))
        return [SummaryRowOut(bucket_kind=k.kind.value, name=k.name, values=_values(axes.organization[k]))
                for k in org_keys]
    period_keys: list[PeriodKey] = list(axes.period)
    return [SummaryRowOut(bucket_kind=k.kind.value, period_start=k.start, values=_values(axes.period[k]))
            for k in period_keys]


@router.get("/projects/{project_id}/effort/summary", response_model=EffortSummaryOut)
def get_effort_summary(
    project_id: int,
    axis: AxisCode = Query("wbs"),
    report_id: int | None = Query(None),
    baseline_id: int | None = Query(None),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
) -> EffortSummaryOut:
    """API-EFF-005 多軸集計（`axis`＝wbs/member/organization/week/month、`report_id`・`baseline_id` 任意）。

    権限は effort.view。計画（今の見積）・基準（選んだ版。既定は最新）・実績・ETC・EAC を別の列で返す（EFF-08）。
    どの軸でも桶の合計は ``total`` に一致する（EFF-09。RULE-EFF-003）。報告日を指定すれば実績は報告日以前の分（5.8）。
    """
    view = effort_service.summary(session, access, user, project_id, axis=axis, report_id=report_id,
                                  baseline_id=baseline_id)
    basis = view.basis
    return EffortSummaryOut(
        axis=axis, report=_report_ref(basis.report), report_specified=basis.report_specified,
        baseline=None if basis.baseline is None else _baseline_ref(basis.baseline),
        total=_values(view.axes.total), rows=_summary_rows(view),
    )


@router.get("/projects/{project_id}/effort/variance", response_model=EffortVarianceOut)
def get_effort_variance(
    project_id: int,
    report_id: int | None = Query(None),
    baseline_id: int | None = Query(None),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
) -> EffortVarianceOut:
    """API-EFF-006 差異の分析（閾値を超えた要素）。

    権限は effort.view。差異＝EAC−基準。差異率と絶対値の両方の閾値（``projects.settings``）を超えた要素と、基準に無い要素を
    WBS の並びで返す（RULE-EFF-004・BR-11）。スケジュールベースラインが無ければ 409 ``INVALID_STATE``。
    """
    view = effort_service.variance(session, access, user, project_id, report_id=report_id, baseline_id=baseline_id)
    assert view.basis.baseline is not None  # 無ければサービスが 409 にした
    items = []
    for row in view.rows:
        e = row.element
        r = row.result
        items.append(
            VarianceRowOut(
                element_id=e.id, parent_id=e.parent_id, wbs_code=e.wbs_code, name=e.name, kind=e.kind,
                baseline=_num(r.baseline), eac=float(r.eac), variance=_num(r.variance), rate=_num(r.rate),
                status=r.status.value,
                note=None if row.note is None else _note_out(NoteView(note=row.note, element=e)),
            )
        )
    return EffortVarianceOut(
        report=_report_ref(view.basis.report), report_specified=view.basis.report_specified,
        baseline=_baseline_ref(view.basis.baseline), rate_threshold=float(view.rate_threshold),
        hours_threshold=float(view.hours_threshold), items=items,
    )


@router.put("/projects/{project_id}/effort/variance/{element_id}/note", response_model=VarianceNoteOut)
def put_variance_note(
    project_id: int,
    element_id: int,
    body: VarianceNoteIn,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
    links: EffortLinks = Depends(get_effort_links),
    now: datetime = Depends(get_now),
) -> VarianceNoteOut:
    """API-EFF-007 差異の理由と是正措置の紐付けを書く。

    権限は variance.note。WBS要素・報告日（空は報告日を指定しない分析）ごとに 1 行を上書きする。紐付ける是正措置（CHG-06）が
    そのプロジェクトに無ければ 404（EFF-10）。
    """
    view = effort_service.put_variance_note(
        session, access, user, links, project_id, element_id, status_report_id=body.status_report_id,
        reason=body.reason, corrective_action_id=body.corrective_action_id, now=now,
    )
    return _note_out(view)
