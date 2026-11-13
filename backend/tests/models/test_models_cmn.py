"""共通（CMN）の群のテーブルの試験（設計書 4.1・4.2）：DB-CMN-001〜004。

列が 4.1 の行どおりであること、一意制約・外部キー・値の制約が DB で効くこと、時刻が UTC で往復すること、
移行（alembic upgrade head）の結果がモデルと食い違わないことを確かめる。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session

from app.config import BACKEND_DIR
from app.infra.db import Base, create_db_engine
from app.models.cmn import AuthSession, RolePermission, ShareLink, User
from app.models.prj import Project

TOKEN_A = "a" * 64
TOKEN_B = "b" * 64
EXPIRES = datetime(2026, 11, 11, 0, 0, tzinfo=UTC)

# 設計書 4.1 の「主な列」
COLUMNS_4_1 = {
    "users": {
        "id", "login_id", "display_name", "email", "password_hash", "is_system_admin", "can_create_project", "is_active",
        "organization", "failed_login_count", "locked_until",
    },
    "role_permissions": {"id", "project_id", "role", "permission"},
    "share_links": {
        "id", "project_id", "token_hash", "scope", "expires_at", "revoked_at", "created_by", "created_at",
    },
    "auth_sessions": {"id", "user_id", "token_hash", "expires_at", "revoked_at", "created_at"},
}


def _user(session: Session, login_id: str = "u1") -> User:
    user = User(login_id=login_id, display_name="利用者", password_hash="scrypt$dummy")
    session.add(user)
    session.flush()
    return user


def _project(session: Session, owner: User, code: str = "P-001") -> int:
    """project_id が指す projects の行（DB-PRJ-001）を作り、その id を返す。"""
    project = Project(code=code, name="プロジェクト", start_date=date(2026, 12, 1), owner_user_id=owner.id)
    session.add(project)
    session.flush()
    return project.id


@pytest.mark.parametrize("table", sorted(COLUMNS_4_1))
def test_columns_match_design_4_1(table: str) -> None:
    """DB-CMN-001〜004 の列が設計書 4.1 の行どおりである。"""
    assert set(Base.metadata.tables[table].c.keys()) == COLUMNS_4_1[table]


def test_users_defaults_and_login_id_unique(db_session: Session) -> None:
    """DB-CMN-001 users：login_id は一意。is_system_admin・can_create_project の既定は偽、is_active の既定は真、
    failed_login_count の既定は 0、locked_until は空（設計書 1.2 の 4.1。CR-010）。"""
    user = _user(db_session)
    db_session.commit()
    db_session.refresh(user)
    assert user.is_system_admin is False
    assert user.can_create_project is False
    assert user.is_active is True
    assert user.failed_login_count == 0
    assert user.locked_until is None
    assert user.email is None and user.organization is None

    db_session.add(User(login_id="u1", display_name="別の利用者", password_hash="scrypt$dummy"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_users_required_columns(db_session: Session) -> None:
    """DB-CMN-001 users：login_id・display_name・password_hash は必須。"""
    db_session.add(User(login_id="u2", display_name="利用者"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_users_server_defaults_apply_to_raw_insert(db_session: Session) -> None:
    """DB-CMN-001 users：真偽の既定値は DB の既定値としても入る（ORM を通らない書き込み）。"""
    db_session.execute(
        sa.text("INSERT INTO users (login_id, display_name, password_hash) VALUES ('raw', '利用者', 'h')")
    )
    row = db_session.execute(
        sa.text(
            "SELECT is_system_admin, is_active, can_create_project, failed_login_count, locked_until "
            "FROM users WHERE login_id = 'raw'"
        )
    ).one()
    assert (bool(row[0]), bool(row[1]), bool(row[2]), row[3], row[4]) == (False, True, False, 0, None)


def test_users_failed_login_count_is_not_negative(db_session: Session) -> None:
    """DB-CMN-001 users：failed_login_count は 0 以上（DB の検査）。locked_until は UTC の時刻で往復する。"""
    user = _user(db_session)
    user.locked_until = datetime(2026, 11, 13, 18, 15, tzinfo=timezone(timedelta(hours=9)))
    db_session.commit()
    db_session.expire_all()
    assert db_session.get(User, user.id).locked_until == datetime(2026, 11, 13, 9, 15, tzinfo=UTC)  # type: ignore[union-attr]

    with pytest.raises(IntegrityError):
        db_session.execute(sa.text("UPDATE users SET failed_login_count = -1 WHERE id = :id"), {"id": user.id})


def test_role_permissions_unique_per_project_role_permission(db_session: Session) -> None:
    """DB-CMN-002 role_permissions：project_id・role・permission の 3 つの組で一意。"""
    owner = _user(db_session)
    first, second = _project(db_session, owner, "P-001"), _project(db_session, owner, "P-002")
    db_session.add_all(
        [
            RolePermission(project_id=first, role="PM", permission="wbs.edit"),
            RolePermission(project_id=first, role="PM", permission="wbs.approve"),
            RolePermission(project_id=first, role="PLANNER", permission="wbs.edit"),
            RolePermission(project_id=second, role="PM", permission="wbs.edit"),
        ]
    )
    db_session.commit()

    db_session.add(RolePermission(project_id=first, role="PM", permission="wbs.edit"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_project_id_columns_and_reference_to_projects() -> None:
    """DB-CMN-002・003 の project_id は整数・必須。projects（DB-PRJ-001）がモデルにあれば projects.id への外部キーを持つ。

    projects は db:PRJ のスコープが作る。そのとき外部キーを張り忘れるとこの試験が落ちる（ASSUMPTIONS.md A-08）。
    """
    for table in ("role_permissions", "share_links"):
        column = Base.metadata.tables[table].c.project_id
        assert isinstance(column.type, sa.Integer)
        assert column.nullable is False
        if "projects" in Base.metadata.tables:
            assert {fk.target_fullname for fk in column.foreign_keys} == {"projects.id"}, table


def test_share_links_scope_and_token_hash(db_session: Session) -> None:
    """DB-CMN-003 share_links：scope は GANTT か REPORT。token_hash は一意。created_by は users を指す。"""
    user = _user(db_session)
    project_id = _project(db_session, user)
    for token, scope in ((TOKEN_A, "GANTT"), (TOKEN_B, "REPORT")):
        db_session.add(ShareLink(project_id=project_id, token_hash=token, scope=scope, expires_at=EXPIRES, created_by=user.id))
    db_session.commit()

    db_session.add(ShareLink(project_id=project_id, token_hash="c" * 64, scope="OTHER", expires_at=EXPIRES, created_by=user.id))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()

    db_session.add(ShareLink(project_id=project_id, token_hash=TOKEN_A, scope="GANTT", expires_at=EXPIRES, created_by=user.id))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()

    db_session.add(ShareLink(project_id=project_id, token_hash="d" * 64, scope="GANTT", expires_at=EXPIRES, created_by=999))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_share_links_expires_at_is_required(db_session: Session) -> None:
    """DB-CMN-003 share_links：共有リンクは有効期限付き（要求 RPT-03）なので expires_at は必須。"""
    user = _user(db_session)
    project_id = _project(db_session, user)
    db_session.add(ShareLink(project_id=project_id, token_hash=TOKEN_A, scope="GANTT", created_by=user.id))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_auth_sessions_user_reference_and_token_hash(db_session: Session) -> None:
    """DB-CMN-004 auth_sessions：user_id は users を指す。token_hash は一意。revoked_at は失効まで空。"""
    user = _user(db_session)
    auth_session = AuthSession(user_id=user.id, token_hash=TOKEN_A, expires_at=EXPIRES)
    db_session.add(auth_session)
    db_session.commit()
    db_session.refresh(auth_session)
    assert auth_session.revoked_at is None
    assert auth_session.created_at.tzinfo is not None

    db_session.add(AuthSession(user_id=user.id, token_hash=TOKEN_A, expires_at=EXPIRES))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()

    db_session.add(AuthSession(user_id=999, token_hash=TOKEN_B, expires_at=EXPIRES))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_datetimes_are_stored_and_returned_in_utc(db_session: Session) -> None:
    """時刻は UTC で保存する（設計書 2.4）。日本時間で渡しても UTC の aware な値で読める。"""
    user = _user(db_session)
    jst = timezone(timedelta(hours=9))
    expires_jst = datetime(2026, 11, 11, 9, 0, tzinfo=jst)
    db_session.add(AuthSession(user_id=user.id, token_hash=TOKEN_A, expires_at=expires_jst))
    db_session.commit()
    db_session.expunge_all()

    loaded = db_session.execute(sa.select(AuthSession)).scalar_one()
    assert loaded.expires_at == datetime(2026, 11, 11, 0, 0, tzinfo=UTC)
    assert loaded.expires_at.utcoffset() == timedelta(0)
    stored = db_session.execute(sa.text("SELECT expires_at FROM auth_sessions")).scalar_one()
    assert str(stored).startswith("2026-11-11 00:00:00")


def test_naive_datetime_is_rejected(db_session: Session) -> None:
    """時差の無い時刻は UTC かどうかを決められないので拒む（fail fast）。"""
    user = _user(db_session)
    db_session.add(AuthSession(user_id=user.id, token_hash=TOKEN_A, expires_at=datetime(2026, 11, 11, 0, 0)))
    with pytest.raises(StatementError, match="時差付き"):
        db_session.flush()


def test_migration_creates_cmn_tables_matching_models(tmp_path: Path) -> None:
    """alembic upgrade head の結果に DB-CMN-001〜004 のテーブルがあり、モデルとの差が無い。"""
    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")

    engine = create_db_engine(url)
    try:
        assert set(COLUMNS_4_1) <= set(sa.inspect(engine).get_table_names())
        with engine.connect() as connection:
            diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
        assert diff == []
    finally:
        engine.dispose()
