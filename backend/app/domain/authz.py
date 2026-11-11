"""認証・権限の判定（設計書 5.1 の RULE-AUTH-001・002、8 章 NFR-04、9 章）。

- 権限の判定（RULE-AUTH-001）：9.2 のロール×権限の表をプロジェクトの作成時に ``role_permissions`` へ写し、
  以後はその**プロジェクトの表**（PM が permission.manage で変えたもの）で可否を決める。
  システム管理者は全ての権限を持つ。progress.enter_own・effort.enter_own は自分の割当の活動に限る。
  委託先（CONTRACTOR）の project.view は、自分の割当の活動とその WBS の経路だけに絞る（9.2 の注記）
- 共有リンクの検証（RULE-AUTH-002）：トークンの SHA-256 のハッシュの照合、失効・有効期限の判定、見せる範囲（GANTT/REPORT）

純粋に保つ（1.3）。DB・HTTP・時刻・乱数に触れない。「今」は引数で受ける。DB の行はサービス層が下のデータクラスへ直して渡す。
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType

# ---------------------------------------------------------------------------
# 9.1 権限のコード・3 章のロール
# ---------------------------------------------------------------------------

PERMISSIONS: tuple[str, ...] = (
    "project.create",
    "project.view",
    "project.manage",
    "member.manage",
    "permission.manage",
    "wbs.edit",
    "wbs.review",
    "wbs.approve",
    "estimate.edit",
    "schedule.edit",
    "baseline.manage",
    "status_report.manage",
    "progress.enter_own",
    "progress.enter_all",
    "effort.enter_own",
    "effort.view",
    "variance.note",
    "change.create",
    "change.approve",
    "change.apply",
    "action.manage",
    "audit.view",
    "share.manage",
    "data.import",
    "data.export",
    "user.admin",
)

# システムの権限（プロジェクトに属さない。9.1）
SYSTEM_PERMISSIONS: frozenset[str] = frozenset({"project.create", "user.admin"})
# システム管理者がユーザーへ個別に許せるシステムの権限（9.1 の project.create の注記）。user.admin はシステム管理者だけが持つ
GRANTABLE_SYSTEM_PERMISSIONS: frozenset[str] = frozenset({"project.create"})
# プロジェクトのロール×権限の表に載る権限
PROJECT_PERMISSIONS: frozenset[str] = frozenset(PERMISSIONS) - SYSTEM_PERMISSIONS
# 自分の割当の活動に限る権限（9.1・6.1 の「自分」）
OWN_ASSIGNMENT_PERMISSIONS: frozenset[str] = frozenset({"progress.enter_own", "effort.enter_own"})

# プロジェクトのロール（要求仕様書 3 章。SYSTEM_ADMIN は users.is_system_admin で表し、ここには含めない）
ROLES: tuple[str, ...] = ("PM", "PLANNER", "EXECUTOR", "PROGRESS_MANAGER", "APPROVER", "CONTRACTOR", "VIEWER")
CONTRACTOR = "CONTRACTOR"

# 9.2 ロールの既定の権限（権限 → 持つロール。表の ○ の列）
_DEFAULT_GRANTS: dict[str, tuple[str, ...]] = {
    "project.view": ("PM", "PLANNER", "EXECUTOR", "PROGRESS_MANAGER", "APPROVER", "CONTRACTOR", "VIEWER"),
    "project.manage": ("PM",),
    "member.manage": ("PM",),
    "permission.manage": ("PM",),
    "wbs.edit": ("PM", "PLANNER"),
    "wbs.review": ("PM", "PLANNER", "APPROVER"),
    "wbs.approve": ("PM", "APPROVER"),
    "estimate.edit": ("PM", "PLANNER"),
    "schedule.edit": ("PM", "PLANNER"),
    "baseline.manage": ("PM",),
    "status_report.manage": ("PM", "PROGRESS_MANAGER"),
    "progress.enter_own": ("PM", "PLANNER", "EXECUTOR", "PROGRESS_MANAGER", "CONTRACTOR"),
    "progress.enter_all": ("PM", "PROGRESS_MANAGER"),
    "effort.enter_own": ("PM", "PLANNER", "EXECUTOR", "PROGRESS_MANAGER", "APPROVER", "CONTRACTOR"),
    "effort.view": ("PM", "PLANNER", "PROGRESS_MANAGER", "APPROVER"),
    "variance.note": ("PM", "PROGRESS_MANAGER"),
    "change.create": ("PM", "PLANNER", "PROGRESS_MANAGER"),
    "change.approve": ("PM", "APPROVER"),
    "change.apply": ("PM",),
    "action.manage": ("PM", "PROGRESS_MANAGER"),
    "audit.view": ("PM", "PROGRESS_MANAGER", "APPROVER"),
    "share.manage": ("PM", "PROGRESS_MANAGER"),
    "data.import": ("PM", "PLANNER"),
    "data.export": ("PM", "PLANNER", "PROGRESS_MANAGER", "VIEWER"),
}

# ロール → 権限の集合（9.2）。プロジェクトの作成時に role_permissions へ写す元
DEFAULT_ROLE_PERMISSIONS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        role: frozenset(permission for permission, roles in _DEFAULT_GRANTS.items() if role in roles)
        for role in ROLES
    }
)

RolePermissionTable = Mapping[str, frozenset[str]]


def default_role_permission_rows() -> list[tuple[str, str]]:
    """9.2 の表を ``role_permissions`` へ写す（ロール, 権限）の行の一覧。ロールの順・権限の順に並べる。"""
    order = {permission: index for index, permission in enumerate(PERMISSIONS)}
    return [
        (role, permission)
        for role in ROLES
        for permission in sorted(DEFAULT_ROLE_PERMISSIONS[role], key=order.__getitem__)
    ]


def role_permission_table(rows: Iterable[tuple[str, str]]) -> dict[str, frozenset[str]]:
    """``role_permissions`` の（ロール, 権限）の行を、ロール → 権限の集合の表にする。

    未知のロール・プロジェクトの表に載らない権限（未知のコード・システムの権限）は ``ValueError``。
    行の無いロールは権限を持たない（空の集合）。
    """
    table: dict[str, set[str]] = {role: set() for role in ROLES}
    for role, permission in rows:
        _check_role(role)
        _check_project_permission(permission)
        table[role].add(permission)
    return {role: frozenset(permissions) for role, permissions in table.items()}


# ---------------------------------------------------------------------------
# 権限の判定の入出力
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Principal:
    """操作する利用者（``users`` の行から作る）。

    ``system_permissions`` はシステム管理者がその利用者へ許したシステムの権限（今は project.create だけを許せる）。
    """

    user_id: int
    is_active: bool
    is_system_admin: bool
    system_permissions: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        extra = set(self.system_permissions) - GRANTABLE_SYSTEM_PERMISSIONS
        if extra:
            raise ValueError(f"個別に許せないシステムの権限: {sorted(extra)}")


@dataclass(frozen=True)
class Membership:
    """判定の対象のプロジェクトでの、その利用者のメンバーとしての立場（``project_members``・``assignments`` から作る）。"""

    roles: frozenset[str]
    assigned_activity_ids: frozenset[int] = frozenset()

    def __post_init__(self) -> None:
        for role in self.roles:
            _check_role(role)


class Reason(StrEnum):
    """判定の理由。"""

    SYSTEM_ADMIN = "SYSTEM_ADMIN"  # システム管理者は全ての権限を持つ
    GRANTED = "GRANTED"  # ロール（またはシステムの権限の個別の許可）で持つ
    GRANTED_ALL = "GRANTED_ALL"  # 自分の割当に限る操作を、全ての活動への権限（progress.enter_all）で許した
    OWN_ASSIGNMENT = "OWN_ASSIGNMENT"  # 自分の割当の活動なので許した
    RESTRICTED_TO_OWN_ASSIGNMENTS = "RESTRICTED_TO_OWN_ASSIGNMENTS"  # 委託先の閲覧。自分の割当の範囲に絞って許した
    INACTIVE_USER = "INACTIVE_USER"
    NOT_MEMBER = "NOT_MEMBER"
    NO_PERMISSION = "NO_PERMISSION"
    NOT_OWN_ASSIGNMENT = "NOT_OWN_ASSIGNMENT"


_ALLOWING_REASONS = frozenset(
    {
        Reason.SYSTEM_ADMIN,
        Reason.GRANTED,
        Reason.GRANTED_ALL,
        Reason.OWN_ASSIGNMENT,
        Reason.RESTRICTED_TO_OWN_ASSIGNMENTS,
    }
)


@dataclass(frozen=True)
class Decision:
    """操作の可否と理由。拒否はサービス層が 403（``FORBIDDEN``）にする。"""

    reason: Reason

    @property
    def allowed(self) -> bool:
        return self.reason in _ALLOWING_REASONS


class ViewLevel(StrEnum):
    """プロジェクトの計画・進捗を読める範囲。"""

    ALL = "ALL"
    OWN_ASSIGNMENTS = "OWN_ASSIGNMENTS"  # 委託先：自分の割当の活動とその WBS の経路だけ
    NONE = "NONE"


@dataclass(frozen=True)
class VisibleScope:
    """委託先に見せる活動と WBS 要素（内部キー）。"""

    activity_ids: frozenset[int]
    element_ids: frozenset[int]


# ---------------------------------------------------------------------------
# 権限の判定
# ---------------------------------------------------------------------------


def effective_permissions(
    principal: Principal,
    *,
    role_permissions: RolePermissionTable,
    membership: Membership | None,
) -> frozenset[str]:
    """利用者がそのプロジェクトで持つ権限の集合（システムの権限を含む。API-CMN-008 の「プロジェクトごとの権限」）。

    自分の割当に限る権限・委託先の閲覧の絞り込みは、この集合には現れない（個々の判定は ``authorize`` で行う）。
    """
    _check_table(role_permissions)
    if not principal.is_active:
        return frozenset()
    if principal.is_system_admin:
        return frozenset(PERMISSIONS)
    granted = set(principal.system_permissions)
    if membership is not None:
        granted |= _granted_by_roles(role_permissions, membership.roles)
    return frozenset(granted)


def project_view_level(
    principal: Principal,
    *,
    role_permissions: RolePermissionTable,
    membership: Membership | None,
) -> ViewLevel:
    """project.view で読める範囲。委託先のロールだけで project.view を持つときは自分の割当の範囲に絞る（9.2 の注記）。

    委託先のほかに project.view を持つロール（例：VIEWER）も持つなら、絞らない。
    """
    _check_table(role_permissions)
    if not principal.is_active:
        return ViewLevel.NONE
    if principal.is_system_admin:
        return ViewLevel.ALL
    if membership is None:
        return ViewLevel.NONE
    viewing_roles = {role for role in membership.roles if "project.view" in role_permissions.get(role, frozenset())}
    if viewing_roles - {CONTRACTOR}:
        return ViewLevel.ALL
    if viewing_roles:
        return ViewLevel.OWN_ASSIGNMENTS
    return ViewLevel.NONE


# RULE-AUTH-001 権限の判定
def authorize(
    principal: Principal,
    permission: str,
    *,
    role_permissions: RolePermissionTable | None = None,
    membership: Membership | None = None,
    activity_id: int | None = None,
) -> Decision:
    """操作の可否を決める（9 章の表とプロジェクトの表）。

    1. 無効な利用者は全て拒む（システム管理者でも）
    2. システム管理者は全ての権限を持つ
    3. システムの権限（project.create・user.admin）は、個別に許されたものだけ
    4. プロジェクトの権限は、メンバーのロール（複数なら和）がプロジェクトの表で持つもの。メンバーでなければ拒む
    5. progress.enter_own・effort.enter_own は ``activity_id`` が自分の割当の活動であること。
       progress.enter_own は progress.enter_all を持てば割当に関わらず許す
    6. project.view は、委託先だけで持つときは自分の割当の範囲に絞って許す。``activity_id`` を渡せばその活動が割当かを見る

    未知の権限・ロール、プロジェクトの権限なのに表が無い、自分の割当に限る権限なのに活動が無い、は呼び手の誤りとして ``ValueError``。
    """
    if permission not in PERMISSIONS:
        raise ValueError(f"未知の権限のコード: {permission!r}")
    if role_permissions is not None:
        _check_table(role_permissions)
    elif permission in PROJECT_PERMISSIONS:
        raise ValueError(f"プロジェクトの権限 {permission} の判定にはプロジェクトのロール×権限の表が要る")
    if permission in OWN_ASSIGNMENT_PERMISSIONS and activity_id is None:
        raise ValueError(f"{permission} は自分の割当の活動に限る権限なので、対象の活動を渡す")

    if not principal.is_active:
        return Decision(Reason.INACTIVE_USER)
    if principal.is_system_admin:
        return Decision(Reason.SYSTEM_ADMIN)

    if permission in SYSTEM_PERMISSIONS:
        return Decision(Reason.GRANTED if permission in principal.system_permissions else Reason.NO_PERMISSION)

    if role_permissions is None or membership is None:  # 表が無いのはプロジェクトの権限でないとき（上で確かめた）
        return Decision(Reason.NOT_MEMBER)
    granted = _granted_by_roles(role_permissions, membership.roles)

    if permission == "project.view":
        level = project_view_level(principal, role_permissions=role_permissions, membership=membership)
        if level is ViewLevel.ALL:
            return Decision(Reason.GRANTED)
        if level is ViewLevel.NONE:
            return Decision(Reason.NO_PERMISSION)
        if activity_id is not None and activity_id not in membership.assigned_activity_ids:
            return Decision(Reason.NOT_OWN_ASSIGNMENT)
        return Decision(Reason.RESTRICTED_TO_OWN_ASSIGNMENTS)

    if permission in OWN_ASSIGNMENT_PERMISSIONS:
        if permission == "progress.enter_own" and "progress.enter_all" in granted:
            return Decision(Reason.GRANTED_ALL)
        if permission not in granted:
            return Decision(Reason.NO_PERMISSION)
        if activity_id not in membership.assigned_activity_ids:
            return Decision(Reason.NOT_OWN_ASSIGNMENT)
        return Decision(Reason.OWN_ASSIGNMENT)

    return Decision(Reason.GRANTED if permission in granted else Reason.NO_PERMISSION)


def own_assignment_scope(
    assigned_activity_ids: Iterable[int],
    *,
    activity_elements: Mapping[int, int],
    element_parents: Mapping[int, int | None],
) -> VisibleScope:
    """委託先に見せる範囲：自分の割当の活動と、その活動を持つ WP から根までの WBS 要素（9.2 の注記の「WBS の経路」）。

    ``activity_elements`` は活動 → WP の要素、``element_parents`` は要素 → 親の要素（根は ``None``）。
    割当の活動・経路の要素が対応に無い、親子が循環している、は ``ValueError``。
    """
    activities = frozenset(assigned_activity_ids)
    elements: set[int] = set()
    for activity in activities:
        if activity not in activity_elements:
            raise ValueError(f"活動 {activity} の WBS 要素が渡されていない")
        element: int | None = activity_elements[activity]
        path: set[int] = set()
        while element is not None and element not in elements:
            if element in path:
                raise ValueError(f"WBS 要素の親子が循環している（要素 {element}）")
            if element not in element_parents:
                raise ValueError(f"WBS 要素 {element} の親が渡されていない")
            path.add(element)
            element = element_parents[element]
        elements |= path
    return VisibleScope(activity_ids=activities, element_ids=frozenset(elements))


def _granted_by_roles(role_permissions: RolePermissionTable, roles: Iterable[str]) -> frozenset[str]:
    granted: set[str] = set()
    for role in roles:
        _check_role(role)
        granted |= role_permissions.get(role, frozenset())
    return frozenset(granted)


def _check_table(role_permissions: RolePermissionTable) -> None:
    for role, permissions in role_permissions.items():
        _check_role(role)
        for permission in permissions:
            _check_project_permission(permission)


def _check_role(role: str) -> None:
    if role not in ROLES:
        raise ValueError(f"未知のロール: {role!r}")


def _check_project_permission(permission: str) -> None:
    if permission not in PROJECT_PERMISSIONS:
        raise ValueError(f"プロジェクトの表に載らない権限: {permission!r}")


# ---------------------------------------------------------------------------
# 共有リンクの検証の入出力
# ---------------------------------------------------------------------------

# 共有リンクで見せる範囲（4.1 の share_links.scope）
SHARE_LINK_SCOPES: tuple[str, ...] = ("GANTT", "REPORT")


def hash_token(token: str) -> str:
    """トークンの SHA-256 のハッシュ（16 進 64 文字）。DB にはこの値だけを保存する（4.2・NFR-04）。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ShareLinkRecord:
    """``share_links`` の行のうち検証に要るもの。時刻は時差つき（aware）で渡す。"""

    project_id: int
    token_hash: str
    scope: str
    expires_at: datetime
    revoked_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.scope not in SHARE_LINK_SCOPES:
            raise ValueError(f"未知の共有の範囲: {self.scope!r}")
        _check_aware(self.expires_at, "expires_at")
        if self.revoked_at is not None:
            _check_aware(self.revoked_at, "revoked_at")


