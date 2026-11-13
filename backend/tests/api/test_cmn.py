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
from httpx import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.domain.authz import DEFAULT_ROLE_PERMISSIONS, PERMISSIONS, ROLES, Membership, default_role_permission_rows
from app.infra.db import Base
from app.infra.security import hash_password, hash_token, verify_password
from app.main import create_app
from app.models.chg import AuditLog
from app.models.cmn import AuthSession, RolePermission, User
from app.models.prj import Project
from app.services.clock import get_now
from app.services.project_access import ProjectTablesAbsent

NOW = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
PASSWORD = "correct horse battery staple"
# ログイン名が無い・無効・ロック中・パスワードの誤りに共通の 401（どれに当たったかもロックも明かさない。NFR-04）
INVALID_CREDENTIALS_BODY = {
    "error": {
        "code": "INVALID_CREDENTIALS",
        "message": "ログイン名またはパスワードが違うか、しばらくログインできません",
        "details": [],
    }
}
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

    def project_ids(self, session: Session) -> list[int]:
        return sorted(self.projects)


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
        "can_create_project": False,
        "is_active": True,
        "locked_until": None,
    }
    assert "password_hash" not in response.text
    assert "failed_login_count" not in response.text

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
    assert response.json() == INVALID_CREDENTIALS_BODY
    assert db_session.scalars(select(AuthSession)).all() == []


