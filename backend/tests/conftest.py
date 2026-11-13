"""試験の共通の部品。

- ``settings``：一時ファイルの SQLite を指す設定（試験ごとに別の DB）。環境変数 ``PMT_TEST_DATABASE_URL``（PostgreSQL の URL）が
  あるときだけ、その PostgreSQL に試験ごとの新しいデータベースを作って指す（本番と同じ DB での確認。CR-020。既定は SQLite のまま）
- ``app``：その設定で作ったアプリ。``Base.metadata`` の全てのテーブルを作ってある
  （テーブルを作った SQLite のファイルを試験の流れごとに 1 度だけ作り、試験ごとに複製する。
  DDL を試験ごとに流すと 1 件 1 秒を超え、検証が重くなるため。複製の後にも ``create_all`` を呼ぶので、
  足りないテーブルがあれば作られる）
- ``client``：FastAPI の TestClient
- ``db_session``：同じ DB へのセッション（試験の準備・確認に使う）
- ``race``：同時実行の試験の道具（``RaceProbe``）。``SELECT … FOR UPDATE`` を PostgreSQL の方言で書き直して記録し、決めた表の
  ロックの直前・決めた行の INSERT の直前に、別のセッションで状態を変えてコミットする（先に終わった同時の操作を真似る。A-49 の 3）
- ``integrity_error``：SQLite で実際に制約（一意・主キー・CHECK・外部キー・NOT NULL）に違反させた ``IntegrityError`` を返す関数
  （``PMT_TEST_DATABASE_URL`` があるときは、その PostgreSQL で違反させたもの）
"""

from __future__ import annotations

import os
import re
import shutil
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, make_url, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import ORMExecuteState, Session, sessionmaker

import app.models as _all_models  # noqa: F401  全てのモデルを Base.metadata に載せる
from app.config import Settings
from app.infra.db import Base, create_db_engine
from app.main import create_app


# PostgreSQL で試験を流すときの接続先（管理用。試験ごとのデータベースをここから作って消す）。無ければ SQLite で流す
TEST_DATABASE_URL_ENV = "PMT_TEST_DATABASE_URL"


class PostgresDatabases:
    """PostgreSQL の上の試験用のデータベース。

    全てのテーブルを作った雛形を試験の流れで 1 度だけ作り、試験ごとに ``CREATE DATABASE … TEMPLATE`` で複製する
    （DDL を試験ごとに流さない。SQLite の雛形の複製と同じ考え方）。接続の利用者には CREATEDB の権限が要る。
    """

    def __init__(self, admin_url: str) -> None:
        url = make_url(admin_url)
        if not url.drivername.startswith("postgresql"):
            raise pytest.UsageError(f"{TEST_DATABASE_URL_ENV} は PostgreSQL の URL にしてください（{url.drivername}）")
        self._url = url
        self._admin = create_engine(url, isolation_level="AUTOCOMMIT")
        self._prefix = f"pmt_test_{uuid.uuid4().hex[:8]}"
        self.template = f"{self._prefix}_tpl"
        self._execute(f'CREATE DATABASE "{self.template}"')
        engine = create_db_engine(self.url_of(self.template))
        try:
            Base.metadata.create_all(engine)
        finally:
            engine.dispose()

    def _execute(self, sql: str) -> None:
        with self._admin.connect() as connection:
            connection.exec_driver_sql(sql)

    def url_of(self, database: str) -> str:
        return self._url.set(database=database).render_as_string(hide_password=False)

    def create(self, *, empty: bool = False) -> str:
        """新しいデータベースを作って URL を返す（既定は雛形の複製。``empty`` なら空）。"""
        name = f"{self._prefix}_{uuid.uuid4().hex[:12]}"
        template = "template0" if empty else self.template
        self._execute(f'CREATE DATABASE "{name}" TEMPLATE "{template}"')
        return self.url_of(name)

    def drop(self, database_url: str) -> None:
        name = make_url(database_url).database
        self._execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')

    def close(self) -> None:
        self._execute(f'DROP DATABASE IF EXISTS "{self.template}" WITH (FORCE)')
        self._admin.dispose()


