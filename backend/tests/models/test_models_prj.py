"""プロジェクト基盤（PRJ）の群のテーブルの試験（設計書 3 章・4.1・4.2）：DB-PRJ-001〜005。

列が 4.1 の行どおりであること、既定値（4.1・4.2）、一意制約・外部キー・値の制約が DB で効くこと、
共通（CMN）の表の project_id が projects を指すこと、移行（alembic upgrade head）の結果がモデルと食い違わないことを確かめる。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import BACKEND_DIR
from app.infra.db import Base, ForeignKeyViolationError, create_db_engine
from app.models.cmn import RolePermission, ShareLink, User
from app.models.prj import (
    Calendar,
    CalendarHoliday,
    Project,
    ProjectMember,
    ScopeItem,
    default_project_settings,
)

# 設計書 4.1 の「主な列」
COLUMNS_4_1 = {
    "projects": {
        "id", "code", "name", "purpose", "requirements", "key_deliverables", "start_date", "target_finish_date",
        "owner_user_id", "wbs_status", "settings",
    },
    "scope_items": {"id", "project_id", "kind", "title", "description", "sort_order"},
    "calendars": {"id", "project_id", "working_weekdays", "hours_per_day"},
    "calendar_holidays": {"id", "calendar_id", "holiday_date", "name"},
    "project_members": {"id", "project_id", "user_id", "roles", "organization", "team"},
}

# 設計書 4.2 の projects.settings の既定値（要求仕様書 BR-03・09・11）
SETTINGS_4_2 = {
    "progress_method_default": "DIRECT",
    "delay_tolerance_days": 0.5,
    "delay_extract_days": 2,
    "variance_rate": 0.10,
    "variance_hours": 8,
    "hundred_percent_tolerance": 0.0,
}

START = date(2026, 12, 1)


def _user(session: Session, login_id: str = "owner") -> User:
    user = User(login_id=login_id, display_name="利用者", password_hash="scrypt$dummy")
    session.add(user)
    session.flush()
    return user


def _project(session: Session, code: str = "P-001", owner: User | None = None) -> Project:
    owner = owner or _user(session, f"owner-{code}")
    project = Project(code=code, name="プロジェクト", start_date=START, owner_user_id=owner.id)
    session.add(project)
    session.flush()
    return project


def _calendar(session: Session, project: Project) -> Calendar:
    calendar = Calendar(project_id=project.id)
    session.add(calendar)
    session.flush()
    return calendar


@pytest.mark.parametrize("table", sorted(COLUMNS_4_1))
def test_columns_match_design_4_1(table: str) -> None:
    """DB-PRJ-001〜005 の列が設計書 4.1 の行どおりである。"""
    assert set(Base.metadata.tables[table].c.keys()) == COLUMNS_4_1[table]


# ---------------------------------------------------------------------------
# DB-PRJ-001 projects
# ---------------------------------------------------------------------------


def test_projects_defaults(db_session: Session) -> None:
    """DB-PRJ-001 projects：wbs_status の既定は DRAFT（5.10）。settings の既定は 4.2 の値。任意の列は空のまま。"""
    project = _project(db_session)
    db_session.commit()
    db_session.expunge_all()

    loaded = db_session.get(Project, project.id)
    assert loaded is not None
    assert loaded.wbs_status == "DRAFT"
    assert loaded.settings == SETTINGS_4_2
    assert loaded.start_date == START
    assert (loaded.purpose, loaded.requirements, loaded.key_deliverables, loaded.target_finish_date) == (
        None, None, None, None,
    )


def test_projects_settings_default_is_fresh_per_row() -> None:
    """DB-PRJ-001 projects：settings の既定値は行ごとに別の辞書（1 つのプロジェクトの設定を変えても他へ響かない）。"""
    first = default_project_settings()
    first["delay_tolerance_days"] = 1.0
    assert default_project_settings() == SETTINGS_4_2


def test_projects_code_unique(db_session: Session) -> None:
    """DB-PRJ-001 projects：code（業務キー）は一意。"""
    owner = _user(db_session)
    _project(db_session, "P-001", owner)
    db_session.commit()
    db_session.add(Project(code="P-001", name="別", start_date=START, owner_user_id=owner.id))
    with pytest.raises(IntegrityError):
        db_session.flush()


@pytest.mark.parametrize("missing", ["code", "name", "start_date", "owner_user_id"])
def test_projects_required_columns(db_session: Session, missing: str) -> None:
    """DB-PRJ-001 projects：名称・開始日・責任者（要求 PRJ-01 の必須項目）と code は必須。"""
    owner = _user(db_session)
    values: dict[str, object] = {"code": "P-001", "name": "プロジェクト", "start_date": START, "owner_user_id": owner.id}
    del values[missing]
    db_session.add(Project(**values))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_projects_owner_must_be_user(db_session: Session) -> None:
    """DB-PRJ-001 projects：owner_user_id は users を指す。"""
    db_session.add(Project(code="P-001", name="プロジェクト", start_date=START, owner_user_id=999))
    with pytest.raises(IntegrityError):
        db_session.flush()


@pytest.mark.parametrize("status", ["DRAFT", "IN_REVIEW", "PENDING_APPROVAL", "BASELINED"])
def test_projects_wbs_status_accepts_states_of_5_10(db_session: Session, status: str) -> None:
    """DB-PRJ-001 projects：wbs_status は 5.10 の 4 つの状態を持てる。"""
    project = _project(db_session)
    project.wbs_status = status
    db_session.commit()


def test_projects_wbs_status_rejects_other_values(db_session: Session) -> None:
    """DB-PRJ-001 projects：5.10 に無い状態は DB が拒む。"""
    project = _project(db_session)
    db_session.commit()
    project.wbs_status = "APPROVED"
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_projects_server_default_for_wbs_status(db_session: Session) -> None:
    """DB-PRJ-001 projects：wbs_status の既定値は DB の既定値としても入る（ORM を通らない書き込み）。"""
    owner = _user(db_session)
    db_session.execute(
        sa.text(
            "INSERT INTO projects (code, name, start_date, owner_user_id, settings) "
            "VALUES ('RAW', '生', '2026-12-01', :owner, '{}')"
        ),
        {"owner": owner.id},
    )
    assert db_session.execute(sa.text("SELECT wbs_status FROM projects WHERE code = 'RAW'")).scalar_one() == "DRAFT"


# ---------------------------------------------------------------------------
# DB-PRJ-002 scope_items
# ---------------------------------------------------------------------------


def test_scope_items_kind_in_or_out(db_session: Session) -> None:
    """DB-PRJ-002 scope_items：区分は IN（対象内）か OUT（対象外）。他の値は DB が拒む。"""
    project = _project(db_session)
    db_session.add_all(
        [
            ScopeItem(project_id=project.id, kind="IN", title="受注管理", sort_order=0),
            ScopeItem(project_id=project.id, kind="OUT", title="会計連携", description="R2 で扱う", sort_order=1),
        ]
    )
    db_session.commit()

    db_session.add(ScopeItem(project_id=project.id, kind="MAYBE", title="不明", sort_order=2))
    with pytest.raises(IntegrityError):
        db_session.flush()


@pytest.mark.parametrize("missing", ["kind", "title", "sort_order"])
def test_scope_items_required_columns(db_session: Session, missing: str) -> None:
    """DB-PRJ-002 scope_items：区分・題名・並び順は必須（説明は任意）。"""
    project = _project(db_session)
    values: dict[str, object] = {"project_id": project.id, "kind": "IN", "title": "受注管理", "sort_order": 0}
    del values[missing]
    db_session.add(ScopeItem(**values))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_scope_items_project_must_exist(db_session: Session) -> None:
    """DB-PRJ-002 scope_items：project_id は projects を指す。"""
    db_session.add(ScopeItem(project_id=999, kind="IN", title="受注管理", sort_order=0))
    with pytest.raises(IntegrityError):
        db_session.flush()


# ---------------------------------------------------------------------------
# DB-PRJ-003 calendars
# ---------------------------------------------------------------------------


def test_calendars_defaults_are_weekdays_and_8_hours(db_session: Session) -> None:
    """DB-PRJ-003 calendars：既定は土日休み（月〜日の 7 文字で 1111100）・1 日 8 時間（要求 PRJ-04）。"""
    calendar = _calendar(db_session, _project(db_session))
    db_session.commit()
    db_session.expunge_all()

    loaded = db_session.get(Calendar, calendar.id)
    assert loaded is not None
    assert loaded.working_weekdays == "1111100"
    assert loaded.hours_per_day == Decimal("8")


def test_calendars_server_defaults_apply_to_raw_insert(db_session: Session) -> None:
    """DB-PRJ-003 calendars：既定値は DB の既定値としても入る（ORM を通らない書き込み）。"""
    project = _project(db_session)
    db_session.execute(sa.text("INSERT INTO calendars (project_id) VALUES (:p)"), {"p": project.id})
    row = db_session.execute(sa.text("SELECT working_weekdays, hours_per_day FROM calendars")).one()
    assert (row[0], Decimal(str(row[1]))) == ("1111100", Decimal("8"))


def test_calendars_one_per_project(db_session: Session) -> None:
    """DB-PRJ-003 calendars：project_id は一意（プロジェクトに稼働カレンダーは 1 つ。3 章の 1 対 1）。"""
    project = _project(db_session)
    _calendar(db_session, project)
    db_session.commit()
    db_session.add(Calendar(project_id=project.id))
    with pytest.raises(IntegrityError):
        db_session.flush()


@pytest.mark.parametrize("weekdays", ["1111100", "1111111", "0000000", "1010101"])
def test_calendars_working_weekdays_accepts_seven_flags(db_session: Session, weekdays: str) -> None:
    """DB-PRJ-003 calendars：working_weekdays は 0 と 1 の 7 文字なら形として受け付ける（稼働日の有無の判断は API とルールが行う）。"""
    project = _project(db_session)
    db_session.add(Calendar(project_id=project.id, working_weekdays=weekdays))
    db_session.commit()


@pytest.mark.parametrize("weekdays", ["111110", "11111000", "111110x", "1111 00", "2111100", ""])
def test_calendars_working_weekdays_rejects_bad_shapes(db_session: Session, weekdays: str) -> None:
    """DB-PRJ-003 calendars：7 文字でない・0 と 1 以外の文字を含む値は DB が拒む。"""
    project = _project(db_session)
    db_session.add(Calendar(project_id=project.id, working_weekdays=weekdays))
    with pytest.raises(IntegrityError):
        db_session.flush()


@pytest.mark.parametrize("hours", [Decimal("0.01"), Decimal("7.5"), Decimal("24")])
def test_calendars_hours_per_day_accepts_bounds(db_session: Session, hours: Decimal) -> None:
    """DB-PRJ-003 calendars：hours_per_day は 0 より大きく 24 以下。境界の値（0.01・24）を受け付け、小数 2 桁で往復する。"""
    project = _project(db_session)
    calendar = Calendar(project_id=project.id, hours_per_day=hours)
    db_session.add(calendar)
    db_session.commit()
    db_session.expunge_all()
    loaded = db_session.get(Calendar, calendar.id)
    assert loaded is not None
    assert loaded.hours_per_day == hours


@pytest.mark.parametrize("hours", [Decimal("0"), Decimal("-1"), Decimal("24.01")])
def test_calendars_hours_per_day_rejects_out_of_range(db_session: Session, hours: Decimal) -> None:
    """DB-PRJ-003 calendars：0 以下・24 を超える 1 日の稼働時間は DB が拒む。"""
    project = _project(db_session)
    db_session.add(Calendar(project_id=project.id, hours_per_day=hours))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_calendars_project_must_exist(db_session: Session) -> None:
    """DB-PRJ-003 calendars：project_id は projects を指す。"""
    db_session.add(Calendar(project_id=999))
    with pytest.raises(IntegrityError):
        db_session.flush()


# ---------------------------------------------------------------------------
# DB-PRJ-004 calendar_holidays
# ---------------------------------------------------------------------------


def test_calendar_holidays_unique_per_calendar_and_date(db_session: Session) -> None:
    """DB-PRJ-004 calendar_holidays：calendar_id＋holiday_date で一意。別のカレンダーなら同じ日付を持てる。名前は任意。"""
    first = _calendar(db_session, _project(db_session, "P-001"))
    second = _calendar(db_session, _project(db_session, "P-002"))
    db_session.add_all(
        [
            CalendarHoliday(calendar_id=first.id, holiday_date=date(2027, 1, 1), name="元日"),
            CalendarHoliday(calendar_id=first.id, holiday_date=date(2027, 1, 11)),
            CalendarHoliday(calendar_id=second.id, holiday_date=date(2027, 1, 1), name="元日"),
        ]
    )
    db_session.commit()

    db_session.add(CalendarHoliday(calendar_id=first.id, holiday_date=date(2027, 1, 1), name="重複"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_calendar_holidays_calendar_must_exist_and_date_required(db_session: Session) -> None:
    """DB-PRJ-004 calendar_holidays：calendar_id は calendars を指す。holiday_date は必須。"""
    db_session.add(CalendarHoliday(calendar_id=999, holiday_date=date(2027, 1, 1)))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()

    calendar = _calendar(db_session, _project(db_session))
    db_session.add(CalendarHoliday(calendar_id=calendar.id, name="日付なし"))
    with pytest.raises(IntegrityError):
        db_session.flush()


# ---------------------------------------------------------------------------
# DB-PRJ-005 project_members
# ---------------------------------------------------------------------------


def test_project_members_unique_per_project_and_user(db_session: Session) -> None:
    """DB-PRJ-005 project_members：project_id＋user_id で一意。roles は JSON 配列で往復する（1 人が複数のロールを持てる）。"""
    owner = _user(db_session, "owner")
    member = _user(db_session, "member")
    project = _project(db_session, "P-001", owner)
    other = _project(db_session, "P-002", owner)
    row = ProjectMember(
        project_id=project.id, user_id=member.id, roles=["PLANNER", "EXECUTOR"], organization="開発部", team="A"
    )
    db_session.add_all([row, ProjectMember(project_id=other.id, user_id=member.id, roles=["VIEWER"])])
    db_session.commit()
    db_session.expunge_all()

    loaded = db_session.get(ProjectMember, row.id)
    assert loaded is not None
    assert loaded.roles == ["PLANNER", "EXECUTOR"]
    assert (loaded.organization, loaded.team) == ("開発部", "A")

    db_session.add(ProjectMember(project_id=project.id, user_id=member.id, roles=["PM"]))
    with pytest.raises(IntegrityError):
        db_session.flush()


@pytest.mark.parametrize(("project_id", "user_id"), [(999, None), (None, 999)], ids=["no-project", "no-user"])
def test_project_members_references(db_session: Session, project_id: int | None, user_id: int | None) -> None:
    """DB-PRJ-005 project_members：project_id は projects を、user_id は users を指す。"""
    project = _project(db_session)
    member = _user(db_session, "member")
    db_session.add(
        ProjectMember(project_id=project_id or project.id, user_id=user_id or member.id, roles=["VIEWER"])
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_project_members_roles_required(db_session: Session) -> None:
    """DB-PRJ-005 project_members：roles は必須（ロールの無いメンバーの行は作らない形にする）。"""
    project = _project(db_session)
    member = _user(db_session, "member")
    db_session.add(ProjectMember(project_id=project.id, user_id=member.id))
    with pytest.raises(IntegrityError):
        db_session.flush()


# ---------------------------------------------------------------------------
# 共通（CMN）の表の project_id → projects.id（ASSUMPTIONS.md A-08）
# ---------------------------------------------------------------------------


def test_cmn_project_id_columns_reject_missing_project(db_session: Session) -> None:
    """DB-CMN-002・003 の project_id は projects を指す。無いプロジェクトの行は DB が拒む。"""
    user = _user(db_session, "u1")
    db_session.add(RolePermission(project_id=999, role="PM", permission="wbs.edit"))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()

    db_session.add(
        ShareLink(
            project_id=999,
            token_hash="a" * 64,
            scope="GANTT",
            expires_at=datetime(2026, 12, 1, tzinfo=UTC),
            created_by=user.id,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


# ---------------------------------------------------------------------------
# 移行
# ---------------------------------------------------------------------------


def _upgrade(url: str, revision: str = "head") -> None:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, revision)


def test_migration_creates_prj_tables_matching_models(tmp_path: Path) -> None:
    """alembic upgrade head の結果に DB-PRJ-001〜005 のテーブルと CMN の表の外部キーがあり、モデルとの差が無い。"""
    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    _upgrade(url)

    engine = create_db_engine(url)
    try:
        inspector = sa.inspect(engine)
        assert set(COLUMNS_4_1) <= set(inspector.get_table_names())
        for table in ("role_permissions", "share_links"):
            to_projects = [fk for fk in inspector.get_foreign_keys(table) if fk["referred_table"] == "projects"]
            assert [fk["constrained_columns"] for fk in to_projects] == [["project_id"]], table
            # SQLite の表の作り直し（batch）で索引が落ちていない
            assert f"ix_{table}_project_id" in {index["name"] for index in inspector.get_indexes(table)}, table
        with engine.connect() as connection:
            diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
        assert diff == []
    finally:
        engine.dispose()


def test_migration_stops_on_cmn_rows_without_project(tmp_path: Path) -> None:
    """0001_cmn の後に、無いプロジェクトを指す role_permissions の行があれば、0002_prj は外部キーの違反で止まる（fail fast）。"""
    url = f"sqlite:///{tmp_path / 'orphan.db'}"
    _upgrade(url, "0001_cmn")
    engine = create_db_engine(url, enforce_foreign_keys=False)
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.text("INSERT INTO role_permissions (project_id, role, permission) VALUES (1, 'PM', 'wbs.edit')")
            )
    finally:
        engine.dispose()

    with pytest.raises(ForeignKeyViolationError, match="role_permissions"):
        _upgrade(url)
