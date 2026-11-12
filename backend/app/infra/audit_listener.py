"""変更履歴の記録（設計書 8 章 NFR-05・6.1、4.1 の audit_logs、5.1 の RULE-CHG-006）。

セッションの生成器に ``after_flush`` のイベントを載せ、ORM で追加・更新・削除した全てのモデル（変更履歴のテーブル自身は除く）を、
RULE-CHG-006（``make_audit_diff``）の差分にして ``audit_logs`` へ追記する。``after_flush`` では ID が振られ、属性の変更前の値も
まだ読める（SQLAlchemy の仕様）。追記は同じトランザクションの中で Core の INSERT で行う（コミットしなければ履歴も残らない）。

- 変更者・時刻：要求ごとに ``bind_actor``（サービス層の ``app.services.auth.bind_actor`` が呼ぶ）でセッションに載せた値。
  載っていなければ変更者は空、時刻はその時の UTC
- プロジェクト・WBS ID：行の ``project_id``（プロジェクト自身は ``id``）、無ければ親の行（要素・活動・報告日・カレンダー・
  レビュー・版）からたどる。WBS ID は要素ならその ID、要素・活動・WP を指す行ならその要素（活動は WP）の ID
- **限界**：``session.execute(update(...))``・``delete(...)`` のような一括の文は ORM の単位作業を通らないので記録しない
  （docs/decisions/ASSUMPTIONS.md A-37）
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Connection, Table, insert, inspect, select
from sqlalchemy.orm import Session, sessionmaker

from app.domain.audit import make_audit_diff
from app.infra.db import Base
from app.models.chg import AuditLog
from app.models.types import utc_now

AUDIT_USER_KEY = "pmt_audit_user_id"
AUDIT_NOW_KEY = "pmt_audit_now"

# 親の行をたどる列 → 親の表（project_id を持つか、さらにたどれる表）
_PARENT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("element_id", "wbs_elements"),
    ("work_package_id", "wbs_elements"),
    ("activity_id", "activities"),
    ("status_report_id", "status_reports"),
    ("calendar_id", "calendars"),
    ("review_id", "wbs_reviews"),
    ("scope_baseline_id", "scope_baselines"),
    ("schedule_baseline_id", "schedule_baselines"),
    ("snapshot_id", "progress_snapshots"),
)


def bind_actor(session: Session, user_id: int | None, now: datetime | None) -> None:
    """このセッションで書く変更の変更者と時刻を載せる（要求ごとに 1 度）。"""
    session.info[AUDIT_USER_KEY] = user_id
    session.info[AUDIT_NOW_KEY] = now


def _table(name: str) -> Table:
    return Base.metadata.tables[name]


def _row(connection: Connection, table_name: str, row_id: int) -> dict[str, Any] | None:
    table = _table(table_name)
    result = connection.execute(select(table).where(table.c.id == row_id)).mappings().first()
    return None if result is None else dict(result)


def _locate(connection: Connection, table_name: str, values: dict[str, Any]) -> tuple[int | None, str | None]:
    """行の値から、属するプロジェクトと WBS ID を求める（親の行を最大 4 段たどる）。"""
    project_id: int | None = None
    wbs_code: str | None = None
    current_table, current = table_name, values
    for _ in range(5):
        if current_table == "projects":
            project_id = project_id or current.get("id")
        if current_table == "wbs_elements" and wbs_code is None:
            wbs_code = current.get("wbs_code")
        if project_id is None and current.get("project_id") is not None:
            project_id = current["project_id"]
        if project_id is not None and (wbs_code is not None or current_table == "projects"):
            break
        parent = next(((t, current[c]) for c, t in _PARENT_COLUMNS if current.get(c) is not None), None)
        if parent is None:
            break
        loaded = _row(connection, parent[0], parent[1])
        if loaded is None:
            break
        current_table, current = parent[0], loaded
    return project_id, wbs_code


def _values(obj: Any) -> dict[str, Any]:
    state = inspect(obj)
    return {attr.key: state.dict[attr.key] for attr in state.mapper.column_attrs if attr.key in state.dict}


def _changes(obj: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """更新した行の、変わった列の前後の値。"""
    state = inspect(obj)
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    for attr in state.mapper.column_attrs:
        history = state.attrs[attr.key].history
        if not history.has_changes():
            continue
        before[attr.key] = history.deleted[0] if history.deleted else None
        after[attr.key] = history.added[0] if history.added else None
    return before, after


def _record(session: Session, _flush_context: Any) -> None:
    connection = session.connection()
    user_id = session.info.get(AUDIT_USER_KEY)
    occurred_at = session.info.get(AUDIT_NOW_KEY) or utc_now()
    rows: list[dict[str, Any]] = []
    changes: list[tuple[Any, str]] = [
        *((o, "CREATE") for o in session.new),
        *((o, "UPDATE") for o in session.dirty),
        *((o, "DELETE") for o in session.deleted),
    ]
    for obj, kind in changes:
        if isinstance(obj, AuditLog):
            continue
        state = inspect(obj)
        table = state.mapper.local_table
        table_name = getattr(table, "name", None)
        values = _values(obj)
        entity_id = values.get("id")
        if not isinstance(table_name, str) or not isinstance(entity_id, int):
            continue
        if kind == "CREATE":
            before, after = None, values
        elif kind == "DELETE":
            before, after = values, None
        else:
            before, after = _changes(obj)
            if not after:
                continue
        project_id, wbs_code = _locate(connection, table_name, values)
        if kind == "DELETE" and table_name == "projects":
            project_id = None  # 消えた行を外部キーで指さない
        diff = make_audit_diff(table_name, entity_id, before, after, wbs_code=wbs_code)
        if diff is None:
            continue
        rows.append({
            "occurred_at": occurred_at, "user_id": user_id, "project_id": project_id,
            "entity_type": diff.entity_type, "entity_id": diff.entity_id, "wbs_code": diff.wbs_code,
            "action": diff.action.value,
            "before": None if diff.before is None else dict(diff.before),
            "after": None if diff.after is None else dict(diff.after),
        })
    if not rows:
        return
    # 同じフラッシュで消えたプロジェクトを指さない（外部キー）
    project_ids = {r["project_id"] for r in rows if r["project_id"] is not None}
    if project_ids:
        projects = _table("projects")
        existing = set(connection.execute(select(projects.c.id).where(projects.c.id.in_(project_ids))).scalars())
        for r in rows:
            if r["project_id"] not in existing:
                r["project_id"] = None
    connection.execute(insert(AuditLog.__table__), rows)


def register_audit_listener(factory: sessionmaker[Session]) -> None:
    """セッションの生成器に変更履歴の記録を載せる（アプリを作るときに 1 度）。"""
    from sqlalchemy import event

    event.listen(factory, "after_flush", _record)
