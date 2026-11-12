"""工数（EFF）の群のテーブルの試験（設計書 3 章・4.1・4.2・5.8、要求仕様書 EFF-01・02・10、BR-11）：DB-EFF-001〜003。

列が 4.1 の行どおりであること、4.1 の一意制約（割当は活動と利用者の組、実績工数は利用者・活動・作業日の組）、
外部キー・値の制約（割当工数・実績工数は 0 以上、実績工数は 1 行 24 時間以下。RULE-EFF-005 の必要条件）が DB で効くこと、
差異の理由が是正措置（DB-CHG-002）を指せること、移行（alembic upgrade head）の結果がモデルと食い違わないことを確かめる。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
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
from app.models.cmn import User
from app.models.eff import TIME_ENTRY_MAX_HOURS, Assignment, TimeEntry, VarianceNote
from app.models.prg import StatusReport
from app.models.prj import Project
from app.models.sch import Activity
from app.models.wbs import WbsElement

# 設計書 4.1 の「主な列」
COLUMNS_4_1 = {
    "assignments": {"id", "activity_id", "user_id", "planned_hours"},
    "time_entries": {"id", "user_id", "activity_id", "work_date", "hours", "note"},
    "variance_notes": {
        "id", "project_id", "element_id", "status_report_id", "reason", "corrective_action_id", "created_by",
        "created_at",
    },
}

AT = datetime(2026, 12, 10, 9, 0, tzinfo=UTC)
MISSING = 999_999


def _user(session: Session, login_id: str = "executor") -> User:
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


def _activity(session: Session, project: Project, name: str = "資料作成", seq: int = 1) -> Activity:
    wp = _wp(session, project, seq)
    activity = Activity(project_id=project.id, work_package_id=wp.id, name=name, sort_order=1, duration_days=5)
    session.add(activity)
    session.flush()
    return activity


def _report(session: Session, project: Project, creator: User, report_date: date = date(2026, 12, 10)) -> StatusReport:
    report = StatusReport(project_id=project.id, report_date=report_date, created_by=creator.id)
    session.add(report)
    session.flush()
    return report


def _flush_fails(session: Session) -> None:
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


@pytest.mark.parametrize("table", sorted(COLUMNS_4_1))
def test_columns_match_design_4_1(table: str) -> None:
    """DB-EFF-001〜003 の列が設計書 4.1 の行どおりである。"""
    assert set(Base.metadata.tables[table].c.keys()) == COLUMNS_4_1[table]


# ---------------------------------------------------------------------------
# DB-EFF-001 assignments
# ---------------------------------------------------------------------------


def test_assignments_round_trip_several_assignees(db_session: Session) -> None:
    """DB-EFF-001 assignments：1 つの活動に複数の担当と割当工数を置ける（EFF-01）。割当工数の NULL は「決めていない」。"""
    project = _project(db_session)
    activity = _activity(db_session, project)
    first, second = _user(db_session, "executor-1"), _user(db_session, "executor-2")
    db_session.add_all([
        Assignment(activity_id=activity.id, user_id=first.id, planned_hours=Decimal("12.5")),
        Assignment(activity_id=activity.id, user_id=second.id),
    ])
    db_session.commit()
    db_session.expunge_all()

    rows = db_session.scalars(
        sa.select(Assignment).where(Assignment.activity_id == activity.id).order_by(Assignment.user_id)
    ).all()
    assert [(r.user_id, r.planned_hours) for r in rows] == [(first.id, Decimal("12.50")), (second.id, None)]


def test_assignments_unique_per_activity_and_user(db_session: Session) -> None:
    """DB-EFF-001 assignments：同じ活動に同じ利用者は 1 行（4.1 の組で一意）。別の活動・別の利用者なら置ける。"""
    project = _project(db_session)
    activity = _activity(db_session, project)
    other_activity = _activity(db_session, project, "関係者調整", seq=2)
    user, other_user = _user(db_session, "executor-1"), _user(db_session, "executor-2")
    db_session.add_all([
        Assignment(activity_id=activity.id, user_id=user.id),
        Assignment(activity_id=other_activity.id, user_id=user.id),
        Assignment(activity_id=activity.id, user_id=other_user.id),
    ])
    db_session.flush()
    db_session.add(Assignment(activity_id=activity.id, user_id=user.id, planned_hours=Decimal("8")))
    _flush_fails(db_session)


def test_assignments_accept_zero_planned_hours(db_session: Session) -> None:
    """DB-EFF-001 assignments：割当工数 0 は境界として受ける。"""
    project = _project(db_session)
    activity = _activity(db_session, project)
    user = _user(db_session)
    db_session.add(Assignment(activity_id=activity.id, user_id=user.id, planned_hours=Decimal("0")))
    db_session.flush()


@pytest.mark.parametrize(
    "override",
    [
        {"planned_hours": Decimal("-0.01")},
        {"activity_id": MISSING},
        {"user_id": MISSING},
        {"activity_id": None},
        {"user_id": None},
    ],
    ids=["negative-hours", "no-activity", "no-user", "null-activity", "null-user"],
)
def test_assignments_rejections(db_session: Session, override: dict[str, Any]) -> None:
    """DB-EFF-001 assignments：負の割当工数、無い・空の活動・利用者は DB が拒む。"""
    project = _project(db_session)
    activity = _activity(db_session, project)
    user = _user(db_session)
    values: dict[str, Any] = {"activity_id": activity.id, "user_id": user.id, "planned_hours": Decimal("8")}
    values.update(override)
    db_session.add(Assignment(**values))
    _flush_fails(db_session)


def test_assignments_block_deleting_their_activity(db_session: Session) -> None:
    """DB-EFF-001 assignments：割当のある活動は DB が消させない（ON DELETE なし。消す API はサービス層で割当を先に消す）。"""
    project = _project(db_session)
    activity = _activity(db_session, project)
    user = _user(db_session)
    db_session.add(Assignment(activity_id=activity.id, user_id=user.id))
    db_session.commit()
    db_session.delete(activity)
    _flush_fails(db_session)


# ---------------------------------------------------------------------------
# DB-EFF-002 time_entries
# ---------------------------------------------------------------------------


def test_time_entries_round_trip(db_session: Session) -> None:
    """DB-EFF-002 time_entries：日別・活動別の実績工数（人時の小数）と備考を持つ（EFF-02）。備考は任意。"""
    project = _project(db_session)
    activity = _activity(db_session, project)
    user = _user(db_session)
    db_session.add_all([
        TimeEntry(user_id=user.id, activity_id=activity.id, work_date=date(2026, 12, 1), hours=Decimal("7.75"),
                  note="資料の初版"),
        TimeEntry(user_id=user.id, activity_id=activity.id, work_date=date(2026, 12, 2), hours=Decimal("3")),
    ])
    db_session.commit()
    db_session.expunge_all()

    rows = db_session.scalars(sa.select(TimeEntry).order_by(TimeEntry.work_date)).all()
    assert [(r.user_id, r.activity_id, r.work_date, r.hours, r.note) for r in rows] == [
        (user.id, activity.id, date(2026, 12, 1), Decimal("7.75"), "資料の初版"),
        (user.id, activity.id, date(2026, 12, 2), Decimal("3.00"), None),
    ]


def test_time_entries_unique_per_user_activity_and_day(db_session: Session) -> None:
    """DB-EFF-002 time_entries：利用者＋活動＋作業日で一意（4.1）。別の日・別の活動・別の利用者なら書ける。"""
    project = _project(db_session)
    activity = _activity(db_session, project)
    other_activity = _activity(db_session, project, "関係者調整", seq=2)
    user, other_user = _user(db_session, "executor-1"), _user(db_session, "executor-2")
    day = date(2026, 12, 1)
    db_session.add_all([
        TimeEntry(user_id=user.id, activity_id=activity.id, work_date=day, hours=Decimal("4")),
        TimeEntry(user_id=user.id, activity_id=activity.id, work_date=date(2026, 12, 2), hours=Decimal("4")),
        TimeEntry(user_id=user.id, activity_id=other_activity.id, work_date=day, hours=Decimal("4")),
        TimeEntry(user_id=other_user.id, activity_id=activity.id, work_date=day, hours=Decimal("4")),
    ])
    db_session.flush()
    db_session.add(TimeEntry(user_id=user.id, activity_id=activity.id, work_date=day, hours=Decimal("1")))
    _flush_fails(db_session)


@pytest.mark.parametrize("hours", [Decimal("0"), Decimal("0.01"), Decimal(TIME_ENTRY_MAX_HOURS)], ids=["zero", "smallest", "max"])
def test_time_entries_accept_boundaries(db_session: Session, hours: Decimal) -> None:
    """DB-EFF-002 time_entries：0 時間・24 時間ちょうどは受ける（RULE-EFF-005 の「負の値の禁止」「1 日 24 時間以下」の境界）。"""
    project = _project(db_session)
    activity = _activity(db_session, project)
    user = _user(db_session)
    db_session.add(TimeEntry(user_id=user.id, activity_id=activity.id, work_date=date(2026, 12, 1), hours=hours))
    db_session.flush()
    assert TIME_ENTRY_MAX_HOURS == 24


@pytest.mark.parametrize(
    "override",
    [
        {"hours": Decimal("-0.01")},
        {"hours": Decimal("24.01")},
        {"hours": None},
        {"work_date": None},
        {"activity_id": MISSING},
        {"user_id": MISSING},
    ],
    ids=["negative", "over-24", "null-hours", "null-date", "no-activity", "no-user"],
)
def test_time_entries_rejections(db_session: Session, override: dict[str, Any]) -> None:
    """DB-EFF-002 time_entries：負の工数・24 時間を超える 1 行（EFF-02）、工数・作業日の空、無い活動・利用者は DB が拒む。"""
    project = _project(db_session)
    activity = _activity(db_session, project)
    user = _user(db_session)
    values: dict[str, Any] = {
        "user_id": user.id, "activity_id": activity.id, "work_date": date(2026, 12, 1), "hours": Decimal("8"),
    }
    values.update(override)
    db_session.add(TimeEntry(**values))
    _flush_fails(db_session)


def test_time_entries_block_deleting_their_activity(db_session: Session) -> None:
    """DB-EFF-002 time_entries：実績工数のある活動は DB が消させない（実績を黙って消さない。OQ-26 (2)）。"""
    project = _project(db_session)
    activity = _activity(db_session, project)
    user = _user(db_session)
    db_session.add(TimeEntry(user_id=user.id, activity_id=activity.id, work_date=date(2026, 12, 1), hours=Decimal("2")))
    db_session.commit()
    db_session.delete(activity)
    _flush_fails(db_session)


# ---------------------------------------------------------------------------
# DB-EFF-003 variance_notes
# ---------------------------------------------------------------------------


def test_variance_notes_round_trip_with_utc_created_at(db_session: Session) -> None:
    """DB-EFF-003 variance_notes：WBS要素の差異の理由・報告日・書いた利用者と時刻（UTC。2.4）を持つ（EFF-10）。"""
    project = _project(db_session)
    wp = _wp(db_session, project)
    pm = _user(db_session, "pm")
    report = _report(db_session, project, pm)
    db_session.add(VarianceNote(project_id=project.id, element_id=wp.id, status_report_id=report.id,
                                reason="仕様の追加で設計の工数が増えた", created_by=pm.id, created_at=AT))
    db_session.commit()
    db_session.expunge_all()

    note = db_session.scalars(sa.select(VarianceNote)).one()
    assert (note.project_id, note.element_id, note.status_report_id, note.reason, note.corrective_action_id,
            note.created_by) == (project.id, wp.id, report.id, "仕様の追加で設計の工数が増えた", None, pm.id)
    assert note.created_at == AT and note.created_at.utcoffset() == UTC.utcoffset(None)


def test_variance_notes_default_created_at_and_optional_report(db_session: Session) -> None:
    """DB-EFF-003 variance_notes：報告日は任意（報告日を指定しない分析。5.8）。作成の時刻の既定は UTC の今。"""
    project = _project(db_session)
    wp = _wp(db_session, project)
    pm = _user(db_session, "pm")
    db_session.add(VarianceNote(project_id=project.id, element_id=wp.id, reason="見積の誤り", created_by=pm.id))
    db_session.commit()
    db_session.expunge_all()

    note = db_session.scalars(sa.select(VarianceNote)).one()
    assert note.status_report_id is None
    assert note.created_at.tzinfo is not None and note.created_at.utcoffset() == UTC.utcoffset(None)


def test_variance_notes_hold_several_notes_per_element(db_session: Session) -> None:
    """DB-EFF-003 variance_notes：4.1 に一意の定めが無いので、同じ要素・報告日に複数の行を持てる（上書きか追記かは API-EFF-007。OQ-26 (1)）。"""
    project = _project(db_session)
    wp = _wp(db_session, project)
    pm = _user(db_session, "pm")
    report = _report(db_session, project, pm)
    for reason in ("一次の理由", "二次の理由"):
        db_session.add(VarianceNote(project_id=project.id, element_id=wp.id, status_report_id=report.id,
                                    reason=reason, created_by=pm.id))
    db_session.flush()
    assert db_session.scalar(sa.select(sa.func.count()).select_from(VarianceNote)) == 2


@pytest.mark.parametrize(
    "override",
    [
        {"reason": None},
        {"project_id": MISSING},
        {"element_id": MISSING},
        {"status_report_id": MISSING},
        {"created_by": MISSING},
        {"element_id": None},
    ],
    ids=["no-reason", "no-project", "no-element", "no-report", "no-creator", "null-element"],
)
def test_variance_notes_rejections(db_session: Session, override: dict[str, Any]) -> None:
    """DB-EFF-003 variance_notes：理由の無い行、無いプロジェクト・WBS要素・報告日・利用者、要素の空は DB が拒む。"""
    project = _project(db_session)
    wp = _wp(db_session, project)
    pm = _user(db_session, "pm")
    values: dict[str, Any] = {"project_id": project.id, "element_id": wp.id, "reason": "理由", "created_by": pm.id}
    values.update(override)
    db_session.add(VarianceNote(**values))
    _flush_fails(db_session)


def test_variance_notes_block_deleting_their_element(db_session: Session) -> None:
    """DB-EFF-003 variance_notes：差異の理由のある WBS要素は DB が消させない（ON DELETE なし）。"""
    project = _project(db_session)
    wp = _wp(db_session, project)
    pm = _user(db_session, "pm")
    db_session.add(VarianceNote(project_id=project.id, element_id=wp.id, reason="理由", created_by=pm.id))
    db_session.commit()
    db_session.delete(wp)
    _flush_fails(db_session)


def test_variance_notes_corrective_action_id_references_corrective_actions_once_it_exists() -> None:
    """DB-EFF-003 variance_notes：corrective_action_id は整数・空を許す。corrective_actions（DB-CHG-002）がモデルにあれば外部キーを持つ。

    corrective_actions は db:CHG のスコープが作る。そのとき外部キーを張り忘れるとこの試験が落ちる（ASSUMPTIONS.md A-15・A-19・A-27）。
    """
    column = Base.metadata.tables["variance_notes"].c.corrective_action_id
    assert isinstance(column.type, sa.Integer)
    assert column.nullable is True
    if "corrective_actions" in Base.metadata.tables:
        assert {fk.target_fullname for fk in column.foreign_keys} == {"corrective_actions.id"}
    else:
        assert column.foreign_keys == set()


# ---------------------------------------------------------------------------
# 移行
# ---------------------------------------------------------------------------


def _alembic_config(url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def test_migration_creates_eff_tables_matching_models(tmp_path: Path) -> None:
    """alembic upgrade head の結果に DB-EFF-001〜003 のテーブルがあり、モデルとの差が無い。0005 へ戻すと消え、また作れる。"""
    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    command.upgrade(_alembic_config(url), "head")

    engine = create_db_engine(url)
    try:
        assert set(COLUMNS_4_1) <= set(sa.inspect(engine).get_table_names())
        with engine.connect() as connection:
            diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
        assert diff == []
    finally:
        engine.dispose()

    command.downgrade(_alembic_config(url), "0005_prg")
    engine = create_db_engine(url)
    try:
        tables = set(sa.inspect(engine).get_table_names())
        assert not set(COLUMNS_4_1) & tables
        assert "progress_entries" in tables
    finally:
        engine.dispose()
    command.upgrade(_alembic_config(url), "head")


def test_migration_enforces_eff_uniques_and_checks(tmp_path: Path) -> None:
    """移行で作った DB でも、割当・実績工数の一意制約と、工数の範囲（0 以上・1 行 24 時間以下）の検査が効く（create_all だけに頼らない）。"""
    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    command.upgrade(_alembic_config(url), "head")
    engine = create_db_engine(url)
    try:
        with Session(engine) as session:
            project = _project(session)
            activity = _activity(session, project)
            user = _user(session)
            session.add(Assignment(activity_id=activity.id, user_id=user.id))
            session.add(TimeEntry(user_id=user.id, activity_id=activity.id, work_date=date(2026, 12, 1),
                                  hours=Decimal("24")))
            session.commit()

            session.add(Assignment(activity_id=activity.id, user_id=user.id))
            _flush_fails(session)
            session.add(TimeEntry(user_id=user.id, activity_id=activity.id, work_date=date(2026, 12, 1),
                                  hours=Decimal("1")))
            _flush_fails(session)
            session.add(TimeEntry(user_id=user.id, activity_id=activity.id, work_date=date(2026, 12, 2),
                                  hours=Decimal("25")))
            _flush_fails(session)
            session.add(Assignment(activity_id=activity.id, user_id=_user(session, "other").id,
                                   planned_hours=Decimal("-1")))
            _flush_fails(session)
    finally:
        engine.dispose()
