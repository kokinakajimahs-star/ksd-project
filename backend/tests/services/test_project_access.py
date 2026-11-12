"""プロジェクトの有無とメンバーの立場を読む口（``app.services.project_access.ProjectMembersTable``）の試験。

``projects``（DB-PRJ-001）・``project_members``（DB-PRJ-005）の行から、共通（CMN）のサービスが使う ``Membership`` を作ることと、
アプリ（``create_app``）がこの読み手を載せていることを確かめる（docs/decisions/ASSUMPTIONS.md A-10・A-11）。
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.domain.authz import Membership, default_role_permission_rows
from app.infra.db import Base
from app.infra.security import hash_password
from app.models.cmn import RolePermission, User
from app.models.prj import Project, ProjectMember
from app.services.clock import get_now
from app.services.project_access import AssignmentsTableAbsent, ProjectMembersTable

PASSWORD = "correct horse battery staple"


def _user(session: Session, login_id: str, *, password: str | None = None) -> User:
    user = User(
        login_id=login_id,
        display_name=f"{login_id} の表示名",
        password_hash=hash_password(password) if password else "unusable",
    )
    session.add(user)
    session.flush()
    return user


def _project(session: Session, code: str, owner: User) -> Project:
    project = Project(code=code, name=f"プロジェクト {code}", start_date=date(2026, 12, 1), owner_user_id=owner.id)
    session.add(project)
    session.flush()
    return project


def _member(session: Session, project: Project, user: User, roles: object) -> None:
    session.add(ProjectMember(project_id=project.id, user_id=user.id, roles=roles))
    session.flush()


class FixedAssignments:
    """試験用の割当（プロジェクト → 利用者 → 活動）。"""

    def __init__(self, table: dict[tuple[int, int], frozenset[int]]) -> None:
        self.table = table

    def activity_ids(self, session: Session, project_id: int, user_id: int) -> frozenset[int]:
        return self.table.get((project_id, user_id), frozenset())


def test_project_exists_follows_projects_rows(db_session: Session) -> None:
    """projects に行があるプロジェクトだけが「ある」。"""
    owner = _user(db_session, "owner")
    project = _project(db_session, "P-001", owner)
    access = ProjectMembersTable()
    assert access.project_exists(db_session, project.id) is True
    assert access.project_exists(db_session, project.id + 1) is False


def test_membership_reads_roles_and_none_for_non_member(db_session: Session) -> None:
    """project_members の roles（JSON 配列）がロールの集合になる。行の無い利用者・別のプロジェクトは None。"""
    owner = _user(db_session, "owner")
    member = _user(db_session, "member")
    project = _project(db_session, "P-001", owner)
    other = _project(db_session, "P-002", owner)
    _member(db_session, project, member, ["PLANNER", "EXECUTOR"])
    access = ProjectMembersTable()

    assert access.membership(db_session, project.id, member.id) == Membership(
        roles=frozenset({"PLANNER", "EXECUTOR"}), assigned_activity_ids=frozenset()
    )
    assert access.membership(db_session, project.id, owner.id) is None
    assert access.membership(db_session, other.id, member.id) is None


def test_memberships_lists_only_that_user_in_project_order(db_session: Session) -> None:
    """その利用者がメンバーである全てのプロジェクト（プロジェクトの ID の順）。他の利用者の行は含まない。"""
    owner = _user(db_session, "owner")
    member = _user(db_session, "member")
    first = _project(db_session, "P-001", owner)
    second = _project(db_session, "P-002", owner)
    third = _project(db_session, "P-003", owner)
    _member(db_session, third, member, ["VIEWER"])
    _member(db_session, first, member, ["PM"])
    _member(db_session, second, owner, ["PM"])
    access = ProjectMembersTable()

    memberships = access.memberships(db_session, member.id)
    assert list(memberships) == [first.id, third.id]
    assert memberships[first.id].roles == frozenset({"PM"})
    assert memberships[third.id].roles == frozenset({"VIEWER"})
    assert access.memberships(db_session, _user(db_session, "nobody").id) == {}


def test_assigned_activities_come_from_assignments_reader(db_session: Session) -> None:
    """割当の活動は AssignedActivities から読む。既定（assignments の表がまだ無い）は割当なし。"""
    owner = _user(db_session, "owner")
    contractor = _user(db_session, "contractor")
    project = _project(db_session, "P-001", owner)
    _member(db_session, project, contractor, ["CONTRACTOR"])

    assert ProjectMembersTable().membership(db_session, project.id, contractor.id) == Membership(
        roles=frozenset({"CONTRACTOR"})
    )
    access = ProjectMembersTable(FixedAssignments({(project.id, contractor.id): frozenset({10, 11})}))
    membership = access.membership(db_session, project.id, contractor.id)
    assert membership is not None
    assert membership.assigned_activity_ids == frozenset({10, 11})
    assert access.memberships(db_session, contractor.id)[project.id].assigned_activity_ids == frozenset({10, 11})


@pytest.mark.parametrize("roles", ["PM", {"role": "PM"}, ["PM", 1]], ids=["string", "object", "non-string-item"])
def test_broken_roles_shape_fails_fast(db_session: Session, roles: object) -> None:
    """roles がロールのコードの配列でなければ止める（権限を推し量らない）。"""
    owner = _user(db_session, "owner")
    member = _user(db_session, "member")
    project = _project(db_session, "P-001", owner)
    _member(db_session, project, member, roles)
    with pytest.raises(ValueError, match="roles"):
        ProjectMembersTable().membership(db_session, project.id, member.id)


def test_unknown_role_code_fails_fast(db_session: Session) -> None:
    """要求仕様書 3 章に無いロールのコードが入っていれば止める（Membership の検査。RULE-AUTH-001）。"""
    owner = _user(db_session, "owner")
    member = _user(db_session, "member")
    project = _project(db_session, "P-001", owner)
    _member(db_session, project, member, ["SUPERUSER"])
    with pytest.raises(ValueError):
        ProjectMembersTable().memberships(db_session, member.id)


def test_app_wires_project_members_table(app: FastAPI) -> None:
    """アプリは projects・project_members を読む ProjectMembersTable を載せる。"""
    assert isinstance(app.state.project_access, ProjectMembersTable)


def test_assignments_reader_is_wired_once_assignments_table_exists(app: FastAPI) -> None:
    """``assignments``（DB-EFF-001）を作ったスコープは、それを読む AssignedActivities に差し替える（A-11）。

    表がまだ無い間は AssignmentsTableAbsent（割当は 1 つも無い）。表ができたのに差し替えていなければ落とす。
    """
    access = app.state.project_access
    assert isinstance(access, ProjectMembersTable)
    if "assignments" in Base.metadata.tables:
        assert not isinstance(access.assignments, AssignmentsTableAbsent)
    else:
        assert isinstance(access.assignments, AssignmentsTableAbsent)


def test_me_reads_memberships_from_project_members(client: TestClient, app: FastAPI, db_session: Session) -> None:
    """API-CMN-008 を、試験の差し替え無しで（アプリの既定の読み手で）流すと、project_members の行がプロジェクトの一覧になる。"""
    app.dependency_overrides[get_now] = lambda: datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
    owner = _user(db_session, "owner")
    member = _user(db_session, "member", password=PASSWORD)
    project = _project(db_session, "P-001", owner)
    for role, permission in default_role_permission_rows():
        db_session.add(RolePermission(project_id=project.id, role=role, permission=permission))
    _member(db_session, project, member, ["VIEWER", "PLANNER"])
    db_session.commit()

    login = client.post("/api/v1/auth/login", json={"login_id": "member", "password": PASSWORD})
    assert login.status_code == 200, login.text
    response = client.get("/api/v1/me", headers={"Authorization": f"Bearer {login.json()['access_token']}"})
    assert response.status_code == 200, response.text
    projects = response.json()["projects"]
    assert [item["project_id"] for item in projects] == [project.id]
    assert projects[0]["roles"] == ["PLANNER", "VIEWER"]
    assert "wbs.edit" in projects[0]["permissions"]
    assert projects[0]["view_level"] == "ALL"
