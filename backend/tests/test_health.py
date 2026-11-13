"""構成づくりの試験：死活の口と、エラー応答の形（設計書 2.4・6.1）。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import insert
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.errors import AppError
from app.models.prj import Calendar


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


def test_foreign_keys_are_enforced(app: FastAPI) -> None:
    """アプリのエンジンでは外部キーが効く。SQLite は接続ごとに ``PRAGMA foreign_keys`` を有効にしてある（既定は無効）。
    PostgreSQL は常に効く。どちらも、無い行を指す行を拒む。"""
    engine = app.state.engine
    if engine.dialect.name == "sqlite":
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        connection.execute(insert(Calendar).values(project_id=999_999, working_weekdays="1111100", hours_per_day=8))
    if engine.dialect.name == "postgresql":  # SQLSTATE 23503（foreign_key_violation。サーバの文言の言語に依らない）
        orig: Any = caught.value.orig
        assert (orig.sqlstate, orig.diag.table_name) == ("23503", "calendars")
    else:
        assert "FOREIGN KEY constraint failed" in str(caught.value)


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


@pytest.mark.parametrize("kind", ["unique", "primary_key"])
def test_unique_violation_is_409_conflict(app: FastAPI, integrity_error: Callable[[str], IntegrityError],
                                          kind: str) -> None:
    """書き込みが一意制約（主キーを含む）に当たったら、500 でなく 409 CONFLICT（6.1 の形。同時の操作が同じ行を先に作った。
    A-49 の 3）。SQL・制約の名前は応答に出さない。"""
    error = integrity_error(kind)

    @app.post("/api/v1/_probe/unique")
    def probe() -> None:
        raise error

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/api/v1/_probe/unique")
    assert response.status_code == 409
    assert response.json() == {"error": {
        "code": "CONFLICT",
        "message": "同時に行われた別の操作と重なったため保存できませんでした。読み直してからやり直してください",
        "details": [],
    }}
    assert "INSERT" not in response.text and "UNIQUE" not in response.text.upper()


@pytest.mark.parametrize("kind", ["check", "foreign_key", "not_null"])
def test_other_integrity_errors_stay_500(app: FastAPI, integrity_error: Callable[[str], IntegrityError],
                                         kind: str) -> None:
    """CHECK・外部キー・NOT NULL の違反は 409 にしない（実装の誤り。想定外の例外として 500 INTERNAL_ERROR。中身は出さない）。"""
    error = integrity_error(kind)

    @app.post("/api/v1/_probe/integrity")
    def probe() -> None:
        raise error

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/api/v1/_probe/integrity")
    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "INTERNAL_ERROR", "message": "サーバの内部でエラーが起きました", "details": []}
    }
    assert "INSERT" not in response.text

    # 例外は握りつぶさずに投げ直す（サーバのログ・試験の TestClient に届く）
    with TestClient(app) as raising, pytest.raises(type(error)):
        raising.post("/api/v1/_probe/integrity")
