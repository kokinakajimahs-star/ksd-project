"""WBS ID の採番と、維持・改番・旧 ID の解決（app.domain.wbs_code）の試験。

設計書 4.1（wbs_elements・wbs_numbering_rules・wbs_code_history）・5.1・5.2、要求仕様書 WBS-05・06・07、BR-01・BR-02、W§4 の例。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.wbs_code import (
    MAX_CODE_LENGTH,
    ActivitySlot,
    CodedElement,
    CodeHistoryEntry,
    NumberingRule,
    ResolvedBy,
    WbsCodeError,
    activity_codes,
    code_fits_parent,
    code_for_path,
    make_code,
    move_element,
    new_element_code,
    renumber,
    resolve_code,
    subtree_ids,
)

DEFAULT = NumberingRule()
T0 = datetime(2026, 11, 10, 1, 0, tzinfo=UTC)


def el(
    id_: int,
    parent: int | None,
    code: str,
    seq: int,
    *,
    last: int = 0,
    order: int | None = None,
    needs: bool = False,
) -> CodedElement:
    return CodedElement(
        id=id_,
        parent_id=parent,
        wbs_code=code,
        sibling_seq=seq,
        last_child_seq=last,
        sort_order=seq if order is None else order,
        needs_renumber=needs,
    )


def w4_tree() -> list[CodedElement]:
    """要求仕様書 W§4 の例（Webサイト公開プロジェクト）。要素の ID は「第 1 階層の順番 × 10 ＋ 子の順番」。"""
    children = {1: 2, 2: 2, 3: 2, 4: 2, 5: 3}
    elements = []
    for top, count in children.items():
        elements.append(el(top * 10, None, f"{top}.0", top, last=count))
        for child in range(1, count + 1):
            elements.append(el(top * 10 + child, top * 10, f"{top}.{child}", child))
    return elements


# ---------------------------------------------------------------------------
# RULE-WBS-001 WBS IDの採番
# ---------------------------------------------------------------------------


def test_default_rule_matches_w4_example() -> None:
    """RULE-WBS-001：既定の規則（. ・1 桁・接頭辞なし・補完あり）で W§4 の例と同じ ID になる（WBS-05 の受入基準・BR-01）。"""
    expected = {
        (1,): "1.0", (1, 1): "1.1", (1, 2): "1.2",
        (2,): "2.0", (2, 1): "2.1", (2, 2): "2.2",
        (3,): "3.0", (3, 1): "3.1", (3, 2): "3.2",
        (4,): "4.0", (4, 1): "4.1", (4, 2): "4.2",
        (5,): "5.0", (5, 1): "5.1", (5, 2): "5.2", (5, 3): "5.3",
    }  # fmt: skip
    for path, code in expected.items():
        assert code_for_path(path, DEFAULT) == code


def test_make_code_first_level_and_children() -> None:
    """RULE-WBS-001：第 1 階層は「順番＋.0」、子は親の ID から .0 を外して「.順番」を足す（1.0 の子は 1.1。1.1 の子は 1.1.1）。"""
    first = make_code(1, DEFAULT)
    assert first == "1.0"
    parent = el(1, None, first, 1)
    second = make_code(2, DEFAULT, parent)
    assert second == "1.2"
    grandchild = make_code(1, DEFAULT, el(2, 1, second, 2))
    assert grandchild == "1.2.1"
    assert make_code(3, DEFAULT, el(3, 2, grandchild, 1)) == "1.2.1.3"


def test_custom_rule_separator_digits_prefix_and_no_fill() -> None:
    """RULE-WBS-001：区切り文字・最小桁数（0 詰め）・接頭辞・第 1 階層の補完なしの規則（WBS-05）。"""
    rule = NumberingRule(separator="-", min_digits=2, prefix="P", pad_first_level=False)
    assert code_for_path((1,), rule) == "P01"
    assert code_for_path((1, 2), rule) == "P01-02"
    assert code_for_path((1, 2, 3), rule) == "P01-02-03"
    # 桁が最小桁数より多ければそのまま（切らない）
    assert code_for_path((123, 4), rule) == "P123-04"


def test_custom_rule_with_first_level_fill() -> None:
    """RULE-WBS-001：補完ありの規則では第 1 階層に「区切り＋0」を足し、子の ID には入れない（5.2 の式どおり "0" を足す）。"""
    rule = NumberingRule(separator="-", min_digits=2, prefix="W", pad_first_level=True)
    top = code_for_path((3,), rule)
    assert top == "W03-0"
    assert make_code(7, rule, el(1, None, top, 3)) == "W03-07"


def test_min_digits_boundary() -> None:
    """RULE-WBS-001：最小桁数の境界。9→10 で桁が増える（1 桁）、2 桁なら 9 は 09・10 は 10。"""
    assert code_for_path((9, 9), DEFAULT) == "9.9"
    assert code_for_path((10, 10), DEFAULT) == "10.10"
    two = NumberingRule(min_digits=2)
    assert code_for_path((9, 9), two) == "09.09"
    assert code_for_path((10, 10), two) == "10.10"


def test_activity_codes_follow_wp_code_and_position_in_wp() -> None:
    """RULE-WBS-001（CR-028）：活動の番号は置き場の WP の ID ＋ WP の中の順番。第 1 階層の WP は .0 を外す（1.0 の活動は 1.1）。"""
    top_wp = el(1, None, "1.0", 1)
    wp = el(3, 2, "2.1", 1)
    deep = el(5, 4, "2.2.1", 1)
    activities = [
        ActivitySlot(id=11, work_package_id=3, sort_order=2),
        ActivitySlot(id=10, work_package_id=3, sort_order=1),
        ActivitySlot(id=12, work_package_id=3, sort_order=5),  # sort_order の飛びは詰めて数える
        ActivitySlot(id=20, work_package_id=1, sort_order=1),
        ActivitySlot(id=30, work_package_id=5, sort_order=1),
        ActivitySlot(id=31, work_package_id=5, sort_order=1),  # 同じ sort_order は id の順
    ]
    codes = activity_codes(activities, {1: top_wp, 3: wp, 5: deep}, DEFAULT)
    assert codes == {10: "2.1.1", 11: "2.1.2", 12: "2.1.3", 20: "1.1", 30: "2.2.1.1", 31: "2.2.1.2"}


def test_activity_codes_follow_rule_and_current_wp_code() -> None:
    """RULE-WBS-001（CR-028）：区切り・桁数・接頭辞の規則に従い、WP が今持っている ID を頭にする。WP を渡さない活動は返さない。"""
    rule = NumberingRule(separator="-", min_digits=2, prefix="P", pad_first_level=False)
    wp = el(2, 1, "P01-03", 3)
    moved = el(4, 1, "P07-01", 1, needs=True)  # 改番待ちでも今の ID を頭にする
    activities = [
        ActivitySlot(id=1, work_package_id=2, sort_order=1),
        ActivitySlot(id=2, work_package_id=4, sort_order=3),
        ActivitySlot(id=3, work_package_id=99, sort_order=1),
    ]
    assert activity_codes(activities, {2: wp, 4: moved}, rule) == {1: "P01-03-01", 2: "P07-01-01"}
    # 前の活動を消すと後ろの番号が振り直る（保存しない）
    assert activity_codes([ActivitySlot(id=5, work_package_id=2, sort_order=2)], {2: wp}, rule) == {5: "P01-03-01"}


def test_make_code_for_path_matches_make_code_chain() -> None:
    """RULE-WBS-001：順番の列からの ID と、親から順に make_code を当てた ID は同じ。"""
    rule = NumberingRule(separator="_", min_digits=3, prefix="X")
    a = make_code(2, rule)
    b = make_code(5, rule, el(1, None, a, 2))
    c = make_code(1, rule, el(2, 1, b, 5))
    assert c == code_for_path((2, 5, 1), rule) == "X002_005_001"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"separator": ""},
        {"separator": "1"},
        {"separator": ".0"},
        {"min_digits": 0},
        {"min_digits": True},
        {"prefix": None},
        {"pad_first_level": "yes"},
    ],
)
def test_invalid_rule_is_rejected(kwargs: dict[str, object]) -> None:
    """RULE-WBS-001：区切り文字が空・数字を含む、最小桁数が 1 未満・整数でない、接頭辞・補完の型が違う規則は拒む。"""
    with pytest.raises(WbsCodeError):
        NumberingRule(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize("seq", [0, -1, True])
def test_invalid_seq_is_rejected(seq: int) -> None:
    """RULE-WBS-001：順番は 1 以上の整数。0・負・真偽値は拒む。ルート（空の列）は ID を持たない。"""
    with pytest.raises(WbsCodeError):
        make_code(seq, DEFAULT)
    with pytest.raises(WbsCodeError):
        code_for_path((), DEFAULT)


def test_code_longer_than_column_is_rejected() -> None:
    """RULE-WBS-001：ID が列の長さ（100 文字）を超えれば拒む。ちょうど 100 文字は作れる。"""
    rule = NumberingRule(prefix="A" * (MAX_CODE_LENGTH - 3))
    assert len(make_code(1, rule)) == MAX_CODE_LENGTH  # A…A + "1" + ".0"
    with pytest.raises(WbsCodeError):
        make_code(10, rule)


# ---------------------------------------------------------------------------
# RULE-WBS-002 WBS IDの維持・改番・旧IDの解決
# ---------------------------------------------------------------------------


def test_new_element_gets_last_child_seq_plus_one() -> None:
    """RULE-WBS-002：新規の要素は親の last_child_seq＋1。W§4 の 5.0（子 3 件）の下は 5.4、最上位（5 件）は 6.0。"""
    parent = el(50, None, "5.0", 5, last=3)
    new = new_element_code(parent, DEFAULT)
    assert (new.sibling_seq, new.wbs_code, new.parent_last_child_seq, new.needs_renumber) == (4, "5.4", 4, False)
    top = new_element_code(None, DEFAULT, top_last_child_seq=5)
    assert (top.sibling_seq, top.wbs_code, top.parent_last_child_seq) == (6, "6.0", 6)
    first = new_element_code(None, DEFAULT, top_last_child_seq=0)
    assert first.wbs_code == "1.0"
    leaf = new_element_code(el(51, 50, "5.1", 1, last=0), DEFAULT)
    assert leaf.wbs_code == "5.1.1"


def test_deleted_number_is_not_reused() -> None:
    """RULE-WBS-002：削除で空いた番号は再利用しない（BR-02）。1.1・1.2・1.3 から 1.3 を消しても、次は 1.4。他の ID は変わらない。"""
    tree = [el(1, None, "1.0", 1, last=3), el(11, 1, "1.1", 1), el(12, 1, "1.2", 2), el(13, 1, "1.3", 3)]
    remaining = [e for e in tree if e.id != 13]
    parent = next(e for e in remaining if e.id == 1)
    assert new_element_code(parent, DEFAULT).wbs_code == "1.4"
    assert [e.wbs_code for e in remaining] == ["1.0", "1.1", "1.2"]


def test_new_element_under_waiting_parent_is_waiting() -> None:
    """RULE-WBS-002：改番待ちの親の下の新しい要素も改番待ち。ID は親の今の ID から作る。"""
    parent = el(21, 10, "2.1", 3, last=2, needs=True)
    new = new_element_code(parent, DEFAULT)
    assert (new.wbs_code, new.needs_renumber) == ("2.1.3", True)


def test_new_element_counter_arguments_are_checked() -> None:
    """RULE-WBS-002：最上位では top_last_child_seq が要り、親のある要素では渡さない（どちらを数えるか取り違えない）。"""
    with pytest.raises(WbsCodeError):
        new_element_code(None, DEFAULT)
    with pytest.raises(WbsCodeError):
        new_element_code(None, DEFAULT, top_last_child_seq=-1)
    with pytest.raises(WbsCodeError):
        new_element_code(el(1, None, "1.0", 1), DEFAULT, top_last_child_seq=3)


def test_move_keeps_codes_and_marks_subtree() -> None:
    """RULE-WBS-002：移動で ID は変えず、移動先の last_child_seq＋1 を順番にし、形が合わない要素と配下を改番待ちにする（WBS-06 の受入基準）。"""
    elements = w4_tree() + [el(211, 21, "2.1.1", 1), el(212, 21, "2.1.2", 2)]
    elements = [e if e.id != 21 else el(21, 20, "2.1", 1, last=2) for e in elements]
    result = move_element(elements, 21, 10, DEFAULT)
    assert result.wbs_code == "2.1"
    assert (result.parent_id, result.sibling_seq, result.parent_last_child_seq) == (10, 3, 3)
    assert dict(result.needs_renumber) == {21: True, 211: True, 212: True}
    # 移動しない要素の ID は入力のまま（関数は他の要素の ID を返さない）
    assert set(result.needs_renumber) == {21, 211, 212}


def test_move_back_to_original_parent_clears_waiting() -> None:
    """RULE-WBS-002：元の親へ戻して形が合えば改番待ちを解く（形の判定は今の ID と移動先の親の ID で行う）。"""
    elements = [
        el(10, None, "1.0", 1, last=3),
        el(20, None, "2.0", 2, last=2),
        el(21, 10, "2.1", 3, needs=True),
        el(211, 21, "2.1.1", 1, needs=True),
    ]
    result = move_element(elements, 21, 20, DEFAULT)
    assert result.sibling_seq == 3
    assert dict(result.needs_renumber) == {21: False, 211: False}


def test_move_to_top_level_and_under_waiting_parent() -> None:
    """RULE-WBS-002：最上位へ移すと最上位の数え＋1。改番待ちの親の下へ移すと、形が合っても改番待ち。"""
    elements = [
        el(10, None, "1.0", 1, last=1),
        el(11, 10, "1.1", 1),
        el(20, None, "2.0", 2, last=0, needs=True),
    ]
    to_top = move_element(elements, 11, None, DEFAULT, top_last_child_seq=2)
    assert (to_top.sibling_seq, to_top.parent_last_child_seq, dict(to_top.needs_renumber)) == (3, 3, {11: True})
    waiting = [el(10, None, "1.0", 1, last=0), el(11, None, "1.1", 3, last=0, needs=True), el(30, 10, "1.1.4", 1)]
    # 1.1.4 は親 1.1 の下の形に合うが、親が改番待ちなので改番待ち
    assert code_fits_parent("1.1.4", waiting[1], DEFAULT)
    moved = move_element(waiting, 30, 11, DEFAULT)
    assert (moved.sibling_seq, dict(moved.needs_renumber)) == (1, {30: True})


def test_move_within_same_parent_keeps_seq() -> None:
    """RULE-WBS-002：同じ親の中の移動（並べ替え）では順番も last_child_seq も変えない。"""
    elements = w4_tree()
    result = move_element(elements, 52, 50, DEFAULT)
    assert (result.sibling_seq, result.parent_last_child_seq, dict(result.needs_renumber)) == (2, None, {52: False})


def test_move_rejects_cycles_and_unknown_elements() -> None:
    """RULE-WBS-002：自分・配下の下へは移せない（循環）。無い要素・無い移動先は拒む。"""
    elements = w4_tree() + [el(111, 11, "1.1.1", 1)]
    with pytest.raises(WbsCodeError):
        move_element(elements, 10, 10, DEFAULT)
    with pytest.raises(WbsCodeError):
        move_element(elements, 10, 111, DEFAULT)
    with pytest.raises(WbsCodeError):
        move_element(elements, 999, 10, DEFAULT)
    with pytest.raises(WbsCodeError):
        move_element(elements, 11, 999, DEFAULT)
    with pytest.raises(WbsCodeError):
        move_element(elements, 11, None, DEFAULT)  # 最上位の数えが無い


def test_code_fits_parent() -> None:
    """RULE-WBS-002：形の判定。最上位は「接頭辞＋順番（＋.0）」、子は「親の頭＋区切り＋順番」。0 詰めの形も見る。"""
    top = el(1, None, "1.0", 1)
    assert code_fits_parent("1.0", None, DEFAULT)
    assert not code_fits_parent("1.1", None, DEFAULT)
    assert code_fits_parent("1.7", top, DEFAULT)
    assert not code_fits_parent("2.7", top, DEFAULT)
    assert not code_fits_parent("1.7.1", top, DEFAULT)
    assert not code_fits_parent("1.0", top, DEFAULT)  # 順番 0 は無い
    two = NumberingRule(min_digits=2, prefix="P")
    assert code_fits_parent("P01.0", None, two)
    assert not code_fits_parent("P1.0", None, two)
    assert code_fits_parent("P01.03", el(1, None, "P01.0", 1), two)
    assert not code_fits_parent("P01.3", el(1, None, "P01.0", 1), two)


def test_renumber_follows_sort_order_and_returns_changes() -> None:
    """RULE-WBS-002：改番は兄弟の表示順に 1 から振り直し、変わった要素だけ（element_id, old_code, new_code）を返す（WBS-06）。"""
    # 1.0 の下：1.2（表示順 1）・1.4（表示順 2）。2.1 を 1.0 の下へ移した後（表示順 3・改番待ち）、2.0 は 2.2 だけ
    elements = [
        el(10, None, "1.0", 1, last=5),
        el(12, 10, "1.2", 2, order=1),
        el(14, 10, "1.4", 4, order=2),
        el(21, 10, "2.1", 5, order=3, needs=True),
        el(211, 21, "2.1.1", 1, needs=True),
        el(20, None, "2.0", 2, last=2),
        el(22, 20, "2.2", 2),
    ]
    result = renumber(elements, DEFAULT, top_last_child_seq=2)
    assert dict(result.codes) == {
        10: "1.0", 12: "1.1", 14: "1.2", 21: "1.3", 211: "1.3.1", 20: "2.0", 22: "2.1",
    }  # fmt: skip
    assert result.sibling_seqs[21] == 3
    assert [(c.element_id, c.old_code, c.new_code) for c in result.changes] == [
        (12, "1.2", "1.1"),
        (14, "1.4", "1.2"),
        (21, "2.1", "1.3"),
        (211, "2.1.1", "1.3.1"),
        (22, "2.2", "2.1"),
    ]
    # last_child_seq は減らさない（BR-02 の「今まで使った最大の順番」）
    assert result.last_child_seqs[10] == 5
    assert result.last_child_seqs[20] == 2
    assert result.top_last_child_seq == 2


def test_renumber_with_new_rule_gives_new_rule_codes() -> None:
    """RULE-WBS-002：規則を変えて改番すると新規則の ID になる（WBS-05 の受入基準）。W§4 の例を接頭辞・2 桁・補完なしへ。"""
    rule = NumberingRule(separator="-", min_digits=2, prefix="WEB", pad_first_level=False)
    result = renumber(w4_tree(), rule, top_last_child_seq=5)
    assert result.codes[10] == "WEB01"
    assert result.codes[11] == "WEB01-01"
    assert result.codes[53] == "WEB05-03"
    assert len(result.changes) == 16


def test_renumber_without_changes_and_ties() -> None:
    """RULE-WBS-002：既に整っていれば変更なし。表示順が同じなら今の順番、次に ID の順。"""
    assert renumber(w4_tree(), DEFAULT, top_last_child_seq=5).changes == ()
    tied = [el(1, None, "1.0", 1, last=3), el(13, 1, "1.3", 3, order=0), el(12, 1, "1.2", 2, order=0)]
    result = renumber(tied, DEFAULT, top_last_child_seq=1)
    assert (result.codes[12], result.codes[13]) == ("1.1", "1.2")


def test_renumber_grows_counter_when_children_exceed_it() -> None:
    """RULE-WBS-002：子の数が last_child_seq を超える入力（取込など）では、子の数まで上げる（同じ番号を二度振らない）。"""
    elements = [el(1, None, "1.0", 1, last=0), el(11, 1, "1.1", 1), el(12, 1, "1.2", 2)]
    result = renumber(elements, DEFAULT)
    assert result.last_child_seqs[1] == 2
    assert result.top_last_child_seq == 1


def test_renumber_rejects_broken_trees() -> None:
    """RULE-WBS-002：ID の重複・無い親・循環の木は改番しない。"""
    with pytest.raises(WbsCodeError):
        renumber([el(1, None, "1.0", 1), el(1, None, "2.0", 2)], DEFAULT)
    with pytest.raises(WbsCodeError):
        renumber([el(1, 99, "1.0", 1)], DEFAULT)
    with pytest.raises(WbsCodeError):
        renumber([el(1, 2, "1.0", 1), el(2, 1, "2.0", 1)], DEFAULT)


def test_resolve_current_code_first() -> None:
    """RULE-WBS-002：今の ID に一致すればそれを返す。旧 ID が別の要素の今の ID と重なれば、今の ID を優先する（BR-02）。"""
    elements = [el(10, None, "1.0", 1), el(12, 10, "1.1", 1), el(14, 10, "1.2", 2)]
    history = [
        CodeHistoryEntry(id=1, element_id=12, old_code="1.2", new_code="1.1", renumbered_at=T0),
        CodeHistoryEntry(id=2, element_id=14, old_code="1.4", new_code="1.2", renumbered_at=T0),
    ]
    hit = resolve_code("1.2", elements, history)
    assert hit is not None
    assert (hit.element_id, hit.wbs_code, hit.resolved_by) == (14, "1.2", ResolvedBy.CURRENT)


def test_resolve_old_code_via_history() -> None:
    """RULE-WBS-002：旧 ID で検索しても今の要素に解決される（WBS-06 の受入基準）。改番を 2 回たどった要素も今の ID を返す。"""
    elements = [el(10, None, "1.0", 1), el(21, 10, "1.3", 3)]
    history = [
        CodeHistoryEntry(id=1, element_id=21, old_code="2.1", new_code="1.5", renumbered_at=T0),
        CodeHistoryEntry(id=7, element_id=21, old_code="1.5", new_code="1.3", renumbered_at=T0 + timedelta(days=3)),
    ]
    for old in ("2.1", "1.5"):
        hit = resolve_code(old, elements, history)
        assert hit is not None
        assert (hit.query, hit.element_id, hit.wbs_code, hit.resolved_by) == (old, 21, "1.3", ResolvedBy.HISTORY)
    assert resolve_code("9.9", elements, history) is None


def test_resolve_takes_newest_history_first() -> None:
    """RULE-WBS-002：同じ旧 ID が複数の対応にあれば、新しい対応（時刻、同じなら ID の大きい方）を採る。その要素が消えていれば None。"""
    elements = [el(10, None, "1.0", 1), el(11, 10, "1.1", 1), el(12, 10, "1.2", 2)]
    history = [
        CodeHistoryEntry(id=1, element_id=11, old_code="1.5", new_code="1.1", renumbered_at=T0),
        CodeHistoryEntry(id=9, element_id=12, old_code="1.5", new_code="1.2", renumbered_at=T0 + timedelta(hours=1)),
    ]
    hit = resolve_code("1.5", elements, history)
    assert hit is not None and hit.element_id == 12
    same_time = [
        CodeHistoryEntry(id=3, element_id=11, old_code="1.6", new_code="1.1", renumbered_at=T0),
        CodeHistoryEntry(id=4, element_id=12, old_code="1.6", new_code="1.2", renumbered_at=T0),
    ]
    hit = resolve_code("1.6", elements, same_time)
    assert hit is not None and hit.element_id == 12
    deleted = [CodeHistoryEntry(id=5, element_id=99, old_code="1.8", new_code="1.3", renumbered_at=T0)]
    assert resolve_code("1.8", elements, deleted) is None


def test_resolve_rejects_ambiguous_and_naive_input() -> None:
    """RULE-WBS-002：今の ID が同じ要素が 2 つあれば決めない。新旧対応の時刻は時差つき。空の ID は拒む。"""
    with pytest.raises(WbsCodeError):
        resolve_code("1.0", [el(1, None, "1.0", 1), el(2, None, "1.0", 2)])
    naive = [CodeHistoryEntry(id=1, element_id=1, old_code="9.0", new_code="1.0", renumbered_at=datetime(2026, 1, 1))]
    with pytest.raises(WbsCodeError):
        resolve_code("9.0", [el(1, None, "1.0", 1)], naive)
    with pytest.raises(WbsCodeError):
        resolve_code("", [el(1, None, "1.0", 1)])


def test_subtree_ids_in_display_order() -> None:
    """RULE-WBS-002：配下の要素の一覧（先行順・兄弟は表示順）。無い要素は拒む。"""
    elements = w4_tree() + [el(511, 51, "5.1.1", 1)]
    assert subtree_ids(elements, 50) == (50, 51, 511, 52, 53)
    with pytest.raises(WbsCodeError):
        subtree_ids(elements, 999)
