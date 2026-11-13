"""スケジュール（SCH）の群のテーブルの試験（設計書 3 章・4.1・4.2・5.5、要求仕様書 BR-05・06・12）：DB-SCH-001〜004。

列が 4.1 の行どおりであること、既定値、一意制約・外部キー・値の制約が DB で効くこと、マイルストーンの期間（4.2）、
依存が R1 の FS・ラグ 0 に限られること（OQ-04）、ベースラインの写しが後から変わらないこと（BR-12）と、
写した活動を消すときの扱い（A-19）、移行（alembic upgrade head）の結果がモデルと食い違わないことを確かめる。
"""

from __future__ import annotations

import typing
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
from app.models.chg import ChangeRequest
from app.models.cmn import User
from app.models.prj import Project
from app.models.sch import (
    PROGRESS_METHODS,
    Activity,
    ActivityDependency,
    ScheduleBaseline,
    ScheduleBaselineItem,
)
from app.models.wbs import WbsElement
from app.schemas.projects import ProgressMethod

# 設計書 4.1 の「主な列」
COLUMNS_4_1 = {
    "activities": {
        "id", "project_id", "work_package_id", "name", "sort_order", "requested_start", "duration_days", "is_milestone",
        "estimate_hours", "progress_method", "start_date", "finish_date", "early_start", "early_finish", "late_start",
        "late_finish", "total_float", "is_critical",
    },
    "activity_dependencies": {"id", "project_id", "predecessor_id", "successor_id", "dep_type", "lag_days"},
    "schedule_baselines": {"id", "project_id", "version", "name", "reason", "change_request_id", "created_by", "created_at"},
    "schedule_baseline_items": {
        "id", "schedule_baseline_id", "item_type", "activity_id", "element_id", "wbs_code", "name", "start_date",
        "finish_date", "duration_days", "is_milestone", "estimate_hours",
    },
}

AT = datetime(2026, 11, 11, 9, 0, tzinfo=UTC)


def _user(session: Session, login_id: str = "pm") -> User:
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


def _wp(session: Session, project: Project, code: str = "1.1", seq: int = 1) -> WbsElement:
    element = WbsElement(
        project_id=project.id, kind="WORK_PACKAGE", name=f"WP {code}", wbs_code=code, sibling_seq=seq, sort_order=seq
    )
    session.add(element)
    session.flush()
    return element


def _activity_values(project: Project, wp: WbsElement, **override: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "project_id": project.id, "work_package_id": wp.id, "name": "画面の設計", "sort_order": 1, "duration_days": 5,
    }
    values.update(override)
    return values


def _activity(session: Session, project: Project, wp: WbsElement, **override: Any) -> Activity:
    activity = Activity(**_activity_values(project, wp, **override))
    session.add(activity)
    session.flush()
    return activity


def _flush_fails(session: Session) -> None:
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


@pytest.mark.parametrize("table", sorted(COLUMNS_4_1))
def test_columns_match_design_4_1(table: str) -> None:
    """DB-SCH-001〜004 の列が設計書 4.1 の行どおりである。"""
    assert set(Base.metadata.tables[table].c.keys()) == COLUMNS_4_1[table]


# ---------------------------------------------------------------------------
# DB-SCH-001 activities
# ---------------------------------------------------------------------------


def test_activities_input_and_defaults(db_session: Session) -> None:
    """DB-SCH-001 activities：WP の配下に置き、入力（希望開始日・期間・見積・算定方式）が往復する。計算の結果は空・非クリティカルから始まる。"""
    project = _project(db_session)
    wp = _wp(db_session, project)
    activity = _activity(
        db_session, project, wp, requested_start=date(2026, 12, 3), estimate_hours=Decimal("16.50"),
        progress_method="FIFTY_FIFTY",
    )
    plain = _activity(db_session, project, wp, name="素の活動", sort_order=2)
    db_session.commit()
    db_session.expunge_all()

    loaded = db_session.get(Activity, activity.id)
    assert loaded is not None
    assert (loaded.project_id, loaded.work_package_id, loaded.name, loaded.sort_order) == (project.id, wp.id, "画面の設計", 1)
    assert (loaded.requested_start, loaded.duration_days, loaded.is_milestone) == (date(2026, 12, 3), 5, False)
    assert (loaded.estimate_hours, loaded.progress_method) == (Decimal("16.50"), "FIFTY_FIFTY")
    assert (loaded.start_date, loaded.finish_date, loaded.early_start, loaded.early_finish) == (None, None, None, None)
    assert (loaded.late_start, loaded.late_finish, loaded.total_float, loaded.is_critical) == (None, None, None, False)

    loaded_plain = db_session.get(Activity, plain.id)
    assert loaded_plain is not None
    # 算定方式の NULL はプロジェクトの既定（4.1）。見積の NULL は未見積
    assert (loaded_plain.requested_start, loaded_plain.estimate_hours, loaded_plain.progress_method) == (None, None, None)


