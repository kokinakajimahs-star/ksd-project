"""工数：ETC・EAC・多軸集計・差異の判定・実績工数の入力検査（設計書 5.1 の RULE-EFF-001〜005、5.8。
要求仕様書 EFF-01・02・06〜10、7.10 BR-10・7.11 BR-11）。

- 残工数（RULE-EFF-001。BR-10）：完了（進捗率 100）は 0。それ以外は担当者の入力値を優先し、入力が無ければ
  見積×（1−進捗率）、進捗率が未算定なら max(見積−実績, 0)。どの式で求めたかを ``EtcSource`` で返す（EFF-06 の「自動か入力か」）
- EAC と上位への集計（RULE-EFF-002。5.8）：活動の EAC＝実績＋ETC。WP はその活動の合計、**活動を 1 つも持たない WP** は
  自分の見積を計画・ETC・EAC とし実績は 0。上位はその下位の合計。計画（今の見積）・基準（スケジュールベースラインの見積）・
  予測（EAC）を別の値で持つ（EFF-08）。基準は版の項目から 5.8 の数え方で求める（WP は版の WORK_PACKAGE の項目。設計書 1.1・CR-007）
- 多軸集計（RULE-EFF-003。5.8 の表）：WBS・担当者・組織・期間（週・月）の 4 軸。どの軸でも桶の合計がプロジェクトの合計に一致する（EFF-09）。
  版にあって今は無い活動・WP の基準は、どの軸でも桶「基準にあって今は無い」に置く
- 差異の判定（RULE-EFF-004。BR-11）：差異＝EAC−基準、差異率＝差異÷基準。差異率と絶対値の**両方**の閾値を超えた要素と、
  基準に無い要素（追加）を抽出する
- 実績工数の入力検査（RULE-EFF-005。EFF-02）：自分の割当の活動だけ・1 日の合計 24 時間以下・負の値の禁止・
  確定した報告日以前の日は変えない。全ての問題を集めて返す（1 件でもあれば全体を拒むのは呼び手）

工数は人時の ``Decimal``（2.4 の ``Numeric(10,2)``）。``float`` は誤差を持ち込むので受けない。式で求めた ETC と、
比で分けた値は小数 2 桁にする（分けた値は最大剰余法で配り、合計を崩さない）。
純粋に保つ（1.3）。DB・HTTP・時刻・乱数に触れない。今日・報告日は引数で受ける。DB の行はサービス層が下のデータクラスへ直して渡す。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from fractions import Fraction
from types import MappingProxyType

from app.domain.calendar import WorkCalendar
from app.domain.estimate import (
    HOURS_QUANTUM,
    MAX_HOURS_PER_DAY,
    ActivityEstimate,
    ElementKind,
    EstimateError,
    RollupSource,
    WbsNode,
    rollup_estimates,
)

_ZERO = Decimal(0)
_HUNDRED = Decimal(100)
_ONE_DAY = timedelta(days=1)
_QUANTUM_EXPONENT = -2  # HOURS_QUANTUM（0.01）の桁


class EffortError(ValueError):
    """工数の計算・集計の入力が不正（未定義の入力は推測せずに拒む）。"""


# ---------------------------------------------------------------------------
# 値の検査
# ---------------------------------------------------------------------------


def _hours(value: object, what: str) -> Decimal:
    """工数の値を ``Decimal`` として受ける。``float``・``bool``・非数・無限大・負の値は拒む。"""
    if isinstance(value, bool) or not isinstance(value, Decimal | int):
        raise EffortError(f"{what} は Decimal か int で渡す（float は誤差を持ち込むので受けない）: {value!r}")
    hours = Decimal(value)
    if not hours.is_finite():
        raise EffortError(f"{what} が数ではない: {value!r}")
    if hours < 0:
        raise EffortError(f"{what} に負の値は使えない: {value}")
    return hours


def _optional_hours(value: object, what: str) -> Decimal | None:
    return None if value is None else _hours(value, what)


def _percent(value: object, what: str) -> Decimal | None:
    """進捗率（0〜100。``None`` は未算定）。"""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, Decimal | int):
        raise EffortError(f"{what} は Decimal か int で渡す: {value!r}")
    percent = Decimal(value)
    if not percent.is_finite() or percent < 0 or percent > _HUNDRED:
        raise EffortError(f"{what} は 0 以上 100 以下: {value!r}")
    return percent


def _quantize(hours: Decimal) -> Decimal:
    return hours.quantize(HOURS_QUANTUM, rounding=ROUND_HALF_UP)


def _add_optional(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    """基準の値の和。片方だけあればその値、両方無ければ ``None``（基準に無い）。"""
    if a is None:
        return b
    if b is None:
        return a
    return a + b


def _allocate(total: Decimal, weights: Sequence[Fraction]) -> list[Decimal]:
    """``total`` を ``weights`` の比で分ける。最大剰余法で小数 2 桁（``total`` がもっと細かければその桁）に配り、合計を崩さない。

    重みの総和が 0 なら均等に分ける。剰余が同じなら先の桶に先に配る（入力の順で決まる）。
    """
    if not weights:
        raise EffortError("分ける先が無い")
    if any(w < 0 for w in weights):
        raise EffortError(f"分ける比に負の値は使えない: {weights}")
    shares = list(weights)
    weight_sum = sum(shares, Fraction(0))
    if weight_sum == 0:
        shares = [Fraction(1)] * len(shares)
        weight_sum = Fraction(len(shares))
    # 配る単位：小数 2 桁（total がもっと細かい桁を持てばその桁）。有限の数だけが来る
    exponent = min(int(total.as_tuple().exponent), _QUANTUM_EXPONENT)
    quantum = Decimal(1).scaleb(exponent)
    units = int(total.scaleb(-exponent))
    exact = [Fraction(units) * w / weight_sum for w in shares]
    floors = [e.numerator // e.denominator for e in exact]
    rest = units - sum(floors)
    order = sorted(range(len(exact)), key=lambda i: (-(exact[i] - floors[i]), i))
    for i in order[:rest]:
        floors[i] += 1
    return [Decimal(n) * quantum for n in floors]


# ---------------------------------------------------------------------------
# 残工数（ETC）
# ---------------------------------------------------------------------------


class EtcSource(StrEnum):
    """ETC をどこから取ったか（EFF-06 の「自動か入力か」の区別）。``ENTERED`` 以外が自動。"""

    ENTERED = "ENTERED"  # 担当者の入力値（progress_entries.remaining_hours）
    PROGRESS = "PROGRESS"  # 見積×（1−進捗率）
    ACTUAL = "ACTUAL"  # 進捗率が未算定：max(見積−実績, 0)
    COMPLETED = "COMPLETED"  # 完了した活動（進捗率 100）：0
    NO_ACTIVITIES = "NO_ACTIVITIES"  # 活動を 1 つも持たない WP：ETC＝自分の見積（5.8）


@dataclass(frozen=True)
class EtcResult:
    """活動の ETC（人時）と、その出どころ。"""

    hours: Decimal
    source: EtcSource

    @property
    def is_entered(self) -> bool:
        return self.source is EtcSource.ENTERED


# RULE-EFF-001 残工数（ETC）
def compute_etc(
    *,
    estimate_hours: Decimal | int | None,
    percent_complete: Decimal | int | None,
    actual_hours: Decimal | int,
    entered_hours: Decimal | int | None = None,
) -> EtcResult:
    """活動の ETC（BR-10・EFF-06）。

    1. 完了した活動（進捗率 100。RULE-PRG-001 は実績終了日があれば 100 を返す）は 0（入力値があっても 0）
    2. 担当者の入力値（``entered_hours``）があれば、それ
    3. 進捗率があれば、見積×（1−進捗率÷100）。0 未満にはしない。小数 2 桁へ四捨五入する（ROUND_HALF_UP）
    4. 進捗率が未算定（``None``）なら、max(見積−実績, 0)

    見積の ``None``（未見積）は 0 とみなす（RULE-EST-001 の集計と同じ）。工数は 0 以上、進捗率は 0〜100。違えば ``EffortError``。
    """
    estimate = _optional_hours(estimate_hours, "見積") or _ZERO
    percent = _percent(percent_complete, "進捗率")
    actual = _hours(actual_hours, "実績工数")
    entered = _optional_hours(entered_hours, "入力した残工数")
    if percent == _HUNDRED:
        return EtcResult(hours=_ZERO, source=EtcSource.COMPLETED)
    if entered is not None:
        return EtcResult(hours=entered, source=EtcSource.ENTERED)
    if percent is not None:
        return EtcResult(hours=max(_quantize(estimate * (_HUNDRED - percent) / _HUNDRED), _ZERO), source=EtcSource.PROGRESS)
    return EtcResult(hours=max(estimate - actual, _ZERO), source=EtcSource.ACTUAL)


# ---------------------------------------------------------------------------
# EAC と上位への集計
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EffortValues:
    """工数の 5 つの値（人時）。

    - ``planned``：計画（今の見積）
    - ``baseline``：基準（選んだスケジュールベースラインの見積）。基準に無ければ ``None``（EFF-08。計画・予測とは別に持つ）
    - ``actual``：実績、``etc``：残工数、``eac``：予測（完了時総工数見込み）
    """

    planned: Decimal = _ZERO
    baseline: Decimal | None = None
    actual: Decimal = _ZERO
    etc: Decimal = _ZERO
    eac: Decimal = _ZERO

    def plus(self, other: EffortValues) -> EffortValues:
        return EffortValues(
            planned=self.planned + other.planned,
            baseline=_add_optional(self.baseline, other.baseline),
            actual=self.actual + other.actual,
            etc=self.etc + other.etc,
            eac=self.eac + other.eac,
        )


def _sum_values(values: Iterable[EffortValues]) -> EffortValues:
    total = EffortValues()
    for v in values:
        total = total.plus(v)
    return total


@dataclass(frozen=True)
class EffortElement:
    """WBS 要素（4.1 の ``wbs_elements`` のうち集計に要る列）。

    ``own_estimate_hours`` が効くのは**活動を 1 つも持たない WP** だけ（5.8。それ以外の要素の値は下位の合計）。
    基準の値は要素に持たせず、版の項目（``BaselineItem``）から求める。
    """

    id: int
    parent_id: int | None
    kind: ElementKind
    own_estimate_hours: Decimal | None = None


@dataclass(frozen=True)
class ActivityEffortInput:
    """活動の工数の入力。

    - ``estimate_hours``：今の見積（``activities.estimate_hours``。``None`` は 0）
    - ``actual_hours``：実績（その活動の ``time_entries`` の合計。報告日を指定したときは報告日以前の分。5.8）
    - ``percent_complete``：進捗率（RULE-PRG-001。``None`` は未算定）、``entered_etc_hours``：担当者が入力した残工数（``None`` は自動）

    基準の値は版の項目（``BaselineItem``）から求める。
    """

    id: int
    work_package_id: int
    estimate_hours: Decimal | None
    actual_hours: Decimal
    percent_complete: Decimal | None = None
    entered_etc_hours: Decimal | None = None


class BaselineItemType(StrEnum):
    """スケジュールベースラインの項目の種類（4.1 の ``schedule_baseline_items.item_type``）。"""

    ACTIVITY = "ACTIVITY"
    WORK_PACKAGE = "WORK_PACKAGE"


@dataclass(frozen=True)
class BaselineItem:
    """基準に選んだスケジュールベースラインの版の項目（4.1 の ``schedule_baseline_items`` のうち基準の値に要る列）。

    - ``item_type``：ACTIVITY は活動の写し、WORK_PACKAGE は WP の写し（見積はその時点の活動の見積の和、活動が無ければ WP の見積）
    - ``element_id``：WP の項目は自分、活動の項目はその時点の置き場の WP。``activity_id``：写した活動（WP の項目では ``None``）。
      どちらも写した時点の ID で、今の計画に無ければ「基準にあって今は無い」（設計書 1.1 の 4.2・5.8）。
      1.1 より前に消した要素・活動の写しは ``None`` のことがあり、今の計画に無いものとして数える
    - ``estimate_hours``：写した見積（``None`` は 0）。``start_date``・``finish_date``：写した開始日・終了日（期間の軸で基準を配る）
    """

    item_type: BaselineItemType | str
    element_id: int | None
    activity_id: int | None = None
    estimate_hours: Decimal | None = None
    start_date: date | None = None
    finish_date: date | None = None


@dataclass(frozen=True)
class BaselinePlacement:
    """担当者・組織・期間の軸に置く基準の値の 1 つ（5.8 の基準の値の数え方）。

    - 今もある活動の、版の活動の項目：``activity_id`` はその活動（担当者は今の割当の比で分ける）。``element_id`` は今の置き場の WP
    - 版に活動の項目を持たない、今もある WP の WORK_PACKAGE の項目：``activity_id`` は ``None``（担当者は「未割当」）

    期間は ``start_date``〜``finish_date`` の稼働日に均等に配る（日付が無ければ「日付なし」）。
    """

    activity_id: int | None
    element_id: int
    hours: Decimal
    start_date: date | None
    finish_date: date | None


@dataclass(frozen=True)
class ActivityEffort:
    """活動の工数の値と、ETC の出どころ。"""

    activity_id: int
    work_package_id: int
    values: EffortValues
    etc_source: EtcSource


@dataclass(frozen=True)
class ElementEffort:
    """要素の工数の値。``source`` は値をどこから取ったか（RULE-EST-001 と同じ区分）。"""

    element_id: int
    values: EffortValues
    source: RollupSource


@dataclass(frozen=True)
class EffortRollup:
    """EAC と上位への集計の結果（読むだけ）。

    - ``elements``：要素の ID → 値（WP は活動の合計か自分の見積、上位は下位の合計）
    - ``activities``：活動の ID → 値
    - ``roots``：最上位の要素の ID（入力の順）、``project``：プロジェクトの合計（最上位の要素の合計。基準は版の合計：
      最上位の要素の基準の合計に ``wbs_removed_baseline`` を足した値）
    - ``baseline_placements``：担当者・組織・期間の軸に置く基準の値（版の項目の順。基準を選ばなければ空）
    - ``removed_baseline``：担当者・組織・期間の軸の桶「基準にあって今は無い」の基準（版にあって今は無い活動の項目と、
      版に活動の項目を持たない、今は無い WP の WORK_PACKAGE の項目の合計）
    - ``wbs_removed_baseline``：WBS の軸の同じ桶の基準（版にあって今は無い WP の値の合計）

    桶の 2 つは、基準を選ばなかったか、版にあって今は無いものが 1 つも無ければ ``None``。
    """

    elements: Mapping[int, ElementEffort]
    activities: Mapping[int, ActivityEffort]
    roots: tuple[int, ...]
    project: EffortValues
    baseline_placements: tuple[BaselinePlacement, ...] = ()
    removed_baseline: Decimal | None = None
    wbs_removed_baseline: Decimal | None = None

    def leaf_values(self) -> Iterable[tuple[int | None, int, EffortValues]]:
        """集計の葉（活動と、活動を 1 つも持たない WP）を ``(活動の ID か None, 要素の ID, 値)`` で返す。

        全ての葉の計画・実績・ETC・EAC の合計がプロジェクトの合計。基準は版の項目から数える（``baseline_placements``）ので、
        葉の基準の合計はプロジェクトの基準に一致するとは限らない。
        """
        for activity in self.activities.values():
            yield activity.activity_id, activity.work_package_id, activity.values
        for element in self.elements.values():
            if element.source is RollupSource.OWN_ESTIMATE:
                yield None, element.element_id, element.values


@dataclass(frozen=True)
class _BaselineBasis:
    """版の項目を今の計画に当てた結果（5.8 の基準の値の数え方）。"""

    activity_hours: Mapping[int, Decimal]  # 今もある活動 → 版のその活動の項目の見積
    work_package_hours: Mapping[int, Decimal]  # 今もある WP → 版のその WP の値
    placements: tuple[BaselinePlacement, ...]
    removed: Decimal | None
    wbs_removed: Decimal | None


def _baseline_basis(
    nodes: Sequence[EffortElement], acts: Sequence[ActivityEffortInput], items: Iterable[BaselineItem],
) -> _BaselineBasis:
    """版の項目を今の計画に当てる。

    - 版の WP の値：その WP の WORK_PACKAGE の項目の見積。項目が無く活動の項目があれば、その活動の項目の和
      （1.1 より前の写しで ``element_id`` が空の項目は、まとめて 1 つの「今は無い WP」として数える）
    - WBS の軸：今もある WP は版の WP の値。今は WP でない ID の値は桶「基準にあって今は無い」
    - 担当者・組織・期間の軸：今もある活動の項目はその活動に置く。版に活動の項目を持たない WP の項目は、今もある WP なら
      「未割当」に置く。今は無い活動の項目と、版に活動の項目を持たない今は無い WP の項目は桶「基準にあって今は無い」

    版が 5.8 のとおりに作られていれば（WP の項目＝その時点の活動の項目の和）、どちらの数え方でも合計は版の合計になる（EFF-09）。
    項目の種類の誤り・負の見積・同じ活動の項目の重複・同じ WP の WORK_PACKAGE の項目の重複・活動を指す WORK_PACKAGE の項目は ``EffortError``。
    """
    current_activities = {a.id: a for a in acts}
    current_wps = {n.id for n in nodes if n.kind == ElementKind.WORK_PACKAGE}

    parsed: list[tuple[BaselineItem, BaselineItemType, Decimal]] = []
    activity_items_of: dict[int | None, Decimal] = {}  # 版の WP（項目の element_id）→ 活動の項目の和
    package_items_of: dict[int | None, Decimal] = {}  # 版の WP → WORK_PACKAGE の項目の見積
    seen_activities: set[int] = set()
    for item in items:
        try:
            item_type = BaselineItemType(item.item_type)
        except ValueError:
            raise EffortError(f"基準の項目の種類は ACTIVITY・WORK_PACKAGE のいずれか: {item.item_type!r}") from None
        what = (f"基準の活動 {item.activity_id} の項目" if item_type is BaselineItemType.ACTIVITY
                else f"基準の WP {item.element_id} の項目")
        hours = _optional_hours(item.estimate_hours, f"{what} の見積")
        hours = _ZERO if hours is None else hours
        if item_type is BaselineItemType.ACTIVITY:
            if item.activity_id is not None:
                if item.activity_id in seen_activities:
                    raise EffortError(f"基準の版に活動 {item.activity_id} の項目が 2 つある")
                seen_activities.add(item.activity_id)
            activity_items_of[item.element_id] = activity_items_of.get(item.element_id, _ZERO) + hours
        else:
            if item.activity_id is not None:
                raise EffortError(f"{what} が活動 {item.activity_id} を指している（WORK_PACKAGE の項目は活動を持たない）")
            if item.element_id is not None and item.element_id in package_items_of:
                raise EffortError(f"基準の版に WP {item.element_id} の WORK_PACKAGE の項目が 2 つある")
            package_items_of[item.element_id] = package_items_of.get(item.element_id, _ZERO) + hours
        parsed.append((item, item_type, hours))

    def version_value(element_id: int | None) -> Decimal:
        return package_items_of[element_id] if element_id in package_items_of else activity_items_of[element_id]

    version_ids = package_items_of.keys() | activity_items_of.keys()
    removed_ids = [i for i in version_ids if i is None or i not in current_wps]
    activity_hours: dict[int, Decimal] = {}
    placements: list[BaselinePlacement] = []
    removed: Decimal | None = None
    for item, item_type, hours in parsed:
        if item_type is BaselineItemType.ACTIVITY:
            current = current_activities.get(item.activity_id) if item.activity_id is not None else None
            if current is not None:
                activity_hours[current.id] = hours
                placements.append(BaselinePlacement(activity_id=current.id, element_id=current.work_package_id,
                                                    hours=hours, start_date=item.start_date,
                                                    finish_date=item.finish_date))
                continue
        elif item.element_id in activity_items_of:
            continue  # 版に活動の項目がある WP：値は活動の項目が持つ
        elif item.element_id is not None and item.element_id in current_wps:
            placements.append(BaselinePlacement(activity_id=None, element_id=item.element_id, hours=hours,
                                                start_date=item.start_date, finish_date=item.finish_date))
            continue
        removed = hours if removed is None else removed + hours
    return _BaselineBasis(
        activity_hours=activity_hours,
        work_package_hours={i: version_value(i) for i in version_ids if i is not None and i in current_wps},
        placements=tuple(placements),
        removed=removed,
        wbs_removed=sum((version_value(i) for i in removed_ids), _ZERO) if removed_ids else None,
    )


# RULE-EFF-002 EACと上位への集計
def rollup_effort(
    elements: Iterable[EffortElement],
    activities: Iterable[ActivityEffortInput] = (),
    baseline_items: Iterable[BaselineItem] | None = None,
) -> EffortRollup:
    """活動の EAC（実績＋ETC。ETC は RULE-EFF-001）を求め、WP→上位→プロジェクトへ合計する（5.8・EFF-07・EFF-08）。

    - 活動：計画＝今の見積、実績＝入力の実績、ETC＝RULE-EFF-001、EAC＝実績＋ETC
    - 活動を持つ WP：その活動の値の合計（自分の見積は使わない）
    - 活動を 1 つも持たない WP：計画＝ETC＝EAC＝自分の見積（``None`` は 0）、実績＝0
    - WP 以外の要素：子の要素の値の合計（子が無ければ全て 0）。プロジェクト：最上位の要素の合計

    基準（``baseline_items`` は選んだ版の項目。``None`` は版が無い：基準の値は全て ``None``）は 5.8 の数え方（設計書 1.1・CR-007）：

    - 活動：版のその活動の項目の見積。項目が無ければ ``None``（追加された活動）
    - WP：版のその WP の WORK_PACKAGE の項目の見積（活動の和ではない）。版に WORK_PACKAGE の項目も活動の項目も無ければ
      ``None``（追加された WP）
    - WP 以外の要素：下位の基準の合計（``None`` は足さない）。子の無い要素は 0。配下に WP があり、その全てが追加された
      要素なら ``None``（追加された上位の要素。BR-11・RULE-EFF-004）
    - プロジェクト：版の合計（最上位の要素の合計と、WBS の軸の桶「基準にあって今は無い」の和）
    - 版にあって今は無い活動・WP の基準は ``removed_baseline``・``wbs_removed_baseline``、担当者・組織・期間の軸に置く基準は
      ``baseline_placements``（``_baseline_basis``）

    木の検査（ID の重複・存在しない親・循環・子を持つ WP・WP 以外に置いた活動）は RULE-EST-001 と同じで、誤りは ``EffortError``。
    """
    nodes = list(elements)
    acts = list(activities)
    try:
        estimates = rollup_estimates(
            [WbsNode(id=n.id, parent_id=n.parent_id, kind=n.kind, own_estimate_hours=n.own_estimate_hours) for n in nodes],
            [ActivityEstimate(id=a.id, work_package_id=a.work_package_id, estimate_hours=a.estimate_hours) for a in acts],
        )
    except EstimateError as exc:
        raise EffortError(str(exc)) from exc
    basis = None if baseline_items is None else _baseline_basis(nodes, acts, baseline_items)

    activity_results: dict[int, ActivityEffort] = {}
    activity_values_of: dict[int, list[EffortValues]] = {}
    for a in acts:
        what = f"活動 {a.id}"
        actual = _hours(a.actual_hours, f"{what} の実績工数")
        etc = compute_etc(estimate_hours=a.estimate_hours, percent_complete=a.percent_complete, actual_hours=actual,
                          entered_hours=a.entered_etc_hours)
        values = EffortValues(
            planned=estimates.activities[a.id],
            baseline=None if basis is None else basis.activity_hours.get(a.id),
            actual=actual,
            etc=etc.hours,
            eac=actual + etc.hours,
        )
        activity_results[a.id] = ActivityEffort(activity_id=a.id, work_package_id=a.work_package_id, values=values,
                                                etc_source=etc.source)
        activity_values_of.setdefault(a.work_package_id, []).append(values)

    by_id = {n.id: n for n in nodes}
    has_work_package: dict[int, bool] = {}  # 自分か配下に WP がある
    children: dict[int, list[int]] = {n.id: [] for n in nodes}
    roots: list[int] = []
    for n in nodes:
        if n.parent_id is None:
            roots.append(n.id)
        else:
            children[n.parent_id].append(n.id)
    preorder: list[int] = []
    stack = list(reversed(roots))
    while stack:
        node_id = stack.pop()
        preorder.append(node_id)
        stack.extend(reversed(children[node_id]))

    results: dict[int, ElementEffort] = {}
    for node_id in reversed(preorder):
        rollup = estimates.elements[node_id]
        if rollup.source is RollupSource.ACTIVITIES:
            values = _sum_values(activity_values_of[node_id])
        elif rollup.source is RollupSource.OWN_ESTIMATE:
            own = rollup.hours
            values = EffortValues(planned=own, actual=_ZERO, etc=own, eac=own)
        else:
            values = _sum_values(results[kid].values for kid in children[node_id])
        is_work_package = by_id[node_id].kind == ElementKind.WORK_PACKAGE
        package_kids = [kid for kid in children[node_id] if has_work_package[kid]]
        has_work_package[node_id] = is_work_package or bool(package_kids)
        if basis is None:
            baseline = None
        elif is_work_package:
            baseline = basis.work_package_hours.get(node_id)
        elif package_kids and all(results[kid].values.baseline is None for kid in package_kids):
            baseline = None
        else:
            baseline = sum((results[kid].values.baseline or _ZERO for kid in children[node_id]), _ZERO)
        results[node_id] = ElementEffort(element_id=node_id, values=replace(values, baseline=baseline),
                                         source=rollup.source)

    project = _sum_values(results[r].values for r in roots)
    if basis is not None:
        project = replace(project, baseline=(project.baseline or _ZERO) + (basis.wbs_removed or _ZERO))
    return EffortRollup(
        elements=MappingProxyType(results),
        activities=MappingProxyType(activity_results),
        roots=tuple(roots),
        project=project,
        baseline_placements=() if basis is None else basis.placements,
        removed_baseline=None if basis is None else basis.removed,
        wbs_removed_baseline=None if basis is None else basis.wbs_removed,
    )


# ---------------------------------------------------------------------------
# 多軸集計
# ---------------------------------------------------------------------------


class PeriodUnit(StrEnum):
    """期間の軸の桶の幅。週は月曜から日曜（ISO 8601）、月は暦の月。"""

    WEEK = "WEEK"
    MONTH = "MONTH"


class PeriodBucketKind(StrEnum):
    PERIOD = "PERIOD"  # 週・月の桶（``start`` がその初日）
    REMAINING = "REMAINING"  # 報告日以降の桶「残り」（ETC と、EAC のうち ETC の分。5.8）
    UNSCHEDULED = "UNSCHEDULED"  # 日付の無い値（日程を計算していない活動・活動を持たない WP の計画と基準）


@dataclass(frozen=True, order=True)
class PeriodKey:
    kind: PeriodBucketKind
    start: date | None = None


REMAINING = PeriodKey(PeriodBucketKind.REMAINING)
UNSCHEDULED = PeriodKey(PeriodBucketKind.UNSCHEDULED)


class OrganizationBucketKind(StrEnum):
    ORGANIZATION = "ORGANIZATION"  # メンバーの所属（``name``）
    NO_ORGANIZATION = "NO_ORGANIZATION"  # 所属の無い利用者（メンバーでない入力者を含む）
    UNASSIGNED = "UNASSIGNED"  # 割当の無い活動・活動を持たない WP の値（担当者の軸の「未割当」）


@dataclass(frozen=True)
class OrganizationKey:
    kind: OrganizationBucketKind
    name: str | None = None


@dataclass(frozen=True)
class AssignmentShare:
    """割当（4.1 の ``assignments``）。``planned_hours`` は割当工数（``None`` は決めていない）。"""

    activity_id: int
    user_id: int
    planned_hours: Decimal | None = None


@dataclass(frozen=True)
class TimeEntryValue:
    """実績工数の 1 行（4.1 の ``time_entries``）。"""

    user_id: int
    activity_id: int
    work_date: date
    hours: Decimal


@dataclass(frozen=True)
class ActivityPeriod:
    """活動の今の計画の日付（期間の軸で計画を配る）。基準は版の項目の日付で配る（``BaselinePlacement``）。"""

    activity_id: int
    start_date: date | None
    finish_date: date | None


@dataclass(frozen=True)
class EffortAxes:
    """多軸集計の結果（読むだけ）。どの軸でも、桶「基準にあって今は無い」を含めた桶の合計が ``total`` に一致する（EFF-09）。

    - ``wbs``：要素の ID → 値（階層ごとの合計。最上位の要素 ``wbs_roots`` の合計と ``wbs_removed`` の和が ``total``）
    - ``assignee``：利用者の ID → 値。``None`` は「未割当」
    - ``organization``：所属 → 値
    - ``period``：期間の桶 → 値（桶の順に並べた）
    - ``removed``：担当者・組織・期間の軸の桶「基準にあって今は無い」（基準だけを持つ）、``wbs_removed``：WBS の軸の同じ桶
      （プロジェクトの直下の行）。版にあって今は無いものが無ければ ``None``（5.8）
    """

    total: EffortValues
    wbs: Mapping[int, EffortValues]
    wbs_roots: tuple[int, ...]
    assignee: Mapping[int | None, EffortValues]
    organization: Mapping[OrganizationKey, EffortValues]
    period: Mapping[PeriodKey, EffortValues]
    removed: EffortValues | None = None
    wbs_removed: EffortValues | None = None


def _split_values(values: EffortValues, weights: Sequence[Fraction]) -> list[EffortValues]:
    """計画・基準・ETC・EAC を比で分ける（実績は分けない。0 を置く）。"""
    planned = _allocate(values.planned, weights)
    baseline = _allocate(values.baseline, weights) if values.baseline is not None else [None] * len(weights)
    etc = _allocate(values.etc, weights)
    eac = _allocate(values.eac, weights)
    return [EffortValues(planned=p, baseline=b, etc=t, eac=e) for p, b, t, e in zip(planned, baseline, etc, eac, strict=True)]


def _add_to[K](buckets: dict[K, EffortValues], key: K, values: EffortValues) -> None:
    buckets[key] = buckets[key].plus(values) if key in buckets else values


def _period_start(d: date, unit: PeriodUnit) -> date:
    return d - timedelta(days=d.weekday()) if unit is PeriodUnit.WEEK else d.replace(day=1)


def _next_period(start: date, unit: PeriodUnit) -> date:
    if unit is PeriodUnit.WEEK:
        return start + timedelta(days=7)
    return date(start.year + 1, 1, 1) if start.month == 12 else date(start.year, start.month + 1, 1)


def _spread_over_days(
    hours: Decimal, first: date | None, last: date | None, calendar: WorkCalendar, unit: PeriodUnit, what: str,
) -> list[tuple[PeriodKey, Decimal]]:
    """``hours`` を ``[first, last]`` の稼働日に均等に配り、期間の桶ごとに返す。日付が無ければ「日付なし」の桶。

    区間に稼働日が無い（非稼働日のマイルストーンなど）ときは、終了日の桶に全てを置く。
    """
    if first is None or last is None:
        return [(UNSCHEDULED, hours)]
    if last < first:
        raise EffortError(f"{what} の終了日 {last} が開始日 {first} より前")
    keys: list[PeriodKey] = []
    weights: list[Fraction] = []
    start = _period_start(first, unit)
    while start <= last:
        end = _next_period(start, unit) - _ONE_DAY
        keys.append(PeriodKey(PeriodBucketKind.PERIOD, start))
        weights.append(Fraction(calendar.count_inclusive(max(first, start), min(last, end))))
        start = _next_period(start, unit)
    if sum(weights) == 0:
        return [(PeriodKey(PeriodBucketKind.PERIOD, _period_start(last, unit)), hours)]
    return list(zip(keys, _allocate(hours, weights), strict=True))


# RULE-EFF-003 多軸集計
def aggregate_effort(
    rollup: EffortRollup,
    *,
    assignments: Iterable[AssignmentShare] = (),
    time_entries: Iterable[TimeEntryValue] = (),
    organizations: Mapping[int, str | None] | None = None,
    schedules: Iterable[ActivityPeriod] = (),
    calendar: WorkCalendar,
    period_unit: PeriodUnit | str = PeriodUnit.MONTH,
    report_date: date | None = None,
) -> EffortAxes:
    """計画・基準・実績・ETC・EAC を WBS・担当者・組織・期間の 4 軸で集計する（5.8 の表・EFF-09）。

    - WBS：RULE-EFF-002 の階層ごとの合計
    - 担当者：計画・ETC・EAC は活動の値を割当工数の比で分ける（割当が無ければ「未割当」。割当工数が全て空か 0 なら均等に分ける）。
      実績は入力した人（``time_entries.user_id``）。活動を持たない WP の値は「未割当」
    - 組織：担当者の軸を ``organizations``（利用者の ID → メンバーの所属）でまとめる。所属が無い・メンバーでない利用者は「所属なし」
    - 期間：計画は今の計画の開始日〜終了日の稼働日に均等に配る（日付が無ければ「日付なし」）。
      実績は作業日の桶。ETC は桶「残り」。EAC は実績の桶と「残り」（ETC の分）に置く
    - 基準：``rollup.baseline_placements`` を置く。担当者は今の割当の比（割当が無い活動と、版に活動の項目を持たない WP の項目は
      「未割当」）、期間は版の項目の開始日〜終了日の稼働日に均等に配る。版にあって今は無いものは、どの軸でも桶
      「基準にあって今は無い」（``removed``・``wbs_removed``。5.8）

    ``time_entries`` は報告日（``report_date``）を指定すれば報告日以前の分だけを使う（5.8）。活動ごとの合計は RULE-EFF-002 に
    渡した実績に一致していなければならない（違えば ``EffortError``。軸ごとに合計が食い違うのを防ぐ）。
    """
    try:
        unit = PeriodUnit(period_unit)
    except ValueError:
        raise EffortError(f"期間の桶の幅は WEEK・MONTH のいずれか: {period_unit!r}") from None
    orgs = organizations or {}
    activities = rollup.activities

    entries = [e for e in time_entries if report_date is None or e.work_date <= report_date]
    entered_sum: dict[int, Decimal] = {}
    for e in entries:
        if e.activity_id not in activities:
            raise EffortError(f"実績工数の活動 {e.activity_id} が集計の入力に無い")
        entered_sum[e.activity_id] = entered_sum.get(e.activity_id, _ZERO) + _hours(e.hours, f"活動 {e.activity_id} の実績工数")
    for activity_id, activity in activities.items():
        if entered_sum.get(activity_id, _ZERO) != activity.values.actual:
            raise EffortError(
                f"活動 {activity_id} の実績工数 {activity.values.actual} が実績工数の行の合計 {entered_sum.get(activity_id, _ZERO)} と一致しない"
            )

    shares_of: dict[int, list[AssignmentShare]] = {}
    for s in assignments:
        if s.activity_id not in activities:
            raise EffortError(f"割当の活動 {s.activity_id} が集計の入力に無い")
        if any(other.user_id == s.user_id for other in shares_of.get(s.activity_id, [])):
            raise EffortError(f"活動 {s.activity_id} の割当の利用者 {s.user_id} が重複している")
        _optional_hours(s.planned_hours, f"活動 {s.activity_id} の割当工数")
        shares_of.setdefault(s.activity_id, []).append(s)

    periods_of: dict[int, ActivityPeriod] = {}
    for p in schedules:
        if p.activity_id not in activities:
            raise EffortError(f"日付の活動 {p.activity_id} が集計の入力に無い")
        periods_of[p.activity_id] = p

    assignee: dict[int | None, EffortValues] = {}
    period: dict[PeriodKey, EffortValues] = {}
    def weights_of(shares: Sequence[AssignmentShare]) -> list[Fraction]:
        return [Fraction(s.planned_hours or 0) for s in shares]

    for activity_id, _element_id, leaf in rollup.leaf_values():
        values = EffortValues(planned=leaf.planned, etc=leaf.etc, eac=leaf.eac)  # 基準は下で版の項目から置く
        # 担当者：計画・ETC・EAC を割当工数の比で分ける
        shares = shares_of.get(activity_id, []) if activity_id is not None else []
        if shares:
            for share, part in zip(shares, _split_values(values, weights_of(shares)), strict=True):
                _add_to(assignee, share.user_id, part)
        else:
            _add_to(assignee, None, values)
        # 期間：計画は稼働日に配り、ETC と EAC の ETC の分は「残り」へ
        dates = periods_of.get(activity_id) if activity_id is not None else None
        for key, hours in _spread_over_days(values.planned, dates and dates.start_date, dates and dates.finish_date,
                                            calendar, unit, f"活動 {activity_id}"):
            _add_to(period, key, EffortValues(planned=hours))
        _add_to(period, REMAINING, EffortValues(etc=values.etc, eac=values.etc))

    for placement in rollup.baseline_placements:
        # 担当者：今の割当の比で分ける（割当が無い活動と WP の項目は「未割当」）。期間：版の項目の日付の稼働日に配る
        shares = shares_of.get(placement.activity_id, []) if placement.activity_id is not None else []
        if shares:
            for share, hours in zip(shares, _allocate(placement.hours, weights_of(shares)), strict=True):
                _add_to(assignee, share.user_id, EffortValues(baseline=hours))
        else:
            _add_to(assignee, None, EffortValues(baseline=placement.hours))
        what = (f"活動 {placement.activity_id} の基準" if placement.activity_id is not None
                else f"WP {placement.element_id} の基準")
        for key, hours in _spread_over_days(placement.hours, placement.start_date, placement.finish_date, calendar,
                                            unit, what):
            _add_to(period, key, EffortValues(baseline=hours))

    for e in entries:
        hours = Decimal(e.hours)
        _add_to(assignee, e.user_id, EffortValues(actual=hours, eac=_ZERO))
        _add_to(period, PeriodKey(PeriodBucketKind.PERIOD, _period_start(e.work_date, unit)),
                EffortValues(actual=hours, eac=hours))

    # 担当者の軸の EAC は比で分けた値（5.8 の表）。実績の行は EAC に足さない（上で eac=0 で足した）
    organization: dict[OrganizationKey, EffortValues] = {}
    for user_id, values in assignee.items():
        if user_id is None:
            key = OrganizationKey(OrganizationBucketKind.UNASSIGNED)
        elif orgs.get(user_id):
            key = OrganizationKey(OrganizationBucketKind.ORGANIZATION, orgs[user_id])
        else:
            key = OrganizationKey(OrganizationBucketKind.NO_ORGANIZATION)
        _add_to(organization, key, values)

    return EffortAxes(
        total=rollup.project,
        wbs=MappingProxyType({element_id: e.values for element_id, e in rollup.elements.items()}),
        wbs_roots=rollup.roots,
        assignee=MappingProxyType(assignee),
        organization=MappingProxyType(organization),
        period=MappingProxyType(dict(sorted(period.items()))),
        removed=None if rollup.removed_baseline is None else EffortValues(baseline=rollup.removed_baseline),
        wbs_removed=None if rollup.wbs_removed_baseline is None else EffortValues(baseline=rollup.wbs_removed_baseline),
    )


# ---------------------------------------------------------------------------
# 差異の判定
# ---------------------------------------------------------------------------


class VarianceStatus(StrEnum):
    EXCEEDED = "EXCEEDED"  # 差異率と絶対値の両方の閾値を超えた
    WITHIN = "WITHIN"  # どちらかが閾値以内
    ADDED = "ADDED"  # 基準に無い要素（差異率を計算しない。BR-11）


@dataclass(frozen=True)
class VarianceResult:
    """差異の判定の結果。``variance``＝EAC−基準、``rate``＝差異÷基準（追加・基準 0 は ``None``）。"""

    baseline: Decimal | None
    eac: Decimal
    variance: Decimal | None
    rate: Decimal | None
    status: VarianceStatus

    @property
    def extracted(self) -> bool:
        """一覧に出すか（閾値を超えた要素と、追加の要素）。"""
        return self.status is not VarianceStatus.WITHIN


def _threshold(value: object, what: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, Decimal | int):
        raise EffortError(f"{what} は Decimal か int で渡す: {value!r}")
    threshold = Decimal(value)
    if not threshold.is_finite() or threshold < 0:
        raise EffortError(f"{what} は 0 以上の数: {value!r}")
    return threshold


def judge_variance(
    *, baseline_hours: Decimal | None, eac_hours: Decimal, rate_threshold: Decimal, hours_threshold: Decimal,
) -> VarianceResult:
    """1 つの要素の差異を判定する（BR-11）。

    - 基準に無い（``baseline_hours`` が ``None``）：``ADDED``（差異・差異率は計算しない）
    - |差異| ＞ 絶対値の閾値 かつ |差異率| ＞ 差異率の閾値：``EXCEEDED``（増えた向き・減った向きの両方。閾値ちょうどは超えていない）
    - それ以外：``WITHIN``

    差異率の閾値は割合（既定 0.10＝10%。4.2 の ``variance_rate``）、絶対値の閾値は人時（既定 8。``variance_hours``）。
    基準が 0 の要素は差異率を割れないので ``rate`` を ``None`` とし、差異が 0 でなければ差異率の閾値を超えたとみなす。
    """
    rate_limit = _threshold(rate_threshold, "差異率の閾値")
    hours_limit = _threshold(hours_threshold, "差異の絶対値の閾値")
    eac = _hours(eac_hours, "EAC")
    if baseline_hours is None:
        return VarianceResult(baseline=None, eac=eac, variance=None, rate=None, status=VarianceStatus.ADDED)
    baseline = _hours(baseline_hours, "基準の見積")
    variance = eac - baseline
    if baseline == 0:
        rate = None
        rate_exceeded = variance != 0
    else:
        rate = variance / baseline
        rate_exceeded = abs(variance) > rate_limit * baseline
    exceeded = rate_exceeded and abs(variance) > hours_limit
    return VarianceResult(baseline=baseline, eac=eac, variance=variance, rate=rate,
                          status=VarianceStatus.EXCEEDED if exceeded else VarianceStatus.WITHIN)


# RULE-EFF-004 差異の判定
def extract_variances[K](
    targets: Iterable[tuple[K, EffortValues]], *, rate_threshold: Decimal, hours_threshold: Decimal,
) -> list[tuple[K, VarianceResult]]:
    """要素ごとの差異（EAC−基準）を判定し、閾値を両方超えた要素と基準に無い要素だけを入力の順に返す（BR-11・EFF-10）。

    ``targets`` は（要素の鍵, RULE-EFF-002 の値）の列。判定は ``judge_variance``。
    """
    results: list[tuple[K, VarianceResult]] = []
    for key, values in targets:
        judged = judge_variance(baseline_hours=values.baseline, eac_hours=values.eac, rate_threshold=rate_threshold,
                                hours_threshold=hours_threshold)
        if judged.extracted:
            results.append((key, judged))
    return results


# ---------------------------------------------------------------------------
# 実績工数の入力検査
# ---------------------------------------------------------------------------


class TimeEntryProblemCode(StrEnum):
    INVALID_HOURS = "INVALID_HOURS"  # 数でない・小数 3 桁以降がある
    NEGATIVE_HOURS = "NEGATIVE_HOURS"  # 負の値
    NOT_ASSIGNED = "NOT_ASSIGNED"  # 自分の割当でない活動
    LOCKED_DATE = "LOCKED_DATE"  # 確定した報告日以前の日を変えようとした
    DUPLICATE = "DUPLICATE"  # 同じ活動・同じ日を 2 回入れた
    DAY_TOTAL_EXCEEDED = "DAY_TOTAL_EXCEEDED"  # 1 日の合計が 24 時間を超える


@dataclass(frozen=True)
class TimeEntryChange:
    """書きたい実績工数の 1 マス（日×活動）。``hours`` はその日・その活動の値で置き換える（0 は 0 時間）。"""

    activity_id: int
    work_date: date
    hours: Decimal


@dataclass(frozen=True)
class TimeEntryProblem:
    code: TimeEntryProblemCode
    work_date: date
    activity_id: int | None
    message: str


@dataclass(frozen=True)
class TimeEntryCheck:
    """実績工数の入力検査の結果。

    - ``problems``：見つけた問題の全て（無ければ空。1 件でもあれば全体を拒むのは呼び手）
    - ``changes``：値が変わるマス（今の値と同じマスは除く）
    - ``day_totals``：書いた後の 1 日の合計（入力のあった日だけ）
    """

    problems: tuple[TimeEntryProblem, ...]
    changes: tuple[TimeEntryChange, ...]
    day_totals: Mapping[date, Decimal]

    @property
    def ok(self) -> bool:
        return not self.problems


def _entry_hours(value: object) -> tuple[Decimal | None, TimeEntryProblemCode | None]:
    if isinstance(value, bool) or not isinstance(value, Decimal | int):
        return None, TimeEntryProblemCode.INVALID_HOURS
    hours = Decimal(value)
    if not hours.is_finite():
        return None, TimeEntryProblemCode.INVALID_HOURS
    if hours < 0:
        return None, TimeEntryProblemCode.NEGATIVE_HOURS
    if hours != hours.quantize(HOURS_QUANTUM):
        return None, TimeEntryProblemCode.INVALID_HOURS
    return hours, None


# RULE-EFF-005 実績工数の入力検査
def check_time_entries(
    changes: Iterable[TimeEntryChange],
    *,
    existing: Iterable[TimeEntryValue],
    assigned_activity_ids: Iterable[int],
    finalized_report_dates: Iterable[date] = (),
) -> TimeEntryCheck:
    """利用者 1 人の実績工数の書き込み（日×活動のマスの置き換え）を検査する（EFF-02・EFF-01）。

    - 値：0 以上・小数 2 桁まで（負の値は ``NEGATIVE_HOURS``、数でない・3 桁目以降は ``INVALID_HOURS``）
    - 自分の割当の活動だけ（``assigned_activity_ids`` はその利用者の割当の活動。違えば ``NOT_ASSIGNED``）
    - 確定した報告日以前の日は変えない（``finalized_report_dates`` のどれか以前の作業日の値を変えれば ``LOCKED_DATE``。
      報告日の当日も締める。写しと報告日を指定した集計が当日の分を含むため。設計書 1.1・CR-005）
    - 1 日の合計 24 時間以下（``existing`` はその利用者の今の行で、プロジェクトをまたいで渡す。書いた後の合計が 24 を超えれば
      ``DAY_TOTAL_EXCEEDED``。ちょうど 24 は通す）
    - 同じ活動・同じ日のマスを 2 回入れれば ``DUPLICATE``

    今の値と同じ値のマス（行が無いマスは 0 とみなす）は「変えない」ので、割当・確定の検査を当てない（画面がまとめて送り直す形のため）。
    1 日の合計には今の値として数える。
    """
    current: dict[tuple[int, date], Decimal] = {}
    for row in existing:
        current[(row.activity_id, row.work_date)] = current.get((row.activity_id, row.work_date), _ZERO) + Decimal(row.hours)
    assigned = frozenset(assigned_activity_ids)
    locked_through = max(finalized_report_dates, default=None)

    problems: list[TimeEntryProblem] = []
    accepted: dict[tuple[int, date], Decimal] = {}
    changed: list[TimeEntryChange] = []
    seen: set[tuple[int, date]] = set()
    for change in changes:
        cell = (change.activity_id, change.work_date)
        if cell in seen:
            problems.append(TimeEntryProblem(TimeEntryProblemCode.DUPLICATE, change.work_date, change.activity_id,
                                             f"活動 {change.activity_id} の {change.work_date} を 2 回入れている"))
            continue
        seen.add(cell)
        hours, problem = _entry_hours(change.hours)
        if hours is None:
            message = "負の値は入れられない" if problem is TimeEntryProblemCode.NEGATIVE_HOURS else "0 以上・小数 2 桁までの数で入れる"
            problems.append(TimeEntryProblem(problem or TimeEntryProblemCode.INVALID_HOURS, change.work_date,
                                             change.activity_id, f"{message}: {change.hours!r}"))
            continue
        if hours == current.get(cell, _ZERO):
            accepted[cell] = hours
            continue
        if change.activity_id not in assigned:
            problems.append(TimeEntryProblem(TimeEntryProblemCode.NOT_ASSIGNED, change.work_date, change.activity_id,
                                             f"活動 {change.activity_id} は自分の割当ではない"))
            continue
        if locked_through is not None and change.work_date <= locked_through:
            problems.append(TimeEntryProblem(TimeEntryProblemCode.LOCKED_DATE, change.work_date, change.activity_id,
                                             f"確定した報告日 {locked_through} 以前の日 {change.work_date} は変えられない"))
            continue
        accepted[cell] = hours
        changed.append(TimeEntryChange(activity_id=change.activity_id, work_date=change.work_date, hours=hours))

    after = {**current, **accepted}
    day_totals: dict[date, Decimal] = {}
    for day in sorted({d for _, d in seen}):
        total = sum((h for (_, d), h in after.items() if d == day), _ZERO)
        day_totals[day] = total
        if total > MAX_HOURS_PER_DAY:
            problems.append(TimeEntryProblem(TimeEntryProblemCode.DAY_TOTAL_EXCEEDED, day, None,
                                             f"{day} の合計 {total} 時間が {MAX_HOURS_PER_DAY} 時間を超える"))
    return TimeEntryCheck(problems=tuple(problems), changes=tuple(changed), day_totals=MappingProxyType(day_totals))
