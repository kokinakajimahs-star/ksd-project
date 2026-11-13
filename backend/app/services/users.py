"""ユーザーの管理のユースケース（設計書 6.2 の API-CMN-003〜005・9 章・NFR-10）。

- 一覧は user.admin か、どれかのプロジェクトでの member.manage（メンバーを選ぶため）
- 作る・変える・無効にするは user.admin（システム管理者だけが持つ。9.1）。プロジェクトの作成の許可（``can_create_project``。
  9.1 の project.create）もここで設定する
- パスワードの方針（NFR-04）：長さは ``min_length``（設定の ``PMT_PASSWORD_MIN_LENGTH``。既定 12）以上 1024 以下、
  ログイン名と同じ文字列は不可。文字種・有効期限は課さない。違反は 400 ``VALIDATION_ERROR``（``loc`` は ``["body", "password"]``）
- 最初のシステム管理者は環境変数 ``PMT_INITIAL_ADMIN_LOGIN``・``PMT_INITIAL_ADMIN_PASSWORD`` から作る（片方だけ・方針に反する
  パスワードなら fail fast）
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
UPDATABLE_FIELDS = frozenset(
    {"display_name", "email", "organization", "password", "is_system_admin", "can_create_project", "is_active"}
)
_NON_NULLABLE_FIELDS = frozenset({"display_name", "password", "is_system_admin", "can_create_project", "is_active"})

# パスワードの長さの上限（NFR-04。scrypt に渡す長さを抑える。API の入力の検査 app.schemas.users.PASSWORD_MAX と同じ値）
PASSWORD_MAX_LENGTH = 1024


def password_policy_problems(password: str, *, login_id: str, min_length: int) -> list[str]:
    """パスワードの方針（NFR-04）に反する点の文言（反していなければ空）。文字種・有効期限は課さない。"""
    problems: list[str] = []
    if len(password) < min_length:
        problems.append(f"パスワードは {min_length} 文字以上にしてください")
    if len(password) > PASSWORD_MAX_LENGTH:
        problems.append(f"パスワードは {PASSWORD_MAX_LENGTH} 文字以下にしてください")
    if password == login_id:
        problems.append("ログイン名と同じパスワードは使えません")
    return problems


def _check_password_policy(password: str, *, login_id: str, min_length: int) -> None:
    problems = password_policy_problems(password, login_id=login_id, min_length=min_length)
    if problems:
        raise AppError(
            400, "VALIDATION_ERROR", "入力に誤りがあります",
            details=[{"loc": ["body", "password"], "message": message} for message in problems],
        )


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
    password_min_length: int,
    email: str | None = None,
    organization: str | None = None,
    is_system_admin: bool = False,
    can_create_project: bool = False,
) -> User:
    """ユーザーを作る（user.admin）。パスワードが方針に反すれば 400。ログイン名が既にあれば 409 ``DUPLICATE_LOGIN_ID``。"""
    _require_user_admin(actor)
    _check_password_policy(password, login_id=login_id, min_length=password_min_length)
    if session.scalar(select(User.id).where(User.login_id == login_id)) is not None:
        raise _duplicate_login_id(login_id)
    user = User(
        login_id=login_id,
        display_name=display_name,
        email=email,
        organization=organization,
        password_hash=hash_password(password),
        is_system_admin=is_system_admin,
        can_create_project=can_create_project,
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
    session: Session,
    actor: User,
    user_id: int,
    changes: Mapping[str, Any],
    *,
    now: datetime,
    password_min_length: int,
) -> User:
    """ユーザーを変える・無効にする（user.admin）。``changes`` に載せた列だけを変える。

    - パスワードが方針に反すれば 400（ログイン名はその利用者のもの）
    - パスワードを設定し直したらロックを解く（数えを 0・``locked_until`` を空。NFR-04）
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
    if "password" in changes:
        _check_password_policy(changes["password"], login_id=user.login_id, min_length=password_min_length)

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
            user.failed_login_count = 0
            user.locked_until = None
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


def ensure_initial_admin(
    session: Session, *, login_id: str | None, password: str | None, password_min_length: int
) -> User | None:
    """最初のシステム管理者を作る（``PMT_INITIAL_ADMIN_LOGIN``・``PMT_INITIAL_ADMIN_PASSWORD``。設計書 8 章 NFR-04・NFR-10）。

    - 両方とも未指定なら何もしない（``None``）
    - 片方だけ・パスワードが方針（NFR-04）に反するなら ``RuntimeError``（fail fast。既定値を持たない。
      設定の誤りなので、そのユーザーが既にあっても止める）
    - そのログイン名のユーザーが既にあれば、そのまま返す（パスワード・権限を上書きしない）
    """
    if not login_id and not password:
        return None
    if not login_id or not password:
        raise RuntimeError("PMT_INITIAL_ADMIN_LOGIN と PMT_INITIAL_ADMIN_PASSWORD は両方を指定する")
    problems = password_policy_problems(password, login_id=login_id, min_length=password_min_length)
    if problems:
        raise RuntimeError("PMT_INITIAL_ADMIN_PASSWORD がパスワードの方針に反する：" + "・".join(problems))
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
