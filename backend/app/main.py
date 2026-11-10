"""FastAPI のアプリの生成とルータの登録（設計書 1.2・2.1・6.1）。

開発時の起動：``.venv/bin/uvicorn app.main:app --app-dir backend --reload``

``app`` は最初に参照されたときに作る（モジュールを import しただけでは環境変数を読まず、DB のエンジンも作らない）。
試験は ``create_app(settings)`` で接続先を差し替えて作る。
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import Settings, get_settings
from app.errors import AppError
from app.infra.db import create_db_engine, create_session_factory

API_PREFIX = "/api/v1"

# HTTP の状態コード → エラーのコード・文言（設計書 2.4・6.1。文言は日本語）
_HTTP_ERRORS: dict[int, tuple[str, str]] = {
    400: ("VALIDATION_ERROR", "入力に誤りがあります"),
    401: ("UNAUTHORIZED", "認証が必要です"),
    403: ("FORBIDDEN", "この操作の権限がありません"),
    404: ("NOT_FOUND", "対象が見つかりません"),
    405: ("METHOD_NOT_ALLOWED", "この操作はできません"),
    409: ("INVALID_STATE", "状態が合わないため操作できません"),
    422: ("BUSINESS_RULE_VIOLATION", "業務ルールに反しています"),
    500: ("INTERNAL_ERROR", "サーバの内部でエラーが起きました"),
}
_DEFAULT_HTTP_ERROR = ("HTTP_ERROR", "要求を処理できません")


def error_body(code: str, message: str, details: list[Any] | None = None) -> dict[str, Any]:
    """エラー応答の本体（設計書 2.4）。"""
    return {"error": {"code": code, "message": message, "details": details or []}}


def _app_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    return JSONResponse(
        status_code=exc.status_code,
        content=jsonable_encoder(error_body(exc.code, exc.message, exc.details)),
    )


def _validation_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    # 入力の誤りは 400（設計書 6.1。422 は業務ルールの違反に使う）
    assert isinstance(exc, RequestValidationError)
    details = [
        {"loc": list(err.get("loc", ())), "message": err.get("msg", ""), "type": err.get("type", "")}
        for err in exc.errors()
    ]
    code, message = _HTTP_ERRORS[400]
    return JSONResponse(status_code=400, content=jsonable_encoder(error_body(code, message, details)))


def _http_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    # フレームワーク（ルーティング・認証の部品）が投げる HTTPException。文言は状態コードで決まる日本語にし、
    # 既定の英語の句（"Not Found" など）以外の detail は details へ入れる。文言を指定したいエラーは AppError を使う
    assert isinstance(exc, StarletteHTTPException)
    code, message = _HTTP_ERRORS.get(exc.status_code, _DEFAULT_HTTP_ERROR)
    details: list[Any] = []
    try:
        standard_phrase: str | None = HTTPStatus(exc.status_code).phrase
    except ValueError:
        standard_phrase = None
    if exc.detail is not None and exc.detail != standard_phrase:
        details.append({"detail": exc.detail})
    return JSONResponse(
        status_code=exc.status_code,
        content=jsonable_encoder(error_body(code, message, details)),
        headers=getattr(exc, "headers", None),
    )


def _unhandled_error_handler(_request: Request, _exc: Exception) -> JSONResponse:
    # 想定外の例外も 2.4 の形で返す（中身は応答に出さない。例外はサーバのログに残る）
    code, message = _HTTP_ERRORS[500]
    return JSONResponse(status_code=500, content=error_body(code, message))


def register_routers(api: APIRouter) -> None:
    """群ごとのルータを ``/api/v1`` の下に登録する。群のルータを足したらここに 1 行足す。"""
    from app.api import health

    api.include_router(health.router)


def create_app(settings: Settings | None = None) -> FastAPI:
    """アプリを作る。試験は ``settings`` で接続先を差し替える。"""
    settings = settings or get_settings()
    engine = create_db_engine(settings.database_url)

    app = FastAPI(title="PMT", version="0.1.0", openapi_url=f"{API_PREFIX}/openapi.json")
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)

    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_error_handler)
    app.add_exception_handler(Exception, _unhandled_error_handler)

    api = APIRouter(prefix=API_PREFIX)
    register_routers(api)
    app.include_router(api)
    return app


def __getattr__(name: str) -> Any:
    # uvicorn の ``app.main:app`` のために、最初に参照されたときだけアプリを作る（PEP 562）
    if name == "app":
        application = create_app()
        globals()["app"] = application
        return application
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
