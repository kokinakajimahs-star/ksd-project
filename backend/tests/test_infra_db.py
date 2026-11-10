"""構成づくりの試験：DB の接続と移行の基盤（app.infra.db・backend/alembic）。"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.config import BACKEND_DIR
from app.infra.db import (
    ForeignKeyViolationError,
    check_foreign_keys,
    create_db_engine,
    create_migration_engine,
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
