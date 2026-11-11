"""FastAPI のアプリの生成とルータの登録（設計書 1.2・2.1・6.1）。

開発時の起動：``.venv/bin/uvicorn app.main:app --app-dir backend --reload``

``app`` は最初に参照されたときに作る（モジュールを import しただけでは環境変数を読まず、DB のエンジンも作らない）。
試験は ``create_app(settings)`` で接続先を差し替えて作る。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
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
from app.services.effort import CorrectiveActionsTable, EffortLinks, eac_by_element
from app.services.progress import ComputedEtcEffort, ProgressLinks
from app.services.project_access import AssignmentsTable, ProjectMembersTable
from app.services.projects import NumberingRulesTable
from app.services.schedule import AssignmentsStore, ScheduleCalculator, ScheduleLinks, progress_entries_latest
from app.services.users import ensure_initial_admin
from app.services.wbs import (
    ActivitiesTable,
    ChangeRequestTransitionNotWired,
    CrossGroupReaders,
    change_requests_table,
    corrective_actions_table,
    estimate_records_table,
    time_entries_table,
)

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
    from app.api import auth, effort, health, progress, projects, schedule, users, wbs

    api.include_router(health.router)
    api.include_router(auth.router)
    api.include_router(users.router)
    api.include_router(projects.router)
    api.include_router(wbs.router)
    api.include_router(schedule.router)
    api.include_router(progress.router)
    api.include_router(effort.router)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    # 最初のシステム管理者（NFR-10）。両方とも未指定なら何もしない。片方だけなら起動を止める（fail fast）
    settings: Settings = app.state.settings
    if settings.initial_admin_login or settings.initial_admin_password:
        with app.state.session_factory() as session:
            ensure_initial_admin(
                session, login_id=settings.initial_admin_login, password=settings.initial_admin_password
            )
    yield


def create_app(settings: Settings | None = None) -> FastAPI:
    """アプリを作る。試験は ``settings`` で接続先を差し替える。"""
    settings = settings or get_settings()
    engine = create_db_engine(settings.database_url)

    app = FastAPI(title="PMT", version="0.1.0", openapi_url=f"{API_PREFIX}/openapi.json", lifespan=_lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    # プロジェクトの有無とメンバーを読む口（app.services.project_access。docs/decisions/ASSUMPTIONS.md A-10・A-11）。
    # 割当の活動は assignments（DB-EFF-001）を読む（db:EFF で差し替えた。A-27）
    app.state.project_access = ProjectMembersTable(assignments=AssignmentsTable())
    # 別の群の表に書く口（app.services.projects。A-14・A-15）。採番規則は表（DB-WBS-002）に書く。
    # 日程の再計算は、日程計算（RULE-SCH-001〜003）を保存まで通す実装（api:SCH で差し替えた。A-19・A-21）
    app.state.numbering_rules = NumberingRulesTable()
    app.state.schedule_recalculator = ScheduleCalculator()
    # WBS の判定・集計が読む活動（app.services.wbs。A-17）。活動の表（DB-SCH-001）を読む（db:SCH で差し替えた。A-19）
    app.state.wbs_activities = ActivitiesTable()
    # 割当（DB-EFF-001）はスケジュールの API と同じ表を読み書きする口（db:EFF。A-27）
    assignments = AssignmentsStore()
    # 責任分担表・横断照会が読む別の群の表（割当・見積の記録・実績・変更要求・是正措置・EAC。A-18）。既定は「表がまだ無い」。
    # 割当・実績は表を読む（db:EFF で差し替えた。A-27）。EAC は RULE-EFF-002 の集計を読む（api:EFF で差し替えた。A-30）。
    # 変更要求・是正措置は表を読む（db:CHG で差し替えた。A-31）。見積の記録は表を読む（db:EST で差し替えた。A-32）
    app.state.wbs_readers = CrossGroupReaders(assignments=assignments.assignments, time_entries=time_entries_table,
                                              estimate_records=estimate_records_table,
                                              change_requests=change_requests_table,
                                              corrective_actions=corrective_actions_table, eac_hours=eac_by_element)
    # 変更要求の承認申請への判断（API-WBS-019）を変更要求へ反映する口（A-18）。表はある（db:CHG）が、状態遷移（RULE-CHG-003）を
    # 通す実装が無いので止める口（A-31）。api:CHG のスコープが差し替える
    app.state.change_request_approvals = ChangeRequestTransitionNotWired()
    # スケジュールの API が読み書きする別の群の表（割当・最新の進捗。app.services.schedule。A-21）。
    # 割当は表を読み書きする（db:EFF で差し替えた。A-27）。最新の進捗は進捗の表を読む（db:PRG で差し替えた。A-22）
    app.state.schedule_links = ScheduleLinks(assignments=assignments, latest_progress=progress_entries_latest)
    # 進捗の API がスナップショットに写す実績工数・ETC の口（app.services.progress。A-26）。実績工数は表から読む
    # （db:EFF で差し替えた。A-27）。ETC は RULE-EFF-001 で求める（rule:EFF で差し替えた。A-28）
    app.state.progress_links = ProgressLinks(effort=ComputedEtcEffort())
    # 工数の API が読む別の群の表（是正措置。app.services.effort。A-30）。是正措置の表を読む（db:CHG で差し替えた。A-31）
    app.state.effort_links = EffortLinks(corrective_actions=CorrectiveActionsTable())

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
