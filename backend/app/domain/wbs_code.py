"""WBS ID の採番と、維持・改番・旧 ID の解決（設計書 5.1 の RULE-WBS-001・002、5.2。要求仕様書 WBS-05・06・07、BR-01・BR-02）。

- 採番（RULE-WBS-001）：採番規則（区切り文字・最小桁数・接頭辞・第 1 階層の補完）と兄弟の順番から ID を作る（5.2 の式）::

      第1階層：  prefix + pad(順番) + (pad_first_level ? separator + "0" : "")
      第2階層以下：code_without_first_pad(親) + separator + pad(順番)
      code_without_first_pad(第1階層の要素) = prefix + pad(順番)

  既定の規則（``.``・1 桁・接頭辞なし・補完あり）で要求仕様書 W§4 の例（1.0、1.1、1.2、2.0、2.1…）になる。
- 維持・改番・旧 ID の解決（RULE-WBS-002）：
  - 新規：``順番 = 親の last_child_seq + 1``。``last_child_seq`` は減らさない（削除した番号を再利用しない）
  - 移動：移動先の親の ``last_child_seq + 1`` を新しい順番にするが **``wbs_code`` は変えない**。
    親の ``wbs_code`` と形が合わない要素（配下を含む）を改番待ち（``needs_renumber``）にする
  - 削除：他の要素の ID・順番は何も変えない（この節の関数を呼ばない）
  - 改番（明示の操作）：兄弟の表示順（``sort_order``）に 1 から振り直し、全要素の ID を作り直す。
    変わった要素ごとに ``(element_id, old_code, new_code)`` を返す（サービス層が ``wbs_code_history`` に保存する）。改番待ちは全て解く
  - 旧 ID の解決：現在の ``wbs_code`` に一致すればそれを返す。無ければ新旧の対応を新しい順にたどり、
    ``old_code`` が一致した要素の現在の ID を返す（BR-02：旧 ID が別の要素の現在の ID と重なれば現在の ID を優先する）

最上位の要素の順番の数え（親の ``last_child_seq`` に当たるもの）は ``wbs_numbering_rules.top_last_child_seq``
（設計書 1.1 の 4.1・5.2。CR-003）。この層はそれを引数（``top_last_child_seq``）で受け、読み書きはサービス層が行う。

純粋に保つ（1.3）。DB・HTTP・時刻・乱数に触れない。DB の行はサービス層が下のデータクラスへ直して渡す。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType

# 採番規則の既定（4.1 の wbs_numbering_rules。BR-01）
SEPARATOR_DEFAULT = "."
MIN_DIGITS_DEFAULT = 1
PREFIX_DEFAULT = ""
PAD_FIRST_LEVEL_DEFAULT = True
# 第 1 階層の補完で足す文字（5.2 の式の "0"）
FIRST_LEVEL_FILL = "0"
# WBS ID の長さの上限（wbs_elements.wbs_code・wbs_code_history の列の長さ。docs/decisions/ASSUMPTIONS.md A-15）
MAX_CODE_LENGTH = 100


class WbsCodeError(ValueError):
    """採番・改番・解決の入力が不正（未定義の入力は推測せずに拒む）。"""


def _check_int(value: object, what: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise WbsCodeError(f"{what} は整数で渡す: {value!r}")
    if value < minimum:
        raise WbsCodeError(f"{what} は {minimum} 以上: {value}")
    return value


@dataclass(frozen=True)
class NumberingRule:
    """採番規則（4.1 の ``wbs_numbering_rules``。WBS-05・BR-01）。

    - ``separator``：区切り文字。空と数字を含む文字列は拒む（順番の桁と見分けられなくなる）
    - ``min_digits``：順番を 0 詰めする最小の桁数（1 以上）
    - ``prefix``：接頭辞（空でよい）
    - ``pad_first_level``：第 1 階層に ``separator + "0"`` を補うか
    """

    separator: str = SEPARATOR_DEFAULT
    min_digits: int = MIN_DIGITS_DEFAULT
    prefix: str = PREFIX_DEFAULT
    pad_first_level: bool = PAD_FIRST_LEVEL_DEFAULT

    def __post_init__(self) -> None:
        if not isinstance(self.separator, str) or self.separator == "":
            raise WbsCodeError(f"区切り文字は 1 文字以上の文字列: {self.separator!r}")
        if any(ch.isdigit() for ch in self.separator):
            raise WbsCodeError(f"区切り文字に数字は使えない（順番の桁と見分けられない）: {self.separator!r}")
        _check_int(self.min_digits, "最小桁数", minimum=1)
        if not isinstance(self.prefix, str):
            raise WbsCodeError(f"接頭辞は文字列で渡す: {self.prefix!r}")
        if not isinstance(self.pad_first_level, bool):
            raise WbsCodeError(f"第 1 階層の補完は真偽値で渡す: {self.pad_first_level!r}")

    def pad(self, seq: int) -> str:
        """``pad(n)``：順番 ``n`` を ``min_digits`` 桁に 0 詰めした文字列（桁が多ければそのまま）。"""
        n = _check_int(seq, "順番", minimum=1)
        return str(n).zfill(self.min_digits)

    @property
    def first_level_fill(self) -> str:
        """第 1 階層の ID の末尾に補う文字列（補わないなら空）。"""
        return self.separator + FIRST_LEVEL_FILL if self.pad_first_level else ""


@dataclass(frozen=True)
class CodedElement:
    """WBS 要素のうち ID の維持に要る列（4.1 の ``wbs_elements``）。最上位の要素は ``parent_id`` が ``None``。"""

    id: int
    parent_id: int | None
    wbs_code: str
    sibling_seq: int
    last_child_seq: int = 0
    sort_order: int = 0
    needs_renumber: bool = False


def _check_code_length(code: str) -> str:
    if len(code) > MAX_CODE_LENGTH:
        raise WbsCodeError(f"WBS ID が長すぎる（上限 {MAX_CODE_LENGTH} 文字）: {code!r}")
    return code


def code_stem(element: CodedElement, rule: NumberingRule) -> str:
    """``code_without_first_pad``：子の ID の頭になる部分。

    第 1 階層の要素は、ID の末尾の補完（``separator + "0"``）を外す（BR-01：``1.0`` の子は ``1.1``）。
    第 2 階層以下の要素と、補完の付いていない第 1 階層の要素は ID のまま。
    """
    fill = rule.first_level_fill
    if element.parent_id is None and fill and element.wbs_code.endswith(fill):
        return element.wbs_code[: -len(fill)]
    return element.wbs_code


# RULE-WBS-001 WBS IDの採番
def make_code(seq: int, rule: NumberingRule, parent: CodedElement | None = None) -> str:
    """兄弟の中の順番 ``seq`` と採番規則から、要素の WBS ID を作る（5.2 の ``code(要素)``・BR-01・WBS-05）。

    - ``parent`` が ``None``（第 1 階層）：``prefix + pad(順番)``、補完ありなら ``separator + "0"`` を足す
    - 第 2 階層以下：親の ``code_without_first_pad``（``code_stem``）＋ ``separator`` ＋ ``pad(順番)``。
      親の ID は**親が今持っている ID** を使う（移動して改番待ちの親の子も、親の表示と同じ頭になる）

    順番は 1 以上の整数。ID が ``MAX_CODE_LENGTH`` を超えれば ``WbsCodeError``。
    """
    padded = rule.pad(seq)
    if parent is None:
        return _check_code_length(rule.prefix + padded + rule.first_level_fill)
    return _check_code_length(code_stem(parent, rule) + rule.separator + padded)


def code_for_path(seq_path: Sequence[int], rule: NumberingRule) -> str:
    """最上位から要素までの兄弟の順番の列から WBS ID を作る（``make_code`` を親から順に当てたものと同じ）。

    ``(1,)`` → ``1.0``、``(1, 2)`` → ``1.2``、``(2, 1, 3)`` → ``2.1.3``（既定の規則）。ルート（空の列）は ID を持たない。
    """
    if len(seq_path) == 0:
        raise WbsCodeError("ルート（プロジェクト全体）は WBS ID を持たない（BR-01）")
    stem = rule.prefix + rule.separator.join(rule.pad(seq) for seq in seq_path)
    return _check_code_length(stem + (rule.first_level_fill if len(seq_path) == 1 else ""))


def _is_canonical_seq(text: str, rule: NumberingRule) -> bool:
    """``text`` が ``pad(n)``（n ≥ 1）の形か。"""
    if not text.isascii() or not text.isdigit():
        return False
    n = int(text)
    return n >= 1 and rule.pad(n) == text


def code_fits_parent(code: str, parent: CodedElement | None, rule: NumberingRule) -> bool:
    """ID ``code`` が、親 ``parent``（``None`` は最上位）の下の ID の形をしているか（5.2 の「形が合う」）。

    - 最上位：``prefix + pad(n)``（補完ありなら末尾に ``separator + "0"``）
    - 第 2 階層以下：親の ``code_stem`` ＋ ``separator`` ＋ ``pad(n)``
    """
    if parent is None:
        fill = rule.first_level_fill
        if not code.startswith(rule.prefix) or not code.endswith(fill):
            return False
        body = code[len(rule.prefix) : len(code) - len(fill)]
        return _is_canonical_seq(body, rule)
    head = code_stem(parent, rule) + rule.separator
    return code.startswith(head) and _is_canonical_seq(code[len(head) :], rule)


# ---------------------------------------------------------------------------
# 木の入力の検査
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Tree:
    nodes: Mapping[int, CodedElement]
    children: Mapping[int | None, list[int]]  # None の下が最上位の要素
    preorder: tuple[int, ...]


def _index_tree(elements: Iterable[CodedElement]) -> _Tree:
    """要素の ID の重複・存在しない親・循環を拒み、子の一覧と先行順（兄弟は表示順）を作る。"""
    nodes: dict[int, CodedElement] = {}
    for element in elements:
        if element.id in nodes:
            raise WbsCodeError(f"WBS 要素の ID が重複している: {element.id}")
        _check_int(element.sibling_seq, f"WBS 要素 {element.id} の順番", minimum=1)
        _check_int(element.last_child_seq, f"WBS 要素 {element.id} の last_child_seq", minimum=0)
        if not isinstance(element.wbs_code, str) or element.wbs_code == "":
            raise WbsCodeError(f"WBS 要素 {element.id} の WBS ID が空")
        nodes[element.id] = element

    children: dict[int | None, list[int]] = {None: []}
    for element_id in nodes:
        children[element_id] = []
    for element in nodes.values():
        if element.parent_id is not None and element.parent_id not in nodes:
            raise WbsCodeError(f"WBS 要素 {element.id} の親 {element.parent_id} が入力に無い")
        children[element.parent_id].append(element.id)

    def display_order(element_id: int) -> tuple[int, int, int]:
        element = nodes[element_id]
        return (element.sort_order, element.sibling_seq, element.id)

    for kids in children.values():
        kids.sort(key=display_order)

    preorder: list[int] = []
    stack = list(reversed(children[None]))
    while stack:
        element_id = stack.pop()
        preorder.append(element_id)
        stack.extend(reversed(children[element_id]))
    if len(preorder) != len(nodes):
        reached = set(preorder)
        cyclic = sorted(element_id for element_id in nodes if element_id not in reached)
        raise WbsCodeError(f"WBS 要素の親子が循環している（最上位から辿れない要素）: {cyclic}")
    return _Tree(nodes=nodes, children=children, preorder=tuple(preorder))


def _subtree(tree: _Tree, root_id: int) -> list[int]:
    """``root_id`` と配下の要素の ID（先行順・兄弟は表示順）。"""
    result: list[int] = []
    stack = [root_id]
    while stack:
        element_id = stack.pop()
        result.append(element_id)
        stack.extend(reversed(tree.children[element_id]))
    return result


def display_preorder(elements: Iterable[CodedElement]) -> tuple[int, ...]:
    """全ての要素の ID を WBS の並び（先行順・兄弟は表示順 ``sort_order``）で返す。木の誤りは ``WbsCodeError``。"""
    return _index_tree(elements).preorder


def subtree_ids(elements: Iterable[CodedElement], element_id: int) -> tuple[int, ...]:
    """要素とその配下の ID（先行順・兄弟は表示順）。要素が無ければ ``WbsCodeError``。"""
    tree = _index_tree(elements)
    if element_id not in tree.nodes:
        raise WbsCodeError(f"WBS 要素 {element_id} が入力に無い")
    return tuple(_subtree(tree, element_id))


# ---------------------------------------------------------------------------
# WBS IDの維持・改番・旧IDの解決（RULE-WBS-002）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NewElementCode:
    """新しい要素に振る順番と ID。

    - ``parent_last_child_seq``：振った後の、親（最上位なら最上位の数え）の ``last_child_seq``。サービス層が保存する
    - ``needs_renumber``：親が改番待ちなら真（改番待ちの要素の配下は改番待ち。5.2）
    """

    sibling_seq: int
    wbs_code: str
    needs_renumber: bool
    parent_last_child_seq: int


def _destination_last_seq(parent: CodedElement | None, top_last_child_seq: int | None) -> int:
    if parent is None:
        if top_last_child_seq is None:
            raise WbsCodeError("最上位の要素の順番を数えるには top_last_child_seq（最上位で今まで使った最大の順番）が要る")
        return _check_int(top_last_child_seq, "最上位の last_child_seq", minimum=0)
    if top_last_child_seq is not None:
        raise WbsCodeError("親のある要素では top_last_child_seq を渡さない（親の last_child_seq を使う）")
    return _check_int(parent.last_child_seq, f"WBS 要素 {parent.id} の last_child_seq", minimum=0)


# RULE-WBS-002 WBS IDの維持・改番・旧IDの解決
def new_element_code(
    parent: CodedElement | None,
    rule: NumberingRule,
    *,
    top_last_child_seq: int | None = None,
) -> NewElementCode:
    """新しい要素の順番と ID を決める（5.2 の「新規の要素」・BR-02・WBS-06）。

    順番は ``親の last_child_seq + 1``（最上位なら ``top_last_child_seq + 1``）。``last_child_seq`` は減らさないので、
    削除で空いた番号は再利用しない。ID は ``make_code``（RULE-WBS-001）で親の今の ID から作る。他の要素は何も変えない。
    """
    seq = _destination_last_seq(parent, top_last_child_seq) + 1
    return NewElementCode(
        sibling_seq=seq,
        wbs_code=make_code(seq, rule, parent),
        needs_renumber=parent.needs_renumber if parent is not None else False,
        parent_last_child_seq=seq,
    )


@dataclass(frozen=True)
class MoveResult:
    """移動の結果。``wbs_code`` は変えない（5.2）。

    - ``sibling_seq``・``parent_last_child_seq``：移動した要素の新しい順番と、移動先の親（最上位なら最上位の数え）の新しい ``last_child_seq``。
      同じ親の中での移動（並べ替え）では変えない
    - ``needs_renumber``：移動した要素と配下の、改番待ちの新しい値（要素の ID から引く）。
      ID が親の ID と形が合わない要素、または改番待ちの要素の配下を真にする
    """

    element_id: int
    parent_id: int | None
    wbs_code: str
    sibling_seq: int
    parent_last_child_seq: int | None
    needs_renumber: Mapping[int, bool]


def move_element(
    elements: Iterable[CodedElement],
    element_id: int,
    new_parent_id: int | None,
    rule: NumberingRule,
    *,
    top_last_child_seq: int | None = None,
) -> MoveResult:
    """要素を ``new_parent_id``（``None`` は最上位）の下へ移したときの順番と改番待ちを決める（5.2 の「移動」・BR-02・WBS-06）。

    - 親が変わるなら、順番は移動先の ``last_child_seq + 1``（最上位なら ``top_last_child_seq + 1``）。親が変わらなければ順番を変えない
    - ID は移動した要素・配下・他の要素の全てで変えない
    - 移動した要素は「移動先の親が改番待ち」か「ID が移動先の親の ID と形が合わない」とき改番待ち。配下は「親が改番待ち」か「形が合わない」とき改番待ち
      （元の親へ戻して形が合えば解ける）

    要素が無い、移動先が無い、移動先が自分か配下（循環になる）なら ``WbsCodeError``。区分（WP は子を持たない）は RULE-WBS-003 が見る。
    """
    tree = _index_tree(elements)
    element = tree.nodes.get(element_id)
    if element is None:
        raise WbsCodeError(f"移動する WBS 要素 {element_id} が入力に無い")
    moved = _subtree(tree, element_id)
    parent: CodedElement | None = None
    if new_parent_id is not None:
        parent = tree.nodes.get(new_parent_id)
        if parent is None:
            raise WbsCodeError(f"移動先の WBS 要素 {new_parent_id} が入力に無い")
        if new_parent_id in moved:
            raise WbsCodeError(f"WBS 要素 {element_id} を自分か配下の {new_parent_id} の下へは移せない（親子が循環する）")

    if new_parent_id == element.parent_id:
        if top_last_child_seq is not None and parent is not None:
            raise WbsCodeError("親のある要素では top_last_child_seq を渡さない（親の last_child_seq を使う）")
        seq, last_seq = element.sibling_seq, None
    else:
        seq = _destination_last_seq(parent, top_last_child_seq) + 1
        last_seq = seq

    flags: dict[int, bool] = {
        element_id: (parent.needs_renumber if parent is not None else False)
        or not code_fits_parent(element.wbs_code, parent, rule)
    }
    for node_id in moved[1:]:
        node = tree.nodes[node_id]
        assert node.parent_id is not None
        flags[node_id] = flags[node.parent_id] or not code_fits_parent(node.wbs_code, tree.nodes[node.parent_id], rule)
    return MoveResult(
        element_id=element_id,
        parent_id=new_parent_id,
        wbs_code=element.wbs_code,
        sibling_seq=seq,
        parent_last_child_seq=last_seq,
        needs_renumber=MappingProxyType(flags),
    )


@dataclass(frozen=True)
class CodeChange:
    """改番で ID が変わった要素 1 件（サービス層が ``wbs_code_history`` の 1 行にする）。"""

    element_id: int
    old_code: str
    new_code: str


@dataclass(frozen=True)
class RenumberResult:
    """改番の結果。

    - ``codes``・``sibling_seqs``：全要素の新しい ID と順番（兄弟の表示順に 1 から）
    - ``changes``：ID が変わった要素だけ（先行順）。変わらない要素は含まない
    - ``last_child_seqs``・``top_last_child_seq``：新しい ``last_child_seq``（今の値と子の数の大きい方。減らさない）
    - 改番待ち（``needs_renumber``）は全て偽にする
    """

    codes: Mapping[int, str]
    sibling_seqs: Mapping[int, int]
    last_child_seqs: Mapping[int, int]
    top_last_child_seq: int
    changes: tuple[CodeChange, ...]


def renumber(
    elements: Iterable[CodedElement],
    rule: NumberingRule,
    *,
    top_last_child_seq: int = 0,
) -> RenumberResult:
    """全要素の順番と ID を、兄弟の表示順（``sort_order``）に 1 から振り直す（5.2 の「改番」・BR-02・WBS-05・06）。

    - 兄弟の並びは ``sort_order`` の昇順。同じ値なら今の順番、次に要素の ID の順で決める
    - ID は ``rule``（新しい規則でよい）と新しい順番から作る（``make_code``）。規則を変えて改番すると新規則の ID になる（WBS-05）
    - 変わった要素ごとに ``(element_id, old_code, new_code)`` を返す
    - ``last_child_seq`` は減らさない（今まで使った最大の順番を保つ。BR-02）
    """
    tree = _index_tree(elements)
    top_last = _check_int(top_last_child_seq, "最上位の last_child_seq", minimum=0)
    codes: dict[int, str] = {}
    seqs: dict[int, int] = {}
    for element_id in tree.preorder:
        element = tree.nodes[element_id]
        siblings = tree.children[element.parent_id]
        seq = siblings.index(element_id) + 1
        seqs[element_id] = seq
        if element.parent_id is None:
            codes[element_id] = make_code(seq, rule)
        else:
            parent = tree.nodes[element.parent_id]
            renumbered_parent = CodedElement(
                id=parent.id, parent_id=parent.parent_id, wbs_code=codes[parent.id], sibling_seq=seqs[parent.id]
            )
            codes[element_id] = make_code(seq, rule, renumbered_parent)

    last_child_seqs = {
        element_id: max(element.last_child_seq, len(tree.children[element_id]))
        for element_id, element in tree.nodes.items()
    }
    changes = tuple(
        CodeChange(element_id=element_id, old_code=tree.nodes[element_id].wbs_code, new_code=codes[element_id])
        for element_id in tree.preorder
        if tree.nodes[element_id].wbs_code != codes[element_id]
    )
    return RenumberResult(
        codes=MappingProxyType(codes),
        sibling_seqs=MappingProxyType(seqs),
        last_child_seqs=MappingProxyType(last_child_seqs),
        top_last_child_seq=max(top_last, len(tree.children[None])),
        changes=changes,
    )


@dataclass(frozen=True)
class CodeHistoryEntry:
    """WBS ID の新旧対応 1 件（4.1 の ``wbs_code_history`` のうち解決に要る列）。``renumbered_at`` は時差つきの時刻。"""

    id: int
    element_id: int
    old_code: str
    new_code: str
    renumbered_at: datetime


class ResolvedBy(StrEnum):
    """旧 ID の解決で、何に一致したか。"""

    CURRENT = "CURRENT"  # 要素の今の ID
    HISTORY = "HISTORY"  # 新旧対応の旧 ID


@dataclass(frozen=True)
class CodeResolution:
    """WBS ID（旧 ID でもよい）を解決した結果。``wbs_code`` は要素の今の ID。"""

    query: str
    element_id: int
    wbs_code: str
    resolved_by: ResolvedBy


def resolve_code(
    code: str,
    elements: Iterable[CodedElement],
    history: Iterable[CodeHistoryEntry] = (),
) -> CodeResolution | None:
    """WBS ID（旧 ID でもよい）を今の要素へ解決する（5.2 の「旧IDの解決」・BR-02・WBS-06・07）。

    1. 今の ID に一致する要素があればそれを返す（旧 ID が別の要素の今の ID と重なれば、今の ID を優先する）
    2. 無ければ新旧対応を新しい順（``renumbered_at``、同じなら ``id`` の大きい順）にたどり、
       ``old_code`` が最初に一致した対応の要素の今の ID を返す
    3. どちらにも無い、または最初に一致した対応の要素がもう無い（削除した）なら ``None``

    今の ID が同じ要素が 2 つ以上あれば、どれか決められないので ``WbsCodeError``。
    """
    if not isinstance(code, str) or code == "":
        raise WbsCodeError(f"解決する WBS ID が空: {code!r}")
    nodes: dict[int, CodedElement] = {}
    for element in elements:
        if element.id in nodes:
            raise WbsCodeError(f"WBS 要素の ID が重複している: {element.id}")
        nodes[element.id] = element
    current = [element for element in nodes.values() if element.wbs_code == code]
    if len(current) > 1:
        ids = sorted(element.id for element in current)
        raise WbsCodeError(f"WBS ID {code!r} を今持つ要素が複数ある: {ids}")
    if current:
        return CodeResolution(query=code, element_id=current[0].id, wbs_code=code, resolved_by=ResolvedBy.CURRENT)

    entries = list(history)
    for entry in entries:
        if entry.renumbered_at.tzinfo is None or entry.renumbered_at.utcoffset() is None:
            raise WbsCodeError(f"新旧対応 {entry.id} の renumbered_at は時差つき（aware）の時刻で渡す")
    for entry in sorted(entries, key=lambda e: (e.renumbered_at, e.id), reverse=True):
        if entry.old_code != code:
            continue
        element = nodes.get(entry.element_id)
        if element is None:
            return None
        return CodeResolution(
            query=code, element_id=element.id, wbs_code=element.wbs_code, resolved_by=ResolvedBy.HISTORY
        )
    return None
