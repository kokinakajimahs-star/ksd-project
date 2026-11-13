"""共通（CMN）の群のテーブル（設計書 4.1・4.2・9 章）：ユーザー・ロールの権限・共有リンク・認証セッション。

- 主キーは整数の ``id``。外部キーは ``<テーブルの単数形>_id`` で、DB の外部キー制約を張る（4.2）
- トークン（共有リンク・認証セッション）は SHA-256 のハッシュ（16 進 64 文字）だけを保存する（4.2・NFR-04）
- 時刻は UTC で保存する（2.4。``UTCDateTime``）

``role_permissions.project_id`` と ``share_links.project_id`` は ``projects.id``（``app.models.prj``）を指す。
外部キーの制約は ``projects`` を作る移行（``0002_prj``）で張った（docs/decisions/ASSUMPTIONS.md A-08・A-11）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String, UniqueConstraint, false, text, true
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db import Base
from app.models.types import UTCDateTime, utc_now

# 共有リンクで見せる範囲（4.1 の share_links.scope）
SHARE_LINK_SCOPES = ("GANTT", "REPORT")

# SHA-256 の 16 進表記の長さ
TOKEN_HASH_LENGTH = 64


# DB-CMN-001
class User(Base):
    """ユーザー。ログイン名は一意。無効にした利用者は ``is_active`` を偽にする（行は消さない）。

    - ``can_create_project``：システム管理者が許したプロジェクトの作成（9.1 の project.create。設計書 1.2・CR-010）
    - ``failed_login_count``・``locked_until``：続けて誤ったログインの回数と、ロックの終わり（NFR-04。``locked_until`` が
      今より後ならロック中。ロックした時に数えは 0 に戻す）
    """

    __tablename__ = "users"
    __table_args__ = (CheckConstraint("failed_login_count >= 0", name="failed_login_count"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    login_id: Mapped[str] = mapped_column(String(100), unique=True)
    display_name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(254))
    # hashlib.scrypt のハッシュ（塩と係数を含む文字列。形式は app.infra.security が決める）
    password_hash: Mapped[str] = mapped_column(String(255))
    is_system_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    can_create_project: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    organization: Mapped[str | None] = mapped_column(String(200))
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime())


# DB-CMN-002
class RolePermission(Base):
    """プロジェクトのロール×権限の表の 1 つの組（9.2 の表をプロジェクトの作成時に写す）。

    行があればそのロールはその権限を持つ。プロジェクト・ロール・権限の組で一意。
    ロールと権限のコードの検査は権限の判定（RULE-AUTH-001）とその API が行う（4.1 は値の一覧を列に定めていない）。
    """

    __tablename__ = "role_permissions"
    __table_args__ = (UniqueConstraint("project_id", "role", "permission"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    role: Mapped[str] = mapped_column(String(32))
    permission: Mapped[str] = mapped_column(String(64))


# DB-CMN-003
class ShareLink(Base):
    """ログインせずに閲覧できる共有リンク（RPT-03）。有効期限付きで、失効させると ``revoked_at`` が入る。"""

    __tablename__ = "share_links"
    __table_args__ = (
        CheckConstraint("scope IN (" + ", ".join(f"'{scope}'" for scope in SHARE_LINK_SCOPES) + ")", name="scope"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(TOKEN_HASH_LENGTH), unique=True)
    scope: Mapped[str] = mapped_column(String(16))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


# DB-CMN-004
class AuthSession(Base):
    """ログインで発行した認証トークンの記録（有効期間は設定の ``token_ttl_hours``）。ログアウトで ``revoked_at`` が入る。"""

    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(TOKEN_HASH_LENGTH), unique=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
