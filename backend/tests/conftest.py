"""試験の共通の部品。

- ``settings``：一時ファイルの SQLite を指す設定（試験ごとに別の DB）
- ``app``：その設定で作ったアプリ。``Base.metadata`` の全てのテーブルを作ってある
  （テーブルを作った SQLite のファイルを試験の流れごとに 1 度だけ作り、試験ごとに複製する。
  DDL を試験ごとに流すと 1 件 1 秒を超え、検証が重くなるため。複製の後にも ``create_all`` を呼ぶので、
  足りないテーブルがあれば作られる）
- ``client``：FastAPI の TestClient
- ``db_session``：同じ DB へのセッション（試験の準備・確認に使う）
- ``race``：同時実行の試験の道具（``RaceProbe``）。``SELECT … FOR UPDATE`` を PostgreSQL の方言で書き直して記録し、決めた表の
  ロックの直前・決めた行の INSERT の直前に、別のセッションで状態を変えてコミットする（先に終わった同時の操作を真似る。A-49 の 3）
- ``integrity_error``：SQLite で実際に制約（一意・主キー・CHECK・外部キー・NOT NULL）に違反させた ``IntegrityError`` を返す関数
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import ORMExecuteState, Session, sessionmaker

import app.models as _all_models  # noqa: F401  全てのモデルを Base.metadata に載せる
from app.config import Settings
from app.infra.db import Base, create_db_engine
from app.main import create_app


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(database_url=f"sqlite:///{tmp_path / 'test.db'}")


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
    engine = create_db_engine("sqlite://")
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
def integrity_error() -> Callable[[str], IntegrityError]:
    return _sqlite_integrity_error
