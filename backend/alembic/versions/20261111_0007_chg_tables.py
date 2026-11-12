"""変更管理（CHG）のテーブル：change_requests・corrective_actions・audit_logs（設計書 4.1）

Revision ID: 0007_chg
Revises: 0006_eff
Create Date: 2026-11-11 11:51:06.819512+00:00

- 時刻の列はモデルでは ``app.models.types.UTCDateTime``。移行は app のコードを import せず ``DateTime(timezone=True)`` で書く（0001 と同じ）
- 変更要求の状態（5.10）・是正措置の状態（4.1）・変更履歴の操作（4.1）は DB の検査でも拒む。変更要求の番号はプロジェクトの中で一意（1 以上）
- 先に作った表の列へ外部キーを張る（docs/decisions/ASSUMPTIONS.md A-15・A-19・A-27 の申し送り）：
  ``scope_baselines.change_request_id``・``schedule_baselines.change_request_id`` → ``change_requests.id``、
  ``variance_notes.corrective_action_id`` → ``corrective_actions.id``。SQLite では表の作り直し（batch）になる
- 外部キーに ``ON DELETE`` を付けない（A-11・A-15・A-19・A-22・A-27）
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_chg"
down_revision: str | Sequence[str] | None = "0006_eff"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "change_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("target_codes", sa.JSON(), nullable=False),
        sa.Column("operations", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'DRAFT'"), nullable=False),
        sa.Column("impact", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_by", sa.Integer(), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'SUBMITTED', 'APPROVED', 'REJECTED', 'APPLIED')", name=op.f("ck_change_requests_status")
        ),
        sa.CheckConstraint("number >= 1", name=op.f("ck_change_requests_number")),
        sa.ForeignKeyConstraint(["applied_by"], ["users.id"], name=op.f("fk_change_requests_applied_by_users")),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name=op.f("fk_change_requests_created_by_users")),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name=op.f("fk_change_requests_project_id_projects")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_change_requests")),
        sa.UniqueConstraint("project_id", "number", name=op.f("uq_change_requests_project_id")),
    )

    op.create_table(
        "corrective_actions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("element_id", sa.Integer(), nullable=True),
        sa.Column("activity_id", sa.Integer(), nullable=True),
        sa.Column("status_report_id", sa.Integer(), nullable=True),
        sa.Column("cause", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'NOT_STARTED'"), nullable=False),
        sa.Column("effect", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('NOT_STARTED', 'IN_PROGRESS', 'DONE')", name=op.f("ck_corrective_actions_status")
        ),
        sa.ForeignKeyConstraint(
            ["activity_id"], ["activities.id"], name=op.f("fk_corrective_actions_activity_id_activities")
        ),
        sa.ForeignKeyConstraint(
            ["element_id"], ["wbs_elements.id"], name=op.f("fk_corrective_actions_element_id_wbs_elements")
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], name=op.f("fk_corrective_actions_owner_user_id_users")),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name=op.f("fk_corrective_actions_project_id_projects")),
        sa.ForeignKeyConstraint(
            ["status_report_id"],
            ["status_reports.id"],
            name=op.f("fk_corrective_actions_status_report_id_status_reports"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_corrective_actions")),
    )
    with op.batch_alter_table("corrective_actions", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_corrective_actions_activity_id"), ["activity_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_corrective_actions_element_id"), ["element_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_corrective_actions_project_id"), ["project_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_corrective_actions_status_report_id"), ["status_report_id"], unique=False)

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("wbs_code", sa.String(length=100), nullable=True),
        sa.Column("action", sa.String(length=8), nullable=False),
        sa.Column("before", sa.JSON(), nullable=True),
        sa.Column("after", sa.JSON(), nullable=True),
        sa.CheckConstraint("action IN ('CREATE', 'UPDATE', 'DELETE')", name=op.f("ck_audit_logs_action")),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name=op.f("fk_audit_logs_project_id_projects")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_audit_logs_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    with op.batch_alter_table("audit_logs", schema=None) as batch_op:
        batch_op.create_index("ix_audit_logs_entity_type_entity_id", ["entity_type", "entity_id"], unique=False)
        batch_op.create_index("ix_audit_logs_project_id_occurred_at", ["project_id", "occurred_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_audit_logs_user_id"), ["user_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_audit_logs_wbs_code"), ["wbs_code"], unique=False)

    # 先に作った表の列へ外部キーを張る（A-15・A-19・A-27）
    with op.batch_alter_table("scope_baselines", schema=None) as batch_op:
        batch_op.create_foreign_key(
            batch_op.f("fk_scope_baselines_change_request_id_change_requests"),
            "change_requests",
            ["change_request_id"],
            ["id"],
        )
    with op.batch_alter_table("schedule_baselines", schema=None) as batch_op:
        batch_op.create_foreign_key(
            batch_op.f("fk_schedule_baselines_change_request_id_change_requests"),
            "change_requests",
            ["change_request_id"],
            ["id"],
        )
    with op.batch_alter_table("variance_notes", schema=None) as batch_op:
        batch_op.create_foreign_key(
            batch_op.f("fk_variance_notes_corrective_action_id_corrective_actions"),
            "corrective_actions",
            ["corrective_action_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("variance_notes", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_variance_notes_corrective_action_id_corrective_actions"), type_="foreignkey"
        )
    with op.batch_alter_table("schedule_baselines", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_schedule_baselines_change_request_id_change_requests"), type_="foreignkey"
        )
    with op.batch_alter_table("scope_baselines", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("fk_scope_baselines_change_request_id_change_requests"), type_="foreignkey")

    with op.batch_alter_table("audit_logs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_audit_logs_wbs_code"))
        batch_op.drop_index(batch_op.f("ix_audit_logs_user_id"))
        batch_op.drop_index("ix_audit_logs_project_id_occurred_at")
        batch_op.drop_index("ix_audit_logs_entity_type_entity_id")
    op.drop_table("audit_logs")

    with op.batch_alter_table("corrective_actions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_corrective_actions_status_report_id"))
        batch_op.drop_index(batch_op.f("ix_corrective_actions_project_id"))
        batch_op.drop_index(batch_op.f("ix_corrective_actions_element_id"))
        batch_op.drop_index(batch_op.f("ix_corrective_actions_activity_id"))
    op.drop_table("corrective_actions")
    op.drop_table("change_requests")
