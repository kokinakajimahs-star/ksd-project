"""構成づくりの試験：死活の口と、エラー応答の形（設計書 2.4・6.1）。"""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.testclient import TestClient

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
    for name in ("PMT_DATABASE_URL", "PMT_TOKEN_TTL_HOURS", "PMT_INITIAL_ADMIN_LOGIN", "PMT_INITIAL_ADMIN_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings()
    assert settings.database_url.startswith("sqlite:///")
    assert settings.database_url.endswith("pmt-dev.db")
    assert settings.token_ttl_hours == 12
    assert settings.initial_admin_login is None
    assert settings.initial_admin_password is None


def test_settings_read_environment(monkeypatch) -> None:
    monkeypatch.setenv("PMT_DATABASE_URL", "sqlite:///x.db")
    monkeypatch.setenv("PMT_TOKEN_TTL_HOURS", "3")
    settings = Settings()
    assert settings.database_url == "sqlite:///x.db"
    assert settings.token_ttl_hours == 3


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
