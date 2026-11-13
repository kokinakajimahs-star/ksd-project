"""見積：ボトムアップ集計と、見積の単位換算・記録（設計書 5.1 の RULE-EST-001・002、5.8。要求仕様書 EST-01・02・08）。

- ボトムアップ集計（RULE-EST-001）：活動→WP→上位→プロジェクトへ合計する。WP の値はその活動の見積の合計、
  **活動を 1 つも持たない WP** は自分の見積（``own_estimate_hours``）を値にする（5.8）。WP 以外の要素は子の要素の値の合計、
  プロジェクトの値は最上位の要素（``parent_id`` が無い要素）の値の合計。下位の合計（5.3 の「子の集計」）も別に返す
- 見積の単位換算と記録（RULE-EST-002）：人日は 1 日の稼働時間（``calendars.hours_per_day``）を掛けて人時へ換算し、
  負の値を拒み、見積の値・入力の値と単位・手法・前提・参照資料を 1 件の記録（4.1 の ``estimate_records`` の 1 行）にする

工数は人時の ``Decimal`` で扱う（2.4 の ``Numeric(10,2)``）。2 進の誤差を持ち込まないよう ``float`` は受けない。
純粋に保つ（1.3）。DB・HTTP・時刻・乱数に触れない。記録の時刻（``entered_at``）は引数で受ける。
DB の行はサービス層が下のデータクラスへ直して渡す。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from types import MappingProxyType

# 2.4 の工数の型 Numeric(10,2)：小数 2 桁、整数部 8 桁
HOURS_QUANTUM = Decimal("0.01")
MAX_HOURS = Decimal("99999999.99")
# 1 日の稼働時間の範囲（4.1 の calendars.hours_per_day。DB の検査制約 0 < h <= 24 と同じ）
MAX_HOURS_PER_DAY = Decimal(24)

_ZERO = Decimal(0)


class EstimateError(ValueError):
    """見積の入力、または集計の入力（WBS の木・活動）が不正（未定義の入力は推測せずに拒む）。"""


class ElementKind(StrEnum):
    """WBS 要素の区分（4.1 の ``wbs_elements.kind``。WBS-02 の 4 値）。"""

    DELIVERABLE = "DELIVERABLE"
    MANAGEMENT = "MANAGEMENT"
    COMPONENT = "COMPONENT"
    WORK_PACKAGE = "WORK_PACKAGE"


class RollupSource(StrEnum):
    """要素の集計値をどこから取ったか。"""

    ACTIVITIES = "ACTIVITIES"  # 活動を持つ WP：活動の見積の合計
    OWN_ESTIMATE = "OWN_ESTIMATE"  # 活動を 1 つも持たない WP：自分の見積（5.8）
    CHILDREN = "CHILDREN"  # WP 以外の要素：子の要素の値の合計


class TargetType(StrEnum):
    """見積の対象（4.1 の ``estimate_records.target_type``）。"""

    ELEMENT = "ELEMENT"
    ACTIVITY = "ACTIVITY"


class InputUnit(StrEnum):
    """見積の入力の単位（4.1 の ``estimate_records.input_unit``。EST-01）。"""

    HOUR = "HOUR"  # 人時
    DAY = "DAY"  # 人日


class EstimateMethod(StrEnum):
    """見積の手法（4.1 の ``estimate_records.method``。EST-08 の 5 つ）。"""

    BOTTOM_UP = "BOTTOM_UP"  # ボトムアップ
    ANALOGOUS = "ANALOGOUS"  # 類推
    THREE_POINT = "THREE_POINT"  # 三点
    EXPERT = "EXPERT"  # 専門家判断
    OTHER = "OTHER"  # その他


# ---------------------------------------------------------------------------
# 値の検査
# ---------------------------------------------------------------------------


def _as_hours(value: object, what: str, *, allow_none: bool = False) -> Decimal | None:
    """工数の値を ``Decimal`` として受ける。``float``・``bool``・非数・無限大・負の値は拒む。"""
    if value is None:
        if allow_none:
            return None
        raise EstimateError(f"{what} が無い")
    if isinstance(value, bool) or not isinstance(value, Decimal | int):
        raise EstimateError(f"{what} は Decimal か int で渡す（float は誤差を持ち込むので受けない）: {value!r}")
    hours = Decimal(value)
    if not hours.is_finite():
        raise EstimateError(f"{what} が数ではない: {value!r}")
    if hours < 0:
        raise EstimateError(f"{what} に負の値は使えない: {value}")
    return hours


def _as_enum[E: StrEnum](enum: type[E], value: object, what: str) -> E:
    try:
        return enum(value)
    except ValueError:
        allowed = "・".join(m.value for m in enum)
        raise EstimateError(f"{what} は {allowed} のいずれか: {value!r}") from None


# ---------------------------------------------------------------------------
# ボトムアップ集計（RULE-EST-001）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WbsNode:
    """WBS 要素（4.1 の ``wbs_elements`` のうち集計に要る列）。``own_estimate_hours`` が NULL なら ``None``。"""

    id: int
    parent_id: int | None
    kind: ElementKind
    own_estimate_hours: Decimal | None = None


@dataclass(frozen=True)
class ActivityEstimate:
    """活動の見積（4.1 の ``activities`` のうち集計に要る列）。``estimate_hours`` が NULL なら ``None``。"""

    id: int
    work_package_id: int
    estimate_hours: Decimal | None = None


@dataclass(frozen=True)
class ElementRollup:
    """1 つの要素の集計の結果。

    - ``hours``：要素の集計値（上位へ足す値）
    - ``source``：集計値をどこから取ったか
    - ``lower_hours``：下位（WP は活動、それ以外は子の要素）の値の合計。下位が 1 つも無ければ ``None``。
      100% ルール（5.3）の「子の集計」に使う
    - ``own_estimate_hours``：要素の自分の見積（入力のまま）
    """

    element_id: int
    hours: Decimal
    source: RollupSource
    lower_hours: Decimal | None
    own_estimate_hours: Decimal | None


@dataclass(frozen=True)
class EstimateRollup:
    """ボトムアップ集計の結果。``elements`` は要素の ID から、``activities`` は活動の ID から引く（読むだけ）。"""

    elements: Mapping[int, ElementRollup]
    activities: Mapping[int, Decimal]
    project_hours: Decimal

    def hours_of(self, element_id: int) -> Decimal:
        """要素の集計値。集計に含まれない要素は ``KeyError``。"""
        return self.elements[element_id].hours


# RULE-EST-001 ボトムアップ集計
def rollup_estimates(elements: Iterable[WbsNode], activities: Iterable[ActivityEstimate] = ()) -> EstimateRollup:
    """活動→WP→上位→プロジェクトへ見積を合計する（5.8・EST-02）。

    - 活動の値は ``estimate_hours``（``None`` は 0）
    - 活動を持つ WP の値は、その活動の値の合計（自分の見積は使わない）
    - 活動を 1 つも持たない WP の値は、自分の見積（``None`` は 0）
    - WP 以外の要素の値は、子の要素の値の合計（子が無ければ 0）
    - プロジェクトの値は、最上位の要素の値の合計

    入力が木になっていない（ID の重複・存在しない親・循環）、WP が子の要素を持つ（WBS-02）、
    活動が WP 以外・存在しない要素に置かれている（WBS-08）、見積が負・数でないときは ``EstimateError``。
    深い木でも再帰の上限に掛からないよう、繰り返しで数える。
    """
    nodes: dict[int, WbsNode] = {}
    for node in elements:
        if node.id in nodes:
            raise EstimateError(f"WBS 要素の ID が重複している: {node.id}")
        kind = _as_enum(ElementKind, node.kind, f"WBS 要素 {node.id} の区分")
        own = _as_hours(node.own_estimate_hours, f"WBS 要素 {node.id} の自分の見積", allow_none=True)
        nodes[node.id] = WbsNode(id=node.id, parent_id=node.parent_id, kind=kind, own_estimate_hours=own)

    children: dict[int, list[int]] = {node_id: [] for node_id in nodes}
    roots: list[int] = []
    for node in nodes.values():
        if node.parent_id is None:
            roots.append(node.id)
        elif node.parent_id not in nodes:
            raise EstimateError(f"WBS 要素 {node.id} の親 {node.parent_id} が集計の入力に無い")
        else:
            children[node.parent_id].append(node.id)

    for node in nodes.values():
        if node.kind is ElementKind.WORK_PACKAGE and children[node.id]:
            raise EstimateError(f"ワークパッケージ {node.id} が子の要素を持っている: {children[node.id]}")

    activity_hours: dict[int, Decimal] = {}
    activities_of: dict[int, list[Decimal]] = {}
    for activity in activities:
        if activity.id in activity_hours:
            raise EstimateError(f"活動の ID が重複している: {activity.id}")
        wp = nodes.get(activity.work_package_id)
        if wp is None:
            raise EstimateError(f"活動 {activity.id} の WP {activity.work_package_id} が集計の入力に無い")
        if wp.kind is not ElementKind.WORK_PACKAGE:
            raise EstimateError(f"活動 {activity.id} が WP でない要素 {wp.id}（{wp.kind}）に置かれている")
        hours = _as_hours(activity.estimate_hours, f"活動 {activity.id} の見積", allow_none=True) or _ZERO
        activity_hours[activity.id] = hours
        activities_of.setdefault(wp.id, []).append(hours)

    # 先行順（親が先）に並べ、その逆順（子が先）に数える
    preorder: list[int] = []
    stack = list(reversed(roots))
    while stack:
        node_id = stack.pop()
        preorder.append(node_id)
        stack.extend(reversed(children[node_id]))
    if len(preorder) != len(nodes):
        reached = set(preorder)
        cyclic = sorted(node_id for node_id in nodes if node_id not in reached)
        raise EstimateError(f"WBS 要素の親子が循環している（最上位から辿れない要素）: {cyclic}")

    results: dict[int, ElementRollup] = {}
    for node_id in reversed(preorder):
        node = nodes[node_id]
        lower: Decimal | None
        if node.kind is ElementKind.WORK_PACKAGE:
            if node_id in activities_of:
                lower = sum(activities_of[node_id], _ZERO)
                hours, source = lower, RollupSource.ACTIVITIES
            else:
                lower = None
                hours, source = node.own_estimate_hours or _ZERO, RollupSource.OWN_ESTIMATE
        else:
            kids = children[node_id]
            lower = sum((results[kid].hours for kid in kids), _ZERO) if kids else None
            hours, source = lower if lower is not None else _ZERO, RollupSource.CHILDREN
        results[node_id] = ElementRollup(
            element_id=node_id,
            hours=hours,
            source=source,
            lower_hours=lower,
            own_estimate_hours=node.own_estimate_hours,
        )

    project_hours = sum((results[root].hours for root in roots), _ZERO)
    return EstimateRollup(
        elements=MappingProxyType(results),
        activities=MappingProxyType(activity_hours),
        project_hours=project_hours,
    )


# ---------------------------------------------------------------------------
# 見積の単位換算と記録（RULE-EST-002）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EstimateRecord:
    """見積の記録 1 件（4.1 の ``estimate_records`` の 1 行。``id`` は DB が付ける）。

    ``hours`` は人時へ換算した見積の値、``input_value``・``input_unit`` は利用者が入れたままの値と単位。
    """

    project_id: int
    target_type: TargetType
    target_id: int
    hours: Decimal
    input_value: Decimal
    input_unit: InputUnit
    method: EstimateMethod | None
    assumptions: str | None
    references: str | None
    entered_by: int
    entered_at: datetime


def _check_hours_per_day(hours_per_day: object) -> Decimal:
    value = _as_hours(hours_per_day, "1 日の稼働時間")
    assert value is not None
    if value == 0 or value > MAX_HOURS_PER_DAY:
        raise EstimateError(f"1 日の稼働時間は 0 より大きく 24 以下: {hours_per_day}")
    return value


def to_hours(input_value: Decimal | int, input_unit: InputUnit | str, hours_per_day: Decimal | int) -> Decimal:
    """見積の入力の値を人時へ換算する（EST-01）。

    - 人時（HOUR）はそのまま、人日（DAY）は ``hours_per_day`` を掛ける
    - 結果は小数 2 桁（2.4 の ``Numeric(10,2)``）へ四捨五入（ROUND_HALF_UP）する
    - 入力の値は 0 以上・小数 2 桁まで。負の値、3 桁目以降に 0 でない数字がある値は拒む（保存の型で黙って丸めない）
    - ``hours_per_day`` は 0 より大きく 24 以下（4.1 の calendars.hours_per_day）
    - 換算した人時が ``Numeric(10,2)`` に入らない値は拒む
    """
    unit = _as_enum(InputUnit, input_unit, "見積の単位")
    value = _as_hours(input_value, "見積の値")
    assert value is not None
    if value != value.quantize(HOURS_QUANTUM):
        raise EstimateError(f"見積の値は小数 2 桁まで: {input_value}")
    if value > MAX_HOURS:
        raise EstimateError(f"見積の値が大きすぎる（上限 {MAX_HOURS}）: {input_value}")
    per_day = _check_hours_per_day(hours_per_day)
    raw = value if unit is InputUnit.HOUR else value * per_day
    hours = raw.quantize(HOURS_QUANTUM, rounding=ROUND_HALF_UP)
    if hours > MAX_HOURS:
        raise EstimateError(f"人時へ換算した見積が大きすぎる（上限 {MAX_HOURS}）: {input_value} {unit} → {hours}")
    return hours


# RULE-EST-002 見積の単位換算と記録
def record_estimate(
    *,
    project_id: int,
    target_type: TargetType | str,
    target_id: int,
    input_value: Decimal | int,
    input_unit: InputUnit | str,
    hours_per_day: Decimal | int,
    entered_by: int,
    entered_at: datetime,
    method: EstimateMethod | str | None = None,
    assumptions: str | None = None,
    references: str | None = None,
) -> EstimateRecord:
    """見積の入力を人時へ換算し、手法・前提・参照資料と合わせて 1 件の記録にする（EST-01・EST-08）。

    - 換算と値の検査は ``to_hours`` のとおり（負の値を拒む）
    - 対象は ELEMENT（WBS 要素）か ACTIVITY（活動）、手法は 5 つ（EST-08）のいずれか。手法・前提・参照資料は省ける（``None``）
    - 前提・参照資料の文字列は入れたまま記録する（書き換えない）
    - ``entered_at`` は時差つき（aware）の時刻で渡す（2.4：時刻は UTC で保存する）。ドメインは「今」を求めない
    """
    target = _as_enum(TargetType, target_type, "見積の対象の種類")
    unit = _as_enum(InputUnit, input_unit, "見積の単位")
    chosen = None if method is None else _as_enum(EstimateMethod, method, "見積の手法")
    for name, text in (("前提", assumptions), ("参照資料", references)):
        if text is not None and not isinstance(text, str):
            raise EstimateError(f"見積の{name}は文字列で渡す: {text!r}")
    if entered_at.tzinfo is None or entered_at.utcoffset() is None:
        raise EstimateError("entered_at は時差つき（aware）の時刻で渡す")
    hours = to_hours(input_value, unit, hours_per_day)
    return EstimateRecord(
        project_id=project_id,
        target_type=target,
        target_id=target_id,
        hours=hours,
        input_value=Decimal(input_value),
        input_unit=unit,
        method=chosen,
        assumptions=assumptions,
        references=references,
        entered_by=entered_by,
        entered_at=entered_at,
    )
