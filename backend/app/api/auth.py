"""認証と権限の API（設計書 6.2 の API-CMN-001・002・006・007・008）。"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.api.deps import (
    get_bearer_token,
    get_current_user,
    get_login_lock_policy,
    get_project_access,
    get_token_ttl_hours,
)
from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    MeOut,
    ProjectPermissionsOut,
    RoleGrants,
    RolePermissionsOut,
    RolePermissionsReplace,
)
from app.schemas.users import UserOut
from app.services import auth as auth_service
from app.services import get_session
from app.services.auth import User
from app.services.clock import get_now
from app.services.project_access import ProjectAccess

router = APIRouter(tags=["auth"])


@router.post("/auth/login", response_model=LoginResponse)
def login(
    body: LoginRequest,
    session: Session = Depends(get_session),
    now: datetime = Depends(get_now),
    ttl_hours: int = Depends(get_token_ttl_hours),
    lock_policy: auth_service.LoginLockPolicy = Depends(get_login_lock_policy),
) -> LoginResponse:
    """API-CMN-001 ログインしてトークンを得る。

    認証を要らない。ログイン名が無い・ロック中・パスワードが違う・無効な利用者は、どれも同じ 401 ``INVALID_CREDENTIALS``。
    続けて誤ると設定の回数でロックする（NFR-04）。
    """
    result = auth_service.login(
        session, login_id=body.login_id, password=body.password, now=now, ttl_hours=ttl_hours,
        lock_policy=lock_policy,
    )
    return LoginResponse(
        access_token=result.token, expires_at=result.expires_at, user=UserOut.model_validate(result.user)
    )


@router.post("/auth/logout", status_code=204)
def logout(
    _user: User = Depends(get_current_user),
    token: str = Depends(get_bearer_token),
    session: Session = Depends(get_session),
    now: datetime = Depends(get_now),
) -> Response:
    """API-CMN-002 トークンを失効させる。

    ログインが要る。送ったトークンを失効させ、以後そのトークンは 401 になる。
    """
    auth_service.logout(session, token, now=now)
    return Response(status_code=204)


def _role_permissions_out(project_id: int, table: auth_service.RolePermissionTable) -> RolePermissionsOut:
    return RolePermissionsOut(
        project_id=project_id,
        roles=[RoleGrants(role=role, permissions=permissions) for role, permissions in auth_service.role_grants(table)],
    )


@router.get("/projects/{project_id}/role-permissions", response_model=RolePermissionsOut)
def get_role_permissions(
    project_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
) -> RolePermissionsOut:
    """API-CMN-006 プロジェクトのロール×権限の表。

    権限は project.view。プロジェクトが無ければ 404。
    """
    table = auth_service.get_role_permissions(session, access, user, project_id)
    return _role_permissions_out(project_id, table)


@router.put("/projects/{project_id}/role-permissions", response_model=RolePermissionsOut)
def replace_role_permissions(
    project_id: int,
    body: RolePermissionsReplace,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
) -> RolePermissionsOut:
    """API-CMN-007 ロール×権限の表を置き換える。

    権限は permission.manage。載せなかったロールは何も持たなくなる。未知のロール・権限のコードは 400。
    どのロールも permission.manage を持たない表は 422 ``NO_PERMISSION_MANAGER``。
    """
    table = auth_service.replace_role_permissions(
        session, access, user, project_id, ((grant.role, grant.permissions) for grant in body.roles)
    )
    return _role_permissions_out(project_id, table)


@router.get("/me", response_model=MeOut)
def me(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
) -> MeOut:
    """API-CMN-008 自分の情報とプロジェクトごとの権限。

    ログインが要る。画面は権限の無い操作のボタンをこの応答で出し分ける（設計書 7.2。サーバでも必ず判定する）。
    システム管理者は全てのプロジェクトを持つ（メンバーでないものはロールが空）。
    """
    mine = auth_service.my_permissions(session, access, user)
    return MeOut(
        user=UserOut.model_validate(mine.user),
        system_permissions=mine.system_permissions,
        projects=[
            ProjectPermissionsOut(
                project_id=project.project_id,
                roles=project.roles,
                permissions=project.permissions,
                view_level=project.view_level.value,
            )
            for project in mine.projects
        ],
    )
