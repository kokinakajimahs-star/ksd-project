"""構成づくりの試験：DB の接続と移行の基盤（app.infra.db・backend/alembic）。"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

import psycopg.errors
import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, ORMExecuteState, mapped_column

from app.config import BACKEND_DIR
from app.infra.db import (
    ForeignKeyViolationError,
    check_foreign_keys,
    create_db_engine,
    create_migration_engine,
    create_session_factory,
    is_unique_violation,
    lock_row,
    lock_rows,
    normalize_database_url,
)


def _foreign_keys_flag(engine: sa.Engine) -> int:
    with engine.connect() as connection:
        return int(connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one())


def _make_parent_child(engine: sa.Engine) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
        connection.exec_driver_sql(
            "CREATE TABLE child (id INTEGER PRIMARY KEY,"
            " parent_id INTEGER NOT NULL REFERENCES parent(id) ON DELETE CASCADE)"
        )
        connection.exec_driver_sql("INSERT INTO parent (id) VALUES (1)")
        connection.exec_driver_sql("INSERT INTO child (id, parent_id) VALUES (10, 1)")


def test_application_engine_enforces_foreign_keys(tmp_path: Path) -> None:
    engine = create_db_engine(f"sqlite:///{tmp_path / 'app.db'}")
    assert _foreign_keys_flag(engine) == 1
    engine.dispose()


def test_migration_engine_does_not_enforce_foreign_keys(tmp_path: Path) -> None:
    engine = create_migration_engine(f"sqlite:///{tmp_path / 'migrate.db'}")
    assert _foreign_keys_flag(engine) == 0
    engine.dispose()


def test_batch_recreate_of_parent_keeps_child_rows(tmp_path: Path) -> None:
    """表の作り直し（SQLite の batch）が、子の行を連鎖で消さないこと。"""
    url = f"sqlite:///{tmp_path / 'batch.db'}"
    setup_engine = create_db_engine(url)
    _make_parent_child(setup_engine)
    setup_engine.dispose()

    engine = create_migration_engine(url)
    with engine.begin() as connection:
        operations = Operations(MigrationContext.configure(connection, opts={"render_as_batch": True}))
        with operations.batch_alter_table("parent", recreate="always") as batch:
            batch.add_column(sa.Column("name", sa.String(50)))
        check_foreign_keys(connection)
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT id, parent_id FROM child").fetchall() == [(10, 1)]
        columns = [row[1] for row in connection.exec_driver_sql("PRAGMA table_info(parent)")]
        assert columns == ["id", "name"]
    engine.dispose()


def test_check_foreign_keys_reports_violations(tmp_path: Path) -> None:
    engine = create_migration_engine(f"sqlite:///{tmp_path / 'violation.db'}")
    _make_parent_child(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("INSERT INTO child (id, parent_id) VALUES (11, 999)")
    with engine.connect() as connection, pytest.raises(ForeignKeyViolationError, match="1 件"):
        check_foreign_keys(connection)
    engine.dispose()


def test_postgresql_url_uses_psycopg3_driver() -> None:
    assert normalize_database_url("postgresql://u@localhost/pmt") == "postgresql+psycopg://u@localhost/pmt"
    assert normalize_database_url("postgresql+psycopg://u@h/db") == "postgresql+psycopg://u@h/db"
    assert normalize_database_url("sqlite:///x.db") == "sqlite:///x.db"
    engine = create_db_engine("postgresql://u@localhost/pmt")  # 接続はしない
    assert engine.dialect.name == "postgresql"
    assert engine.dialect.driver == "psycopg"
    engine.dispose()


def test_in_memory_sqlite_is_shared_across_threads() -> None:
    engine = create_db_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    seen: list[list[str]] = []

    def read_tables() -> None:
        with engine.connect() as connection:
            seen.append([row[0] for row in connection.exec_driver_sql("SELECT name FROM sqlite_master")])

    worker = threading.Thread(target=read_tables)
    worker.start()
    worker.join()
    assert seen == [["t"]]
    engine.dispose()


def test_alembic_upgrade_head_runs_against_given_url(tmp_path: Path) -> None:
    database = tmp_path / "alembic.db"
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    command.upgrade(config, "head")
    engine = create_db_engine(f"sqlite:///{database}")
    assert "alembic_version" in sa.inspect(engine).get_table_names()
    engine.dispose()


# ---------------------------------------------------------------------------
# 同時実行（A-49 の 3）：一意制約の違反の見分け方と、行のロック・読み直し
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(("kind", "unique"), [("unique", True), ("primary_key", True), ("check", False),
                                              ("foreign_key", False), ("not_null", False)])
def test_is_unique_violation_on_sqlite(integrity_error: Callable[[str], IntegrityError], kind: str,
                                       unique: bool) -> None:
    """SQLite は拡張エラーコードの名前で見分ける：一意制約・主キーだけが真（CHECK・外部キー・NOT NULL は 409 にしない）。"""
    assert is_unique_violation(integrity_error(kind)) is unique


@pytest.mark.parametrize(("error", "unique"), [
    (psycopg.errors.UniqueViolation, True),
    (psycopg.errors.CheckViolation, False),
    (psycopg.errors.ForeignKeyViolation, False),
    (psycopg.errors.NotNullViolation, False),
])
def test_is_unique_violation_on_postgresql(error: type[psycopg.errors.IntegrityError], unique: bool) -> None:
    """PostgreSQL（psycopg 3）は SQLSTATE で見分ける：23505（unique_violation）だけが真。

    ここでは psycopg の例外の型（SQLSTATE を持つ）を SQLAlchemy の ``IntegrityError`` で包んだもので確かめる。実際の PostgreSQL へ
    つないでの確認はしていない。"""
    assert is_unique_violation(IntegrityError("INSERT …", {}, error("違反"))) is unique


def test_is_unique_violation_reads_psycopg2_pgcode() -> None:
    """psycopg2 の形（SQLSTATE を ``pgcode`` に持つ）でも同じに見分ける。"""

    class Psycopg2Error(Exception):
        def __init__(self, pgcode: str) -> None:
            super().__init__(pgcode)
            self.pgcode = pgcode

    assert is_unique_violation(IntegrityError("INSERT …", {}, Psycopg2Error("23505"))) is True
    assert is_unique_violation(IntegrityError("INSERT …", {}, Psycopg2Error("23514"))) is False


class _LockBase(DeclarativeBase):
    pass


class _Item(_LockBase):
    __tablename__ = "lock_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str]


def test_lock_row_and_lock_rows_reread_values_committed_by_others(tmp_path: Path) -> None:
    """``lock_row``・``lock_rows`` は ``SELECT … FOR UPDATE`` を出し（PostgreSQL の方言で確かめる。SQLite は無視する）、
    セッションに既にある行も DB の値で上書きする（ロックの前に読んだ古い値で判定させない）。"""
    engine = create_db_engine(f"sqlite:///{tmp_path / 'lock.db'}")
    _LockBase.metadata.create_all(engine)
    factory = create_session_factory(engine)
    with factory() as setup:
        setup.add_all([_Item(id=1, status="OPEN"), _Item(id=2, status="OPEN")])
        setup.commit()

    locked_sql: list[str] = []

    def record(state: ORMExecuteState) -> None:
        sql = str(state.statement.compile(dialect=postgresql.dialect()))
        if state.is_select and "FOR UPDATE" in sql:
            locked_sql.append(sql)

    def finish_by_other(item_id: int) -> None:
        with factory() as other:
            item = other.get(_Item, item_id)
            assert item is not None
            item.status = "DONE"
            other.commit()

    with factory() as session:
        sa.event.listen(session, "do_orm_execute", record)
        first = session.get(_Item, 1)
        assert first is not None and first.status == "OPEN"
        both = session.scalars(sa.select(_Item).order_by(_Item.id)).all()
        assert [i.status for i in both] == ["OPEN", "OPEN"]
        finish_by_other(1)
        finish_by_other(2)

        # ロックしない読み直しはセッションの古い値を返す（だから判定にはロックした読み直しを使う）
        assert session.get(_Item, 1) is first and first.status == "OPEN"
        assert locked_sql == []

        assert lock_row(session, _Item, 1) is first and first.status == "DONE"
        assert lock_row(session, _Item, 999) is None
        assert [i.status for i in session.scalars(lock_rows(sa.select(_Item).order_by(_Item.id)))] == ["DONE", "DONE"]
        assert len(locked_sql) == 3 and all(sql.rstrip().endswith("FOR UPDATE") for sql in locked_sql)
        assert "FROM lock_items" in locked_sql[0] and "WHERE lock_items.id = " in locked_sql[0]
    engine.dispose()
