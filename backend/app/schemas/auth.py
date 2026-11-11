"""認証と権限の入出力（API-CMN-001・002・006〜008）。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.users import LOGIN_ID_MAX, PASSWORD_MAX, UserOut


class LoginRequest(BaseModel):
    """ログインの入力（API-CMN-001）。"""

    model_config = ConfigDict(extra="forbid")

    login_id: str = Field(min_length=1, max_length=LOGIN_ID_MAX)
    password: str = Field(min_length=1, max_length=PASSWORD_MAX)


class LoginResponse(BaseModel):
    """発行した認証トークン。以後 ``Authorization: Bearer <access_token>`` で送る（設計書 6.1）。"""

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime
    user: UserOut


class RoleGrants(BaseModel):
    """1 つのロールが持つ権限（9.1 の権限のコード）。"""

    role: str = Field(min_length=1)
    permissions: list[str]


class RolePermissionsOut(BaseModel):
    """プロジェクトのロール×権限の表（API-CMN-006）。3 章のロールの順に全てのロールを載せる（権限の無いロールは空の一覧）。"""

    project_id: int
    roles: list[RoleGrants]


class RolePermissionsReplace(BaseModel):
    """ロール×権限の表を置き換える入力（API-CMN-007）。載せなかったロールは何も持たなくなる。"""

    model_config = ConfigDict(extra="forbid")

    roles: list[RoleGrants]


class ProjectPermissionsOut(BaseModel):
    """メンバーである 1 つのプロジェクトでのロールと権限。

    ``view_level`` は project.view で読める範囲（ALL・OWN_ASSIGNMENTS＝委託先は自分の割当の範囲だけ）。
    """

    project_id: int
    roles: list[str]
    permissions: list[str]
    view_level: str


class MeOut(BaseModel):
    """自分の情報と権限（API-CMN-008）。"""

    user: UserOut
    system_permissions: list[str]
    projects: list[ProjectPermissionsOut]