@pytest.fixture(scope="session")
def postgres_databases() -> Iterator[PostgresDatabases | None]:
    """``PMT_TEST_DATABASE_URL`` があるときの PostgreSQL の試験用のデータベース。無ければ None（SQLite で流す）。"""
    admin_url = os.environ.get(TEST_DATABASE_URL_ENV)
    if not admin_url:
        yield None
        return
    databases = PostgresDatabases(admin_url)
    try:
        yield databases
    finally:
        databases.close()


@pytest.fixture()
def settings(tmp_path: Path, postgres_databases: PostgresDatabases | None) -> Iterator[Settings]:
    if postgres_databases is None:
        yield Settings(database_url=f"sqlite:///{tmp_path / 'test.db'}")
        return
    database_url = postgres_databases.create()
    try:
        yield Settings(database_url=database_url)
    finally:
        postgres_databases.drop(database_url)


_SQLITE_FILE_PREFIX = "sqlite:///"


@pytest.fixture(scope="session")
def schema_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """``Base.metadata`` の全てのテーブルを作った SQLite のファイル（試験の流れで 1 度だけ作る）。"""
    path = tmp_path_factory.mktemp("schema-template") / "template.db"
    engine = create_db_engine(f"{_SQLITE_FILE_PREFIX}{path}")
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()
    return path


def _seed_from_template(database_url: str, template: Path) -> None:
    """接続先がまだ無い SQLite のファイルなら、テーブルを作ったひな形を複製しておく。ほかの接続先には何もしない。"""
    if not database_url.startswith(_SQLITE_FILE_PREFIX):
        return
    target = Path(database_url.removeprefix(_SQLITE_FILE_PREFIX))
    if target.name == ":memory:" or target.exists():
        return
    shutil.copyfile(template, target)


@pytest.fixture()
def app(settings: Settings, schema_template: Path) -> Iterator[FastAPI]:
    _seed_from_template(settings.database_url, schema_template)
    application = create_app(settings)
    Base.metadata.create_all(application.state.engine)
    yield application
    application.state.engine.dispose()


@pytest.fixture()
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def db_session(app: FastAPI) -> Iterator[Session]:
    session = app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


_LOCKED_TABLE = (re.compile(r"FOR UPDATE OF (\w+)"), re.compile(r"\bFROM (\w+)"))


