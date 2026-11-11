"""プロジェクト基盤（PRJ）のテーブル：projects・scope_items・calendars・calendar_holidays・project_members（設計書 4.1）

Revision ID: 0002_prj
Revises: 0001_cmn
Create Date: 2026-11-10 10:27:49.992254+00:00

- ``role_permissions.project_id``・``share_links.project_id``（0001_cmn）に ``projects.id`` への外部キーを張る
  （docs/decisions/ASSUMPTIONS.md A-08）。SQLite では表を作り直す（batch）。参照先の無い行が残っていれば、
  移行の後の外部キーの確かめ（``app.infra.db.check_foreign_keys``）で止まる
- 既定値は DB の既定値にも置く：``projects.wbs_status``（DRAFT）・``calendars.working_weekdays``（1111100）・``calendars.hours_per_day``（8）。
  ``projects.settings``（4.2 の JSON）はモデルの既定値だけで、DB の既定値は置かない
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_prj"
down_revision: str | Sequence[str] | None = "0001_cmn"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=True),
        sa.Column("requirements", sa.Text(), nullable=True),
        sa.Column("key_deliverables", sa.Text(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("target_finish_date", sa.Date(), nullable=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("wbs_status", sa.String(length=32), server_default=sa.text("'DRAFT'"), nullable=False),
        sa.Column("settings", sa.JSON(), nullable=False),
        sa.CheckConstraint(
            "wbs_status IN ('DRAFT', 'IN_REVIEW', 'PENDING_APPROVAL', 'BASELINED')", name=op.f("ck_projects_wbs_status")
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], name=op.f("fk_projects_owner_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_projects")),
        sa.UniqueConstraint("code", name=op.f("uq_projects_code")),
    )
    with op.batch_alter_table("projects", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_projects_owner_user_id"), ["owner_user_id"], unique=False)

    op.create_table(
        "scope_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.CheckConstraint("kind IN ('IN', 'OUT')", name=op.f("ck_scope_items_kind")),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name=op.f("fk_scope_items_project_id_projects")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scope_items")),
    )
    with op.batch_alter_table("scope_items", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_scope_items_project_id"), ["project_id"], unique=False)

    op.create_table(
        "calendars",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("working_weekdays", sa.String(length=7), server_default=sa.text("'1111100'"), nullable=False),
        sa.Column("hours_per_day", sa.Numeric(precision=10, scale=2), server_default=sa.text("8"), nullable=False),
        sa.CheckConstraint(
            "length(working_weekdays) = 7 AND replace(replace(working_weekdays, '0', ''), '1', '') = ''",
            name=op.f("ck_calendars_working_weekdays"),
        ),
        sa.CheckConstraint("hours_per_day > 0 AND hours_per_day <= 24", name=op.f("ck_calendars_hours_per_day")),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name=op.f("fk_calendars_project_id_projects")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_calendars")),
        sa.UniqueConstraint("project_id", name=op.f("uq_calendars_project_id")),
    )

    op.create_table(
        "calendar_holidays",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("calendar_id", sa.Integer(), nullable=False),
        sa.Column("holiday_date", sa.Date(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.ForeignKeyConstraint(
            ["calendar_id"], ["calendars.id"], name=op.f("fk_calendar_holidays_calendar_id_calendars")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_calendar_holidays")),
        sa.UniqueConstraint("calendar_id", "holiday_date", name=op.f("uq_calendar_holidays_calendar_id")),
    )

    op.create_table(
        "project_members",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("roles", sa.JSON(), nullable=False),
        sa.Column("organization", sa.String(length=200), nullable=True),
        sa.Column("team", sa.String(length=200), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name=op.f("fk_project_members_project_id_projects")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_project_members_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_project_members")),
        sa.UniqueConstraint("project_id", "user_id", name=op.f("uq_project_members_project_id")),
    )
    with op.batch_alter_table("project_members", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_project_members_user_id"), ["user_id"], unique=False)

    # 共通（CMN）の表の project_id に、projects.id への外部キーを張る（A-08）
    with op.batch_alter_table("role_permissions", schema=None) as batch_op:
        batch_op.create_foreign_key(
            batch_op.f("fk_role_permissions_project_id_projects"), "projects", ["project_id"], ["id"]
        )
    with op.batch_alter_table("share_links", schema=None) as batch_op:
        batch_op.create_foreign_key(batch_op.f("fk_share_links_project_id_projects"), "projects", ["project_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("share_links", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("fk_share_links_project_id_projects"), type_="foreignkey")
    with op.batch_alter_table("role_permissions", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("fk_role_permissions_project_id_projects"), type_="foreignkey")

    with op.batch_alter_table("project_members", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_project_members_user_id"))
    op.drop_table("project_members")

    op.drop_table("calendar_holidays")
    op.drop_table("calendars")

    with op.batch_alter_table("scope_items", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_scope_items_project_id"))
    op.drop_table("scope_items")

    with op.batch_alter_table("projects", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_projects_owner_user_id"))
    op.drop_table("projects")
