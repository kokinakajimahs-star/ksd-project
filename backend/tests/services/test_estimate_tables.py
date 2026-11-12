"""見積（EST）の表を読む口の試験（db:EST で差し替えた口。docs/decisions/ASSUMPTIONS.md A-32）。

- ``app.services.wbs.estimate_records_table``：横断照会（API-WBS-023・RULE-WBS-008）が読む見積の記録の対象

**別のプロジェクトの行を混ぜない**ことを、プロジェクトを 2 つ置いて確かめる。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.domain.wbs_rules import EstimateRecordRef, EstimateTarget
from app.models.cmn import User
from app.models.est import EstimateRecord
from app.models.prj import Project
from app.models.sch import Activity
from app.models.wbs import WbsElement
from app.services.wbs import estimate_records_table

MISSING = 999_999


def _user(session: Session, login_id: str) -> User:
    user = User(login_id=login_id, display_name="利用者", password_hash="scrypt$dummy")
    session.add(user)
    session.flush()
    return user


def _project(session: Session, code: str, owner: User) -> tuple[Project, WbsElement, Activity]:
    project = Project(code=code, name="プロジェクト", start_date=date(2026, 12, 1), owner_user_id=owner.id)
    session.add(project)
    session.flush()
    wp = WbsElement(project_id=project.id, kind="WORK_PACKAGE", name="WP", wbs_code="1", sibling_seq=1, sort_order=1)
    session.add(wp)
    session.flush()
    activity = Activity(project_id=project.id, work_package_id=wp.id, name="資料作成", sort_order=1, duration_days=5)
    session.add(activity)
    session.flush()
    return project, wp, activity


def _record(session: Session, project: Project, user: User, target_type: str, target_id: int,
            hours: str) -> EstimateRecord:
    record = EstimateRecord(project_id=project.id, target_type=target_type, target_id=target_id, hours=Decimal(hours),
                            input_value=Decimal(hours), input_unit="HOUR", entered_by=user.id)
    session.add(record)
    session.flush()
    return record


def test_estimate_records_table_reads_targets_of_the_project(db_session: Session) -> None:
    """見積の記録の対象（種類と ID）を、そのプロジェクトの全ての記録（同じ対象の古い記録も）から ID の順に読む。"""
    planner = _user(db_session, "planner")
    project, wp, activity = _project(db_session, "P-1", planner)
    other, other_wp, _oa = _project(db_session, "P-2", planner)
    first = _record(db_session, project, planner, "ELEMENT", wp.id, "40")
    _record(db_session, other, planner, "ELEMENT", other_wp.id, "8")
    second = _record(db_session, project, planner, "ACTIVITY", activity.id, "16")
    third = _record(db_session, project, planner, "ELEMENT", wp.id, "48")
    db_session.commit()

    assert estimate_records_table(db_session, project.id) == [
        EstimateRecordRef(id=first.id, target_type=EstimateTarget.ELEMENT, target_id=wp.id),
        EstimateRecordRef(id=second.id, target_type=EstimateTarget.ACTIVITY, target_id=activity.id),
        EstimateRecordRef(id=third.id, target_type=EstimateTarget.ELEMENT, target_id=wp.id),
    ]
    assert estimate_records_table(db_session, MISSING) == []
