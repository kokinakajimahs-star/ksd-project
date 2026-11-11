"""共通（CMN）の API の試験（設計書 6.2 の API-CMN-001〜008・6.1・9 章・12 章）。

SQLite の一時ファイルの DB と TestClient で、正常系・認証なし（401）・権限なし（403）・状態の不一致（409）・
業務ルールの違反（422）のうち該当するものを確かめる。「今」は ``get_now`` を差し替えて固定する。

プロジェクトの有無とメンバーの立場は試験の ``FakeProjectAccess`` で与える（``app.state.project_access`` を差し替える。
アプリの既定の ``ProjectMembersTable`` は ``backend/tests/services/test_project_access.py`` が確かめる）。
``role_permissions.project_id`` は ``projects`` を外部キーで指すので、表を持つプロジェクトは ``add_project`` で
``projects`` の行も作る。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.domain.authz import DEFAULT_ROLE_PERMISSIONS, PERMISSIONS, ROLES, Membership, default_role_permission_rows
from app.infra.db import Base
from app.infra.security import hash_password, hash_token, verify_password
from app.main import create_app
from app.models.cmn import AuthSession, RolePermission, User
from app.models.prj import Project
from app.services.clock import get_now
from app.services.project_access import ProjectTablesAbsent

NOW = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
PASSWORD = "correct horse battery staple"
PROJECT = 1
OTHER_PROJECT = 2
MISSING_PROJECT = 99
_ORDER = {permission: index for index, permission in enumerate(PERMISSIONS)}


class Clock:
    """試験の「今」。``get_now`` の代わりに渡す。"""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class FakeProjectAccess:
    """試験用のプロジェクトの有無とメンバー（プロジェクト → 利用者 → 立場）。"""

    def __init__(self) -> None:
        self.projects: dict[int, dict[int, Membership]] = {}

    def add_project(self, project_id: int) -> None:
        self.projects.setdefault(project_id, {})

    def add_member(self, project_id: int, user_id: int, *roles: str, activities: frozenset[int] = frozenset()) -> None:
        self.projects.setdefault(project_id, {})[user_id] = Membership(
            roles=frozenset(roles), assigned_activity_ids=activities
        )

    def project_exists(self, session: Session, project_id: int) -> bool:
        return project_id in self.projects

    def membership(self, session: Session, project_id: int, user_id: int) -> Membership | None:
        return self.projects.get(project_id, {}).get(user_id)

    def memberships(self, session: Session, user_id: int) -> dict[int, Membership]:
        return {pid: members[user_id] for pid, members in self.projects.items() if user_id in members}


@pytest.fixture()
def clock(app: FastAPI) -> Clock:
    current = Clock(NOW)
    app.dependency_overrides[get_now] = current
    return current


@pytest.fixture()
def access(app: FastAPI) -> FakeProjectAccess:
    fake = FakeProjectAccess()
    app.state.project_access = fake
    return fake


def make_user(
    session: Session,
    login_id: str,
    *,
    password: str = PASSWORD,
    is_system_admin: bool = False,
    is_active: bool = True,
) -> User:
    user = User(
        login_id=login_id,
        display_name=f"{login_id} の表示名",
        password_hash=hash_password(password),
        is_system_admin=is_system_admin,
        is_active=is_active,
    )
    session.add(user)
    session.commit()
    return user


def login(client: TestClient, login_id: str, password: str = PASSWORD) -> str:
    response = client.post("/api/v1/auth/login", json={"login_id": login_id, "password": password})
    assert response.status_code == 200, response.text
    token: str = response.json()["access_token"]
    return token


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def add_project(session: Session, access: FakeProjectAccess, project_id: int) -> None:
    """``projects`` の行（``role_permissions`` が外部キーで指す）を作り、``FakeProjectAccess`` にも載せる。

    責任者はログインしない利用者（パスワードのハッシュは形が壊れていて、どのパスワードとも合わない）。
    """
    owner = User(login_id=f"owner-{project_id}", display_name="責任者", password_hash="unusable")
    session.add(owner)
    session.flush()
    session.add(
        Project(
            id=project_id,
            code=f"P-{project_id:03d}",
            name=f"プロジェクト {project_id}",
            start_date=date(2026, 12, 1),
            owner_user_id=owner.id,
        )
    )
    session.commit()
    access.add_project(project_id)


def seed_default_table(session: Session, project_id: int) -> None:
    for role, permission in default_role_permission_rows():
        session.add(RolePermission(project_id=project_id, role=role, permission=permission))
    session.commit()


def ordered(permissions: frozenset[str]) -> list[str]:
    return sorted(permissions, key=_ORDER.__getitem__)


@pytest.fixture()
def admin(db_session: Session) -> User:
    return make_user(db_session, "admin", is_system_admin=True)


@pytest.fixture()
def project(db_session: Session, access: FakeProjectAccess) -> int:
    add_project(db_session, access, PROJECT)
    seed_default_table(db_session, PROJECT)
    return PROJECT


# ---------------------------------------------------------------------------
# API-CMN-001 ログイン
# ---------------------------------------------------------------------------


def test_login_issues_token_with_12_hour_expiry_and_stores_only_hash(
    client: TestClient, db_session: Session, clock: Clock
) -> None:
    """API-CMN-001：正しいログイン名とパスワードでトークンを得る。有効期間は 12 時間。DB にはハッシュだけ。"""
    user = make_user(db_session, "u1")
    response = client.post("/api/v1/auth/login", json={"login_id": "u1", "password": PASSWORD})
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert datetime.fromisoformat(body["expires_at"]) == NOW + timedelta(hours=12)
    assert body["user"] == {
        "id": user.id,
        "login_id": "u1",
        "display_name": "u1 の表示名",
        "email": None,
        "organization": None,
        "is_system_admin": False,
        "is_active": True,
    }
    assert "password_hash" not in response.text

    token = body["access_token"]
    stored = db_session.scalars(select(AuthSession)).all()
    assert len(stored) == 1
    assert stored[0].token_hash == hash_token(token)
    assert stored[0].token_hash != token
    assert stored[0].user_id == user.id
    assert stored[0].created_at == NOW


def test_login_uses_configured_ttl(tmp_path: Path) -> None:
    """API-CMN-001：有効期間は設定の PMT_TOKEN_TTL_HOURS に従う。"""
    application = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'ttl.db'}", token_ttl_hours=3))
    Base.metadata.create_all(application.state.engine)
    application.dependency_overrides[get_now] = Clock(NOW)
    try:
        with application.state.session_factory() as session:
            make_user(session, "u1")
        with TestClient(application) as client:
            response = client.post("/api/v1/auth/login", json={"login_id": "u1", "password": PASSWORD})
        assert datetime.fromisoformat(response.json()["expires_at"]) == NOW + timedelta(hours=3)
    finally:
        application.state.engine.dispose()


@pytest.mark.parametrize(
    ("login_id", "password"),
    [("u1", "wrong password"), ("nobody", PASSWORD), ("inactive", PASSWORD)],
    ids=["wrong-password", "unknown-login", "inactive-user"],
)
def test_login_rejects_bad_credentials_with_same_401(
    client: TestClient, db_session: Session, clock: Clock, login_id: str, password: str
) -> None:
    """API-CMN-001：パスワード違い・無いログイン名・無効な利用者は、どれも同じ 401（どれに当たったかを漏らさない）。"""
    make_user(db_session, "u1")
    make_user(db_session, "inactive", is_active=False)
    response = client.post("/api/v1/auth/login", json={"login_id": login_id, "password": password})
    assert response.status_code == 401
    assert response.json() == {
        "error": {"code": "INVALID_CREDENTIALS", "message": "ログイン名またはパスワードが違います", "details": []}
    }
    assert db_session.scalars(select(AuthSession)).all() == []


def test_login_input_error_is_400(client: TestClient) -> None:
    """API-CMN-001：入力の誤り（パスワードが無い）は 400。"""
    response = client.post("/api/v1/auth/login", json={"login_id": "u1"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# ---------------------------------------------------------------------------
# 認証（6.1：Bearer。有効期間・失効・無効な利用者）
# ---------------------------------------------------------------------------


def test_requests_without_token_are_401(client: TestClient) -> None:
    """6.1：API-CMN-001 のほかは認証が要る。トークンが無い・形の違う・知らないトークンは 401。"""
    for path in ("/api/v1/me", "/api/v1/users", f"/api/v1/projects/{PROJECT}/role-permissions"):
        response = client.get(path)
        assert response.status_code == 401, path
        assert response.headers["www-authenticate"] == "Bearer"
    assert client.get("/api/v1/me", headers={"Authorization": "Basic abc"}).status_code == 401
    assert client.get("/api/v1/me", headers=bearer("unknown-token")).status_code == 401
    assert client.post("/api/v1/auth/logout").status_code == 401


def test_token_expires_exactly_at_expires_at(client: TestClient, db_session: Session, clock: Clock) -> None:
    """6.1：トークンは発行から 12 時間有効。期限の時刻ちょうどで使えなくなる。"""
    make_user(db_session, "u1")
    token = login(client, "u1")
    clock.now = NOW + timedelta(hours=12) - timedelta(seconds=1)
    assert client.get("/api/v1/me", headers=bearer(token)).status_code == 200
    clock.now = NOW + timedelta(hours=12)
    assert client.get("/api/v1/me", headers=bearer(token)).status_code == 401


# ---------------------------------------------------------------------------
# API-CMN-002 ログアウト
# ---------------------------------------------------------------------------


def test_logout_revokes_only_that_token(client: TestClient, db_session: Session, clock: Clock) -> None:
    """API-CMN-002：送ったトークンを失効させる。以後そのトークンは 401。同じ利用者の別のトークンは使える。"""
    make_user(db_session, "u1")
    first = login(client, "u1")
    second = login(client, "u1")
    clock.now = NOW + timedelta(minutes=5)

    response = client.post("/api/v1/auth/logout", headers=bearer(first))
    assert response.status_code == 204
    assert response.content == b""
    assert client.get("/api/v1/me", headers=bearer(first)).status_code == 401
    assert client.post("/api/v1/auth/logout", headers=bearer(first)).status_code == 401
    assert client.get("/api/v1/me", headers=bearer(second)).status_code == 200

    record = db_session.scalar(select(AuthSession).where(AuthSession.token_hash == hash_token(first)))
    assert record is not None
    db_session.refresh(record)
    assert record.revoked_at == NOW + timedelta(minutes=5)


# ---------------------------------------------------------------------------
# API-CMN-003 ユーザーの一覧
# ---------------------------------------------------------------------------


def test_admin_lists_users_with_paging(client: TestClient, db_session: Session, admin: User, clock: Clock) -> None:
    """API-CMN-003：user.admin（システム管理者）は一覧を読める。ID の順・limit と offset・総数。ハッシュは出さない。"""
    for index in range(3):
        make_user(db_session, f"u{index}")
    token = login(client, "admin")

    response = client.get("/api/v1/users", headers=bearer(token))
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 4
    assert (body["limit"], body["offset"]) == (100, 0)
    assert [item["login_id"] for item in body["items"]] == ["admin", "u0", "u1", "u2"]
    assert "password_hash" not in response.text

    page = client.get("/api/v1/users", params={"limit": 2, "offset": 1}, headers=bearer(token)).json()
    assert [item["login_id"] for item in page["items"]] == ["u0", "u1"]
    assert page["total"] == 4


def test_list_users_limit_bounds_are_400(client: TestClient, admin: User, clock: Clock) -> None:
    """API-CMN-003：limit の上限は 1000（6.1）。超える・0・負の offset は 400。"""
    token = login(client, "admin")
    assert client.get("/api/v1/users", params={"limit": 1000}, headers=bearer(token)).status_code == 200
    for params in ({"limit": 1001}, {"limit": 0}, {"offset": -1}):
        response = client.get("/api/v1/users", params=params, headers=bearer(token))
        assert response.status_code == 400, params


def test_list_users_allowed_by_member_manage_in_a_project(
    client: TestClient, db_session: Session, access: FakeProjectAccess, project: int, clock: Clock
) -> None:
    """API-CMN-003：どれかのプロジェクトで member.manage を持つ（PM）なら読める。持たない（EXECUTOR・メンバーでない）は 403。"""
    pm = make_user(db_session, "pm")
    executor = make_user(db_session, "executor")
    make_user(db_session, "outsider")
    access.add_member(project, pm.id, "PM")
    access.add_member(project, executor.id, "EXECUTOR")

    assert client.get("/api/v1/users", headers=bearer(login(client, "pm"))).status_code == 200
    for login_id in ("executor", "outsider"):
        response = client.get("/api/v1/users", headers=bearer(login(client, login_id)))
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"


def test_list_users_follows_project_table_not_default(
    client: TestClient, db_session: Session, access: FakeProjectAccess, clock: Clock
) -> None:
    """API-CMN-003：member.manage はプロジェクトの表で決まる。表で EXECUTOR に member.manage を足せば読める。"""
    add_project(db_session, access, PROJECT)
    executor = make_user(db_session, "executor")
    access.add_member(PROJECT, executor.id, "EXECUTOR")
    db_session.add(RolePermission(project_id=PROJECT, role="EXECUTOR", permission="member.manage"))
    db_session.commit()
    assert client.get("/api/v1/users", headers=bearer(login(client, "executor"))).status_code == 200


# ---------------------------------------------------------------------------
# API-CMN-004 ユーザーを作る
# ---------------------------------------------------------------------------


def test_admin_creates_user_who_can_log_in(
    client: TestClient, db_session: Session, admin: User, clock: Clock
) -> None:
    """API-CMN-004：システム管理者がユーザーを作る（201）。パスワードは scrypt のハッシュで保存し、そのパスワードでログインできる。"""
    token = login(client, "admin")
    response = client.post(
        "/api/v1/users",
        headers=bearer(token),
        json={
            "login_id": "new.user",
            "display_name": "新しい利用者",
            "password": "s3cret-pass",
            "email": "new.user@example.invalid",
            "organization": "委託先A",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["login_id"] == "new.user"
    assert body["is_system_admin"] is False
    assert body["is_active"] is True
    assert body["organization"] == "委託先A"
    assert "password" not in body and "password_hash" not in body

    stored = db_session.get(User, body["id"])
    assert stored is not None
    assert stored.password_hash.startswith("scrypt$16384$8$1$")
    assert "s3cret-pass" not in stored.password_hash
    assert verify_password("s3cret-pass", stored.password_hash)
    login(client, "new.user", "s3cret-pass")


def test_create_user_requires_user_admin(
    client: TestClient, db_session: Session, access: FakeProjectAccess, project: int, clock: Clock
) -> None:
    """API-CMN-004：user.admin はシステム管理者だけ。プロジェクトの PM でも 403。"""
    pm = make_user(db_session, "pm")
    access.add_member(project, pm.id, "PM")
    response = client.post(
        "/api/v1/users",
        headers=bearer(login(client, "pm")),
        json={"login_id": "x", "display_name": "x", "password": "p"},
    )
    assert response.status_code == 403
    assert db_session.scalar(select(User).where(User.login_id == "x")) is None


def test_create_user_duplicate_login_id_is_409(client: TestClient, admin: User, clock: Clock) -> None:
    """API-CMN-004：ログイン名は一意。既にあるログイン名は 409 DUPLICATE_LOGIN_ID。"""
    response = client.post(
        "/api/v1/users",
        headers=bearer(login(client, "admin")),
        json={"login_id": "admin", "display_name": "二人目", "password": "p"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DUPLICATE_LOGIN_ID"


@pytest.mark.parametrize(
    "payload",
    [
        {"login_id": "", "display_name": "x", "password": "p"},
        {"login_id": "x", "display_name": "x", "password": ""},
        {"login_id": "x" * 101, "display_name": "x", "password": "p"},
        {"login_id": "x", "display_name": "x", "password": "p", "is_active": False},
        {"login_id": "x", "display_name": "x", "password": "p", "password_hash": "scrypt$x"},
    ],
    ids=["empty-login", "empty-password", "long-login", "unknown-field", "hash-field"],
)
def test_create_user_input_errors_are_400(client: TestClient, admin: User, clock: Clock, payload: dict) -> None:
    """API-CMN-004：空・長すぎる値、知らない項目（password_hash など）は 400。"""
    response = client.post("/api/v1/users", headers=bearer(login(client, "admin")), json=payload)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# ---------------------------------------------------------------------------
# API-CMN-005 ユーザーを変える・無効にする
# ---------------------------------------------------------------------------


def test_admin_updates_only_given_fields(client: TestClient, db_session: Session, admin: User, clock: Clock) -> None:
    """API-CMN-005：載せた項目だけを変える（表示名・所属）。メールを null で空にできる。"""
    user = make_user(db_session, "u1")
    user.email = "u1@example.invalid"
    db_session.commit()
    response = client.patch(
        f"/api/v1/users/{user.id}",
        headers=bearer(login(client, "admin")),
        json={"display_name": "変えた名前", "organization": "部署B", "email": None},
    )
    assert response.status_code == 200
    body = response.json()
    assert (body["display_name"], body["organization"], body["email"]) == ("変えた名前", "部署B", None)
    assert body["login_id"] == "u1"
    assert body["is_active"] is True


def test_deactivating_user_blocks_login_and_revokes_tokens(
    client: TestClient, db_session: Session, admin: User, clock: Clock
) -> None:
    """API-CMN-005：無効にした利用者はログインできず、発行済みのトークンも使えない（行は消さない）。"""
    user = make_user(db_session, "u1")
    user_token = login(client, "u1")
    response = client.patch(
        f"/api/v1/users/{user.id}", headers=bearer(login(client, "admin")), json={"is_active": False}
    )
    assert response.status_code == 200
    assert response.json()["is_active"] is False
    assert client.get("/api/v1/me", headers=bearer(user_token)).status_code == 401
    assert client.post("/api/v1/auth/login", json={"login_id": "u1", "password": PASSWORD}).status_code == 401
    assert all(record.revoked_at == NOW for record in db_session.scalars(
        select(AuthSession).where(AuthSession.user_id == user.id)
    ))
    db_session.expire_all()
    assert db_session.get(User, user.id) is not None

    reactivated = client.patch(
        f"/api/v1/users/{user.id}", headers=bearer(login(client, "admin")), json={"is_active": True}
    )
    assert reactivated.status_code == 200
    login(client, "u1")


def test_password_change_revokes_old_tokens(
    client: TestClient, db_session: Session, admin: User, clock: Clock
) -> None:
    """API-CMN-005：パスワードを変えると、その利用者の発行済みのトークンは全て失効し、新しいパスワードでだけログインできる。"""
    user = make_user(db_session, "u1")
    old_token = login(client, "u1")
    response = client.patch(
        f"/api/v1/users/{user.id}", headers=bearer(login(client, "admin")), json={"password": "new-pass"}
    )
    assert response.status_code == 200
    assert client.get("/api/v1/me", headers=bearer(old_token)).status_code == 401
    assert client.post("/api/v1/auth/login", json={"login_id": "u1", "password": PASSWORD}).status_code == 401
    login(client, "u1", "new-pass")


@pytest.mark.parametrize("change", [{"is_active": False}, {"is_system_admin": False}], ids=["deactivate", "demote"])
def test_last_active_system_admin_cannot_be_removed(
    client: TestClient, db_session: Session, admin: User, clock: Clock, change: dict
) -> None:
    """API-CMN-005：有効なシステム管理者が 1 人もいなくなる変更は 422 LAST_SYSTEM_ADMIN。2 人目がいれば通る。"""
    make_user(db_session, "inactive-admin", is_system_admin=True, is_active=False)
    token = login(client, "admin")
    response = client.patch(f"/api/v1/users/{admin.id}", headers=bearer(token), json=change)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "LAST_SYSTEM_ADMIN"
    db_session.expire_all()
    assert db_session.get(User, admin.id).is_system_admin is True  # type: ignore[union-attr]

    make_user(db_session, "admin2", is_system_admin=True)
    response = client.patch(f"/api/v1/users/{admin.id}", headers=bearer(token), json=change)
    assert response.status_code == 200


def test_update_user_requires_user_admin(client: TestClient, db_session: Session, clock: Clock) -> None:
    """API-CMN-005：システム管理者でなければ、自分自身を変えることも 403（管理者に昇格できない）。"""
    user = make_user(db_session, "u1")
    response = client.patch(
        f"/api/v1/users/{user.id}", headers=bearer(login(client, "u1")), json={"is_system_admin": True}
    )
    assert response.status_code == 403
    db_session.expire_all()
    assert db_session.get(User, user.id).is_system_admin is False  # type: ignore[union-attr]


def test_update_user_not_found_and_input_errors(client: TestClient, admin: User, clock: Clock) -> None:
    """API-CMN-005：無いユーザーは 404。空にできない項目の null・ログイン名の変更・知らない項目は 400。"""
    token = login(client, "admin")
    assert client.patch("/api/v1/users/9999", headers=bearer(token), json={"display_name": "x"}).status_code == 404
    for payload in ({"display_name": None}, {"is_active": None}, {"login_id": "renamed"}, {"display_name": ""}):
        response = client.patch(f"/api/v1/users/{admin.id}", headers=bearer(token), json=payload)
        assert response.status_code == 400, payload
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# ---------------------------------------------------------------------------
# API-CMN-006 ロール×権限の表を読む
# ---------------------------------------------------------------------------


def test_member_reads_project_role_permission_table(
    client: TestClient, db_session: Session, access: FakeProjectAccess, project: int, clock: Clock
) -> None:
    """API-CMN-006：project.view を持つメンバー（VIEWER）は表を読める。3 章のロールの順・9.1 の権限の順で、9.2 の既定と一致する。"""
    viewer = make_user(db_session, "viewer")
    access.add_member(project, viewer.id, "VIEWER")
    response = client.get(f"/api/v1/projects/{project}/role-permissions", headers=bearer(login(client, "viewer")))
    assert response.status_code == 200
    body = response.json()
    assert body["project_id"] == project
    assert [entry["role"] for entry in body["roles"]] == list(ROLES)
    for entry in body["roles"]:
        assert entry["permissions"] == ordered(DEFAULT_ROLE_PERMISSIONS[entry["role"]])


def test_role_permission_table_access_rules(
    client: TestClient, db_session: Session, access: FakeProjectAccess, project: int, admin: User, clock: Clock
) -> None:
    """API-CMN-006：委託先も読める（project.view）。メンバーでない・別のプロジェクトのメンバーは 403。
    システム管理者はメンバーでなくても読める。無いプロジェクトは 404。"""
    contractor = make_user(db_session, "contractor")
    other = make_user(db_session, "other")
    make_user(db_session, "outsider")
    access.add_member(project, contractor.id, "CONTRACTOR", activities=frozenset({10}))
    access.add_member(OTHER_PROJECT, other.id, "PM")
    path = f"/api/v1/projects/{project}/role-permissions"

    assert client.get(path, headers=bearer(login(client, "contractor"))).status_code == 200
    assert client.get(path, headers=bearer(login(client, "admin"))).status_code == 200
    for login_id in ("outsider", "other"):
        response = client.get(path, headers=bearer(login(client, login_id)))
        assert response.status_code == 403, login_id
        assert response.json()["error"]["code"] == "FORBIDDEN"
    missing = client.get(
        f"/api/v1/projects/{MISSING_PROJECT}/role-permissions", headers=bearer(login(client, "admin"))
    )
    assert missing.status_code == 404


def test_role_without_project_view_in_table_is_403(
    client: TestClient, db_session: Session, access: FakeProjectAccess, clock: Clock
) -> None:
    """API-CMN-006：可否はプロジェクトの表で決まる。表で VIEWER から project.view を外したプロジェクトでは、VIEWER は 403。"""
    add_project(db_session, access, PROJECT)
    for role, permission in default_role_permission_rows():
        if (role, permission) != ("VIEWER", "project.view"):
            db_session.add(RolePermission(project_id=PROJECT, role=role, permission=permission))
    db_session.commit()
    viewer = make_user(db_session, "viewer")
    access.add_member(PROJECT, viewer.id, "VIEWER")
    response = client.get(f"/api/v1/projects/{PROJECT}/role-permissions", headers=bearer(login(client, "viewer")))
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# API-CMN-007 ロール×権限の表を置き換える
# ---------------------------------------------------------------------------


def test_pm_replaces_role_permission_table(
    client: TestClient, db_session: Session, access: FakeProjectAccess, project: int, clock: Clock
) -> None:
    """API-CMN-007：permission.manage（PM）は表を置き換えられる。載せなかったロールは何も持たない。置き換えた表で以後の判定が決まる。"""
    pm = make_user(db_session, "pm")
    viewer = make_user(db_session, "viewer")
    access.add_member(project, pm.id, "PM")
    access.add_member(project, viewer.id, "VIEWER")
    viewer_token = login(client, "viewer")
    path = f"/api/v1/projects/{project}/role-permissions"
    pm_permissions = ordered(DEFAULT_ROLE_PERMISSIONS["PM"])

    response = client.put(
        path,
        headers=bearer(login(client, "pm")),
        json={
            "roles": [
                {"role": "PM", "permissions": list(reversed(pm_permissions))},
                {"role": "EXECUTOR", "permissions": ["project.view", "effort.enter_own", "data.export"]},
            ]
        },
    )
    assert response.status_code == 200
    body = response.json()
    grants = {entry["role"]: entry["permissions"] for entry in body["roles"]}
    assert list(grants) == list(ROLES)
    assert grants["PM"] == pm_permissions
    assert grants["EXECUTOR"] == ["project.view", "effort.enter_own", "data.export"]
    assert grants["VIEWER"] == [] and grants["PLANNER"] == []

    rows = db_session.execute(
        select(RolePermission.role, RolePermission.permission).where(RolePermission.project_id == project)
    ).all()
    assert len(rows) == len(pm_permissions) + 3
    assert ("EXECUTOR", "data.export") in {tuple(row) for row in rows}
    # VIEWER は project.view を失ったので、表を読めなくなる
    assert client.get(path, headers=bearer(viewer_token)).status_code == 403


def test_replace_role_permissions_does_not_touch_other_projects(
    client: TestClient, db_session: Session, access: FakeProjectAccess, project: int, admin: User, clock: Clock
) -> None:
    """API-CMN-007：置き換えるのはそのプロジェクトの表だけ。"""
    add_project(db_session, access, OTHER_PROJECT)
    seed_default_table(db_session, OTHER_PROJECT)
    response = client.put(
        f"/api/v1/projects/{project}/role-permissions",
        headers=bearer(login(client, "admin")),
        json={"roles": [{"role": "PM", "permissions": ["project.view", "permission.manage"]}]},
    )
    assert response.status_code == 200
    other_rows = db_session.scalars(select(RolePermission).where(RolePermission.project_id == OTHER_PROJECT)).all()
    assert len(other_rows) == len(default_role_permission_rows())


def test_replace_role_permissions_requires_permission_manage(
    client: TestClient, db_session: Session, access: FakeProjectAccess, project: int, clock: Clock
) -> None:
    """API-CMN-007：permission.manage を持たない（PLANNER）は 403。表は変わらない。無いプロジェクトは 404。"""
    planner = make_user(db_session, "planner")
    access.add_member(project, planner.id, "PLANNER")
    token = login(client, "planner")
    response = client.put(
        f"/api/v1/projects/{project}/role-permissions",
        headers=bearer(token),
        json={"roles": [{"role": "PLANNER", "permissions": ["permission.manage"]}]},
    )
    assert response.status_code == 403
    count = len(db_session.scalars(select(RolePermission).where(RolePermission.project_id == project)).all())
    assert count == len(default_role_permission_rows())
    missing = client.put(
        f"/api/v1/projects/{MISSING_PROJECT}/role-permissions", headers=bearer(token), json={"roles": []}
    )
    assert missing.status_code == 404


@pytest.mark.parametrize(
    "roles",
    [
        [{"role": "PM", "permissions": ["no.such.permission"]}],
        [{"role": "PM", "permissions": ["user.admin"]}],
        [{"role": "PM", "permissions": ["project.create"]}],
        [{"role": "SYSTEM_ADMIN", "permissions": ["project.view"]}],
        [{"role": "PM", "permissions": ["project.view"]}, {"role": "PM", "permissions": ["wbs.edit"]}],
    ],
    ids=["unknown-permission", "system-permission-user-admin", "system-permission-project-create", "unknown-role",
         "duplicate-role"],
)
def test_replace_role_permissions_rejects_bad_codes(
    client: TestClient, db_session: Session, access: FakeProjectAccess, project: int, admin: User, clock: Clock,
    roles: list,
) -> None:
    """API-CMN-007：9.1 に無い権限・システムの権限・3 章に無いロール・同じロールの重複は 400。表は変わらない。"""
    response = client.put(
        f"/api/v1/projects/{project}/role-permissions", headers=bearer(login(client, "admin")), json={"roles": roles}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    count = len(db_session.scalars(select(RolePermission).where(RolePermission.project_id == project)).all())
    assert count == len(default_role_permission_rows())


# ---------------------------------------------------------------------------
# API-CMN-008 自分の情報と権限
# ---------------------------------------------------------------------------


def test_me_lists_permissions_per_project(
    client: TestClient, db_session: Session, access: FakeProjectAccess, project: int, clock: Clock
) -> None:
    """API-CMN-008：自分の情報と、メンバーであるプロジェクトごとのロールと権限（プロジェクトの表から）。
    複数のロールは和。委託先だけなら閲覧は自分の割当の範囲（OWN_ASSIGNMENTS）。システムの権限は持たない。"""
    user = make_user(db_session, "u1")
    add_project(db_session, access, OTHER_PROJECT)
    seed_default_table(db_session, OTHER_PROJECT)
    access.add_member(project, user.id, "PLANNER", "APPROVER")
    access.add_member(OTHER_PROJECT, user.id, "CONTRACTOR", activities=frozenset({7}))

    response = client.get("/api/v1/me", headers=bearer(login(client, "u1")))
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["login_id"] == "u1"
    assert body["system_permissions"] == []
    by_project = {entry["project_id"]: entry for entry in body["projects"]}
    assert list(by_project) == [project, OTHER_PROJECT]
    assert by_project[project]["roles"] == ["PLANNER", "APPROVER"]
    assert by_project[project]["permissions"] == ordered(
        DEFAULT_ROLE_PERMISSIONS["PLANNER"] | DEFAULT_ROLE_PERMISSIONS["APPROVER"]
    )
    assert by_project[project]["view_level"] == "ALL"
    assert by_project[OTHER_PROJECT]["roles"] == ["CONTRACTOR"]
    assert by_project[OTHER_PROJECT]["permissions"] == ordered(DEFAULT_ROLE_PERMISSIONS["CONTRACTOR"])
    assert by_project[OTHER_PROJECT]["view_level"] == "OWN_ASSIGNMENTS"


def test_me_for_system_admin(
    client: TestClient, db_session: Session, access: FakeProjectAccess, project: int, admin: User, clock: Clock
) -> None:
    """API-CMN-008：システム管理者はシステムの権限（project.create・user.admin）を持ち、メンバーのプロジェクトでは全ての権限を持つ。"""
    access.add_member(project, admin.id, "VIEWER")
    body = client.get("/api/v1/me", headers=bearer(login(client, "admin"))).json()
    assert body["user"]["is_system_admin"] is True
    assert body["system_permissions"] == ["project.create", "user.admin"]
    assert body["projects"][0]["permissions"] == [p for p in PERMISSIONS if p not in {"project.create", "user.admin"}]


def test_me_without_memberships_has_no_projects(client: TestClient, db_session: Session, clock: Clock) -> None:
    """API-CMN-008：どのプロジェクトのメンバーでもなければ、プロジェクトの一覧は空（アプリの既定の ProjectMembersTable で読む）。"""
    make_user(db_session, "u1")
    body = client.get("/api/v1/me", headers=bearer(login(client, "u1"))).json()
    assert body["projects"] == []
    assert body["system_permissions"] == []


# ---------------------------------------------------------------------------
# 土台：プロジェクトを読む口の差し替え・最初のシステム管理者（NFR-10）
# ---------------------------------------------------------------------------


def test_project_access_is_wired_once_project_tables_exist(app: FastAPI) -> None:
    """``projects``・``project_members`` を作ったスコープは、それを読む ProjectAccess に差し替える（A-10）。

    表がまだ無い間は ProjectTablesAbsent（プロジェクトは 1 つも無い）。表ができたのに差し替えていなければ落とす。
    """
    tables = Base.metadata.tables
    if "projects" in tables or "project_members" in tables:
        assert not isinstance(app.state.project_access, ProjectTablesAbsent)
    else:
        assert isinstance(app.state.project_access, ProjectTablesAbsent)


def _app_with_admin_settings(tmp_path: Path, login_id: str | None, password: str | None) -> FastAPI:
    application = create_app(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'init.db'}",
            initial_admin_login=login_id,
            initial_admin_password=password,
        )
    )
    Base.metadata.create_all(application.state.engine)
    application.dependency_overrides[get_now] = Clock(NOW)
    return application


@pytest.fixture()
def disposables() -> Iterator[list[FastAPI]]:
    created: list[FastAPI] = []
    yield created
    for application in created:
        application.state.engine.dispose()


def test_initial_admin_is_created_at_startup(tmp_path: Path, disposables: list[FastAPI]) -> None:
    """NFR-10：PMT_INITIAL_ADMIN_LOGIN・PASSWORD を両方指定すると、起動時にそのシステム管理者を作る（既にあれば上書きしない）。"""
    application = _app_with_admin_settings(tmp_path, "root", "initial-pass")
    disposables.append(application)
    with TestClient(application) as client:
        token = login(client, "root", "initial-pass")
        assert client.get("/api/v1/me", headers=bearer(token)).json()["user"]["is_system_admin"] is True
        client.patch(
            f"/api/v1/users/{client.get('/api/v1/me', headers=bearer(token)).json()['user']['id']}",
            headers=bearer(token),
            json={"password": "changed-pass"},
        )
    with TestClient(application) as client:  # 2 回目の起動はパスワードを戻さない
        login(client, "root", "changed-pass")


@pytest.mark.parametrize(("login_id", "password"), [("root", None), (None, "initial-pass")])
def test_initial_admin_with_only_one_setting_fails_fast(
    tmp_path: Path, disposables: list[FastAPI], login_id: str | None, password: str | None
) -> None:
    """NFR-10：最初のシステム管理者の設定が片方だけなら、起動を止める（既定値を持たない）。"""
    application = _app_with_admin_settings(tmp_path, login_id, password)
    disposables.append(application)
    with pytest.raises(RuntimeError, match="PMT_INITIAL_ADMIN"):
        with TestClient(application):
            pass
