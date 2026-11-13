"""API の共通の依存（設計書 6.1）：Bearer の認証・「今」・プロジェクトを読む口・別の群の表に書く口・設定。

認証（``get_current_user``）が通らなければ 401（``WWW-Authenticate: Bearer`` を付ける）。
権限の判定は各ルートが呼ぶサービスが行う（RULE-AUTH-001）。
"""

from __future__ import annotations

from datetime import datetime

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.services import get_session
from app.services.auth import LoginLockPolicy, User, authenticate, bind_actor
from app.services.clock import get_now
from app.services.project_access import ProjectAccess
from app.services.projects import NumberingRulesStore, ScheduleRecalculator

_bearer = HTTPBearer(auto_error=False)


def _unauthorized() -> HTTPException:
    return HTTPException(401, headers={"WWW-Authenticate": "Bearer"})


def get_bearer_token(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> str:
    """``Authorization: Bearer <トークン>`` のトークン。無ければ 401。"""
    if credentials is None or not credentials.credentials:
        raise _unauthorized()
    return credentials.credentials


def get_current_user(
    token: str = Depends(get_bearer_token),
    session: Session = Depends(get_session),
    now: datetime = Depends(get_now),
) -> User:
    """トークンの利用者。トークンが無い・失効した・期限切れ・利用者が無効なら 401。"""
    user = authenticate(session, token, now=now)
    if user is None:
        raise _unauthorized()
    # この要求で書く変更を、変更者と時刻つきで変更履歴に残す（6.1・NFR-05。記録は infra のイベント。A-37）
    bind_actor(session, user.id, now)
    return user


def get_project_access(request: Request) -> ProjectAccess:
    """アプリに載せたプロジェクトを読む口（``app.services.project_access``）。"""
    access: ProjectAccess = request.app.state.project_access
    return access


def get_numbering_rules(request: Request) -> NumberingRulesStore:
    """アプリに載せた WBS の採番規則を書く口（``app.services.projects``）。"""
    store: NumberingRulesStore = request.app.state.numbering_rules
    return store


def get_schedule_recalculator(request: Request) -> ScheduleRecalculator:
    """アプリに載せた日程の再計算の口（``app.services.projects``）。"""
    recalculator: ScheduleRecalculator = request.app.state.schedule_recalculator
    return recalculator


def get_token_ttl_hours(request: Request) -> int:
    """認証トークンの有効期間（時間。設定の ``PMT_TOKEN_TTL_HOURS``）。"""
    return int(request.app.state.settings.token_ttl_hours)


def get_login_lock_policy(request: Request) -> LoginLockPolicy:
    """ログインの試行の制限（設定の ``PMT_LOGIN_MAX_FAILURES``・``PMT_LOGIN_LOCK_MINUTES``。NFR-04）。"""
    settings = request.app.state.settings
    return LoginLockPolicy(
        max_failures=int(settings.login_max_failures), lock_minutes=int(settings.login_lock_minutes)
    )


def get_password_min_length(request: Request) -> int:
    """パスワードの最小の長さ（設定の ``PMT_PASSWORD_MIN_LENGTH``。NFR-04）。"""
    return int(request.app.state.settings.password_min_length)
