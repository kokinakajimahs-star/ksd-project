"""WBS のテーブル：wbs_elements・wbs_numbering_rules・wbs_code_history・wbs_dictionary_entries・wbs_scope_links・
wbs_reviews・wbs_review_comments・approval_requests・scope_baselines・scope_baseline_elements（設計書 4.1）

Revision ID: 0003_wbs
Revises: 0002_prj
Create Date: 2026-11-10 11:22:34.671914+00:00

- 時刻の列はモデルでは ``app.models.types.UTCDateTime``。移行は app のコードを import せず ``DateTime(timezone=True)`` で書く（0001 と同じ）
- 既定値は DB の既定値にも置く：``wbs_elements.last_child_seq``（0）・``needs_renumber``（偽）、採番規則の 4 項目（``.``・1・空・真）、
  レビュー・指摘の状態（OPEN）、承認申請の状態（PENDING）
- ``scope_baselines.change_request_id`` の ``change_requests.id`` への外部キーは、``change_requests``（DB-CHG-001）を作る移行で張る
  （docs/decisions/ASSUMPTIONS.md A-15）
- 外部キーに ``ON DELETE`` を付けない（A-11・A-15）
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_wbs"
down_revision: str | Sequence[str] | None = "0002_prj"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "approval_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("target_type", sa.String(length=16), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'PENDING'"), nullable=False),
        sa.Column("requested_by", sa.Integer(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_by", sa.Integer(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.CheckConstraint("status IN ('PENDING', 'APPROVED', 'REJECTED')", name=op.f("ck_approval_requests_status")),
        sa.CheckConstraint("target_type IN ('WBS', 'CHANGE_REQUEST')", name=op.f("ck_approval_requests_target_type")),
        sa.ForeignKeyConstraint(["decided_by"], ["users.id"], name=op.f("fk_approval_requests_decided_by_users")),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name=op.f("fk_approval_requests_project_id_projects")),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], name=op.f("fk_approval_requests_requested_by_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval_requests"))
    )
    with op.batch_alter_table("approval_requests", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_approval_requests_project_id"), ["project_id"], unique=False)

    op.create_table(
        "wbs_elements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("wbs_code", sa.String(length=100), nullable=False),
        sa.Column("sibling_seq", sa.Integer(), nullable=False),
        sa.Column("last_child_seq", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("own_estimate_hours", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("responsible_user_id", sa.Integer(), nullable=True),
        sa.Column("responsible_team", sa.String(length=200), nullable=True),
        sa.Column("needs_renumber", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.CheckConstraint("kind IN ('DELIVERABLE', 'MANAGEMENT', 'COMPONENT', 'WORK_PACKAGE')", name=op.f("ck_wbs_elements_kind")),
        sa.CheckConstraint("last_child_seq >= 0", name=op.f("ck_wbs_elements_last_child_seq")),
        sa.CheckConstraint("own_estimate_hours IS NULL OR own_estimate_hours >= 0", name=op.f("ck_wbs_elements_own_estimate_hours")),
        sa.CheckConstraint("sibling_seq >= 1", name=op.f("ck_wbs_elements_sibling_seq")),
        sa.ForeignKeyConstraint(["parent_id"], ["wbs_elements.id"], name=op.f("fk_wbs_elements_parent_id_wbs_elements")),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name=op.f("fk_wbs_elements_project_id_projects")),
        sa.ForeignKeyConstraint(["responsible_user_id"], ["users.id"], name=op.f("fk_wbs_elements_responsible_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wbs_elements"))
    )
    with op.batch_alter_table("wbs_elements", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_wbs_elements_parent_id"), ["parent_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_wbs_elements_project_id"), ["project_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_wbs_elements_responsible_user_id"), ["responsible_user_id"], unique=False)

    op.create_table(
        "wbs_numbering_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("separator", sa.String(length=8), server_default=sa.text("'.'"), nullable=False),
        sa.Column("min_digits", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("prefix", sa.String(length=20), server_default=sa.text("''"), nullable=False),
        sa.Column("pad_first_level", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.CheckConstraint("min_digits >= 1", name=op.f("ck_wbs_numbering_rules_min_digits")),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name=op.f("fk_wbs_numbering_rules_project_id_projects")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wbs_numbering_rules")),
        sa.UniqueConstraint("project_id", name=op.f("uq_wbs_numbering_rules_project_id"))
    )
    op.create_table(
        "wbs_reviews",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'OPEN'"), nullable=False),
        sa.Column("requested_by", sa.Integer(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewer_ids", sa.JSON(), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('OPEN', 'CLOSED')", name=op.f("ck_wbs_reviews_status")),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name=op.f("fk_wbs_reviews_project_id_projects")),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], name=op.f("fk_wbs_reviews_requested_by_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wbs_reviews"))
    )
    with op.batch_alter_table("wbs_reviews", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_wbs_reviews_project_id"), ["project_id"], unique=False)

    op.create_table(
        "scope_baselines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("approval_request_id", sa.Integer(), nullable=True),
        sa.Column("change_request_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("reason IN ('INITIAL_APPROVAL', 'RE_APPROVAL', 'CHANGE_REQUEST')", name=op.f("ck_scope_baselines_reason")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_scope_baselines_version")),
        sa.ForeignKeyConstraint(["approval_request_id"], ["approval_requests.id"], name=op.f("fk_scope_baselines_approval_request_id_approval_requests")),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name=op.f("fk_scope_baselines_created_by_users")),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name=op.f("fk_scope_baselines_project_id_projects")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scope_baselines")),
        sa.UniqueConstraint("project_id", "version", name=op.f("uq_scope_baselines_project_id"))
    )
    with op.batch_alter_table("scope_baselines", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_scope_baselines_approval_request_id"), ["approval_request_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_scope_baselines_change_request_id"), ["change_request_id"], unique=False)

    op.create_table(
        "wbs_code_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("element_id", sa.Integer(), nullable=False),
        sa.Column("old_code", sa.String(length=100), nullable=False),
        sa.Column("new_code", sa.String(length=100), nullable=False),
        sa.Column("renumbered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("renumbered_by", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["element_id"], ["wbs_elements.id"], name=op.f("fk_wbs_code_history_element_id_wbs_elements")),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name=op.f("fk_wbs_code_history_project_id_projects")),
        sa.ForeignKeyConstraint(["renumbered_by"], ["users.id"], name=op.f("fk_wbs_code_history_renumbered_by_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wbs_code_history"))
    )
    with op.batch_alter_table("wbs_code_history", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_wbs_code_history_element_id"), ["element_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_wbs_code_history_project_id"), ["project_id"], unique=False)

    op.create_table(
        "wbs_dictionary_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("element_id", sa.Integer(), nullable=False),
        sa.Column("deliverable", sa.Text(), nullable=True),
        sa.Column("work_scope", sa.Text(), nullable=True),
        sa.Column("completion_criteria", sa.Text(), nullable=True),
        sa.Column("assumptions", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["element_id"], ["wbs_elements.id"], name=op.f("fk_wbs_dictionary_entries_element_id_wbs_elements")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wbs_dictionary_entries")),
        sa.UniqueConstraint("element_id", name=op.f("uq_wbs_dictionary_entries_element_id"))
    )
    op.create_table(
        "wbs_review_comments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("review_id", sa.Integer(), nullable=False),
        sa.Column("element_id", sa.Integer(), nullable=True),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'OPEN'"), nullable=False),
        sa.Column("author_id", sa.Integer(), nullable=False),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("category IN ('OMISSION', 'DUPLICATION', 'AMBIGUITY', 'OTHER')", name=op.f("ck_wbs_review_comments_category")),
        sa.CheckConstraint("status IN ('OPEN', 'RESOLVED', 'REJECTED')", name=op.f("ck_wbs_review_comments_status")),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], name=op.f("fk_wbs_review_comments_author_id_users")),
        sa.ForeignKeyConstraint(["element_id"], ["wbs_elements.id"], name=op.f("fk_wbs_review_comments_element_id_wbs_elements")),
        sa.ForeignKeyConstraint(["review_id"], ["wbs_reviews.id"], name=op.f("fk_wbs_review_comments_review_id_wbs_reviews")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wbs_review_comments"))
    )
    with op.batch_alter_table("wbs_review_comments", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_wbs_review_comments_element_id"), ["element_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_wbs_review_comments_review_id"), ["review_id"], unique=False)

    op.create_table(
        "wbs_scope_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("element_id", sa.Integer(), nullable=False),
        sa.Column("scope_item_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["element_id"], ["wbs_elements.id"], name=op.f("fk_wbs_scope_links_element_id_wbs_elements")),
        sa.ForeignKeyConstraint(["scope_item_id"], ["scope_items.id"], name=op.f("fk_wbs_scope_links_scope_item_id_scope_items")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wbs_scope_links")),
        sa.UniqueConstraint("element_id", "scope_item_id", name=op.f("uq_wbs_scope_links_element_id"))
    )
    with op.batch_alter_table("wbs_scope_links", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_wbs_scope_links_scope_item_id"), ["scope_item_id"], unique=False)

    op.create_table(
        "scope_baseline_elements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("scope_baseline_id", sa.Integer(), nullable=False),
        sa.Column("element_id", sa.Integer(), nullable=False),
        sa.Column("parent_element_id", sa.Integer(), nullable=True),
        sa.Column("wbs_code", sa.String(length=100), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("own_estimate_hours", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("dictionary", sa.JSON(none_as_null=True), nullable=True),
        sa.CheckConstraint("kind IN ('DELIVERABLE', 'MANAGEMENT', 'COMPONENT', 'WORK_PACKAGE')", name=op.f("ck_scope_baseline_elements_kind")),
        sa.CheckConstraint("own_estimate_hours IS NULL OR own_estimate_hours >= 0", name=op.f("ck_scope_baseline_elements_own_estimate_hours")),
        sa.ForeignKeyConstraint(["element_id"], ["wbs_elements.id"], name=op.f("fk_scope_baseline_elements_element_id_wbs_elements")),
        sa.ForeignKeyConstraint(["parent_element_id"], ["wbs_elements.id"], name=op.f("fk_scope_baseline_elements_parent_element_id_wbs_elements")),
        sa.ForeignKeyConstraint(["scope_baseline_id"], ["scope_baselines.id"], name=op.f("fk_scope_baseline_elements_scope_baseline_id_scope_baselines")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scope_baseline_elements"))
    )
    with op.batch_alter_table("scope_baseline_elements", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_scope_baseline_elements_element_id"), ["element_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_scope_baseline_elements_parent_element_id"), ["parent_element_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_scope_baseline_elements_scope_baseline_id"), ["scope_baseline_id"], unique=False)



def downgrade() -> None:
    with op.batch_alter_table("scope_baseline_elements", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_scope_baseline_elements_scope_baseline_id"))
        batch_op.drop_index(batch_op.f("ix_scope_baseline_elements_parent_element_id"))
        batch_op.drop_index(batch_op.f("ix_scope_baseline_elements_element_id"))

    op.drop_table("scope_baseline_elements")
    with op.batch_alter_table("wbs_scope_links", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_wbs_scope_links_scope_item_id"))

    op.drop_table("wbs_scope_links")
    with op.batch_alter_table("wbs_review_comments", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_wbs_review_comments_review_id"))
        batch_op.drop_index(batch_op.f("ix_wbs_review_comments_element_id"))

    op.drop_table("wbs_review_comments")
    op.drop_table("wbs_dictionary_entries")
    with op.batch_alter_table("wbs_code_history", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_wbs_code_history_project_id"))
        batch_op.drop_index(batch_op.f("ix_wbs_code_history_element_id"))

    op.drop_table("wbs_code_history")
    with op.batch_alter_table("scope_baselines", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_scope_baselines_change_request_id"))
        batch_op.drop_index(batch_op.f("ix_scope_baselines_approval_request_id"))

    op.drop_table("scope_baselines")
    with op.batch_alter_table("wbs_reviews", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_wbs_reviews_project_id"))

    op.drop_table("wbs_reviews")
    op.drop_table("wbs_numbering_rules")
    with op.batch_alter_table("wbs_elements", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_wbs_elements_responsible_user_id"))
        batch_op.drop_index(batch_op.f("ix_wbs_elements_project_id"))
        batch_op.drop_index(batch_op.f("ix_wbs_elements_parent_id"))

    op.drop_table("wbs_elements")
    with op.batch_alter_table("approval_requests", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_approval_requests_project_id"))

    op.drop_table("approval_requests")
