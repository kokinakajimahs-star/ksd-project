"""ユーザーの API（設計書 6.2 の API-CMN-003・004・005）。"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_password_min_length, get_project_access
from app.schemas.users import UserCreate, UserList, UserListItemOut, UserOut, UserUpdate
from app.services import get_session
from app.services import users as users_service
from app.services.auth import User
from app.services.clock import get_now
from app.services.project_access import ProjectAccess

router = APIRouter(tags=["users"])


def _list_item(user: User, *, user_admin: bool) -> UserListItemOut:
    """一覧の 1 件。user.admin を持たない人には ``locked_until``・``can_create_project`` を null で返す（CR-019）。"""
    item = UserListItemOut.model_validate(user)
    return item if user_admin else item.model_copy(update={"locked_until": None, "can_create_project": None})


@router.get("/users", response_model=UserList)
def list_users(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
) -> UserList:
    """API-CMN-003 ユーザーの一覧。

    権限は user.admin か、どれかのプロジェクトでの member.manage。``limit``（既定 100・上限 1000）・``offset``（6.1）。
    ``locked_until``・``can_create_project`` は user.admin を持つ人にだけ返し、他は null（CR-019）。
    """
    users, total, user_admin = users_service.list_users(session, access, user, limit=limit, offset=offset)
    return UserList(items=[_list_item(item, user_admin=user_admin) for item in users], total=total, limit=limit,
                    offset=offset)


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(
    body: UserCreate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    password_min_length: int = Depends(get_password_min_length),
) -> UserOut:
    """API-CMN-004 ユーザーを作る。

    権限は user.admin。パスワードが方針（NFR-04）に反すれば 400。ログイン名が既にあれば 409 ``DUPLICATE_LOGIN_ID``。
    """
    created = users_service.create_user(
        session,
        user,
        login_id=body.login_id,
        display_name=body.display_name,
        password=body.password,
        password_min_length=password_min_length,
        email=body.email,
        organization=body.organization,
        is_system_admin=body.is_system_admin,
        can_create_project=body.can_create_project,
    )
    return UserOut.model_validate(created)


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    body: UserUpdate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    now: datetime = Depends(get_now),
    password_min_length: int = Depends(get_password_min_length),
) -> UserOut:
    """API-CMN-005 ユーザーを変える・無効にする。

    権限は user.admin。載せた項目だけを変える（無効にするのは ``is_active: false``）。
    パスワードが方針（NFR-04）に反すれば 400。パスワードを設定し直すとログインのロックを解く。
    有効なシステム管理者がいなくなる変更は 422 ``LAST_SYSTEM_ADMIN``。
    """
    updated = users_service.update_user(
        session, user, user_id, body.model_dump(exclude_unset=True), now=now, password_min_length=password_min_length
    )
    return UserOut.model_validate(updated)
