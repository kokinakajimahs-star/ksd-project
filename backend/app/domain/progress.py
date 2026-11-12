"""進捗率の算定方式（設計書 5.1 の RULE-PRG-001。要求仕様書 7.6 BR-06、PRG-02・PRG-05）。

方式（4.1 の ``activities.progress_method``。NULL はプロジェクトの既定 ``projects.settings.progress_method_default``）::

    DIRECT               入力した値
    DELIVERABLE_RATIO    完了した成果物の項目数 ÷ 成果物の項目数
    APPROVED_WORK_RATIO  承認済みの作業項目数 ÷ 作業項目数
    CHECK_ITEM_RATIO     完了した検証項目数 ÷ 検証項目数
    WEIGHTED_MILESTONE   達成したマイルストーン項目の重みの和 ÷ 重みの総和
    ZERO_HUNDRED         実績終了日があれば 100、無ければ 0
    FIFTY_FIFTY          実績終了日があれば 100、実績開始日だけあれば 50、どちらも無ければ 0

- 実績終了日があれば、方式によらず 100（BR-06）
- 項目を数える方式で該当する項目が 0 件なら「未算定」（``None``）。入力を促す（BR-06）。重みの総和が 0 も割れないので未算定
- 直接入力で値が無ければ未算定（4.1 の ``progress_entries.percent_complete`` の NULL）
- 入力の検査（PRG-02）：進捗率は 0〜100、実績開始日より前の実績終了日は拒む（マイルストーンは実績終了日だけを持てるので、
  実績開始日の無い実績終了日は拒まない）

進捗率は 0〜100 の ``Decimal``。項目を数える方式の割合は 4.1 の列（``Numeric(5,2)``）の桁へ四捨五入する
（スナップショットに写した進捗率から同じ進捗ポイントを再現するため。docs/decisions/ASSUMPTIONS.md A-23）。
純粋に保つ（1.3）。DB・HTTP・時刻・乱数に触れない。項目の行はサービス層が下のデータクラスへ直して渡す。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import StrEnum
from typing import Final

HUNDRED: Final = Decimal(100)
FIFTY: Final = Decimal(50)
ZERO: Final = Decimal(0)
# 4.1 の progress_entries.percent_complete（Numeric(5,2)）の桁
PERCENT_QUANTUM: Final = Decimal("0.01")


class ProgressError(ValueError):
    """進捗の入力・算定の入力が不正（未定義の入力は推測せずに拒む）。"""


class ProgressMethod(StrEnum):
    """進捗率の算定方式（BR-06 の 7 つ。4.1 の ``activities.progress_method`` の値）。"""

    DIRECT = "DIRECT"
    DELIVERABLE_RATIO = "DELIVERABLE_RATIO"
    APPROVED_WORK_RATIO = "APPROVED_WORK_RATIO"
    CHECK_ITEM_RATIO = "CHECK_ITEM_RATIO"
    WEIGHTED_MILESTONE = "WEIGHTED_MILESTONE"
    ZERO_HUNDRED = "ZERO_HUNDRED"
    FIFTY_FIFTY = "FIFTY_FIFTY"


class ProgressItemType(StrEnum):
    """進捗の算定項目の種類（4.1 の ``progress_items.item_type``）。"""

    DELIVERABLE = "DELIVERABLE"  # 成果物完了割合
    WORK = "WORK"  # 承認済み作業割合
    CHECK = "CHECK"  # 検証項目完了割合
    MILESTONE = "MILESTONE"  # 重み付きマイルストーン


# 項目を数える方式 → 数える項目の種類
_ITEM_TYPE_OF: Final = {
    ProgressMethod.DELIVERABLE_RATIO: ProgressItemType.DELIVERABLE,
    ProgressMethod.APPROVED_WORK_RATIO: ProgressItemType.WORK,
    ProgressMethod.CHECK_ITEM_RATIO: ProgressItemType.CHECK,
    ProgressMethod.WEIGHTED_MILESTONE: ProgressItemType.MILESTONE,
}


@dataclass(frozen=True)
class ProgressItem:
    """進捗の算定項目（4.1 の ``progress_items`` のうち算定に要る列）。

    ``done``：完了・達成した（成果物・検証項目・マイルストーン）。``approved``：承認済み（作業項目）。
    ``weight``：重み（重み付きマイルストーンで使う。既定 1。負は拒む）。
    """

    item_type: ProgressItemType
    done: bool = False
    approved: bool = False
    weight: Decimal = Decimal(1)

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "item_type", ProgressItemType(self.item_type))
        except ValueError as exc:
            raise ProgressError(f"算定項目の種類が不正: {self.item_type!r}") from exc
        weight = _as_decimal(self.weight, "算定項目の重み")
        if weight < 0:
            raise ProgressError(f"算定項目の重みは 0 以上: {weight}")
        object.__setattr__(self, "weight", weight)


def _as_decimal(value: object, what: str) -> Decimal:
    if isinstance(value, bool):
        raise ProgressError(f"{what}は数で渡す: {value!r}")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ProgressError(f"{what}は数で渡す: {value!r}") from exc
    if not result.is_finite():
        raise ProgressError(f"{what}が有限の数ではない: {value!r}")
    return result


def _quantize(percent: Decimal) -> Decimal:
    return percent.quantize(PERCENT_QUANTUM, rounding=ROUND_HALF_UP)


def validate_progress_input(
    *,
    percent_complete: Decimal | int | None = None,
    actual_start: date | None = None,
    actual_finish: date | None = None,
) -> None:
    """進捗の入力の検査（PRG-02）。進捗率は 0〜100、実績終了日は（実績開始日があれば）実績開始日以後。"""
    if percent_complete is not None:
        p = _as_decimal(percent_complete, "進捗率")
        if p < ZERO or p > HUNDRED:
            raise ProgressError(f"進捗率は 0〜100: {p}")
    if actual_finish is not None and actual_start is not None and actual_finish < actual_start:
        raise ProgressError(f"実績終了日 {actual_finish} が実績開始日 {actual_start} より前（PRG-02）")


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    if denominator == 0:
        return None  # 該当する項目が 0 件（重みの総和が 0）：未算定（BR-06）
    return _quantize(numerator * HUNDRED / denominator)


# RULE-PRG-001 進捗率の算定方式
def compute_percent_complete(
    method: ProgressMethod | str,
    *,
    entered_percent: Decimal | int | None = None,
    items: Iterable[ProgressItem] = (),
    actual_start: date | None = None,
    actual_finish: date | None = None,
) -> Decimal | None:
    """活動の進捗率（0〜100）を算定方式に従って求める（BR-06）。未算定は ``None``。

    - ``method``：活動の方式（活動に無ければサービス層がプロジェクトの既定を渡す）。7 つ以外は ``ProgressError``
    - ``entered_percent``：直接入力の値（``DIRECT`` だけが使う。他の方式では使わない）
    - ``items``：活動の算定項目の全て（方式に合わない種類の項目は数えない）
    - 実績終了日があれば方式によらず 100。入力は ``validate_progress_input`` と同じ検査を通す
    """
    try:
        method = ProgressMethod(method)
    except ValueError as exc:
        raise ProgressError(f"進捗率の算定方式が不正: {method!r}") from exc
    validate_progress_input(
        percent_complete=entered_percent,
        actual_start=actual_start,
        actual_finish=actual_finish,
    )
    if actual_finish is not None:
        return _quantize(HUNDRED)

    if method is ProgressMethod.DIRECT:
        return None if entered_percent is None else _quantize(_as_decimal(entered_percent, "進捗率"))
    if method is ProgressMethod.ZERO_HUNDRED:
        return _quantize(ZERO)
    if method is ProgressMethod.FIFTY_FIFTY:
        return _quantize(FIFTY if actual_start is not None else ZERO)

    item_type = _ITEM_TYPE_OF[method]
    counted = [item for item in items if item.item_type is item_type]
    if method is ProgressMethod.WEIGHTED_MILESTONE:
        total = sum((item.weight for item in counted), ZERO)
        achieved = sum((item.weight for item in counted if item.done), ZERO)
        return _ratio(achieved, total)
    if method is ProgressMethod.APPROVED_WORK_RATIO:
        finished = sum(1 for item in counted if item.approved)
    else:
        finished = sum(1 for item in counted if item.done)
    return _ratio(Decimal(finished), Decimal(len(counted)))
