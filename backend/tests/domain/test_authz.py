"""認証・権限の判定（app.domain.authz）の試験。設計書 5.1・9 章・4.2、要求仕様書 CMN-01・PRJ-06・RPT-03。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.domain.authz import (
    DEFAULT_ROLE_PERMISSIONS,
    PERMISSIONS,
    PROJECT_PERMISSIONS,
    ROLES,
    SYSTEM_PERMISSIONS,
    Membership,
    Principal,
    Reason,
    ShareLinkRecord,
    ShareLinkStatus,
    ViewLevel,
    authorize,
    default_role_permission_rows,
    effective_permissions,
    hash_token,
    own_assignment_scope,
    project_view_level,
    role_permission_table,
    verify_share_link,
)

# 設計書 9.2 の表をそのまま写したもの（列の順：PM, PLANNER, EXECUTOR, PROGRESS_MANAGER, APPROVER, CONTRACTOR, VIEWER）
DESIGN_9_2 = """
| project.view | ○ | ○ | ○ | ○ | ○ | ○ | ○ |
| project.manage | ○ | | | | | | |
| member.manage | ○ | | | | | | |
| permission.manage | ○ | | | | | | |
| wbs.edit | ○ | ○ | | | | | |
| wbs.review | ○ | ○ | | | ○ | | |
| wbs.approve | ○ | | | | ○ | | |
| estimate.edit | ○ | ○ | | | | | |
| schedule.edit | ○ | ○ | | | | | |
| baseline.manage | ○ | | | | | | |
| status_report.manage | ○ | | | ○ | | | |
| progress.enter_own | ○ | ○ | ○ | ○ | | ○ | |
| progress.enter_all | ○ | | | ○ | | | |
| effort.enter_own | ○ | ○ | ○ | ○ | ○ | ○ | |
| effort.view | ○ | ○ | | ○ | ○ | | |
| variance.note | ○ | | | ○ | | | |
| change.create | ○ | ○ | | ○ | | | |
| change.approve | ○ | | | | ○ | | |
| change.apply | ○ | | | | | | |
| action.manage | ○ | | | ○ | | | |
| audit.view | ○ | | | ○ | ○ | | |
| share.manage | ○ | | | ○ | | | |
| data.import | ○ | ○ | | | | | |
| data.export | ○ | ○ | | ○ | | | ○ |
"""
DESIGN_9_2_ROLES = ("PM", "PLANNER", "EXECUTOR", "PROGRESS_MANAGER", "APPROVER", "CONTRACTOR", "VIEWER")

# 設計書 9.1 の権限のコード（表の順）
DESIGN_9_1 = (
    "project.create project.view project.manage member.manage permission.manage wbs.edit wbs.review wbs.approve "
    "estimate.edit schedule.edit baseline.manage status_report.manage progress.enter_own progress.enter_all "
    "effort.enter_own effort.view variance.note change.create change.approve change.apply action.manage audit.view "
    "share.manage data.import data.export user.admin"
).split()


def _design_grants() -> set[tuple[str, str]]:
    grants: set[tuple[str, str]] = set()
    for line in DESIGN_9_2.strip().splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        permission, marks = cells[0], cells[1:]
        assert len(marks) == len(DESIGN_9_2_ROLES)
        grants |= {(role, permission) for role, mark in zip(DESIGN_9_2_ROLES, marks, strict=True) if mark == "○"}
    return grants


DESIGN_GRANTS = _design_grants()
DEFAULT_TABLE = role_permission_table(default_role_permission_rows())

USER = Principal(user_id=10, is_active=True, is_system_admin=False)
ADMIN = Principal(user_id=1, is_active=True, is_system_admin=True)

ASSIGNED = 101
NOT_ASSIGNED = 202


def member(*roles: str, assigned: tuple[int, ...] = (ASSIGNED,)) -> Membership:
    return Membership(roles=frozenset(roles), assigned_activity_ids=frozenset(assigned))


def can(permission: str, membership: Membership | None, *, principal: Principal = USER, table=DEFAULT_TABLE, activity_id=None):
    return authorize(principal, permission, role_permissions=table, membership=membership, activity_id=activity_id)


# ---------------------------------------------------------------------------
# RULE-AUTH-001 権限の判定
# ---------------------------------------------------------------------------


def test_permission_codes_match_design_9_1() -> None:
    """RULE-AUTH-001：権限のコードは 9.1 の 26 件。システムの権限は project.create と user.admin。"""
    assert list(PERMISSIONS) == DESIGN_9_1
    assert SYSTEM_PERMISSIONS == {"project.create", "user.admin"}
    assert PROJECT_PERMISSIONS == set(DESIGN_9_1) - {"project.create", "user.admin"}
    assert ROLES == DESIGN_9_2_ROLES


def test_default_table_matches_design_9_2() -> None:
    """RULE-AUTH-001：プロジェクトの作成時に写す既定の表は 9.2 の ○ と一致する（過不足なし）。"""
    rows = default_role_permission_rows()
    assert set(rows) == DESIGN_GRANTS
    assert len(rows) == len(DESIGN_GRANTS)  # 重複なし
    assert {(role, p) for role, perms in DEFAULT_ROLE_PERMISSIONS.items() for p in perms} == DESIGN_GRANTS


@pytest.mark.parametrize("role", DESIGN_9_2_ROLES)
@pytest.mark.parametrize("permission", sorted(PROJECT_PERMISSIONS))
def test_each_role_and_permission_follows_design_9_2(role: str, permission: str) -> None:
    """RULE-AUTH-001：9.2 の表のとおりに許可・拒否する（CMN-01 の受入基準。自分の割当に限る権限は割当の活動で見る）。"""
    activity = ASSIGNED if permission in {"progress.enter_own", "effort.enter_own"} else None
    decision = can(permission, member(role), activity_id=activity)
    assert decision.allowed is ((role, permission) in DESIGN_GRANTS), decision.reason


def test_viewer_cannot_write() -> None:
    """RULE-AUTH-001：閲覧者ロールでは書き込みの操作ができない（RPT-03 の受入基準）。読めるのは閲覧と出力だけ。"""
    allowed = {p for p in PROJECT_PERMISSIONS if can(p, member("VIEWER"), activity_id=ASSIGNED).allowed}
    assert allowed == {"project.view", "data.export"}
    assert can("wbs.edit", member("VIEWER")).reason is Reason.NO_PERMISSION


def test_multiple_roles_union_their_permissions() -> None:
    """RULE-AUTH-001：1 人が複数のロールを持つとき（要求仕様書 3 章）、権限はロールの和になる。"""
    both = member("EXECUTOR", "APPROVER")
    assert can("change.approve", both).allowed
    assert can("progress.enter_own", both, activity_id=ASSIGNED).allowed
    assert not can("wbs.edit", both).allowed
    assert effective_permissions(USER, role_permissions=DEFAULT_TABLE, membership=both) == (
        DEFAULT_ROLE_PERMISSIONS["EXECUTOR"] | DEFAULT_ROLE_PERMISSIONS["APPROVER"]
    )


def test_project_table_overrides_default() -> None:
    """RULE-AUTH-001：PM が変えたプロジェクトの表（role_permissions）で判定する。外した権限は拒み、足した権限は許す。"""
    rows = [r for r in default_role_permission_rows() if r != ("PROGRESS_MANAGER", "share.manage")]
    rows.append(("EXECUTOR", "data.export"))
    table = role_permission_table(rows)
    assert can("share.manage", member("PROGRESS_MANAGER")).allowed  # 既定の表では許す
    assert not can("share.manage", member("PROGRESS_MANAGER"), table=table).allowed
    assert not can("data.export", member("EXECUTOR")).allowed
    assert can("data.export", member("EXECUTOR"), table=table).allowed


def test_role_without_rows_has_no_permission() -> None:
    """RULE-AUTH-001：表に行の無いロールは何も持たない（既定の表へ戻らない）。"""
    table = role_permission_table([("PM", "project.view")])
    assert table["PLANNER"] == frozenset()
    assert not can("project.view", member("PLANNER"), table=table).allowed
    assert can("project.view", member("PM"), table=table).allowed


def test_non_member_and_member_without_roles() -> None:
    """RULE-AUTH-001：メンバーでなければ拒む（NOT_MEMBER）。ロールが空のメンバーは権限を持たない。"""
    assert can("project.view", None).reason is Reason.NOT_MEMBER
    assert can("project.view", member()).reason is Reason.NO_PERMISSION
    assert project_view_level(USER, role_permissions=DEFAULT_TABLE, membership=None) is ViewLevel.NONE


def test_system_admin_has_every_permission() -> None:
    """RULE-AUTH-001：システム管理者は全ての権限を持つ（9.2）。メンバーでなくても、割当が無くても許す。"""
    for permission in PERMISSIONS:
        decision = can(permission, None, principal=ADMIN, activity_id=NOT_ASSIGNED)
        assert decision.allowed and decision.reason is Reason.SYSTEM_ADMIN, permission
    assert effective_permissions(ADMIN, role_permissions=DEFAULT_TABLE, membership=None) == set(PERMISSIONS)
    assert project_view_level(ADMIN, role_permissions=DEFAULT_TABLE, membership=None) is ViewLevel.ALL


def test_inactive_user_is_denied_everything() -> None:
    """RULE-AUTH-001：無効にした利用者は、システム管理者・PM でも全て拒む。"""
    inactive_admin = Principal(user_id=2, is_active=False, is_system_admin=True)
    inactive_pm = Principal(user_id=3, is_active=False, is_system_admin=False)
    assert can("project.view", None, principal=inactive_admin).reason is Reason.INACTIVE_USER
    assert can("project.view", member("PM"), principal=inactive_pm).reason is Reason.INACTIVE_USER
    assert effective_permissions(inactive_pm, role_permissions=DEFAULT_TABLE, membership=member("PM")) == frozenset()
    assert project_view_level(inactive_pm, role_permissions=DEFAULT_TABLE, membership=member("PM")) is ViewLevel.NONE


def test_system_permissions() -> None:
    """RULE-AUTH-001：project.create はシステム管理者と許されたユーザーだけ。user.admin はシステム管理者だけ。プロジェクトのロールでは得られない。"""
    assert authorize(USER, "project.create").reason is Reason.NO_PERMISSION
    assert authorize(USER, "user.admin").reason is Reason.NO_PERMISSION
    creator = Principal(user_id=11, is_active=True, is_system_admin=False, system_permissions=frozenset({"project.create"}))
    assert authorize(creator, "project.create").reason is Reason.GRANTED
    assert not authorize(creator, "user.admin").allowed
    assert authorize(ADMIN, "user.admin").allowed
    # PM のロールを持っていてもシステムの権限は持たない
    assert not can("project.create", member("PM")).allowed
    assert not can("user.admin", member("PM")).allowed
    with pytest.raises(ValueError):
        Principal(user_id=12, is_active=True, is_system_admin=False, system_permissions=frozenset({"user.admin"}))


@pytest.mark.parametrize("permission", ["progress.enter_own", "effort.enter_own"])
def test_own_assignment_permissions(permission: str) -> None:
    """RULE-AUTH-001：progress.enter_own・effort.enter_own は自分の割当の活動だけ（6.1 の「自分」）。境界：割当の活動・割当でない活動。"""
    executor = member("EXECUTOR")
    assert can(permission, executor, activity_id=ASSIGNED).reason is Reason.OWN_ASSIGNMENT
    assert can(permission, executor, activity_id=NOT_ASSIGNED).reason is Reason.NOT_OWN_ASSIGNMENT
    # 割当が無いメンバーはどの活動にも書けない
    assert can(permission, member("EXECUTOR", assigned=()), activity_id=ASSIGNED).reason is Reason.NOT_OWN_ASSIGNMENT
    # 権限を持たないロールは、割当の活動でも拒む
    assert can(permission, member("VIEWER"), activity_id=ASSIGNED).reason is Reason.NO_PERMISSION
    # 対象の活動を渡さないのは呼び手の誤り
    with pytest.raises(ValueError):
        can(permission, executor)


def test_progress_enter_all_covers_any_activity() -> None:
    """RULE-AUTH-001：progress.enter_all を持てば、割当でない活動の進捗も書ける。effort.enter_own には全件の権限が無い。"""
    manager = member("PROGRESS_MANAGER", assigned=())
    assert can("progress.enter_own", manager, activity_id=NOT_ASSIGNED).reason is Reason.GRANTED_ALL
    assert can("progress.enter_all", manager).allowed
    assert not can("progress.enter_all", member("EXECUTOR")).allowed
    assert can("effort.enter_own", manager, activity_id=NOT_ASSIGNED).reason is Reason.NOT_OWN_ASSIGNMENT


def test_contractor_view_is_restricted_to_own_assignments() -> None:
    """RULE-AUTH-001：委託先の project.view は自分の割当の範囲に絞る（9.2 の注記）。ほかのロールで project.view を持てば絞らない。"""
    contractor = member("CONTRACTOR")
    assert project_view_level(USER, role_permissions=DEFAULT_TABLE, membership=contractor) is ViewLevel.OWN_ASSIGNMENTS
    assert can("project.view", contractor).reason is Reason.RESTRICTED_TO_OWN_ASSIGNMENTS
    assert can("project.view", contractor, activity_id=ASSIGNED).allowed
    assert can("project.view", contractor, activity_id=NOT_ASSIGNED).reason is Reason.NOT_OWN_ASSIGNMENT

    contractor_and_viewer = member("CONTRACTOR", "VIEWER")
    assert project_view_level(USER, role_permissions=DEFAULT_TABLE, membership=contractor_and_viewer) is ViewLevel.ALL
    assert can("project.view", contractor_and_viewer, activity_id=NOT_ASSIGNED).reason is Reason.GRANTED

    executor = member("EXECUTOR")
    assert project_view_level(USER, role_permissions=DEFAULT_TABLE, membership=executor) is ViewLevel.ALL

    # PM が委託先から project.view を外せば読めない
    table = role_permission_table([r for r in default_role_permission_rows() if r != ("CONTRACTOR", "project.view")])
    assert project_view_level(USER, role_permissions=table, membership=contractor) is ViewLevel.NONE
    assert can("project.view", contractor, table=table, activity_id=ASSIGNED).reason is Reason.NO_PERMISSION


def test_own_assignment_scope_is_activities_and_wbs_path() -> None:
    """RULE-AUTH-001：委託先に見せるのは割当の活動と、その WP から根までの WBS の経路だけ（兄弟の要素・他の活動は見せない）。"""
    # 1 ─┬ 1.1 ─┬ 1.1.1(WP: 活動 101, 102)
    #    │      └ 1.1.2(WP: 活動 103)
    #    └ 1.2 ── 1.2.1(WP: 活動 104)
    parents = {1: None, 11: 1, 111: 11, 112: 11, 12: 1, 121: 12}
    activity_elements = {101: 111, 102: 111, 103: 112, 104: 121}
    scope = own_assignment_scope([101], activity_elements=activity_elements, element_parents=parents)
    assert scope.activity_ids == {101}
    assert scope.element_ids == {111, 11, 1}

    scope = own_assignment_scope([101, 104], activity_elements=activity_elements, element_parents=parents)
    assert scope.element_ids == {111, 11, 1, 121, 12}

    empty = own_assignment_scope([], activity_elements=activity_elements, element_parents=parents)
    assert empty.activity_ids == frozenset() and empty.element_ids == frozenset()


def test_own_assignment_scope_rejects_missing_or_cyclic_structure() -> None:
    """RULE-AUTH-001：割当の活動の要素・親が渡されない、親子が循環している、は ValueError（fail fast）。"""
    with pytest.raises(ValueError):
        own_assignment_scope([999], activity_elements={}, element_parents={})
    with pytest.raises(ValueError):
        own_assignment_scope([101], activity_elements={101: 111}, element_parents={111: 11})
    with pytest.raises(ValueError):
        own_assignment_scope([101], activity_elements={101: 111}, element_parents={111: 11, 11: 111})


def test_invalid_inputs_fail_fast() -> None:
    """RULE-AUTH-001：未知の権限・ロール、表に載らない権限、プロジェクトの権限なのに表が無い、は ValueError。"""
    with pytest.raises(ValueError):
        can("wbs.delete", member("PM"))
    with pytest.raises(ValueError):
        member("SYSTEM_ADMIN")
    with pytest.raises(ValueError):
        role_permission_table([("PM", "user.admin")])
    with pytest.raises(ValueError):
        role_permission_table([("OWNER", "project.view")])
    with pytest.raises(ValueError):
        authorize(USER, "project.view", membership=member("PM"))
    with pytest.raises(ValueError):
        can("project.view", member("PM"), table={"PM": frozenset({"no.such"})})


def test_effective_permissions_for_me() -> None:
    """RULE-AUTH-001：プロジェクトごとの権限（API-CMN-008）は、ロールの和と個別に許されたシステムの権限。"""
    assert effective_permissions(USER, role_permissions=DEFAULT_TABLE, membership=member("VIEWER")) == {
        "project.view",
        "data.export",
    }
    creator = Principal(user_id=11, is_active=True, is_system_admin=False, system_permissions=frozenset({"project.create"}))
    assert effective_permissions(creator, role_permissions=DEFAULT_TABLE, membership=None) == {"project.create"}


# ---------------------------------------------------------------------------
# RULE-AUTH-002 共有リンクの検証
# ---------------------------------------------------------------------------

TOKEN = "s3cr3t-share-token"
EXPIRES = datetime(2026, 12, 1, 0, 0, tzinfo=UTC)


def link(scope: str = "GANTT", *, revoked_at: datetime | None = None, expires_at: datetime = EXPIRES) -> ShareLinkRecord:
    return ShareLinkRecord(
        project_id=7, token_hash=hash_token(TOKEN), scope=scope, expires_at=expires_at, revoked_at=revoked_at
    )


def test_hash_token_is_sha256_hex() -> None:
    """RULE-AUTH-002：トークンは SHA-256 のハッシュ（16 進 64 文字）だけを保存する（4.2・NFR-04）。"""
    assert hash_token("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert len(hash_token(TOKEN)) == 64
    assert hash_token(TOKEN) != TOKEN


def test_valid_link_returns_project_and_scope() -> None:
    """RULE-AUTH-002：有効期限前で失効していないリンクは有効。見せるプロジェクトと範囲を返す。"""
    verdict = verify_share_link(TOKEN, link("REPORT"), now=EXPIRES - timedelta(days=3))
    assert verdict.status is ShareLinkStatus.VALID and verdict.valid
    assert (verdict.project_id, verdict.scope) == (7, "REPORT")


def test_expiry_boundary() -> None:
    """RULE-AUTH-002：有効期限の境界。expires_at の直前は有効、ちょうど・後は EXPIRED（失効後は閲覧できない。RPT-03）。"""
    assert verify_share_link(TOKEN, link(), now=EXPIRES - timedelta(microseconds=1)).valid
    expired = verify_share_link(TOKEN, link(), now=EXPIRES)
    assert expired.status is ShareLinkStatus.EXPIRED
    assert (expired.project_id, expired.scope) == (None, None)
    assert verify_share_link(TOKEN, link(), now=EXPIRES + timedelta(days=1)).status is ShareLinkStatus.EXPIRED


def test_expiry_compares_instants_across_time_zones() -> None:
    """RULE-AUTH-002：時差の違う時刻も同じ瞬間として比べる（JST 09:00 = UTC 00:00）。"""
    jst = timezone(timedelta(hours=9))
    expires_jst = datetime(2026, 12, 1, 9, 0, tzinfo=jst)  # = EXPIRES
    assert verify_share_link(TOKEN, link(expires_at=expires_jst), now=EXPIRES).status is ShareLinkStatus.EXPIRED
    assert verify_share_link(TOKEN, link(expires_at=expires_jst), now=EXPIRES - timedelta(seconds=1)).valid


def test_revoked_link_is_rejected() -> None:
    """RULE-AUTH-002：失効させたリンクは有効期限の前でも REVOKED。期限切れと両方なら REVOKED を返す。"""
    now = EXPIRES - timedelta(days=1)
    revoked = verify_share_link(TOKEN, link(revoked_at=now - timedelta(hours=1)), now=now)
    assert revoked.status is ShareLinkStatus.REVOKED and not revoked.valid
    assert revoked.project_id is None
    both = verify_share_link(TOKEN, link(revoked_at=now), now=EXPIRES + timedelta(days=1))
    assert both.status is ShareLinkStatus.REVOKED


def test_token_mismatch_or_missing_link_is_not_found() -> None:
    """RULE-AUTH-002：トークンのハッシュが合わない・リンクが無い・トークンが空は NOT_FOUND。"""
    now = EXPIRES - timedelta(days=1)
    assert verify_share_link("other-token", link(), now=now).status is ShareLinkStatus.NOT_FOUND
    assert verify_share_link(TOKEN + " ", link(), now=now).status is ShareLinkStatus.NOT_FOUND
    assert verify_share_link(TOKEN, None, now=now).status is ShareLinkStatus.NOT_FOUND
    assert verify_share_link("", link(), now=now).status is ShareLinkStatus.NOT_FOUND
    # 行に平文のトークンが入っていても照合しない（ハッシュとだけ比べる）
    plain = ShareLinkRecord(project_id=7, token_hash=TOKEN, scope="GANTT", expires_at=EXPIRES)
    assert verify_share_link(TOKEN, plain, now=now).status is ShareLinkStatus.NOT_FOUND


@pytest.mark.parametrize(("scope", "gantt", "report"), [("GANTT", True, False), ("REPORT", False, True)])
def test_scope_limits_what_is_shown(scope: str, gantt: bool, report: bool) -> None:
    """RULE-AUTH-002：見せる範囲はリンクの scope（GANTT/REPORT）と一致するものだけ。無効なリンクは何も見せない。"""
    now = EXPIRES - timedelta(days=1)
    verdict = verify_share_link(TOKEN, link(scope), now=now)
    assert verdict.can_view("GANTT") is gantt
    assert verdict.can_view("REPORT") is report
    expired = verify_share_link(TOKEN, link(scope), now=EXPIRES)
    assert not expired.can_view("GANTT") and not expired.can_view("REPORT")
    with pytest.raises(ValueError):
        verdict.can_view("WBS")


def test_share_link_inputs_fail_fast() -> None:
    """RULE-AUTH-002：時差の無い時刻・未知の範囲は ValueError（fail fast）。"""
    with pytest.raises(ValueError):
        verify_share_link(TOKEN, link(), now=datetime(2026, 11, 10, 0, 0))
    with pytest.raises(ValueError):
        link(expires_at=datetime(2026, 12, 1, 0, 0))
    with pytest.raises(ValueError):
        link(revoked_at=datetime(2026, 11, 10, 0, 0))
    with pytest.raises(ValueError):
        link("WBS")
