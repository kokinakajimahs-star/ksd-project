"""プロジェクトの作成の許可とログインの試行の制限：users の 3 列（設計書 1.2 の 4.1 DB-CMN-001・9.1・NFR-04。CR-010）

Revision ID: 0011_users_auth
Revises: 0010_copy_no_fk
Create Date: 2026-11-13 10:00:00.000000+00:00

- 足す列：``can_create_project``・``failed_login_count``・``locked_until``
- ``can_create_project`` は既定 false、``failed_login_count`` は既定 0（DB の既定値にも置く。既にある行はこの値になる）。
  ``failed_login_count`` の負の値は DB の検査でも拒む
- ``locked_until`` は時刻（UTC）で、ロックしていなければ空
- 真偽の既定値は ``sa.false()``（PostgreSQL の boolean に ``DEFAULT 0`` は書けない。0001_cmn と同じ）
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_users_auth"
down_revision: str | Sequence[str] | None = "0010_copy_no_fk"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("can_create_project", sa.Boolean(), server_default=sa.false(), nullable=False))
        batch_op.add_column(sa.Column("failed_login_count", sa.Integer(), server_default=sa.text("0"), nullable=False))
        batch_op.add_column(sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_check_constraint(batch_op.f("ck_users_failed_login_count"), "failed_login_count >= 0")


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("ck_users_failed_login_count"), type_="check")
        batch_op.drop_column("locked_until")
        batch_op.drop_column("failed_login_count")
        batch_op.drop_column("can_create_project")