class ShareLinkStatus(StrEnum):
    VALID = "VALID"
    NOT_FOUND = "NOT_FOUND"  # リンクが無い・トークンのハッシュが合わない
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class ShareLinkVerdict:
    """共有リンクの検証の結果。有効なときだけ、見せるプロジェクトと範囲を持つ。"""

    status: ShareLinkStatus
    project_id: int | None = None
    scope: str | None = None

    @property
    def valid(self) -> bool:
        return self.status is ShareLinkStatus.VALID

    def can_view(self, view: str) -> bool:
        """``view``（GANTT・REPORT）を見せてよいか。有効なリンクの範囲と一致するものだけを見せる。"""
        if view not in SHARE_LINK_SCOPES:
            raise ValueError(f"未知の共有の範囲: {view!r}")
        return self.valid and self.scope == view


# RULE-AUTH-002 共有リンクの検証
def verify_share_link(token: str, link: ShareLinkRecord | None, *, now: datetime) -> ShareLinkVerdict:
    """示されたトークンと共有リンクの行を照合し、失効・有効期限を判定する。

    - ``link`` が無い・トークンが空・トークンのハッシュが行の ``token_hash`` と合わない → NOT_FOUND
    - ``revoked_at`` が入っている → REVOKED（有効期限より先に見る）
    - ``now >= expires_at`` → EXPIRED（``expires_at`` の時刻ちょうどで見られなくなる）
    - それ以外 → VALID（行の ``project_id`` と ``scope`` を返す）

    ``now`` は時差つき（aware）で渡す。時差の無い値は ``ValueError``。
    """
    _check_aware(now, "now")
    if link is None or not token:
        return ShareLinkVerdict(ShareLinkStatus.NOT_FOUND)
    if not hmac.compare_digest(hash_token(token).encode("utf-8"), link.token_hash.encode("utf-8")):
        return ShareLinkVerdict(ShareLinkStatus.NOT_FOUND)
    if link.revoked_at is not None:
        return ShareLinkVerdict(ShareLinkStatus.REVOKED)
    if now >= link.expires_at:
        return ShareLinkVerdict(ShareLinkStatus.EXPIRED)
    return ShareLinkVerdict(ShareLinkStatus.VALID, project_id=link.project_id, scope=link.scope)


def _check_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} は時差つき（aware）の時刻で渡す")