def test_activities_store_schedule_results(db_session: Session) -> None:
    """DB-SCH-001 activities：日程計算（RULE-SCH-002・003）の結果の日付・トータルフロート（稼働日）・クリティカルを保存できる。"""
    project = _project(db_session)
    activity = _activity(db_session, project, _wp(db_session, project))
    activity.start_date, activity.finish_date = date(2026, 12, 1), date(2026, 12, 7)
    activity.early_start, activity.early_finish = date(2026, 12, 1), date(2026, 12, 7)
    activity.late_start, activity.late_finish = date(2026, 12, 3), date(2026, 12, 9)
    activity.total_float, activity.is_critical = 2, False
    db_session.commit()
    db_session.expunge_all()

    loaded = db_session.get(Activity, activity.id)
    assert loaded is not None
    assert (loaded.start_date, loaded.finish_date, loaded.early_start, loaded.early_finish) == (
        date(2026, 12, 1), date(2026, 12, 7), date(2026, 12, 1), date(2026, 12, 7),
    )
    assert (loaded.late_start, loaded.late_finish, loaded.total_float, loaded.is_critical) == (
        date(2026, 12, 3), date(2026, 12, 9), 2, False,
    )


def test_activities_milestone_has_zero_duration(db_session: Session) -> None:
    """DB-SCH-001 activities：マイルストーンは期間 0（4.2）、それ以外の活動は期間 1 以上を受け付ける（境界）。"""
    project = _project(db_session)
    wp = _wp(db_session, project)
    _activity(db_session, project, wp, name="出荷判定", is_milestone=True, duration_days=0)
    _activity(db_session, project, wp, name="1 日の作業", duration_days=1)
    db_session.commit()


@pytest.mark.parametrize(
    "override",
    [
        {"is_milestone": True, "duration_days": 1},
        {"is_milestone": False, "duration_days": 0},
        {"duration_days": -1},
        {"estimate_hours": Decimal("-0.01")},
        {"progress_method": "MANUAL"},
    ],
    ids=["milestone-with-duration", "activity-without-duration", "negative-duration", "negative-hours", "progress-method"],
)
def test_activities_reject_out_of_range(db_session: Session, override: dict[str, Any]) -> None:
    """DB-SCH-001 activities：期間のあるマイルストーン・期間 0 の活動・負の期間・負の見積・BR-06 に無い算定方式は DB が拒む。"""
    project = _project(db_session)
    db_session.add(Activity(**_activity_values(project, _wp(db_session, project), **override)))
    _flush_fails(db_session)


def test_activities_accept_zero_estimate(db_session: Session) -> None:
    """DB-SCH-001 activities：見積 0 は受け付ける（負の境界の内側）。"""
    project = _project(db_session)
    _activity(db_session, project, _wp(db_session, project), estimate_hours=Decimal("0"))
    db_session.commit()


@pytest.mark.parametrize("method", PROGRESS_METHODS)
def test_activities_accept_seven_progress_methods(db_session: Session, method: str) -> None:
    """DB-SCH-001 activities：算定方式は要求仕様書 BR-06 の 7 つのどれか（PRG-05）。"""
    project = _project(db_session)
    _activity(db_session, project, _wp(db_session, project), progress_method=method)
    db_session.commit()


def test_progress_methods_match_project_default_codes() -> None:
    """DB-SCH-001 activities：活動の算定方式の一覧は、プロジェクトの既定（projects.settings.progress_method_default）と同じ 7 つ。"""
    assert set(PROGRESS_METHODS) == set(typing.get_args(ProgressMethod))
    assert len(PROGRESS_METHODS) == 7


