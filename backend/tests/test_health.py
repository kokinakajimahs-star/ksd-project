"""構成づくりの試験：死活の口と、エラー応答の形（設計書 2.4・6.1）。"""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.errors import AppError


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_unknown_path_returns_error_shape(client: TestClient) -> None:
    response = client.get("/api/v1/no-such-resource")
    assert response.status_code == 404
    body = response.json()
    assert body == {"error": {"code": "NOT_FOUND", "message": "対象が見つかりません", "details": []}}


def test_validation_error_is_400_with_details(app: FastAPI, client: TestClient) -> None:
    @app.get("/api/v1/_probe/validation")
    def probe(limit: int) -> dict[str, int]:
        return {"limit": limit}

    response = client.get("/api/v1/_probe/validation", params={"limit": "abc"})
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"][0]["loc"] == ["query", "limit"]


def test_app_error_keeps_status_code_and_details(app: FastAPI, client: TestClient) -> None:
    @app.get("/api/v1/_probe/app-error")
    def probe() -> None:
        raise AppError(422, "SCHEDULE_CYCLE", "依存が循環しています", details=[{"activity_id": 1}])

    response = client.get("/api/v1/_probe/app-error")
    assert response.status_code == 422
    assert response.json() == {
        "error": {"code": "SCHEDULE_CYCLE", "message": "依存が循環しています", "details": [{"activity_id": 1}]}
    }


def test_sqlite_foreign_keys_are_enabled(app: FastAPI) -> None:
    with app.state.engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1


def test_settings_defaults(monkeypatch) -> None:
    for name in ("PMT_DATABASE_URL", "PMT_TOKEN_TTL_HOURS", "PMT_INITIAL_ADMIN_LOGIN", "PMT_INITIAL_ADMIN_PASSWORD",
                 "PMT_PASSWORD_MIN_LENGTH", "PMT_LOGIN_MAX_FAILURES", "PMT_LOGIN_LOCK_MINUTES"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings()
    assert settings.database_url.startswith("sqlite:///")
    assert settings.database_url.endswith("pmt-dev.db")
    assert settings.token_ttl_hours == 12
    assert settings.initial_admin_login is None
    assert settings.initial_admin_password is None
    # NFR-04・NFR-10（設計書 1.2。CR-010）
    assert settings.password_min_length == 12
    assert settings.login_max_failures == 10
    assert settings.login_lock_minutes == 15


def test_settings_read_environment(monkeypatch) -> None:
    monkeypatch.setenv("PMT_DATABASE_URL", "sqlite:///x.db")
    monkeypatch.setenv("PMT_TOKEN_TTL_HOURS", "3")
    monkeypatch.setenv("PMT_PASSWORD_MIN_LENGTH", "16")
    monkeypatch.setenv("PMT_LOGIN_MAX_FAILURES", "5")
    monkeypatch.setenv("PMT_LOGIN_LOCK_MINUTES", "30")
    settings = Settings()
    assert settings.database_url == "sqlite:///x.db"
    assert settings.token_ttl_hours == 3
    assert (settings.password_min_length, settings.login_max_failures, settings.login_lock_minutes) == (16, 5, 30)


@pytest.mark.parametrize(
    ("name", "value"),
    [("PMT_PASSWORD_MIN_LENGTH", "0"), ("PMT_PASSWORD_MIN_LENGTH", "1025"), ("PMT_LOGIN_MAX_FAILURES", "0"),
     ("PMT_LOGIN_LOCK_MINUTES", "0")],
)
def test_settings_reject_unusable_auth_policy(monkeypatch, name: str, value: str) -> None:
    # どのパスワードも通らない最小の長さ・ロックしない設定は、起動時に止める（fail fast）
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError):
        Settings()


def test_framework_http_exception_gets_japanese_message(app: FastAPI, client: TestClient) -> None:
    @app.get("/api/v1/_probe/unauthorized")
    def probe() -> None:
        raise HTTPException(401, detail="Not authenticated", headers={"WWW-Authenticate": "Bearer"})

    response = client.get("/api/v1/_probe/unauthorized")
    assert response.status_code == 401
    assert response.json() == {
        "error": {"code": "UNAUTHORIZED", "message": "認証が必要です", "details": [{"detail": "Not authenticated"}]}
    }
    assert response.headers["www-authenticate"] == "Bearer"


def test_bearer_scheme_without_header_is_japanese(app: FastAPI, client: TestClient) -> None:
    bearer = HTTPBearer()

    @app.get("/api/v1/_probe/bearer")
    def probe(credentials: HTTPAuthorizationCredentials = Depends(bearer)) -> dict[str, str]:
        return {"scheme": credentials.scheme}

    response = client.get("/api/v1/_probe/bearer")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"
    assert response.json()["error"]["message"] == "認証が必要です"
    assert response.headers["www-authenticate"] == "Bearer"


def test_http_exception_keeps_structured_detail(app: FastAPI, client: TestClient) -> None:
    @app.get("/api/v1/_probe/conflict")
    def probe() -> None:
        raise HTTPException(409, detail={"reason": "locked"})

    response = client.get("/api/v1/_probe/conflict")
    assert response.status_code == 409
    assert response.json()["error"] == {
        "code": "INVALID_STATE",
        "message": "状態が合わないため操作できません",
        "details": [{"detail": {"reason": "locked"}}],
    }


def test_unhandled_exception_returns_error_shape_without_internals(app: FastAPI) -> None:
    @app.get("/api/v1/_probe/boom")
    def probe() -> None:
        raise RuntimeError("内部の秘密の情報")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/_probe/boom")
    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "INTERNAL_ERROR", "message": "サーバの内部でエラーが起きました", "details": []}
    }
    assert "内部の秘密の情報" not in response.text
