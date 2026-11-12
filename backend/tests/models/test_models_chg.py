"""変更管理（CHG）の群のテーブルの試験（設計書 3 章・4.1・4.2・5.9・5.10、要求仕様書 CHG-01〜04・06・09、NFR-05）：DB-CHG-001〜003。

列が 4.1 の行どおりであること、4.1 の「プロジェクト内の連番」（変更要求の番号）の一意、値の一覧（変更要求・是正措置の状態、
変更履歴の操作）と外部キーが DB で効くこと、変更履歴が追記だけであること（4.2・NFR-05）、
先に作った表の列（スコープ／スケジュールベースラインの変更要求、差異の理由の是正措置）の外部キーが DB で効くこと、
移行（alembic upgrade head）の結果がモデルと食い違わないことを確かめる。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import BACKEND_DIR
from app.infra.db import Base, create_db_engine
from app.models.chg import (
    AUDIT_ACTIONS,
    CHANGE_REQUEST_STATUSES,
    CORRECTIVE_ACTION_STATUSES,
    AuditLog,
    AuditLogImmutableError,
    ChangeRequest,
    CorrectiveAction,
)
from app.models.cmn import User
from app.models.eff import VarianceNote
from app.models.prg import StatusReport
from app.models.prj import Project
from app.models.sch import Activity, ScheduleBaseline
from app.models.wbs import ScopeBaseline, WbsElement

# 設計書 4.1 の「主な列」
COLUMNS_4_1 = {
    "change_requests": {
        "id", "project_id", "number", "title", "reason", "target_codes", "operations", "status", "impact", "created_by",
        "submitted_at", "applied_by", "applied_at",
    },
    "corrective_actions": {
        "id", "project_id", "element_id", "activity_id", "status_report_id", "cause", "action", "owner_user_id",
        "due_date", "status", "effect",
    },
    "audit_logs": {
        "id", "occurred_at", "user_id", "project_id", "entity_type", "entity_id", "wbs_code", "action", "before",
        "after",
    },
}

AT = datetime(2026, 12, 10, 9, 0, tzinfo=UTC)
MISSING = 999_999


def _user(session: Session, login_id: str = "planner") -> User:
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


def _wp(session: Session, project: Project, seq: int = 1) -> WbsElement:
    wp = WbsElement(
        project_id=project.id, kind="WORK_PACKAGE", name=f"WP {seq}", wbs_code=f"1.{seq}", sibling_seq=seq, sort_order=seq
    )
    session.add(wp)
    session.flush()
    return wp


def _activity(session: Session, project: Project, wp: WbsElement) -> Activity:
    activity = Activity(project_id=project.id, work_package_id=wp.id, name="資料作成", sort_order=1, duration_days=5)
    session.add(activity)
    session.flush()
    return activity


def _report(session: Session, project: Project, creator: User) -> StatusReport:
    report = StatusReport(project_id=project.id, report_date=date(2026, 12, 10), created_by=creator.id)
    session.add(report)
    session.flush()
    return report


def _change_request(session: Session, project: Project, creator: User, number: int = 1, **values: Any) -> ChangeRequest:
    fields: dict[str, Any] = {
        "project_id": project.id, "number": number, "title": "画面の追加", "reason": "顧客の要望", "created_by": creator.id,
    }
    fields.update(values)
    change_request = ChangeRequest(**fields)
    session.add(change_request)
    session.flush()
    return change_request


def _action(session: Session, project: Project, **values: Any) -> CorrectiveAction:
    fields: dict[str, Any] = {"project_id": project.id, "cause": "仕様の追加", "action": "要員を足す"}
    fields.update(values)
    action = CorrectiveAction(**fields)
    session.add(action)
    session.flush()
    return action


def _flush_fails(session: Session) -> None:
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


@pytest.mark.parametrize("table", sorted(COLUMNS_4_1))
def test_columns_match_design_4_1(table: str) -> None:
    """DB-CHG-001〜003 の列が設計書 4.1 の行どおりである。"""
    assert set(Base.metadata.tables[table].c.keys()) == COLUMNS_4_1[table]


def test_value_lists_match_design() -> None:
    """値の一覧が設計書どおり：変更要求の状態は 5.10 の 5 つ、是正措置の状態は 4.1 の 3 つ、変更履歴の操作は 4.1 の 3 つ。"""
    assert CHANGE_REQUEST_STATUSES == ("DRAFT", "SUBMITTED", "APPROVED", "REJECTED", "APPLIED")
    assert CORRECTIVE_ACTION_STATUSES == ("NOT_STARTED", "IN_PROGRESS", "DONE")
    assert AUDIT_ACTIONS == ("CREATE", "UPDATE", "DELETE")


# ---------------------------------------------------------------------------
# DB-CHG-001 change_requests
# ---------------------------------------------------------------------------


def test_change_requests_round_trip_with_json_columns(db_session: Session) -> None:
    """DB-CHG-001 change_requests：対象の WBS ID（複数）・変更内容（5.9 の操作の配列）・影響分析を JSON で保存できる（CHG-01・02）。

    起票は DRAFT、提出・反映の時刻と反映した人は空から始まる。
    """
    project = _project(db_session)
    creator = _user(db_session)
    operations = [
        {"op": "add_element", "parent_code": "1.0", "kind": "WORK_PACKAGE", "name": "画面 C", "ref": "wp-c"},
        {"op": "add_activity", "wp_ref": "wp-c", "name": "画面 C の製造", "duration_days": 3, "estimate_hours": 24,
         "ref": "a-c"},
        {"op": "add_dependency", "predecessor": 101, "successor": "a-c"},
    ]
    created = _change_request(db_session, project, creator, target_codes=["1.0", "1.2"], operations=operations)
    db_session.commit()
    db_session.expunge_all()

    loaded = db_session.get(ChangeRequest, created.id)
    assert loaded is not None
    assert (loaded.number, loaded.title, loaded.reason, loaded.target_codes, loaded.operations) == (
        1, "画面の追加", "顧客の要望", ["1.0", "1.2"], operations)
    assert (loaded.status, loaded.impact, loaded.submitted_at, loaded.applied_by, loaded.applied_at) == (
        "DRAFT", None, None, None, None)

    applier = _user(db_session, "pm")
    impact = {"project_finish": {"before": "2027-01-29", "after": "2027-02-03", "shift_days": 3},
              "total_estimate_hours": {"before": 400, "after": 424}}
    loaded.status, loaded.impact, loaded.submitted_at = "APPLIED", impact, AT
    loaded.applied_by, loaded.applied_at = applier.id, AT
    db_session.commit()
    db_session.expunge_all()
    again = db_session.get(ChangeRequest, created.id)
    assert again is not None
    assert (again.status, again.impact, again.submitted_at, again.applied_by, again.applied_at) == (
        "APPLIED", impact, AT, applier.id, AT)


def test_change_requests_defaults_are_empty_lists(db_session: Session) -> None:
    """DB-CHG-001 change_requests：対象・変更内容を渡さなければ空の配列（NULL にしない）。"""
    project = _project(db_session)
    created = _change_request(db_session, project, _user(db_session))
    db_session.commit()
    db_session.expunge_all()
    loaded = db_session.get(ChangeRequest, created.id)
    assert loaded is not None and (loaded.target_codes, loaded.operations) == ([], [])


def test_change_requests_number_is_unique_per_project(db_session: Session) -> None:
    """DB-CHG-001 change_requests：番号はプロジェクトの中の連番（project_id＋number で一意）。別のプロジェクトなら同じ番号を置ける。"""
    project, other = _project(db_session), _project(db_session, "P-002")
    creator = _user(db_session)
    _change_request(db_session, project, creator, 1)
    _change_request(db_session, project, creator, 2)
    _change_request(db_session, other, creator, 1)
    db_session.commit()
    db_session.add(ChangeRequest(project_id=project.id, number=2, title="t", reason="r", created_by=creator.id))
    _flush_fails(db_session)


@pytest.mark.parametrize("status", CHANGE_REQUEST_STATUSES)
def test_change_requests_accept_every_status(db_session: Session, status: str) -> None:
    """DB-CHG-001 change_requests：5.10 の 5 つの状態はどれも保存できる。"""
    _change_request(db_session, _project(db_session), _user(db_session), status=status)
    db_session.commit()


@pytest.mark.parametrize(
    ("field", "value"),
    [("status", "CANCELLED"), ("status", "draft"), ("number", 0), ("number", -1), ("title", None), ("reason", None)],
)
def test_change_requests_rejections(db_session: Session, field: str, value: Any) -> None:
    """DB-CHG-001 change_requests：5.10 に無い状態・1 未満の番号・件名や理由の欠けを DB が拒む。"""
    project = _project(db_session)
    creator = _user(db_session)
    db_session.commit()
    fields: dict[str, Any] = {"project_id": project.id, "number": 1, "title": "t", "reason": "r", "created_by": creator.id}
    fields[field] = value
    db_session.add(ChangeRequest(**fields))
    _flush_fails(db_session)


@pytest.mark.parametrize("field", ["project_id", "created_by", "applied_by"])
def test_change_requests_foreign_keys(db_session: Session, field: str) -> None:
    """DB-CHG-001 change_requests：プロジェクト・起票者・反映した人は実在する行を指す。"""
    project = _project(db_session)
    creator = _user(db_session)
    db_session.commit()
    fields: dict[str, Any] = {"project_id": project.id, "number": 1, "title": "t", "reason": "r", "created_by": creator.id}
    fields[field] = MISSING
    db_session.add(ChangeRequest(**fields))
    _flush_fails(db_session)


def test_baselines_point_at_existing_change_requests(db_session: Session) -> None:
    """DB-CHG-001：スコープ・スケジュールベースラインの change_request_id は実在する変更要求だけを指す（A-15・A-19 の外部キー）。"""
    project = _project(db_session)
    creator = _user(db_session)
    change_request = _change_request(db_session, project, creator)
    db_session.add(ScopeBaseline(project_id=project.id, version=1, reason="CHANGE_REQUEST",
                                 change_request_id=change_request.id, created_by=creator.id))
    db_session.add(ScheduleBaseline(project_id=project.id, version=1, name="変更 1", reason="CHANGE_REQUEST",
                                    change_request_id=change_request.id, created_by=creator.id))
    db_session.commit()

    db_session.add(ScopeBaseline(project_id=project.id, version=2, reason="CHANGE_REQUEST",
                                 change_request_id=MISSING, created_by=creator.id))
    _flush_fails(db_session)
    db_session.add(ScheduleBaseline(project_id=project.id, version=2, name="変更 2", reason="CHANGE_REQUEST",
                                    change_request_id=MISSING, created_by=creator.id))
    _flush_fails(db_session)


def test_change_request_with_baseline_is_not_deleted(db_session: Session) -> None:
    """DB-CHG-001：版を作った変更要求は DB が消させない（ON DELETE を付けない。旧版を残す BR-12）。"""
    project = _project(db_session)
    creator = _user(db_session)
    change_request = _change_request(db_session, project, creator)
    db_session.add(ScopeBaseline(project_id=project.id, version=1, reason="CHANGE_REQUEST",
                                 change_request_id=change_request.id, created_by=creator.id))
    db_session.commit()
    db_session.delete(change_request)
    _flush_fails(db_session)


# ---------------------------------------------------------------------------
# DB-CHG-002 corrective_actions
# ---------------------------------------------------------------------------


def test_corrective_actions_round_trip(db_session: Session) -> None:
    """DB-CHG-002 corrective_actions：WBS要素・活動・報告日に紐付け、原因・対策・担当・期限・状態・効果を持つ（CHG-06）。登録は NOT_STARTED。"""
    project = _project(db_session)
    owner = _user(db_session, "progress")
    wp = _wp(db_session, project)
    activity = _activity(db_session, project, wp)
    report = _report(db_session, project, owner)
    created = _action(db_session, project, element_id=wp.id, activity_id=activity.id, status_report_id=report.id,
                      owner_user_id=owner.id, due_date=date(2026, 12, 20))
    db_session.commit()
    db_session.expunge_all()

    loaded = db_session.get(CorrectiveAction, created.id)
    assert loaded is not None
    assert (loaded.element_id, loaded.activity_id, loaded.status_report_id, loaded.cause, loaded.action) == (
        wp.id, activity.id, report.id, "仕様の追加", "要員を足す")
    assert (loaded.owner_user_id, loaded.due_date, loaded.status, loaded.effect) == (
        owner.id, date(2026, 12, 20), "NOT_STARTED", None)

    loaded.status, loaded.effect = "DONE", "遅れを 2 日取り戻した"
    db_session.commit()
    db_session.expunge_all()
    again = db_session.get(CorrectiveAction, created.id)
    assert again is not None and (again.status, again.effect) == ("DONE", "遅れを 2 日取り戻した")


def test_corrective_actions_links_and_owner_are_optional(db_session: Session) -> None:
    """DB-CHG-002 corrective_actions：紐付け（要素・活動・報告日）・担当・期限・効果は空でよい（後から決める）。"""
    created = _action(db_session, _project(db_session))
    db_session.commit()
    db_session.expunge_all()
    loaded = db_session.get(CorrectiveAction, created.id)
    assert loaded is not None
    assert (loaded.element_id, loaded.activity_id, loaded.status_report_id, loaded.owner_user_id, loaded.due_date) == (
        None, None, None, None, None)


@pytest.mark.parametrize(
    ("field", "value"),
    [("status", "CANCELLED"), ("status", "done"), ("cause", None), ("action", None)],
)
def test_corrective_actions_rejections(db_session: Session, field: str, value: Any) -> None:
    """DB-CHG-002 corrective_actions：4.1 に無い状態、原因・対策の欠けを DB が拒む。"""
    project = _project(db_session)
    db_session.commit()
    fields: dict[str, Any] = {"project_id": project.id, "cause": "c", "action": "a"}
    fields[field] = value
    db_session.add(CorrectiveAction(**fields))
    _flush_fails(db_session)


@pytest.mark.parametrize("field", ["project_id", "element_id", "activity_id", "status_report_id", "owner_user_id"])
def test_corrective_actions_foreign_keys(db_session: Session, field: str) -> None:
    """DB-CHG-002 corrective_actions：プロジェクト・要素・活動・報告日・担当は実在する行を指す。"""
    project = _project(db_session)
    db_session.commit()
    fields: dict[str, Any] = {"project_id": project.id, "cause": "c", "action": "a"}
    fields[field] = MISSING
    db_session.add(CorrectiveAction(**fields))
    _flush_fails(db_session)


def test_variance_notes_point_at_existing_corrective_actions(db_session: Session) -> None:
    """DB-CHG-002：差異の理由の corrective_action_id は実在する是正措置だけを指す（A-27 の外部キー。EFF-10 の紐付け）。"""
    project = _project(db_session)
    creator = _user(db_session)
    wp = _wp(db_session, project)
    action = _action(db_session, project, element_id=wp.id)
    db_session.add(VarianceNote(project_id=project.id, element_id=wp.id, reason="仕様の追加",
                                corrective_action_id=action.id, created_by=creator.id))
    db_session.commit()

    db_session.add(VarianceNote(project_id=project.id, element_id=wp.id, reason="仕様の追加",
                                corrective_action_id=MISSING, created_by=creator.id))
    _flush_fails(db_session)


# ---------------------------------------------------------------------------
# DB-CHG-003 audit_logs
# ---------------------------------------------------------------------------


def test_audit_logs_round_trip(db_session: Session) -> None:
    """DB-CHG-003 audit_logs：変更者・日時・対象（種類・ID・WBS ID）・操作・変更前後の値（JSON）を残す（CHG-09）。"""
    project = _project(db_session)
    user = _user(db_session)
    before = {"name": "基本設計", "own_estimate_hours": "16.00"}
    after = {"name": "基本設計（画面）", "own_estimate_hours": "24.00"}
    created = AuditLog(occurred_at=AT, user_id=user.id, project_id=project.id, entity_type="wbs_elements",
                       entity_id=42, wbs_code="1.1", action="UPDATE", before=before, after=after)
    db_session.add(created)
    db_session.commit()
    db_session.expunge_all()

    loaded = db_session.get(AuditLog, created.id)
    assert loaded is not None
    assert (loaded.occurred_at, loaded.user_id, loaded.project_id, loaded.entity_type, loaded.entity_id) == (
        AT, user.id, project.id, "wbs_elements", 42)
    assert (loaded.wbs_code, loaded.action, loaded.before, loaded.after) == ("1.1", "UPDATE", before, after)


def test_audit_logs_allow_rows_without_user_project_or_values(db_session: Session) -> None:
    """DB-CHG-003 audit_logs：プロジェクトに属さない対象（利用者など）・利用者の操作でない書き込みは空。CREATE は変更前、DELETE は変更後が空。

    時刻を渡さなければ記録した時刻（UTC）が入る。対象の行が消えた後も履歴は残る（entity_id は外部キーを持たない）。
    """
    created = AuditLog(entity_type="users", entity_id=MISSING, action="CREATE", after={"login_id": "admin"})
    deleted = AuditLog(entity_type="activities", entity_id=MISSING, action="DELETE", before={"name": "資料作成"})
    db_session.add_all([created, deleted])
    db_session.commit()
    db_session.expunge_all()

    loaded = db_session.get(AuditLog, created.id)
    assert loaded is not None
    assert (loaded.user_id, loaded.project_id, loaded.wbs_code, loaded.before) == (None, None, None, None)
    assert loaded.occurred_at.tzinfo is not None and loaded.occurred_at.utcoffset() == UTC.utcoffset(None)
    gone = db_session.get(AuditLog, deleted.id)
    assert gone is not None and (gone.before, gone.after) == ({"name": "資料作成"}, None)


@pytest.mark.parametrize(
    ("field", "value"),
    [("action", "UPSERT"), ("action", "update"), ("entity_type", None), ("entity_id", None),
     ("user_id", MISSING), ("project_id", MISSING)],
)
def test_audit_logs_rejections(db_session: Session, field: str, value: Any) -> None:
    """DB-CHG-003 audit_logs：4.1 に無い操作・対象の欠け・実在しない変更者やプロジェクトを DB が拒む。"""
    fields: dict[str, Any] = {"entity_type": "projects", "entity_id": 1, "action": "CREATE"}
    fields[field] = value
    db_session.add(AuditLog(**fields))
    _flush_fails(db_session)


def test_audit_logs_are_append_only(db_session: Session) -> None:
    """DB-CHG-003 audit_logs：追記だけ（4.2・NFR-05）。ORM から更新・削除しようとすると止まり、行は元のまま残る。"""
    row = AuditLog(entity_type="projects", entity_id=1, action="CREATE", after={"name": "プロジェクト"})
    db_session.add(row)
    db_session.commit()
    row_id = row.id

    row.after = {"name": "書き換え"}
    with pytest.raises(AuditLogImmutableError):
        db_session.flush()
    db_session.rollback()

    db_session.delete(row)
    with pytest.raises(AuditLogImmutableError):
        db_session.flush()
    db_session.rollback()

    db_session.expunge_all()
    kept = db_session.get(AuditLog, row_id)
    assert kept is not None and kept.after == {"name": "プロジェクト"}


# ---------------------------------------------------------------------------
# 移行
# ---------------------------------------------------------------------------


def _alembic_config(url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def _foreign_key_targets(engine: sa.Engine, table: str, column: str) -> set[str]:
    return {
        fk["referred_table"]
        for fk in sa.inspect(engine).get_foreign_keys(table)
        if fk["constrained_columns"] == [column]
    }


def test_migration_creates_chg_tables_matching_models(tmp_path: Path) -> None:
    """alembic upgrade head の結果に DB-CHG-001〜003 のテーブルと、先の表の列への外部キーがあり、モデルとの差が無い。

    0006 へ戻すとテーブルと外部キーが消え（先の表の列は残る）、また作れる。
    """
    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    command.upgrade(_alembic_config(url), "head")

    engine = create_db_engine(url)
    try:
        assert set(COLUMNS_4_1) <= set(sa.inspect(engine).get_table_names())
        assert _foreign_key_targets(engine, "scope_baselines", "change_request_id") == {"change_requests"}
        assert _foreign_key_targets(engine, "schedule_baselines", "change_request_id") == {"change_requests"}
        assert _foreign_key_targets(engine, "variance_notes", "corrective_action_id") == {"corrective_actions"}
        with engine.connect() as connection:
            diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
        assert diff == []
    finally:
        engine.dispose()

    command.downgrade(_alembic_config(url), "0006_eff")
    engine = create_db_engine(url)
    try:
        tables = set(sa.inspect(engine).get_table_names())
        assert not set(COLUMNS_4_1) & tables
        assert {"scope_baselines", "schedule_baselines", "variance_notes"} <= tables
        assert _foreign_key_targets(engine, "scope_baselines", "change_request_id") == set()
        assert _foreign_key_targets(engine, "schedule_baselines", "change_request_id") == set()
        assert _foreign_key_targets(engine, "variance_notes", "corrective_action_id") == set()
    finally:
        engine.dispose()
    command.upgrade(_alembic_config(url), "head")


def test_migration_enforces_chg_constraints(tmp_path: Path) -> None:
    """移行で作った DB でも、変更要求の番号の一意・状態と操作の検査・先の表の列の外部キーが効く（create_all だけに頼らない）。"""
    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    command.upgrade(_alembic_config(url), "head")
    engine = create_db_engine(url)
    try:
        with Session(engine) as session:
            project = _project(session)
            creator = _user(session)
            wp = _wp(session, project)
            _change_request(session, project, creator)
            session.commit()

            session.add(ChangeRequest(project_id=project.id, number=1, title="t", reason="r", created_by=creator.id))
            _flush_fails(session)
            session.add(ChangeRequest(project_id=project.id, number=2, title="t", reason="r", created_by=creator.id,
                                      status="CANCELLED"))
            _flush_fails(session)
            session.add(CorrectiveAction(project_id=project.id, cause="c", action="a", status="CANCELLED"))
            _flush_fails(session)
            session.add(AuditLog(entity_type="projects", entity_id=1, action="UPSERT"))
            _flush_fails(session)
            session.add(ScopeBaseline(project_id=project.id, version=1, reason="CHANGE_REQUEST",
                                      change_request_id=MISSING, created_by=creator.id))
            _flush_fails(session)
            session.add(VarianceNote(project_id=project.id, element_id=wp.id, reason="r", corrective_action_id=MISSING,
                                     created_by=creator.id))
            _flush_fails(session)
    finally:
        engine.dispose()
