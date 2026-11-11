"""進捗（PRG）のテーブル：status_reports・progress_entries・progress_items・progress_snapshots・progress_snapshot_items（設計書 4.1）

Revision ID: 0005_prg
Revises: 0004_sch
Create Date: 2026-11-11 03:40:23.284667+00:00

- 時刻の列はモデルでは ``app.models.types.UTCDateTime``。移行は app のコードを import せず ``DateTime(timezone=True)`` で書く（0001 と同じ）
- 既定値は DB の既定値にも置く：報告日の ``status``（OPEN）、算定項目の ``weight``（1）・``done``・``approved``（偽）、
  スナップショットの ``plan_basis``（LATEST）、スナップショットの活動の ``is_milestone``・``is_critical``（偽）
- スナップショットの活動の ``activity_id`` は空を許す（写した活動を後から消すときにサービス層が空にする。docs/decisions/ASSUMPTIONS.md A-22）
- 外部キーに ``ON DELETE`` を付けない（A-11・A-15・A-19）
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_prg"
down_revision: str | Sequence[str] | None = "0004_sch"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "status_reports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'OPEN'"), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finalized_by", sa.Integer(), nullable=True),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(status = 'OPEN' AND finalized_by IS NULL AND finalized_at IS NULL)"
            " OR (status = 'FINALIZED' AND finalized_by IS NOT NULL AND finalized_at IS NOT NULL)",
            name=op.f("ck_status_reports_finalized_pair"),
        ),
        sa.CheckConstraint("status IN ('OPEN', 'FINALIZED')", name=op.f("ck_status_reports_status")),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name=op.f("fk_status_reports_created_by_users")),
        sa.ForeignKeyConstraint(["finalized_by"], ["users.id"], name=op.f("fk_status_reports_finalized_by_users")),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name=op.f("fk_status_reports_project_id_projects")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_status_reports")),
        sa.UniqueConstraint("project_id", "report_date", name=op.f("uq_status_reports_project_id")),
    )

    op.create_table(
        "progress_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("status_report_id", sa.Integer(), nullable=False),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("report_line", sa.Integer(), nullable=False),
        sa.Column("tolerance_days", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column("extract_days", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column("plan_basis", sa.String(length=16), server_default=sa.text("'LATEST'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("plan_basis IN ('LATEST')", name=op.f("ck_progress_snapshots_plan_basis")),
        sa.CheckConstraint("extract_days >= 0", name=op.f("ck_progress_snapshots_extract_days")),
        sa.CheckConstraint("tolerance_days >= 0", name=op.f("ck_progress_snapshots_tolerance_days")),
        sa.ForeignKeyConstraint(
            ["status_report_id"],
            ["status_reports.id"],
            name=op.f("fk_progress_snapshots_status_report_id_status_reports"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_progress_snapshots")),
        sa.UniqueConstraint("status_report_id", name=op.f("uq_progress_snapshots_status_report_id")),
    )

    op.create_table(
        "progress_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("status_report_id", sa.Integer(), nullable=False),
        sa.Column("activity_id", sa.Integer(), nullable=False),
        sa.Column("percent_complete", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("actual_start", sa.Date(), nullable=True),
        sa.Column("actual_finish", sa.Date(), nullable=True),
        sa.Column("remaining_hours", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("delay_reason", sa.Text(), nullable=True),
        sa.Column("forecast_finish", sa.Date(), nullable=True),
        sa.Column("entered_by", sa.Integer(), nullable=False),
        sa.Column("entered_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "actual_finish IS NULL OR actual_start IS NULL OR actual_finish >= actual_start",
            name=op.f("ck_progress_entries_actual_dates"),
        ),
        sa.CheckConstraint(
            "percent_complete IS NULL OR (percent_complete >= 0 AND percent_complete <= 100)",
            name=op.f("ck_progress_entries_percent_complete"),
        ),
        sa.CheckConstraint(
            "remaining_hours IS NULL OR remaining_hours >= 0", name=op.f("ck_progress_entries_remaining_hours")
        ),
        sa.ForeignKeyConstraint(["activity_id"], ["activities.id"], name=op.f("fk_progress_entries_activity_id_activities")),
        sa.ForeignKeyConstraint(["entered_by"], ["users.id"], name=op.f("fk_progress_entries_entered_by_users")),
        sa.ForeignKeyConstraint(
            ["status_report_id"],
            ["status_reports.id"],
            name=op.f("fk_progress_entries_status_report_id_status_reports"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_progress_entries")),
        sa.UniqueConstraint("status_report_id", "activity_id", name=op.f("uq_progress_entries_status_report_id")),
    )
    with op.batch_alter_table("progress_entries", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_progress_entries_activity_id"), ["activity_id"], unique=False)

    op.create_table(
        "progress_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("activity_id", sa.Integer(), nullable=False),
        sa.Column("item_type", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("weight", sa.Numeric(precision=10, scale=2), server_default=sa.text("1"), nullable=False),
        sa.Column("done", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("approved", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "item_type IN ('DELIVERABLE', 'WORK', 'CHECK', 'MILESTONE')", name=op.f("ck_progress_items_item_type")
        ),
        sa.CheckConstraint("done OR done_at IS NULL", name=op.f("ck_progress_items_done_at_when_done")),
        sa.CheckConstraint("weight > 0", name=op.f("ck_progress_items_weight")),
        sa.ForeignKeyConstraint(["activity_id"], ["activities.id"], name=op.f("fk_progress_items_activity_id_activities")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_progress_items")),
    )
    with op.batch_alter_table("progress_items", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_progress_items_activity_id"), ["activity_id"], unique=False)

    op.create_table(
        "progress_snapshot_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("activity_id", sa.Integer(), nullable=True),
        sa.Column("row_order", sa.Integer(), nullable=False),
        sa.Column("wbs_code", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("finish_date", sa.Date(), nullable=True),
        sa.Column("duration_days", sa.Integer(), nullable=True),
        sa.Column("is_milestone", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("is_critical", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("percent_complete", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("actual_start", sa.Date(), nullable=True),
        sa.Column("actual_finish", sa.Date(), nullable=True),
        sa.Column("actual_hours", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("etc_hours", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("point", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("deviation", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("judgement", sa.String(length=16), nullable=False),
        sa.CheckConstraint(
            "judgement IN ('COMPLETED', 'NOT_STARTED', 'BEHIND', 'AHEAD', 'ON_SCHEDULE', 'NOT_COMPUTED')",
            name=op.f("ck_progress_snapshot_items_judgement"),
        ),
        sa.CheckConstraint(
            "actual_finish IS NULL OR actual_start IS NULL OR actual_finish >= actual_start",
            name=op.f("ck_progress_snapshot_items_actual_dates"),
        ),
        sa.CheckConstraint("actual_hours IS NULL OR actual_hours >= 0", name=op.f("ck_progress_snapshot_items_actual_hours")),
        sa.CheckConstraint(
            "duration_days IS NULL OR duration_days >= 0", name=op.f("ck_progress_snapshot_items_duration_days")
        ),
        sa.CheckConstraint("etc_hours IS NULL OR etc_hours >= 0", name=op.f("ck_progress_snapshot_items_etc_hours")),
        sa.CheckConstraint(
            "percent_complete IS NULL OR (percent_complete >= 0 AND percent_complete <= 100)",
            name=op.f("ck_progress_snapshot_items_percent_complete"),
        ),
        sa.ForeignKeyConstraint(
            ["activity_id"], ["activities.id"], name=op.f("fk_progress_snapshot_items_activity_id_activities")
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["progress_snapshots.id"],
            name=op.f("fk_progress_snapshot_items_snapshot_id_progress_snapshots"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_progress_snapshot_items")),
        sa.UniqueConstraint("snapshot_id", "row_order", name=op.f("uq_progress_snapshot_items_snapshot_id")),
    )
    with op.batch_alter_table("progress_snapshot_items", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_progress_snapshot_items_activity_id"), ["activity_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_progress_snapshot_items_snapshot_id"), ["snapshot_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("progress_snapshot_items", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_progress_snapshot_items_snapshot_id"))
        batch_op.drop_index(batch_op.f("ix_progress_snapshot_items_activity_id"))

    op.drop_table("progress_snapshot_items")
    with op.batch_alter_table("progress_items", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_progress_items_activity_id"))

    op.drop_table("progress_items")
    with op.batch_alter_table("progress_entries", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_progress_entries_activity_id"))

    op.drop_table("progress_entries")
    op.drop_table("progress_snapshots")
    op.drop_table("status_reports")
