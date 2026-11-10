"""試験の共通の部品。

- ``settings``：一時ファイルの SQLite を指す設定（試験ごとに別の DB）
- ``app``：その設定で作ったアプリ。``Base.metadata`` の全てのテーブルを作ってある
- ``client``：FastAPI の TestClient
- ``db_session``：同じ DB へのセッション（試験の準備・確認に使う）
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import app.models as _all_models  # noqa: F401  全てのモデルを Base.metadata に載せる
from app.config import Settings
from app.infra.db import Base
from app.main import create_app


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(database_url=f"sqlite:///{tmp_path / 'test.db'}")


@pytest.fixture()
def app(settings: Settings) -> Iterator[FastAPI]:
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
