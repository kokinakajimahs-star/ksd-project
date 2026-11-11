"""プロジェクトの有無とメンバーの立場を読む口（共通の群のサービスが、プロジェクト基盤の群の表を読むための境）。

共通（CMN）の API（API-CMN-003・006・007・008）は「プロジェクトがあるか」「その利用者がメンバーとしてどのロールを持つか」を要る。
その正は ``projects``（DB-PRJ-001）・``project_members``（DB-PRJ-005）・``assignments``（DB-EFF-001）で、
それぞれ別の群（db:PRJ・db:EFF）のスコープが作る（``graphs/group-needs.toml`` では api:CMN はそれらを待たない）。

そこで読み方をこの ``ProjectAccess`` に閉じ込め、アプリは ``app.state.project_access`` に 1 つ載せる。

- ``ProjectMembersTable``：``projects``・``project_members`` を読む実装（db:PRJ で差し替えた。docs/decisions/ASSUMPTIONS.md A-11）。
  割当の活動は ``AssignedActivities`` から読む
- ``AssignmentsTable``：割当の表（``assignments``。DB-EFF-001）を読む ``AssignedActivities``（db:EFF で差し替えた。A-27）
- ``AssignmentsTableAbsent``：割当の表がまだ無かった間の ``AssignedActivities``（割当は 1 つも無い）。今はアプリに載せない。
  載せ戻すと ``backend/tests/services/test_project_access.py::test_assignments_reader_is_wired_once_assignments_table_exists`` が落とす
- ``ProjectTablesAbsent``：プロジェクトの表がまだ無かった間の実装（プロジェクトは 1 つも無い）。今はアプリに載せない
  （``backend/tests/api/test_cmn.py::test_project_access_is_wired_once_project_tables_exist`` が確かめる。A-10）
"""

from __future__ import annotations

from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.authz import Membership
from app.models.eff import Assignment
from app.models.prj import Project, ProjectMember
from app.models.sch import Activity


class ProjectAccess(Protocol):
    """プロジェクトの有無と、利用者のメンバーとしての立場を読む。"""

    def project_exists(self, session: Session, project_id: int) -> bool:
        """プロジェクトがあるか。"""
        ...

    def membership(self, session: Session, project_id: int, user_id: int) -> Membership | None:
        """その利用者のそのプロジェクトでの立場（ロールと割当の活動）。メンバーでなければ ``None``。"""
        ...

    def memberships(self, session: Session, user_id: int) -> dict[int, Membership]:
        """その利用者がメンバーである全てのプロジェクトの立場（プロジェクトの ID → 立場）。"""
        ...


class ProjectTablesAbsent:
    """プロジェクトの表（``projects``・``project_members``）がまだ無い間の ``ProjectAccess``。プロジェクトは 1 つも無い。"""

    def project_exists(self, session: Session, project_id: int) -> bool:
        return False

    def membership(self, session: Session, project_id: int, user_id: int) -> Membership | None:
        return None

    def memberships(self, session: Session, user_id: int) -> dict[int, Membership]:
        return {}


class AssignedActivities(Protocol):
    """利用者に割り当てた活動（``assignments``）を読む。"""

    def activity_ids(self, session: Session, project_id: int, user_id: int) -> frozenset[int]:
        """そのプロジェクトで、その利用者に割り当てた活動の ID。"""
        ...


class AssignmentsTableAbsent:
    """割当の表（``assignments``。DB-EFF-001）がまだ無い間の ``AssignedActivities``。割当は 1 つも無い。"""

    def activity_ids(self, session: Session, project_id: int, user_id: int) -> frozenset[int]:
        return frozenset()


class AssignmentsTable:
    """``assignments``（DB-EFF-001）を読む ``AssignedActivities``（db:EFF で差し替えた。A-27）。

    そのプロジェクトの活動（``activities.project_id``）のうち、その利用者を担当に置いた活動の ID を返す。
    """

    def activity_ids(self, session: Session, project_id: int, user_id: int) -> frozenset[int]:
        rows = session.scalars(
            select(Assignment.activity_id)
            .join(Activity, Activity.id == Assignment.activity_id)
            .where(Activity.project_id == project_id, Assignment.user_id == user_id)
        )
        return frozenset(rows)


def _roles_of(member: ProjectMember) -> frozenset[str]:
    """``project_members.roles``（JSON 配列）をロールの集合にする。形が壊れていれば止める（権限を推し量らない）。"""
    roles: Any = member.roles
    if not isinstance(roles, list) or not all(isinstance(role, str) for role in roles):
        raise ValueError(
            f"project_members(id={member.id}).roles はロールのコードの配列でなければならない（{roles!r} が入っている）"
        )
    return frozenset(roles)


class ProjectMembersTable:
    """``projects``・``project_members`` を読む ``ProjectAccess``。割当の活動は ``assignments`` の読み手から得る。

    ロールのコードの検査は ``Membership`` が行う（要求仕様書 3 章に無いコードが入っていれば ``ValueError`` で止める）。
    """

    def __init__(self, assignments: AssignedActivities | None = None) -> None:
        self.assignments: AssignedActivities = assignments or AssignmentsTableAbsent()

    def project_exists(self, session: Session, project_id: int) -> bool:
        return session.scalar(select(Project.id).where(Project.id == project_id)) is not None

    def membership(self, session: Session, project_id: int, user_id: int) -> Membership | None:
        member = session.scalar(
            select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.user_id == user_id)
        )
        if member is None:
            return None
        return self._membership_of(session, member)

    def memberships(self, session: Session, user_id: int) -> dict[int, Membership]:
        members = session.scalars(
            select(ProjectMember).where(ProjectMember.user_id == user_id).order_by(ProjectMember.project_id)
        ).all()
        return {member.project_id: self._membership_of(session, member) for member in members}

    def _membership_of(self, session: Session, member: ProjectMember) -> Membership:
        return Membership(
            roles=_roles_of(member),
            assigned_activity_ids=self.assignments.activity_ids(session, member.project_id, member.user_id),
        )
