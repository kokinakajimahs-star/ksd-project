"""工数（EFF）の表を読む口の試験（db:EFF で差し替えた口。docs/decisions/ASSUMPTIONS.md A-27）。

- ``app.services.project_access.AssignmentsTable``：利用者に割り当てた活動（委託先の絞り込み・自分の割当の判定。9.2）
- ``app.services.schedule.AssignmentsStore``：活動の担当・割当工数の読み書き（API-SCH-004・005、責任分担表 API-WBS-011）
- ``app.services.wbs.time_entries_table``：横断照会（API-WBS-023）が読む実績工数
- ``app.services.progress.TimeEntriesEffort``：確定のスナップショットに写す実績工数（報告日以前の合計。5.8・BR-13）
- ``app.services.progress.ComputedEtcEffort``：同じ実績工数と、RULE-EFF-001 で求めた ETC（rule:EFF で差し替えた口。A-28）

どれも**別のプロジェクトの行を混ぜない**ことを、プロジェクトを 2 つ置いて確かめる。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import FastAPI
from sqlalchemy.orm import Session

from app.domain.wbs_rules import AssignmentRef, TimeEntryRef
from app.models.cmn import User
from app.models.eff import Assignment, TimeEntry
from app.models.prg import ProgressEntry, StatusReport
from app.models.prj import Project
from app.models.sch import Activity
from app.models.wbs import WbsElement
from app.services.progress import ActivityEffort, ComputedEtcEffort, TimeEntriesEffort
from app.services.project_access import AssignmentsTable
from app.services.schedule import AssignmentsStore
from app.services.wbs import time_entries_table


def _user(session: Session, login_id: str) -> User:
    user = User(login_id=login_id, display_name="利用者", password_hash="scrypt$dummy")
    session.add(user)
    session.flush()
    return user


def _project_with_activities(session: Session, code: str, owner: User) -> tuple[Project, Activity, Activity]:
    project = Project(code=code, name="プロジェクト", start_date=date(2026, 12, 1), owner_user_id=owner.id)
    session.add(project)
    session.flush()
    wp = WbsElement(project_id=project.id, kind="WORK_PACKAGE", name="WP", wbs_code="1", sibling_seq=1, sort_order=1)
    session.add(wp)
    session.flush()
    first = Activity(project_id=project.id, work_package_id=wp.id, name="資料作成", sort_order=1, duration_days=5)
    second = Activity(project_id=project.id, work_package_id=wp.id, name="関係者調整", sort_order=2, duration_days=5)
    session.add_all([first, second])
    session.flush()
    return project, first, second


def _entry(session: Session, user: User, activity: Activity, day: int, hours: str) -> TimeEntry:
    entry = TimeEntry(user_id=user.id, activity_id=activity.id, work_date=date(2026, 12, day), hours=Decimal(hours))
    session.add(entry)
    session.flush()
    return entry


def test_assigned_activities_are_scoped_to_project_and_user(app: FastAPI, db_session: Session) -> None:
    """AssignmentsTable：そのプロジェクトの活動のうち、その利用者を担当に置いた活動だけ。"""
    owner, executor, other = _user(db_session, "owner"), _user(db_session, "executor"), _user(db_session, "other")
    project, a, b = _project_with_activities(db_session, "P-001", owner)
    other_project, x, _ = _project_with_activities(db_session, "P-002", owner)
    db_session.add_all([
        Assignment(activity_id=a.id, user_id=executor.id),
        Assignment(activity_id=b.id, user_id=other.id),
        Assignment(activity_id=x.id, user_id=executor.id),
    ])
    db_session.flush()

    reader = AssignmentsTable()
    assert reader.activity_ids(db_session, project.id, executor.id) == frozenset({a.id})
    assert reader.activity_ids(db_session, other_project.id, executor.id) == frozenset({x.id})
    assert reader.activity_ids(db_session, other_project.id, other.id) == frozenset()


def test_assignments_store_reads_replaces_and_removes(app: FastAPI, db_session: Session) -> None:
    """AssignmentsStore：プロジェクトの割当を（活動, 利用者）の順で読む。置き換えは残る担当の割当工数を変えず、消すとその活動の行だけ消える。"""
    owner, u1, u2, u3 = (_user(db_session, n) for n in ("owner", "u1", "u2", "u3"))
    project, a, b = _project_with_activities(db_session, "P-001", owner)
    other_project, x, _ = _project_with_activities(db_session, "P-002", owner)
    db_session.add_all([
        Assignment(activity_id=a.id, user_id=u1.id, planned_hours=Decimal("8")),
        Assignment(activity_id=a.id, user_id=u2.id, planned_hours=Decimal("4")),
        Assignment(activity_id=b.id, user_id=u1.id),
        Assignment(activity_id=x.id, user_id=u1.id, planned_hours=Decimal("2")),
    ])
    db_session.flush()
    store = AssignmentsStore()

    assert list(store.assignments(db_session, project.id)) == [
        AssignmentRef(a.id, u1.id, Decimal("8.00")), AssignmentRef(a.id, u2.id, Decimal("4.00")),
        AssignmentRef(b.id, u1.id, None),
    ]

    store.replace_assignees(db_session, a.id, frozenset({u1.id, u3.id}))
    db_session.flush()
    assert [r for r in store.assignments(db_session, project.id) if r.activity_id == a.id] == [
        AssignmentRef(a.id, u1.id, Decimal("8.00")), AssignmentRef(a.id, u3.id, None),
    ]

    store.replace_assignees(db_session, a.id, frozenset())
    store.remove_activity(db_session, b.id)
    db_session.flush()
    assert list(store.assignments(db_session, project.id)) == []
    assert list(store.assignments(db_session, other_project.id)) == [AssignmentRef(x.id, u1.id, Decimal("2.00"))]


def test_time_entries_table_is_scoped_to_project(app: FastAPI, db_session: Session) -> None:
    """time_entries_table：そのプロジェクトの活動の実績だけを、ID の順で返す。"""
    owner, executor = _user(db_session, "owner"), _user(db_session, "executor")
    project, a, b = _project_with_activities(db_session, "P-001", owner)
    _, x, _ = _project_with_activities(db_session, "P-002", owner)
    e1 = _entry(db_session, executor, a, 1, "3.5")
    _entry(db_session, executor, x, 1, "8")
    e3 = _entry(db_session, executor, b, 2, "2")

    assert time_entries_table(db_session, project.id) == [
        TimeEntryRef(e1.id, a.id, Decimal("3.50")), TimeEntryRef(e3.id, b.id, Decimal("2.00")),
    ]


def test_time_entries_effort_sums_up_to_report_date(app: FastAPI, db_session: Session) -> None:
    """TimeEntriesEffort：実績工数は報告日以前の分の合計（5.8。報告日ちょうどは入れ、翌日は入れない）、実績の無い活動は 0。
    ETC は入力した残工数（EFF-06）、入力が無ければ空（RULE-EFF-001 はまだ無い。A-27）。"""
    owner, u1, u2 = _user(db_session, "owner"), _user(db_session, "u1"), _user(db_session, "u2")
    project, a, b = _project_with_activities(db_session, "P-001", owner)
    _entry(db_session, u1, a, 1, "3")
    _entry(db_session, u2, a, 1, "1.25")
    _entry(db_session, u1, a, 6, "2.5")
    _entry(db_session, u1, a, 7, "4")
    report = StatusReport(project_id=project.id, report_date=date(2026, 12, 6), created_by=owner.id)
    db_session.add(report)
    db_session.flush()
    entry = ProgressEntry(status_report_id=report.id, activity_id=a.id, remaining_hours=Decimal("12"), entered_by=u1.id)
    db_session.add(entry)
    db_session.flush()

    effort = TimeEntriesEffort().effort(db_session, project.id, date(2026, 12, 6), {a.id: entry}, [a.id, b.id])
    assert effort == {
        a.id: ActivityEffort(actual_hours=Decimal("6.75"), etc_hours=Decimal("12.00")),
        b.id: ActivityEffort(actual_hours=Decimal("0"), etc_hours=None),
    }
    assert TimeEntriesEffort().effort(db_session, project.id, date(2026, 12, 6), {}, []) == {}


def test_computed_etc_effort_applies_etc_rule(app: FastAPI, db_session: Session) -> None:
    """ComputedEtcEffort：実績工数は TimeEntriesEffort と同じ。ETC は RULE-EFF-001（BR-10）で求める。

    a：入力した残工数が優先（12）。b：進捗率 25%・見積 40 → 40×0.75＝30。c：未算定・見積 10・実績 4 → max(10−4, 0)＝6。
    d：進捗率 100（完了）→ 0（入力した残工数 7 があっても）。進捗率は渡した ``percents`` を使い、渡さなければ入力の行の値。
    """
    owner, u1 = _user(db_session, "owner"), _user(db_session, "u1")
    project, a, b = _project_with_activities(db_session, "P-001", owner)
    c = Activity(project_id=project.id, work_package_id=a.work_package_id, name="公開準備", sort_order=3, duration_days=2,
                 estimate_hours=Decimal("10"))
    d = Activity(project_id=project.id, work_package_id=a.work_package_id, name="環境整備", sort_order=4, duration_days=2,
                 estimate_hours=Decimal("8"))
    a.estimate_hours, b.estimate_hours = Decimal("20"), Decimal("40")
    db_session.add_all([c, d])
    db_session.flush()
    _entry(db_session, u1, c, 2, "4")
    _entry(db_session, u1, c, 9, "5")  # 報告日の後は実績に入れない
    report = StatusReport(project_id=project.id, report_date=date(2026, 12, 6), created_by=owner.id)
    db_session.add(report)
    db_session.flush()
    entries = {
        a.id: ProgressEntry(status_report_id=report.id, activity_id=a.id, percent_complete=Decimal("50"),
                            remaining_hours=Decimal("12"), entered_by=u1.id),
        b.id: ProgressEntry(status_report_id=report.id, activity_id=b.id, percent_complete=Decimal("25"), entered_by=u1.id),
        d.id: ProgressEntry(status_report_id=report.id, activity_id=d.id, percent_complete=Decimal("100"),
                            remaining_hours=Decimal("7"), entered_by=u1.id),
    }
    db_session.add_all(entries.values())
    db_session.flush()
    ids = [a.id, b.id, c.id, d.id]

    effort = ComputedEtcEffort().effort(db_session, project.id, date(2026, 12, 6), entries, ids)
    assert effort == {
        a.id: ActivityEffort(actual_hours=Decimal("0"), etc_hours=Decimal("12.00")),
        b.id: ActivityEffort(actual_hours=Decimal("0"), etc_hours=Decimal("30.00")),
        c.id: ActivityEffort(actual_hours=Decimal("4.00"), etc_hours=Decimal("6.00")),
        d.id: ActivityEffort(actual_hours=Decimal("0"), etc_hours=Decimal("0")),
    }
    # 確定が求めた進捗率（RULE-PRG-001）を渡せばそれを使う：b を 50% にすると 40×0.5＝20、c を 90% にすると 10×0.1＝1
    by_percent = ComputedEtcEffort().effort(db_session, project.id, date(2026, 12, 6), entries, ids,
                                            percents={b.id: Decimal("50"), c.id: Decimal("90")})
    assert (by_percent[b.id].etc_hours, by_percent[c.id].etc_hours) == (Decimal("20.00"), Decimal("1.00"))
    assert ComputedEtcEffort().effort(db_session, project.id, date(2026, 12, 6), {}, []) == {}
