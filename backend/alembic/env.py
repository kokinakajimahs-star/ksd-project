"""Alembic の環境（設計書 2.1）。

- 接続先は Alembic の設定の ``sqlalchemy.url`` があればそれ（試験が使う）、無ければ ``app.config.Settings``（環境変数 ``PMT_DATABASE_URL``）
- ``target_metadata`` は ``app.models`` を import した ``Base.metadata``（自動生成の比較に使う）
- SQLite では列の変更を ``render_as_batch`` で表の作り直しにする。作り直しの DROP が子の行を消さないよう、
  移行の間は外部キーの強制を外し、終わったら違反が無いことを確かめる（``app.infra.db.create_migration_engine``）
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import app.models  # noqa: E402,F401  全てのモデルを Base.metadata に載せる
from app.config import get_settings  # noqa: E402
from app.infra.db import (  # noqa: E402
    Base,
    check_foreign_keys,
    create_migration_engine,
    normalize_database_url,
)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    configured = config.get_main_option("sqlalchemy.url")
    return normalize_database_url(configured or get_settings().database_url)


def run_migrations_offline() -> None:
    """接続せずに SQL を出す。"""
    url = _database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=url.startswith("sqlite"),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """接続して移行を流す。"""
    url = _database_url()
    engine = create_migration_engine(url)
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                render_as_batch=url.startswith("sqlite"),
                compare_type=True,
            )
            with context.begin_transaction():
                context.run_migrations()
            check_foreign_keys(connection)
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
