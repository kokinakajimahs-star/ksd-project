"""プロジェクト基盤（PRJ）のユースケース（設計書 6.2 の API-PRJ-001〜011・4.1・4.2・9 章）。

- プロジェクトを作る（project.create）：基本情報・稼働カレンダー（4.1 の既定）・WBS の採番規則・ロール×権限の表（9.2 の写し）を
  1 つのトランザクションで作る
- 読む操作は project.view、基本情報・スコープ・カレンダーを変える操作は project.manage、メンバーを変える操作は member.manage
  （全て RULE-AUTH-001 を ``app.services.auth.authorize_project`` で通す。プロジェクトが無ければ 404 を 403 より先に返す）
- カレンダーを変えた・開始日を変えたら、同じトランザクションの最後に日程を再計算する（1.3。``ScheduleRecalculator``）

**別の群の表に書く 2 つの口**（``graphs/group-needs.toml`` では api:PRJ は db:WBS・db:SCH を待たない）：

- ``NumberingRulesStore``：WBS の採番規則（``wbs_numbering_rules``。DB-WBS-002）の既定の行を作る。アプリは表に書く
  ``NumberingRulesTable`` を載せる（db:WBS で差し替えた。docs/decisions/ASSUMPTIONS.md A-15）。``NumberingRulesTableAbsent`` は表の無い間の口
- ``ScheduleRecalculator``：日程の再計算（RULE-SCH-002・003）。活動の表（``activities``。DB-SCH-001）がまだ無い間は
  ``ActivitiesTableAbsent``（再計算する活動が無い）。db:SCH で表を作り、日程計算の実装が無い間の ``SchedulingNotWired``
  （活動があれば止める）へ差し替えた（A-19）。api:SCH で日程を計算して保存する ``app.services.schedule.ScheduleCalculator`` へ
  差し替えた（A-21）

差し替え忘れは ``backend/tests/api/test_prj.py`` の ``test_*_is_wired_once_*_table_exists`` が落とす（docs/decisions/ASSUMPTIONS.md A-14）。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.authz import ROLES, authorize, default_role_permission_rows
from app.domain.calendar import CalendarError, WorkCalendar
from app.errors import AppError
from app.models.cmn import RolePermission, User
from app.models.prj import (
    HOURS_PER_DAY_DEFAULT,
    WORKING_WEEKDAYS_DEFAULT,
    Calendar,
    CalendarHoliday,
    Project,
    ProjectMember,
    ScopeItem,
    default_project_settings,
)
from app.models.sch import Activity
from app.models.wbs import WbsNumberingRule
from app.services.auth import authorize_project, forbidden, load_role_permission_table, principal_of
from app.services.project_access import ProjectAccess

# PATCH で変えられる列と、そのうち空（null）にできない列（API-PRJ-004）
UPDATABLE_FIELDS = frozenset(
    {
        "code",
        "name",
        "purpose",
        "requirements",
        "key_deliverables",
        "start_date",
        "target_finish_date",
        "owner_user_id",
        "settings",
    }
)
_NON_NULLABLE_FIELDS = frozenset({"code", "name", "start_date", "owner_user_id", "settings"})
# ``projects.settings`` の項目（4.2）
SETTINGS_FIELDS = frozenset(default_project_settings())
_ROLE_ORDER = {role: index for index, role in enumerate(ROLES)}


# ---------------------------------------------------------------------------
# 別の群の表に書く口
# ---------------------------------------------------------------------------


class NumberingRulesStore(Protocol):
    """WBS の採番規則（``wbs_numbering_rules``。DB-WBS-002）を読み書きする。"""

    def create_default(self, session: Session, project_id: int) -> None:
        """プロジェクトの既定の採番規則の行を作る（コミットは呼び手が行う）。"""
        ...


class NumberingRulesTableAbsent:
    """採番規則の表（DB-WBS-002）がまだ無い間の ``NumberingRulesStore``。作る先の表が無いので何も書かない。"""

    def create_default(self, session: Session, project_id: int) -> None:
        return None


class NumberingRulesTable:
    """採番規則の表（``wbs_numbering_rules``。DB-WBS-002）に書く ``NumberingRulesStore``。

    既定の規則（4.1 の既定値：区切り ``.``・桁数 1・接頭辞なし・第 1 階層の補完あり。5.2 で W§4 の例 1.0・1.1… になる）の行を作る。
    同じプロジェクトに既に行があれば一意の制約で止まる（呼び手のトランザクションごと巻き戻る）。
    """

    def create_default(self, session: Session, project_id: int) -> None:
        session.add(WbsNumberingRule(project_id=project_id))
        session.flush()


class ScheduleRecalculator(Protocol):
    """プロジェクトの日程を再計算して保存する（RULE-SCH-002・003。設計書 1.3 の SCH-06 の自動反映）。"""

    def recalculate(self, session: Session, project_id: int) -> None:
        """カレンダー・開始日を変えた同じトランザクションの中で呼ぶ（コミットは呼び手が行う）。"""
        ...


class ActivitiesTableAbsent:
    """活動の表（``activities``。DB-SCH-001）がまだ無い間の ``ScheduleRecalculator``。再計算する活動が無い。"""

    def recalculate(self, session: Session, project_id: int) -> None:
        return None


class SchedulingNotWired:
    """活動の表（DB-SCH-001）はあるが、日程計算（RULE-SCH-002・003。rule:SCH）を保存まで通す実装（api:SCH）がまだ無い間の
    ``ScheduleRecalculator``（db:SCH で差し替えた。docs/decisions/ASSUMPTIONS.md A-19）。

    プロジェクトに活動が 1 つも無ければ再計算するものが無い（何もしない）。活動があれば、古い日程を残したまま
    カレンダー・開始日の変更を通さずに止める（fail fast。呼び手のトランザクションごと巻き戻る）。
    **api:SCH のスコープが再計算の実装へ差し替える**（差し替え忘れは ``backend/tests/api/test_prj.py`` の
    ``test_schedule_recalculator_is_replaced_once_schedule_api_exists`` が落とす）。
    """

    def recalculate(self, session: Session, project_id: int) -> None:
        has_activity = session.scalar(select(Activity.id).where(Activity.project_id == project_id).limit(1))
        if has_activity is not None:
            raise RuntimeError(
                "日程の再計算（RULE-SCH-002・003）を保存まで通す実装がまだ無い。api:SCH が app.state.schedule_recalculator を差し替える"
            )


# ---------------------------------------------------------------------------
# 共通の部品
# ---------------------------------------------------------------------------


def _validation_error(details: list[dict[str, Any]], message: str = "入力に誤りがあります") -> AppError:
    return AppError(400, "VALIDATION_ERROR", message, details=details)


def _duplicate_code(code: str) -> AppError:
    return AppError(409, "DUPLICATE_PROJECT_CODE", "そのプロジェクトのコードは既に使われています", details=[{"code": code}])


def _check_date_range(start_date: date, target_finish_date: date | None) -> None:
    if target_finish_date is not None and target_finish_date < start_date:
        raise _validation_error(
            [{"loc": ["body", "target_finish_date"], "message": "終了予定日は開始日以後にします"}]
        )


def _check_owner(session: Session, owner_user_id: int) -> User:
    """責任者は有効な利用者であること（無い・無効なら 400）。"""
    owner = session.get(User, owner_user_id)
    if owner is None or not owner.is_active:
        raise _validation_error(
            [{"loc": ["body", "owner_user_id"], "message": "責任者が見つからないか、無効な利用者です"}]
        )
    return owner


def _code_taken(session: Session, code: str, *, except_project_id: int | None = None) -> bool:
    query = select(Project.id).where(Project.code == code)
    if except_project_id is not None:
        query = query.where(Project.id != except_project_id)
    return session.scalar(query) is not None


def _project(session: Session, project_id: int) -> Project:
    project = session.get(Project, project_id)
    if project is None:  # authorize_project が先に 404 を返すので、ここへは来ない
        raise AppError(404, "NOT_FOUND", "プロジェクトが見つかりません", details=[{"project_id": project_id}])
    return project


def _owners(session: Session, projects: Iterable[Project]) -> dict[int, User]:
    owner_ids = {project.owner_user_id for project in projects}
    if not owner_ids:
        return {}
    return {user.id: user for user in session.scalars(select(User).where(User.id.in_(owner_ids)))}


@dataclass(frozen=True)
class ProjectView:
    """プロジェクトとその責任者（一覧・詳細の出力の元）。"""

    project: Project
    owner: User


def _view_of(session: Session, project: Project) -> ProjectView:
    owner = session.get(User, project.owner_user_id)
    if owner is None:  # 外部キーがあるので起きない
        raise RuntimeError(f"projects(id={project.id}) の責任者 users(id={project.owner_user_id}) が無い")
    return ProjectView(project=project, owner=owner)


# ---------------------------------------------------------------------------
# プロジェクト（API-PRJ-001〜004）
# ---------------------------------------------------------------------------


def create_project(
    session: Session,
    actor: User,
    numbering_rules: NumberingRulesStore,
    *,
    code: str,
    name: str,
    start_date: date,
    owner_user_id: int,
    purpose: str | None = None,
    requirements: str | None = None,
    key_deliverables: str | None = None,
    target_finish_date: date | None = None,
) -> ProjectView:
    """プロジェクトを作る（project.create）。カレンダー・採番規則・既定の権限も同じトランザクションで作る。

    - 稼働カレンダーは 4.1 の既定（``1111100``・8 時間・休日なし）。設定は 4.2 の既定。WBS の状態は DRAFT
    - ロール×権限の表は 9.2 の既定を ``role_permissions`` へ写す
    - 終了予定日が開始日より前・責任者が無い／無効は 400。コードが既にあれば 409 ``DUPLICATE_PROJECT_CODE``
    """
    if not authorize(principal_of(actor), "project.create").allowed:
        raise forbidden()
    _check_date_range(start_date, target_finish_date)
    owner = _check_owner(session, owner_user_id)
    if _code_taken(session, code):
        raise _duplicate_code(code)

    project = Project(
        code=code,
        name=name,
        purpose=purpose,
        requirements=requirements,
        key_deliverables=key_deliverables,
        start_date=start_date,
        target_finish_date=target_finish_date,
        owner_user_id=owner.id,
        settings=default_project_settings(),
    )
    session.add(project)
    try:
        session.flush()
        session.add(
            Calendar(project_id=project.id, working_weekdays=WORKING_WEEKDAYS_DEFAULT, hours_per_day=HOURS_PER_DAY_DEFAULT)
        )
        for role, permission in default_role_permission_rows():
            session.add(RolePermission(project_id=project.id, role=role, permission=permission))
        numbering_rules.create_default(session, project.id)
        session.commit()
    except IntegrityError as exc:  # 同時に同じコードで作られた
        session.rollback()
        raise _duplicate_code(code) from exc
    return ProjectView(project=project, owner=owner)


def visible_project_ids(session: Session, access: ProjectAccess, actor: User) -> list[int] | None:
    """利用者が project.view で読めるプロジェクトの ID（昇順）。システム管理者（全て読める）は ``None``。"""
    principal = principal_of(actor)
    if not principal.is_active:
        return []
    if principal.is_system_admin:
        return None
    visible: list[int] = []
    for project_id, membership in sorted(access.memberships(session, actor.id).items()):
        table = load_role_permission_table(session, project_id)
        if authorize(principal, "project.view", role_permissions=table, membership=membership).allowed:
            visible.append(project_id)
    return visible


def list_projects(
    session: Session, access: ProjectAccess, actor: User, *, limit: int, offset: int
) -> tuple[list[ProjectView], int]:
    """自分が見られるプロジェクト（ID の順）と総数。

    システム管理者は全て。ほかの利用者はメンバーであり、そのプロジェクトの表で project.view を持つもの
    （委託先は割当の範囲に絞って見られるので一覧に載る）。
    """
    visible = visible_project_ids(session, access, actor)
    query = select(Project)
    count = select(func.count()).select_from(Project)
    if visible is not None:
        query = query.where(Project.id.in_(visible))
        count = count.where(Project.id.in_(visible))
    total = session.scalar(count) or 0
    projects = list(session.scalars(query.order_by(Project.id).limit(limit).offset(offset)))
    owners = _owners(session, projects)
    return [ProjectView(project=project, owner=owners[project.owner_user_id]) for project in projects], total


def get_project(session: Session, access: ProjectAccess, actor: User, project_id: int) -> ProjectView:
    """プロジェクトの詳細（project.view）。"""
    authorize_project(session, access, actor, project_id, "project.view")
    return _view_of(session, _project(session, project_id))


def update_project(
    session: Session,
    access: ProjectAccess,
    actor: User,
    recalculator: ScheduleRecalculator,
    project_id: int,
    changes: Mapping[str, Any],
) -> ProjectView:
    """基本情報・設定を変える（project.manage）。``changes`` に載せた列だけを変える。

    - ``settings`` は載せた項目だけを今の設定に重ねる（4.2 の項目に無い名前は呼び手の誤り）
    - code・name・start_date・owner_user_id・settings（とその項目）の null・終了予定日が開始日より前・責任者が無い／無効は 400
    - コードが他のプロジェクトと重なれば 409 ``DUPLICATE_PROJECT_CODE``
    - 開始日（日程の軸の原点・活動の下限。5.4・5.5）を変えたら日程を再計算する
    """
    authorize_project(session, access, actor, project_id, "project.manage")
    unknown = set(changes) - UPDATABLE_FIELDS
    if unknown:
        raise ValueError(f"変えられない列: {sorted(unknown)}")
    settings_changes: Mapping[str, Any] = changes.get("settings") or {}
    unknown_settings = set(settings_changes) - SETTINGS_FIELDS
    if unknown_settings:
        raise ValueError(f"4.2 に無い設定の項目: {sorted(unknown_settings)}")
    nulls = [["body", name] for name in sorted(_NON_NULLABLE_FIELDS & set(changes)) if changes[name] is None]
    nulls += [["body", "settings", name] for name in sorted(settings_changes) if settings_changes[name] is None]
    if nulls:
        raise _validation_error([{"loc": loc, "message": "空にできません"} for loc in nulls])

    project = _project(session, project_id)
    start_date: date = changes.get("start_date", project.start_date)
    target_finish_date: date | None = changes.get("target_finish_date", project.target_finish_date)
    _check_date_range(start_date, target_finish_date)
    if "owner_user_id" in changes and changes["owner_user_id"] != project.owner_user_id:
        _check_owner(session, changes["owner_user_id"])
    code = changes.get("code", project.code)
    if code != project.code and _code_taken(session, code, except_project_id=project.id):
        raise _duplicate_code(code)

    start_changed = start_date != project.start_date
    for name, value in changes.items():
        if name == "settings":
            # JSON の列は中身の書き換えを追跡しないので、新しい辞書を代入する（A-11）
            project.settings = {**project.settings, **settings_changes}
        else:
            setattr(project, name, value)
    try:
        session.flush()
        if start_changed:
            recalculator.recalculate(session, project.id)
        session.commit()
    except IntegrityError as exc:  # 同時に同じコードへ変えられた
        session.rollback()
        raise _duplicate_code(code) from exc
    return _view_of(session, project)


# ---------------------------------------------------------------------------
# スコープ項目（API-PRJ-005・006）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScopeItemSpec:
    """置き換えの 1 項目。``id`` があれば既存の項目を変え、無ければ足す。"""

    kind: str
    title: str
    description: str | None = None
    id: int | None = None


def list_scope_items(
    session: Session, access: ProjectAccess, actor: User, project_id: int, *, limit: int, offset: int
) -> tuple[list[ScopeItem], int]:
    """スコープ項目（並び順）と総数（project.view）。"""
    authorize_project(session, access, actor, project_id, "project.view")
    total = session.scalar(select(func.count()).select_from(ScopeItem).where(ScopeItem.project_id == project_id)) or 0
    items = list(
        session.scalars(
            select(ScopeItem)
            .where(ScopeItem.project_id == project_id)
            .order_by(ScopeItem.sort_order, ScopeItem.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return items, total


def replace_scope_items(
    session: Session, access: ProjectAccess, actor: User, project_id: int, specs: Sequence[ScopeItemSpec]
) -> list[ScopeItem]:
    """スコープ項目を置き換える（project.manage）。

    ``id`` を書いた項目はその行を変え（ID を保つ。WBS 要素との紐付けは内部キーで指すため。3 章）、書かない項目は足す。
    載せなかった既存の項目は消す。並び順（``sort_order``）は配列の順に 1 から振る。
    他のプロジェクトの・存在しない ``id``、同じ ``id`` を 2 回書くのは 400。
    """
    authorize_project(session, access, actor, project_id, "project.manage")
    existing = {item.id: item for item in session.scalars(select(ScopeItem).where(ScopeItem.project_id == project_id))}
    errors: list[dict[str, Any]] = []
    seen: set[int] = set()
    for index, spec in enumerate(specs):
        if spec.id is None:
            continue
        if spec.id in seen:
            errors.append({"loc": ["body", "items", index, "id"], "message": "同じ項目が 2 回書かれています"})
        elif spec.id not in existing:
            errors.append({"loc": ["body", "items", index, "id"], "message": "このプロジェクトのスコープ項目ではありません"})
        seen.add(spec.id)
    if errors:
        raise _validation_error(errors)

    for item_id, item in existing.items():
        if item_id not in seen:
            session.delete(item)
    result: list[ScopeItem] = []
    for index, spec in enumerate(specs, start=1):
        if spec.id is None:
            item = ScopeItem(project_id=project_id)
            session.add(item)
        else:
            item = existing[spec.id]
        item.kind = spec.kind
        item.title = spec.title
        item.description = spec.description
        item.sort_order = index
        result.append(item)
    session.commit()
    return result


# ---------------------------------------------------------------------------
# 稼働カレンダー（API-PRJ-007・008）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CalendarView:
    """稼働カレンダーと休日（日付の順）。"""

    project_id: int
    working_weekdays: str
    hours_per_day: Decimal
    holidays: list[tuple[date, str | None]]


def _calendar_of(session: Session, project_id: int) -> Calendar | None:
    return session.scalar(select(Calendar).where(Calendar.project_id == project_id))


def _calendar_view(session: Session, project_id: int, calendar: Calendar | None) -> CalendarView:
    if calendar is None:
        # API-PRJ-001 を通らずに作られたプロジェクト。カレンダーの行が無いときは 4.1・PRJ-04 の既定（土日休み・8 時間・休日なし）
        return CalendarView(project_id, WORKING_WEEKDAYS_DEFAULT, HOURS_PER_DAY_DEFAULT, [])
    holidays = session.execute(
        select(CalendarHoliday.holiday_date, CalendarHoliday.name)
        .where(CalendarHoliday.calendar_id == calendar.id)
        .order_by(CalendarHoliday.holiday_date)
    ).all()
    return CalendarView(
        project_id=project_id,
        working_weekdays=calendar.working_weekdays,
        hours_per_day=Decimal(calendar.hours_per_day),
        holidays=[(holiday_date, name) for holiday_date, name in holidays],
    )


def get_calendar(session: Session, access: ProjectAccess, actor: User, project_id: int) -> CalendarView:
    """稼働カレンダーを読む（project.view）。"""
    authorize_project(session, access, actor, project_id, "project.view")
    return _calendar_view(session, project_id, _calendar_of(session, project_id))


def replace_calendar(
    session: Session,
    access: ProjectAccess,
    actor: User,
    recalculator: ScheduleRecalculator,
    project_id: int,
    *,
    working_weekdays: str,
    hours_per_day: Decimal,
    holidays: Sequence[tuple[date, str | None]],
) -> CalendarView:
    """稼働カレンダーを更新する（project.manage）。休日は丸ごと置き換え、同じトランザクションの最後に日程を再計算する。

    - 同じ日付の休日を 2 回書くのは 400
    - 稼働の曜日が 1 つも無いなど、RULE-CAL-001（``WorkCalendar``）が日付を数えられないカレンダーは 422 ``INVALID_CALENDAR``
    """
    authorize_project(session, access, actor, project_id, "project.manage")
    seen: set[date] = set()
    errors: list[dict[str, Any]] = []
    for index, (holiday_date, _name) in enumerate(holidays):
        if holiday_date in seen:
            errors.append(
                {"loc": ["body", "holidays", index, "holiday_date"], "message": "同じ日付の休日が 2 回書かれています"}
            )
        seen.add(holiday_date)
    if errors:
        raise _validation_error(errors)
    try:
        WorkCalendar.of(working_weekdays, seen)
    except CalendarError as exc:
        raise AppError(
            422, "INVALID_CALENDAR", "このカレンダーでは稼働日を数えられません", details=[{"message": str(exc)}]
        ) from exc

    calendar = _calendar_of(session, project_id)
    if calendar is None:
        calendar = Calendar(project_id=project_id)
        session.add(calendar)
    calendar.working_weekdays = working_weekdays
    calendar.hours_per_day = hours_per_day
    session.flush()
    session.execute(delete(CalendarHoliday).where(CalendarHoliday.calendar_id == calendar.id))
    for holiday_date, name in holidays:
        session.add(CalendarHoliday(calendar_id=calendar.id, holiday_date=holiday_date, name=name))
    session.flush()
    recalculator.recalculate(session, project_id)
    session.commit()
    return _calendar_view(session, project_id, calendar)


# ---------------------------------------------------------------------------
# メンバー（API-PRJ-009〜011）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemberView:
    """メンバーとその利用者の名前。ロールは 3 章の順。"""

    user_id: int
    login_id: str
    display_name: str
    is_active: bool
    roles: list[str]
    organization: str | None
    team: str | None


def _member_view(member: ProjectMember, user: User) -> MemberView:
    return MemberView(
        user_id=user.id,
        login_id=user.login_id,
        display_name=user.display_name,
        is_active=user.is_active,
        roles=sorted(member.roles, key=lambda role: _ROLE_ORDER.get(role, len(ROLES))),
        organization=member.organization,
        team=member.team,
    )


def list_members(
    session: Session, access: ProjectAccess, actor: User, project_id: int, *, limit: int, offset: int
) -> tuple[list[MemberView], int]:
    """メンバー（利用者の ID の順）と総数（project.view）。"""
    authorize_project(session, access, actor, project_id, "project.view")
    total = (
        session.scalar(select(func.count()).select_from(ProjectMember).where(ProjectMember.project_id == project_id))
        or 0
    )
    rows = session.execute(
        select(ProjectMember, User)
        .join(User, User.id == ProjectMember.user_id)
        .where(ProjectMember.project_id == project_id)
        .order_by(ProjectMember.user_id)
        .limit(limit)
        .offset(offset)
    ).all()
    return [_member_view(member, user) for member, user in rows], total


def upsert_member(
    session: Session,
    access: ProjectAccess,
    actor: User,
    project_id: int,
    user_id: int,
    *,
    roles: Sequence[str],
    organization: str | None = None,
    team: str | None = None,
) -> MemberView:
    """メンバーを足す・ロールと所属を変える（member.manage）。ロールと所属は載せた値で置き換える。

    - 3 章に無いロール・同じロールの重複・ロールが 1 つも無いのは 400
    - 利用者が無ければ 404。無効な利用者を新しく足すのは 422 ``USER_INACTIVE``（既にメンバーなら変えられる）
    """
    authorize_project(session, access, actor, project_id, "member.manage")
    errors: list[dict[str, Any]] = []
    if not roles:
        errors.append({"loc": ["body", "roles"], "message": "ロールを 1 つ以上書きます"})
    seen: set[str] = set()
    for index, role in enumerate(roles):
        if role not in _ROLE_ORDER:
            errors.append({"loc": ["body", "roles", index], "message": f"3 章に無いロールのコード: {role}"})
        elif role in seen:
            errors.append({"loc": ["body", "roles", index], "message": f"同じロールが 2 回書かれています: {role}"})
        seen.add(role)
    if errors:
        raise _validation_error(errors)

    user = session.get(User, user_id)
    if user is None:
        raise AppError(404, "NOT_FOUND", "ユーザーが見つかりません", details=[{"user_id": user_id}])
    member = session.scalar(
        select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.user_id == user_id)
    )
    if member is None:
        if not user.is_active:
            raise AppError(
                422, "USER_INACTIVE", "無効な利用者はメンバーに足せません", details=[{"user_id": user_id}]
            )
        member = ProjectMember(project_id=project_id, user_id=user_id)
        session.add(member)
    # JSON の列は中身の書き換えを追跡しないので、新しい配列を代入する（A-11）
    member.roles = sorted(seen, key=_ROLE_ORDER.__getitem__)
    member.organization = organization
    member.team = team
    session.commit()
    return _member_view(member, user)


def remove_member(session: Session, access: ProjectAccess, actor: User, project_id: int, user_id: int) -> None:
    """メンバーを外す（member.manage）。メンバーでなければ 404。"""
    authorize_project(session, access, actor, project_id, "member.manage")
    member = session.scalar(
        select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.user_id == user_id)
    )
    if member is None:
        raise AppError(
            404, "NOT_FOUND", "メンバーが見つかりません", details=[{"project_id": project_id, "user_id": user_id}]
        )
    session.delete(member)
    session.commit()
