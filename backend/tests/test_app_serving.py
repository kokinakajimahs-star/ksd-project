"""構成づくりの試験：アプリが外へ出すもの（設計書 6.1、CR-019 の 1）。

- API の説明（``/docs``・``/redoc``・``/api/v1/openapi.json``）は ``PMT_API_DOCS`` が真のときだけ出す
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

DOCS_PATHS = ("/docs", "/redoc", "/api/v1/openapi.json", "/docs/oauth2-redirect")
NOT_FOUND = {"error": {"code": "NOT_FOUND", "message": "対象が見つかりません", "details": []}}

BuildApp = Callable[..., FastAPI]


@pytest.fixture()
def build_app(settings: Settings) -> Iterator[BuildApp]:
    """設定の一部を差し替えてアプリを作る関数（環境変数の PMT_API_DOCS に左右されないよう明示する）。"""
    built: list[FastAPI] = []

    def build(*, api_docs: bool = False) -> FastAPI:
        application = create_app(settings.model_copy(update={"api_docs": api_docs}))
        built.append(application)
        return application

    yield build
    for application in built:
        application.state.engine.dispose()


# ---------------------------------------------------------------------------
# API の説明（6.1・CR-019 の 1）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", DOCS_PATHS)
def test_api_docs_are_hidden_by_default(build_app: BuildApp, path: str) -> None:
    """既定（PMT_API_DOCS が偽）では API の説明を出さない。無いパスと同じ 404（2.4 の形）。"""
    with TestClient(build_app()) as client:
        response = client.get(path)
    assert response.status_code == 404
    assert response.json() == NOT_FOUND


def test_api_docs_default_setting_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PMT_API_DOCS", raising=False)
    assert Settings().api_docs is False


def test_api_docs_are_served_when_enabled(build_app: BuildApp) -> None:
    with TestClient(build_app(api_docs=True)) as client:
        schema = client.get("/api/v1/openapi.json")
        docs = client.get("/docs")
        redoc = client.get("/redoc")
    assert schema.status_code == 200
    assert "/api/v1/health" in schema.json()["paths"]
    assert docs.status_code == 200 and "/api/v1/openapi.json" in docs.text
    assert redoc.status_code == 200 and "/api/v1/openapi.json" in redoc.text

