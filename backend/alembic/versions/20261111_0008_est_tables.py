"""見積（EST）のテーブル：estimate_records（設計書 4.1）

Revision ID: 0008_est
Revises: 0007_chg
Create Date: 2026-11-11 12:30:00.000000+00:00

- 時刻の列はモデルでは ``app.models.types.UTCDateTime``。移行は app のコードを import せず ``DateTime(timezone=True)`` で書く（0001 と同じ）
- 対象の種類・入力の単位・手法（4.1）は DB の検査でも拒む。見積の値・入力の値は 0 以上（EST-01 の「負の値は入力できない」）
- ``target_id`` は対象の種類で指す表が変わるので外部キーを持たない（``approval_requests.target_id``・``audit_logs.entity_id`` と同じ）
- 外部キーに ``ON DELETE`` を付けない（docs/decisions/ASSUMPTIONS.md A-11・A-15・A-19・A-22・A-27・A-31）
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_est"
down_revision: str | Sequence[str] | None = "0007_chg"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "estimate_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("target_type", sa.String(length=16), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("hours", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("input_value", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("input_unit", sa.String(length=8), nullable=False),
        sa.Column("method", sa.String(length=16), nullable=True),
        sa.Column("assumptions", sa.Text(), nullable=True),
        sa.Column("references", sa.Text(), nullable=True),
        sa.Column("entered_by", sa.Integer(), nullable=False),
        sa.Column("entered_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("target_type IN ('ELEMENT', 'ACTIVITY')", name=op.f("ck_estimate_records_target_type")),
        sa.CheckConstraint("input_unit IN ('HOUR', 'DAY')", name=op.f("ck_estimate_records_input_unit")),
        sa.CheckConstraint(
            "method IS NULL OR method IN ('BOTTOM_UP', 'ANALOGOUS', 'THREE_POINT', 'EXPERT', 'OTHER')",
            name=op.f("ck_estimate_records_method"),
        ),
        sa.CheckConstraint("hours >= 0", name=op.f("ck_estimate_records_hours")),
        sa.CheckConstraint("input_value >= 0", name=op.f("ck_estimate_records_input_value")),
        sa.ForeignKeyConstraint(["entered_by"], ["users.id"], name=op.f("fk_estimate_records_entered_by_users")),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name=op.f("fk_estimate_records_project_id_projects")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_estimate_records")),
    )
    with op.batch_alter_table("estimate_records", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_estimate_records_entered_by"), ["entered_by"], unique=False)
        batch_op.create_index(
            "ix_estimate_records_project_id_target", ["project_id", "target_type", "target_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("estimate_records", schema=None) as batch_op:
        batch_op.drop_index("ix_estimate_records_project_id_target")
        batch_op.drop_index(batch_op.f("ix_estimate_records_entered_by"))

    op.drop_table("estimate_records")