@pytest.mark.parametrize("missing", ["project_id", "work_package_id", "name", "sort_order", "duration_days"])
def test_activities_required_columns(db_session: Session, missing: str) -> None:
    """DB-SCH-001 activities：プロジェクト・置き場の WP・名称・表示順・期間は必須。"""
    project = _project(db_session)
    values = _activity_values(project, _wp(db_session, project))
    del values[missing]
    db_session.add(Activity(**values))
    _flush_fails(db_session)


@pytest.mark.parametrize("column", ["project_id", "work_package_id"])
def test_activities_references(db_session: Session, column: str) -> None:
    """DB-SCH-001 activities：project_id は projects、work_package_id は wbs_elements を指す（内部キー。改番しても切れない）。"""
    project = _project(db_session)
    db_session.add(Activity(**_activity_values(project, _wp(db_session, project), **{column: 9999})))
    _flush_fails(db_session)


def test_activities_keep_link_when_wp_is_renumbered(db_session: Session) -> None:
    """DB-SCH-001 activities：WP を改番しても活動の置き場は変わらない（3 章。他のテーブルは内部キーで要素を指す。SCH-01）。"""
    project = _project(db_session)
    wp = _wp(db_session, project, code="1.1")
    activity = _activity(db_session, project, wp)
    db_session.commit()
    wp.wbs_code = "2.3"
    db_session.commit()
    db_session.expunge_all()
    loaded = db_session.get(Activity, activity.id)
    assert loaded is not None and loaded.work_package_id == wp.id


def test_activities_block_deleting_their_work_package(db_session: Session) -> None:
    """DB-SCH-001 activities：活動のある WP は DB が消させない（ON DELETE なし。API-WBS-005 の ELEMENT_HAS_ACTIVITIES と同じ向き）。"""
    project = _project(db_session)
    wp = _wp(db_session, project)
    _activity(db_session, project, wp)
    db_session.commit()
    db_session.delete(wp)
    _flush_fails(db_session)


def test_activities_server_defaults_apply_to_raw_insert(db_session: Session) -> None:
    """DB-SCH-001 activities：is_milestone・is_critical（偽）は DB の既定値としても入る（ORM を通らない書き込み）。"""
    project = _project(db_session)
    wp = _wp(db_session, project)
    db_session.execute(
        sa.text(
            "INSERT INTO activities (project_id, work_package_id, name, sort_order, duration_days) "
            "VALUES (:p, :w, '生', 1, 3)"
        ),
        {"p": project.id, "w": wp.id},
    )
    row = db_session.execute(sa.text("SELECT is_milestone, is_critical FROM activities")).one()
    assert (bool(row[0]), bool(row[1])) == (False, False)


# ---------------------------------------------------------------------------
# DB-SCH-002 activity_dependencies
# ---------------------------------------------------------------------------


def _two_activities(session: Session) -> tuple[Project, Activity, Activity]:
    project = _project(session)
    wp = _wp(session, project)
    return project, _activity(session, project, wp, name="先行"), _activity(session, project, wp, name="後続", sort_order=2)


def test_dependencies_default_to_fs_without_lag(db_session: Session) -> None:
    """DB-SCH-002 activity_dependencies：種類の既定は FS、ラグの既定は 0（R1。OQ-04）。先行 → 後続の向きを持つ。"""
    project, first, second = _two_activities(db_session)
    dependency = ActivityDependency(project_id=project.id, predecessor_id=first.id, successor_id=second.id)
    db_session.add(dependency)
    db_session.commit()
    db_session.expunge_all()
    loaded = db_session.get(ActivityDependency, dependency.id)
    assert loaded is not None
    assert (loaded.predecessor_id, loaded.successor_id, loaded.dep_type, loaded.lag_days) == (first.id, second.id, "FS", 0)


def test_dependencies_server_defaults_apply_to_raw_insert(db_session: Session) -> None:
    """DB-SCH-002 activity_dependencies：dep_type（FS）・lag_days（0）は DB の既定値としても入る。"""
    project, first, second = _two_activities(db_session)
    db_session.execute(
        sa.text("INSERT INTO activity_dependencies (project_id, predecessor_id, successor_id) VALUES (:p, :a, :b)"),
        {"p": project.id, "a": first.id, "b": second.id},
    )
    assert tuple(db_session.execute(sa.text("SELECT dep_type, lag_days FROM activity_dependencies")).one()) == ("FS", 0)


