"""進捗率の算定方式（app.domain.progress）の試験。

設計書 5.1 の RULE-PRG-001、要求仕様書 7.6 BR-06・PRG-02・PRG-05。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from fractions import Fraction

import pytest

from app.domain.progress import (
    ProgressError,
    ProgressItem,
    ProgressItemType,
    ProgressMethod,
    compute_percent_complete,
    exact_percent_complete,
    round_percent,
    validate_progress_input,
)
from app.models.prg import PROGRESS_ITEM_TYPES
from app.models.sch import PROGRESS_METHODS

START = date(2026, 12, 1)
FINISH = date(2026, 12, 8)


def items(item_type: ProgressItemType, *flags: bool, approved: bool = False) -> list[ProgressItem]:
    """種類の同じ項目を、完了（承認）の真偽の並びから作る。"""
    if approved:
        return [ProgressItem(item_type, approved=f) for f in flags]
    return [ProgressItem(item_type, done=f) for f in flags]


def test_methods_and_item_types_match_table_values() -> None:
    """RULE-PRG-001：方式は BR-06 の 7 つ、項目の種類は 4.1 の 4 つ（DB の検査の値と同じ名前）。"""
    assert tuple(m.value for m in ProgressMethod) == PROGRESS_METHODS
    assert tuple(t.value for t in ProgressItemType) == PROGRESS_ITEM_TYPES


# ---------------------------------------------------------------------------
# 直接入力
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [Decimal(0), Decimal("37.5"), Decimal(100), 60])
def test_direct_returns_entered_value(value: Decimal | int) -> None:
    """RULE-PRG-001：直接入力は入力した値（0 と 100 の境界を含む）。"""
    assert compute_percent_complete(ProgressMethod.DIRECT, entered_percent=value) == Decimal(value)


def test_direct_without_value_is_not_computed() -> None:
    """RULE-PRG-001：直接入力で値が無ければ未算定（None。4.1 の NULL）。"""
    assert compute_percent_complete("DIRECT") is None
    assert compute_percent_complete("DIRECT", actual_start=START) is None


@pytest.mark.parametrize("value", [Decimal("-0.01"), Decimal("100.01"), Decimal(150)])
def test_percent_out_of_range_rejected(value: Decimal) -> None:
    """RULE-PRG-001：進捗率は 0〜100（PRG-02）。範囲の外は拒む。"""
    with pytest.raises(ProgressError):
        compute_percent_complete(ProgressMethod.DIRECT, entered_percent=value)
    with pytest.raises(ProgressError):
        validate_progress_input(percent_complete=value)


# ---------------------------------------------------------------------------
# 実績終了日があれば 100
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method", list(ProgressMethod))
def test_actual_finish_means_100_for_every_method(method: ProgressMethod) -> None:
    """RULE-PRG-001：実績終了日があれば方式によらず 100（BR-06・PRG-02）。項目が 0 件でも、入力値が 30 でも 100。"""
    assert compute_percent_complete(
        method,
        entered_percent=Decimal(30),
        items=(),
        actual_start=START,
        actual_finish=FINISH,
    ) == Decimal(100)


def test_actual_finish_before_start_rejected() -> None:
    """RULE-PRG-001：実績開始日より前の実績終了日は入力できない（PRG-02）。同じ日は通る。"""
    with pytest.raises(ProgressError):
        compute_percent_complete("ZERO_HUNDRED", actual_start=FINISH, actual_finish=START)
    with pytest.raises(ProgressError):
        validate_progress_input(actual_start=FINISH, actual_finish=START)
    validate_progress_input(actual_start=START, actual_finish=START)


def test_milestone_may_have_actual_finish_only() -> None:
    """RULE-PRG-001：実績終了日だけ（マイルストーンの達成）でも 100 になり、拒まない。"""
    assert compute_percent_complete("DIRECT", actual_finish=FINISH) == Decimal(100)


# ---------------------------------------------------------------------------
# 項目を数える方式
# ---------------------------------------------------------------------------


def test_deliverable_ratio_counts_only_deliverables() -> None:
    """RULE-PRG-001：成果物完了割合＝完了した成果物の項目数 ÷ 成果物の項目数。他の種類の項目は数えない。"""
    all_items = items(ProgressItemType.DELIVERABLE, True, False, False, True) + items(ProgressItemType.CHECK, True)
    assert compute_percent_complete("DELIVERABLE_RATIO", items=all_items) == Decimal(50)


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        ((True, False, False), Decimal("33.33")),  # 1/3（4.1 の Numeric(5,2) の桁へ四捨五入）
        ((True, True, False), Decimal("66.67")),  # 2/3
        ((False, False, False), Decimal(0)),
        ((True, True, True), Decimal(100)),
    ],
)
def test_check_item_ratio(flags: tuple[bool, ...], expected: Decimal) -> None:
    """RULE-PRG-001：検証項目完了割合＝完了した検証項目数 ÷ 検証項目数（0 と 100 の境界を含む）。"""
    assert compute_percent_complete("CHECK_ITEM_RATIO", items=items(ProgressItemType.CHECK, *flags)) == expected


def test_approved_work_ratio_counts_approved_not_done() -> None:
    """RULE-PRG-001：承認済み作業割合＝承認済みの作業項目数 ÷ 作業項目数。完了しても承認が無ければ数えない。"""
    work = [
        ProgressItem(ProgressItemType.WORK, done=True, approved=True),
        ProgressItem(ProgressItemType.WORK, done=True, approved=False),
        ProgressItem(ProgressItemType.WORK, done=False, approved=False),
        ProgressItem(ProgressItemType.WORK, done=False, approved=False),
    ]
    assert compute_percent_complete("APPROVED_WORK_RATIO", items=work) == Decimal(25)


def test_weighted_milestone_uses_weights() -> None:
    """RULE-PRG-001：重み付きマイルストーン＝達成したマイルストーン項目の重みの和 ÷ 重みの総和。"""
    ms = [
        ProgressItem(ProgressItemType.MILESTONE, done=False, weight=Decimal(1)),
        ProgressItem(ProgressItemType.MILESTONE, done=True, weight=Decimal(2)),
        ProgressItem(ProgressItemType.MILESTONE, done=True, weight=Decimal(3)),
        ProgressItem(ProgressItemType.DELIVERABLE, done=False, weight=Decimal(100)),  # 種類が違うので数えない
    ]
    assert compute_percent_complete("WEIGHTED_MILESTONE", items=ms) == Decimal("83.33")  # 5/6


@pytest.mark.parametrize(
    "method",
    [
        ProgressMethod.DELIVERABLE_RATIO,
        ProgressMethod.APPROVED_WORK_RATIO,
        ProgressMethod.CHECK_ITEM_RATIO,
        ProgressMethod.WEIGHTED_MILESTONE,
    ],
)
def test_zero_items_is_not_computed(method: ProgressMethod) -> None:
    """RULE-PRG-001：項目を数える方式で該当する項目が 0 件なら未算定（BR-06）。別の種類の項目だけあっても 0 件。"""
    assert compute_percent_complete(method, items=()) is None
    other = ProgressItemType.CHECK if method is not ProgressMethod.CHECK_ITEM_RATIO else ProgressItemType.WORK
    assert compute_percent_complete(method, items=items(other, True), actual_start=START) is None


def test_weighted_milestone_zero_total_weight_is_not_computed() -> None:
    """RULE-PRG-001：重みの総和が 0 の重み付きマイルストーンは割れないので未算定。負の重みは拒む。"""
    ms = [ProgressItem(ProgressItemType.MILESTONE, done=True, weight=Decimal(0))]
    assert compute_percent_complete("WEIGHTED_MILESTONE", items=ms) is None
    with pytest.raises(ProgressError):
        ProgressItem(ProgressItemType.MILESTONE, weight=Decimal(-1))


def test_unknown_item_type_rejected() -> None:
    """RULE-PRG-001：項目の種類は 4 つだけ。"""
    with pytest.raises(ProgressError):
        ProgressItem("OTHER")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 0/100 法・50/50 法
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("actual_start", "actual_finish", "expected"),
    [(None, None, 0), (START, None, 0), (START, FINISH, 100)],
)
def test_zero_hundred(actual_start: date | None, actual_finish: date | None, expected: int) -> None:
    """RULE-PRG-001：0/100 法は実績終了日があれば 100、無ければ 0（着手していても 0）。"""
    result = compute_percent_complete("ZERO_HUNDRED", actual_start=actual_start, actual_finish=actual_finish)
    assert result == Decimal(expected)


@pytest.mark.parametrize(
    ("actual_start", "actual_finish", "expected"),
    [(None, None, 0), (START, None, 50), (START, FINISH, 100)],
)
def test_fifty_fifty(actual_start: date | None, actual_finish: date | None, expected: int) -> None:
    """RULE-PRG-001：50/50 法は実績終了日があれば 100、実績開始日だけあれば 50、どちらも無ければ 0。"""
    result = compute_percent_complete("FIFTY_FIFTY", actual_start=actual_start, actual_finish=actual_finish)
    assert result == Decimal(expected)


def test_entered_value_ignored_by_other_methods() -> None:
    """RULE-PRG-001：直接入力の値は直接入力の方式だけが使う（他の方式では算定の値が勝つ）。"""
    assert compute_percent_complete("ZERO_HUNDRED", entered_percent=Decimal(70), actual_start=START) == Decimal(0)


def test_unknown_method_rejected() -> None:
    """RULE-PRG-001：方式は 7 つだけ。未定義の方式は推測せずに拒む。"""
    with pytest.raises(ProgressError):
        compute_percent_complete("EARNED_VALUE")


def test_exact_percent_keeps_unrounded_ratio_and_stored_value_is_rounded() -> None:
    """RULE-PRG-001：丸める前の進捗率（5.6）は割り切れない割合をそのまま持ち、保存する値は 4.1 の小数 2 桁へ四捨五入する（設計書 1.1・CR-009）。"""
    two_of_three = items(ProgressItemType.CHECK, True, True, False)
    assert exact_percent_complete("CHECK_ITEM_RATIO", items=two_of_three) == Fraction(200, 3)
    assert compute_percent_complete("CHECK_ITEM_RATIO", items=two_of_three) == Decimal("66.67")
    assert exact_percent_complete("DIRECT", entered_percent=Decimal("12.5")) == Fraction(25, 2)
    assert exact_percent_complete("CHECK_ITEM_RATIO", items=[]) is None
    assert exact_percent_complete("ZERO_HUNDRED", actual_start=START, actual_finish=FINISH) == 100
    # 四捨五入は分数のまま行う（境目ちょうどは切り上げ。近似で境目をまたがない）
    assert round_percent(Fraction(1, 200)) == Decimal("0.01")
    assert round_percent(Fraction(1, 201)) == Decimal("0.00")
    assert round_percent(Fraction(100, 3)) == Decimal("33.33")
    assert round_percent(Decimal("12.345")) == Decimal("12.35")
    assert round_percent(None) is None
    with pytest.raises(ProgressError):
        round_percent(Fraction(-1, 3))
