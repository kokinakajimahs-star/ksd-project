"""認証と権限のユースケース（設計書 6.1・8 章 NFR-04・9 章）：ログイン・ログアウト・トークンの照合・
プロジェクトのロール×権限の表・自分の権限。

権限は全てここ（サービス層）で RULE-AUTH-001（``app.domain.authz.authorize``）を通す。拒否は 403 ``FORBIDDEN``。
「今」は呼び手（API 層の ``get_now``）から受ける。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.domain.authz import (
    PERMISSIONS,
    ROLES,
    SYSTEM_PERMISSIONS,
    Membership,
    Principal,
    RolePermissionTable,
    ViewLevel,
    authorize,
    effective_permissions,
    project_view_level,
    role_permission_table,
)
from app.errors import AppError
from app.infra.security import generate_token, hash_token, spend_password_check_time, verify_password
from app.models.cmn import AuthSession, RolePermission
from app.models.cmn import User as User  # API 層は利用者の型をここから取る（1.3：API はモデルを直接 import しない）
from app.services.project_access import ProjectAccess

_PERMISSION_ORDER = {permission: index for index, permission in enumerate(PERMISSIONS)}


def forbidden() -> AppError:
    """権限の無い操作（403。設計書 6.1）。"""
    return AppError(403, "FORBIDDEN", "この操作の権限がありません")


def project_not_found(project_id: int) -> AppError:
    return AppError(404, "NOT_FOUND", "プロジェクトが見つかりません", details=[{"project_id": project_id}])


def sort_permissions(permissions: Iterable[str]) -> list[str]:
    """権限を 9.1 の表の順に並べる。"""
    return sorted(permissions, key=_PERMISSION_ORDER.__getitem__)


def principal_of(user: User) -> Principal:
    """``users`` の行から判定の入力を作る。

    システムの権限の個別の許可（project.create）は保存先が 4.1 に無いので空で渡す（＝システム管理者だけが持つ。OQ-09）。
    """
    return Principal(user_id=user.id, is_active=user.is_active, is_system_admin=user.is_system_admin)


def _check_aware(now: datetime) -> None:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now は時差つき（aware）の時刻で渡す")


# ---------------------------------------------------------------------------
# ログイン・トークン
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LoginResult:
    """ログインで発行したトークン（平文はこの応答でだけ返し、DB にはハッシュだけを残す）。"""

    token: str
    expires_at: datetime
    user: User


def login(session: Session, *, login_id: str, password: str, now: datetime, ttl_hours: int) -> LoginResult:
    """ログイン名とパスワードを照合し、有効期間 ``ttl_hours`` 時間の認証トークンを発行する。

    ログイン名が無い・パスワードが違う・無効な利用者は、どれも同じ 401 ``INVALID_CREDENTIALS``（どれに当たったかを漏らさない）。
    """
    _check_aware(now)
    if ttl_hours <= 0:
        raise ValueError("トークンの有効期間は正の時間で渡す")
    user = session.scalar(select(User).where(User.login_id == login_id))
    if user is None:
        spend_password_check_time(password)
        raise _invalid_credentials()
    if not verify_password(password, user.password_hash) or not user.is_active:
        raise _invalid_credentials()

    token = generate_token()
    expires_at = now + timedelta(hours=ttl_hours)
    session.add(AuthSession(user_id=user.id, token_hash=hash_token(token), expires_at=expires_at, created_at=now))
    session.commit()
    return LoginResult(token=token, expires_at=expires_at, user=user)


def _invalid_credentials() -> AppError:
    return AppError(401, "INVALID_CREDENTIALS", "ログイン名またはパスワードが違います")


def authenticate(session: Session, token: str, *, now: datetime) -> User | None:
    """Bearer のトークンから利用者を引く。無い・失効した・期限が切れた（``now >= expires_at``）・利用者が無効なら ``None``。"""
    _check_aware(now)
    if not token:
        return None
    record = _session_of(session, token)
    if record is None or record.revoked_at is not None or now >= record.expires_at:
        return None
    user = session.get(User, record.user_id)
    if user is None or not user.is_active:
        return None
    return user


def logout(session: Session, token: str, *, now: datetime) -> None:
    """そのトークンを失効させる（``revoked_at`` を入れる）。既に失効したトークンには何もしない。"""
    _check_aware(now)
    record = _session_of(session, token)
    if record is None or record.revoked_at is not None:
        return
    record.revoked_at = now
    session.commit()


def revoke_user_sessions(session: Session, user_id: int, *, now: datetime) -> None:
    """その利用者の失効していない認証トークンを全て失効させる（コミットは呼び手が行う）。"""
    session.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )


def _session_of(session: Session, token: str) -> AuthSession | None:
    return session.scalar(select(AuthSession).where(AuthSession.token_hash == hash_token(token)))


# ---------------------------------------------------------------------------
# プロジェクトのロール×権限の表
# ---------------------------------------------------------------------------


def role_grants(table: RolePermissionTable) -> list[tuple[str, list[str]]]:
    """表を（ロール, 権限の一覧）の並びにする。3 章のロールの順に全てのロールを載せ、権限は 9.1 の順に並べる。"""
    return [(role, sort_permissions(table.get(role, frozenset()))) for role in ROLES]


def load_role_permission_table(session: Session, project_id: int) -> dict[str, frozenset[str]]:
    """プロジェクトの ``role_permissions`` の行をロール → 権限の集合の表にする（行の無いロールは空の集合）。"""
    rows = session.execute(
        select(RolePermission.role, RolePermission.permission).where(RolePermission.project_id == project_id)
    ).all()
    return role_permission_table((role, permission) for role, permission in rows)


def authorize_project(
    session: Session, access: ProjectAccess, user: User, project_id: int, permission: str
) -> RolePermissionTable:
    """プロジェクトの権限を確かめる。プロジェクトが無ければ 404、権限が無ければ 403。通ればそのプロジェクトの表を返す。"""
    if not access.project_exists(session, project_id):
        raise project_not_found(project_id)
    table = load_role_permission_table(session, project_id)
    membership = access.membership(session, project_id, user.id)
    decision = authorize(principal_of(user), permission, role_permissions=table, membership=membership)
    if not decision.allowed:
        raise forbidden()
    return table


def get_role_permissions(
    session: Session, access: ProjectAccess, user: User, project_id: int
) -> RolePermissionTable:
    """プロジェクトのロール×権限の表を読む（project.view）。"""
    return authorize_project(session, access, user, project_id, "project.view")


def replace_role_permissions(
    session: Session,
    access: ProjectAccess,
    user: User,
    project_id: int,
    grants: Iterable[tuple[str, Iterable[str]]],
) -> RolePermissionTable:
    """プロジェクトのロール×権限の表を置き換える（permission.manage）。

    ``grants`` は（ロール, 権限の一覧）の並び。載せなかったロールは何も持たない（表を丸ごと置き換える）。
    同じロールを 2 回書く・3 章に無いロール・プロジェクトの表に載らない権限（未知のコード・システムの権限）は 400。
    """
    authorize_project(session, access, user, project_id, "permission.manage")

    rows: list[tuple[str, str]] = []
    seen_roles: set[str] = set()
    errors: list[dict[str, object]] = []
    for role, permissions in grants:
        if role in seen_roles:
            errors.append({"role": role, "message": "同じロールが 2 回書かれています"})
            continue
        seen_roles.add(role)
        for permission in permissions:
            rows.append((role, permission))
    try:
        table = role_permission_table(rows)
    except ValueError as exc:
        errors.append({"message": str(exc)})
    if errors:
        raise AppError(400, "VALIDATION_ERROR", "ロールか権限のコードに誤りがあります", details=errors)

    session.execute(delete(RolePermission).where(RolePermission.project_id == project_id))
    for role in ROLES:
        for permission in sort_permissions(table[role]):
            session.add(RolePermission(project_id=project_id, role=role, permission=permission))
    session.commit()
    return table


# ---------------------------------------------------------------------------
# 自分の情報と権限
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectPermissions:
    """メンバーである 1 つのプロジェクトでのロールと権限。"""

    project_id: int
    roles: list[str]
    permissions: list[str]
    view_level: ViewLevel


@dataclass(frozen=True)
class MyPermissions:
    """自分の情報と、システムの権限・プロジェクトごとの権限（画面のボタンの出し分けに使う。設計書 7.2）。"""

    user: User
    system_permissions: list[str]
    projects: list[ProjectPermissions]


def my_permissions(session: Session, access: ProjectAccess, user: User) -> MyPermissions:
    """自分の情報と、システムの権限・メンバーであるプロジェクトごとの権限（RULE-AUTH-001 の ``effective_permissions``）。"""
    principal = principal_of(user)
    system = effective_permissions(principal, role_permissions={}, membership=None) & SYSTEM_PERMISSIONS
    projects: list[ProjectPermissions] = []
    for project_id, membership in sorted(access.memberships(session, user.id).items()):
        table = load_role_permission_table(session, project_id)
        granted = effective_permissions(principal, role_permissions=table, membership=membership)
        projects.append(
            ProjectPermissions(
                project_id=project_id,
                roles=[role for role in ROLES if role in membership.roles],
                permissions=sort_permissions(granted - SYSTEM_PERMISSIONS),
                view_level=project_view_level(principal, role_permissions=table, membership=membership),
            )
        )
    return MyPermissions(user=user, system_permissions=sort_permissions(system), projects=projects)


def holds_in_any_project(
    session: Session, access: ProjectAccess, user: User, permission: str
) -> bool:
    """メンバーであるどれかのプロジェクトで、その権限を持つか。"""
    principal = principal_of(user)
    memberships: Mapping[int, Membership] = access.memberships(session, user.id)
    for project_id, membership in memberships.items():
        table = load_role_permission_table(session, project_id)
        if authorize(principal, permission, role_permissions=table, membership=membership).allowed:
            return True
    return False
