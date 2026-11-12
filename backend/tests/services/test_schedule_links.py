"""ガントが読む最新の進捗（``app.services.schedule.progress_entries_latest``。設計書 4.2、DB-PRG-001・002）の試験。

4.2：「最新の値は、確定していない最新の報告日、無ければ最新の確定済みの報告日の行である」。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import FastAPI
from sqlalchemy.orm import Session

from app.models.cmn import User
from app.models.prg import ProgressEntry, StatusReport
from app.models.prj import Project
from app.models.sch import Activity
from app.models.wbs import WbsElement
from app.services.schedule import ActivityProgress, LatestProgress, progress_entries_latest

AT = datetime(2026, 12, 10, 9, 0, tzinfo=UTC)


def _setup(session: Session, code: str = "P-001") -> tuple[Project, User, Activity, Activity]:
    user = User(login_id=f"pm-{code}", display_name="利用者", password_hash="scrypt$dummy")
    session.add(user)
    session.flush()
    project = Project(code=code, name="プロジェクト", start_date=date(2026, 12, 1), owner_user_id=user.id)
    session.add(project)
    session.flush()
    wp = WbsElement(project_id=project.id, kind="WORK_PACKAGE", name="WP", wbs_code="1", sibling_seq=1, sort_order=1)
    session.add(wp)
    session.flush()
    first = Activity(project_id=project.id, work_package_id=wp.id, name="資料作成", sort_order=1, duration_days=21)
    second = Activity(project_id=project.id, work_package_id=wp.id, name="関係者調整", sort_order=2, duration_days=21)
    session.add_all([first, second])
    session.flush()
    return project, user, first, second


def _report(session: Session, project: Project, user: User, day: date, finalized: bool = False) -> StatusReport:
    report = StatusReport(project_id=project.id, report_date=day, created_by=user.id)
    if finalized:
        report.status, report.finalized_by, report.finalized_at = "FINALIZED", user.id, AT
    session.add(report)
    session.flush()
    return report


def _entry(session: Session, report: StatusReport, activity: Activity, user: User, percent: str | None) -> None:
    session.add(
        ProgressEntry(
            status_report_id=report.id, activity_id=activity.id,
            percent_complete=Decimal(percent) if percent is not None else None, entered_by=user.id,
        )
    )
    session.flush()


def test_no_report_means_no_progress(db_session: Session) -> None:
    """報告日が 1 つも無いプロジェクトは、報告日も進捗の入力も無い。"""
    project, _, _, _ = _setup(db_session)
    assert progress_entries_latest(db_session, project.id) == LatestProgress()


def test_latest_finalized_report_when_no_open_report(db_session: Session) -> None:
    """確定していない報告日が無ければ、最新の確定済みの報告日の行を返す（4.2）。"""
    project, user, first, second = _setup(db_session)
    old = _report(db_session, project, user, date(2026, 12, 3), finalized=True)
    new = _report(db_session, project, user, date(2026, 12, 10), finalized=True)
    _entry(db_session, old, first, user, "10")
    _entry(db_session, new, first, user, "25")
    _entry(db_session, new, second, user, None)

    latest = progress_entries_latest(db_session, project.id)
    assert latest.report_date == date(2026, 12, 10)
    assert dict(latest.activities) == {
        first.id: ActivityProgress(percent_complete=Decimal("25")),
        second.id: ActivityProgress(percent_complete=None),
    }


def test_open_report_wins_over_newer_finalized_report(db_session: Session) -> None:
    """確定していない報告日があれば、より新しい確定済みの報告日があってもそちらを返す（4.2 の順）。最新の OPEN を選ぶ。"""
    project, user, first, _ = _setup(db_session)
    older_open = _report(db_session, project, user, date(2026, 12, 3))
    newer_open = _report(db_session, project, user, date(2026, 12, 8))
    finalized = _report(db_session, project, user, date(2026, 12, 10), finalized=True)
    _entry(db_session, older_open, first, user, "5")
    _entry(db_session, newer_open, first, user, "20")
    _entry(db_session, finalized, first, user, "30")

    latest = progress_entries_latest(db_session, project.id)
    assert latest.report_date == date(2026, 12, 8)
    assert dict(latest.activities) == {first.id: ActivityProgress(percent_complete=Decimal("20"))}


def test_actual_dates_are_returned_and_other_projects_ignored(db_session: Session) -> None:
    """実績の開始日・終了日も返す。別のプロジェクトの報告日は読まない。"""
    project, user, first, _ = _setup(db_session)
    other, other_user, other_activity, _ = _setup(db_session, code="P-002")
    report = _report(db_session, project, user, date(2026, 12, 10))
    db_session.add(
        ProgressEntry(
            status_report_id=report.id, activity_id=first.id, percent_complete=Decimal("100"),
            actual_start=date(2026, 12, 1), actual_finish=date(2026, 12, 8), entered_by=user.id,
        )
    )
    _entry(db_session, _report(db_session, other, other_user, date(2026, 12, 17)), other_activity, other_user, "50")

    latest = progress_entries_latest(db_session, project.id)
    assert latest.report_date == date(2026, 12, 10)
    assert dict(latest.activities) == {
        first.id: ActivityProgress(
            percent_complete=Decimal("100"), actual_start=date(2026, 12, 1), actual_finish=date(2026, 12, 8)
        ),
    }


def test_app_reads_progress_from_table(app: FastAPI) -> None:
    """アプリに載せた最新の進捗の口は、進捗の表を読む実装である（db:PRG で差し替えた。A-22）。"""
    assert app.state.schedule_links.latest_progress is progress_entries_latest