def test_dependencies_unique_pair_but_reverse_is_another_row(db_session: Session) -> None:
    """DB-SCH-002 activity_dependencies：predecessor_id＋successor_id で一意。逆向きは別の組（循環は RULE-SCH-001 が検出する）。"""
    project, first, second = _two_activities(db_session)
    db_session.add(ActivityDependency(project_id=project.id, predecessor_id=first.id, successor_id=second.id))
    db_session.commit()
    db_session.add(ActivityDependency(project_id=project.id, predecessor_id=first.id, successor_id=second.id))
    _flush_fails(db_session)

    db_session.add(ActivityDependency(project_id=project.id, predecessor_id=second.id, successor_id=first.id))
    db_session.commit()


@pytest.mark.parametrize(
    "override",
    [
        {"dep_type": "SS"},
        {"dep_type": "FF"},
        {"dep_type": "SF"},
        {"lag_days": 1},
        {"lag_days": -1},
        {"self": True},
        {"project_id": 9999},
        {"predecessor_id": 9999},
        {"successor_id": 9999},
    ],
    ids=["SS", "FF", "SF", "lag-1", "lag-minus-1", "self", "no-project", "no-predecessor", "no-successor"],
)
def test_dependencies_rejections(db_session: Session, override: dict[str, Any]) -> None:
    """DB-SCH-002 activity_dependencies：R1 は FS・ラグ 0 だけ（4.1・OQ-04）。自分への依存、無いプロジェクト・活動は DB が拒む。"""
    project, first, second = _two_activities(db_session)
    values: dict[str, Any] = {"project_id": project.id, "predecessor_id": first.id, "successor_id": second.id}
    if override.pop("self", False):
        values["successor_id"] = first.id
    values.update(override)
    db_session.add(ActivityDependency(**values))
    _flush_fails(db_session)


def test_dependencies_block_deleting_linked_activity(db_session: Session) -> None:
    """DB-SCH-002 activity_dependencies：依存のある活動だけを消すことはできない（活動を消すサービスが依存の行から消す。ON DELETE なし）。"""
    project, first, second = _two_activities(db_session)
    db_session.add(ActivityDependency(project_id=project.id, predecessor_id=first.id, successor_id=second.id))
    db_session.commit()
    db_session.delete(second)
    _flush_fails(db_session)


# ---------------------------------------------------------------------------
# DB-SCH-003 schedule_baselines・DB-SCH-004 schedule_baseline_items
# ---------------------------------------------------------------------------


def _baseline(
    session: Session, project: Project, creator: User, version: int = 1, reason: str = "MANUAL", **extra: Any
) -> ScheduleBaseline:
    baseline = ScheduleBaseline(
        project_id=project.id, version=version, name=f"第 {version} 版", reason=reason, created_by=creator.id, **extra
    )
    session.add(baseline)
    session.flush()
    return baseline


def test_schedule_baselines_versions_increase_per_project(db_session: Session) -> None:
    """DB-SCH-003 schedule_baselines：版はプロジェクトの中の連番で、project_id＋version で一意（上書きせず増やす。SCH-17・BR-12）。"""
    project = _project(db_session, "P-001")
    other = _project(db_session, "P-002")
    creator = _user(db_session, "pm")
    first = _baseline(db_session, project, creator, 1, created_at=AT)
    _baseline(db_session, project, creator, 2, reason="RE_APPROVAL")
    # 変更要求（DB-CHG-001）への外部キーを張った（db:CHG。A-31）ので、実在する変更要求を指す
    change_request = ChangeRequest(project_id=project.id, number=1, title="変更", reason="要望", created_by=creator.id)
    db_session.add(change_request)
    db_session.flush()
    _baseline(db_session, project, creator, 3, reason="CHANGE_REQUEST", change_request_id=change_request.id)
    _baseline(db_session, other, creator, 1)
    db_session.commit()
    db_session.expunge_all()

    loaded = db_session.get(ScheduleBaseline, first.id)
    assert loaded is not None
    assert (loaded.version, loaded.name, loaded.reason, loaded.change_request_id, loaded.created_by, loaded.created_at) == (
        1, "第 1 版", "MANUAL", None, creator.id, AT,
    )
    versions = db_session.scalars(
        sa.select(ScheduleBaseline.version).where(ScheduleBaseline.project_id == project.id).order_by(ScheduleBaseline.version)
    ).all()
    assert list(versions) == [1, 2, 3]

    db_session.add(ScheduleBaseline(project_id=project.id, version=2, name="重複", reason="MANUAL", created_by=creator.id))
    _flush_fails(db_session)


