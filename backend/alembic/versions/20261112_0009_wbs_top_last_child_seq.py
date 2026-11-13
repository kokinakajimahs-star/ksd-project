"""第 1 階層の順番の数え：wbs_numbering_rules.top_last_child_seq（設計書 1.1 の 4.1・5.2。CR-003）

Revision ID: 0009_wbs_top_seq
Revises: 0008_est
Create Date: 2026-11-12 10:00:00.000000+00:00

- 既定は 0（DB の既定値にも置く）。0 以上は DB の検査でも拒む（``wbs_elements.last_child_seq`` と同じ）
- 既にある行は、そのプロジェクトの第 1 階層の要素の ``sibling_seq`` の最大で埋める（この版までの数え方。A-17。
  既に消した第 1 階層の番号は分からないので埋めない）
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_wbs_top_seq"
down_revision: str | Sequence[str] | None = "0008_est"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("wbs_numbering_rules", schema=None) as batch_op:
        batch_op.add_column(sa.Column("top_last_child_seq", sa.Integer(), server_default=sa.text("0"), nullable=False))
        batch_op.create_check_constraint(
            batch_op.f("ck_wbs_numbering_rules_top_last_child_seq"), "top_last_child_seq >= 0"
        )
    op.execute(
        "UPDATE wbs_numbering_rules SET top_last_child_seq = COALESCE(("
        "SELECT MAX(wbs_elements.sibling_seq) FROM wbs_elements "
        "WHERE wbs_elements.project_id = wbs_numbering_rules.project_id AND wbs_elements.parent_id IS NULL"
        "), 0)"
    )


def downgrade() -> None:
    with op.batch_alter_table("wbs_numbering_rules", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("ck_wbs_numbering_rules_top_last_child_seq"), type_="check")
        batch_op.drop_column("top_last_child_seq")