class RaceProbe:
    """同時実行の試験の道具（A-49 の 3）。

    SQLite は ``FOR UPDATE`` を無視する（SQL に書かない）ので、ORM の文を PostgreSQL の方言で書き直して確かめる。

    - ``locked``：``SELECT … FOR UPDATE`` でロックした表の名前（流した順。``FOR UPDATE OF`` があればその表、無ければ最初の FROM）
    - ``before_lock(表, 処理)``：その表を次にロックする文を流す**直前**に、別のセッションで ``処理`` を流してコミットする（1 度だけ）。
      PostgreSQL で「先にロックを取った同時の操作がコミットし、こちらはロックを待ってからコミットの後の値を読む」順を、ロックの効かない
      SQLite で再現する。判定をロックの前の値で行う実装なら、処理の結果を見ずに通ってしまう
    - ``before_insert(モデル, 処理)``：その型の新しい行を flush する**直前**に、別のセッションで ``処理`` を流してコミットする（1 度だけ）。
      同時の操作が同じ行を先に作った順（一意制約の違反）を再現する
    """

    def __init__(self, factory: sessionmaker[Session]) -> None:
        self._factory = factory
        self.locked: list[str] = []
        self._before_lock: dict[str, list[Callable[[Session], None]]] = {}
        self._before_insert: list[tuple[type[Any], Callable[[Session], None]]] = []
        self._running = False
        event.listen(factory, "do_orm_execute", self._on_execute)
        event.listen(factory, "before_flush", self._on_flush)

    def close(self) -> None:
        event.remove(self._factory, "do_orm_execute", self._on_execute)
        event.remove(self._factory, "before_flush", self._on_flush)

    def before_lock(self, table: str, action: Callable[[Session], None]) -> None:
        self._before_lock.setdefault(table, []).append(action)

    def before_insert(self, model: type[Any], action: Callable[[Session], None]) -> None:
        self._before_insert.append((model, action))

    def _run(self, action: Callable[[Session], None]) -> None:
        self._running = True
        try:
            with self._factory() as other:
                if other.get_bind().dialect.name == "postgresql":
                    # PostgreSQL ではロックが効く。試験の流れが既に持つ行ロックを別のセッションが待つと、同じスレッドなので
                    # 終わらない。待たずに誤りにして、試験の組み立ての誤りとして見せる
                    other.execute(text("SET LOCAL lock_timeout = '5s'"))
                action(other)
                other.commit()
        finally:
            self._running = False

    def _on_execute(self, state: ORMExecuteState) -> None:
        if self._running or not state.is_select:
            return
        sql = str(state.statement.compile(dialect=postgresql.dialect()))
        if "FOR UPDATE" not in sql:
            return
        match = _LOCKED_TABLE[0].search(sql) or _LOCKED_TABLE[1].search(sql)
        assert match is not None, sql
        table = match.group(1)
        self.locked.append(table)
        for action in self._before_lock.pop(table, []):
            self._run(action)

    def _on_flush(self, session: Session, _context: Any, _instances: Any) -> None:
        if self._running:
            return
        for entry in list(self._before_insert):
            model, action = entry
            if any(isinstance(obj, model) for obj in session.new):
                self._before_insert.remove(entry)
                self._run(action)


@pytest.fixture()
def race(app: FastAPI) -> Iterator[RaceProbe]:
    probe = RaceProbe(app.state.session_factory)
    yield probe
    probe.close()


# 種類 → 違反させる INSERT（表 t には id=1・code='a' の行がある）
_VIOLATIONS = {
    "unique": "INSERT INTO t (id, code, n) VALUES (2, 'a', 0)",
    "primary_key": "INSERT INTO t (id, code, n) VALUES (1, 'b', 0)",
    "check": "INSERT INTO t (id, code, n) VALUES (3, 'c', -1)",
    "foreign_key": "INSERT INTO t (id, code, n, parent_id) VALUES (4, 'd', 0, 999)",
    "not_null": "INSERT INTO t (id, code, n) VALUES (5, 'e', NULL)",
}


def _sqlite_integrity_error(kind: str) -> IntegrityError:
    """SQLite（外部キーを有効にしたアプリのエンジン）で実際に制約に違反させ、その ``IntegrityError`` を返す。"""
    return _integrity_error_on(create_db_engine("sqlite://"), kind)


def _integrity_error_on(engine: Any, kind: str) -> IntegrityError:
    """空の DB に表を作って制約に違反させ、その ``IntegrityError`` を返す（エンジンは最後に閉じる）。"""
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
            connection.exec_driver_sql(
                "CREATE TABLE t (id INTEGER PRIMARY KEY, code TEXT UNIQUE, n INTEGER NOT NULL CHECK (n >= 0),"
                " parent_id INTEGER REFERENCES parent(id))"
            )
            connection.exec_driver_sql("INSERT INTO t (id, code, n) VALUES (1, 'a', 0)")
        with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
            connection.exec_driver_sql(_VIOLATIONS[kind])
        return caught.value
    finally:
        engine.dispose()


@pytest.fixture()
def integrity_error(postgres_databases: PostgresDatabases | None) -> Iterator[Callable[[str], IntegrityError]]:
    if postgres_databases is None:
        yield _sqlite_integrity_error
        return
    created: list[str] = []

    def on_postgres(kind: str) -> IntegrityError:
        database_url = postgres_databases.create(empty=True)
        created.append(database_url)
        return _integrity_error_on(create_db_engine(database_url), kind)

    try:
        yield on_postgres
    finally:
        for database_url in created:
            postgres_databases.drop(database_url)
