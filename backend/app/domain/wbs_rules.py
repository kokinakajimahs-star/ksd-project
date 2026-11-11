"""WBS の判定ルール（設計書 5.1 の RULE-WBS-003・004・005・007・008、5.3。要求仕様書 WBS-01・02・07・08・10・11・13・16、PRJ-02、BR-03）。

- 階層と区分の整合（RULE-WBS-003）：区分は 4 値、WP は子を持たない、活動は WP にだけ置ける、親子の循環を作らない
- 100% ルールの検査（RULE-WBS-004・5.3）：合計の差異（SUM_MISMATCH）・対象外の混入（OUT_OF_SCOPE_LINKED）・対象内の漏れ（IN_SCOPE_UNCOVERED）。
  子の集計は RULE-EST-001（``app.domain.estimate.rollup_estimates``）の値を使う
- 完了条件の必須検査（RULE-WBS-005）：成果物か完了条件が空の WP を列挙する
- 責任分担表の組み立て（RULE-WBS-007）：WP × メンバーの表（責任者・担当・割当工数）と、責任者の無い WP の一覧
- WBS ID による横断照会（RULE-WBS-008）：1 つの WBS ID（旧 ID も可）から配下の要素・活動・見積・実績・EAC・変更要求・是正措置を集める。
  ID の解決は RULE-WBS-002（``app.domain.wbs_code.resolve_code``）を使う

工数は人時の ``Decimal`` で扱い、``float`` は受けない（2.4）。純粋に保つ（1.3）。DB・HTTP・時刻・乱数に触れない。
DB の行はサービス層が下のデータクラスへ直して渡す。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from app.domain.estimate import ActivityEstimate, ElementKind, WbsNode, rollup_estimates
from app.domain.wbs_code import (
    CodedElement,
    CodeHistoryEntry,
    CodeResolution,
    resolve_code,
    subtree_ids,
)

_ZERO = Decimal(0)


class WbsRuleError(ValueError):
    """判定の入力が不正（未定義の入力は推測せずに拒む）。``code`` は API の 422 で返すコード。"""

    def __init__(self, message: str, *, code: str = "VALIDATION_ERROR", details: Sequence[object] = ()) -> None:
        super().__init__(message)
        self.code = code
        self.details = tuple(details)


def _as_hours(value: object, what: str) -> Decimal:
    """工数を ``Decimal`` として受ける。``None`` は 0。``float``・``bool``・非数・負の値は拒む。"""
    if value is None:
        return _ZERO
    if isinstance(value, bool) or not isinstance(value, Decimal | int):
        raise WbsRuleError(f"{what} は Decimal か int で渡す（float は誤差を持ち込むので受けない）: {value!r}")
    hours = Decimal(value)
    if not hours.is_finite() or hours < 0:
        raise WbsRuleError(f"{what} は 0 以上の数: {value!r}")
    return hours


@dataclass(frozen=True)
class ActivityRef:
    """活動のうち WBS の判定に要る列（4.1 の ``activities.id``・``work_package_id``）。"""

    id: int
    work_package_id: int


# ---------------------------------------------------------------------------
# RULE-WBS-003 階層と区分の整合
# ---------------------------------------------------------------------------


class HierarchyIssueCode(StrEnum):
    """階層と区分の不整合の種類。"""

    INVALID_KIND = "INVALID_KIND"  # 区分が 4 値のいずれでもない（WBS-02）
    DUPLICATE_ELEMENT = "DUPLICATE_ELEMENT"  # 要素の ID が重複している
    PARENT_NOT_FOUND = "PARENT_NOT_FOUND"  # 親が入力に無い
    PARENT_CYCLE = "PARENT_CYCLE"  # 親子が循環している（WBS-01）
    WORK_PACKAGE_HAS_CHILDREN = "WORK_PACKAGE_HAS_CHILDREN"  # WP が子を持っている（WBS-02）
    ACTIVITY_NOT_ON_WORK_PACKAGE = "ACTIVITY_NOT_ON_WORK_PACKAGE"  # 活動が WP 以外・無い要素に置かれている（WBS-08・SCH-01）


@dataclass(frozen=True)
class HierarchyIssue:
    """不整合 1 件。``element_ids`` は関わる要素（循環なら循環をなす要素の昇順）、``activity_id`` は活動の不整合のときだけ。"""

    code: HierarchyIssueCode
    element_ids: tuple[int, ...]
    activity_id: int | None
    message: str


def _cycles(parent_of: Mapping[int, int | None]) -> list[tuple[int, ...]]:
    """親のたどりで閉じる輪を、輪ごとに要素の昇順で返す（輪の外から輪へぶら下がる要素は含めない）。"""
    state: dict[int, int] = {}  # 0: 未訪問, 1: たどり中, 2: 済み
    cycles: list[tuple[int, ...]] = []
    for start in sorted(parent_of):
        path: list[int] = []
        node: int | None = start
        while node is not None and node in parent_of and state.get(node, 0) == 0:
            state[node] = 1
            path.append(node)
            node = parent_of[node]
        if node is not None and state.get(node) == 1:
            cycles.append(tuple(sorted(path[path.index(node) :])))
        for visited in path:
            state[visited] = 2
    return cycles


# RULE-WBS-003 階層と区分の整合
def check_hierarchy(elements: Iterable[WbsNode], activities: Iterable[ActivityRef] = ()) -> tuple[HierarchyIssue, ...]:
    """WBS の木と活動の置き場の不整合を全て列挙する（空なら整合。WBS-01・02・08、SCH-01）。

    - 区分は DELIVERABLE・MANAGEMENT・COMPONENT・WORK_PACKAGE のいずれか（``INVALID_KIND``）
    - WP は子の要素を持たない（``WORK_PACKAGE_HAS_CHILDREN``。WP の要素 1 件につき 1 件）
    - 活動は WP にだけ置ける（``ACTIVITY_NOT_ON_WORK_PACKAGE``。WP 以外・入力に無い要素）
    - 親子は循環しない（``PARENT_CYCLE``。自分を親にする要素も循環。輪 1 つにつき 1 件）
    - 入力の誤り：要素の ID の重複（``DUPLICATE_ELEMENT``）、入力に無い親（``PARENT_NOT_FOUND``）

    操作（追加・区分の変更・移動・活動の追加）の検査は、サービス層が**操作した後の木**を渡して呼ぶ。
    並びは、要素の検査（入力の順）→ 循環 → WP の子 → 活動（入力の順）。
    """
    issues: list[HierarchyIssue] = []
    kinds: dict[int, ElementKind | None] = {}
    parent_of: dict[int, int | None] = {}
    for node in elements:
        if node.id in kinds:
            issues.append(
                HierarchyIssue(
                    HierarchyIssueCode.DUPLICATE_ELEMENT, (node.id,), None, f"WBS 要素の ID が重複している: {node.id}"
                )
            )
            continue
        try:
            kinds[node.id] = ElementKind(node.kind)
        except ValueError:
            kinds[node.id] = None
            allowed = "・".join(kind.value for kind in ElementKind)
            issues.append(
                HierarchyIssue(
                    HierarchyIssueCode.INVALID_KIND,
                    (node.id,),
                    None,
                    f"WBS 要素 {node.id} の区分は {allowed} のいずれか: {node.kind!r}",
                )
            )
        parent_of[node.id] = node.parent_id

    for node_id, parent_id in parent_of.items():
        if parent_id is not None and parent_id not in parent_of:
            issues.append(
                HierarchyIssue(
                    HierarchyIssueCode.PARENT_NOT_FOUND,
                    (node_id,),
                    None,
                    f"WBS 要素 {node_id} の親 {parent_id} が入力に無い",
                )
            )

    for cycle in _cycles(parent_of):
        issues.append(
            HierarchyIssue(HierarchyIssueCode.PARENT_CYCLE, cycle, None, f"WBS 要素の親子が循環している: {list(cycle)}")
        )

    children: dict[int, list[int]] = {}
    for node_id, parent_id in parent_of.items():
        if parent_id is not None and parent_id in parent_of:
            children.setdefault(parent_id, []).append(node_id)
    for node_id, kind in kinds.items():
        if kind is ElementKind.WORK_PACKAGE and node_id in children:
            issues.append(
                HierarchyIssue(
                    HierarchyIssueCode.WORK_PACKAGE_HAS_CHILDREN,
                    (node_id, *children[node_id]),
                    None,
                    f"ワークパッケージ {node_id} は子を持てない: {children[node_id]}",
                )
            )

    for activity in activities:
        kind = kinds.get(activity.work_package_id)
        if kind is not ElementKind.WORK_PACKAGE:
            where = "入力に無い要素" if activity.work_package_id not in kinds else f"WP でない要素（{kind}）"
            issues.append(
                HierarchyIssue(
                    HierarchyIssueCode.ACTIVITY_NOT_ON_WORK_PACKAGE,
                    (activity.work_package_id,),
                    activity.id,
                    f"活動 {activity.id} は WP にだけ置ける。置き場 {activity.work_package_id} は{where}",
                )
            )
    return tuple(issues)


def ensure_hierarchy(elements: Iterable[WbsNode], activities: Iterable[ActivityRef] = ()) -> None:
    """``check_hierarchy`` の不整合が 1 件でもあれば ``WbsRuleError``（``code`` は最初の不整合の種類、``details`` は全件）。"""
    issues = check_hierarchy(elements, activities)
    if issues:
        raise WbsRuleError(issues[0].message, code=issues[0].code.value, details=issues)


# ---------------------------------------------------------------------------
# RULE-WBS-004 100%ルールの検査
# ---------------------------------------------------------------------------


class FindingLevel(StrEnum):
    """判定の水準（5.3）。"""

    ERROR = "ERROR"
    WARNING = "WARNING"


class FindingCode(StrEnum):
    """100% ルールの判定のコード（5.3・BR-03）。"""

    SUM_MISMATCH = "SUM_MISMATCH"  # 合計の差異
    OUT_OF_SCOPE_LINKED = "OUT_OF_SCOPE_LINKED"  # 対象外の混入
    IN_SCOPE_UNCOVERED = "IN_SCOPE_UNCOVERED"  # 対象内の漏れ


_LEVEL_OF = {
    FindingCode.SUM_MISMATCH: FindingLevel.WARNING,
    FindingCode.OUT_OF_SCOPE_LINKED: FindingLevel.ERROR,
    FindingCode.IN_SCOPE_UNCOVERED: FindingLevel.WARNING,
}


class ScopeKind(StrEnum):
    """スコープ項目の区分（4.1 の ``scope_items.kind``）。"""

    IN = "IN"  # 対象内
    OUT = "OUT"  # 対象外


@dataclass(frozen=True)
class ScopeItemRef:
    """スコープ項目（4.1 の ``scope_items`` のうち判定に要る列）。"""

    id: int
    kind: ScopeKind


@dataclass(frozen=True)
class ScopeLinkRef:
    """要素とスコープ項目の紐付け（4.1 の ``wbs_scope_links``）。"""

    element_id: int
    scope_item_id: int


@dataclass(frozen=True)
class HundredPercentFinding:
    """100% ルールの判定 1 件（5.3 の ``level``・``code``・対象）。

    - SUM_MISMATCH：``element_id``・``own_estimate_hours``（自分の見積）・``lower_hours``（子の集計）
    - OUT_OF_SCOPE_LINKED：``element_id``・``scope_item_id``（紐付け 1 件につき 1 件）
    - IN_SCOPE_UNCOVERED：``scope_item_id``
    """

    level: FindingLevel
    code: FindingCode
    element_id: int | None = None
    scope_item_id: int | None = None
    own_estimate_hours: Decimal | None = None
    lower_hours: Decimal | None = None


def _finding(code: FindingCode, **kwargs: object) -> HundredPercentFinding:
    return HundredPercentFinding(level=_LEVEL_OF[code], code=code, **kwargs)  # type: ignore[arg-type]


# RULE-WBS-004 100%ルールの検査
def check_hundred_percent(
    elements: Sequence[WbsNode],
    activities: Iterable[ActivityEstimate],
    scope_items: Iterable[ScopeItemRef],
    links: Iterable[ScopeLinkRef],
    tolerance: Decimal | int = 0,
) -> tuple[HundredPercentFinding, ...]:
    """100% ルールの 3 判定を返す（5.3・BR-03・WBS-16 の受入基準）。

    - ``SUM_MISMATCH``（WARNING）：子（WP では活動）を持つ要素に自分の見積があり、``abs(子の集計−自分の見積) / 自分の見積 > tolerance``。
      自分の見積が 0 の要素は割合を計算せず、子の集計が 0 でなければ差異とする。子の集計は RULE-EST-001 の ``lower_hours``
    - ``OUT_OF_SCOPE_LINKED``（ERROR）：対象外（OUT）のスコープ項目に紐付いた要素がある
    - ``IN_SCOPE_UNCOVERED``（WARNING）：どの要素にも紐付いていない対象内（IN）のスコープ項目がある

    ``tolerance`` は割合（0.10 が 10%。4.2 の ``hundred_percent_tolerance``。既定 0）。0 以上の ``Decimal`` か ``int``。
    並びは SUM_MISMATCH（要素の入力の順）→ OUT_OF_SCOPE_LINKED（紐付けの入力の順）→ IN_SCOPE_UNCOVERED（項目の入力の順）。
    木・活動の不正は RULE-EST-001 の ``EstimateError``、スコープの入力の不正は ``WbsRuleError``。
    """
    if isinstance(tolerance, bool) or not isinstance(tolerance, Decimal | int):
        raise WbsRuleError(f"許容幅は Decimal か int で渡す（float は受けない）: {tolerance!r}")
    rate = Decimal(tolerance)
    if not rate.is_finite() or rate < 0:
        raise WbsRuleError(f"許容幅は 0 以上の数: {tolerance!r}")

    rollup = rollup_estimates(elements, activities)
    findings: list[HundredPercentFinding] = []
    for node in elements:
        result = rollup.elements[node.id]
        own, lower = result.own_estimate_hours, result.lower_hours
        if own is None or lower is None:
            continue
        mismatch = lower != 0 if own == 0 else abs(lower - own) > rate * own
        if mismatch:
            findings.append(
                _finding(FindingCode.SUM_MISMATCH, element_id=node.id, own_estimate_hours=own, lower_hours=lower)
            )

    items: dict[int, ScopeKind] = {}
    for item in scope_items:
        if item.id in items:
            raise WbsRuleError(f"スコープ項目の ID が重複している: {item.id}")
        try:
            items[item.id] = ScopeKind(item.kind)
        except ValueError:
            raise WbsRuleError(f"スコープ項目 {item.id} の区分は IN か OUT: {item.kind!r}") from None

    linked: set[int] = set()
    seen: set[tuple[int, int]] = set()
    for link in links:
        pair = (link.element_id, link.scope_item_id)
        if pair in seen:
            raise WbsRuleError(f"紐付けが重複している: 要素 {pair[0]}・スコープ項目 {pair[1]}")
        seen.add(pair)
        if link.element_id not in rollup.elements:
            raise WbsRuleError(f"紐付けの要素 {link.element_id} が入力に無い")
        kind = items.get(link.scope_item_id)
        if kind is None:
            raise WbsRuleError(f"紐付けのスコープ項目 {link.scope_item_id} が入力に無い")
        linked.add(link.scope_item_id)
        if kind is ScopeKind.OUT:
            findings.append(
                _finding(FindingCode.OUT_OF_SCOPE_LINKED, element_id=link.element_id, scope_item_id=link.scope_item_id)
            )

    for item_id, kind in items.items():
        if kind is ScopeKind.IN and item_id not in linked:
            findings.append(_finding(FindingCode.IN_SCOPE_UNCOVERED, scope_item_id=item_id))
    return tuple(findings)


def count_errors(findings: Iterable[HundredPercentFinding]) -> int:
    """ERROR の判定の件数（WBS の承認申請の前提。5.10）。"""
    return sum(1 for finding in findings if finding.level is FindingLevel.ERROR)


# ---------------------------------------------------------------------------
# RULE-WBS-005 完了条件の必須検査
# ---------------------------------------------------------------------------


class CompletionField(StrEnum):
    """WP に必須の辞書の項目（4.1 の ``wbs_dictionary_entries`` の列名。WBS-13）。"""

    DELIVERABLE = "deliverable"  # 成果物
    COMPLETION_CRITERIA = "completion_criteria"  # 完了条件（受入条件）


@dataclass(frozen=True)
class DictionaryRef:
    """WBS 辞書のうち必須検査に要る列（4.1 の ``wbs_dictionary_entries``）。"""

    element_id: int
    deliverable: str | None = None
    completion_criteria: str | None = None


@dataclass(frozen=True)
class MissingCompletion:
    """成果物か完了条件が空の WP 1 件。``missing`` は空の項目（成果物 → 完了条件の順）。"""

    element_id: int
    missing: tuple[CompletionField, ...]


def _is_blank(text: object) -> bool:
    if text is None:
        return True
    if not isinstance(text, str):
        raise WbsRuleError(f"辞書の項目は文字列で渡す: {text!r}")
    return text.strip() == ""


# RULE-WBS-005 完了条件の必須検査
def find_missing_completion(
    elements: Iterable[WbsNode], entries: Iterable[DictionaryRef] = ()
) -> tuple[MissingCompletion, ...]:
    """成果物か完了条件が空の WP を列挙する（WBS-13 の受入基準・WBS-11）。

    - 対象は WP だけ（区分が WORK_PACKAGE の要素。他の区分は空でも挙げない）
    - 空とは、辞書の行が無い・値が ``None``・空白だけの文字列（全角の空白を含む）
    - 並びは要素の入力の順
    1 件でもあれば WBS の承認申請ができない（RULE-WBS-006 の前提）。辞書の行の重複・入力に無い要素の行は ``WbsRuleError``。
    """
    nodes: dict[int, ElementKind] = {}
    for node in elements:
        if node.id in nodes:
            raise WbsRuleError(f"WBS 要素の ID が重複している: {node.id}")
        try:
            nodes[node.id] = ElementKind(node.kind)
        except ValueError:
            raise WbsRuleError(f"WBS 要素 {node.id} の区分が 4 値のいずれでもない: {node.kind!r}") from None
    by_element: dict[int, DictionaryRef] = {}
    for entry in entries:
        if entry.element_id in by_element:
            raise WbsRuleError(f"WBS 要素 {entry.element_id} の辞書が重複している")
        if entry.element_id not in nodes:
            raise WbsRuleError(f"辞書の要素 {entry.element_id} が入力に無い")
        by_element[entry.element_id] = entry

    result: list[MissingCompletion] = []
    for node_id, kind in nodes.items():
        if kind is not ElementKind.WORK_PACKAGE:
            continue
        entry = by_element.get(node_id)
        missing: list[CompletionField] = []
        if entry is None or _is_blank(entry.deliverable):
            missing.append(CompletionField.DELIVERABLE)
        if entry is None or _is_blank(entry.completion_criteria):
            missing.append(CompletionField.COMPLETION_CRITERIA)
        if missing:
            result.append(MissingCompletion(element_id=node_id, missing=tuple(missing)))
    return tuple(result)


# ---------------------------------------------------------------------------
# RULE-WBS-007 責任分担表の組み立て
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResponsibilityElement:
    """WBS 要素のうち責任分担に要る列（4.1 の ``wbs_elements``）。"""

    id: int
    kind: ElementKind
    responsible_user_id: int | None = None
    responsible_team: str | None = None


@dataclass(frozen=True)
class AssignmentRef:
    """担当・割当（4.1 の ``assignments``）。``planned_hours`` が NULL なら ``None``。"""

    activity_id: int
    user_id: int
    planned_hours: Decimal | None = None


@dataclass(frozen=True)
class MatrixCell:
    """責任分担表の 1 マス（WP × 人）。"""

    user_id: int
    is_responsible: bool  # WP の責任者
    is_assigned: bool  # WP の活動のどれかの担当
    planned_hours: Decimal  # WP の活動での割当工数の合計（割当が無ければ 0）


@dataclass(frozen=True)
class MatrixRow:
    """責任分担表の 1 行（WP 1 件）。``cells`` は表の列（``ResponsibilityMatrix.user_ids``）の順。"""

    element_id: int
    responsible_user_id: int | None
    responsible_team: str | None
    cells: tuple[MatrixCell, ...]
    planned_hours: Decimal  # 行の割当工数の合計


@dataclass(frozen=True)
class ResponsibilityMatrix:
    """責任分担表（WBS-10）。

    - ``user_ids``：列。メンバーを入力の順に並べ、メンバーでない人が責任者・担当に居れば ID の昇順で後ろに足す
    - ``non_member_user_ids``：列のうちメンバーでない人（メンバーから外した後も責任・割当が残っている人）
    - ``rows``：WP を入力の順に並べた行
    - ``without_responsible``：責任者（``responsible_user_id``）の無い WP の ID（入力の順）
    """

    user_ids: tuple[int, ...]
    non_member_user_ids: tuple[int, ...]
    rows: tuple[MatrixRow, ...]
    without_responsible: tuple[int, ...]


# RULE-WBS-007 責任分担表の組み立て
def build_responsibility_matrix(
    elements: Iterable[ResponsibilityElement],
    activities: Iterable[ActivityRef],
    assignments: Iterable[AssignmentRef],
    member_user_ids: Sequence[int],
) -> ResponsibilityMatrix:
    """WP × メンバーの表（責任者・担当・割当工数）と責任者の無い WP の一覧を作る（WBS-10 の受入基準）。

    - 行は WP だけ（WP 以外の要素は表に入れない）。責任者が無い WP は、担当チームだけがあっても「責任者の無い WP」に入れる
    - 担当は、WP の活動のどれかに割当（``assignments``）がある人。割当工数は WP の活動での ``planned_hours`` の合計（NULL は 0）
    - 活動が WP 以外・入力に無い要素に置かれている、割当の活動が入力に無い、割当（活動・人）が重複する、工数が負なら ``WbsRuleError``
    """
    members: list[int] = []
    for user_id in member_user_ids:
        if user_id in members:
            raise WbsRuleError(f"メンバーの利用者 ID が重複している: {user_id}")
        members.append(user_id)

    wps: list[ResponsibilityElement] = []
    kinds: dict[int, ElementKind] = {}
    for element in elements:
        if element.id in kinds:
            raise WbsRuleError(f"WBS 要素の ID が重複している: {element.id}")
        try:
            kinds[element.id] = ElementKind(element.kind)
        except ValueError:
            raise WbsRuleError(f"WBS 要素 {element.id} の区分が 4 値のいずれでもない: {element.kind!r}") from None
        if kinds[element.id] is ElementKind.WORK_PACKAGE:
            wps.append(element)

    wp_of_activity: dict[int, int] = {}
    for activity in activities:
        if activity.id in wp_of_activity:
            raise WbsRuleError(f"活動の ID が重複している: {activity.id}")
        if kinds.get(activity.work_package_id) is not ElementKind.WORK_PACKAGE:
            raise WbsRuleError(f"活動 {activity.id} が WP でない・入力に無い要素 {activity.work_package_id} に置かれている")
        wp_of_activity[activity.id] = activity.work_package_id

    hours: dict[tuple[int, int], Decimal] = {}  # (WP, 人) → 割当工数
    seen: set[tuple[int, int]] = set()
    for assignment in assignments:
        pair = (assignment.activity_id, assignment.user_id)
        if pair in seen:
            raise WbsRuleError(f"割当が重複している: 活動 {pair[0]}・利用者 {pair[1]}")
        seen.add(pair)
        wp_id = wp_of_activity.get(assignment.activity_id)
        if wp_id is None:
            raise WbsRuleError(f"割当の活動 {assignment.activity_id} が入力に無い")
        planned = _as_hours(assignment.planned_hours, f"活動 {assignment.activity_id} の割当工数")
        key = (wp_id, assignment.user_id)
        hours[key] = hours.get(key, _ZERO) + planned

    member_set = set(members)
    others = {wp.responsible_user_id for wp in wps if wp.responsible_user_id is not None}
    others |= {user_id for _, user_id in hours}
    non_members = tuple(sorted(others - member_set))
    columns = (*members, *non_members)

    rows: list[MatrixRow] = []
    for wp in wps:
        cells = tuple(
            MatrixCell(
                user_id=user_id,
                is_responsible=wp.responsible_user_id == user_id,
                is_assigned=(wp.id, user_id) in hours,
                planned_hours=hours.get((wp.id, user_id), _ZERO),
            )
            for user_id in columns
        )
        rows.append(
            MatrixRow(
                element_id=wp.id,
                responsible_user_id=wp.responsible_user_id,
                responsible_team=wp.responsible_team,
                cells=cells,
                planned_hours=sum((cell.planned_hours for cell in cells), _ZERO),
            )
        )
    return ResponsibilityMatrix(
        user_ids=columns,
        non_member_user_ids=non_members,
        rows=tuple(rows),
        without_responsible=tuple(wp.id for wp in wps if wp.responsible_user_id is None),
    )


# ---------------------------------------------------------------------------
# RULE-WBS-008 WBS IDによる横断照会
# ---------------------------------------------------------------------------


class EstimateTarget(StrEnum):
    """見積の記録の対象（4.1 の ``estimate_records.target_type``）。"""

    ELEMENT = "ELEMENT"
    ACTIVITY = "ACTIVITY"


@dataclass(frozen=True)
class EstimateRecordRef:
    """見積の記録（4.1 の ``estimate_records`` のうち照会に要る列）。"""

    id: int
    target_type: EstimateTarget
    target_id: int


@dataclass(frozen=True)
class TimeEntryRef:
    """実績工数（4.1 の ``time_entries`` のうち照会に要る列）。"""

    id: int
    activity_id: int
    hours: Decimal


@dataclass(frozen=True)
class ChangeRequestRef:
    """変更要求（4.1 の ``change_requests`` のうち照会に要る列）。``target_codes`` は起票した時点の WBS ID の並び。"""

    id: int
    target_codes: tuple[str, ...]


@dataclass(frozen=True)
class CorrectiveActionRef:
    """是正措置（4.1 の ``corrective_actions`` のうち照会に要る列）。"""

    id: int
    element_id: int | None = None
    activity_id: int | None = None


@dataclass(frozen=True)
class WbsTrace:
    """WBS ID による横断照会の結果（WBS-07）。ID の並びはどれも入力の順（要素は先行順・兄弟は表示順）。

    - ``resolution``：照会した ID の解決（今の ID か旧 ID か、要素の今の ID）
    - ``element_ids``：解決した要素と配下
    - ``activity_ids``：配下の WP の活動
    - ``estimate_record_ids``：配下の要素・活動を対象にした見積の記録
    - ``time_entry_ids``・``actual_hours``：配下の活動の実績工数と、その合計
    - ``eac_hours``：解決した要素の EAC（呼び手が RULE-EFF-002 の集計の値を渡したときだけ。渡さなければ ``None``）
    - ``change_request_ids``：対象の WBS ID（旧 ID も解決する）のどれかが配下の要素に当たる変更要求
    - ``corrective_action_ids``：配下の要素か活動を指す是正措置
    """

    resolution: CodeResolution
    element_ids: tuple[int, ...]
    activity_ids: tuple[int, ...]
    estimate_record_ids: tuple[int, ...]
    time_entry_ids: tuple[int, ...]
    actual_hours: Decimal
    eac_hours: Decimal | None
    change_request_ids: tuple[int, ...]
    corrective_action_ids: tuple[int, ...]


# RULE-WBS-008 WBS IDによる横断照会
def trace_wbs_code(
    code: str,
    *,
    elements: Sequence[CodedElement],
    history: Sequence[CodeHistoryEntry] = (),
    activities: Iterable[ActivityRef] = (),
    estimate_records: Iterable[EstimateRecordRef] = (),
    time_entries: Iterable[TimeEntryRef] = (),
    change_requests: Iterable[ChangeRequestRef] = (),
    corrective_actions: Iterable[CorrectiveActionRef] = (),
    eac_hours_by_element: Mapping[int, Decimal] | None = None,
) -> WbsTrace | None:
    """1 つの WBS ID（旧 ID も可）から、その要素（配下を含む）の活動・見積・実績・EAC・変更要求・是正措置を集める（WBS-07 の受入基準）。

    ID は RULE-WBS-002（``resolve_code``）で今の要素へ解決する。解決できなければ ``None``。
    変更要求の対象の ID も同じく解決し、解決できない ID（消した要素など）は数えない。
    入力の不正（木・ID の重複）は ``WbsCodeError``・``WbsRuleError``。
    """
    resolution = resolve_code(code, elements, history)
    if resolution is None:
        return None
    scope = subtree_ids(elements, resolution.element_id)
    in_scope = set(scope)

    activity_ids: list[int] = []
    seen_activities: set[int] = set()
    for activity in activities:
        if activity.id in seen_activities:
            raise WbsRuleError(f"活動の ID が重複している: {activity.id}")
        seen_activities.add(activity.id)
        if activity.work_package_id in in_scope:
            activity_ids.append(activity.id)
    activity_set = set(activity_ids)

    record_ids: list[int] = []
    for record in estimate_records:
        try:
            target = EstimateTarget(record.target_type)
        except ValueError:
            raise WbsRuleError(f"見積の記録 {record.id} の対象は ELEMENT か ACTIVITY: {record.target_type!r}") from None
        if record.target_id in (in_scope if target is EstimateTarget.ELEMENT else activity_set):
            record_ids.append(record.id)

    entry_ids: list[int] = []
    actual = _ZERO
    for entry in time_entries:
        if entry.activity_id in activity_set:
            entry_ids.append(entry.id)
            actual += _as_hours(entry.hours, f"実績工数 {entry.id}")

    resolved: dict[str, int | None] = {}

    def element_of(target_code: str) -> int | None:
        if target_code not in resolved:
            hit = resolve_code(target_code, elements, history)
            resolved[target_code] = None if hit is None else hit.element_id
        return resolved[target_code]

    change_ids = [
        change.id
        for change in change_requests
        if any(element_of(target_code) in in_scope for target_code in change.target_codes)
    ]

    action_ids = [
        action.id
        for action in corrective_actions
        if (action.element_id is not None and action.element_id in in_scope)
        or (action.activity_id is not None and action.activity_id in activity_set)
    ]

    eac: Decimal | None = None
    if eac_hours_by_element is not None and resolution.element_id in eac_hours_by_element:
        eac = _as_hours(eac_hours_by_element[resolution.element_id], f"WBS 要素 {resolution.element_id} の EAC")

    return WbsTrace(
        resolution=resolution,
        element_ids=scope,
        activity_ids=tuple(activity_ids),
        estimate_record_ids=tuple(record_ids),
        time_entry_ids=tuple(entry_ids),
        actual_hours=actual,
        eac_hours=eac,
        change_request_ids=tuple(change_ids),
        corrective_action_ids=tuple(action_ids),
    )