def test_schedule_baselines_created_at_defaults_to_utc_now(db_session: Session) -> None:
    """DB-SCH-003 schedule_baselines：保存の時刻は省けば今の時刻（UTC の aware な値）が入る。"""
    project = _project(db_session)
    baseline = _baseline(db_session, project, _user(db_session))
    db_session.commit()
    db_session.expunge_all()
    loaded = db_session.get(ScheduleBaseline, baseline.id)
    assert loaded is not None and loaded.created_at.tzinfo is not None


@pytest.mark.parametrize(
    "override",
    [
        {"reason": "INITIAL_APPROVAL"},
        {"version": 0},
        {"name": None},
        {"project_id": 9999},
        {"created_by": 9999},
    ],
    ids=["reason", "version-0", "no-name", "no-project", "no-creator"],
)
def test_schedule_baselines_rejections(db_session: Session, override: dict[str, Any]) -> None:
    """DB-SCH-003 schedule_baselines：4.1 に無い理由、0 の版、名前の無い版、無いプロジェクト・利用者は DB が拒む。"""
    project = _project(db_session)
    creator = _user(db_session)
    values: dict[str, Any] = {
        "project_id": project.id, "version": 1, "name": "初版", "reason": "MANUAL", "created_by": creator.id,
    }
    values.update(override)
    db_session.add(ScheduleBaseline(**values))
    _flush_fails(db_session)


def test_schedule_baselines_change_request_id_references_change_requests_once_it_exists() -> None:
    """DB-SCH-003 schedule_baselines：change_request_id は整数・空を許す。change_requests（DB-CHG-001）がモデルにあれば外部キーを持つ。

    change_requests は db:CHG のスコープが作る。そのとき外部キーを張り忘れるとこの試験が落ちる（ASSUMPTIONS.md A-15・A-19）。
    """
    column = Base.metadata.tables["schedule_baselines"].c.change_request_id
    assert isinstance(column.type, sa.Integer)
    assert column.nullable is True
    if "change_requests" in Base.metadata.tables:
        assert {fk.target_fullname for fk in column.foreign_keys} == {"change_requests.id"}
    else:
        assert column.foreign_keys == set()


def test_schedule_baseline_items_snapshot_round_trip(db_session: Session) -> None:
    """DB-SCH-004 schedule_baseline_items：版に活動（開始日・終了日・期間・見積。SCH-16）と WP（EFF-08 の基準の値）の写しを持つ。"""
    project = _project(db_session)
    creator = _user(db_session)
    wp = _wp(db_session, project)
    no_activity_wp = _wp(db_session, project, code="1.2", seq=2)
    activity = _activity(db_session, project, wp, estimate_hours=Decimal("16"))
    milestone = _activity(db_session, project, wp, name="出荷判定", sort_order=2, is_milestone=True, duration_days=0)
    baseline = _baseline(db_session, project, creator)
    db_session.add_all(
        [
            ScheduleBaselineItem(
                schedule_baseline_id=baseline.id, item_type="ACTIVITY", activity_id=activity.id, element_id=wp.id,
                wbs_code="1.1", name="画面の設計", start_date=date(2026, 12, 1), finish_date=date(2026, 12, 7),
                duration_days=5, estimate_hours=Decimal("16.00"),
            ),
            ScheduleBaselineItem(
                schedule_baseline_id=baseline.id, item_type="ACTIVITY", activity_id=milestone.id, element_id=wp.id,
                wbs_code="1.1", name="出荷判定", start_date=date(2026, 12, 7), finish_date=date(2026, 12, 7),
                duration_days=0, is_milestone=True,
            ),
            ScheduleBaselineItem(
                schedule_baseline_id=baseline.id, item_type="WORK_PACKAGE", element_id=no_activity_wp.id, wbs_code="1.2",
                name="WP 1.2", estimate_hours=Decimal("8"),
            ),
        ]
    )
    db_session.commit()
    db_session.expunge_all()

    loaded = db_session.scalars(
        sa.select(ScheduleBaselineItem)
        .where(ScheduleBaselineItem.schedule_baseline_id == baseline.id)
        .order_by(ScheduleBaselineItem.id)
    ).all()
    assert [
        (r.item_type, r.activity_id, r.element_id, r.wbs_code, r.start_date, r.finish_date, r.duration_days, r.is_milestone,
         r.estimate_hours)
        for r in loaded
    ] == [
        ("ACTIVITY", activity.id, wp.id, "1.1", date(2026, 12, 1), date(2026, 12, 7), 5, False, Decimal("16.00")),
        ("ACTIVITY", milestone.id, wp.id, "1.1", date(2026, 12, 7), date(2026, 12, 7), 0, True, None),
        ("WORK_PACKAGE", None, no_activity_wp.id, "1.2", None, None, None, False, Decimal("8.00")),
    ]


