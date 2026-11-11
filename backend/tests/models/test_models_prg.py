"""進捗（PRG）の群のテーブルの試験（設計書 3 章・4.1・4.2・5.4・5.6・5.10、要求仕様書 BR-06・08・09・13）：DB-PRG-001〜005。

列が 4.1 の行どおりであること、既定値、一意制約・外部キー・値の制約が DB で効くこと、報告日の状態と確定の組（5.10）、
進捗率の範囲と実績日付の前後（PRG-02）、算定項目の種類（BR-06）、スナップショットが 5.6 の試験の基準の値を
そのまま持てて後から変わらないこと（BR-13）と、写した活動を消すときの扱い（A-22）、
移行（alembic upgrade head）の結果がモデルと食い違わないことを確かめる。
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
from app.models.prg import (
    JUDGEMENTS,
    PROGRESS_ITEM_TYPES,
    ProgressEntry,
    ProgressItem,
    ProgressSnapshot,
    ProgressSnapshotItem,
    StatusReport,
)
from app.models.prj import Project
from app.models.sch import Activity
from app.models.wbs import WbsElement

# 設計書 4.1 の「主な列」
COLUMNS_4_1 = {
    "status_reports": {
        "id", "project_id", "report_date", "status", "created_by", "created_at", "finalized_by", "finalized_at",
    },
    "progress_entries": {
        "id", "status_report_id", "activity_id", "percent_complete", "actual_start", "actual_finish", "remaining_hours",
        "delay_reason", "forecast_finish", "entered_by", "entered_at",
    },
    "progress_items": {"id", "activity_id", "item_type", "title", "weight", "done", "approved", "done_at"},
    "progress_snapshots": {
        "id", "status_report_id", "report_date", "report_line", "tolerance_days", "extract_days", "plan_basis",
        "created_at",
    },
    "progress_snapshot_items": {
        "id", "snapshot_id", "activity_id", "row_order", "wbs_code", "name", "start_date", "finish_date",
        "duration_days", "is_milestone", "is_critical", "percent_complete", "actual_start", "actual_finish",
        "actual_hours", "etc_hours", "point", "deviation", "judgement",
    },
}

AT = datetime(2026, 12, 10, 9, 0, tzinfo=UTC)


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


def _activity(session: Session, project: Project, name: str = "資料作成", seq: int = 1, **override: Any) -> Activity:
    wp = WbsElement(
        project_id=project.id, kind="WORK_PACKAGE", name=f"WP {seq}", wbs_code=f"1.{seq}", sibling_seq=seq, sort_order=seq
    )
    session.add(wp)
    session.flush()
    values: dict[str, Any] = {
        "project_id": project.id, "work_package_id": wp.id, "name": name, "sort_order": 1, "duration_days": 21,
    }
    values.update(override)
    activity = Activity(**values)
    session.add(activity)
    session.flush()
    return activity


def _report(session: Session, project: Project, creator: User, report_date: date = date(2026, 12, 10)) -> StatusReport:
    report = StatusReport(project_id=project.id, report_date=report_date, created_by=creator.id)
    session.add(report)
    session.flush()
    return report


def _snapshot(session: Session, report: StatusReport, **override: Any) -> ProgressSnapshot:
    values: dict[str, Any] = {
        "status_report_id": report.id, "report_date": report.report_date, "report_line": 10,
        "tolerance_days": Decimal("0.5"), "extract_days": Decimal("2"),
    }
    values.update(override)
    snapshot = ProgressSnapshot(**values)
    session.add(snapshot)
    session.flush()
    return snapshot


def _snapshot_item_values(snapshot: ProgressSnapshot, activity: Activity | None, **override: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "snapshot_id": snapshot.id, "activity_id": activity.id if activity else None, "row_order": 1, "wbs_code": "1.1",
        "name": "資料作成", "point": Decimal("5.25"), "deviation": Decimal("-4.75"), "judgement": "BEHIND",
    }
    values.update(override)
    return values


def _flush_fails(session: Session) -> None:
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


@pytest.mark.parametrize("table", sorted(COLUMNS_4_1))
def test_columns_match_design_4_1(table: str) -> None:
    """DB-PRG-001〜005 の列が設計書 4.1 の行どおりである。"""
    assert set(Base.metadata.tables[table].c.keys()) == COLUMNS_4_1[table]


# ---------------------------------------------------------------------------
# DB-PRG-001 status_reports
# ---------------------------------------------------------------------------


def test_status_reports_start_open_with_utc_created_at(db_session: Session) -> None:
    """DB-PRG-001 status_reports：報告日は OPEN から始まり、確定の利用者・時刻は空。作成の時刻は UTC（2.4）。"""
    project = _project(db_session)
    creator = _user(db_session)
    report = _report(db_session, project, creator)
    db_session.commit()
    db_session.expunge_all()

    loaded = db_session.get(StatusReport, report.id)
    assert loaded is not None
    assert (loaded.project_id, loaded.report_date, loaded.status, loaded.created_by) == (
        project.id, date(2026, 12, 10), "OPEN", creator.id,
    )
    assert (loaded.finalized_by, loaded.finalized_at) == (None, None)
    assert loaded.created_at.tzinfo is not None and loaded.created_at.utcoffset() == UTC.utcoffset(None)


def test_status_reports_finalize_records_who_and_when(db_session: Session) -> None:
    """DB-PRG-001 status_reports：OPEN → FINALIZED（5.10）で、確定した利用者と時刻を両方持つ。"""
    project = _project(db_session)
    creator = _user(db_session)
    report = _report(db_session, project, creator)
    db_session.commit()
    report.status, report.finalized_by, report.finalized_at = "FINALIZED", creator.id, AT
    db_session.commit()
    db_session.expunge_all()

    loaded = db_session.get(StatusReport, report.id)
    assert loaded is not None
    assert (loaded.status, loaded.finalized_by, loaded.finalized_at) == ("FINALIZED", creator.id, AT)


def test_status_reports_unique_per_project_and_date(db_session: Session) -> None:
    """DB-PRG-001 status_reports：同じプロジェクトの同じ日付の報告日は 1 つ（4.1 の組で一意）。別の日付・別のプロジェクトなら作れる。"""
    project = _project(db_session)
    other = _project(db_session, code="P-002")
    creator = _user(db_session)
    _report(db_session, project, creator)
    _report(db_session, project, creator, date(2026, 12, 17))
    _report(db_session, other, creator)
    db_session.commit()

    db_session.add(StatusReport(project_id=project.id, report_date=date(2026, 12, 10), created_by=creator.id))
    _flush_fails(db_session)


@pytest.mark.parametrize(
    "override",
    [
        {"status": "CLOSED"},
        {"status": "FINALIZED"},
        {"status": "FINALIZED", "finalized_at": AT},
        {"finalized_at": AT},
        {"report_date": None},
        {"project_id": 9999},
        {"created_by": 9999},
        {"created_by": None},
    ],
    ids=[
        "status", "finalized-without-who-and-when", "finalized-without-who", "open-with-finalized-at", "no-date",
        "no-project", "no-creator", "creator-missing",
    ],
)
def test_status_reports_rejections(db_session: Session, override: dict[str, Any]) -> None:
    """DB-PRG-001 status_reports：4.1 に無い状態、確定の利用者・時刻の欠けた FINALIZED、確定の時刻のある OPEN、日付の無い報告日、無いプロジェクト・利用者は DB が拒む。"""
    project = _project(db_session)
    creator = _user(db_session)
    values: dict[str, Any] = {"project_id": project.id, "report_date": date(2026, 12, 10), "created_by": creator.id}
    values.update(override)
    db_session.add(StatusReport(**values))
    _flush_fails(db_session)


def test_status_reports_server_default_applies_to_raw_insert(db_session: Session) -> None:
    """DB-PRG-001 status_reports：status（OPEN）は DB の既定値としても入る（ORM を通らない書き込み）。"""
    project = _project(db_session)
    creator = _user(db_session)
    db_session.execute(
        sa.text(
            "INSERT INTO status_reports (project_id, report_date, created_by, created_at) "
            "VALUES (:p, '2026-12-10', :u, '2026-12-10 00:00:00')"
        ),
        {"p": project.id, "u": creator.id},
    )
    assert db_session.execute(sa.text("SELECT status FROM status_reports")).scalar_one() == "OPEN"


# ---------------------------------------------------------------------------
# DB-PRG-002 progress_entries
# ---------------------------------------------------------------------------


def test_progress_entries_round_trip(db_session: Session) -> None:
    """DB-PRG-002 progress_entries：進捗率（小数）・実績日付・残工数・遅延理由・完了見込みを報告日ごとに持つ（PRG-02・03、EFF-06）。"""
    project = _project(db_session)
    user = _user(db_session)
    report = _report(db_session, project, user)
    activity = _activity(db_session, project)
    entry = ProgressEntry(
        status_report_id=report.id, activity_id=activity.id, percent_complete=Decimal("33.33"),
        actual_start=date(2026, 12, 1), remaining_hours=Decimal("12.50"), delay_reason="仕様の確認待ち",
        forecast_finish=date(2026, 12, 24), entered_by=user.id, entered_at=AT,
    )
    db_session.add(entry)
    db_session.commit()
    db_session.expunge_all()

    loaded = db_session.get(ProgressEntry, entry.id)
    assert loaded is not None
    assert (loaded.status_report_id, loaded.activity_id, loaded.percent_complete) == (report.id, activity.id, Decimal("33.33"))
    assert (loaded.actual_start, loaded.actual_finish, loaded.remaining_hours) == (date(2026, 12, 1), None, Decimal("12.50"))
    assert (loaded.delay_reason, loaded.forecast_finish, loaded.entered_by, loaded.entered_at) == (
        "仕様の確認待ち", date(2026, 12, 24), user.id, AT,
    )


def test_progress_entries_null_means_not_computed_and_automatic(db_session: Session) -> None:
    """DB-PRG-002 progress_entries：進捗率の NULL は未算定、残工数の NULL は自動（4.1）。入力の時刻は既定で UTC の現在時刻。"""
    project = _project(db_session)
    user = _user(db_session)
    entry = ProgressEntry(
        status_report_id=_report(db_session, project, user).id, activity_id=_activity(db_session, project).id,
        entered_by=user.id,
    )
    db_session.add(entry)
    db_session.commit()
    db_session.expunge_all()

    loaded = db_session.get(ProgressEntry, entry.id)
    assert loaded is not None
    assert (loaded.percent_complete, loaded.remaining_hours, loaded.actual_start, loaded.actual_finish) == (None, None, None, None)
    assert loaded.entered_at.tzinfo is not None


def test_progress_entries_unique_per_report_and_activity(db_session: Session) -> None:
    """DB-PRG-002 progress_entries：報告日と活動の組で一意。同じ活動でも別の報告日なら別の行（4.2：報告日ごとに持つ）。"""
    project = _project(db_session)
    user = _user(db_session)
    first = _report(db_session, project, user)
    second = _report(db_session, project, user, date(2026, 12, 17))
    activity = _activity(db_session, project)
    db_session.add_all(
        [
            ProgressEntry(status_report_id=first.id, activity_id=activity.id, percent_complete=Decimal("25"), entered_by=user.id),
            ProgressEntry(status_report_id=second.id, activity_id=activity.id, percent_complete=Decimal("60"), entered_by=user.id),
        ]
    )
    db_session.commit()

    db_session.add(ProgressEntry(status_report_id=first.id, activity_id=activity.id, entered_by=user.id))
    _flush_fails(db_session)


def test_progress_entries_accept_boundaries(db_session: Session) -> None:
    """DB-PRG-002 progress_entries：進捗率 0 と 100、実績開始日と同じ日の実績終了日、残工数 0 は受け付ける（PRG-02 の境界の内側）。"""
    project = _project(db_session)
    user = _user(db_session)
    report = _report(db_session, project, user)
    db_session.add_all(
        [
            ProgressEntry(
                status_report_id=report.id, activity_id=_activity(db_session, project, seq=1).id,
                percent_complete=Decimal("0"), entered_by=user.id,
            ),
            ProgressEntry(
                status_report_id=report.id, activity_id=_activity(db_session, project, seq=2).id,
                percent_complete=Decimal("100"), actual_start=date(2026, 12, 3), actual_finish=date(2026, 12, 3),
                remaining_hours=Decimal("0"), entered_by=user.id,
            ),
        ]
    )
    db_session.commit()


@pytest.mark.parametrize(
    "override",
    [
        {"percent_complete": Decimal("-0.01")},
        {"percent_complete": Decimal("100.01")},
        {"actual_start": date(2026, 12, 4), "actual_finish": date(2026, 12, 3)},
        {"remaining_hours": Decimal("-0.01")},
        {"status_report_id": 9999},
        {"activity_id": 9999},
        {"entered_by": 9999},
        {"entered_by": None},
    ],
    ids=[
        "percent-below-0", "percent-above-100", "finish-before-start", "negative-remaining", "no-report", "no-activity",
        "no-user", "user-missing",
    ],
)
def test_progress_entries_rejections(db_session: Session, override: dict[str, Any]) -> None:
    """DB-PRG-002 progress_entries：0〜100 の外の進捗率、実績開始日より前の実績終了日（PRG-02）、負の残工数、無い報告日・活動・利用者は DB が拒む。"""
    project = _project(db_session)
    user = _user(db_session)
    values: dict[str, Any] = {
        "status_report_id": _report(db_session, project, user).id, "activity_id": _activity(db_session, project).id,
        "entered_by": user.id,
    }
    values.update(override)
    db_session.add(ProgressEntry(**values))
    _flush_fails(db_session)


def test_progress_entries_block_deleting_their_activity_and_report(db_session: Session) -> None:
    """DB-PRG-002 progress_entries：進捗の入力のある活動・報告日は DB が消させない（ON DELETE なし。消し方はサービス層が決める。A-22）。"""
    project = _project(db_session)
    user = _user(db_session)
    report = _report(db_session, project, user)
    activity = _activity(db_session, project)
    db_session.add(ProgressEntry(status_report_id=report.id, activity_id=activity.id, entered_by=user.id))
    db_session.commit()
    report_id, activity_id = report.id, activity.id

    db_session.delete(activity)
    _flush_fails(db_session)
    db_session.delete(db_session.get(StatusReport, report_id))
    _flush_fails(db_session)
    assert db_session.get(Activity, activity_id) is not None


# ---------------------------------------------------------------------------
# DB-PRG-003 progress_items
# ---------------------------------------------------------------------------


def test_progress_items_defaults(db_session: Session) -> None:
    """DB-PRG-003 progress_items：重みの既定は 1、完了・承認は偽、完了の時刻は空（4.1）。"""
    project = _project(db_session)
    item = ProgressItem(activity_id=_activity(db_session, project).id, item_type="DELIVERABLE", title="画面設計書")
    db_session.add(item)
    db_session.commit()
    db_session.expunge_all()

    loaded = db_session.get(ProgressItem, item.id)
    assert loaded is not None
    assert (loaded.item_type, loaded.title, loaded.weight, loaded.done, loaded.approved, loaded.done_at) == (
        "DELIVERABLE", "画面設計書", Decimal("1"), False, False, None,
    )


def test_progress_items_round_trip_done_and_approved(db_session: Session) -> None:
    """DB-PRG-003 progress_items：完了・承認・完了の時刻・重みを持つ（BR-06 の分子・分母になる）。"""
    project = _project(db_session)
    activity = _activity(db_session, project)
    item = ProgressItem(
        activity_id=activity.id, item_type="WORK", title="レビュー", weight=Decimal("2.5"), done=True, approved=True,
        done_at=AT,
    )
    db_session.add(item)
    db_session.commit()
    db_session.expunge_all()

    loaded = db_session.get(ProgressItem, item.id)
    assert loaded is not None
    assert (loaded.activity_id, loaded.weight, loaded.done, loaded.approved, loaded.done_at) == (
        activity.id, Decimal("2.50"), True, True, AT,
    )


@pytest.mark.parametrize("item_type", PROGRESS_ITEM_TYPES)
def test_progress_items_accept_four_types(db_session: Session, item_type: str) -> None:
    """DB-PRG-003 progress_items：種類は 4.1 の 4 つ（成果物・作業・検証項目・マイルストーン。BR-06 の項目を数える方式）。"""
    project = _project(db_session)
    db_session.add(ProgressItem(activity_id=_activity(db_session, project).id, item_type=item_type, title="x"))
    db_session.commit()


def test_progress_item_types_match_design() -> None:
    """DB-PRG-003 progress_items：種類の一覧は 4.1 の DELIVERABLE/WORK/CHECK/MILESTONE。"""
    assert PROGRESS_ITEM_TYPES == ("DELIVERABLE", "WORK", "CHECK", "MILESTONE")


def test_progress_items_accept_smallest_positive_weight(db_session: Session) -> None:
    """DB-PRG-003 progress_items：重み 0.01 は受け付ける（0 の境界の内側）。"""
    project = _project(db_session)
    db_session.add(
        ProgressItem(activity_id=_activity(db_session, project).id, item_type="MILESTONE", title="x", weight=Decimal("0.01"))
    )
    db_session.commit()


@pytest.mark.parametrize(
    "override",
    [
        {"item_type": "TASK"},
        {"item_type": None},
        {"weight": Decimal("0")},
        {"weight": Decimal("-1")},
        {"done_at": AT},
        {"title": None},
        {"activity_id": 9999},
    ],
    ids=["item-type", "no-item-type", "zero-weight", "negative-weight", "done-at-not-done", "no-title", "no-activity"],
)
def test_progress_items_rejections(db_session: Session, override: dict[str, Any]) -> None:
    """DB-PRG-003 progress_items：4.1 に無い種類、0 以下の重み、完了していない項目の完了の時刻、名前の無い項目、無い活動は DB が拒む。"""
    project = _project(db_session)
    values: dict[str, Any] = {"activity_id": _activity(db_session, project).id, "item_type": "CHECK", "title": "x"}
    values.update(override)
    db_session.add(ProgressItem(**values))
    _flush_fails(db_session)


def test_progress_items_server_defaults_apply_to_raw_insert(db_session: Session) -> None:
    """DB-PRG-003 progress_items：weight（1）・done・approved（偽）は DB の既定値としても入る。"""
    project = _project(db_session)
    activity = _activity(db_session, project)
    db_session.execute(
        sa.text("INSERT INTO progress_items (activity_id, item_type, title) VALUES (:a, 'CHECK', '生')"),
        {"a": activity.id},
    )
    weight, done, approved = db_session.execute(sa.text("SELECT weight, done, approved FROM progress_items")).one()
    assert (Decimal(str(weight)), bool(done), bool(approved)) == (Decimal("1"), False, False)


# ---------------------------------------------------------------------------
# DB-PRG-004 progress_snapshots
# ---------------------------------------------------------------------------


def test_progress_snapshots_round_trip_and_defaults(db_session: Session) -> None:
    """DB-PRG-004 progress_snapshots：報告日・報告日線（5.6 の試験の基準の L＝10）・許容幅・抽出の閾値を写し、計画の基準は LATEST（OQ-03）。"""
    project = _project(db_session)
    report = _report(db_session, project, _user(db_session))
    snapshot = _snapshot(db_session, report)
    db_session.commit()
    db_session.expunge_all()

    loaded = db_session.get(ProgressSnapshot, snapshot.id)
    assert loaded is not None
    assert (loaded.status_report_id, loaded.report_date, loaded.report_line) == (report.id, date(2026, 12, 10), 10)
    assert (loaded.tolerance_days, loaded.extract_days, loaded.plan_basis) == (Decimal("0.50"), Decimal("2.00"), "LATEST")
    assert loaded.created_at.tzinfo is not None


def test_progress_snapshots_one_per_report(db_session: Session) -> None:
    """DB-PRG-004 progress_snapshots：報告日ごとに 1 つ（status_report_id で一意。4.1）。"""
    project = _project(db_session)
    report = _report(db_session, project, _user(db_session))
    _snapshot(db_session, report)
    db_session.commit()
    db_session.add(
        ProgressSnapshot(
            status_report_id=report.id, report_date=report.report_date, report_line=10, tolerance_days=Decimal("0.5"),
            extract_days=Decimal("2"),
        )
    )
    _flush_fails(db_session)


@pytest.mark.parametrize(
    "override",
    [
        {"plan_basis": "BASELINE"},
        {"tolerance_days": Decimal("-0.01")},
        {"extract_days": Decimal("-0.01")},
        {"report_line": None},
        {"tolerance_days": None},
        {"status_report_id": 9999},
    ],
    ids=["plan-basis", "negative-tolerance", "negative-extract", "no-line", "no-tolerance", "no-report"],
)
def test_progress_snapshots_rejections(db_session: Session, override: dict[str, Any]) -> None:
    """DB-PRG-004 progress_snapshots：R1 に無い計画の基準（OQ-03）、負の許容幅・閾値、報告日線・許容幅の無い写し、無い報告日は DB が拒む。"""
    project = _project(db_session)
    report = _report(db_session, project, _user(db_session))
    values: dict[str, Any] = {
        "status_report_id": report.id, "report_date": report.report_date, "report_line": 10,
        "tolerance_days": Decimal("0.5"), "extract_days": Decimal("2"),
    }
    values.update(override)
    db_session.add(ProgressSnapshot(**values))
    _flush_fails(db_session)


def test_progress_snapshots_accept_zero_thresholds(db_session: Session) -> None:
    """DB-PRG-004 progress_snapshots：許容幅 0・抽出の閾値 0 は受け付ける（負の境界の内側）。"""
    project = _project(db_session)
    _snapshot(
        db_session, _report(db_session, project, _user(db_session)), tolerance_days=Decimal("0"), extract_days=Decimal("0")
    )
    db_session.commit()


def test_progress_snapshots_server_default_applies_to_raw_insert(db_session: Session) -> None:
    """DB-PRG-004 progress_snapshots：plan_basis（LATEST）は DB の既定値としても入る。"""
    project = _project(db_session)
    report = _report(db_session, project, _user(db_session))
    db_session.execute(
        sa.text(
            "INSERT INTO progress_snapshots (status_report_id, report_date, report_line, tolerance_days, extract_days, "
            "created_at) VALUES (:r, '2026-12-10', 10, 0.5, 2, '2026-12-10 00:00:00')"
        ),
        {"r": report.id},
    )
    assert db_session.execute(sa.text("SELECT plan_basis FROM progress_snapshots")).scalar_one() == "LATEST"


# ---------------------------------------------------------------------------
# DB-PRG-005 progress_snapshot_items
# ---------------------------------------------------------------------------

# 設計書 5.6 の試験の基準（稲妻チャート.txt §6 の例）：全ての曜日を稼働日、原点 2026-12-01、報告日 2026-12-10（L＝10）
EXAMPLE_5_6 = [
    # (名称, 開始日, 終了日, 期間, 進捗率, P, δ, 判定)
    ("資料作成", date(2026, 12, 1), date(2026, 12, 21), 21, Decimal("25"), Decimal("5.25"), Decimal("-4.75"), "BEHIND"),
    ("関係者調整", date(2026, 12, 1), date(2026, 12, 21), 21, Decimal("60"), Decimal("12.6"), Decimal("2.6"), "AHEAD"),
    ("公開準備", date(2026, 12, 12), date(2026, 12, 18), 7, Decimal("0"), Decimal("10"), Decimal("0"), "NOT_STARTED"),
    ("環境整備", date(2026, 12, 1), date(2026, 12, 8), 8, Decimal("100"), Decimal("10"), Decimal("0"), "COMPLETED"),
]


def test_progress_snapshot_items_hold_example_5_6(db_session: Session) -> None:
    """DB-PRG-005 progress_snapshot_items：5.6 の試験の基準の 4 活動の計画・進捗率・ポイント・ずれ・判定を、行の順に丸めずに持てる（PRG-17・18）。"""
    project = _project(db_session)
    report = _report(db_session, project, _user(db_session))
    snapshot = _snapshot(db_session, report)
    for row, (name, start, finish, days, percent, point, deviation, judgement) in enumerate(EXAMPLE_5_6, start=1):
        activity = _activity(db_session, project, name=name, seq=row, duration_days=days)
        db_session.add(
            ProgressSnapshotItem(
                snapshot_id=snapshot.id, activity_id=activity.id, row_order=row, wbs_code=f"1.{row}", name=name,
                start_date=start, finish_date=finish, duration_days=days, percent_complete=percent, point=point,
                deviation=deviation, judgement=judgement,
            )
        )
    db_session.commit()
    db_session.expunge_all()

    loaded = db_session.scalars(
        sa.select(ProgressSnapshotItem)
        .where(ProgressSnapshotItem.snapshot_id == snapshot.id)
        .order_by(ProgressSnapshotItem.row_order)
    ).all()
    assert [
        (r.name, r.start_date, r.finish_date, r.duration_days, r.percent_complete, r.point, r.deviation, r.judgement)
        for r in loaded
    ] == EXAMPLE_5_6
    # δ ＝ P − L（5.6）が写しの中でも成り立つ
    assert all(r.deviation == r.point - 10 for r in loaded)


def test_progress_snapshot_items_round_trip_actuals_and_effort(db_session: Session) -> None:
    """DB-PRG-005 progress_snapshot_items：実績日付・実績工数・ETC・マイルストーン・クリティカルを写す（BR-13 の全ての値）。"""
    project = _project(db_session)
    activity = _activity(db_session, project, name="出荷判定", is_milestone=True, duration_days=0)
    snapshot = _snapshot(db_session, _report(db_session, project, _user(db_session)))
    item = ProgressSnapshotItem(
        **_snapshot_item_values(
            snapshot, activity, name="出荷判定", start_date=date(2026, 12, 9), finish_date=date(2026, 12, 9),
            duration_days=0, is_milestone=True, is_critical=True, percent_complete=Decimal("100"),
            actual_start=date(2026, 12, 9), actual_finish=date(2026, 12, 9), actual_hours=Decimal("1.5"),
            etc_hours=Decimal("0"), point=Decimal("9"), deviation=Decimal("-1"), judgement="BEHIND",
        )
    )
    db_session.add(item)
    db_session.commit()
    db_session.expunge_all()

    loaded = db_session.get(ProgressSnapshotItem, item.id)
    assert loaded is not None
    assert (loaded.duration_days, loaded.is_milestone, loaded.is_critical) == (0, True, True)
    assert (loaded.actual_start, loaded.actual_finish, loaded.actual_hours, loaded.etc_hours) == (
        date(2026, 12, 9), date(2026, 12, 9), Decimal("1.50"), Decimal("0.00"),
    )


def test_progress_snapshot_items_keep_values_when_activity_and_entry_change(db_session: Session) -> None:
    """DB-PRG-005 progress_snapshot_items：確定の後に活動の計画・進捗を直しても、写しの値は変わらない（PRG-17・BR-13）。"""
    project = _project(db_session)
    user = _user(db_session)
    report = _report(db_session, project, user)
    activity = _activity(db_session, project)
    activity.start_date, activity.finish_date = date(2026, 12, 1), date(2026, 12, 21)
    entry = ProgressEntry(status_report_id=report.id, activity_id=activity.id, percent_complete=Decimal("25"), entered_by=user.id)
    snapshot = _snapshot(db_session, report)
    item = ProgressSnapshotItem(
        **_snapshot_item_values(
            snapshot, activity, start_date=date(2026, 12, 1), finish_date=date(2026, 12, 21), duration_days=21,
            percent_complete=Decimal("25"),
        )
    )
    db_session.add_all([entry, item])
    db_session.commit()

    activity.name, activity.duration_days = "変えた名称", 30
    activity.start_date, activity.finish_date = date(2026, 12, 2), date(2026, 12, 31)
    entry.percent_complete = Decimal("80")
    db_session.commit()
    db_session.expunge_all()

    loaded = db_session.get(ProgressSnapshotItem, item.id)
    assert loaded is not None
    assert (loaded.name, loaded.start_date, loaded.finish_date, loaded.duration_days, loaded.percent_complete) == (
        "資料作成", date(2026, 12, 1), date(2026, 12, 21), 21, Decimal("25.00"),
    )
    assert (loaded.point, loaded.deviation, loaded.judgement) == (Decimal("5.25"), Decimal("-4.75"), "BEHIND")


def test_progress_snapshot_items_survive_deleted_activity(db_session: Session) -> None:
    """DB-PRG-005 progress_snapshot_items：写した活動は、紐付けを空にしないと DB が消させない。空にすれば活動を消せて写しは残る（BR-13。A-22）。"""
    project = _project(db_session)
    activity = _activity(db_session, project)
    snapshot = _snapshot(db_session, _report(db_session, project, _user(db_session)))
    item = ProgressSnapshotItem(**_snapshot_item_values(snapshot, activity))
    db_session.add(item)
    db_session.commit()
    activity_id, item_id = activity.id, item.id

    db_session.delete(activity)
    _flush_fails(db_session)

    loaded = db_session.get(ProgressSnapshotItem, item_id)
    assert loaded is not None
    loaded.activity_id = None
    db_session.flush()
    db_session.delete(db_session.get(Activity, activity_id))
    db_session.commit()
    db_session.expunge_all()
    assert db_session.get(Activity, activity_id) is None
    survived = db_session.get(ProgressSnapshotItem, item_id)
    assert survived is not None
    assert (survived.activity_id, survived.name, survived.point, survived.judgement) == (None, "資料作成", Decimal("5.25"), "BEHIND")


def test_progress_snapshot_items_row_order_unique_within_snapshot(db_session: Session) -> None:
    """DB-PRG-005 progress_snapshot_items：行の順はスナップショットの中で一意（折れ線の点の順。RULE-PRG-005）。別のスナップショットなら同じ順を使える。"""
    project = _project(db_session)
    user = _user(db_session)
    first = _snapshot(db_session, _report(db_session, project, user))
    second = _snapshot(db_session, _report(db_session, project, user, date(2026, 12, 17)), report_line=17)
    activity = _activity(db_session, project)
    db_session.add_all(
        [
            ProgressSnapshotItem(**_snapshot_item_values(first, activity)),
            ProgressSnapshotItem(**_snapshot_item_values(second, activity)),
        ]
    )
    db_session.commit()
    db_session.add(ProgressSnapshotItem(**_snapshot_item_values(first, activity, name="重複")))
    _flush_fails(db_session)


@pytest.mark.parametrize("judgement", JUDGEMENTS)
def test_progress_snapshot_items_accept_six_judgements(db_session: Session, judgement: str) -> None:
    """DB-PRG-005 progress_snapshot_items：判定は 5.6・BR-08・BR-09 の 6 つ（完了・未着手・遅れ・前倒し・予定どおり・未算定）のどれか。"""
    project = _project(db_session)
    snapshot = _snapshot(db_session, _report(db_session, project, _user(db_session)))
    db_session.add(ProgressSnapshotItem(**_snapshot_item_values(snapshot, None, judgement=judgement)))
    db_session.commit()


def test_judgements_cover_design_5_6() -> None:
    """DB-PRG-005 progress_snapshot_items：判定の値は 6 つで重複しない（5.6 の完了・未着手（予定内）・遅れ・前倒し・予定どおり・未算定）。"""
    assert len(JUDGEMENTS) == len(set(JUDGEMENTS)) == 6


@pytest.mark.parametrize(
    "override",
    [
        {"judgement": "LATE"},
        {"judgement": None},
        {"percent_complete": Decimal("100.01")},
        {"percent_complete": Decimal("-0.01")},
        {"duration_days": -1},
        {"actual_start": date(2026, 12, 4), "actual_finish": date(2026, 12, 3)},
        {"actual_hours": Decimal("-0.01")},
        {"etc_hours": Decimal("-0.01")},
        {"point": None},
        {"deviation": None},
        {"row_order": None},
        {"wbs_code": None},
        {"name": None},
        {"snapshot_id": 9999},
        {"activity_id": 9999},
    ],
    ids=[
        "judgement", "no-judgement", "percent-above-100", "percent-below-0", "negative-duration", "finish-before-start",
        "negative-actual-hours", "negative-etc", "no-point", "no-deviation", "no-row-order", "no-code", "no-name",
        "no-snapshot", "no-activity",
    ],
)
def test_progress_snapshot_items_rejections(db_session: Session, override: dict[str, Any]) -> None:
    """DB-PRG-005 progress_snapshot_items：6 つに無い判定、0〜100 の外の進捗率、負の期間・工数、前後の逆の実績日付、ポイント・ずれ・行の順・WBS ID・名称の無い写し、無いスナップショット・活動は DB が拒む。"""
    project = _project(db_session)
    snapshot = _snapshot(db_session, _report(db_session, project, _user(db_session)))
    db_session.add(ProgressSnapshotItem(**_snapshot_item_values(snapshot, _activity(db_session, project), **override)))
    _flush_fails(db_session)


def test_progress_snapshot_items_server_defaults_apply_to_raw_insert(db_session: Session) -> None:
    """DB-PRG-005 progress_snapshot_items：is_milestone・is_critical（偽）は DB の既定値としても入る。"""
    project = _project(db_session)
    snapshot = _snapshot(db_session, _report(db_session, project, _user(db_session)))
    db_session.execute(
        sa.text(
            "INSERT INTO progress_snapshot_items (snapshot_id, row_order, wbs_code, name, point, deviation, judgement) "
            "VALUES (:s, 1, '1.1', '生', 10, 0, 'ON_SCHEDULE')"
        ),
        {"s": snapshot.id},
    )
    milestone, critical = db_session.execute(sa.text("SELECT is_milestone, is_critical FROM progress_snapshot_items")).one()
    assert (bool(milestone), bool(critical)) == (False, False)


def test_progress_snapshots_block_deleting_snapshot_with_items_and_report_with_snapshot(db_session: Session) -> None:
    """DB-PRG-004・005：活動の写しのあるスナップショット、スナップショットのある報告日は DB が消させない（BR-13。ON DELETE なし）。"""
    project = _project(db_session)
    report = _report(db_session, project, _user(db_session))
    snapshot = _snapshot(db_session, report)
    db_session.add(ProgressSnapshotItem(**_snapshot_item_values(snapshot, None)))
    db_session.commit()
    report_id = report.id

    db_session.delete(snapshot)
    _flush_fails(db_session)
    db_session.delete(db_session.get(StatusReport, report_id))
    _flush_fails(db_session)


# ---------------------------------------------------------------------------
# 移行
# ---------------------------------------------------------------------------


def _alembic_config(url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def test_migration_creates_prg_tables_matching_models(tmp_path: Path) -> None:
    """alembic upgrade head の結果に DB-PRG-001〜005 のテーブルがあり、モデルとの差が無い。0004 へ戻すと消え、また作れる。"""
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

    command.downgrade(_alembic_config(url), "0004_sch")
    engine = create_db_engine(url)
    try:
        tables = set(sa.inspect(engine).get_table_names())
        assert not set(COLUMNS_4_1) & tables
        assert "activities" in tables
    finally:
        engine.dispose()
    command.upgrade(_alembic_config(url), "head")


def test_migration_enforces_report_state_and_progress_checks(tmp_path: Path) -> None:
    """移行で作った DB でも、報告日の確定の組（5.10）・進捗率の範囲と実績日付の前後（PRG-02）・判定の値の検査が効く（モデルの create_all だけに頼らない）。"""
    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    command.upgrade(_alembic_config(url), "head")
    engine = create_db_engine(url)
    try:
        with Session(engine) as session:
            project = _project(session)
            user = _user(session)
            report = _report(session, project, user)
            activity = _activity(session, project)
            snapshot = _snapshot(session, report)
            session.commit()
            for bad in (
                StatusReport(project_id=project.id, report_date=date(2026, 12, 17), created_by=user.id, status="FINALIZED"),
                ProgressEntry(
                    status_report_id=report.id, activity_id=activity.id, percent_complete=Decimal("101"), entered_by=user.id
                ),
                ProgressEntry(
                    status_report_id=report.id, activity_id=activity.id, actual_start=date(2026, 12, 4),
                    actual_finish=date(2026, 12, 3), entered_by=user.id,
                ),
                ProgressItem(activity_id=activity.id, item_type="CHECK", title="x", weight=Decimal("0")),
                ProgressSnapshotItem(**_snapshot_item_values(snapshot, activity, judgement="LATE")),
            ):
                session.add(bad)
                _flush_fails(session)
    finally:
        engine.dispose()
