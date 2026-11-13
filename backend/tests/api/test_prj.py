"""プロジェクト基盤（PRJ）の API の試験（設計書 6.2 の API-PRJ-001〜011・4.1・4.2・6.1・9 章・12 章）。

SQLite の一時ファイルの DB と TestClient で、正常系・認証なし（401）・権限なし（403）・無い（404）・重複（409）・
業務ルールの違反（422）・入力の誤り（400）のうち該当するものを確かめる。「今」は ``get_now`` を差し替えて固定する。

メンバーの立場はアプリの既定の ``ProjectMembersTable``（``projects``・``project_members`` を読む）から得る。
試験の準備はメンバーを足す API（API-PRJ-010）で行う。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.authz import ROLES, default_role_permission_rows
from app.infra.db import Base
from app.infra.security import generate_token, hash_token
from app.models.cmn import AuthSession, RolePermission, User
from app.models.prj import Calendar, CalendarHoliday, Project, ProjectMember, ScopeItem, default_project_settings
from app.services.clock import get_now
from app.models.sch import Activity
from app.models.wbs import WbsElement, WbsNumberingRule
from app.services.projects import (
    ActivitiesTableAbsent,
    NumberingRulesTable,
    NumberingRulesTableAbsent,
    SchedulingNotWired,
)

NOW = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
MISSING = 9999


# ---------------------------------------------------------------------------
# 準備
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def fixed_now(app: FastAPI) -> Iterator[None]:
    app.dependency_overrides[get_now] = lambda: NOW
    yield
    app.dependency_overrides.pop(get_now, None)


def make_user(session: Session, login_id: str, *, is_system_admin: bool = False, is_active: bool = True) -> User:
    # ログインの API は通さない（scrypt を試験ごとに何度も回さない。ログインは test_cmn.py が確かめる）。
    # パスワードのハッシュは形の壊れた値で、どのパスワードとも合わない
    user = User(
        login_id=login_id,
        display_name=f"{login_id} の表示名",
        password_hash="unusable",
        is_system_admin=is_system_admin,
        is_active=is_active,
    )
    session.add(user)
    session.commit()
    return user


def login(session: Session, user: User) -> dict[str, str]:
    """ログイン（API-CMN-001）が発行するのと同じ形の認証トークンを DB に置き、その Bearer の見出しを返す。"""
    token = generate_token()
    session.add(
        AuthSession(user_id=user.id, token_hash=hash_token(token), expires_at=NOW + timedelta(hours=12), created_at=NOW)
    )
    session.commit()
    return {"Authorization": f"Bearer {token}"}


class Recorder:
    """採番規則の口・日程の再計算の口の代わり。呼ばれたプロジェクトを記録し、指定されれば失敗する。"""

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[int] = []
        self.fail = fail

    def _record(self, project_id: int) -> None:
        self.calls.append(project_id)
        if self.fail:
            raise RuntimeError("試験の失敗")

    def create_default(self, session: Session, project_id: int) -> None:
        self._record(project_id)

    def recalculate(self, session: Session, project_id: int) -> None:
        self._record(project_id)


class World:
    """システム管理者・PM・メンバー・メンバーでない利用者と、管理者が作った 1 つのプロジェクト。"""

    def __init__(self, client: TestClient, session: Session) -> None:
        self.client = client
        self.session = session
        self.admin = make_user(session, "admin", is_system_admin=True)
        self.pm = make_user(session, "pm")
        self.planner = make_user(session, "planner")
        self.viewer = make_user(session, "viewer")
        self.contractor = make_user(session, "contractor")
        self.outsider = make_user(session, "outsider")
        self.admin_h = login(session, self.admin)
        self.project = self.create_project(code="PRJ-001", name="基幹システム更改")
        pid = self.project["id"]
        self.put_member(pid, self.pm.id, ["PM"])
        self.put_member(pid, self.planner.id, ["PLANNER"])
        self.put_member(pid, self.viewer.id, ["VIEWER"])
        self.put_member(pid, self.contractor.id, ["CONTRACTOR"])
        self.pm_h = login(session, self.pm)
        self.planner_h = login(session, self.planner)
        self.viewer_h = login(session, self.viewer)
        self.contractor_h = login(session, self.contractor)
        self.outsider_h = login(session, self.outsider)

    @property
    def pid(self) -> int:
        project_id: int = self.project["id"]
        return project_id

    def create_project(self, **overrides: Any) -> dict[str, Any]:
        body = {"code": "P", "name": "名称", "start_date": "2026-12-01", "owner_user_id": self.admin.id}
        body.update(overrides)
        response = self.client.post("/api/v1/projects", json=body, headers=self.admin_h)
        assert response.status_code == 201, response.text
        project: dict[str, Any] = response.json()
        return project

    def put_member(self, project_id: int, user_id: int, roles: list[str], **extra: Any) -> dict[str, Any]:
        response = self.client.put(
            f"/api/v1/projects/{project_id}/members/{user_id}", json={"roles": roles, **extra}, headers=self.admin_h
        )
        assert response.status_code == 200, response.text
        member: dict[str, Any] = response.json()
        return member


@pytest.fixture()
def world(client: TestClient, db_session: Session) -> World:
    return World(client, db_session)


def error_code(response: Any) -> str:
    code: str = response.json()["error"]["code"]
    return code


# ---------------------------------------------------------------------------
# 認証・プロジェクトが無い
# ---------------------------------------------------------------------------

_ENDPOINTS: list[tuple[str, str, dict[str, Any] | None]] = [
    ("POST", "/api/v1/projects", {"code": "X", "name": "X", "start_date": "2026-12-01", "owner_user_id": 1}),
    ("GET", "/api/v1/projects", None),
    ("GET", "/api/v1/projects/{pid}", None),
    ("PATCH", "/api/v1/projects/{pid}", {"name": "X"}),
    ("GET", "/api/v1/projects/{pid}/scope-items", None),
    ("PUT", "/api/v1/projects/{pid}/scope-items", {"items": []}),
    ("GET", "/api/v1/projects/{pid}/calendar", None),
    ("PUT", "/api/v1/projects/{pid}/calendar", {"working_weekdays": "1111100", "hours_per_day": 8, "holidays": []}),
    ("GET", "/api/v1/projects/{pid}/members", None),
    ("PUT", "/api/v1/projects/{pid}/members/{uid}", {"roles": ["VIEWER"]}),
    ("DELETE", "/api/v1/projects/{pid}/members/{uid}", None),
]


@pytest.mark.parametrize(("method", "path", "body"), _ENDPOINTS, ids=[f"{m} {p}" for m, p, _ in _ENDPOINTS])
def test_all_endpoints_require_login(
    client: TestClient, world: World, method: str, path: str, body: dict[str, Any] | None
) -> None:
    url = path.format(pid=world.pid, uid=world.viewer.id)
    response = client.request(method, url, json=body)
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert error_code(response) == "UNAUTHORIZED"


@pytest.mark.parametrize(
    ("method", "path", "body"), _ENDPOINTS[2:], ids=[f"{m} {p}" for m, p, _ in _ENDPOINTS[2:]]
)
def test_project_endpoints_return_404_for_missing_project_before_403(
    client: TestClient, world: World, method: str, path: str, body: dict[str, Any] | None
) -> None:
    url = path.format(pid=MISSING, uid=world.viewer.id)
    for headers in (world.admin_h, world.outsider_h):
        response = client.request(method, url, json=body, headers=headers)
        assert response.status_code == 404, response.text
        assert error_code(response) == "NOT_FOUND"


# ---------------------------------------------------------------------------
# プロジェクトを作る（project.create）
# ---------------------------------------------------------------------------


def test_admin_creates_project_with_calendar_permissions_and_defaults(
    client: TestClient, db_session: Session, world: World
) -> None:
    body = {
        "code": "PRJ-100",
        "name": "新しいプロジェクト",
        "purpose": "目的",
        "requirements": "要求事項",
        "key_deliverables": "主要成果物",
        "start_date": "2027-01-04",
        "target_finish_date": "2027-03-31",
        "owner_user_id": world.pm.id,
    }
    response = client.post("/api/v1/projects", json=body, headers=world.admin_h)
    assert response.status_code == 201, response.text
    created = response.json()
    pid = created["id"]
    assert {k: created[k] for k in body} == body
    assert created["owner"] == {"id": world.pm.id, "login_id": "pm", "display_name": "pm の表示名"}
    assert created["wbs_status"] == "DRAFT"
    assert created["settings"] == default_project_settings()  # 4.2 の既定

    db_session.expire_all()
    # 稼働カレンダー：4.1 の既定（土日休み・8 時間）。休日は無い
    calendar = db_session.scalar(select(Calendar).where(Calendar.project_id == pid))
    assert calendar is not None
    assert calendar.working_weekdays == "1111100"
    assert Decimal(calendar.hours_per_day) == Decimal("8")
    assert db_session.scalars(select(CalendarHoliday).where(CalendarHoliday.calendar_id == calendar.id)).all() == []
    # ロール×権限の表：9.2 の既定を写す
    rows = db_session.execute(
        select(RolePermission.role, RolePermission.permission).where(RolePermission.project_id == pid)
    ).all()
    assert sorted(rows) == sorted(default_role_permission_rows())
    # システム管理者が作るとメンバーは作らない（システム管理者は全ての権限を持つ。9.2・CR-014）
    assert db_session.scalars(select(ProjectMember).where(ProjectMember.project_id == pid)).all() == []

    # 詳細・既定のカレンダー・権限の表が API から読める
    assert client.get(f"/api/v1/projects/{pid}", headers=world.admin_h).json() == created
    calendar_out = client.get(f"/api/v1/projects/{pid}/calendar", headers=world.admin_h).json()
    assert calendar_out == {"project_id": pid, "working_weekdays": "1111100", "hours_per_day": 8.0, "holidays": []}
    table = client.get(f"/api/v1/projects/{pid}/role-permissions", headers=world.admin_h).json()
    assert {grant["role"]: grant["permissions"] for grant in table["roles"]}["PM"][:3] == [
        "project.view",
        "project.manage",
        "member.manage",
    ]


def test_create_project_calls_numbering_rules_in_same_transaction(
    app: FastAPI, client: TestClient, db_session: Session, world: World
) -> None:
    recorder = Recorder()
    app.state.numbering_rules = recorder
    created = world.create_project(code="PRJ-200")
    assert recorder.calls == [created["id"]]

    # 採番規則を作れなければ、プロジェクト・カレンダー・権限の表も残らない（1 つのトランザクション。1.3）
    app.state.numbering_rules = Recorder(fail=True)
    with pytest.raises(RuntimeError):
        client.post(
            "/api/v1/projects",
            json={"code": "PRJ-201", "name": "X", "start_date": "2026-12-01", "owner_user_id": world.admin.id},
            headers=world.admin_h,
        )
    db_session.expire_all()
    assert db_session.scalar(select(Project).where(Project.code == "PRJ-201")) is None
    assert db_session.scalars(select(Calendar).join(Project, Project.id == Calendar.project_id).where(
        Project.code == "PRJ-201"
    )).all() == []


def test_create_project_requires_project_create(client: TestClient, world: World) -> None:
    # プロジェクトの PM でも、システムの権限 project.create は持たない（9.1。個別の許可は users.can_create_project。CR-010）
    body = {"code": "PRJ-300", "name": "X", "start_date": "2026-12-01", "owner_user_id": world.pm.id}
    for headers in (world.pm_h, world.outsider_h):
        response = client.post("/api/v1/projects", json=body, headers=headers)
        assert response.status_code == 403
        assert error_code(response) == "FORBIDDEN"


def test_create_project_allowed_by_can_create_project(client: TestClient, world: World) -> None:
    # 9.1：システム管理者が許したユーザー（API-CMN-005 の can_create_project）はプロジェクトを作れる（RULE-AUTH-001 の
    # system_permissions）。許しを外すと 403 に戻る
    body = {"code": "PRJ-301", "name": "X", "start_date": "2026-12-01", "owner_user_id": world.outsider.id}
    granted = client.patch(f"/api/v1/users/{world.outsider.id}", json={"can_create_project": True}, headers=world.admin_h)
    assert granted.status_code == 200, granted.text
    created = client.post("/api/v1/projects", json=body, headers=world.outsider_h)
    assert created.status_code == 201, created.text

    revoked = client.patch(f"/api/v1/users/{world.outsider.id}", json={"can_create_project": False}, headers=world.admin_h)
    assert revoked.status_code == 200
    response = client.post("/api/v1/projects", json={**body, "code": "PRJ-302"}, headers=world.outsider_h)
    assert response.status_code == 403
    assert error_code(response) == "FORBIDDEN"


def test_creator_who_is_not_system_admin_becomes_pm_member(
    client: TestClient, db_session: Session, world: World
) -> None:
    # API-PRJ-001（設計書 6.2・CR-014）：システム管理者でない人が作ると、同じトランザクションで作った人をメンバー（ロール PM・
    # 所属は空）にする。作った直後から読め（API-PRJ-002・003・009）、API-CMN-008 の projects に載る
    granted = client.patch(f"/api/v1/users/{world.outsider.id}", json={"can_create_project": True}, headers=world.admin_h)
    assert granted.status_code == 200, granted.text
    body = {"code": "PRJ-303", "name": "X", "start_date": "2026-12-01", "owner_user_id": world.pm.id}
    created = client.post("/api/v1/projects", json=body, headers=world.outsider_h)
    assert created.status_code == 201, created.text
    pid = created.json()["id"]

    db_session.expire_all()
    members = db_session.scalars(select(ProjectMember).where(ProjectMember.project_id == pid)).all()
    assert [(m.user_id, m.roles, m.organization, m.team) for m in members] == [(world.outsider.id, ["PM"], None, None)]

    assert client.get(f"/api/v1/projects/{pid}", headers=world.outsider_h).json() == created.json()
    listed = client.get("/api/v1/projects", headers=world.outsider_h).json()
    assert [p["id"] for p in listed["items"]] == [pid]
    member_list = client.get(f"/api/v1/projects/{pid}/members", headers=world.outsider_h).json()
    assert [(m["user_id"], m["roles"], m["organization"], m["team"]) for m in member_list["items"]] == [
        (world.outsider.id, ["PM"], None, None)]
    me = client.get("/api/v1/me", headers=world.outsider_h).json()
    mine = [p for p in me["projects"] if p["project_id"] == pid]
    assert len(mine) == 1 and mine[0]["roles"] == ["PM"] and mine[0]["view_level"] == "ALL"
    assert {"project.manage", "member.manage", "permission.manage"} <= set(mine[0]["permissions"])
    # PM として基本情報を変えられる（9.2 の既定の表）。責任者（owner）はメンバーにしない
    assert client.patch(f"/api/v1/projects/{pid}", json={"name": "Y"}, headers=world.outsider_h).status_code == 200
    assert client.get(f"/api/v1/projects/{pid}", headers=world.pm_h).status_code == 403

    # システム管理者が作ってもメンバーは増えない
    by_admin = world.create_project(code="PRJ-304")
    db_session.expire_all()
    assert db_session.scalars(select(ProjectMember).where(ProjectMember.project_id == by_admin["id"])).all() == []


def test_create_project_duplicate_code_is_409(client: TestClient, world: World) -> None:
    body = {"code": "PRJ-001", "name": "別の名前", "start_date": "2026-12-01", "owner_user_id": world.admin.id}
    response = client.post("/api/v1/projects", json=body, headers=world.admin_h)
    assert response.status_code == 409
    assert error_code(response) == "DUPLICATE_PROJECT_CODE"


@pytest.mark.parametrize(
    "change",
    [
        {"name": None},  # 名称は必須（PRJ-01）
        {"name": ""},
        {"start_date": None},  # 開始日は必須（PRJ-01）
        {"owner_user_id": None},  # 責任者は必須（PRJ-01）
        {"code": ""},
        {"code": "C" * 51},
        {"start_date": "2026-13-01"},
        {"target_finish_date": "2026-11-30"},  # 開始日より前
        {"owner_user_id": MISSING},  # 責任者が無い
        {"owner_user_id": "inactive"},  # 責任者が無効
        {"wbs_status": "BASELINED"},  # 状態は作るときに書けない
        {"settings": {}},
    ],
    ids=lambda change: next(iter(change)) + "=" + str(next(iter(change.values()))),
)
def test_create_project_input_errors_are_400(
    client: TestClient, db_session: Session, world: World, change: dict[str, Any]
) -> None:
    body: dict[str, Any] = {"code": "PRJ-400", "name": "X", "start_date": "2026-12-01", "owner_user_id": world.admin.id}
    if change.get("owner_user_id") == "inactive":
        change = {"owner_user_id": make_user(db_session, "gone", is_active=False).id}
    body.update(change)
    body = {key: value for key, value in body.items() if value is not None}
    response = client.post("/api/v1/projects", json=body, headers=world.admin_h)
    assert response.status_code == 400, response.text
    assert error_code(response) == "VALIDATION_ERROR"
    db_session.expire_all()
    assert db_session.scalar(select(Project).where(Project.code == "PRJ-400")) is None


def test_target_finish_date_equal_to_start_is_allowed(world: World) -> None:
    created = world.create_project(code="PRJ-401", start_date="2026-12-01", target_finish_date="2026-12-01")
    assert created["target_finish_date"] == "2026-12-01"


# ---------------------------------------------------------------------------
# 一覧・詳細（project.view）
# ---------------------------------------------------------------------------


def test_list_projects_shows_only_visible_projects(client: TestClient, world: World) -> None:
    second = world.create_project(code="PRJ-002", name="二つ目")
    third = world.create_project(code="PRJ-003", name="三つ目")
    world.put_member(third["id"], world.pm.id, ["VIEWER"])

    def codes(headers: dict[str, str]) -> list[str]:
        response = client.get("/api/v1/projects", headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == len(body["items"])
        return [item["code"] for item in body["items"]]

    assert codes(world.admin_h) == ["PRJ-001", "PRJ-002", "PRJ-003"]  # システム管理者は全て
    assert codes(world.pm_h) == ["PRJ-001", "PRJ-003"]  # メンバーであるものだけ（ID の順）
    assert codes(world.contractor_h) == ["PRJ-001"]  # 委託先も割当の範囲に絞って見られる
    assert codes(world.outsider_h) == []
    assert second["id"] not in [p["id"] for p in client.get("/api/v1/projects", headers=world.pm_h).json()["items"]]


def test_list_projects_follows_project_role_table(client: TestClient, world: World) -> None:
    # VIEWER から project.view を外した表（API-CMN-007）では、VIEWER だけのメンバーに見えない
    table = client.get(f"/api/v1/projects/{world.pid}/role-permissions", headers=world.pm_h).json()
    roles = [
        {"role": g["role"], "permissions": [p for p in g["permissions"] if g["role"] != "VIEWER" or p != "project.view"]}
        for g in table["roles"]
    ]
    put = client.put(f"/api/v1/projects/{world.pid}/role-permissions", json={"roles": roles}, headers=world.pm_h)
    assert put.status_code == 200, put.text
    listed = client.get("/api/v1/projects", headers=world.viewer_h).json()
    assert listed["items"] == [] and listed["total"] == 0
    assert client.get(f"/api/v1/projects/{world.pid}", headers=world.viewer_h).status_code == 403


def test_list_projects_paging(client: TestClient, world: World) -> None:
    for number in range(2, 6):
        world.create_project(code=f"PRJ-{number:03d}")
    body = client.get("/api/v1/projects?limit=2&offset=1", headers=world.admin_h).json()
    assert [item["code"] for item in body["items"]] == ["PRJ-002", "PRJ-003"]
    assert (body["total"], body["limit"], body["offset"]) == (5, 2, 1)
    for query in ("limit=0", "limit=1001", "offset=-1"):
        response = client.get(f"/api/v1/projects?{query}", headers=world.admin_h)
        assert response.status_code == 400, query


def test_get_project_detail_by_member_and_403_for_others(client: TestClient, world: World) -> None:
    for headers in (world.pm_h, world.viewer_h, world.contractor_h):
        response = client.get(f"/api/v1/projects/{world.pid}", headers=headers)
        assert response.status_code == 200
        assert response.json()["code"] == "PRJ-001"
    response = client.get(f"/api/v1/projects/{world.pid}", headers=world.outsider_h)
    assert response.status_code == 403
    assert error_code(response) == "FORBIDDEN"


# ---------------------------------------------------------------------------
# 基本情報・設定を更新する（project.manage）
# ---------------------------------------------------------------------------


def test_pm_updates_only_given_fields_and_merges_settings(client: TestClient, world: World) -> None:
    patch = {
        "name": "改名",
        "purpose": "新しい目的",
        "target_finish_date": "2027-06-30",
        "owner_user_id": world.pm.id,
        "settings": {"progress_method_default": "FIFTY_FIFTY", "delay_tolerance_days": 1.0},
    }
    response = client.patch(f"/api/v1/projects/{world.pid}", json=patch, headers=world.pm_h)
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["name"] == "改名"
    assert updated["purpose"] == "新しい目的"
    assert updated["target_finish_date"] == "2027-06-30"
    assert updated["owner"]["id"] == world.pm.id
    assert updated["code"] == "PRJ-001" and updated["start_date"] == "2026-12-01"  # 載せていない項目は変わらない
    expected_settings = {**default_project_settings(), "progress_method_default": "FIFTY_FIFTY", "delay_tolerance_days": 1.0}
    assert updated["settings"] == expected_settings
    assert client.get(f"/api/v1/projects/{world.pid}", headers=world.viewer_h).json() == updated

    # 空にできる項目は null で消せる
    cleared = client.patch(
        f"/api/v1/projects/{world.pid}", json={"purpose": None, "target_finish_date": None}, headers=world.pm_h
    ).json()
    assert cleared["purpose"] is None and cleared["target_finish_date"] is None
    assert cleared["settings"] == expected_settings


def test_update_project_requires_project_manage(client: TestClient, world: World) -> None:
    for headers in (world.planner_h, world.viewer_h, world.contractor_h, world.outsider_h):
        response = client.patch(f"/api/v1/projects/{world.pid}", json={"name": "X"}, headers=headers)
        assert response.status_code == 403
        assert error_code(response) == "FORBIDDEN"
    assert client.patch(f"/api/v1/projects/{world.pid}", json={"name": "管理者"}, headers=world.admin_h).status_code == 200


@pytest.mark.parametrize(
    "patch",
    [
        {"name": None},
        {"code": None},
        {"start_date": None},
        {"owner_user_id": None},
        {"settings": None},
        {"settings": {"variance_rate": None}},
        {"settings": {"variance_rate": -0.1}},
        {"settings": {"delay_extract_days": -1}},
        # 許容幅はスナップショットの Numeric(6,2) に写すので、小数 2 桁・9999.99 まで（丸めない。A-49 の 4）
        {"settings": {"delay_tolerance_days": 0.555}},
        {"settings": {"delay_extract_days": 10000}},
        {"settings": {"delay_tolerance_days": 1e-7}},
        {"settings": {"progress_method_default": "EARNED"}},
        {"settings": {"unknown": 1}},
        {"wbs_status": "BASELINED"},  # WBS の状態は 5.10 の遷移で変わる
        {"target_finish_date": "2026-11-30"},  # 今の開始日 2026-12-01 より前
        {"start_date": "2027-01-01", "target_finish_date": "2026-12-31"},
        {"owner_user_id": MISSING},
        {"name": ""},
    ],
    ids=lambda patch: ",".join(f"{k}={v}" for k, v in patch.items()),
)
def test_update_project_input_errors_are_400(client: TestClient, world: World, patch: dict[str, Any]) -> None:
    before = client.get(f"/api/v1/projects/{world.pid}", headers=world.pm_h).json()
    response = client.patch(f"/api/v1/projects/{world.pid}", json=patch, headers=world.pm_h)
    assert response.status_code == 400, response.text
    assert error_code(response) == "VALIDATION_ERROR"
    assert client.get(f"/api/v1/projects/{world.pid}", headers=world.pm_h).json() == before


@pytest.mark.parametrize(
    ("values", "error_type"),
    [
        ({"delay_tolerance_days": 0.555}, "decimal_max_places"),
        ({"delay_extract_days": 10000}, "decimal_whole_digits"),
        ({"delay_extract_days": 12345.5}, "decimal_whole_digits"),
    ],
)
def test_update_project_tolerance_settings_fit_the_snapshot_columns(
    client: TestClient, world: World, values: dict[str, Any], error_type: str
) -> None:
    """A-49 の 4（OQ-42 (8)）：遅れの許容幅・抽出の閾値は、報告日の確定で写す ``progress_snapshots`` の ``Numeric(6,2)`` に収まる値
    （9999.99 まで・小数 2 桁まで）だけを受ける。超えた値は丸めずに 400。"""
    response = client.patch(f"/api/v1/projects/{world.pid}", json={"settings": values}, headers=world.pm_h)
    assert response.status_code == 400, response.text
    (name,) = values
    assert [(d["loc"], d["type"]) for d in response.json()["error"]["details"]] == [(["body", "settings", name], error_type)]

    upper = client.patch(f"/api/v1/projects/{world.pid}",
                         json={"settings": {"delay_tolerance_days": 9999.99, "delay_extract_days": 0.01}}, headers=world.pm_h)
    assert upper.status_code == 200, upper.text
    stored = upper.json()["settings"]
    assert (stored["delay_tolerance_days"], stored["delay_extract_days"]) == (9999.99, 0.01)


def test_update_project_finish_before_new_start_is_400(client: TestClient, world: World) -> None:
    client.patch(f"/api/v1/projects/{world.pid}", json={"target_finish_date": "2027-01-31"}, headers=world.pm_h)
    response = client.patch(f"/api/v1/projects/{world.pid}", json={"start_date": "2027-02-01"}, headers=world.pm_h)
    assert response.status_code == 400


def test_update_project_code_change_and_duplicate_is_409(client: TestClient, world: World) -> None:
    world.create_project(code="PRJ-002")
    response = client.patch(f"/api/v1/projects/{world.pid}", json={"code": "PRJ-002"}, headers=world.pm_h)
    assert response.status_code == 409
    assert error_code(response) == "DUPLICATE_PROJECT_CODE"
    renamed = client.patch(f"/api/v1/projects/{world.pid}", json={"code": "PRJ-001A"}, headers=world.pm_h)
    assert renamed.status_code == 200 and renamed.json()["code"] == "PRJ-001A"
    # 自分のコードのままは重複にならない
    assert client.patch(f"/api/v1/projects/{world.pid}", json={"code": "PRJ-001A"}, headers=world.pm_h).status_code == 200


def test_changing_start_date_recalculates_schedule(app: FastAPI, client: TestClient, world: World) -> None:
    recorder = Recorder()
    app.state.schedule_recalculator = recorder
    client.patch(f"/api/v1/projects/{world.pid}", json={"name": "日程は変わらない"}, headers=world.pm_h)
    client.patch(f"/api/v1/projects/{world.pid}", json={"start_date": "2026-12-01"}, headers=world.pm_h)  # 同じ日
    assert recorder.calls == []
    client.patch(f"/api/v1/projects/{world.pid}", json={"start_date": "2026-12-02"}, headers=world.pm_h)
    assert recorder.calls == [world.pid]


def test_start_date_after_remaining_requested_start_is_422(client: TestClient, db_session: Session, world: World) -> None:
    """API-PRJ-004：新しい開始日より前の希望開始日の活動が残る開始日の変更は 422 ``BEFORE_PROJECT_START`` で、何も書かない
    （5.5・CR-018）。希望開始日ちょうど・それより前への変更と、希望開始日の無い活動は拒まない。
    """
    url = f"/api/v1/projects/{world.pid}"
    wp = WbsElement(project_id=world.pid, kind="WORK_PACKAGE", name="WP", wbs_code="1.0", sibling_seq=1, sort_order=1)
    db_session.add(wp)
    db_session.flush()
    early = Activity(project_id=world.pid, work_package_id=wp.id, name="12/3 から", sort_order=1, duration_days=1,
                     requested_start=date(2026, 12, 3))
    db_session.add_all([early, Activity(project_id=world.pid, work_package_id=wp.id, name="希望なし", sort_order=2,
                                        duration_days=1)])
    db_session.commit()
    name = client.get(url, headers=world.pm_h).json()["name"]

    refused = client.patch(url, json={"start_date": "2026-12-04", "name": "変えない"}, headers=world.pm_h)
    assert refused.status_code == 422, refused.text
    assert error_code(refused) == "BEFORE_PROJECT_START"
    assert refused.json()["error"]["details"] == [
        {"loc": ["body", "start_date"], "activity_id": early.id, "requested_start": "2026-12-03",
         "project_start": "2026-12-04", "message": "希望開始日 2026-12-03 がプロジェクトの開始日 2026-12-04 より前です"}]
    current = client.get(url, headers=world.pm_h).json()
    assert (current["start_date"], current["name"]) == ("2026-12-01", name)
    assert client.patch(url, json={"start_date": "2026-12-03"}, headers=world.pm_h).status_code == 200
    assert client.patch(url, json={"start_date": "2026-11-20"}, headers=world.pm_h).status_code == 200


def test_failed_recalculation_rolls_back_start_date(app: FastAPI, client: TestClient, world: World) -> None:
    app.state.schedule_recalculator = Recorder(fail=True)
    with pytest.raises(RuntimeError):
        client.patch(f"/api/v1/projects/{world.pid}", json={"start_date": "2026-12-02"}, headers=world.pm_h)
    app.state.schedule_recalculator = ActivitiesTableAbsent()
    assert client.get(f"/api/v1/projects/{world.pid}", headers=world.pm_h).json()["start_date"] == "2026-12-01"


# ---------------------------------------------------------------------------
# スコープ項目（読む：project.view・置き換える：project.manage）
# ---------------------------------------------------------------------------


def _scope(client: TestClient, world: World, headers: dict[str, str] | None = None) -> dict[str, Any]:
    response = client.get(f"/api/v1/projects/{world.pid}/scope-items", headers=headers or world.viewer_h)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def test_pm_replaces_scope_items_keeping_ids(client: TestClient, db_session: Session, world: World) -> None:
    assert _scope(client, world)["items"] == []
    first = client.put(
        f"/api/v1/projects/{world.pid}/scope-items",
        json={
            "items": [
                {"kind": "IN", "title": "受注管理の機能", "description": "受注の登録・変更"},
                {"kind": "IN", "title": "在庫の引当"},
                {"kind": "OUT", "title": "会計システムの改修"},
            ]
        },
        headers=world.pm_h,
    )
    assert first.status_code == 200, first.text
    items = first.json()["items"]
    assert [(i["kind"], i["title"], i["sort_order"]) for i in items] == [
        ("IN", "受注管理の機能", 1),
        ("IN", "在庫の引当", 2),
        ("OUT", "会計システムの改修", 3),
    ]
    assert items[0]["description"] == "受注の登録・変更" and items[1]["description"] is None
    assert _scope(client, world)["items"] == items

    # id を書いた項目は ID を保って変わり、書かない項目は足され、載せなかった項目は消える。並びは配列の順
    order_id, stock_id, accounting_id = (item["id"] for item in items)
    second = client.put(
        f"/api/v1/projects/{world.pid}/scope-items",
        json={
            "items": [
                {"id": accounting_id, "kind": "OUT", "title": "会計システムの改修（対象外）"},
                {"kind": "IN", "title": "出荷の指示"},
                {"id": order_id, "kind": "IN", "title": "受注管理の機能"},
            ]
        },
        headers=world.pm_h,
    ).json()["items"]
    assert [(i["id"], i["title"], i["sort_order"]) for i in second] == [
        (accounting_id, "会計システムの改修（対象外）", 1),
        (second[1]["id"], "出荷の指示", 2),
        (order_id, "受注管理の機能", 3),
    ]
    assert second[1]["id"] not in (order_id, stock_id, accounting_id)
    assert second[2]["description"] is None  # 載せた値で置き換える
    db_session.expire_all()
    assert db_session.get(ScopeItem, stock_id) is None
    listed = _scope(client, world)
    assert listed["items"] == second and listed["total"] == 3

    # 空の配列で全て消える
    assert client.put(f"/api/v1/projects/{world.pid}/scope-items", json={"items": []}, headers=world.pm_h).json()[
        "items"
    ] == []
    assert _scope(client, world)["total"] == 0


def test_scope_items_paging(client: TestClient, world: World) -> None:
    items = [{"kind": "IN", "title": f"項目 {n}"} for n in range(1, 6)]
    client.put(f"/api/v1/projects/{world.pid}/scope-items", json={"items": items}, headers=world.pm_h)
    body = client.get(f"/api/v1/projects/{world.pid}/scope-items?limit=2&offset=2", headers=world.pm_h).json()
    assert [item["title"] for item in body["items"]] == ["項目 3", "項目 4"]
    assert (body["total"], body["limit"], body["offset"]) == (5, 2, 2)


def test_scope_items_access_rules(client: TestClient, world: World) -> None:
    for headers in (world.pm_h, world.planner_h, world.viewer_h, world.contractor_h):
        assert client.get(f"/api/v1/projects/{world.pid}/scope-items", headers=headers).status_code == 200
    assert client.get(f"/api/v1/projects/{world.pid}/scope-items", headers=world.outsider_h).status_code == 403
    body = {"items": [{"kind": "IN", "title": "X"}]}
    for headers in (world.planner_h, world.viewer_h, world.outsider_h):
        response = client.put(f"/api/v1/projects/{world.pid}/scope-items", json=body, headers=headers)
        assert response.status_code == 403
    assert _scope(client, world)["items"] == []


def test_replace_scope_items_rejects_bad_input(client: TestClient, world: World) -> None:
    other = world.create_project(code="PRJ-002")
    other_item = client.put(
        f"/api/v1/projects/{other['id']}/scope-items", json={"items": [{"kind": "IN", "title": "他"}]},
        headers=world.admin_h,
    ).json()["items"][0]
    mine = client.put(
        f"/api/v1/projects/{world.pid}/scope-items", json={"items": [{"kind": "IN", "title": "自分"}]},
        headers=world.pm_h,
    ).json()["items"][0]
    bad_bodies = [
        {"items": [{"id": other_item["id"], "kind": "IN", "title": "他のプロジェクトの項目"}]},
        {"items": [{"id": MISSING, "kind": "IN", "title": "無い項目"}]},
        {"items": [{"id": mine["id"], "kind": "IN", "title": "a"}, {"id": mine["id"], "kind": "IN", "title": "b"}]},
        {"items": [{"kind": "MAYBE", "title": "区分が違う"}]},
        {"items": [{"kind": "IN", "title": ""}]},
        {"items": [{"kind": "IN"}]},
        {},
    ]
    for body in bad_bodies:
        response = client.put(f"/api/v1/projects/{world.pid}/scope-items", json=body, headers=world.pm_h)
        assert response.status_code == 400, body
        assert error_code(response) == "VALIDATION_ERROR"
    assert _scope(client, world)["items"] == [mine]  # 何も変わらない
    assert client.get(f"/api/v1/projects/{other['id']}/scope-items", headers=world.admin_h).json()["items"] == [
        other_item
    ]


# ---------------------------------------------------------------------------
# 稼働カレンダー（読む：project.view・更新する：project.manage）
# ---------------------------------------------------------------------------


def test_pm_replaces_calendar_and_holidays(app: FastAPI, client: TestClient, db_session: Session, world: World) -> None:
    recorder = Recorder()
    app.state.schedule_recalculator = recorder
    body = {
        "working_weekdays": "1111110",  # 土曜も稼働
        "hours_per_day": 7.5,
        "holidays": [
            {"holiday_date": "2027-01-01", "name": "元日"},
            {"holiday_date": "2026-12-31"},
        ],
    }
    response = client.put(f"/api/v1/projects/{world.pid}/calendar", json=body, headers=world.pm_h)
    assert response.status_code == 200, response.text
    expected = {
        "project_id": world.pid,
        "working_weekdays": "1111110",
        "hours_per_day": 7.5,
        "holidays": [  # 日付の順
            {"holiday_date": "2026-12-31", "name": None},
            {"holiday_date": "2027-01-01", "name": "元日"},
        ],
    }
    assert response.json() == expected
    assert client.get(f"/api/v1/projects/{world.pid}/calendar", headers=world.contractor_h).json() == expected
    assert recorder.calls == [world.pid]  # 日程を再計算する（1.3・SCH-06）

    # 休日は丸ごと置き換える
    body2 = {"working_weekdays": "1111100", "hours_per_day": 8, "holidays": [{"holiday_date": "2027-01-04"}]}
    replaced = client.put(f"/api/v1/projects/{world.pid}/calendar", json=body2, headers=world.pm_h).json()
    assert replaced["holidays"] == [{"holiday_date": "2027-01-04", "name": None}]
    assert replaced["hours_per_day"] == 8.0
    assert recorder.calls == [world.pid, world.pid]
    db_session.expire_all()
    calendars = db_session.scalars(select(Calendar).where(Calendar.project_id == world.pid)).all()
    assert len(calendars) == 1
    holidays = db_session.scalars(select(CalendarHoliday).where(CalendarHoliday.calendar_id == calendars[0].id)).all()
    assert [h.holiday_date for h in holidays] == [date(2027, 1, 4)]


def test_calendar_without_row_reads_as_default_and_put_creates_it(
    client: TestClient, db_session: Session, world: World
) -> None:
    # API-PRJ-001 を通らずに作られたプロジェクト（カレンダーの行が無い）は、4.1・PRJ-04 の既定で読める
    owner = db_session.get(User, world.admin.id)
    assert owner is not None
    raw = Project(code="RAW", name="行だけ", start_date=date(2026, 12, 1), owner_user_id=owner.id)
    db_session.add(raw)
    db_session.commit()
    got = client.get(f"/api/v1/projects/{raw.id}/calendar", headers=world.admin_h).json()
    assert got == {"project_id": raw.id, "working_weekdays": "1111100", "hours_per_day": 8.0, "holidays": []}
    body = {"working_weekdays": "0111110", "hours_per_day": 6, "holidays": []}
    put = client.put(f"/api/v1/projects/{raw.id}/calendar", json=body, headers=world.admin_h)
    assert put.status_code == 200, put.text
    db_session.expire_all()
    assert db_session.scalar(select(Calendar.working_weekdays).where(Calendar.project_id == raw.id)) == "0111110"


def test_calendar_without_working_weekday_is_422(app: FastAPI, client: TestClient, world: World) -> None:
    recorder = Recorder()
    app.state.schedule_recalculator = recorder
    body = {"working_weekdays": "0000000", "hours_per_day": 8, "holidays": []}
    response = client.put(f"/api/v1/projects/{world.pid}/calendar", json=body, headers=world.pm_h)
    assert response.status_code == 422
    assert error_code(response) == "INVALID_CALENDAR"
    assert recorder.calls == []
    assert client.get(f"/api/v1/projects/{world.pid}/calendar", headers=world.pm_h).json()["working_weekdays"] == "1111100"


@pytest.mark.parametrize(
    "change",
    [
        {"working_weekdays": "111110"},
        {"working_weekdays": "11111000"},
        {"working_weekdays": "11111x0"},
        {"hours_per_day": 0},
        {"hours_per_day": 24.01},
        {"hours_per_day": 7.555},
        {"hours_per_day": -1},
        {"holidays": [{"holiday_date": "2027-01-01"}, {"holiday_date": "2027-01-01", "name": "重複"}]},
        {"holidays": [{"holiday_date": "2027-02-30"}]},
        {"holidays": None},
    ],
    ids=lambda change: ",".join(f"{k}={v}" for k, v in change.items()),
)
def test_calendar_input_errors_are_400(client: TestClient, world: World, change: dict[str, Any]) -> None:
    body: dict[str, Any] = {"working_weekdays": "1111100", "hours_per_day": 8, "holidays": []}
    body.update(change)
    body = {key: value for key, value in body.items() if value is not None}
    response = client.put(f"/api/v1/projects/{world.pid}/calendar", json=body, headers=world.pm_h)
    assert response.status_code == 400, response.text
    assert error_code(response) == "VALIDATION_ERROR"


def test_calendar_hours_boundaries_are_accepted(client: TestClient, world: World) -> None:
    for hours in (0.01, 24):
        body = {"working_weekdays": "1111100", "hours_per_day": hours, "holidays": []}
        response = client.put(f"/api/v1/projects/{world.pid}/calendar", json=body, headers=world.pm_h)
        assert response.status_code == 200, response.text
        assert response.json()["hours_per_day"] == hours


def test_calendar_access_rules(client: TestClient, world: World) -> None:
    assert client.get(f"/api/v1/projects/{world.pid}/calendar", headers=world.viewer_h).status_code == 200
    assert client.get(f"/api/v1/projects/{world.pid}/calendar", headers=world.outsider_h).status_code == 403
    body = {"working_weekdays": "1111111", "hours_per_day": 8, "holidays": []}
    for headers in (world.planner_h, world.viewer_h, world.outsider_h):
        assert client.put(f"/api/v1/projects/{world.pid}/calendar", json=body, headers=headers).status_code == 403
    assert client.get(f"/api/v1/projects/{world.pid}/calendar", headers=world.pm_h).json()["working_weekdays"] == "1111100"


def test_failed_recalculation_rolls_back_calendar(app: FastAPI, client: TestClient, world: World) -> None:
    app.state.schedule_recalculator = Recorder(fail=True)
    body = {"working_weekdays": "1111111", "hours_per_day": 8, "holidays": [{"holiday_date": "2027-01-01"}]}
    with pytest.raises(RuntimeError):
        client.put(f"/api/v1/projects/{world.pid}/calendar", json=body, headers=world.pm_h)
    got = client.get(f"/api/v1/projects/{world.pid}/calendar", headers=world.pm_h).json()
    assert got["working_weekdays"] == "1111100" and got["holidays"] == []


# ---------------------------------------------------------------------------
# メンバー（読む：project.view・変える：member.manage）
# ---------------------------------------------------------------------------


def test_member_list_with_names_roles_and_affiliation(client: TestClient, world: World) -> None:
    world.put_member(world.pid, world.planner.id, ["APPROVER", "PLANNER"], organization="開発部", team="基盤チーム")
    response = client.get(f"/api/v1/projects/{world.pid}/members", headers=world.contractor_h)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 4
    assert [m["login_id"] for m in body["items"]] == ["pm", "planner", "viewer", "contractor"]  # 利用者の ID の順
    planner = body["items"][1]
    assert planner == {
        "user_id": world.planner.id,
        "login_id": "planner",
        "display_name": "planner の表示名",
        "is_active": True,
        "roles": ["PLANNER", "APPROVER"],  # 3 章の順
        "organization": "開発部",
        "team": "基盤チーム",
    }
    paged = client.get(f"/api/v1/projects/{world.pid}/members?limit=2&offset=3", headers=world.pm_h).json()
    assert [m["login_id"] for m in paged["items"]] == ["contractor"]
    assert (paged["total"], paged["limit"], paged["offset"]) == (4, 2, 3)
    assert client.get(f"/api/v1/projects/{world.pid}/members", headers=world.outsider_h).status_code == 403


def test_pm_adds_member_who_then_gets_role_permissions(client: TestClient, world: World) -> None:
    assert client.get(f"/api/v1/projects/{world.pid}", headers=world.outsider_h).status_code == 403
    response = client.put(
        f"/api/v1/projects/{world.pid}/members/{world.outsider.id}",
        json={"roles": ["PLANNER"], "organization": "協力会社"},
        headers=world.pm_h,
    )
    assert response.status_code == 200, response.text
    assert response.json()["roles"] == ["PLANNER"]
    assert client.get(f"/api/v1/projects/{world.pid}", headers=world.outsider_h).status_code == 200
    me = client.get("/api/v1/me", headers=world.outsider_h).json()
    mine = next(p for p in me["projects"] if p["project_id"] == world.pid)
    assert mine["roles"] == ["PLANNER"] and "wbs.edit" in mine["permissions"]
    assert "project.manage" not in mine["permissions"]

    # ロールを変える：載せた値で置き換える（所属も）
    changed = client.put(
        f"/api/v1/projects/{world.pid}/members/{world.outsider.id}", json={"roles": ["PM", "VIEWER"]}, headers=world.pm_h
    ).json()
    assert changed["roles"] == ["PM", "VIEWER"] and changed["organization"] is None
    assert client.patch(f"/api/v1/projects/{world.pid}", json={"name": "X"}, headers=world.outsider_h).status_code == 200


def test_remove_member_revokes_access(client: TestClient, db_session: Session, world: World) -> None:
    response = client.delete(f"/api/v1/projects/{world.pid}/members/{world.viewer.id}", headers=world.pm_h)
    assert response.status_code == 204
    assert response.content == b""
    assert client.get(f"/api/v1/projects/{world.pid}", headers=world.viewer_h).status_code == 403
    assert client.get("/api/v1/projects", headers=world.viewer_h).json()["items"] == []
    db_session.expire_all()
    assert db_session.get(User, world.viewer.id) is not None  # 利用者は消えない
    again = client.delete(f"/api/v1/projects/{world.pid}/members/{world.viewer.id}", headers=world.pm_h)
    assert again.status_code == 404
    assert error_code(again) == "NOT_FOUND"


def test_member_changes_require_member_manage(client: TestClient, world: World) -> None:
    for headers in (world.planner_h, world.viewer_h, world.contractor_h, world.outsider_h):
        put = client.put(
            f"/api/v1/projects/{world.pid}/members/{world.outsider.id}", json={"roles": ["PM"]}, headers=headers
        )
        assert put.status_code == 403
        delete = client.delete(f"/api/v1/projects/{world.pid}/members/{world.viewer.id}", headers=headers)
        assert delete.status_code == 403
    members = client.get(f"/api/v1/projects/{world.pid}/members", headers=world.pm_h).json()
    assert [m["login_id"] for m in members["items"]] == ["pm", "planner", "viewer", "contractor"]


def test_member_belongs_to_one_project_only(client: TestClient, world: World) -> None:
    other = world.create_project(code="PRJ-002")
    # 他のプロジェクトの PM ではないので、そのメンバーは変えられない
    response = client.put(
        f"/api/v1/projects/{other['id']}/members/{world.pm.id}", json={"roles": ["PM"]}, headers=world.pm_h
    )
    assert response.status_code == 403


@pytest.mark.parametrize(
    "body",
    [
        {"roles": []},
        {"roles": ["OWNER"]},  # 3 章に無いロール
        {"roles": ["SYSTEM_ADMIN"]},  # システム管理者はロールではない（users.is_system_admin）
        {"roles": ["PM", "PM"]},
        {},
        {"roles": ["PM"], "organization": "x" * 201},
        {"roles": ["PM"], "extra": 1},
    ],
    ids=lambda body: str(body)[:40],
)
def test_upsert_member_input_errors_are_400(client: TestClient, world: World, body: dict[str, Any]) -> None:
    response = client.put(f"/api/v1/projects/{world.pid}/members/{world.outsider.id}", json=body, headers=world.pm_h)
    assert response.status_code == 400, response.text
    assert error_code(response) == "VALIDATION_ERROR"
    members = client.get(f"/api/v1/projects/{world.pid}/members", headers=world.pm_h).json()["items"]
    assert world.outsider.id not in [m["user_id"] for m in members]


def test_upsert_member_unknown_user_is_404_and_inactive_user_is_422(
    client: TestClient, db_session: Session, world: World
) -> None:
    missing = client.put(f"/api/v1/projects/{world.pid}/members/{MISSING}", json={"roles": ["PM"]}, headers=world.pm_h)
    assert missing.status_code == 404
    assert error_code(missing) == "NOT_FOUND"

    gone = make_user(db_session, "gone", is_active=False)
    inactive = client.put(
        f"/api/v1/projects/{world.pid}/members/{gone.id}", json={"roles": ["VIEWER"]}, headers=world.pm_h
    )
    assert inactive.status_code == 422
    assert error_code(inactive) == "USER_INACTIVE"

    # 既にメンバーの利用者が無効になっても、ロールは変えられる（外すこともできる）
    deactivate = client.patch(f"/api/v1/users/{world.viewer.id}", json={"is_active": False}, headers=world.admin_h)
    assert deactivate.status_code == 200, deactivate.text
    changed = client.put(
        f"/api/v1/projects/{world.pid}/members/{world.viewer.id}", json={"roles": ["EXECUTOR"]}, headers=world.pm_h
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["is_active"] is False and changed.json()["roles"] == ["EXECUTOR"]
    assert client.delete(f"/api/v1/projects/{world.pid}/members/{world.viewer.id}", headers=world.pm_h).status_code == 204


def test_member_roles_are_stored_in_role_order(db_session: Session, world: World) -> None:
    world.put_member(world.pid, world.outsider.id, list(reversed(ROLES)))
    db_session.expire_all()
    member = db_session.scalar(
        select(ProjectMember).where(ProjectMember.project_id == world.pid, ProjectMember.user_id == world.outsider.id)
    )
    assert member is not None
    assert member.roles == list(ROLES)


# ---------------------------------------------------------------------------
# 別の群の表に書く口の差し替え忘れ（A-14）
# ---------------------------------------------------------------------------


def test_numbering_rules_store_is_wired_once_numbering_table_exists(app: FastAPI) -> None:
    """採番規則の表（wbs_numbering_rules）ができたら、アプリはそこへ書く実装を載せていること。"""
    if "wbs_numbering_rules" in Base.metadata.tables:
        assert not isinstance(app.state.numbering_rules, NumberingRulesTableAbsent), (
            "wbs_numbering_rules ができたので、app.main.create_app の app.state.numbering_rules を表に書く実装へ差し替える"
        )
    else:
        assert isinstance(app.state.numbering_rules, NumberingRulesTableAbsent)


def test_schedule_recalculator_is_wired_once_activities_table_exists(app: FastAPI) -> None:
    """活動の表（activities）ができたら、アプリは日程を再計算する実装を載せていること。"""
    if "activities" in Base.metadata.tables:
        assert not isinstance(app.state.schedule_recalculator, ActivitiesTableAbsent), (
            "activities ができたので、app.main.create_app の app.state.schedule_recalculator を再計算の実装へ差し替える"
        )
    else:
        assert isinstance(app.state.schedule_recalculator, ActivitiesTableAbsent)


# 日程を計算し直す API（API-SCH-008）のパス（設計書 6.2）
SCHEDULE_CALCULATE_PATH = "/api/v1/projects/{project_id}/schedule/calculate"


def test_schedule_recalculator_is_replaced_once_schedule_api_exists(app: FastAPI) -> None:
    """日程を計算し直す API（API-SCH-008）ができたら、アプリは日程を計算して保存する実装を載せていること（A-19）。

    それまでは SchedulingNotWired（活動があれば止める）。api:SCH が差し替えを忘れるとこの試験が落ちる。
    """
    # FastAPI 0.162 の app.routes は include_router したルートのパスを出さない（_IncludedRouter）ので、OpenAPI のパスでも見る
    paths = {getattr(route, "path", None) for route in app.routes} | set(app.openapi()["paths"])
    assert "/api/v1/projects/{project_id}/wbs" in paths, "ルートのパスを読めていない（判定の前提が崩れている）"
    if SCHEDULE_CALCULATE_PATH in paths:
        assert not isinstance(app.state.schedule_recalculator, (ActivitiesTableAbsent, SchedulingNotWired)), (
            "API-SCH-008 ができたので、app.main.create_app の app.state.schedule_recalculator を日程計算の実装へ差し替える"
        )
    else:
        assert isinstance(app.state.schedule_recalculator, SchedulingNotWired)


def test_scheduling_not_wired_stops_only_projects_with_activities(
    app: FastAPI, client: TestClient, db_session: Session, world: World
) -> None:
    """既定の再計算の口 SchedulingNotWired（A-19）：活動の無いプロジェクトは開始日・カレンダーを変えられる。
    活動があれば日程を計算しないまま変更を通さずに止め（fail fast）、変更は巻き戻る。

    アプリの既定は api:SCH で日程計算の実装（ScheduleCalculator）へ差し替えたので、この口を明示して載せて確かめる。"""
    app.state.schedule_recalculator = SchedulingNotWired()
    url = f"/api/v1/projects/{world.pid}"
    assert client.patch(url, json={"start_date": "2026-12-02"}, headers=world.pm_h).status_code == 200

    wp = WbsElement(project_id=world.pid, kind="WORK_PACKAGE", name="WP", wbs_code="1.0", sibling_seq=1, sort_order=1)
    db_session.add(wp)
    db_session.flush()
    db_session.add(Activity(project_id=world.pid, work_package_id=wp.id, name="活動", sort_order=1, duration_days=3))
    db_session.commit()

    with pytest.raises(RuntimeError, match="RULE-SCH-002"):
        client.patch(url, json={"start_date": "2026-12-03"}, headers=world.pm_h)
    calendar = {"working_weekdays": "1111111", "hours_per_day": 8, "holidays": []}
    with pytest.raises(RuntimeError, match="RULE-SCH-002"):
        client.put(f"{url}/calendar", json=calendar, headers=world.pm_h)
    assert client.get(url, headers=world.pm_h).json()["start_date"] == "2026-12-02"
    assert client.get(f"{url}/calendar", headers=world.pm_h).json()["working_weekdays"] == "1111100"
    # 日程に関わらない変更は通る
    assert client.patch(url, json={"name": "改名"}, headers=world.pm_h).status_code == 200

    # 活動の無い別のプロジェクトは止めない
    other = world.create_project(code="PRJ-301")
    assert client.patch(f"/api/v1/projects/{other['id']}", json={"start_date": "2026-12-05"},
                        headers=world.admin_h).status_code == 200


def test_create_project_writes_default_numbering_rule(
    client: TestClient, db_session: Session, world: World
) -> None:
    """API-PRJ-001 の「採番規則も作る」：アプリの既定の口は wbs_numbering_rules に 4.1 の既定の行を 1 つ作る（A-15）。"""
    created = world.create_project(code="PRJ-210")
    db_session.expire_all()
    rules = db_session.scalars(select(WbsNumberingRule).where(WbsNumberingRule.project_id == created["id"])).all()
    assert [(r.separator, r.min_digits, r.prefix, r.pad_first_level) for r in rules] == [(".", 1, "", True)]


def test_numbering_rules_table_rejects_second_rule_for_project(app: FastAPI, db_session: Session, world: World) -> None:
    """採番規則はプロジェクトに 1 つ（4.1 の project_id 一意）。既にあるプロジェクトへ既定の行を作ると止まる。"""
    created = world.create_project(code="PRJ-211")
    with pytest.raises(IntegrityError):
        NumberingRulesTable().create_default(db_session, created["id"])
