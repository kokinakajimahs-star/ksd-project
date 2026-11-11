"""共通（CMN）のテーブル：users・role_permissions・share_links・auth_sessions（設計書 4.1）

Revision ID: 0001_cmn
Revises:
Create Date: 2026-11-10 09:54:38.863723+00:00

- 時刻の列はモデルでは ``app.models.types.UTCDateTime``（UTC で保存）。移行は app のコードを import しないよう、
  その中身の ``DateTime(timezone=True)`` で書く
- 真偽の既定値は ``sa.false()``・``sa.true()``（PostgreSQL の boolean に ``DEFAULT 0`` は書けない）
- ``role_permissions.project_id``・``share_links.project_id`` の外部キー（→ ``projects.id``）は、``projects`` を作る移行で張る
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_cmn"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("login_id", sa.String(length=100), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=True),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_system_admin", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("organization", sa.String(length=200), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("login_id", name=op.f("uq_users_login_id")),
    )

    op.create_table(
        "role_permissions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("permission", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_role_permissions")),
        sa.UniqueConstraint("project_id", "role", "permission", name=op.f("uq_role_permissions_project_id")),
    )
    with op.batch_alter_table("role_permissions", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_role_permissions_project_id"), ["project_id"], unique=False)

    op.create_table(
        "share_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("scope IN ('GANTT', 'REPORT')", name=op.f("ck_share_links_scope")),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name=op.f("fk_share_links_created_by_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_share_links")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_share_links_token_hash")),
    )
    with op.batch_alter_table("share_links", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_share_links_project_id"), ["project_id"], unique=False)

    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_auth_sessions_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_auth_sessions_token_hash")),
    )
    with op.batch_alter_table("auth_sessions", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_auth_sessions_user_id"), ["user_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("auth_sessions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_auth_sessions_user_id"))
    op.drop_table("auth_sessions")

    with op.batch_alter_table("share_links", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_share_links_project_id"))
    op.drop_table("share_links")

    with op.batch_alter_table("role_permissions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_role_permissions_project_id"))
    op.drop_table("role_permissions")

    op.drop_table("users")
