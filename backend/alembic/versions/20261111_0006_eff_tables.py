"""工数（EFF）のテーブル：assignments・time_entries・variance_notes（設計書 4.1）

Revision ID: 0006_eff
Revises: 0005_prg
Create Date: 2026-11-11 10:37:08.502017+00:00

- 時刻の列はモデルでは ``app.models.types.UTCDateTime``。移行は app のコードを import せず ``DateTime(timezone=True)`` で書く（0001 と同じ）
- 割当工数・実績工数は 0 以上、実績工数は 1 行 24 時間以下（EFF-02・RULE-EFF-005 の必要条件）を DB の検査でも拒む
- ``variance_notes.corrective_action_id`` の ``corrective_actions.id`` への外部キーは、``corrective_actions``（DB-CHG-002）を作る移行で張る
  （``scope_baselines.change_request_id`` と同じ。docs/decisions/ASSUMPTIONS.md A-15・A-19・A-27）
- 外部キーに ``ON DELETE`` を付けない（A-11・A-15・A-19・A-22）
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_eff"
down_revision: str | Sequence[str] | None = "0005_prg"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "assignments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("activity_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("planned_hours", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.CheckConstraint("planned_hours IS NULL OR planned_hours >= 0", name=op.f("ck_assignments_planned_hours")),
        sa.ForeignKeyConstraint(["activity_id"], ["activities.id"], name=op.f("fk_assignments_activity_id_activities")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_assignments_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assignments")),
        sa.UniqueConstraint("activity_id", "user_id", name=op.f("uq_assignments_activity_id")),
    )
    with op.batch_alter_table("assignments", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_assignments_user_id"), ["user_id"], unique=False)

    op.create_table(
        "time_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("activity_id", sa.Integer(), nullable=False),
        sa.Column("work_date", sa.Date(), nullable=False),
        sa.Column("hours", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.CheckConstraint("hours >= 0 AND hours <= 24", name=op.f("ck_time_entries_hours")),
        sa.ForeignKeyConstraint(["activity_id"], ["activities.id"], name=op.f("fk_time_entries_activity_id_activities")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_time_entries_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_time_entries")),
        sa.UniqueConstraint("user_id", "activity_id", "work_date", name=op.f("uq_time_entries_user_id")),
    )
    with op.batch_alter_table("time_entries", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_time_entries_activity_id"), ["activity_id"], unique=False)

    op.create_table(
        "variance_notes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("element_id", sa.Integer(), nullable=False),
        sa.Column("status_report_id", sa.Integer(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("corrective_action_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name=op.f("fk_variance_notes_created_by_users")),
        sa.ForeignKeyConstraint(
            ["element_id"], ["wbs_elements.id"], name=op.f("fk_variance_notes_element_id_wbs_elements")
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name=op.f("fk_variance_notes_project_id_projects")),
        sa.ForeignKeyConstraint(
            ["status_report_id"],
            ["status_reports.id"],
            name=op.f("fk_variance_notes_status_report_id_status_reports"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_variance_notes")),
    )
    with op.batch_alter_table("variance_notes", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_variance_notes_corrective_action_id"), ["corrective_action_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_variance_notes_element_id"), ["element_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_variance_notes_status_report_id"), ["status_report_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("variance_notes", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_variance_notes_status_report_id"))
        batch_op.drop_index(batch_op.f("ix_variance_notes_element_id"))
        batch_op.drop_index(batch_op.f("ix_variance_notes_corrective_action_id"))

    op.drop_table("variance_notes")
    with op.batch_alter_table("time_entries", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_time_entries_activity_id"))

    op.drop_table("time_entries")
    with op.batch_alter_table("assignments", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_assignments_user_id"))

    op.drop_table("assignments")