def test_schedule_baseline_items_keep_snapshot_when_activity_changes(db_session: Session) -> None:
    """DB-SCH-004 schedule_baseline_items：保存の後に活動の日程・見積を変えても、写しの値は変わらない（SCH-16・EFF-08・BR-12）。"""
    project = _project(db_session)
    wp = _wp(db_session, project)
    activity = _activity(db_session, project, wp, estimate_hours=Decimal("16"))
    activity.start_date, activity.finish_date = date(2026, 12, 1), date(2026, 12, 7)
    baseline = _baseline(db_session, project, _user(db_session))
    item = ScheduleBaselineItem(
        schedule_baseline_id=baseline.id, item_type="ACTIVITY", activity_id=activity.id, element_id=wp.id, wbs_code="1.1",
        name="画面の設計", start_date=date(2026, 12, 1), finish_date=date(2026, 12, 7), duration_days=5,
        estimate_hours=Decimal("16"),
    )
    db_session.add(item)
    db_session.commit()

    activity.name = "変えた名称"
    activity.duration_days = 8
    activity.start_date, activity.finish_date = date(2026, 12, 2), date(2026, 12, 11)
    activity.estimate_hours = Decimal("24")
    db_session.commit()
    db_session.expunge_all()
    loaded = db_session.get(ScheduleBaselineItem, item.id)
    assert loaded is not None
    assert (loaded.name, loaded.start_date, loaded.finish_date, loaded.duration_days, loaded.estimate_hours) == (
        "画面の設計", date(2026, 12, 1), date(2026, 12, 7), 5, Decimal("16.00"),
    )


def test_schedule_baseline_items_survive_deleted_activity(db_session: Session) -> None:
    """DB-SCH-004 schedule_baseline_items：写しの活動・WP の列は外部キーを張らない写しの値（設計書 1.1 の 4.2。CR-002）。

    写した活動・WP を消しても写しは変わらず、消した活動の内部キーは再利用しない（AUTOINCREMENT。SCH-18 の削除）。
    """
    project = _project(db_session)
    wp = _wp(db_session, project)
    activity = _activity(db_session, project, wp)
    baseline = _baseline(db_session, project, _user(db_session))
    item = ScheduleBaselineItem(
        schedule_baseline_id=baseline.id, item_type="ACTIVITY", activity_id=activity.id, element_id=wp.id, wbs_code="1.1",
        name="画面の設計", duration_days=5,
    )
    db_session.add(item)
    db_session.commit()
    wp_id, activity_id, item_id = wp.id, activity.id, item.id

    db_session.delete(activity)
    db_session.flush()
    db_session.delete(wp)
    db_session.commit()
    db_session.expunge_all()
    assert db_session.get(Activity, activity_id) is None
    survived = db_session.get(ScheduleBaselineItem, item_id)
    assert survived is not None
    assert (survived.item_type, survived.activity_id, survived.element_id, survived.name, survived.duration_days) == (
        "ACTIVITY", activity_id, wp_id, "画面の設計", 5,
    )
    again = _activity(db_session, project, _wp(db_session, project))
    assert again.id > activity_id


@pytest.mark.parametrize(
    "override",
    [
        {"item_type": "MILESTONE"},
        {"item_type": "WORK_PACKAGE"},
        {"duration_days": -1},
        {"estimate_hours": Decimal("-1")},
        {"schedule_baseline_id": 9999},
        {"wbs_code": None},
        {"name": None},
        {"item_type": None},
    ],
    ids=[
        "item-type", "work-package-with-activity", "negative-duration", "negative-hours", "no-baseline",
        "no-code", "no-name", "no-item-type",
    ],
)
def test_schedule_baseline_items_rejections(db_session: Session, override: dict[str, Any]) -> None:
    """DB-SCH-004 schedule_baseline_items：4.1 に無い種類、活動を指す WP の項目、負の期間・見積、無い版、WBS ID・名称・種類の無い写しは DB が拒む。

    写した活動・WP の ID は今は無い行を指してよい（外部キーを張らない。設計書 1.1 の 4.2）。
    """
    project = _project(db_session)
    wp = _wp(db_session, project)
    activity = _activity(db_session, project, wp)
    baseline = _baseline(db_session, project, _user(db_session))
    values: dict[str, Any] = {
        "schedule_baseline_id": baseline.id, "item_type": "ACTIVITY", "activity_id": activity.id, "element_id": wp.id,
        "wbs_code": "1.1", "name": "x",
    }
    values.update(override)
    db_session.add(ScheduleBaselineItem(**values))
    _flush_fails(db_session)


