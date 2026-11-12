"""ユーザーの管理のユースケース（設計書 6.2 の API-CMN-003〜005・9 章・NFR-10）。

- 一覧は user.admin か、どれかのプロジェクトでの member.manage（メンバーを選ぶため）
- 作る・変える・無効にするは user.admin（システム管理者だけが持つ。9.1）
- 最初のシステム管理者は環境変数 ``PMT_INITIAL_ADMIN_LOGIN``・``PMT_INITIAL_ADMIN_PASSWORD`` から作る（片方だけなら fail fast）
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.authz import authorize
from app.errors import AppError
from app.infra.security import hash_password
from app.models.cmn import User
from app.services.auth import forbidden, holds_in_any_project, principal_of, revoke_user_sessions
from app.services.project_access import ProjectAccess

# PATCH で変えられる列（login_id は変えない）と、そのうち空（null）にできない列
UPDATABLE_FIELDS = frozenset({"display_name", "email", "organization", "password", "is_system_admin", "is_active"})
_NON_NULLABLE_FIELDS = frozenset({"display_name", "password", "is_system_admin", "is_active"})


def _require_user_admin(actor: User) -> None:
    if not authorize(principal_of(actor), "user.admin").allowed:
        raise forbidden()


def _user_not_found(user_id: int) -> AppError:
    return AppError(404, "NOT_FOUND", "ユーザーが見つかりません", details=[{"user_id": user_id}])


def _duplicate_login_id(login_id: str) -> AppError:
    return AppError(409, "DUPLICATE_LOGIN_ID", "そのログイン名は既に使われています", details=[{"login_id": login_id}])


def list_users(
    session: Session, access: ProjectAccess, actor: User, *, limit: int, offset: int
) -> tuple[list[User], int]:
    """ユーザーの一覧（ID の順）と総数。user.admin か、どれかのプロジェクトで member.manage を持つ利用者だけが読める。"""
    if not authorize(principal_of(actor), "user.admin").allowed and not holds_in_any_project(
        session, access, actor, "member.manage"
    ):
        raise forbidden()
    total = session.scalar(select(func.count()).select_from(User)) or 0
    users = list(session.scalars(select(User).order_by(User.id).limit(limit).offset(offset)))
    return users, total


def create_user(
    session: Session,
    actor: User,
    *,
    login_id: str,
    display_name: str,
    password: str,
    email: str | None = None,
    organization: str | None = None,
    is_system_admin: bool = False,
) -> User:
    """ユーザーを作る（user.admin）。ログイン名が既にあれば 409 ``DUPLICATE_LOGIN_ID``。"""
    _require_user_admin(actor)
    if session.scalar(select(User.id).where(User.login_id == login_id)) is not None:
        raise _duplicate_login_id(login_id)
    user = User(
        login_id=login_id,
        display_name=display_name,
        email=email,
        organization=organization,
        password_hash=hash_password(password),
        is_system_admin=is_system_admin,
        is_active=True,
    )
    session.add(user)
    try:
        session.commit()
    except IntegrityError as exc:  # 同時に同じログイン名で作られた
        session.rollback()
        raise _duplicate_login_id(login_id) from exc
    return user


def update_user(
    session: Session, actor: User, user_id: int, changes: Mapping[str, Any], *, now: datetime
) -> User:
    """ユーザーを変える・無効にする（user.admin）。``changes`` に載せた列だけを変える。

    - パスワードを変えた・無効にしたら、その利用者の認証トークンを全て失効させる
    - 有効なシステム管理者が 1 人も残らなくなる変更（最後の 1 人を無効にする・管理者から外す）は 422 ``LAST_SYSTEM_ADMIN``
    """
    _require_user_admin(actor)
    unknown = set(changes) - UPDATABLE_FIELDS
    if unknown:
        raise ValueError(f"変えられない列: {sorted(unknown)}")
    nulls = sorted(name for name in _NON_NULLABLE_FIELDS & set(changes) if changes[name] is None)
    if nulls:
        raise AppError(
            400, "VALIDATION_ERROR", "入力に誤りがあります",
            details=[{"loc": ["body", name], "message": "空にできません"} for name in nulls],
        )

    user = session.get(User, user_id)
    if user is None:
        raise _user_not_found(user_id)

    stays_admin = changes.get("is_system_admin", user.is_system_admin) and changes.get("is_active", user.is_active)
    if user.is_system_admin and user.is_active and not stays_admin and _other_active_admins(session, user.id) == 0:
        raise AppError(
            422, "LAST_SYSTEM_ADMIN", "有効なシステム管理者が 1 人もいなくなるため変更できません",
            details=[{"user_id": user.id}],
        )

    revoke = False
    for name, value in changes.items():
        if name == "password":
            user.password_hash = hash_password(value)
            revoke = True
        else:
            setattr(user, name, value)
    if changes.get("is_active") is False:
        revoke = True
    if revoke:
        revoke_user_sessions(session, user.id, now=now)
    session.commit()
    return user


def _other_active_admins(session: Session, user_id: int) -> int:
    return session.scalar(
        select(func.count())
        .select_from(User)
        .where(User.is_system_admin.is_(True), User.is_active.is_(True), User.id != user_id)
    ) or 0


def ensure_initial_admin(session: Session, *, login_id: str | None, password: str | None) -> User | None:
    """最初のシステム管理者を作る（``PMT_INITIAL_ADMIN_LOGIN``・``PMT_INITIAL_ADMIN_PASSWORD``。設計書 8 章 NFR-10）。

    - 両方とも未指定なら何もしない（``None``）
    - 片方だけなら ``RuntimeError``（fail fast。既定値を持たない）
    - そのログイン名のユーザーが既にあれば、そのまま返す（パスワード・権限を上書きしない）
    """
    if not login_id and not password:
        return None
    if not login_id or not password:
        raise RuntimeError("PMT_INITIAL_ADMIN_LOGIN と PMT_INITIAL_ADMIN_PASSWORD は両方を指定する")
    existing = session.scalar(select(User).where(User.login_id == login_id))
    if existing is not None:
        return existing
    user = User(
        login_id=login_id,
        display_name=login_id,
        password_hash=hash_password(password),
        is_system_admin=True,
        is_active=True,
    )
    session.add(user)
    session.commit()
    return user