def test_login_input_error_is_400(client: TestClient) -> None:
    """API-CMN-001：入力の誤り（パスワードが無い）は 400。"""
    response = client.post("/api/v1/auth/login", json={"login_id": "u1"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def post_login(client: TestClient, login_id: str, password: str) -> Response:
    return client.post("/api/v1/auth/login", json={"login_id": login_id, "password": password})


def stored_user(session: Session, user_id: int) -> User:
    """DB に残った値（401 を返した要求の書き込みも、コミットしていれば読める）。"""
    session.expire_all()
    user = session.get(User, user_id)
    assert user is not None
    return user


def test_login_locks_after_10_failures_for_15_minutes(client: TestClient, db_session: Session, clock: Clock) -> None:
    """API-CMN-001・NFR-04：パスワードを続けて 10 回誤るとロックし（数えは 0 に戻る）、15 分の間は正しいパスワードでも 401。
    ロック中の試行は数えずロックも延ばさない。15 分たてばログインでき、成功で数えとロックが消える。"""
    user = make_user(db_session, "u1")
    for attempt in range(1, 10):
        assert post_login(client, "u1", "wrong password").status_code == 401
        assert (stored_user(db_session, user.id).failed_login_count, stored_user(db_session, user.id).locked_until) == (
            attempt, None)

    tenth = post_login(client, "u1", "wrong password")
    assert tenth.status_code == 401
    assert tenth.json() == INVALID_CREDENTIALS_BODY
    locked = stored_user(db_session, user.id)
    assert (locked.failed_login_count, locked.locked_until) == (0, NOW + timedelta(minutes=15))

    for offset in (timedelta(0), timedelta(minutes=15) - timedelta(seconds=1)):
        clock.now = NOW + offset
        for password in (PASSWORD, "wrong password"):
            response = post_login(client, "u1", password)
            assert response.status_code == 401
            assert response.json() == INVALID_CREDENTIALS_BODY
    still = stored_user(db_session, user.id)
    assert (still.failed_login_count, still.locked_until) == (0, NOW + timedelta(minutes=15))
    assert db_session.scalars(select(AuthSession)).all() == []

    clock.now = NOW + timedelta(minutes=15)
    assert post_login(client, "u1", PASSWORD).status_code == 200
    unlocked = stored_user(db_session, user.id)
    assert (unlocked.failed_login_count, unlocked.locked_until) == (0, None)


def test_login_success_resets_failure_count(client: TestClient, db_session: Session, clock: Clock) -> None:
    """API-CMN-001・NFR-04：数えるのは「続けて」誤った回数。成功で 0 に戻るので、その後の 9 回の誤りではロックしない。"""
    user = make_user(db_session, "u1")
    for _ in range(9):
        post_login(client, "u1", "wrong password")
    assert stored_user(db_session, user.id).failed_login_count == 9
    login(client, "u1")
    assert stored_user(db_session, user.id).failed_login_count == 0
    for _ in range(9):
        post_login(client, "u1", "wrong password")
    assert stored_user(db_session, user.id).locked_until is None
    login(client, "u1")


def test_locked_and_bad_credentials_are_indistinguishable(
    client: TestClient, db_session: Session, clock: Clock
) -> None:
    """API-CMN-001・NFR-04：ログイン名が無い・無効・ロック中（正しいパスワード）・パスワードの誤りの応答は、状態・本体・見出しが同じ。"""
    make_user(db_session, "u1")
    make_user(db_session, "inactive", is_active=False)
    locked = make_user(db_session, "locked")
    locked.locked_until = NOW + timedelta(minutes=5)
    db_session.commit()

    responses = [
        post_login(client, "nobody", PASSWORD),
        post_login(client, "inactive", PASSWORD),
        post_login(client, "locked", PASSWORD),
        post_login(client, "u1", "wrong password"),
    ]
    assert {response.status_code for response in responses} == {401}
    assert all(response.json() == INVALID_CREDENTIALS_BODY for response in responses)
    assert len({tuple(sorted(response.headers.items())) for response in responses}) == 1


def test_login_lock_follows_settings(tmp_path: Path, disposables: list[FastAPI]) -> None:
    """NFR-10：ロックする回数と長さは PMT_LOGIN_MAX_FAILURES・PMT_LOGIN_LOCK_MINUTES に従う。"""
    application = create_app(
        Settings(database_url=f"sqlite:///{tmp_path / 'lock.db'}", login_max_failures=2, login_lock_minutes=1)
    )
    disposables.append(application)
    Base.metadata.create_all(application.state.engine)
    clock = Clock(NOW)
    application.dependency_overrides[get_now] = clock
    with application.state.session_factory() as session:
        user_id = make_user(session, "u1").id
    with TestClient(application) as client:
        post_login(client, "u1", "wrong password")
        post_login(client, "u1", "wrong password")
        assert post_login(client, "u1", PASSWORD).status_code == 401
        with application.state.session_factory() as session:
            assert stored_user(session, user_id).locked_until == NOW + timedelta(minutes=1)
        clock.now = NOW + timedelta(minutes=1)
        assert post_login(client, "u1", PASSWORD).status_code == 200


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
            "password": "s3cret-passphrase",
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
    assert "s3cret-passphrase" not in stored.password_hash
    assert verify_password("s3cret-passphrase", stored.password_hash)
    login(client, "new.user", "s3cret-passphrase")


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
        json={"login_id": "admin", "display_name": "二人目", "password": PASSWORD},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DUPLICATE_LOGIN_ID"


@pytest.mark.parametrize(
    "payload",
    [
        {"login_id": "", "display_name": "x", "password": PASSWORD},
        {"login_id": "x", "display_name": "x", "password": ""},
        {"login_id": "x" * 101, "display_name": "x", "password": PASSWORD},
        {"login_id": "x", "display_name": "x", "password": PASSWORD, "is_active": False},
        {"login_id": "x", "display_name": "x", "password": PASSWORD, "password_hash": "scrypt$x"},
    ],
    ids=["empty-login", "empty-password", "long-login", "unknown-field", "hash-field"],
)
def test_create_user_input_errors_are_400(client: TestClient, admin: User, clock: Clock, payload: dict) -> None:
    """API-CMN-004：空・長すぎる値、知らない項目（password_hash など）は 400。"""
    response = client.post("/api/v1/users", headers=bearer(login(client, "admin")), json=payload)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize(
    ("login_id", "password", "message"),
    [
        ("u-short", "x" * 11, "パスワードは 12 文字以上にしてください"),
        ("same.as.login.id", "same.as.login.id", "ログイン名と同じパスワードは使えません"),
    ],
    ids=["11-chars", "same-as-login-id"],
)
def test_create_user_rejects_password_against_policy(
    client: TestClient, db_session: Session, admin: User, clock: Clock, login_id: str, password: str, message: str
) -> None:
    """API-CMN-004・NFR-04：12 文字未満・ログイン名と同じパスワードは 400 VALIDATION_ERROR（loc は body.password）。作らない。"""
    response = client.post(
        "/api/v1/users",
        headers=bearer(login(client, "admin")),
        json={"login_id": login_id, "display_name": "x", "password": password},
    )
    assert response.status_code == 400
    body = response.json()["error"]
    assert body["code"] == "VALIDATION_ERROR"
    assert body["details"] == [{"loc": ["body", "password"], "message": message}]
    assert db_session.scalar(select(User).where(User.login_id == login_id)) is None


def test_create_user_accepts_exactly_12_chars_and_any_character_kinds(
    client: TestClient, admin: User, clock: Clock
) -> None:
    """API-CMN-004・NFR-04：ちょうど 12 文字は通る。文字種（数字・記号など）は課さない。1024 文字まで通り、1025 文字は 400。"""
    token = login(client, "admin")
    for login_id, password in (("u12", "a" * 12), ("u1024", "b" * 1024)):
        response = client.post(
            "/api/v1/users", headers=bearer(token), json={"login_id": login_id, "display_name": "x", "password": password}
        )
        assert response.status_code == 201, response.text
        login(client, login_id, password)
    too_long = client.post(
        "/api/v1/users", headers=bearer(token), json={"login_id": "u1025", "display_name": "x", "password": "c" * 1025}
    )
    assert too_long.status_code == 400


def test_password_min_length_follows_settings(tmp_path: Path, disposables: list[FastAPI]) -> None:
    """NFR-10：最小の長さは PMT_PASSWORD_MIN_LENGTH に従う（API-CMN-004・005 の両方）。"""
    application = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'policy.db'}", password_min_length=16))
    disposables.append(application)
    Base.metadata.create_all(application.state.engine)
    application.dependency_overrides[get_now] = Clock(NOW)
    with application.state.session_factory() as session:
        make_user(session, "admin", is_system_admin=True)
    with TestClient(application) as client:
        token = login(client, "admin")
        short = client.post(
            "/api/v1/users", headers=bearer(token), json={"login_id": "u1", "display_name": "x", "password": "y" * 15}
        )
        assert short.status_code == 400
        assert short.json()["error"]["details"][0]["message"] == "パスワードは 16 文字以上にしてください"
        created = client.post(
            "/api/v1/users", headers=bearer(token), json={"login_id": "u1", "display_name": "x", "password": "y" * 16}
        )
        assert created.status_code == 201
        patched = client.patch(f"/api/v1/users/{created.json()['id']}", headers=bearer(token), json={"password": "z" * 15})
        assert patched.status_code == 400


def test_admin_sets_can_create_project_and_me_reports_project_create(
    client: TestClient, db_session: Session, admin: User, clock: Clock
) -> None:
    """API-CMN-004・005・008・9.1：user.admin がプロジェクトの作成を許すと（can_create_project）、その人の /me の
    system_permissions に project.create が出る。外すと消える。既定は許さない。"""
    token = login(client, "admin")
    created = client.post(
        "/api/v1/users",
        headers=bearer(token),
        json={"login_id": "creator", "display_name": "作る人", "password": PASSWORD, "can_create_project": True},
    )
    assert created.status_code == 201
    assert created.json()["can_create_project"] is True
    plain = client.post(
        "/api/v1/users", headers=bearer(token), json={"login_id": "plain", "display_name": "x", "password": PASSWORD}
    )
    assert plain.json()["can_create_project"] is False

    assert client.get("/api/v1/me", headers=bearer(login(client, "creator"))).json()["system_permissions"] == [
        "project.create"
    ]
    assert client.get("/api/v1/me", headers=bearer(login(client, "plain"))).json()["system_permissions"] == []

    revoked = client.patch(
        f"/api/v1/users/{created.json()['id']}", headers=bearer(token), json={"can_create_project": False}
    )
    assert revoked.status_code == 200
    assert revoked.json()["can_create_project"] is False
    assert client.get("/api/v1/me", headers=bearer(login(client, "creator"))).json()["system_permissions"] == []
    assert client.patch(
        f"/api/v1/users/{created.json()['id']}", headers=bearer(token), json={"can_create_project": None}
    ).status_code == 400


def test_only_user_admin_sets_can_create_project(client: TestClient, db_session: Session, clock: Clock) -> None:
    """API-CMN-005・9.1：project.create の許可を設定するのは user.admin だけ。自分に許すことも 403。"""
    user = make_user(db_session, "u1")
    response = client.patch(
        f"/api/v1/users/{user.id}", headers=bearer(login(client, "u1")), json={"can_create_project": True}
    )
    assert response.status_code == 403
    assert stored_user(db_session, user.id).can_create_project is False


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
        f"/api/v1/users/{user.id}", headers=bearer(login(client, "admin")), json={"password": "new-passphrase"}
    )
    assert response.status_code == 200
    assert client.get("/api/v1/me", headers=bearer(old_token)).status_code == 401
    assert client.post("/api/v1/auth/login", json={"login_id": "u1", "password": PASSWORD}).status_code == 401
    login(client, "u1", "new-passphrase")