def test_schedule_baseline_items_accept_lower_bounds(db_session: Session) -> None:
    """DB-SCH-004 schedule_baseline_items：期間 0（マイルストーン）・見積 0 は受け付ける。"""
    project = _project(db_session)
    wp = _wp(db_session, project)
    baseline = _baseline(db_session, project, _user(db_session))
    db_session.add(
        ScheduleBaselineItem(
            schedule_baseline_id=baseline.id, item_type="WORK_PACKAGE", element_id=wp.id, wbs_code="1.1", name="x",
            duration_days=0, estimate_hours=Decimal("0"),
        )
    )
    db_session.commit()


def test_schedule_baseline_items_server_default_applies_to_raw_insert(db_session: Session) -> None:
    """DB-SCH-004 schedule_baseline_items：is_milestone（偽）は DB の既定値としても入る。"""
    project = _project(db_session)
    baseline = _baseline(db_session, project, _user(db_session))
    db_session.execute(
        sa.text(
            "INSERT INTO schedule_baseline_items (schedule_baseline_id, item_type, wbs_code, name) "
            "VALUES (:b, 'WORK_PACKAGE', '1.1', '生')"
        ),
        {"b": baseline.id},
    )
    assert bool(db_session.execute(sa.text("SELECT is_milestone FROM schedule_baseline_items")).scalar_one()) is False


def test_schedule_baseline_block_deleting_baseline_with_items(db_session: Session) -> None:
    """DB-SCH-003・004：項目のある版は DB が消させない（版は削除しない。BR-12。ON DELETE なし）。"""
    project = _project(db_session)
    wp = _wp(db_session, project)
    baseline = _baseline(db_session, project, _user(db_session))
    db_session.add(
        ScheduleBaselineItem(schedule_baseline_id=baseline.id, item_type="WORK_PACKAGE", element_id=wp.id, wbs_code="1.1",
                             name="x")
    )
    db_session.commit()
    db_session.delete(baseline)
    _flush_fails(db_session)


# ---------------------------------------------------------------------------
# 移行
# ---------------------------------------------------------------------------


def _alembic_config(url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def test_migration_creates_sch_tables_matching_models(tmp_path: Path) -> None:
    """alembic upgrade head の結果に DB-SCH-001〜004 のテーブルがあり、モデルとの差が無い。0003 へ戻すと消え、また作れる。"""
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

    command.downgrade(_alembic_config(url), "0003_wbs")
    engine = create_db_engine(url)
    try:
        tables = set(sa.inspect(engine).get_table_names())
        assert not set(COLUMNS_4_1) & tables
        assert "wbs_elements" in tables
    finally:
        engine.dispose()
    command.upgrade(_alembic_config(url), "head")


def test_migration_enforces_milestone_and_dependency_checks(tmp_path: Path) -> None:
    """移行で作った DB でも、マイルストーンの期間（4.2）と依存の FS・ラグ 0（OQ-04）の検査が効く（モデルの create_all だけに頼らない）。"""
    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    command.upgrade(_alembic_config(url), "head")
    engine = create_db_engine(url)
    try:
        with Session(engine) as session:
            project = _project(session)
            wp = _wp(session, project)
            first = _activity(session, project, wp)
            second = _activity(session, project, wp, name="後続", sort_order=2)
            session.commit()
            for bad in (
                Activity(**_activity_values(project, wp, is_milestone=True, duration_days=2)),
                ActivityDependency(project_id=project.id, predecessor_id=first.id, successor_id=second.id, dep_type="SS"),
                ActivityDependency(project_id=project.id, predecessor_id=first.id, successor_id=second.id, lag_days=2),
            ):
                session.add(bad)
                _flush_fails(session)
    finally:
        engine.dispose()
