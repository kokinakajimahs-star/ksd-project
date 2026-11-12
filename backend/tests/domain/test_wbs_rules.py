"""WBS の判定ルール（app.domain.wbs_rules）の試験。

設計書 4.1（wbs_elements・wbs_dictionary_entries・wbs_scope_links・scope_items・assignments ほか）・5.1・5.3、
要求仕様書 WBS-01・02・07・08・10・11・13・16、PRJ-02、BR-03。
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.domain.estimate import ActivityEstimate, ElementKind, EstimateError, WbsNode
from app.domain.wbs_code import CodedElement, CodeHistoryEntry, ResolvedBy
from app.domain.wbs_rules import (
    ActivityRef,
    AssignmentRef,
    ChangeRequestRef,
    CompletionField,
    CorrectiveActionRef,
    DictionaryRef,
    EstimateRecordRef,
    EstimateTarget,
    FindingCode,
    FindingLevel,
    HierarchyIssueCode,
    ResponsibilityElement,
    ScopeItemRef,
    ScopeKind,
    ScopeLinkRef,
    TimeEntryRef,
    WbsRuleError,
    build_responsibility_matrix,
    check_hierarchy,
    check_hundred_percent,
    count_errors,
    ensure_hierarchy,
    find_missing_completion,
    trace_wbs_code,
)

DEL = ElementKind.DELIVERABLE
MGT = ElementKind.MANAGEMENT
CMP = ElementKind.COMPONENT
WP = ElementKind.WORK_PACKAGE
D = Decimal
T0 = datetime(2026, 11, 10, 1, 0, tzinfo=UTC)


def node(id_: int, parent: int | None, kind: ElementKind, own: str | None = None) -> WbsNode:
    return WbsNode(id=id_, parent_id=parent, kind=kind, own_estimate_hours=None if own is None else D(own))


def act(id_: int, wp: int, hours: str | None = None) -> ActivityEstimate:
    return ActivityEstimate(id=id_, work_package_id=wp, estimate_hours=None if hours is None else D(hours))


# ---------------------------------------------------------------------------
# RULE-WBS-003 階層と区分の整合
# ---------------------------------------------------------------------------


def test_consistent_tree_has_no_issues() -> None:
    """RULE-WBS-003：区分が 4 値・WP は葉・活動は WP の上・循環なし の木は不整合 0 件。枝ごとに深さが違ってよい（WBS-01）。"""
    elements = [
        node(1, None, DEL),
        node(11, 1, WP),
        node(2, None, MGT),
        node(21, 2, CMP),
        node(211, 21, CMP),
        node(2111, 211, WP),
    ]
    assert check_hierarchy(elements, [ActivityRef(100, 11), ActivityRef(101, 2111)]) == ()
    ensure_hierarchy(elements)


def test_work_package_cannot_have_children() -> None:
    """RULE-WBS-003：WP は子を持てない（WBS-02 の受入基準「子を作るとエラー」）。"""
    issues = check_hierarchy([node(1, None, DEL), node(11, 1, WP), node(111, 11, CMP), node(112, 11, WP)])
    assert [(i.code, i.element_ids) for i in issues] == [(HierarchyIssueCode.WORK_PACKAGE_HAS_CHILDREN, (11, 111, 112))]
    with pytest.raises(WbsRuleError) as excinfo:
        ensure_hierarchy([node(11, None, WP), node(111, 11, WP)])
    assert excinfo.value.code == "WORK_PACKAGE_HAS_CHILDREN"


def test_kind_must_be_one_of_four_values() -> None:
    """RULE-WBS-003：区分は 4 値のいずれか（WBS-02 の受入基準）。4 値は全て受ける。"""
    assert check_hierarchy([node(i, None, kind) for i, kind in enumerate(ElementKind, start=1)]) == ()
    issues = check_hierarchy([WbsNode(id=1, parent_id=None, kind="TASK")])  # type: ignore[arg-type]
    assert [(i.code, i.element_ids) for i in issues] == [(HierarchyIssueCode.INVALID_KIND, (1,))]


def test_activity_only_on_work_package() -> None:
    """RULE-WBS-003：活動は WP にだけ置ける（WBS-08・SCH-01）。WP 以外・入力に無い要素の上の活動を挙げる。"""
    elements = [node(1, None, DEL), node(11, 1, WP)]
    issues = check_hierarchy(elements, [ActivityRef(100, 11), ActivityRef(101, 1), ActivityRef(102, 99)])
    assert [(i.code, i.element_ids, i.activity_id) for i in issues] == [
        (HierarchyIssueCode.ACTIVITY_NOT_ON_WORK_PACKAGE, (1,), 101),
        (HierarchyIssueCode.ACTIVITY_NOT_ON_WORK_PACKAGE, (99,), 102),
    ]


def test_parent_cycle_is_detected() -> None:
    """RULE-WBS-003：親子の循環を作らない。移動した後の木（2 を 21 の下へ）を渡すと、輪をなす要素を挙げる。自分を親にする要素も循環。"""
    moved = [node(1, None, DEL), node(2, 21, MGT), node(21, 2, CMP), node(211, 21, WP), node(3, 3, CMP)]
    issues = check_hierarchy(moved)
    assert [(i.code, i.element_ids) for i in issues] == [
        (HierarchyIssueCode.PARENT_CYCLE, (2, 21)),
        (HierarchyIssueCode.PARENT_CYCLE, (3,)),
    ]
    with pytest.raises(WbsRuleError) as excinfo:
        ensure_hierarchy(moved)
    assert excinfo.value.code == "PARENT_CYCLE"
    assert len(excinfo.value.details) == 2


def test_input_errors_duplicate_and_missing_parent() -> None:
    """RULE-WBS-003：要素の ID の重複と、入力に無い親を挙げる。"""
    issues = check_hierarchy([node(1, None, DEL), node(1, None, MGT), node(2, 9, WP)])
    assert [(i.code, i.element_ids) for i in issues] == [
        (HierarchyIssueCode.DUPLICATE_ELEMENT, (1,)),
        (HierarchyIssueCode.PARENT_NOT_FOUND, (2,)),
    ]


# ---------------------------------------------------------------------------
# RULE-WBS-004 100%ルールの検査
# ---------------------------------------------------------------------------


def test_sum_mismatch_uses_rollup_and_tolerance_boundary() -> None:
    """RULE-WBS-004：子の集計（RULE-EST-001）と自分の見積の差の割合が許容幅を超えたら SUM_MISMATCH（WARNING）。ちょうど許容幅は出さない（5.3・BR-03）。"""
    # 1.0（自分 100）の子：1.1 WP（活動 60）・1.2 WP（自分 50・活動なし）→ 子の集計 110。差の割合 0.10
    elements = [node(1, None, DEL, "100"), node(11, 1, WP), node(12, 1, WP, "50")]
    activities = [act(101, 11, "60")]
    over = check_hundred_percent(elements, activities, [], [], tolerance=D("0.09"))
    assert [(f.level, f.code, f.element_id, f.own_estimate_hours, f.lower_hours) for f in over] == [
        (FindingLevel.WARNING, FindingCode.SUM_MISMATCH, 1, D("100"), D("110"))
    ]
    assert check_hundred_percent(elements, activities, [], [], tolerance=D("0.10")) == ()
    # 既定の許容幅 0%（4.2）：わずかな差も警告する
    assert [f.element_id for f in check_hundred_percent(elements, [act(101, 11, "50.01")], [], [])] == [1]
    assert check_hundred_percent(elements, [act(101, 11, "50")], [], []) == ()


def test_sum_mismatch_for_work_package_with_activities() -> None:
    """RULE-WBS-004：WP では活動が子。活動の見積の合計と WP の自分の見積を比べる。活動の無い WP・自分の見積の無い要素は見ない。"""
    elements = [node(1, None, DEL), node(11, 1, WP, "16"), node(12, 1, WP, "10")]
    findings = check_hundred_percent(elements, [act(101, 11, "8"), act(102, 11, "4")], [], [])
    assert [(f.element_id, f.own_estimate_hours, f.lower_hours) for f in findings] == [(11, D("16"), D("12"))]


def test_zero_own_estimate_is_not_divided() -> None:
    """RULE-WBS-004：自分の見積が 0 の要素は割合を計算せず、子の集計が 0 でなければ SUM_MISMATCH、0 なら出さない（5.3）。"""
    elements = [node(1, None, DEL, "0"), node(11, 1, WP, "8")]
    assert [f.element_id for f in check_hundred_percent(elements, [], [], [], tolerance=D("100"))] == [1]
    zero = [node(1, None, DEL, "0"), node(11, 1, WP, "0")]
    assert check_hundred_percent(zero, [], [], []) == ()


def test_scope_out_linked_and_in_uncovered() -> None:
    """RULE-WBS-004：対象外の項目への紐付けは OUT_OF_SCOPE_LINKED（ERROR）、紐付いていない対象内の項目は IN_SCOPE_UNCOVERED（WARNING）（PRJ-02・WBS-16）。"""
    elements = [node(1, None, DEL), node(11, 1, WP), node(12, 1, WP)]
    items = [
        ScopeItemRef(501, ScopeKind.IN),
        ScopeItemRef(502, ScopeKind.IN),
        ScopeItemRef(503, ScopeKind.OUT),
        ScopeItemRef(504, ScopeKind.OUT),
    ]
    links = [ScopeLinkRef(11, 501), ScopeLinkRef(12, 503), ScopeLinkRef(11, 503)]
    findings = check_hundred_percent(elements, [], items, links)
    assert [(f.level, f.code, f.element_id, f.scope_item_id) for f in findings] == [
        (FindingLevel.ERROR, FindingCode.OUT_OF_SCOPE_LINKED, 12, 503),
        (FindingLevel.ERROR, FindingCode.OUT_OF_SCOPE_LINKED, 11, 503),
        (FindingLevel.WARNING, FindingCode.IN_SCOPE_UNCOVERED, None, 502),
    ]
    assert count_errors(findings) == 2


def test_three_judgements_together_in_order() -> None:
    """RULE-WBS-004：3 つの判定を結果として返す（WBS-16 の受入基準）。並びは合計の差異→対象外の混入→対象内の漏れ。"""
    elements = [node(1, None, DEL, "10"), node(11, 1, WP, "4")]
    findings = check_hundred_percent(
        elements, [], [ScopeItemRef(1, ScopeKind.OUT), ScopeItemRef(2, ScopeKind.IN)], [ScopeLinkRef(11, 1)]
    )
    assert [f.code for f in findings] == [
        FindingCode.SUM_MISMATCH,
        FindingCode.OUT_OF_SCOPE_LINKED,
        FindingCode.IN_SCOPE_UNCOVERED,
    ]


@pytest.mark.parametrize("tolerance", [D("-0.01"), 0.1, True, D("NaN")])
def test_invalid_tolerance_is_rejected(tolerance: object) -> None:
    """RULE-WBS-004：許容幅は 0 以上の Decimal か int。負・float・真偽値・非数は拒む。"""
    with pytest.raises(WbsRuleError):
        check_hundred_percent([node(1, None, DEL)], [], [], [], tolerance=tolerance)  # type: ignore[arg-type]


def test_invalid_scope_input_is_rejected() -> None:
    """RULE-WBS-004：紐付けの重複・入力に無い要素や項目・区分の誤りは拒む。木の不正は RULE-EST-001 の EstimateError。"""
    elements = [node(1, None, DEL)]
    items = [ScopeItemRef(5, ScopeKind.IN)]
    with pytest.raises(WbsRuleError):
        check_hundred_percent(elements, [], items, [ScopeLinkRef(1, 5), ScopeLinkRef(1, 5)])
    with pytest.raises(WbsRuleError):
        check_hundred_percent(elements, [], items, [ScopeLinkRef(9, 5)])
    with pytest.raises(WbsRuleError):
        check_hundred_percent(elements, [], items, [ScopeLinkRef(1, 9)])
    with pytest.raises(WbsRuleError):
        check_hundred_percent(elements, [], [ScopeItemRef(5, "MAYBE")], [])  # type: ignore[arg-type]
    with pytest.raises(EstimateError):
        check_hundred_percent([node(1, 2, DEL), node(2, 1, DEL)], [], [], [])


# ---------------------------------------------------------------------------
# RULE-WBS-005 完了条件の必須検査
# ---------------------------------------------------------------------------


def test_missing_deliverable_or_criteria_is_listed() -> None:
    """RULE-WBS-005：成果物か完了条件が空の WP を一覧で返す（WBS-13 の受入基準）。WP 以外は空でも挙げない。"""
    elements = [node(1, None, DEL), node(11, 1, WP), node(12, 1, WP), node(13, 1, WP), node(14, 1, WP)]
    entries = [
        DictionaryRef(1),
        DictionaryRef(11, deliverable="計画書", completion_criteria="PM の承認"),
        DictionaryRef(12, deliverable="議事録", completion_criteria=None),
        DictionaryRef(13, deliverable=" 　", completion_criteria="レビュー済み"),
    ]
    result = find_missing_completion(elements, entries)
    assert [(m.element_id, m.missing) for m in result] == [
        (12, (CompletionField.COMPLETION_CRITERIA,)),
        (13, (CompletionField.DELIVERABLE,)),
        (14, (CompletionField.DELIVERABLE, CompletionField.COMPLETION_CRITERIA)),
    ]


def test_all_defined_gives_empty_list() -> None:
    """RULE-WBS-005：全ての WP に成果物と完了条件があれば 0 件（境界：1 文字でも空でない）。"""
    elements = [node(11, None, WP)]
    assert find_missing_completion(elements, [DictionaryRef(11, deliverable="a", completion_criteria="b")]) == ()
    assert find_missing_completion([node(1, None, DEL)]) == ()


def test_missing_completion_rejects_bad_input() -> None:
    """RULE-WBS-005：辞書の重複・入力に無い要素の辞書・文字列でない値は拒む。"""
    elements = [node(11, None, WP)]
    with pytest.raises(WbsRuleError):
        find_missing_completion(elements, [DictionaryRef(11), DictionaryRef(11)])
    with pytest.raises(WbsRuleError):
        find_missing_completion(elements, [DictionaryRef(99)])
    with pytest.raises(WbsRuleError):
        find_missing_completion(elements, [DictionaryRef(11, deliverable=1)])  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# RULE-WBS-007 責任分担表の組み立て
# ---------------------------------------------------------------------------


def test_responsibility_matrix_rows_cells_and_without_responsible() -> None:
    """RULE-WBS-007：WP × メンバーの表（責任者・担当・割当工数）と、責任者の無い WP の一覧（WBS-10 の受入基準）。"""
    elements = [
        ResponsibilityElement(1, DEL, responsible_user_id=9),
        ResponsibilityElement(11, WP, responsible_user_id=7, responsible_team="設計"),
        ResponsibilityElement(12, WP, responsible_team="開発"),
        ResponsibilityElement(13, WP),
    ]
    activities = [ActivityRef(101, 11), ActivityRef(102, 11), ActivityRef(103, 12)]
    assignments = [
        AssignmentRef(101, 7, D("8")),
        AssignmentRef(102, 7, D("4.5")),
        AssignmentRef(102, 8, None),
        AssignmentRef(103, 8, D("16")),
    ]
    matrix = build_responsibility_matrix(elements, activities, assignments, [8, 7])
    assert matrix.user_ids == (8, 7)
    assert matrix.non_member_user_ids == ()
    assert [row.element_id for row in matrix.rows] == [11, 12, 13]
    row11 = matrix.rows[0]
    assert [(c.user_id, c.is_responsible, c.is_assigned, c.planned_hours) for c in row11.cells] == [
        (8, False, True, D("0")),
        (7, True, True, D("12.5")),
    ]
    assert (row11.responsible_team, row11.planned_hours) == ("設計", D("12.5"))
    assert [(c.is_assigned, c.planned_hours) for c in matrix.rows[2].cells] == [(False, D("0")), (False, D("0"))]
    # 担当チームだけの WP も「責任者の無い WP」
    assert matrix.without_responsible == (12, 13)


def test_non_members_are_added_as_columns() -> None:
    """RULE-WBS-007：メンバーでない人が責任者・担当に残っていれば、ID の昇順で列の後ろに足して示す。"""
    elements = [ResponsibilityElement(11, WP, responsible_user_id=30)]
    matrix = build_responsibility_matrix(
        elements, [ActivityRef(101, 11)], [AssignmentRef(101, 20, D("1"))], member_user_ids=[5]
    )
    assert matrix.user_ids == (5, 20, 30)
    assert matrix.non_member_user_ids == (20, 30)


def test_responsibility_matrix_rejects_bad_input() -> None:
    """RULE-WBS-007：WP 以外の上の活動・入力に無い活動の割当・割当の重複・負の工数・メンバーの重複は拒む。"""
    elements = [ResponsibilityElement(1, DEL), ResponsibilityElement(11, WP)]
    with pytest.raises(WbsRuleError):
        build_responsibility_matrix(elements, [ActivityRef(101, 1)], [], [])
    with pytest.raises(WbsRuleError):
        build_responsibility_matrix(elements, [], [AssignmentRef(999, 1)], [])
    with pytest.raises(WbsRuleError):
        build_responsibility_matrix(
            elements, [ActivityRef(101, 11)], [AssignmentRef(101, 1), AssignmentRef(101, 1)], []
        )
    with pytest.raises(WbsRuleError):
        build_responsibility_matrix(elements, [ActivityRef(101, 11)], [AssignmentRef(101, 1, D("-1"))], [])
    with pytest.raises(WbsRuleError):
        build_responsibility_matrix(elements, [], [], [1, 1])


# ---------------------------------------------------------------------------
# RULE-WBS-008 WBS IDによる横断照会
# ---------------------------------------------------------------------------


def ce(id_: int, parent: int | None, code: str, seq: int) -> CodedElement:
    return CodedElement(id=id_, parent_id=parent, wbs_code=code, sibling_seq=seq, sort_order=seq)


def trace_fixture() -> dict[str, object]:
    """1.0（10）の下に 1.1（11・WP）・1.2（12・構成要素）・1.2.1（121・WP）。2.0（20）の下に 2.1（21・WP）。
    1.2 は改番前に 1.3 だった（新旧対応あり）。
    """
    return {
        "elements": [
            ce(10, None, "1.0", 1),
            ce(11, 10, "1.1", 1),
            ce(12, 10, "1.2", 2),
            ce(121, 12, "1.2.1", 1),
            ce(20, None, "2.0", 2),
            ce(21, 20, "2.1", 1),
        ],
        "history": [
            CodeHistoryEntry(id=1, element_id=12, old_code="1.3", new_code="1.2", renumbered_at=T0),
            CodeHistoryEntry(id=2, element_id=121, old_code="1.3.1", new_code="1.2.1", renumbered_at=T0),
        ],
        "activities": [ActivityRef(1001, 11), ActivityRef(1211, 121), ActivityRef(1212, 121), ActivityRef(2101, 21)],
        "estimate_records": [
            EstimateRecordRef(1, EstimateTarget.ELEMENT, 12),
            EstimateRecordRef(2, EstimateTarget.ACTIVITY, 1211),
            EstimateRecordRef(3, EstimateTarget.ACTIVITY, 2101),
            EstimateRecordRef(4, EstimateTarget.ELEMENT, 10),
        ],
        "time_entries": [
            TimeEntryRef(1, 1211, D("3.5")),
            TimeEntryRef(2, 1212, D("2")),
            TimeEntryRef(3, 1001, D("8")),
        ],
        "change_requests": [
            ChangeRequestRef(1, ("1.3.1",)),
            ChangeRequestRef(2, ("2.1", "1.2")),
            ChangeRequestRef(3, ("1.1",)),
            ChangeRequestRef(4, ("9.9",)),
        ],
        "corrective_actions": [
            CorrectiveActionRef(1, element_id=121),
            CorrectiveActionRef(2, activity_id=1212),
            CorrectiveActionRef(3, element_id=11),
        ],
    }


def test_trace_collects_subtree_items() -> None:
    """RULE-WBS-008：WBS ID を 1 つ指定すると、その要素（配下を含む）の活動・見積・実績・EAC・変更要求・是正措置を 1 回で返す（WBS-07 の受入基準）。"""
    data = trace_fixture()
    trace = trace_wbs_code("1.2", eac_hours_by_element={12: D("40"), 10: D("90")}, **data)  # type: ignore[arg-type]
    assert trace is not None
    assert (trace.resolution.element_id, trace.resolution.resolved_by) == (12, ResolvedBy.CURRENT)
    assert trace.element_ids == (12, 121)
    assert trace.activity_ids == (1211, 1212)
    assert trace.estimate_record_ids == (1, 2)
    assert trace.time_entry_ids == (1, 2)
    assert trace.actual_hours == D("5.5")
    assert trace.eac_hours == D("40")
    # 変更要求 1 は旧 ID 1.3.1 で 1.2.1 を、2 は 1.2 を対象にしている
    assert trace.change_request_ids == (1, 2)
    assert trace.corrective_action_ids == (1, 2)


def test_trace_by_old_code_resolves_to_current_element() -> None:
    """RULE-WBS-008：旧 ID でも照会できる（RULE-WBS-002 の解決）。結果は今の要素と今の ID。"""
    trace = trace_wbs_code("1.3", **trace_fixture())  # type: ignore[arg-type]
    assert trace is not None
    assert (trace.resolution.query, trace.resolution.wbs_code, trace.resolution.resolved_by) == (
        "1.3",
        "1.2",
        ResolvedBy.HISTORY,
    )
    assert trace.element_ids == (12, 121)
    assert trace.eac_hours is None


def test_trace_top_level_and_unknown_code() -> None:
    """RULE-WBS-008：最上位の ID は配下全体を集める。解決できない ID は None（404 にする）。"""
    data = trace_fixture()
    top = trace_wbs_code("1.0", **data)  # type: ignore[arg-type]
    assert top is not None
    assert top.element_ids == (10, 11, 12, 121)
    assert top.activity_ids == (1001, 1211, 1212)
    assert top.estimate_record_ids == (1, 2, 4)
    assert top.actual_hours == D("13.5")
    assert top.change_request_ids == (1, 2, 3)
    assert top.corrective_action_ids == (1, 2, 3)
    assert trace_wbs_code("7.0", **data) is None  # type: ignore[arg-type]


def test_trace_leaf_without_related_items() -> None:
    """RULE-WBS-008：関わるものが無い要素は空の一覧と実績 0 を返す。"""
    elements = [ce(1, None, "1.0", 1)]
    trace = trace_wbs_code("1.0", elements=elements)
    assert trace is not None
    assert (trace.element_ids, trace.activity_ids, trace.actual_hours, trace.change_request_ids) == ((1,), (), D("0"), ())


def test_trace_rejects_bad_input() -> None:
    """RULE-WBS-008：活動の重複・見積の対象の誤り・負の実績工数は拒む。"""
    elements = [ce(1, None, "1.0", 1)]
    with pytest.raises(WbsRuleError):
        trace_wbs_code("1.0", elements=elements, activities=[ActivityRef(5, 1), ActivityRef(5, 1)])
    with pytest.raises(WbsRuleError):
        trace_wbs_code("1.0", elements=elements, estimate_records=[EstimateRecordRef(1, "WBS", 1)])  # type: ignore[arg-type]
    with pytest.raises(WbsRuleError):
        trace_wbs_code(
            "1.0", elements=elements, activities=[ActivityRef(5, 1)], time_entries=[TimeEntryRef(1, 5, D("-1"))]
        )
