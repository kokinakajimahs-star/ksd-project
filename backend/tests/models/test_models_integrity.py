"""表を横に見る DB の整合の試験（設計書 1.2 の 4.2、CR-012、docs/decisions/ASSUMPTIONS.md A-49 の 3・5・7・8）。

- 全ての表の内部キーを再利用しない（SQLite は ``AUTOINCREMENT``。消した最大の ``id`` を使い直さない。CR-012）
- 制約・索引の名前は PostgreSQL の上限の 63 文字以内（切り詰めた名前を付けない。A-49 の 8）
- JSON の列の ``None`` は SQL の NULL で持つ（JSON の ``null`` を保存しない。A-49 の 5）
- 移行（alembic upgrade head）とモデル（``create_all``）で、CHECK の名前と式・既定値（server_default）・制約と索引の名前・
  ``AUTOINCREMENT`` が一致する（A-49 の 7）
- 0011 から 0012 へ上げると、行と ``id``・``sqlite_sequence`` が残り、JSON の ``null`` が NULL になり、採番規則の行の無い
  プロジェクトに行が足される（A-49 の 3）。新しい検査に反する行があれば上げずに止まる。戻しても行を失わない

状態と決めた人・時刻の組の検査（A-49 の 6）は、その表の群の試験（``test_models_wbs.py``・``test_models_chg.py``）が見る。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateIndex, CreateTable

from app.config import BACKEND_DIR
from app.infra.db import Base, create_db_engine
from app.models.chg import AuditLog, ChangeRequest
from app.models.cmn import User
from app.models.est import EstimateRecord
from app.models.prj import Project, ProjectMember
from app.models.wbs import ApprovalRequest, ScopeBaseline, ScopeBaselineElement, WbsElement, WbsReview

AT = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)
# PostgreSQL の識別子の上限（これを超える名前は切り詰められ、モデルの名前と食い違う）
POSTGRESQL_NAME_MAX = 63
# 0012 で足した検査と、付け直した外部キーの名前
NEW_CHECKS = {
    "approval_requests": "ck_approval_requests_decided_pair",
    "change_requests": "ck_change_requests_submitted_at",
}
NEW_APPLIED_CHECK = "ck_change_requests_applied_pair"
SHORT_FK = "fk_schedule_baseline_items_schedule_baseline_id"
LONG_FK = "fk_schedule_baseline_items_schedule_baseline_id_schedule_baselines"
# 0010 で AUTOINCREMENT にした表（0012 は作り直さない）
AUTOINCREMENT_SINCE_0010 = {"wbs_elements", "activities"}
# JSON の列：NULL を許すもの・許さないもの（4.1）
NULLABLE_JSON = {("audit_logs", "before"), ("audit_logs", "after"), ("change_requests", "impact"),
                 ("scope_baseline_elements", "dictionary")}
NOT_NULL_JSON = {("projects", "settings"), ("project_members", "roles"), ("wbs_reviews", "reviewer_ids"),
                 ("change_requests", "target_codes"), ("change_requests", "operations")}


def _user(session: Session, login_id: str) -> User:
    user = User(login_id=login_id, display_name="利用者", password_hash="scrypt$dummy")
    session.add(user)
    session.flush()
    return user


def _project(session: Session, code: str = "P-001") -> Project:
    owner = _user(session, f"owner-{code}")
    project = Project(code=code, name="プロジェクト", start_date=date(2026, 12, 1), owner_user_id=owner.id)
    session.add(project)
    session.flush()
    return project


def _alembic(url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def _autoincrement_tables(connection: sa.Connection) -> dict[str, bool]:
    rows = connection.execute(sa.text("SELECT name, sql FROM sqlite_master WHERE type = 'table'")).all()
    return {name: "AUTOINCREMENT" in sql for name, sql in rows if name in Base.metadata.tables}


def _sequences(connection: sa.Connection) -> dict[str, int]:
    return {name: seq for name, seq in connection.execute(sa.text("SELECT name, seq FROM sqlite_sequence")).all()}


def _schema(engine: sa.Engine) -> dict[str, dict[str, Any]]:
    """DB から読み取った、表ごとの列（型・NULL・既定値）・CHECK（名前と式）・外部キー・一意・索引（名前つき）・AUTOINCREMENT。"""
    inspector = sa.inspect(engine)
    with engine.connect() as connection:
        autoincrement = _autoincrement_tables(connection)
    schema: dict[str, dict[str, Any]] = {}
    for table in sorted(Base.metadata.tables):
        schema[table] = {
            "columns": sorted(
                (c["name"], str(c["type"]), c["nullable"], c.get("default")) for c in inspector.get_columns(table)
            ),
            "checks": sorted((c["name"], " ".join(c["sqltext"].split())) for c in inspector.get_check_constraints(table)),
            "foreign_keys": sorted(
                (fk["name"], tuple(fk["constrained_columns"]), fk["referred_table"], tuple(fk["referred_columns"]))
                for fk in inspector.get_foreign_keys(table)
            ),
            "uniques": sorted((u["name"], tuple(u["column_names"])) for u in inspector.get_unique_constraints(table)),
            "indexes": sorted((i["name"], tuple(i["column_names"]), bool(i["unique"])) for i in inspector.get_indexes(table)),
            "autoincrement": autoincrement[table],
        }
    return schema


# ---------------------------------------------------------------------------
# 内部キーを再利用しない（CR-012）
# ---------------------------------------------------------------------------


def test_every_table_is_declared_autoincrement() -> None:
    """CR-012（4.2）：全ての表のモデルが ``sqlite_autoincrement`` を持つ（表を足したときに付け忘れない）。"""
    missing = [table.name for table in Base.metadata.sorted_tables if not table.dialect_options["sqlite"]["autoincrement"]]
    assert missing == []


def test_every_table_is_created_with_autoincrement(db_session: Session) -> None:
    """CR-012（4.2）：モデルから作った SQLite の表は、全て ``AUTOINCREMENT`` の主キーを持つ。"""
    tables = _autoincrement_tables(db_session.connection())
    assert set(tables) == set(Base.metadata.tables)
    assert sorted(name for name, has in tables.items() if not has) == []


def _new_approval(session: Session, project: Project, user: User) -> int:
    row = ApprovalRequest(project_id=project.id, target_type="WBS", target_id=project.id, requested_by=user.id)
    session.add(row)
    session.flush()
    return row.id


def _new_audit_log(session: Session, project: Project, user: User) -> int:
    row = AuditLog(user_id=user.id, project_id=project.id, entity_type="projects", entity_id=project.id, action="UPDATE")
    session.add(row)
    session.flush()
    return row.id


def _new_estimate(session: Session, project: Project, user: User) -> int:
    row = EstimateRecord(project_id=project.id, target_type="ELEMENT", target_id=1, hours=8, input_value=1, input_unit="DAY",
                         entered_by=user.id)
    session.add(row)
    session.flush()
    return row.id


def _new_change_request(session: Session, project: Project, user: User) -> int:
    number = (session.scalar(sa.select(sa.func.max(ChangeRequest.number))) or 0) + 1
    row = ChangeRequest(project_id=project.id, number=number, title="t", reason="r", created_by=user.id)
    session.add(row)
    session.flush()
    return row.id


def _new_user(session: Session, _owner_project: Project, _writer: User) -> int:
    count = session.scalar(sa.select(sa.func.count()).select_from(User)) or 0
    return _user(session, f"extra-{count}").id


@pytest.mark.parametrize(
    ("table", "create"),
    [
        ("approval_requests", _new_approval),
        ("audit_logs", _new_audit_log),
        ("estimate_records", _new_estimate),
        ("change_requests", _new_change_request),
        ("users", _new_user),
    ],
)
def test_deleted_max_id_is_not_reused(
    db_session: Session, table: str, create: Callable[[Session, Project, User], int]
) -> None:
    """CR-012（4.2）：最大の ``id`` の行を消しても、次の行はその ``id`` を使い直さない（外部キーの無い ``target_id``・``entity_id`` が
    後から作った別の行を指さないため）。"""
    project = _project(db_session)
    user = _user(db_session, "writer")
    create(db_session, project, user)
    last = create(db_session, project, user)
    db_session.commit()
    # 変更履歴は ORM から消せない（追記だけ）ので、SQL で消す
    db_session.execute(sa.text(f"DELETE FROM {table} WHERE id = :id"), {"id": last})
    db_session.commit()
    again = create(db_session, project, user)
    db_session.commit()
    assert again > last


# ---------------------------------------------------------------------------
# 制約・索引の名前（A-49 の 8）
# ---------------------------------------------------------------------------


def _names() -> list[tuple[str, str, str]]:
    names: list[tuple[str, str, str]] = []
    for table in Base.metadata.sorted_tables:
        names += [(table.name, type(constraint).__name__, str(constraint.name or "")) for constraint in table.constraints]
        names += [(table.name, "Index", str(index.name or "")) for index in table.indexes]
    return names


def test_constraint_and_index_names_fit_postgresql_limit() -> None:
    """A-49 の 8：全ての制約・索引に名前があり、どれも 63 文字以内（PostgreSQL が切り詰めない）。"""
    names = _names()
    assert [(t, kind) for t, kind, name in names if not name] == []
    assert [(t, kind, name, len(name)) for t, kind, name in names if len(name) > POSTGRESQL_NAME_MAX] == []
    assert (Base.metadata.tables["schedule_baseline_items"].c.schedule_baseline_id.foreign_keys.pop().constraint.name
            == SHORT_FK)


def test_postgresql_ddl_uses_the_model_names_as_they_are() -> None:
    """A-49 の 8：PostgreSQL の DDL に出る制約・索引の名前がモデルの名前そのもの（SQLAlchemy も縮めない）。サーバを使わず DDL を作って見る。"""
    dialect = postgresql.dialect()
    quote = dialect.identifier_preparer.quote
    missing: list[tuple[str, str]] = []
    for table in Base.metadata.sorted_tables:
        ddl = str(CreateTable(table).compile(dialect=dialect))
        missing += [(table.name, str(c.name)) for c in table.constraints if f"CONSTRAINT {quote(str(c.name))} " not in ddl]
        missing += [
            (table.name, str(i.name)) for i in table.indexes
            if f"INDEX {quote(str(i.name))} " not in str(CreateIndex(i).compile(dialect=dialect))
        ]
    assert missing == []


# ---------------------------------------------------------------------------
# JSON の null（A-49 の 5）
# ---------------------------------------------------------------------------


def test_json_columns_keep_none_as_sql_null() -> None:
    """A-49 の 5：全ての JSON の列が ``none_as_null``（``None`` を JSON の ``null`` でなく SQL の NULL で書く）。"""
    columns = {
        (table.name, column.name): column for table in Base.metadata.sorted_tables for column in table.columns
        if isinstance(column.type, sa.JSON)
    }
    assert NULLABLE_JSON | NOT_NULL_JSON <= set(columns)
    assert sorted(name for name, column in columns.items() if not column.type.none_as_null) == []  # type: ignore[attr-defined]
    assert {name for name, column in columns.items() if column.nullable} == NULLABLE_JSON


def test_none_in_nullable_json_columns_is_stored_as_sql_null(db_session: Session) -> None:
    """A-49 の 5：NULL を許す JSON の列に ``None`` を書くと SQL の NULL になる（JSON の ``null`` の文字列を残さない）。読むと ``None``。"""
    project = _project(db_session)
    user = _user(db_session, "writer")
    audit = AuditLog(entity_type="projects", entity_id=project.id, action="CREATE", before=None, after={"name": "x"})
    change_request = ChangeRequest(project_id=project.id, number=1, title="t", reason="r", created_by=user.id, impact=None)
    element = WbsElement(project_id=project.id, kind="DELIVERABLE", name="成果物", wbs_code="1.0", sibling_seq=1, sort_order=1)
    db_session.add_all([audit, change_request, element])
    db_session.flush()
    baseline = ScopeBaseline(project_id=project.id, version=1, reason="INITIAL_APPROVAL", created_by=user.id)
    db_session.add(baseline)
    db_session.flush()
    snapshot = ScopeBaselineElement(scope_baseline_id=baseline.id, element_id=element.id, wbs_code="1.0", kind="DELIVERABLE",
                                    name="成果物", dictionary=None)
    db_session.add(snapshot)
    db_session.commit()

    raw = db_session.execute(sa.text(
        "SELECT (SELECT before IS NULL FROM audit_logs WHERE id = :a), (SELECT after FROM audit_logs WHERE id = :a), "
        "(SELECT impact IS NULL FROM change_requests WHERE id = :c), "
        "(SELECT dictionary IS NULL FROM scope_baseline_elements WHERE id = :s)"
    ), {"a": audit.id, "c": change_request.id, "s": snapshot.id}).one()
    assert tuple(raw) == (1, '{"name": "x"}', 1, 1)

    db_session.expunge_all()
    loaded = db_session.get(AuditLog, audit.id)
    assert loaded is not None and (loaded.before, loaded.after) == (None, {"name": "x"})


def _not_null_json_row(session: Session, table: str) -> Any:
    project = _project(session)
    user = _user(session, "writer")
    rows: dict[str, Any] = {
        "projects": lambda: Project(code="P-NULL", name="p", start_date=date(2026, 12, 1), owner_user_id=user.id),
        "project_members": lambda: ProjectMember(project_id=project.id, user_id=user.id, roles=["MEMBER"]),
        "wbs_reviews": lambda: WbsReview(project_id=project.id, requested_by=user.id, reviewer_ids=[user.id]),
        "change_requests": lambda: ChangeRequest(project_id=project.id, number=1, title="t", reason="r", created_by=user.id),
    }
    return rows[table]()


@pytest.mark.parametrize(("table", "column"), sorted(NOT_NULL_JSON))
def test_none_in_not_null_json_columns_is_refused(db_session: Session, table: str, column: str) -> None:
    """A-49 の 5：NOT NULL の JSON の列に ``None`` を書くと、JSON の ``null`` で通らず DB が拒む（SQL の挿入・ORM の更新）。"""
    row = _not_null_json_row(db_session, table)
    db_session.add(row)
    db_session.flush()
    values = {c.name: getattr(row, c.key) for c in row.__table__.columns if c.name != "id"}
    db_session.rollback()

    with pytest.raises(IntegrityError, match=f"NOT NULL constraint failed: {table}.{column}"):
        db_session.execute(sa.insert(row.__table__).values({**values, column: None}))
    db_session.rollback()

    row = _not_null_json_row(db_session, table)
    db_session.add(row)
    db_session.commit()
    setattr(row, column, None)
    with pytest.raises(IntegrityError, match=f"NOT NULL constraint failed: {table}.{column}"):
        db_session.flush()
    db_session.rollback()


@pytest.mark.parametrize(
    ("table", "column"), sorted(column for column in NOT_NULL_JSON if column != ("project_members", "roles"))
)
def test_none_in_json_columns_with_default_takes_the_default_on_orm_insert(
    db_session: Session, table: str, column: str
) -> None:
    """A-49 の 5：既定値のある NOT NULL の JSON の列に ORM の挿入で ``None`` を渡すと、既定値が入る（JSON の ``null`` を残さない）。"""
    row = _not_null_json_row(db_session, table)
    setattr(row, column, None)
    db_session.add(row)
    db_session.commit()
    stored = db_session.scalar(sa.text(f"SELECT {column} FROM {table} WHERE id = :id"), {"id": row.id})
    default = row.__table__.c[column].default.arg
    expected = default(None) if callable(default) else default
    assert stored != "null"
    db_session.expunge_all()
    assert getattr(db_session.get(type(row), row.id), column) == expected


# ---------------------------------------------------------------------------
# 移行とモデルの一致（A-49 の 7）
# ---------------------------------------------------------------------------


def test_migration_matches_models_including_checks_defaults_and_names(tmp_path: Path) -> None:
    """A-49 の 7：upgrade head の DB とモデルから作った DB が、CHECK の名前と式・既定値（server_default）・制約と索引の名前・
    AUTOINCREMENT まで一致する。Alembic の比較（compare_metadata）も既定値を比べて差が無い。"""
    migrated_url = f"sqlite:///{tmp_path / 'migrated.db'}"
    command.upgrade(_alembic(migrated_url), "head")
    migrated = create_db_engine(migrated_url)
    modelled = create_db_engine(f"sqlite:///{tmp_path / 'models.db'}")
    try:
        Base.metadata.create_all(modelled)
        expected, actual = _schema(modelled), _schema(migrated)
        assert actual == expected
        with migrated.connect() as connection:
            context = MigrationContext.configure(connection, opts={"compare_server_default": True, "compare_type": True})
            assert compare_metadata(context, Base.metadata) == []
    finally:
        migrated.dispose()
        modelled.dispose()

    # 比べたものが空でない（CHECK の式と既定値を読めている）
    assert {name for name, _ in actual["approval_requests"]["checks"]} >= {NEW_CHECKS["approval_requests"]}
    assert {name for name, _ in actual["change_requests"]["checks"]} >= {NEW_CHECKS["change_requests"], NEW_APPLIED_CHECK}
    assert ("status", "VARCHAR(16)", False, "'PENDING'") in actual["approval_requests"]["columns"]
    assert [fk[0] for fk in actual["schedule_baseline_items"]["foreign_keys"] if fk[1] == ("schedule_baseline_id",)] == [SHORT_FK]
    assert all(table["autoincrement"] for table in actual.values())


# ---------------------------------------------------------------------------
# 0011 → 0012 の移行（行・id・sqlite_sequence・JSON の null・採番規則の行）
# ---------------------------------------------------------------------------

_NOW = "2026-11-13 09:00:00.000000"


def _seed_at_0011(connection: sa.Connection) -> dict[str, int]:
    """0011 の DB に行を入れる（ORM のモデルは 0012 の形なので SQL で書く）。"""
    def insert(sql: str, **values: Any) -> int:
        result = connection.execute(sa.text(sql), values)
        return int(result.lastrowid)  # type: ignore[attr-defined]

    ids: dict[str, int] = {}
    user_sql = "INSERT INTO users (login_id, display_name, password_hash) VALUES (:login, '利用者', 'scrypt$dummy')"
    ids["owner"] = insert(user_sql, login="owner")
    ids["pm"] = insert(user_sql, login="pm")
    project_sql = ("INSERT INTO projects (code, name, start_date, owner_user_id, settings) "
                   "VALUES (:code, 'p', '2026-12-01', :owner, '{}')")
    ids["with_elements"] = insert(project_sql, code="P-1", owner=ids["owner"])
    ids["with_rule"] = insert(project_sql, code="P-2", owner=ids["owner"])
    ids["empty"] = insert(project_sql, code="P-3", owner=ids["owner"])
    connection.execute(sa.text("INSERT INTO wbs_numbering_rules (project_id, top_last_child_seq) VALUES (:p, 7)"),
                       {"p": ids["with_rule"]})

    element_sql = ("INSERT INTO wbs_elements (project_id, parent_id, kind, name, wbs_code, sibling_seq, sort_order) "
                   "VALUES (:p, :parent, 'DELIVERABLE', 'x', :code, :seq, :seq)")
    first = insert(element_sql, p=ids["with_elements"], parent=None, code="1.0", seq=1)
    insert(element_sql, p=ids["with_elements"], parent=None, code="4.0", seq=4)
    insert(element_sql, p=ids["with_elements"], parent=first, code="1.9", seq=9)  # 第 2 階層は数えない
    deleted_element = insert(element_sql, p=ids["with_elements"], parent=None, code="9.0", seq=9)
    connection.execute(sa.text("DELETE FROM wbs_elements WHERE id = :id"), {"id": deleted_element})
    ids["deleted_element"] = deleted_element
    ids["first_element"] = first

    approval_sql = ("INSERT INTO approval_requests (project_id, target_type, target_id, status, requested_by, requested_at, "
                    "decided_by, decided_at) VALUES (:p, 'WBS', :p, :status, :pm, :now, :by, :at)")
    ids["pending"] = insert(approval_sql, p=ids["with_elements"], status="PENDING", pm=ids["pm"], now=_NOW, by=None, at=None)
    ids["approved"] = insert(approval_sql, p=ids["with_elements"], status="APPROVED", pm=ids["pm"], now=_NOW,
                             by=ids["owner"], at=_NOW)

    change_sql = ("INSERT INTO change_requests (project_id, number, title, reason, target_codes, operations, status, impact, "
                  "created_by, submitted_at, applied_by, applied_at) "
                  "VALUES (:p, :number, 't', 'r', '[]', '[]', :status, :impact, :pm, :submitted, :by, :at)")
    ids["draft"] = insert(change_sql, p=ids["with_elements"], number=1, status="DRAFT", impact="null", pm=ids["pm"],
                          submitted=None, by=None, at=None)
    ids["applied"] = insert(change_sql, p=ids["with_elements"], number=2, status="APPLIED", impact='{"shift_days": 3}',
                            pm=ids["pm"], submitted=_NOW, by=ids["owner"], at=_NOW)

    audit_sql = ("INSERT INTO audit_logs (occurred_at, entity_type, entity_id, action, before, after) "
                 "VALUES (:now, 'projects', 1, :action, :before, :after)")
    ids["audit_create"] = insert(audit_sql, now=_NOW, action="CREATE", before="null", after='{"name": "p"}')
    ids["audit_update"] = insert(audit_sql, now=_NOW, action="UPDATE", before='{"name": "p"}', after='{"name": "q"}')
    ids["audit_sql_null"] = insert(audit_sql, now=_NOW, action="DELETE", before='{"name": "q"}', after=None)
    deleted_audit = insert(audit_sql, now=_NOW, action="UPDATE", before="null", after="null")
    connection.execute(sa.text("DELETE FROM audit_logs WHERE id = :id"), {"id": deleted_audit})

    baseline = insert("INSERT INTO scope_baselines (project_id, version, reason, created_by, created_at) "
                      "VALUES (:p, 1, 'INITIAL_APPROVAL', :pm, :now)", p=ids["with_elements"], pm=ids["pm"], now=_NOW)
    ids["snapshot"] = insert(
        "INSERT INTO scope_baseline_elements (scope_baseline_id, element_id, wbs_code, kind, name, dictionary) "
        "VALUES (:b, :e, '1.0', 'DELIVERABLE', 'x', 'null')", b=baseline, e=first)
    return ids


def test_migration_0012_keeps_rows_and_ids_and_fixes_json_null_and_numbering(tmp_path: Path) -> None:
    """0011 → 0012：行と ``id`` を保って全ての表を AUTOINCREMENT にし、JSON の ``null`` を NULL にし、採番規則の行を補う。

    作り直した表の ``sqlite_sequence`` は写した最大の ``id``（それより前に消した ``id`` は分からない）。0010 で AUTOINCREMENT に
    した ``wbs_elements`` は作り直さないので、消した最大の ``id`` を覚えたまま。0011 へ戻しても行は残り、また上げられる。
    """
    url = f"sqlite:///{tmp_path / 'data.db'}"
    command.upgrade(_alembic(url), "0011_users_auth")
    engine = create_db_engine(url)
    try:
        with engine.begin() as connection:
            ids = _seed_at_0011(connection)
            before_counts = {t: connection.scalar(sa.text(f"SELECT COUNT(*) FROM {t}")) for t in Base.metadata.tables}
            before_ids = {t: connection.execute(sa.text(f"SELECT id FROM {t} ORDER BY id")).scalars().all()
                          for t in Base.metadata.tables if t != "wbs_numbering_rules"}
            assert _autoincrement_tables(connection)["audit_logs"] is False
    finally:
        engine.dispose()

    command.upgrade(_alembic(url), "0012_db_integrity")
    engine = create_db_engine(url)
    try:
        with engine.begin() as connection:
            assert all(_autoincrement_tables(connection).values())
            after_ids = {t: connection.execute(sa.text(f"SELECT id FROM {t} ORDER BY id")).scalars().all() for t in before_ids}
            assert after_ids == before_ids
            sequences = _sequences(connection)
            assert sequences["wbs_elements"] == ids["deleted_element"]
            assert sequences["audit_logs"] == ids["audit_sql_null"]
            assert sequences["change_requests"] == ids["applied"]

            # JSON の null → NULL。ほかの値はそのまま
            assert connection.execute(sa.text("SELECT id, before, after FROM audit_logs ORDER BY id")).all() == [
                (ids["audit_create"], None, '{"name": "p"}'),
                (ids["audit_update"], '{"name": "p"}', '{"name": "q"}'),
                (ids["audit_sql_null"], '{"name": "q"}', None),
            ]
            assert connection.execute(sa.text("SELECT id, impact FROM change_requests ORDER BY id")).all() == [
                (ids["draft"], None), (ids["applied"], '{"shift_days": 3}')]
            assert connection.scalar(sa.text("SELECT dictionary IS NULL FROM scope_baseline_elements")) == 1

            # 採番規則の行：無いプロジェクトに足す（第 1 階層の順番の最大。要素が無ければ 0）。あった行は変えない
            rules = dict(connection.execute(sa.text("SELECT project_id, top_last_child_seq FROM wbs_numbering_rules")).all())
            assert rules == {ids["with_elements"]: 4, ids["with_rule"]: 7, ids["empty"]: 0}
            assert connection.scalar(sa.text("SELECT COUNT(*) FROM wbs_numbering_rules")) == 3

            # 消した最大の id を使い直さない（0010 の wbs_elements と、0012 で作り直した audit_logs）
            new_element = connection.execute(sa.text(
                "INSERT INTO wbs_elements (project_id, kind, name, wbs_code, sibling_seq, sort_order) "
                "VALUES (:p, 'DELIVERABLE', 'y', '5.0', 5, 5)"), {"p": ids["with_elements"]}).lastrowid
            assert new_element > ids["deleted_element"]
            connection.execute(sa.text("DELETE FROM audit_logs WHERE id = :id"), {"id": ids["audit_sql_null"]})
            new_audit = connection.execute(sa.text(
                "INSERT INTO audit_logs (occurred_at, entity_type, entity_id, action) VALUES (:now, 'projects', 1, 'CREATE')"),
                {"now": _NOW}).lastrowid
            assert new_audit > ids["audit_sql_null"]

        # 新しい検査と外部キーの名前
        with engine.connect() as connection, pytest.raises(IntegrityError, match=NEW_CHECKS["approval_requests"]):
            connection.execute(sa.text("UPDATE approval_requests SET status = 'APPROVED' WHERE id = :id"), {"id": ids["pending"]})
        fks = sa.inspect(engine).get_foreign_keys("schedule_baseline_items")
        assert [fk["name"] for fk in fks if fk["constrained_columns"] == ["schedule_baseline_id"]] == [SHORT_FK]
    finally:
        engine.dispose()

    command.downgrade(_alembic(url), "0011_users_auth")
    engine = create_db_engine(url)
    try:
        with engine.connect() as connection:
            autoincrement = _autoincrement_tables(connection)
            assert {t for t, has in autoincrement.items() if has} == AUTOINCREMENT_SINCE_0010
            counts = {t: connection.scalar(sa.text(f"SELECT COUNT(*) FROM {t}")) for t in Base.metadata.tables}
            # 0012 で補った採番規則の行（1 行）と、上で足した要素（1 行）は残る。消した変更履歴と足した変更履歴で数は同じ
            assert counts == {**before_counts, "wbs_numbering_rules": before_counts["wbs_numbering_rules"] + 2,
                              "wbs_elements": before_counts["wbs_elements"] + 1}
        inspector = sa.inspect(engine)
        assert NEW_CHECKS["approval_requests"] not in {c["name"] for c in inspector.get_check_constraints("approval_requests")}
        assert not {NEW_CHECKS["change_requests"], NEW_APPLIED_CHECK} & {
            c["name"] for c in inspector.get_check_constraints("change_requests")}
        fks = inspector.get_foreign_keys("schedule_baseline_items")
        assert [fk["name"] for fk in fks if fk["constrained_columns"] == ["schedule_baseline_id"]] == [LONG_FK]
    finally:
        engine.dispose()
    command.upgrade(_alembic(url), "head")


@pytest.mark.parametrize(
    ("row", "sql", "message"),
    [
        ("draft", "UPDATE change_requests SET status = 'SUBMITTED'", "ck_change_requests_submitted_at"),
        ("draft", "UPDATE change_requests SET applied_by = (SELECT MIN(id) FROM users)", "ck_change_requests_applied_pair"),
        ("pending", f"UPDATE approval_requests SET decided_at = '{_NOW}'", "ck_approval_requests_decided_pair"),
        ("empty", "UPDATE projects SET settings = 'null'", r"projects\.settings"),
        ("draft", "UPDATE change_requests SET operations = 'null'", r"change_requests\.operations"),
    ],
    ids=["submitted-without-time", "draft-with-applier", "pending-with-time", "settings-null", "operations-null"],
)
def test_migration_0012_refuses_rows_it_cannot_keep(tmp_path: Path, row: str, sql: str, message: str) -> None:
    """0011 → 0012：新しい検査に反する行・NOT NULL の JSON の列の JSON の ``null`` があれば、何も変えずに止まる（直し方は人が決める）。"""
    url = f"sqlite:///{tmp_path / 'refuse.db'}"
    command.upgrade(_alembic(url), "0011_users_auth")
    engine = create_db_engine(url)
    try:
        with engine.begin() as connection:
            ids = _seed_at_0011(connection)
            connection.execute(sa.text(f"{sql} WHERE id = :id"), {"id": ids[row]})
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match=message):
        command.upgrade(_alembic(url), "head")

    engine = create_db_engine(url)
    try:
        with engine.connect() as connection:
            assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == "0011_users_auth"
            assert connection.scalar(sa.text("SELECT COUNT(*) FROM wbs_numbering_rules")) == 1
            kept = connection.scalar(sa.text("SELECT before FROM audit_logs WHERE id = :id"), {"id": ids["audit_create"]})
            assert kept == "null"
            assert _autoincrement_tables(connection)["users"] is False
    finally:
        engine.dispose()
