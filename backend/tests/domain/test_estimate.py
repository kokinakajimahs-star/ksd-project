"""見積のボトムアップ集計・単位換算と記録（app.domain.estimate）の試験。

設計書 4.1（wbs_elements・activities・estimate_records・calendars）・5.1・5.3・5.8、要求仕様書 EST-01・EST-02・EST-08・WBS-02・WBS-08。
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.domain.estimate import (
    MAX_HOURS,
    ActivityEstimate,
    ElementKind,
    EstimateError,
    EstimateMethod,
    EstimateRecord,
    InputUnit,
    RollupSource,
    TargetType,
    WbsNode,
    record_estimate,
    rollup_estimates,
    to_hours,
)

DEL = ElementKind.DELIVERABLE
MGT = ElementKind.MANAGEMENT
CMP = ElementKind.COMPONENT
WP = ElementKind.WORK_PACKAGE

D = Decimal
AT = datetime(2026, 11, 10, 1, 2, 3, tzinfo=UTC)


def node(id_: int, parent: int | None, kind: ElementKind, own: str | None = None) -> WbsNode:
    return WbsNode(id=id_, parent_id=parent, kind=kind, own_estimate_hours=None if own is None else D(own))


def act(id_: int, wp: int, hours: str | None) -> ActivityEstimate:
    return ActivityEstimate(id=id_, work_package_id=wp, estimate_hours=None if hours is None else D(hours))


def sample_tree() -> tuple[list[WbsNode], list[ActivityEstimate]]:
    """1.0 成果物（自分の見積 40）
         1.1 WP（自分の見積 99。活動 A 8・B 16.5 を持つ）
         1.2 WP（自分の見積 10。活動なし）
       2.0 管理領域
         2.1 構成要素
           2.1.1 WP（自分の見積なし。活動なし）
           2.1.2 WP（活動 C 見積なし・D 0.25）
         2.2 構成要素（子なし。自分の見積 5）
    """
    elements = [
        node(1, None, DEL, "40"),
        node(11, 1, WP, "99"),
        node(12, 1, WP, "10"),
        node(2, None, MGT),
        node(21, 2, CMP),
        node(211, 21, WP),
        node(212, 21, WP),
        node(22, 2, CMP, "5"),
    ]
    activities = [act(101, 11, "8"), act(102, 11, "16.5"), act(103, 212, None), act(104, 212, "0.25")]
    return elements, activities


# ---------------------------------------------------------------------------
# RULE-EST-001 ボトムアップ集計
# ---------------------------------------------------------------------------


def test_rollup_sums_activity_to_wp_to_upper_to_project() -> None:
    """RULE-EST-001：活動→WP→上位→プロジェクトの順に合計される（EST-02 の受入基準・5.8）。"""
    elements, activities = sample_tree()
    r = rollup_estimates(elements, activities)

    assert r.hours_of(11) == D("24.5")  # 8 + 16.5
    assert r.hours_of(12) == D("10")  # 活動の無い WP は自分の見積
    assert r.hours_of(1) == D("34.5")  # 子の合計（自分の見積 40 は使わない）
    assert r.hours_of(211) == D("0")
    assert r.hours_of(212) == D("0.25")
    assert r.hours_of(21) == D("0.25")
    assert r.hours_of(22) == D("0")  # 子の無い上位の要素は 0（自分の見積は集計に使わない）
    assert r.hours_of(2) == D("0.25")
    assert r.project_hours == D("34.75")
    assert dict(r.activities) == {101: D("8"), 102: D("16.5"), 103: D("0"), 104: D("0.25")}


def test_rollup_sources_and_lower_totals() -> None:
    """RULE-EST-001：値の出どころと下位の合計（5.3 の「子の集計」）。下位が無ければ下位の合計は None。"""
    elements, activities = sample_tree()
    r = rollup_estimates(elements, activities)

    wp_with_activities = r.elements[11]
    assert wp_with_activities.source is RollupSource.ACTIVITIES
    assert wp_with_activities.lower_hours == D("24.5")
    assert wp_with_activities.own_estimate_hours == D("99")

    wp_without_activities = r.elements[12]
    assert wp_without_activities.source is RollupSource.OWN_ESTIMATE
    assert wp_without_activities.lower_hours is None
    assert wp_without_activities.own_estimate_hours == D("10")

    upper = r.elements[1]
    assert upper.source is RollupSource.CHILDREN
    assert upper.lower_hours == D("34.5")
    assert upper.own_estimate_hours == D("40")

    leaf_upper = r.elements[22]
    assert leaf_upper.source is RollupSource.CHILDREN
    assert leaf_upper.lower_hours is None
    assert leaf_upper.own_estimate_hours == D("5")


def test_wp_without_activities_and_without_own_estimate_is_zero() -> None:
    """RULE-EST-001：活動も自分の見積も無い WP は 0（自分の見積の値を使い、NULL は 0）。"""
    r = rollup_estimates([node(1, None, WP)])
    assert r.hours_of(1) == D("0")
    assert r.elements[1].source is RollupSource.OWN_ESTIMATE
    assert r.project_hours == D("0")


def test_wp_with_activities_without_estimates_does_not_fall_back_to_own_estimate() -> None:
    """RULE-EST-001：活動を 1 つでも持つ WP は活動の合計（見積の無い活動は 0）で、自分の見積に戻らない（5.8）。"""
    r = rollup_estimates([node(1, None, WP, "30")], [act(1, 1, None)])
    assert r.hours_of(1) == D("0")
    assert r.elements[1].source is RollupSource.ACTIVITIES
    assert r.elements[1].lower_hours == D("0")


def test_changing_a_lower_value_changes_every_ancestor_only() -> None:
    """RULE-EST-001：下位を変えると上位の値が変わり、別の枝は変わらない（EST-02 の受入基準）。"""
    elements, activities = sample_tree()
    before = rollup_estimates(elements, activities)
    changed = [a if a.id != 104 else act(104, 212, "2.25") for a in activities]
    after = rollup_estimates(elements, changed)

    for element_id in (212, 21, 2):
        assert after.hours_of(element_id) - before.hours_of(element_id) == D("2")
    assert after.project_hours - before.project_hours == D("2")
    for element_id in (1, 11, 12, 211, 22):
        assert after.hours_of(element_id) == before.hours_of(element_id)


def test_changing_own_estimate_of_wp_without_activities_changes_ancestors() -> None:
    """RULE-EST-001：活動の無い WP の自分の見積を変えると上位の値が変わる。"""
    elements, activities = sample_tree()
    changed = [e if e.id != 12 else node(12, 1, WP, "12.5") for e in elements]
    r = rollup_estimates(changed, activities)
    assert r.hours_of(12) == D("12.5")
    assert r.hours_of(1) == D("37")
    assert r.project_hours == D("37.25")


def test_empty_wbs_project_total_is_zero() -> None:
    """RULE-EST-001：要素が 1 つも無いプロジェクトの合計は 0。"""
    r = rollup_estimates([])
    assert r.project_hours == D("0")
    assert dict(r.elements) == {}
    assert dict(r.activities) == {}


def test_rollup_is_exact_in_decimal() -> None:
    """RULE-EST-001：小数の和に 2 進の誤差を持ち込まない（0.1＋0.2＝0.3）。"""
    r = rollup_estimates([node(1, None, WP)], [act(1, 1, "0.1"), act(2, 1, "0.2")])
    assert r.hours_of(1) == D("0.3")


def test_rollup_result_is_read_only() -> None:
    """RULE-EST-001：結果の表は読むだけ（呼び手が書き換えて上位とずれない）。"""
    r = rollup_estimates([node(1, None, WP, "1")])
    with pytest.raises(TypeError):
        r.elements[1] = r.elements[1]  # type: ignore[index]
    with pytest.raises(KeyError):
        r.hours_of(999)


def test_rollup_handles_deep_tree_without_recursion_limit() -> None:
    """RULE-EST-001：再帰の上限より深い木でも数えられる（任意の深さ。WBS-01）。"""
    depth = sys.getrecursionlimit() + 100
    elements = [node(i, i - 1 if i > 1 else None, CMP) for i in range(1, depth)]
    elements.append(node(depth, depth - 1, WP, "3"))
    r = rollup_estimates(elements)
    assert r.hours_of(1) == D("3")
    assert r.project_hours == D("3")


def test_rollup_accepts_kind_as_string() -> None:
    """RULE-EST-001：区分は DB の値（文字列）のままでも受ける。4 値以外は拒む。"""
    r = rollup_estimates([WbsNode(id=1, parent_id=None, kind="WORK_PACKAGE", own_estimate_hours=D("2"))])  # type: ignore[arg-type]
    assert r.hours_of(1) == D("2")
    with pytest.raises(EstimateError, match="区分"):
        rollup_estimates([WbsNode(id=1, parent_id=None, kind="TASK", own_estimate_hours=None)])  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("elements", "activities", "message"),
    [
        ([node(1, None, WP), node(1, None, WP)], [], "ID が重複"),
        ([node(1, 9, WP)], [], "親 9 が集計の入力に無い"),
        ([node(1, 2, CMP), node(2, 1, CMP)], [], "循環"),
        ([node(1, 1, CMP)], [], "循環"),
        ([node(1, None, DEL), node(2, 3, CMP), node(3, 2, CMP)], [], r"循環.*\[2, 3\]"),
        ([node(1, None, WP), node(2, 1, WP)], [], "ワークパッケージ 1 が子の要素"),
        ([node(1, None, DEL)], [act(1, 1, "1")], "WP でない要素"),
        ([node(1, None, WP)], [act(1, 2, "1")], "WP 2 が集計の入力に無い"),
        ([node(1, None, WP)], [act(1, 1, "1"), act(1, 1, "2")], "活動の ID が重複"),
    ],
)
def test_rollup_rejects_malformed_tree(
    elements: list[WbsNode], activities: list[ActivityEstimate], message: str
) -> None:
    """RULE-EST-001：木になっていない入力・WP の子・WP 以外への活動（WBS-02・WBS-08）は推測せずに拒む。"""
    with pytest.raises(EstimateError, match=message):
        rollup_estimates(elements, activities)


@pytest.mark.parametrize("bad", [D("-0.01"), -1, 1.5, True, D("NaN"), D("Infinity")])
def test_rollup_rejects_invalid_hours(bad: object) -> None:
    """RULE-EST-001：負・float・bool・非数の見積は集計に入れない（活動の見積・要素の自分の見積の両方）。"""
    with pytest.raises(EstimateError):
        rollup_estimates([node(1, None, WP)], [ActivityEstimate(id=1, work_package_id=1, estimate_hours=bad)])  # type: ignore[arg-type]
    with pytest.raises(EstimateError):
        rollup_estimates([WbsNode(id=1, parent_id=None, kind=WP, own_estimate_hours=bad)])  # type: ignore[arg-type]


def test_rollup_accepts_zero_and_int_hours() -> None:
    """RULE-EST-001：0 と整数の見積は受ける（境界：0 は負ではない）。"""
    r = rollup_estimates([node(1, None, WP)], [ActivityEstimate(id=1, work_package_id=1, estimate_hours=0)])  # type: ignore[arg-type]
    assert r.hours_of(1) == D("0")
    r = rollup_estimates([WbsNode(id=1, parent_id=None, kind=WP, own_estimate_hours=7)])  # type: ignore[arg-type]
    assert r.hours_of(1) == D("7")


# ---------------------------------------------------------------------------
# RULE-EST-002 見積の単位換算と記録
# ---------------------------------------------------------------------------


def test_enumerations_match_table_definition() -> None:
    """RULE-EST-002：対象・単位・手法の値は 4.1 の estimate_records の定義のとおり（手法は EST-08 の 5 つ）。"""
    assert [t.value for t in TargetType] == ["ELEMENT", "ACTIVITY"]
    assert [u.value for u in InputUnit] == ["HOUR", "DAY"]
    assert [m.value for m in EstimateMethod] == ["BOTTOM_UP", "ANALOGOUS", "THREE_POINT", "EXPERT", "OTHER"]


@pytest.mark.parametrize(
    ("value", "unit", "per_day", "expected"),
    [
        (D("12.5"), InputUnit.HOUR, D("8"), D("12.50")),  # 人時はそのまま
        (D("2.5"), InputUnit.DAY, D("8"), D("20.00")),  # 人日×1 日の稼働時間（既定 8）
        (D("1.5"), InputUnit.DAY, D("7.5"), D("11.25")),
        (3, "DAY", 8, D("24.00")),  # 整数・文字列の単位も受ける
        (D("0"), InputUnit.DAY, D("8"), D("0.00")),  # 0 は受ける（負ではない）
        (D("1.500"), InputUnit.HOUR, D("8"), D("1.50")),  # 3 桁目が 0 なら小数 2 桁の値
        (D("0.33"), InputUnit.DAY, D("7.75"), D("2.56")),  # 2.5575 → 2.56（四捨五入）
        (D("0.01"), InputUnit.DAY, D("0.5"), D("0.01")),  # 0.005 → 0.01（ちょうど半分は切り上げ）
        (D("0.01"), InputUnit.DAY, D("0.49"), D("0.00")),  # 0.0049 → 0.00
        (D("1"), InputUnit.DAY, D("24"), D("24.00")),  # 1 日の稼働時間の上限 24
        (D("1"), InputUnit.DAY, D("0.01"), D("0.01")),  # 1 日の稼働時間の下限に近い値
        (MAX_HOURS, InputUnit.HOUR, D("8"), D("99999999.99")),  # Numeric(10,2) の上限
    ],
)
def test_to_hours_converts_to_person_hours(value: object, unit: object, per_day: object, expected: Decimal) -> None:
    """RULE-EST-002：人時・人日のどちらでも入力でき、人時（小数 2 桁）へ換算する（EST-01 の受入基準）。"""
    hours = to_hours(value, unit, per_day)  # type: ignore[arg-type]
    assert hours == expected
    assert hours.as_tuple().exponent == -2


@pytest.mark.parametrize(
    ("value", "unit", "per_day", "message"),
    [
        (D("-0.01"), InputUnit.HOUR, D("8"), "負の値"),  # 負の値は入力できない（EST-01）
        (D("-1"), InputUnit.DAY, D("8"), "負の値"),
        (D("1.005"), InputUnit.HOUR, D("8"), "小数 2 桁まで"),
        (D("100000000"), InputUnit.HOUR, D("8"), "大きすぎる"),
        (D("12500000"), InputUnit.DAY, D("8"), "換算した見積が大きすぎる"),  # 12,500,000 人日 × 8 ＝ 1 億人時
        (D("1"), InputUnit.DAY, D("0"), "0 より大きく 24 以下"),
        (D("1"), InputUnit.DAY, D("24.01"), "0 より大きく 24 以下"),
        (D("1"), InputUnit.DAY, D("-8"), "負の値"),
        (D("1"), "WEEK", D("8"), "HOUR・DAY のいずれか"),
        (1.5, InputUnit.HOUR, D("8"), "float"),
        (True, InputUnit.HOUR, D("8"), "float"),
        (D("NaN"), InputUnit.HOUR, D("8"), "数ではない"),
        (None, InputUnit.HOUR, D("8"), "見積の値 が無い"),
        (D("1"), InputUnit.HOUR, None, "1 日の稼働時間 が無い"),
    ],
)
def test_to_hours_rejects_invalid_input(value: object, unit: object, per_day: object, message: str) -> None:
    """RULE-EST-002：負の値・桁あふれ・未定義の単位・不正な 1 日の稼働時間は拒む（推測して丸めない）。"""
    with pytest.raises(EstimateError, match=message):
        to_hours(value, unit, per_day)  # type: ignore[arg-type]


def test_record_estimate_builds_one_record_with_basis() -> None:
    """RULE-EST-002：見積の値と手法・前提・参照資料を 1 件の記録にする（EST-08 の受入基準。4.1 の estimate_records の列）。"""
    rec = record_estimate(
        project_id=7,
        target_type=TargetType.ELEMENT,
        target_id=12,
        input_value=D("2.5"),
        input_unit=InputUnit.DAY,
        hours_per_day=D("7.5"),
        method=EstimateMethod.ANALOGOUS,
        assumptions="前回の類似案件と同じ規模",
        references="案件A の実績表",
        entered_by=3,
        entered_at=AT,
    )
    assert rec == EstimateRecord(
        project_id=7,
        target_type=TargetType.ELEMENT,
        target_id=12,
        hours=D("18.75"),
        input_value=D("2.5"),
        input_unit=InputUnit.DAY,
        method=EstimateMethod.ANALOGOUS,
        assumptions="前回の類似案件と同じ規模",
        references="案件A の実績表",
        entered_by=3,
        entered_at=AT,
    )


def test_record_estimate_keeps_input_value_and_unit_as_entered() -> None:
    """RULE-EST-002：入力の値と単位は入れたまま残し、人時の値は換算したものを持つ（保存は人時。EST-01）。"""
    rec = record_estimate(
        project_id=1,
        target_type="ACTIVITY",
        target_id=5,
        input_value=3,
        input_unit="DAY",
        hours_per_day=8,
        entered_by=1,
        entered_at=AT,
    )
    assert rec.target_type is TargetType.ACTIVITY
    assert rec.input_unit is InputUnit.DAY
    assert rec.input_value == D("3")
    assert isinstance(rec.input_value, Decimal)
    assert rec.hours == D("24.00")


def test_record_estimate_basis_is_optional() -> None:
    """RULE-EST-002：手法・前提・参照資料は省ける（None のまま記録する）。"""
    rec = record_estimate(
        project_id=1,
        target_type=TargetType.ELEMENT,
        target_id=1,
        input_value=D("8"),
        input_unit=InputUnit.HOUR,
        hours_per_day=D("8"),
        entered_by=1,
        entered_at=AT,
    )
    assert rec.method is None
    assert rec.assumptions is None
    assert rec.references is None
    assert rec.hours == D("8.00")


@pytest.mark.parametrize("method", [m.value for m in EstimateMethod])
def test_record_estimate_accepts_each_method_string(method: str) -> None:
    """RULE-EST-002：手法は 5 つのいずれも記録でき、DB の値（文字列）でも受ける。"""
    rec = record_estimate(
        project_id=1,
        target_type=TargetType.ELEMENT,
        target_id=1,
        input_value=D("1"),
        input_unit=InputUnit.HOUR,
        hours_per_day=D("8"),
        method=method,
        entered_by=1,
        entered_at=AT,
    )
    assert rec.method is EstimateMethod(method)


def test_record_estimate_keeps_entered_time_and_offset() -> None:
    """RULE-EST-002：記録の時刻は渡されたものをそのまま使う（ドメインは「今」を求めない）。"""
    jst = timezone(timedelta(hours=9))
    at = datetime(2026, 11, 10, 9, 0, tzinfo=jst)
    rec = record_estimate(
        project_id=1,
        target_type=TargetType.ELEMENT,
        target_id=1,
        input_value=D("1"),
        input_unit=InputUnit.HOUR,
        hours_per_day=D("8"),
        entered_by=1,
        entered_at=at,
    )
    assert rec.entered_at is at


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"input_value": D("-0.5")}, "負の値"),  # 負の値は記録しない（EST-01）
        ({"input_value": D("-1"), "input_unit": InputUnit.DAY}, "負の値"),
        ({"target_type": "PROJECT"}, "ELEMENT・ACTIVITY のいずれか"),
        ({"input_unit": "MONTH"}, "HOUR・DAY のいずれか"),
        ({"method": "GUESS"}, "BOTTOM_UP・ANALOGOUS・THREE_POINT・EXPERT・OTHER のいずれか"),
        ({"assumptions": 123}, "前提は文字列"),
        ({"references": ["a"]}, "参照資料は文字列"),
        ({"entered_at": datetime(2026, 11, 10, 1, 2, 3)}, "aware"),
        ({"hours_per_day": D("0"), "input_unit": InputUnit.DAY}, "0 より大きく 24 以下"),
    ],
)
def test_record_estimate_rejects_invalid_input(overrides: dict[str, object], message: str) -> None:
    """RULE-EST-002：負の値・未定義の対象・単位・手法・時差の無い時刻は記録にしない。"""
    args: dict[str, object] = {
        "project_id": 1,
        "target_type": TargetType.ELEMENT,
        "target_id": 1,
        "input_value": D("1"),
        "input_unit": InputUnit.HOUR,
        "hours_per_day": D("8"),
        "entered_by": 1,
        "entered_at": AT,
    }
    args.update(overrides)
    with pytest.raises(EstimateError, match=message):
        record_estimate(**args)  # type: ignore[arg-type]


def test_hours_per_day_is_validated_even_for_hour_unit() -> None:
    """RULE-EST-002：1 日の稼働時間は人時の入力でも確かめる（カレンダーの定義の誤りを黙って通さない）。"""
    with pytest.raises(EstimateError, match="0 より大きく 24 以下"):
        to_hours(D("1"), InputUnit.HOUR, D("25"))
