"""ユーザーの入出力（API-CMN-003〜005・008）。パスワードのハッシュは出力に載せない。

長さの上限は ``users`` の列（docs/decisions/ASSUMPTIONS.md A-08）に合わせる。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

LOGIN_ID_MAX = 100
DISPLAY_NAME_MAX = 200
EMAIL_MAX = 254
ORGANIZATION_MAX = 200
# scrypt に渡す長さの上限（極端に長い入力で照合の時間を伸ばさせない）
PASSWORD_MAX = 1024


class UserOut(BaseModel):
    """ユーザー（``users`` の行。password_hash を除く）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    login_id: str
    display_name: str
    email: str | None
    organization: str | None
    is_system_admin: bool
    is_active: bool


class UserList(BaseModel):
    """ユーザーの一覧（``limit``・``offset`` の範囲）と総数。"""

    items: list[UserOut]
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


class UserUpdate(BaseModel):
    """ユーザーを変える・無効にする入力（API-CMN-005）。載せた項目だけを変える。ログイン名は変えない。

    display_name・password・is_system_admin・is_active に null は書けない（サービス層が 400 にする）。
    """

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=DISPLAY_NAME_MAX)
    email: str | None = Field(default=None, max_length=EMAIL_MAX)
    organization: str | None = Field(default=None, max_length=ORGANIZATION_MAX)
    password: str | None = Field(default=None, min_length=1, max_length=PASSWORD_MAX)
    is_system_admin: bool | None = None
    is_active: bool | None = None