@pytest.mark.parametrize(
    ("password", "message"),
    [("x" * 11, "パスワードは 12 文字以上にしてください"), ("long.login.id", "ログイン名と同じパスワードは使えません")],
    ids=["11-chars", "same-as-login-id"],
)
def test_update_user_rejects_password_against_policy(
    client: TestClient, db_session: Session, admin: User, clock: Clock, password: str, message: str
) -> None:
    """API-CMN-005・NFR-04：設定し直すパスワードも方針で確かめる（ログイン名はその利用者のもの）。違反は 400 で、何も変えない。"""
    user = make_user(db_session, "long.login.id")
    before = user.password_hash
    response = client.patch(
        f"/api/v1/users/{user.id}", headers=bearer(login(client, "admin")), json={"password": password}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert response.json()["error"]["details"] == [{"loc": ["body", "password"], "message": message}]
    assert stored_user(db_session, user.id).password_hash == before
    login(client, "long.login.id")


def test_admin_password_reset_unlocks_user(
    client: TestClient, db_session: Session, admin: User, clock: Clock
) -> None:
    """API-CMN-005・NFR-04：user.admin がパスワードを設定し直すとロックを解き（数え 0・locked_until 空）、すぐにログインできる。
    一覧（API-CMN-003）の locked_until でロック中が分かる。"""
    user = make_user(db_session, "u1")
    for _ in range(10):
        post_login(client, "u1", "wrong password")
    token = login(client, "admin")
    listed = {item["login_id"]: item for item in client.get("/api/v1/users", headers=bearer(token)).json()["items"]}
    assert datetime.fromisoformat(listed["u1"]["locked_until"]) == NOW + timedelta(minutes=15)

    response = client.patch(f"/api/v1/users/{user.id}", headers=bearer(token), json={"password": "reset-passphrase"})
    assert response.status_code == 200
    assert response.json()["locked_until"] is None
    reset = stored_user(db_session, user.id)
    assert (reset.failed_login_count, reset.locked_until) == (0, None)
    login(client, "u1", "reset-passphrase")


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


def test_replace_role_permissions_must_keep_a_permission_manager(
    client: TestClient, db_session: Session, access: FakeProjectAccess, project: int, admin: User, clock: Clock
) -> None:
    """API-CMN-007・9.2 の注記：どのロールも permission.manage を持たない表は 422 NO_PERMISSION_MANAGER。表は変わらない。
    PM 以外のロールに残せば通る。"""
    path = f"/api/v1/projects/{project}/role-permissions"
    token = login(client, "admin")
    for roles in ([], [{"role": "PM", "permissions": ["project.view", "member.manage"]}]):
        response = client.put(path, headers=bearer(token), json={"roles": roles})
        assert response.status_code == 422, roles
        assert response.json()["error"]["code"] == "NO_PERMISSION_MANAGER"
        assert response.json()["error"]["details"] == [{"permission": "permission.manage"}]
        count = len(db_session.scalars(select(RolePermission).where(RolePermission.project_id == project)).all())
        assert count == len(default_role_permission_rows())

    moved = client.put(
        path, headers=bearer(token),
        json={"roles": [{"role": "PM", "permissions": ["project.view"]},
                        {"role": "PLANNER", "permissions": ["project.view", "permission.manage"]}]},
    )
    assert moved.status_code == 200


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


def test_me_for_system_admin_lists_all_projects(
    client: TestClient, db_session: Session, access: FakeProjectAccess, project: int, admin: User, clock: Clock
) -> None:
    """API-CMN-008・9.2 の注記：システム管理者は全てのプロジェクトを持つ。メンバーでないプロジェクトはロールが空で、
    全ての権限・閲覧は全て（ALL）。"""
    add_project(db_session, access, OTHER_PROJECT)
    access.add_member(project, admin.id, "VIEWER")
    body = client.get("/api/v1/me", headers=bearer(login(client, "admin"))).json()
    project_permissions = [p for p in PERMISSIONS if p not in {"project.create", "user.admin"}]
    assert body["projects"] == [
        {"project_id": project, "roles": ["VIEWER"], "permissions": project_permissions, "view_level": "ALL"},
        {"project_id": OTHER_PROJECT, "roles": [], "permissions": project_permissions, "view_level": "ALL"},
    ]


def test_me_for_system_admin_reads_projects_table(client: TestClient, db_session: Session, clock: Clock) -> None:
    """API-CMN-008：アプリの既定の ProjectMembersTable でも、システム管理者はメンバーでないプロジェクトを全て持つ。
    システム管理者でない人には載らない。"""
    admin_user = make_user(db_session, "admin", is_system_admin=True)
    make_user(db_session, "u1")
    for code in ("P-001", "P-002"):
        db_session.add(Project(code=code, name=code, start_date=date(2026, 12, 1), owner_user_id=admin_user.id))
    db_session.commit()
    ids = sorted(db_session.scalars(select(Project.id)))
    body = client.get("/api/v1/me", headers=bearer(login(client, "admin"))).json()
    assert [entry["project_id"] for entry in body["projects"]] == ids
    assert all(entry["roles"] == [] for entry in body["projects"])
    assert client.get("/api/v1/me", headers=bearer(login(client, "u1"))).json()["projects"] == []


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


@pytest.mark.parametrize(
    ("login_id", "password"),
    [("root", "short-pass1"), ("root.administrator", "root.administrator")],
    ids=["11-chars", "same-as-login-id"],
)
def test_initial_admin_password_against_policy_fails_fast(
    tmp_path: Path, disposables: list[FastAPI], login_id: str, password: str
) -> None:
    """NFR-04・NFR-10：最初のシステム管理者のパスワードも方針で確かめ、反すれば起動を止める（作らない）。"""
    application = _app_with_admin_settings(tmp_path, login_id, password)
    disposables.append(application)
    with pytest.raises(RuntimeError, match="PMT_INITIAL_ADMIN_PASSWORD"):
        with TestClient(application):
            pass
    with application.state.session_factory() as session:
        assert session.scalars(select(User)).all() == []


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


# ---------------------------------------------------------------------------
# 変更履歴（NFR-05）：ハッシュの伏せ字・ログインの変更者
# ---------------------------------------------------------------------------


def audit_rows(session: Session, entity_type: str, entity_id: int) -> list[AuditLog]:
    session.expire_all()
    return list(
        session.scalars(
            select(AuditLog).where(AuditLog.entity_type == entity_type, AuditLog.entity_id == entity_id)
            .order_by(AuditLog.id)
        )
    )


def test_audit_logs_redact_hashes_and_record_login_actors(
    client: TestClient, db_session: Session, admin: User, clock: Clock
) -> None:
    """NFR-05：ハッシュの列（password_hash・token_hash）は *** に伏せて記録する。auth_sessions も記録し、
    ログイン・ログアウトの変更者は本人、ログインの失敗で変わる users の行の変更者は空、管理者の変更は管理者。"""
    admin_token = login(client, "admin")
    created = client.post(
        "/api/v1/users", headers=bearer(admin_token),
        json={"login_id": "u1", "display_name": "利用者", "password": PASSWORD},
    ).json()
    user_id = created["id"]
    [create_log] = audit_rows(db_session, "users", user_id)
    assert (create_log.action, create_log.user_id) == ("CREATE", admin.id)
    assert create_log.after is not None and create_log.after["password_hash"] == "***"

    clock.now = NOW + timedelta(minutes=1)
    assert post_login(client, "u1", "wrong password").status_code == 401
    failure = audit_rows(db_session, "users", user_id)[-1]
    assert (failure.action, failure.user_id, failure.occurred_at) == ("UPDATE", None, NOW + timedelta(minutes=1))
    assert (failure.before, failure.after) == ({"failed_login_count": 0}, {"failed_login_count": 1})

    clock.now = NOW + timedelta(minutes=2)
    token = login(client, "u1")
    reset = audit_rows(db_session, "users", user_id)[-1]
    assert (reset.user_id, reset.before, reset.after) == (user_id, {"failed_login_count": 1}, {"failed_login_count": 0})
    session_row = db_session.scalar(select(AuthSession).where(AuthSession.token_hash == hash_token(token)))
    assert session_row is not None
    [session_created] = audit_rows(db_session, "auth_sessions", session_row.id)
    assert (session_created.action, session_created.user_id) == ("CREATE", user_id)
    assert session_created.after is not None and session_created.after["token_hash"] == "***"

    clock.now = NOW + timedelta(minutes=3)
    assert client.post("/api/v1/auth/logout", headers=bearer(token)).status_code == 204
    session_revoked = audit_rows(db_session, "auth_sessions", session_row.id)[-1]
    assert (session_revoked.action, session_revoked.user_id) == ("UPDATE", user_id)
    assert session_revoked.after == {"revoked_at": (NOW + timedelta(minutes=3)).isoformat()}

    client.patch(f"/api/v1/users/{user_id}", headers=bearer(admin_token), json={"password": "reset-passphrase"})
    changed = audit_rows(db_session, "users", user_id)[-1]
    assert changed.user_id == admin.id
    assert changed.before is not None and changed.after is not None
    assert (changed.before["password_hash"], changed.after["password_hash"]) == ("***", "***")

    stored_hash = stored_user(db_session, user_id).password_hash
    for row in db_session.scalars(select(AuditLog)):
        text = f"{row.before} {row.after}"
        assert stored_hash not in text
        assert hash_token(token) not in text
