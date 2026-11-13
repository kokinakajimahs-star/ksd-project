"""ユーザーの入出力（API-CMN-003〜005・008）。パスワードのハッシュとログインの誤りの数えは出力に載せない。

一覧（API-CMN-003）のロックの状態と作成の許可は user.admin を持つ人にだけ返す（``UserListItemOut``。CR-019）。

長さの上限は ``users`` の列（docs/decisions/ASSUMPTIONS.md A-08）に合わせる。
パスワードの最小の長さ・ログイン名と同じでないことは設定に依るので、サービス層が確かめる（NFR-04）。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

LOGIN_ID_MAX = 100
DISPLAY_NAME_MAX = 200
EMAIL_MAX = 254
ORGANIZATION_MAX = 200
# scrypt に渡す長さの上限（極端に長い入力で照合の時間を伸ばさせない。NFR-04 の 1024 以下）
PASSWORD_MAX = 1024


class UserOut(BaseModel):
    """ユーザー（``users`` の行。password_hash・failed_login_count を除く）。

    ``locked_until`` はログインのロックの終わり（今より後ならロック中。管理の画面で示す。NFR-04）。
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    login_id: str
    display_name: str
    email: str | None
    organization: str | None
    is_system_admin: bool
    can_create_project: bool
    is_active: bool
    locked_until: datetime | None


class UserListItemOut(BaseModel):
    """ユーザーの一覧（API-CMN-003）の 1 件。``UserOut`` と同じ項目。

    ``locked_until``・``can_create_project`` は user.admin を持つ人にだけ返し、member.manage だけで読む人には null（CR-019）。
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    login_id: str
    display_name: str
    email: str | None
    organization: str | None
    is_system_admin: bool
    can_create_project: bool | None
    is_active: bool
    locked_until: datetime | None


class UserList(BaseModel):
    """ユーザーの一覧（``limit``・``offset`` の範囲）と総数。"""

    items: list[UserListItemOut]
    total: int
    limit: int
    offset: int


class UserCreate(BaseModel):
    """ユーザーを作る入力（API-CMN-004）。"""

    model_config = ConfigDict(extra="forbid")

    login_id: str = Field(min_length=1, max_length=LOGIN_ID_MAX)
    display_name: str = Field(min_length=1, max_length=DISPLAY_NAME_MAX)
    password: str = Field(min_length=1, max_length=PASSWORD_MAX)
    email: str | None = Field(default=None, max_length=EMAIL_MAX)
    organization: str | None = Field(default=None, max_length=ORGANIZATION_MAX)
    is_system_admin: bool = False
    can_create_project: bool = False


class UserUpdate(BaseModel):
    """ユーザーを変える・無効にする入力（API-CMN-005）。載せた項目だけを変える。ログイン名は変えない。

    display_name・password・is_system_admin・can_create_project・is_active に null は書けない（サービス層が 400 にする）。
    """

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=DISPLAY_NAME_MAX)
    email: str | None = Field(default=None, max_length=EMAIL_MAX)
    organization: str | None = Field(default=None, max_length=ORGANIZATION_MAX)
    password: str | None = Field(default=None, min_length=1, max_length=PASSWORD_MAX)
    is_system_admin: bool | None = None
    can_create_project: bool | None = None
    is_active: bool | None = None
