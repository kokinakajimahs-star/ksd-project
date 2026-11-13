"""DB の整合：内部キーを再利用しない・状態の組の検査・JSON の null・長い制約名・採番規則の行（設計書 1.2 の 4.2。CR-012・A-49 の 3・5・6・8）

Revision ID: 0012_db_integrity
Revises: 0011_users_auth
Create Date: 2026-11-13 12:00:00.000000+00:00

- 内部キーを再利用しない（CR-012。4.2）：SQLite では、0010 で作り直した ``wbs_elements``・``activities`` を除く全ての表を
  ``AUTOINCREMENT`` で作り直す（0010 と同じ方法）。行と ``id`` はそのまま写す。作り直した表の ``sqlite_sequence`` は写した最大の
  ``id`` になる（前に消した、それより大きい ``id`` は分からないので数えない）。``wbs_elements``・``activities`` は作り直さないので
  ``sqlite_sequence`` もそのまま。外部キーの強制を外した接続（``app.infra.db.create_migration_engine``）で流す
  （作り直しの DROP が子の行を連鎖で消さない・違反で止まらないため。流し終わりに env.py が違反の無いことを確かめる）。
  PostgreSQL の連番（sequence・identity）は元から再利用しないので何もしない
- 状態と決めた人・時刻の組の検査（A-49 の 6。5.10 の状態と遷移から。``status_reports.finalized_pair`` と同じ形）：
  ``approval_requests.decided_pair``・``change_requests.submitted_at``・``change_requests.applied_pair``。
  今の行が反していれば、表と行の数を示して止める（直し方は人が決める）
- JSON の null（A-49 の 5）：NULL を許す JSON の列（``audit_logs.before``・``after``、``change_requests.impact``、
  ``scope_baseline_elements.dictionary``）にある JSON の ``null`` を SQL の NULL にする。NOT NULL の JSON の列に JSON の ``null`` があれば
  NULL にできないので、列と行の数を示して止める
- 63 文字を超える制約名（A-49 の 8）：``schedule_baseline_items.schedule_baseline_id`` の外部キーを
  ``fk_schedule_baseline_items_schedule_baseline_id`` に付け直す。元の名前（66 文字）は、PostgreSQL では作ったとき（0004 の ``op.f``）に
  SQLAlchemy が上限 63 文字に収まる名前（先頭 55 文字＋``_``＋名前のハッシュ 4 文字）に縮めて付けている。落とすときも同じ ``op.f`` の
  名前を渡すので、同じ縮め方で同じ名前になる。SQLite は名前を縮めない
- 採番規則の行の補い（A-49 の 3）：行の無いプロジェクトに既定の行を作る（ほかの列は DB の既定値）。``top_last_child_seq`` は
  そのプロジェクトの第 1 階層の要素の ``sibling_seq`` の最大（0009 と同じ埋め方。要素が無ければ 0）
- 戻すとき：検査を外し、外部キーを元の名前に付け直し、SQLite では作り直した表を ``AUTOINCREMENT`` 無しで作り直す（行と ``id`` は残る。
  それらの表の ``sqlite_sequence`` の行は消え、消した最大の ``id`` を使い直しうる 0011 の振る舞いに戻る）。
  JSON の ``null`` を NULL にしたことと、補った採番規則の行は戻さない：0011 の版のモデルも NULL と JSON の ``null`` をどちらも
  ``None`` で読み、補った行は 0011 の版のサービスが初めて読んだときに作る行と同じ値なので、残しても 0011 の版の読む値は変わらない
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_db_integrity"
down_revision: str | Sequence[str] | None = "0011_users_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# SQLite で AUTOINCREMENT で作り直す表（wbs_elements・activities は 0010 で済み）。親の表から順に並べる
AUTOINCREMENT_TABLES: tuple[str, ...] = (
    "users",
    "auth_sessions",
    "projects",
    "approval_requests",
    "audit_logs",
    "calendars",
    "change_requests",
    "estimate_records",
    "project_members",
    "role_permissions",
    "scope_items",
    "share_links",
    "status_reports",
    "wbs_numbering_rules",
    "wbs_reviews",
    "calendar_holidays",
    "progress_snapshots",
    "schedule_baselines",
    "scope_baselines",
    "wbs_code_history",
    "wbs_dictionary_entries",
    "wbs_review_comments",
    "wbs_scope_links",
    "activity_dependencies",
    "assignments",
    "corrective_actions",
    "progress_entries",
    "progress_items",
    "progress_snapshot_items",
    "schedule_baseline_items",
    "scope_baseline_elements",
    "time_entries",
    "variance_notes",
)

# 状態と決めた人・時刻の組の検査：(表, 制約の名前, 式)。式はモデル（app.models.wbs・chg）と同じ文字列
STATE_CHECKS: tuple[tuple[str, str, str], ...] = (
    (
        "approval_requests",
        "ck_approval_requests_decided_pair",
        "(status = 'PENDING' AND decided_by IS NULL AND decided_at IS NULL)"
        " OR (status IN ('APPROVED', 'REJECTED') AND decided_by IS NOT NULL AND decided_at IS NOT NULL)",
    ),
    (
        "change_requests",
        "ck_change_requests_submitted_at",
        "(status = 'DRAFT' AND submitted_at IS NULL)"
        " OR (status IN ('SUBMITTED', 'APPROVED', 'REJECTED', 'APPLIED') AND submitted_at IS NOT NULL)",
    ),
    (
        "change_requests",
        "ck_change_requests_applied_pair",
        "(status = 'APPLIED' AND applied_by IS NOT NULL AND applied_at IS NOT NULL)"
        " OR (status IN ('DRAFT', 'SUBMITTED', 'APPROVED', 'REJECTED') AND applied_by IS NULL AND applied_at IS NULL)",
    ),
)

# JSON の列：(表, 列)
NULLABLE_JSON_COLUMNS: tuple[tuple[str, str], ...] = (
    ("audit_logs", "before"),
    ("audit_logs", "after"),
    ("change_requests", "impact"),
    ("scope_baseline_elements", "dictionary"),
)
NOT_NULL_JSON_COLUMNS: tuple[tuple[str, str], ...] = (
    ("projects", "settings"),
    ("project_members", "roles"),
    ("wbs_reviews", "reviewer_ids"),
    ("change_requests", "target_codes"),
    ("change_requests", "operations"),
)

# 付け直す外部キー：表・列・指す表と、元の名前（名前の規約の 66 文字）・新しい名前
FK_TABLE = "schedule_baseline_items"
FK_COLUMN = "schedule_baseline_id"
FK_REFERENT = "schedule_baselines"
FK_LONG_NAME = "fk_schedule_baseline_items_schedule_baseline_id_schedule_baselines"
FK_SHORT_NAME = "fk_schedule_baseline_items_schedule_baseline_id"

_DIALECTS = ("sqlite", "postgresql")


def _dialect() -> str:
    name = op.get_bind().dialect.name
    if name not in _DIALECTS:
        # JSON の null の見分け方と表の作り直しは方言ごとに書いている（設計書 1.1 の SQLite・PostgreSQL だけ）
        raise RuntimeError(f"この移行は SQLite と PostgreSQL だけを扱う（接続先は {name}）")
    return name


def _quote(column: str) -> str:
    return op.get_bind().dialect.identifier_preparer.quote(column)


def _is_json_null(dialect: str, column: str) -> str:
    """列の値が JSON の ``null`` である条件。SQLite は JSON を文字列で持つ。PostgreSQL は json・jsonb のどちらでも見分ける。"""
    if dialect == "postgresql":
        return f"json_typeof(CAST({column} AS json)) = 'null'"
    return f"{column} = 'null'"


def _count(sql: str) -> int:
    return int(op.get_bind().execute(sa.text(sql)).scalar_one())


def _refuse_rows_that_cannot_be_kept(dialect: str) -> None:
    """新しい検査に反する行・NOT NULL の JSON の列の JSON の ``null`` があれば止める（どれも直し方を人が決める）。"""
    problems = [
        f"{table} の {name} に反する行が {count} 行"
        for table, name, expression in STATE_CHECKS
        if (count := _count(f"SELECT COUNT(*) FROM {table} WHERE NOT ({expression})"))
    ]
    problems += [
        f"{table}.{column}（NOT NULL）に JSON の null が {count} 行"
        for table, column in NOT_NULL_JSON_COLUMNS
        if (count := _count(f"SELECT COUNT(*) FROM {table} WHERE {_is_json_null(dialect, _quote(column))}"))
    ]
    if problems:
        raise RuntimeError("0012 へ上げられません（今の行を直してから流す）: " + "、".join(problems))


def _refuse_enforced_foreign_keys() -> None:
    """SQLite で外部キーの強制が効いていれば止める（表の作り直しの DROP が子の行に及ぶため。env.py の移行の接続では外れている）。"""
    if op.get_bind().exec_driver_sql("PRAGMA foreign_keys").scalar() != 0:
        raise RuntimeError("SQLite の表を作り直すので、外部キーの強制を外した接続（create_migration_engine）で流す")


def upgrade() -> None:
    dialect = _dialect()
    _refuse_rows_that_cannot_be_kept(dialect)

    # JSON の null を SQL の NULL にする（A-49 の 5）
    for table, column in NULLABLE_JSON_COLUMNS:
        quoted = _quote(column)
        op.execute(f"UPDATE {table} SET {quoted} = NULL WHERE {quoted} IS NOT NULL AND {_is_json_null(dialect, quoted)}")

    # 採番規則の行の無いプロジェクトに既定の行を作る（A-49 の 3。0009 と同じ埋め方）
    op.execute(
        "INSERT INTO wbs_numbering_rules (project_id, top_last_child_seq) "
        "SELECT projects.id, COALESCE(("
        "SELECT MAX(wbs_elements.sibling_seq) FROM wbs_elements "
        "WHERE wbs_elements.project_id = projects.id AND wbs_elements.parent_id IS NULL"
        "), 0) FROM projects "
        "WHERE NOT EXISTS (SELECT 1 FROM wbs_numbering_rules WHERE wbs_numbering_rules.project_id = projects.id)"
    )

    if dialect == "sqlite":
        _refuse_enforced_foreign_keys()
        for table in AUTOINCREMENT_TABLES:
            with op.batch_alter_table(table, recreate="always", table_kwargs={"sqlite_autoincrement": True}) as batch_op:
                for t, name, expression in STATE_CHECKS:
                    if t == table:
                        batch_op.create_check_constraint(batch_op.f(name), expression)
                if table == FK_TABLE:
                    batch_op.drop_constraint(batch_op.f(FK_LONG_NAME), type_="foreignkey")
                    batch_op.create_foreign_key(batch_op.f(FK_SHORT_NAME), FK_REFERENT, [FK_COLUMN], ["id"])
        return

    # PostgreSQL：連番は元から再利用しないので表は作り直さない。検査を足し、外部キーを付け直す
    for table, name, expression in STATE_CHECKS:
        op.create_check_constraint(op.f(name), table, expression)
    op.drop_constraint(op.f(FK_LONG_NAME), FK_TABLE, type_="foreignkey")
    op.create_foreign_key(op.f(FK_SHORT_NAME), FK_TABLE, FK_REFERENT, [FK_COLUMN], ["id"])


def downgrade() -> None:
    dialect = _dialect()
    if dialect == "sqlite":
        _refuse_enforced_foreign_keys()
        for table in AUTOINCREMENT_TABLES:
            with op.batch_alter_table(table, recreate="always", table_kwargs={"sqlite_autoincrement": False}) as batch_op:
                for t, name, _expression in STATE_CHECKS:
                    if t == table:
                        batch_op.drop_constraint(batch_op.f(name), type_="check")
                if table == FK_TABLE:
                    batch_op.drop_constraint(batch_op.f(FK_SHORT_NAME), type_="foreignkey")
                    batch_op.create_foreign_key(batch_op.f(FK_LONG_NAME), FK_REFERENT, [FK_COLUMN], ["id"])
        return

    # PostgreSQL：元の名前は op.f の長い名前を渡すと、0004 が作ったときと同じ縮め方の名前になる
    op.drop_constraint(op.f(FK_SHORT_NAME), FK_TABLE, type_="foreignkey")
    op.create_foreign_key(op.f(FK_LONG_NAME), FK_TABLE, FK_REFERENT, [FK_COLUMN], ["id"])
    for table, name, _expression in STATE_CHECKS:
        op.drop_constraint(op.f(name), table, type_="check")
