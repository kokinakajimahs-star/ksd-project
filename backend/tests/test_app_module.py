"""構成づくりの試験：``app.main`` を import しただけではアプリ（DB のエンジン）を作らないこと。"""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI

import app.main as main_module


@pytest.fixture()
def fresh_main(monkeypatch: pytest.MonkeyPatch) -> Iterator[object]:
    yield main_module
    built = vars(main_module).pop("app", None)
    if isinstance(built, FastAPI):
        built.state.engine.dispose()
    monkeypatch.undo()
    importlib.reload(main_module)


def test_import_does_not_read_environment(fresh_main: object, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PMT_TOKEN_TTL_HOURS", "0")  # 不正な値。import の時に読むなら ValidationError になる
    module = importlib.reload(main_module)
    assert "app" not in vars(module)


def test_app_attribute_is_built_once_on_first_access(
    fresh_main: object, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PMT_DATABASE_URL", f"sqlite:///{tmp_path / 'lazy.db'}")
    module = importlib.reload(main_module)
    application = module.app
    assert isinstance(application, FastAPI)
    assert module.app is application
    assert str(application.state.engine.url) == f"sqlite:///{tmp_path / 'lazy.db'}"
