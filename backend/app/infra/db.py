"""DB 接続とセッション（設計書 1.3・2.1）。

- ``Base``：全てのモデルの基底（``app.models`` の各モジュールが継承する）
- ``create_db_engine``：接続先の URL からエンジンを作る。SQLite では外部キーの制約を有効にする
- ``create_migration_engine``・``check_foreign_keys``：Alembic の移行に使う（SQLite では移行の間だけ外部キーの強制を外し、
  終わったら違反が無いことを確かめる）
- ``create_session_factory``：セッションの生成器
- ``get_session``：FastAPI の依存。1 つの要求に 1 つのセッションを渡し、終われば閉じる
  （コミットはサービス層が 1 操作 1 トランザクションで行う）

PostgreSQL（本番。設計書 1.1）の接続先は ``postgresql://…`` と書けばよい（ドライバは psycopg 3 を使う）。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from fastapi import Request
from sqlalchemy import Connection, Engine, MetaData, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

# 制約の名前を決めておく（Alembic の移行で SQLite と PostgreSQL の差を出さないため）
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

_IN_MEMORY_SQLITE = ("sqlite://", "sqlite:///:memory:")


class Base(DeclarativeBase):
    """全てのモデルの基底。"""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class ForeignKeyViolationError(RuntimeError):
    """移行の後に外部キーの違反が残っている。"""


def _enable_sqlite_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def normalize_database_url(url: str) -> str:
    """``postgresql://`` を psycopg 3 のドライバの指定（``postgresql+psycopg://``）に直す。ほかはそのまま。"""
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    return url


def create_db_engine(url: str, *, enforce_foreign_keys: bool = True) -> Engine:
    """接続先の URL からエンジンを作る（接続はまだしない）。

    SQLite では ``enforce_foreign_keys`` が真なら接続ごとに外部キーの制約を有効にする。
    インメモリの SQLite は 1 本の接続を使い回す（スレッドが変わっても同じ DB を見るため）。
    """
    url = normalize_database_url(url)
    if url.startswith("sqlite"):
        if url in _IN_MEMORY_SQLITE:
            engine = create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
        else:
            engine = create_engine(url, connect_args={"check_same_thread": False})
        if enforce_foreign_keys:
            event.listen(engine, "connect", _enable_sqlite_foreign_keys)
        return engine
    return create_engine(url, pool_pre_ping=True)


def create_migration_engine(url: str) -> Engine:
    """移行のためのエンジン。

    SQLite の表の変更（Alembic の batch）は表を作り直す（DROP してから RENAME）。外部キーの制約が効いていると、
    その DROP が子の行を連鎖で消すか、違反で失敗する。そのため移行の間は外部キーの強制を外し、
    終わったら ``check_foreign_keys`` で違反が無いことを確かめる。
    """
    return create_db_engine(url, enforce_foreign_keys=False)


def check_foreign_keys(connection: Connection) -> None:
    """SQLite の外部キーの違反が無いことを確かめる（ほかの DB では何もしない）。違反があれば例外。"""
    if connection.dialect.name != "sqlite":
        return
    violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if violations:
        rows = ", ".join(f"{table}(rowid={rowid})→{parent}" for table, rowid, parent, _fkid in violations[:20])
        raise ForeignKeyViolationError(f"外部キーの違反が {len(violations)} 件ある: {rows}")


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """セッションの生成器を作る。"""
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session(request: Request) -> Iterator[Session]:
    """FastAPI の依存：アプリに載せたセッションの生成器から 1 つ作って渡す。"""
    factory: sessionmaker[Session] = request.app.state.session_factory
    session = factory()
    try:
        yield session
    finally:
        session.close()
