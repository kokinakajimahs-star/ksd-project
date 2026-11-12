"""変更内容の適用と影響分析（app.domain.change）の試験。

設計書 5.1・5.9（RULE-CHG-001・002）・5.2・5.5、要求仕様書 CHG-01・02・04。
2026-12 の曜日：12/01＝火、12/04＝金、12/07＝月。稼働日の軸（原点 12/01）の添字：
0=12/01 1=12/02 2=12/03 3=12/04 4=12/07 5=12/08 6=12/09 7=12/10 8=12/11 9=12/14 10=12/15 11=12/16。

基準の計画：1.0（成果物）の下に WP 1.1・1.2。活動 10「設計」（1.1・3 日・24h）→ 11「実装」（1.2・5 日・40h）
→ 12「完了」（1.2・マイルストーン）。日程：10 は 12/01〜12/03、11 は 12/04〜12/10、12 は 12/10。終了日 12/10、総見積 64h。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from app.domain.calendar import WorkCalendar
from app.domain.change import (
    ActivityLabel,
    ChangeError,
    ChangeErrorCode,
    ElementLabel,
    PlanActivity,
    PlanElement,
    PlanModel,
    analyze_impact,
    apply_operations,
)
from app.domain.estimate import ElementKind
from app.domain.scheduling import ScheduleCycleError
from app.domain.wbs_code import CodeHistoryEntry

CAL = WorkCalendar()
START = date(2026, 12, 1)
DEL, WP = ElementKind.DELIVERABLE, ElementKind.WORK_PACKAGE

PLAN = PlanModel(
    elements=(
        PlanElement(id=1, parent_id=None, kind=DEL, name="システム", wbs_code="1.0", sibling_seq=1, last_child_seq=2,
                    sort_order=1),
        PlanElement(id=2, parent_id=1, kind=WP, name="設計WP", wbs_code="1.1", sibling_seq=1, sort_order=1),
        PlanElement(id=3, parent_id=1, kind=WP, name="実装WP", wbs_code="1.2", sibling_seq=2, sort_order=2),
    ),
    activities=(
        PlanActivity(id=10, work_package_id=2, name="設計", duration_days=3, estimate_hours=Decimal(24), sort_order=1),
        PlanActivity(id=11, work_package_id=3, name="実装", duration_days=5, estimate_hours=Decimal(40), sort_order=1),
        PlanActivity(id=12, work_package_id=3, name="完了", duration_days=0, is_milestone=True, sort_order=2),
    ),
    dependencies=((10, 11), (11, 12)),
    top_last_child_seq=1,
)


def apply(*operations: dict[str, Any], plan: PlanModel = PLAN):  # type: ignore[no-untyped-def]
    return apply_operations(plan, list(operations))


def rejected(*operations: dict[str, Any], plan: PlanModel = PLAN) -> ChangeError:
    with pytest.raises(ChangeError) as excinfo:
        apply_operations(plan, list(operations))
    return excinfo.value


def elements_of(model: PlanModel) -> dict[int, PlanElement]:
    return {e.id: e for e in model.elements}


def activities_of(model: PlanModel) -> dict[int, PlanActivity]:
    return {a.id: a for a in model.activities}


def impact(*operations: dict[str, Any], plan: PlanModel = PLAN):  # type: ignore[no-untyped-def]
    return analyze_impact(plan, list(operations), calendar=CAL, project_start=START)


# ---------------------------------------------------------------------------
# RULE-CHG-001 変更内容の適用
# ---------------------------------------------------------------------------


def test_add_element_numbers_under_parent_and_updates_last_child_seq() -> None:
    """RULE-CHG-001：add_element は parent_code の下に RULE-WBS-002 の「新規」の ID（親の last_child_seq＋1）で要素を足す。"""
    result = apply({"op": "add_element", "parent_code": "1.0", "kind": "WORK_PACKAGE", "name": "試験WP", "ref": "t"})
    new_id = result.element_refs["t"]
    assert new_id < 0  # 仮の ID は負（DB の ID と重ならない）
    added = elements_of(result.plan)[new_id]
    assert (added.wbs_code, added.sibling_seq, added.parent_id, added.kind) == ("1.3", 3, 1, WP)
    assert added.sort_order == 3  # 兄弟の表示順の最大＋1
    assert elements_of(result.plan)[1].last_child_seq == 3


def test_add_element_does_not_reuse_deleted_numbers() -> None:
    """RULE-CHG-001：削除で空いた番号は再利用しない（last_child_seq が 3 なら 1.3 を消した後でも次は 1.4。BR-02）。"""
    plan = replace(PLAN, elements=(replace(PLAN.elements[0], last_child_seq=3), *PLAN.elements[1:]))
    result = apply({"op": "add_element", "parent_code": "1.0", "kind": "COMPONENT", "name": "x", "ref": "x"}, plan=plan)
    assert elements_of(result.plan)[result.element_refs["x"]].wbs_code == "1.4"


@pytest.mark.parametrize("root", ["", None])
def test_add_element_at_root_uses_top_last_child_seq(root: str | None) -> None:
    """RULE-CHG-001：parent_code が空ならルート（第 1 階層）。最上位の数え＋1 で 2.0（5.2 の第 1 階層の補完）。"""
    result = apply({"op": "add_element", "parent_code": root, "kind": "MANAGEMENT", "name": "管理", "ref": "m"})
    assert elements_of(result.plan)[result.element_refs["m"]].wbs_code == "2.0"
    assert result.plan.top_last_child_seq == 2


def test_add_element_at_root_without_top_count_fails_fast() -> None:
    """RULE-CHG-001：最上位の数え（OQ-16）が渡されていなければ推測せずに止める。"""
    error = rejected(
        {"op": "add_element", "parent_code": "", "kind": "MANAGEMENT", "name": "管理", "ref": "m"},
        plan=replace(PLAN, top_last_child_seq=None),
    )
    assert (error.code, error.index) == (ChangeErrorCode.VALIDATION_ERROR, 0)


def test_operations_apply_in_order_and_refs_resolve() -> None:
    """RULE-CHG-001：操作は順に当て、前の操作が作った ref（要素の wp_ref・活動の依存の端）を後の操作から引ける。"""
    result = apply(
        {"op": "add_element", "parent_code": "1.0", "kind": "WORK_PACKAGE", "name": "試験WP", "ref": "wp"},
        {"op": "add_activity", "wp_ref": "wp", "name": "試験", "duration_days": 2, "estimate_hours": 16, "ref": "t"},
        {"op": "add_activity", "wp_code": "1.3", "name": "出荷", "duration_days": 0, "estimate_hours": None,
         "ref": "ship", "requested_start": "2026-12-20"},
        {"op": "add_dependency", "predecessor": 11, "successor": "t"},
        {"op": "add_dependency", "predecessor": "t", "successor": "ship"},
    )
    wp_id, t_id, ship_id = result.element_refs["wp"], result.activity_refs["t"], result.activity_refs["ship"]
    acts = activities_of(result.plan)
    assert acts[t_id].work_package_id == wp_id and acts[t_id].estimate_hours == Decimal(16)
    assert acts[t_id].is_milestone is False
    assert acts[ship_id].is_milestone is True and acts[ship_id].requested_start == date(2026, 12, 20)
    assert acts[ship_id].sort_order == 2  # WP の中の表示順の最大＋1
    assert result.plan.dependencies == ((10, 11), (11, 12), (11, t_id), (t_id, ship_id))
    assert result.ref_of(t_id) == "t" and result.ref_of(10) is None


def test_update_element_changes_only_given_fields() -> None:
    """RULE-CHG-001：update_element は fields（name・kind・own_estimate_hours・責任者）だけを変える。"""
    result = apply(
        {"op": "update_element", "code": "1.1",
         "fields": {"name": "基本設計", "own_estimate_hours": "10.5", "responsible_user_id": 7}},
    )
    element = elements_of(result.plan)[2]
    assert (element.name, element.own_estimate_hours, element.responsible_user_id) == ("基本設計", Decimal("10.5"), 7)
    assert (element.kind, element.wbs_code) == (WP, "1.1")


def test_old_wbs_code_resolves_through_history() -> None:
    """RULE-CHG-001：WBS ID は今の ID、無ければ新旧対応の旧 ID で解決する（RULE-WBS-002・BR-02）。"""
    history = (CodeHistoryEntry(id=1, element_id=3, old_code="1.9", new_code="1.2",
                                renumbered_at=datetime(2026, 11, 1, tzinfo=UTC)),)
    result = apply({"op": "update_element", "code": "1.9", "fields": {"name": "実装（旧ID）"}},
                   plan=replace(PLAN, code_history=history))
    assert elements_of(result.plan)[3].name == "実装（旧ID）"


def test_remove_element_removes_subtree_activities_and_dependencies() -> None:
    """RULE-CHG-001：remove_element は要素を配下ごと消し、その活動と活動の依存も消す。"""
    result = apply({"op": "remove_element", "code": "1.2"})
    assert set(elements_of(result.plan)) == {1, 2}
    assert set(activities_of(result.plan)) == {10}
    assert result.plan.dependencies == ()
    removed_root = apply({"op": "remove_element", "code": "1.0"})
    assert removed_root.plan.elements == () and removed_root.plan.activities == ()


def test_update_and_remove_activity() -> None:
    """RULE-CHG-001：update_activity は fields を変え（期間 0 ならマイルストーン）、remove_activity は依存も消す。"""
    result = apply(
        {"op": "update_activity", "activity_id": 12, "fields": {"duration_days": 2, "name": "検収"}},
        {"op": "update_activity", "activity_id": 10, "fields": {"duration_days": 0, "requested_start": None}},
        {"op": "remove_activity", "activity_id": 11},
    )
    acts = activities_of(result.plan)
    assert (acts[12].duration_days, acts[12].is_milestone, acts[12].name) == (2, False, "検収")
    assert (acts[10].duration_days, acts[10].is_milestone) == (0, True)
    assert 11 not in acts and result.plan.dependencies == ()


def test_remove_dependency() -> None:
    """RULE-CHG-001：remove_dependency は指定の組だけを消す。無ければ DEPENDENCY_NOT_FOUND。"""
    assert apply({"op": "remove_dependency", "predecessor": 10, "successor": 11}).plan.dependencies == ((11, 12),)
    error = rejected({"op": "remove_dependency", "predecessor": 10, "successor": 12})
    assert error.code is ChangeErrorCode.DEPENDENCY_NOT_FOUND


def test_input_plan_is_not_modified() -> None:
    """RULE-CHG-001：当てるのは写し。入力の計画は変わらない（途中で止まった場合も）。"""
    snapshot = replace(PLAN)
    apply({"op": "remove_element", "code": "1.2"}, {"op": "add_element", "parent_code": "1.0", "kind": "COMPONENT",
                                                     "name": "x", "ref": "x"})
    rejected({"op": "remove_activity", "activity_id": 10}, {"op": "remove_activity", "activity_id": 999})
    assert PLAN == snapshot


@pytest.mark.parametrize(
    ("operations", "index"),
    [
        ([{"op": "update_element", "code": "9.9", "fields": {"name": "x"}}], 0),
        ([{"op": "remove_element", "code": "1.1"}, {"op": "remove_element", "code": "1.1"}], 1),
        ([{"op": "add_activity", "wp_ref": "none", "name": "x", "duration_days": 1, "estimate_hours": 1, "ref": "a"}], 0),
        ([{"op": "update_activity", "activity_id": 99, "fields": {"name": "x"}}], 0),
        ([{"op": "remove_activity", "activity_id": 99}], 0),
        ([{"op": "add_dependency", "predecessor": 10, "successor": "nope"}], 0),
        ([{"op": "add_element", "parent_code": "1.0", "kind": "WORK_PACKAGE", "name": "x", "ref": "w"},
          {"op": "remove_element", "code": "1.3"},
          {"op": "add_activity", "wp_ref": "w", "name": "x", "duration_days": 1, "estimate_hours": 1, "ref": "a"}], 2),
    ],
)
def test_unresolved_references_are_rejected(operations: list[dict[str, Any]], index: int) -> None:
    """RULE-CHG-001：WBS ID・仮の参照名・活動の ID が解決できなければ REFERENCE_NOT_FOUND（どの操作かを index で返す）。"""
    error = rejected(*operations)
    assert (error.code, error.index) == (ChangeErrorCode.REFERENCE_NOT_FOUND, index)


def test_duplicate_ref_is_rejected() -> None:
    """RULE-CHG-001：同じ仮の参照名を 2 回作れば DUPLICATE_REF（要素と活動で名前を共有する）。"""
    error = rejected(
        {"op": "add_element", "parent_code": "1.0", "kind": "WORK_PACKAGE", "name": "x", "ref": "same"},
        {"op": "add_activity", "wp_code": "1.1", "name": "y", "duration_days": 1, "estimate_hours": 1, "ref": "same"},
    )
    assert (error.code, error.index) == (ChangeErrorCode.DUPLICATE_REF, 1)


@pytest.mark.parametrize(
    ("operation", "code"),
    [
        ({"op": "add_element", "parent_code": "1.1", "kind": "COMPONENT", "name": "x", "ref": "x"},
         ChangeErrorCode.WORK_PACKAGE_HAS_CHILDREN),
        ({"op": "update_element", "code": "1.0", "fields": {"kind": "WORK_PACKAGE"}},
         ChangeErrorCode.WORK_PACKAGE_HAS_CHILDREN),
        ({"op": "update_element", "code": "1.1", "fields": {"kind": "COMPONENT"}},
         ChangeErrorCode.ACTIVITY_NOT_ON_WORK_PACKAGE),
        ({"op": "add_activity", "wp_code": "1.0", "name": "x", "duration_days": 1, "estimate_hours": 1, "ref": "a"},
         ChangeErrorCode.ACTIVITY_NOT_ON_WORK_PACKAGE),
    ],
)
def test_hierarchy_rules_are_checked(operation: dict[str, Any], code: ChangeErrorCode) -> None:
    """RULE-CHG-001：WP は子を持たない・活動は WP にだけ置ける（RULE-WBS-003）を操作ごとに検査する。"""
    assert rejected(operation).code is code


def test_dependency_cycle_is_rejected_with_cycle_members() -> None:
    """RULE-CHG-001：依存を足すと循環になるなら SCHEDULE_CYCLE。details は循環の活動（後続→…→先行。RULE-SCH-001）。"""
    error = rejected({"op": "add_dependency", "predecessor": 12, "successor": 10})
    assert (error.code, error.details) == (ChangeErrorCode.SCHEDULE_CYCLE, (10, 11, 12))
    self_loop = rejected({"op": "add_dependency", "predecessor": 10, "successor": 10})
    assert (self_loop.code, self_loop.details) == (ChangeErrorCode.SCHEDULE_CYCLE, (10,))


def test_duplicate_dependency_is_rejected() -> None:
    """RULE-CHG-001：同じ組の依存は足せない（4.1 の組の一意）。"""
    assert rejected({"op": "add_dependency", "predecessor": 10, "successor": 11}).code is ChangeErrorCode.DUPLICATE_DEPENDENCY


def test_name_length_boundary() -> None:
    """RULE-CHG-001：名称は 200 文字まで（4.1 の列の長さ）。201 文字は VALIDATION_ERROR。"""
    ok = apply({"op": "update_element", "code": "1.1", "fields": {"name": "あ" * 200}})
    assert elements_of(ok.plan)[2].name == "あ" * 200
    assert rejected({"op": "update_element", "code": "1.1", "fields": {"name": "あ" * 201}}).code is (
        ChangeErrorCode.VALIDATION_ERROR
    )


def test_hours_from_json_numbers() -> None:
    """RULE-CHG-001：工数は JSON の数（int・小数）と文字列を受け、2 進の誤差を持ち込まずに Decimal にする。0 は境界として通す。"""
    result = apply(
        {"op": "update_activity", "activity_id": 10, "fields": {"estimate_hours": 12.5}},
        {"op": "update_activity", "activity_id": 11, "fields": {"estimate_hours": 0}},
        {"op": "update_activity", "activity_id": 12, "fields": {"estimate_hours": "0.01"}},
    )
    acts = activities_of(result.plan)
    assert (acts[10].estimate_hours, acts[11].estimate_hours, acts[12].estimate_hours) == (
        Decimal("12.5"), Decimal(0), Decimal("0.01"))


@pytest.mark.parametrize(
    "operation",
    [
        {"op": "rename_element", "code": "1.1"},
        {"op": ["add_element"]},
        {"code": "1.1"},
        {"op": "remove_element"},
        {"op": "remove_element", "code": "1.1", "extra": 1},
        {"op": "add_activity", "name": "x", "duration_days": 1, "estimate_hours": 1, "ref": "a"},
        {"op": "add_activity", "wp_code": "1.1", "wp_ref": "w", "name": "x", "duration_days": 1, "estimate_hours": 1,
         "ref": "a"},
        {"op": "add_activity", "wp_code": "1.1", "name": "x", "duration_days": -1, "estimate_hours": 1, "ref": "a"},
        {"op": "add_activity", "wp_code": "1.1", "name": "x", "duration_days": 1.5, "estimate_hours": 1, "ref": "a"},
        {"op": "add_activity", "wp_code": "1.1", "name": "x", "duration_days": True, "estimate_hours": 1, "ref": "a"},
        {"op": "add_activity", "wp_code": "1.1", "name": "x", "duration_days": 1, "estimate_hours": -0.01, "ref": "a"},
        {"op": "add_activity", "wp_code": "1.1", "name": "x", "duration_days": 1, "estimate_hours": "1.005", "ref": "a"},
        {"op": "add_activity", "wp_code": "1.1", "name": "x", "duration_days": 1, "estimate_hours": "abc", "ref": "a"},
        {"op": "add_activity", "wp_code": "1.1", "name": "x", "duration_days": 1, "estimate_hours": 1, "ref": "a",
         "requested_start": "2026/12/01"},
        {"op": "add_activity", "wp_code": "1.1", "name": " ", "duration_days": 1, "estimate_hours": 1, "ref": "a"},
        {"op": "add_activity", "wp_code": "1.1", "name": "x", "duration_days": 1, "estimate_hours": 1, "ref": ""},
        {"op": "add_element", "parent_code": "1.0", "kind": "PHASE", "name": "x", "ref": "x"},
        {"op": "update_element", "code": "1.1", "fields": {}},
        {"op": "update_element", "code": "1.1", "fields": {"wbs_code": "9.0"}},
        {"op": "update_element", "code": "1.1", "fields": {"responsible_user_id": 0}},
        {"op": "update_activity", "activity_id": "10", "fields": {"name": "x"}},
        {"op": "update_activity", "activity_id": 10, "fields": {"is_milestone": True}},
        {"op": "add_dependency", "predecessor": -1, "successor": 11},
    ],
)
def test_malformed_operations_are_validation_errors(operation: dict[str, Any]) -> None:
    """RULE-CHG-001：5.9 の形に合わない操作（未知の op・必須の欠け・無い項目・wp_code と wp_ref の両方／どちらも無し・
    負や小数の期間・負や 3 桁の工数・日付の形・空の名称・未知の区分・変えられない項目）は VALIDATION_ERROR。"""
    error = rejected(operation)
    assert (error.code, error.index) == (ChangeErrorCode.VALIDATION_ERROR, 0)


def test_operations_must_be_a_list_of_objects() -> None:
    """RULE-CHG-001：operations は操作のオブジェクトの配列。"""
    with pytest.raises(ChangeError):
        apply_operations(PLAN, "add_element")  # type: ignore[arg-type]
    assert rejected("remove_element").code is ChangeErrorCode.VALIDATION_ERROR  # type: ignore[arg-type]


def test_empty_operations_return_same_plan() -> None:
    """RULE-CHG-001：操作が無ければ計画は変わらない。"""
    result = apply()
    assert result.plan == PLAN and dict(result.element_refs) == {} and dict(result.activity_refs) == {}


# ---------------------------------------------------------------------------
# RULE-CHG-002 影響分析
# ---------------------------------------------------------------------------


def test_impact_of_lengthening_and_adding_on_critical_path() -> None:
    """RULE-CHG-002：実装を 7 日に延ばし、試験 WP と活動（2 日）を実装→試験→完了の間に足すと、
    終了日は 12/10 → 12/16（4 稼働日のずれ）、マイルストーン「完了」も 4 日ずれ、試験がクリティカルパスに加わり、
    総見積は 64 → 80h、要素は 1.3 の追加（5.9 の形）。"""
    result = impact(
        {"op": "update_activity", "activity_id": 11, "fields": {"duration_days": 7}},
        {"op": "add_element", "parent_code": "1.0", "kind": "WORK_PACKAGE", "name": "試験WP", "ref": "wp"},
        {"op": "add_activity", "wp_ref": "wp", "name": "試験", "duration_days": 2, "estimate_hours": 16, "ref": "t"},
        {"op": "add_dependency", "predecessor": 11, "successor": "t"},
        {"op": "add_dependency", "predecessor": "t", "successor": 12},
    )
    assert (result.project_finish.before, result.project_finish.after, result.project_finish.shift_days) == (
        date(2026, 12, 10), date(2026, 12, 16), 4)
    assert [(m.activity_id, m.name, m.before, m.after, m.shift_days) for m in result.milestones] == [
        (12, "完了", date(2026, 12, 10), date(2026, 12, 16), 4)]
    assert result.critical_path.added == (ActivityLabel(activity_id=None, ref="t", name="試験"),)
    assert result.critical_path.removed == ()
    assert (result.total_estimate_hours.before, result.total_estimate_hours.after) == (Decimal(64), Decimal(80))
    assert result.total_estimate_hours.difference == Decimal(16)
    assert result.elements.added == (ElementLabel(element_id=None, ref="wp", wbs_code="1.3", name="試験WP", kind=WP),)
    assert result.elements.updated == () and result.elements.removed == ()


def test_impact_finish_moves_earlier_and_activity_leaves_critical_path() -> None:
    """RULE-CHG-002：依存 設計→実装 を消すと実装が 12/01 に始まり、終了日は 12/10 → 12/07（−3 稼働日。前へずれたら負）、
    設計はフロートができてクリティカルパスから外れる。"""
    result = impact({"op": "remove_dependency", "predecessor": 10, "successor": 11})
    assert (result.project_finish.after, result.project_finish.shift_days) == (date(2026, 12, 7), -3)
    assert [(m.activity_id, m.shift_days) for m in result.milestones] == [(12, -3)]
    assert result.critical_path.added == ()
    assert result.critical_path.removed == (ActivityLabel(activity_id=10, ref=None, name="設計"),)
    assert result.total_estimate_hours.after == Decimal(64)


def test_impact_reports_updated_and_removed_elements() -> None:
    """RULE-CHG-002：elements は変わった要素（変わった項目の前・後）と、消した要素（配下を含む WBS の並び）を返す。
    活動を持つ WP の自分の見積は総見積に入らない（RULE-EST-001）。"""
    result = impact(
        {"op": "update_element", "code": "1.1", "fields": {"name": "基本設計", "own_estimate_hours": 10,
                                                            "responsible_user_id": None}},
        {"op": "remove_element", "code": "1.2"},
    )
    (updated,) = result.elements.updated
    assert (updated.element_id, updated.wbs_code, updated.name) == (2, "1.1", "基本設計")
    assert dict(updated.changes) == {"name": ("設計WP", "基本設計"), "own_estimate_hours": (None, Decimal(10))}
    assert result.elements.removed == (ElementLabel(element_id=3, ref=None, wbs_code="1.2", name="実装WP", kind=WP),)
    assert (result.total_estimate_hours.before, result.total_estimate_hours.after) == (Decimal(64), Decimal(24))
    assert (result.project_finish.after, result.project_finish.shift_days) == (date(2026, 12, 3), -5)
    assert result.milestones == ()  # 消したマイルストーンは「ずれたもの」ではない
    assert [a.activity_id for a in result.critical_path.removed] == [11, 12]


def test_impact_does_not_write_to_the_plan() -> None:
    """RULE-CHG-002：本計画には書き込まない（入力の計画は分析の前後で同じ）。変更が無ければ差も無い。"""
    snapshot = replace(PLAN)
    result = impact({"op": "remove_element", "code": "1.2"})
    assert PLAN == snapshot and result.applied.before == PLAN
    unchanged = impact()
    assert unchanged.project_finish.shift_days == 0 and unchanged.milestones == ()
    assert unchanged.critical_path.added == unchanged.critical_path.removed == ()
    assert unchanged.elements.added == unchanged.elements.updated == unchanged.elements.removed == ()


def test_impact_with_no_activities_has_no_finish_date() -> None:
    """RULE-CHG-002：活動が 1 つも無い計画は終了日もずれも None（推測しない）。"""
    result = impact({"op": "remove_element", "code": "1.0"})
    assert (result.project_finish.before, result.project_finish.after, result.project_finish.shift_days) == (
        date(2026, 12, 10), None, None)
    assert result.total_estimate_hours.after == Decimal(0)


def test_impact_propagates_operation_errors_and_cycles_in_current_plan() -> None:
    """RULE-CHG-002：当てられない変更は RULE-CHG-001 の ChangeError、今の計画に循環があれば ScheduleCycleError。"""
    with pytest.raises(ChangeError):
        impact({"op": "remove_activity", "activity_id": 404})
    cyclic = replace(PLAN, dependencies=((10, 11), (11, 10)))
    with pytest.raises(ScheduleCycleError):
        impact(plan=cyclic)


def test_impact_json_shape() -> None:
    """RULE-CHG-002：change_requests.impact に保存する JSON は 5.9 の 5 つの項目を持ち、日付は ISO、工数は文字列。"""
    result = impact(
        {"op": "update_activity", "activity_id": 11, "fields": {"duration_days": 7}},
        {"op": "update_element", "code": "1.1", "fields": {"kind": "WORK_PACKAGE", "own_estimate_hours": "2.5"}},
    )
    data = result.to_json()
    assert set(data) == {"project_finish", "milestones", "critical_path", "total_estimate_hours", "elements"}
    assert data["project_finish"] == {"before": "2026-12-10", "after": "2026-12-14", "shift_days": 2}
    assert data["milestones"] == [
        {"activity_id": 12, "name": "完了", "before": "2026-12-10", "after": "2026-12-14", "shift_days": 2}]
    assert data["critical_path"] == {"added": [], "removed": []}
    assert data["total_estimate_hours"] == {"before": "64.00", "after": "64.00"}
    assert data["elements"]["updated"] == [
        {"element_id": 2, "wbs_code": "1.1", "name": "設計WP",
         "changes": {"own_estimate_hours": {"before": None, "after": "2.50"}}}]
