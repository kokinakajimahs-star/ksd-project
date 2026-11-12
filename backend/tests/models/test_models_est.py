"""見積（EST）の群のテーブルの試験（設計書 3 章・4.1・4.2・5.8、要求仕様書 EST-01・08）：DB-EST-001。

列が 4.1 の行どおりであること、見積を書くたびに行を足せる（見積値を変えた履歴が残る。EST-08）こと、
値の制約（対象の種類・入力の単位・手法は 4.1 の値、見積の値・入力の値は 0 以上。EST-01）と外部キーが DB で効くこと、
対象（WBS要素・活動）は外部キーで縛らず記録が残ること、移行（alembic upgrade head）の結果がモデルと食い違わないことを確かめる。
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
from app.domain.estimate import EstimateMethod, InputUnit, TargetType
from app.infra.db import Base, create_db_engine
from app.models.cmn import User
from app.models.est import ESTIMATE_INPUT_UNITS, ESTIMATE_METHODS, ESTIMATE_TARGET_TYPES, EstimateRecord
from app.models.prj import Project
from app.models.sch import Activity
from app.models.wbs import WbsElement

# 設計書 4.1 の「主な列」
COLUMNS_4_1 = {
    "estimate_records": {
        "id", "project_id", "target_type", "target_id", "hours", "input_value", "input_unit", "method",
        "assumptions", "references", "entered_by", "entered_at",
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


def _activity(session: Session, project: Project, seq: int = 1) -> Activity:
    wp = _wp(session, project, seq)
    activity = Activity(project_id=project.id, work_package_id=wp.id, name="資料作成", sort_order=1, duration_days=5)
    session.add(activity)
    session.flush()
    return activity


def _values(project: Project, user: User, target: int, /, **override: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "project_id": project.id, "target_type": "ELEMENT", "target_id": target, "hours": Decimal("16"),
        "input_value": Decimal("2"), "input_unit": "DAY", "entered_by": user.id,
    }
    values.update(override)
    return values


def _flush_fails(session: Session) -> None:
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


@pytest.mark.parametrize("table", sorted(COLUMNS_4_1))
def test_columns_match_design_4_1(table: str) -> None:
    """DB-EST-001 の列が設計書 4.1 の行どおりである。"""
    assert set(Base.metadata.tables[table].c.keys()) == COLUMNS_4_1[table]


def test_value_lists_match_design_4_1_and_estimate_rules() -> None:
    """DB-EST-001 estimate_records：対象の種類・入力の単位・手法の値は 4.1 のとおりで、RULE-EST-002 の値（app.domain.estimate）と同じ。"""
    assert ESTIMATE_TARGET_TYPES == ("ELEMENT", "ACTIVITY") == tuple(TargetType)
    assert ESTIMATE_INPUT_UNITS == ("HOUR", "DAY") == tuple(InputUnit)
    assert ESTIMATE_METHODS == ("BOTTOM_UP", "ANALOGOUS", "THREE_POINT", "EXPERT", "OTHER") == tuple(EstimateMethod)


def test_estimate_records_round_trip_with_basis_and_utc_entered_at(db_session: Session) -> None:
    """DB-EST-001 estimate_records：換算した人時・入れたままの値と単位・手法・前提・参照資料・書いた利用者と時刻（UTC。2.4）を持つ（EST-01・08）。"""
    project = _project(db_session)
    wp = _wp(db_session, project)
    planner = _user(db_session)
    db_session.add(EstimateRecord(**_values(
        project, planner, wp.id, hours=Decimal("12.5"), input_value=Decimal("1.25"), input_unit="DAY",
        method="ANALOGOUS", assumptions="前回の類似案件と同じ体制", references="P-000 の実績表", entered_at=AT,
    )))
    db_session.commit()
    db_session.expunge_all()

    record = db_session.scalars(sa.select(EstimateRecord)).one()
    assert (record.project_id, record.target_type, record.target_id, record.hours, record.input_value,
            record.input_unit, record.method, record.assumptions, record.references, record.entered_by) == (
        project.id, "ELEMENT", wp.id, Decimal("12.50"), Decimal("1.25"), "DAY", "ANALOGOUS",
        "前回の類似案件と同じ体制", "P-000 の実績表", planner.id,
    )
    assert record.entered_at == AT and record.entered_at.utcoffset() == UTC.utcoffset(None)


def test_estimate_records_keep_history_per_target(db_session: Session) -> None:
    """DB-EST-001 estimate_records：同じ対象に何行でも足せる（見積値を変えた履歴が残る。EST-08）。手法・前提・参照資料は任意、時刻の既定は UTC の今。"""
    project = _project(db_session)
    activity = _activity(db_session, project)
    planner = _user(db_session)
    for hours in (Decimal("8"), Decimal("10"), Decimal("6")):
        db_session.add(EstimateRecord(**_values(project, planner, activity.id, target_type="ACTIVITY", hours=hours,
                                                input_value=hours, input_unit="HOUR")))
    db_session.commit()
    db_session.expunge_all()

    rows = db_session.scalars(
        sa.select(EstimateRecord)
        .where(EstimateRecord.target_type == "ACTIVITY", EstimateRecord.target_id == activity.id)
        .order_by(EstimateRecord.id)
    ).all()
    assert [r.hours for r in rows] == [Decimal("8.00"), Decimal("10.00"), Decimal("6.00")]
    assert all(r.method is None and r.assumptions is None and r.references is None for r in rows)
    assert all(r.entered_at.tzinfo is not None and r.entered_at.utcoffset() == UTC.utcoffset(None) for r in rows)


@pytest.mark.parametrize("method", ESTIMATE_METHODS)
def test_estimate_records_accept_each_method(db_session: Session, method: str) -> None:
    """DB-EST-001 estimate_records：EST-08 の 5 つの手法はどれも書ける。"""
    project = _project(db_session)
    wp = _wp(db_session, project)
    planner = _user(db_session)
    db_session.add(EstimateRecord(**_values(project, planner, wp.id, method=method)))
    db_session.flush()


def test_estimate_records_accept_zero(db_session: Session) -> None:
    """DB-EST-001 estimate_records：見積の値・入力の値の 0 は境界として受ける（負の値だけを拒む。EST-01）。"""
    project = _project(db_session)
    wp = _wp(db_session, project)
    planner = _user(db_session)
    db_session.add(EstimateRecord(**_values(project, planner, wp.id, hours=Decimal("0"), input_value=Decimal("0"))))
    db_session.flush()


@pytest.mark.parametrize(
    "override",
    [
        {"hours": Decimal("-0.01")},
        {"input_value": Decimal("-0.01")},
        {"target_type": "PROJECT"},
        {"input_unit": "WEEK"},
        {"method": "GUESS"},
        {"hours": None},
        {"input_value": None},
        {"input_unit": None},
        {"target_type": None},
        {"target_id": None},
        {"project_id": MISSING},
        {"entered_by": MISSING},
        {"entered_by": None},
    ],
    ids=[
        "negative-hours", "negative-input", "unknown-target-type", "unknown-unit", "unknown-method", "null-hours",
        "null-input", "null-unit", "null-target-type", "null-target-id", "no-project", "no-user", "null-user",
    ],
)
def test_estimate_records_rejections(db_session: Session, override: dict[str, Any]) -> None:
    """DB-EST-001 estimate_records：負の値（EST-01）、4.1 に無い対象の種類・単位・手法、必須の列の空、無いプロジェクト・利用者は DB が拒む。"""
    project = _project(db_session)
    wp = _wp(db_session, project)
    planner = _user(db_session)
    db_session.add(EstimateRecord(**_values(project, planner, wp.id, **override)))
    _flush_fails(db_session)


def test_estimate_records_target_has_no_foreign_key_and_survives_its_target(db_session: Session) -> None:
    """DB-EST-001 estimate_records：target_id は対象の種類で指す表が変わるので外部キーを持たない。対象を消しても記録（履歴）は残る。"""
    column = Base.metadata.tables["estimate_records"].c.target_id
    assert isinstance(column.type, sa.Integer) and column.nullable is False
    assert column.foreign_keys == set()

    project = _project(db_session)
    wp = _wp(db_session, project)
    planner = _user(db_session)
    db_session.add(EstimateRecord(**_values(project, planner, wp.id)))
    db_session.commit()
    db_session.delete(wp)
    db_session.commit()
    assert db_session.scalar(sa.select(sa.func.count()).select_from(EstimateRecord)) == 1


def test_estimate_records_block_deleting_their_project(db_session: Session) -> None:
    """DB-EST-001 estimate_records：記録のあるプロジェクトは DB が消させない（ON DELETE なし）。"""
    project = _project(db_session)
    wp = _wp(db_session, project)
    planner = _user(db_session)
    db_session.add(EstimateRecord(**_values(project, planner, wp.id)))
    db_session.commit()
    db_session.delete(wp)
    db_session.flush()
    db_session.delete(project)
    _flush_fails(db_session)


# ---------------------------------------------------------------------------
# 移行
# ---------------------------------------------------------------------------


def _alembic_config(url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def test_migration_creates_est_tables_matching_models(tmp_path: Path) -> None:
    """alembic upgrade head の結果に DB-EST-001 のテーブルがあり、モデルとの差が無い。0007 へ戻すと消え、また作れる。"""
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

    command.downgrade(_alembic_config(url), "0007_chg")
    engine = create_db_engine(url)
    try:
        tables = set(sa.inspect(engine).get_table_names())
        assert not set(COLUMNS_4_1) & tables
        assert "change_requests" in tables
    finally:
        engine.dispose()
    command.upgrade(_alembic_config(url), "head")


def test_migration_enforces_est_checks(tmp_path: Path) -> None:
    """移行で作った DB でも、負の値・4.1 に無い対象の種類・単位・手法の検査が効く（create_all だけに頼らない）。"""
    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    command.upgrade(_alembic_config(url), "head")
    engine = create_db_engine(url)
    try:
        with Session(engine) as session:
            project = _project(session)
            wp = _wp(session, project)
            planner = _user(session)
            session.add(EstimateRecord(**_values(project, planner, wp.id, method="THREE_POINT",
                                                 references="見積根拠.xlsx")))
            session.add(EstimateRecord(**_values(project, planner, wp.id)))
            session.commit()

            for override in ({"hours": Decimal("-1")}, {"input_value": Decimal("-1")}, {"target_type": "PROJECT"},
                             {"input_unit": "WEEK"}, {"method": "GUESS"}):
                session.add(EstimateRecord(**_values(project, planner, wp.id, **override)))
                _flush_fails(session)
    finally:
        engine.dispose()
