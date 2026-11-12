"""写しの列の外部キーを外し、要素・活動の内部キーを再利用しない（設計書 1.1 の 4.2。CR-002）

Revision ID: 0010_copy_no_fk
Revises: 0009_wbs_top_seq
Create Date: 2026-11-12 11:00:00.000000+00:00

- ベースライン・スナップショットの写しの表の、写した時点の要素・活動を指す列（``scope_baseline_elements.element_id``・
  ``parent_element_id``、``schedule_baseline_items.activity_id``・``element_id``、``progress_snapshot_items.activity_id``）の
  外部キー制約を外す。版・スナップショットの親への外部キー制約は残す。索引は残す
- SQLite では ``wbs_elements``・``activities`` を ``AUTOINCREMENT`` で作り直す（消した行の内部キーを再利用しない）。
  ほかの DB の連番は再利用しないので作り直さない
- 戻すとき：写しが今は無い要素を指していれば戻せない（止める）。活動・WP の写しの紐付けは、今は無い行を指すものを空にしてから
  外部キー制約を張る（1.0 の振る舞い。A-19・A-22）
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_copy_no_fk"
down_revision: str | Sequence[str] | None = "0009_wbs_top_seq"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (表, 列, 指す表)
COPY_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("scope_baseline_elements", "element_id", "wbs_elements"),
    ("scope_baseline_elements", "parent_element_id", "wbs_elements"),
    ("schedule_baseline_items", "activity_id", "activities"),
    ("schedule_baseline_items", "element_id", "wbs_elements"),
    ("progress_snapshot_items", "activity_id", "activities"),
)


def _fk_name(table: str, column: str, referent: str) -> str:
    return f"fk_{table}_{column}_{referent}"


def _recreate_with_autoincrement(enabled: bool) -> None:
    if op.get_bind().dialect.name != "sqlite":
        return
    for table in ("wbs_elements", "activities"):
        with op.batch_alter_table(table, recreate="always", table_kwargs={"sqlite_autoincrement": enabled}):
            pass


def upgrade() -> None:
    tables = sorted({table for table, _, _ in COPY_COLUMNS})
    for table in tables:
        with op.batch_alter_table(table, schema=None) as batch_op:
            for t, column, referent in COPY_COLUMNS:
                if t == table:
                    batch_op.drop_constraint(batch_op.f(_fk_name(t, column, referent)), type_="foreignkey")
    _recreate_with_autoincrement(True)


def downgrade() -> None:
    bind = op.get_bind()
    dangling = bind.execute(sa.text(
        "SELECT COUNT(*) FROM scope_baseline_elements s WHERE NOT EXISTS (SELECT 1 FROM wbs_elements e WHERE e.id = s.element_id)"
        " OR (s.parent_element_id IS NOT NULL"
        " AND NOT EXISTS (SELECT 1 FROM wbs_elements e WHERE e.id = s.parent_element_id))"
    )).scalar_one()
    if dangling:
        raise RuntimeError(
            f"スコープベースラインの写し {dangling} 行が今は無い要素を指すので 0009 へ戻せません（1.0 は外部キーで消させなかった）"
        )
    _recreate_with_autoincrement(False)
    for table, column, referent in COPY_COLUMNS:
        if table == "scope_baseline_elements":
            continue
        op.execute(
            f"UPDATE {table} SET {column} = NULL WHERE {column} IS NOT NULL"
            f" AND NOT EXISTS (SELECT 1 FROM {referent} r WHERE r.id = {table}.{column})"
        )
    tables = sorted({table for table, _, _ in COPY_COLUMNS})
    for table in tables:
        with op.batch_alter_table(table, schema=None) as batch_op:
            for t, column, referent in COPY_COLUMNS:
                if t == table:
                    batch_op.create_foreign_key(batch_op.f(_fk_name(t, column, referent)), referent, [column], ["id"])
