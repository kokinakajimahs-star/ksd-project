"""スケジュール（SCH）のテーブル：activities・activity_dependencies・schedule_baselines・schedule_baseline_items（設計書 4.1）

Revision ID: 0004_sch
Revises: 0003_wbs
Create Date: 2026-11-11 02:13:47.594595+00:00

- 時刻の列はモデルでは ``app.models.types.UTCDateTime``。移行は app のコードを import せず ``DateTime(timezone=True)`` で書く（0001 と同じ）
- 既定値は DB の既定値にも置く：``activities.is_milestone``・``is_critical``（偽）、依存の ``dep_type``（FS）・``lag_days``（0）、
  ベースラインの項目の ``is_milestone``（偽）
- ``schedule_baselines.change_request_id`` の ``change_requests.id`` への外部キーは、``change_requests``（DB-CHG-001）を作る移行で張る
  （docs/decisions/ASSUMPTIONS.md A-15・A-19）
- 外部キーに ``ON DELETE`` を付けない（A-11・A-15・A-19）
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_sch"
down_revision: str | Sequence[str] | None = "0003_wbs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "schedule_baselines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("change_request_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("reason IN ('MANUAL', 'RE_APPROVAL', 'CHANGE_REQUEST')", name=op.f("ck_schedule_baselines_reason")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_schedule_baselines_version")),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name=op.f("fk_schedule_baselines_created_by_users")),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name=op.f("fk_schedule_baselines_project_id_projects")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_schedule_baselines")),
        sa.UniqueConstraint("project_id", "version", name=op.f("uq_schedule_baselines_project_id"))
    )
    with op.batch_alter_table("schedule_baselines", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_schedule_baselines_change_request_id"), ["change_request_id"], unique=False)

    op.create_table(
        "activities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("work_package_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("requested_start", sa.Date(), nullable=True),
        sa.Column("duration_days", sa.Integer(), nullable=False),
        sa.Column("is_milestone", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("estimate_hours", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("progress_method", sa.String(length=32), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("finish_date", sa.Date(), nullable=True),
        sa.Column("early_start", sa.Date(), nullable=True),
        sa.Column("early_finish", sa.Date(), nullable=True),
        sa.Column("late_start", sa.Date(), nullable=True),
        sa.Column("late_finish", sa.Date(), nullable=True),
        sa.Column("total_float", sa.Integer(), nullable=True),
        sa.Column("is_critical", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.CheckConstraint(
            "progress_method IS NULL OR progress_method IN ('DIRECT', 'DELIVERABLE_RATIO', 'APPROVED_WORK_RATIO', "
            "'CHECK_ITEM_RATIO', 'WEIGHTED_MILESTONE', 'ZERO_HUNDRED', 'FIFTY_FIFTY')",
            name=op.f("ck_activities_progress_method"),
        ),
        sa.CheckConstraint(
            "(is_milestone AND duration_days = 0) OR (NOT is_milestone AND duration_days >= 1)",
            name=op.f("ck_activities_milestone_duration"),
        ),
        sa.CheckConstraint("estimate_hours IS NULL OR estimate_hours >= 0", name=op.f("ck_activities_estimate_hours")),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name=op.f("fk_activities_project_id_projects")),
        sa.ForeignKeyConstraint(["work_package_id"], ["wbs_elements.id"], name=op.f("fk_activities_work_package_id_wbs_elements")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_activities"))
    )
    with op.batch_alter_table("activities", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_activities_project_id"), ["project_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_activities_work_package_id"), ["work_package_id"], unique=False)

    op.create_table(
        "activity_dependencies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("predecessor_id", sa.Integer(), nullable=False),
        sa.Column("successor_id", sa.Integer(), nullable=False),
        sa.Column("dep_type", sa.String(length=8), server_default=sa.text("'FS'"), nullable=False),
        sa.Column("lag_days", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.CheckConstraint("dep_type IN ('FS')", name=op.f("ck_activity_dependencies_dep_type")),
        sa.CheckConstraint("lag_days = 0", name=op.f("ck_activity_dependencies_lag_days")),
        sa.CheckConstraint("predecessor_id <> successor_id", name=op.f("ck_activity_dependencies_not_self")),
        sa.ForeignKeyConstraint(["predecessor_id"], ["activities.id"], name=op.f("fk_activity_dependencies_predecessor_id_activities")),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name=op.f("fk_activity_dependencies_project_id_projects")),
        sa.ForeignKeyConstraint(["successor_id"], ["activities.id"], name=op.f("fk_activity_dependencies_successor_id_activities")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_activity_dependencies")),
        sa.UniqueConstraint("predecessor_id", "successor_id", name=op.f("uq_activity_dependencies_predecessor_id"))
    )
    with op.batch_alter_table("activity_dependencies", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_activity_dependencies_project_id"), ["project_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_activity_dependencies_successor_id"), ["successor_id"], unique=False)

    op.create_table(
        "schedule_baseline_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("schedule_baseline_id", sa.Integer(), nullable=False),
        sa.Column("item_type", sa.String(length=16), nullable=False),
        sa.Column("activity_id", sa.Integer(), nullable=True),
        sa.Column("element_id", sa.Integer(), nullable=True),
        sa.Column("wbs_code", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("finish_date", sa.Date(), nullable=True),
        sa.Column("duration_days", sa.Integer(), nullable=True),
        sa.Column("is_milestone", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("estimate_hours", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.CheckConstraint(
            "item_type = 'ACTIVITY' OR activity_id IS NULL",
            name=op.f("ck_schedule_baseline_items_work_package_without_activity"),
        ),
        sa.CheckConstraint("item_type IN ('ACTIVITY', 'WORK_PACKAGE')", name=op.f("ck_schedule_baseline_items_item_type")),
        sa.CheckConstraint("duration_days IS NULL OR duration_days >= 0", name=op.f("ck_schedule_baseline_items_duration_days")),
        sa.CheckConstraint("estimate_hours IS NULL OR estimate_hours >= 0", name=op.f("ck_schedule_baseline_items_estimate_hours")),
        sa.ForeignKeyConstraint(["activity_id"], ["activities.id"], name=op.f("fk_schedule_baseline_items_activity_id_activities")),
        sa.ForeignKeyConstraint(["element_id"], ["wbs_elements.id"], name=op.f("fk_schedule_baseline_items_element_id_wbs_elements")),
        sa.ForeignKeyConstraint(
            ["schedule_baseline_id"],
            ["schedule_baselines.id"],
            name=op.f("fk_schedule_baseline_items_schedule_baseline_id_schedule_baselines"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_schedule_baseline_items"))
    )
    with op.batch_alter_table("schedule_baseline_items", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_schedule_baseline_items_activity_id"), ["activity_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_schedule_baseline_items_element_id"), ["element_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_schedule_baseline_items_schedule_baseline_id"), ["schedule_baseline_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("schedule_baseline_items", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_schedule_baseline_items_schedule_baseline_id"))
        batch_op.drop_index(batch_op.f("ix_schedule_baseline_items_element_id"))
        batch_op.drop_index(batch_op.f("ix_schedule_baseline_items_activity_id"))

    op.drop_table("schedule_baseline_items")
    with op.batch_alter_table("activity_dependencies", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_activity_dependencies_successor_id"))
        batch_op.drop_index(batch_op.f("ix_activity_dependencies_project_id"))

    op.drop_table("activity_dependencies")
    with op.batch_alter_table("activities", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_activities_work_package_id"))
        batch_op.drop_index(batch_op.f("ix_activities_project_id"))

    op.drop_table("activities")
    with op.batch_alter_table("schedule_baselines", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_schedule_baselines_change_request_id"))

    op.drop_table("schedule_baselines")
