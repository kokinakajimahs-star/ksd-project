"""プロジェクト基盤の API（設計書 6.2 の API-PRJ-001〜011）：プロジェクト・スコープ項目・稼働カレンダー・メンバー。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_numbering_rules, get_project_access, get_schedule_recalculator
from app.schemas.projects import (
    CalendarHolidayIO,
    CalendarOut,
    CalendarReplace,
    MemberList,
    MemberOut,
    MemberUpsert,
    ProjectCreate,
    ProjectList,
    ProjectOut,
    ProjectSettingsOut,
    ProjectUpdate,
    ScopeItemList,
    ScopeItemOut,
    ScopeItemsReplace,
    UserSummary,
)
from app.services import get_session
from app.services import projects as projects_service
from app.services.auth import User
from app.services.project_access import ProjectAccess
from app.services.projects import (
    CalendarView,
    MemberView,
    NumberingRulesStore,
    ProjectView,
    ScheduleRecalculator,
    ScopeItemSpec,
)

router = APIRouter(tags=["projects"])


def _project_out(view: ProjectView) -> ProjectOut:
    project = view.project
    return ProjectOut(
        id=project.id,
        code=project.code,
        name=project.name,
        purpose=project.purpose,
        requirements=project.requirements,
        key_deliverables=project.key_deliverables,
        start_date=project.start_date,
        target_finish_date=project.target_finish_date,
        owner_user_id=project.owner_user_id,
        owner=UserSummary.model_validate(view.owner),
        wbs_status=project.wbs_status,
        settings=ProjectSettingsOut.model_validate(project.settings),
    )


def _calendar_out(view: CalendarView) -> CalendarOut:
    return CalendarOut(
        project_id=view.project_id,
        working_weekdays=view.working_weekdays,
        hours_per_day=float(view.hours_per_day),
        holidays=[CalendarHolidayIO(holiday_date=holiday_date, name=name) for holiday_date, name in view.holidays],
    )


def _member_out(view: MemberView) -> MemberOut:
    return MemberOut(
        user_id=view.user_id,
        login_id=view.login_id,
        display_name=view.display_name,
        is_active=view.is_active,
        roles=view.roles,
        organization=view.organization,
        team=view.team,
    )


# ---------------------------------------------------------------------------
# プロジェクト
# ---------------------------------------------------------------------------


@router.post("/projects", response_model=ProjectOut, status_code=201)
def create_project(
    body: ProjectCreate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    numbering_rules: NumberingRulesStore = Depends(get_numbering_rules),
) -> ProjectOut:
    """API-PRJ-001 プロジェクトを作成する（カレンダー・採番規則・既定の権限も作る）。

    権限は project.create。稼働カレンダーは既定（土日休み・8 時間）、ロール×権限の表は 9.2 の既定を写す。
    システム管理者でない人が作ると、作った人をメンバー（ロール PM）にする（CR-014）。
    コードが既にあれば 409 ``DUPLICATE_PROJECT_CODE``。終了予定日が開始日より前・責任者が無い／無効は 400。
    """
    view = projects_service.create_project(
        session,
        user,
        numbering_rules,
        code=body.code,
        name=body.name,
        purpose=body.purpose,
        requirements=body.requirements,
        key_deliverables=body.key_deliverables,
        start_date=body.start_date,
        target_finish_date=body.target_finish_date,
        owner_user_id=body.owner_user_id,
    )
    return _project_out(view)


@router.get("/projects", response_model=ProjectList)
def list_projects(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
) -> ProjectList:
    """API-PRJ-002 自分が見られるプロジェクトの一覧。

    権限は project.view（メンバーであり、そのプロジェクトの表で持つもの。システム管理者は全て）。``limit``・``offset``（6.1）。
    """
    views, total = projects_service.list_projects(session, access, user, limit=limit, offset=offset)
    return ProjectList(items=[_project_out(view) for view in views], total=total, limit=limit, offset=offset)


@router.get("/projects/{project_id}", response_model=ProjectOut)
def get_project(
    project_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
) -> ProjectOut:
    """API-PRJ-003 プロジェクトの詳細。

    権限は project.view。プロジェクトが無ければ 404。
    """
    return _project_out(projects_service.get_project(session, access, user, project_id))


@router.patch("/projects/{project_id}", response_model=ProjectOut)
def update_project(
    project_id: int,
    body: ProjectUpdate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
    recalculator: ScheduleRecalculator = Depends(get_schedule_recalculator),
) -> ProjectOut:
    """API-PRJ-004 基本情報・設定を更新する。

    権限は project.manage。載せた項目だけを変える（``settings`` は載せた項目だけを重ねる）。開始日を変えたら日程を再計算する。
    コードの重複は 409 ``DUPLICATE_PROJECT_CODE``。
    """
    # 載せた項目だけ（入れ子の settings も載せた項目だけになる）
    changes = body.model_dump(exclude_unset=True)
    view =projects_service.update_project(session, access, user, recalculator, project_id, changes)
    return _project_out(view)


# ---------------------------------------------------------------------------
# スコープ項目
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/scope-items", response_model=ScopeItemList)
def list_scope_items(
    project_id: int,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
) -> ScopeItemList:
    """API-PRJ-005 スコープ項目の一覧。

    権限は project.view。並び順で返す。``limit``・``offset``（6.1）。
    """
    items, total = projects_service.list_scope_items(session, access, user, project_id, limit=limit, offset=offset)
    return ScopeItemList(
        project_id=project_id,
        items=[ScopeItemOut.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.put("/projects/{project_id}/scope-items", response_model=ScopeItemList)
def replace_scope_items(
    project_id: int,
    body: ScopeItemsReplace,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
) -> ScopeItemList:
    """API-PRJ-006 スコープ項目を置き換える。

    権限は project.manage。``id`` を書いた項目は変え（ID を保つ）、書かない項目は足し、載せなかった項目は消す。並び順は配列の順。
    """
    specs = [
        ScopeItemSpec(id=item.id, kind=item.kind, title=item.title, description=item.description) for item in body.items
    ]
    items = projects_service.replace_scope_items(session, access, user, project_id, specs)
    return ScopeItemList(
        project_id=project_id,
        items=[ScopeItemOut.model_validate(item) for item in items],
        total=len(items),
        limit=len(items),
        offset=0,
    )


# ---------------------------------------------------------------------------
# 稼働カレンダー
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/calendar", response_model=CalendarOut)
def get_calendar(
    project_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
) -> CalendarOut:
    """API-PRJ-007 稼働カレンダーを読む。

    権限は project.view。休日は日付の順。
    """
    return _calendar_out(projects_service.get_calendar(session, access, user, project_id))


@router.put("/projects/{project_id}/calendar", response_model=CalendarOut)
def replace_calendar(
    project_id: int,
    body: CalendarReplace,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
    recalculator: ScheduleRecalculator = Depends(get_schedule_recalculator),
) -> CalendarOut:
    """API-PRJ-008 稼働カレンダーを更新する（日程を再計算する）。

    権限は project.manage。休日は丸ごと置き換える。稼働日を数えられないカレンダー（稼働の曜日が無い）は 422 ``INVALID_CALENDAR``。
    """
    view = projects_service.replace_calendar(
        session,
        access,
        user,
        recalculator,
        project_id,
        working_weekdays=body.working_weekdays,
        hours_per_day=body.hours_per_day,
        holidays=[(holiday.holiday_date, holiday.name) for holiday in body.holidays],
    )
    return _calendar_out(view)


# ---------------------------------------------------------------------------
# メンバー
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/members", response_model=MemberList)
def list_members(
    project_id: int,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
) -> MemberList:
    """API-PRJ-009 メンバーの一覧。

    権限は project.view。利用者の ID の順。``limit``・``offset``（6.1）。
    """
    members, total = projects_service.list_members(session, access, user, project_id, limit=limit, offset=offset)
    return MemberList(
        project_id=project_id, items=[_member_out(view) for view in members], total=total, limit=limit, offset=offset
    )


@router.put("/projects/{project_id}/members/{user_id}", response_model=MemberOut)
def upsert_member(
    project_id: int,
    user_id: int,
    body: MemberUpsert,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
) -> MemberOut:
    """API-PRJ-010 メンバーを足す・ロールと所属を変える。

    権限は member.manage。ロール・所属・チームは載せた値で置き換える。3 章に無いロールは 400、利用者が無ければ 404、
    無効な利用者を新しく足すのは 422 ``USER_INACTIVE``。最後の PM から PM のロールを外すのは 409 ``LAST_PM``
    （システム管理者も同じ。先に別の PM を足す。CR-019）。
    """
    view = projects_service.upsert_member(
        session,
        access,
        user,
        project_id,
        user_id,
        roles=body.roles,
        organization=body.organization,
        team=body.team,
    )
    return _member_out(view)


@router.delete("/projects/{project_id}/members/{user_id}", status_code=204)
def remove_member(
    project_id: int,
    user_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
) -> Response:
    """API-PRJ-011 メンバーを外す。

    権限は member.manage。メンバーでなければ 404。最後の PM を外すのは 409 ``LAST_PM``（システム管理者も同じ。
    先に別の PM を足す。CR-019）。
    """
    projects_service.remove_member(session, access, user, project_id, user_id)
    return Response(status_code=204)
