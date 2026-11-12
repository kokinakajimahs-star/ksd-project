"""変更内容の適用と影響分析（設計書 5.1 の RULE-CHG-001・002、5.9。要求仕様書 CHG-01・02・04）。

- 変更内容の適用（RULE-CHG-001）：``change_requests.operations``（5.9 の操作の配列）を、計画のモデル（DB の写しではない
  純粋なデータ ``PlanModel``）へ**順に**当てる。操作ごとに形の検査・参照の解決・業務ルールの検査を行い、1 件でも不正なら
  その操作の位置（``index``）を付けた ``ChangeError`` で止める（それまでの操作も含めて何も返さない）。

  - 参照の解決：WBS ID（``parent_code``・``code``・``wp_code``）は今の ID、無ければ新旧対応の旧 ID で解決する（RULE-WBS-002）。
    ``ref`` は同じ変更要求の中で新しく作るものの仮の名前で、``wp_ref``（要素）と ``add_dependency`` の端（活動）から引ける
  - 新しく作る要素・活動には**負の仮の ID**（-1, -2, … の作った順）を振る。DB の ID（1 以上）とは重ならない
  - 新しい要素の WBS ID は RULE-WBS-002 の「新規」（親の ``last_child_seq + 1``。削除で空いた番号は再利用しない）、
    表示順は兄弟の最大＋1（API-WBS の要素の追加と同じ）
  - 業務ルール：WP は子を持たない・活動は WP にだけ置ける（RULE-WBS-003）、依存は循環を作らない（RULE-SCH-001）、
    期間 0 の活動がマイルストーン（4.2）

- 影響分析（RULE-CHG-002）：計画の写しに変更を当て、前後の計画それぞれに RULE-SCH-002・003（``calculate_schedule``）と
  RULE-EST-001（``rollup_estimates``）を流し、前後の差を 5.9 の形で返す：``project_finish``（前・後・ずれの稼働日）、
  ``milestones``（ずれたもの）、``critical_path``（加わった・外れた活動）、``total_estimate_hours``（前・後）、
  ``elements``（追加・変更・削除）。**本計画には書き込まない**（モデルは不変のデータで、写しにだけ当てる）。

純粋に保つ（1.3）。DB・HTTP・時刻・乱数に触れない。DB の行と ``operations`` の JSON はサービス層が渡す。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Final

from app.domain.baseline import working_day_difference
from app.domain.calendar import WorkCalendar
from app.domain.estimate import HOURS_QUANTUM, MAX_HOURS, ActivityEstimate, ElementKind, WbsNode, rollup_estimates
from app.domain.scheduling import (
    MAX_ACTIVITY_NAME_LENGTH,
    DependencyGraph,
    Schedule,
    ScheduleActivity,
    calculate_schedule,
)
from app.domain.wbs_code import (
    CodedElement,
    CodeHistoryEntry,
    NumberingRule,
    WbsCodeError,
    display_preorder,
    new_element_code,
    resolve_code,
)
from app.domain.wbs_rules import ActivityRef, check_hierarchy

# WBS 要素の名称の長さの上限（4.1 の wbs_elements.name の列の長さ）
MAX_ELEMENT_NAME_LENGTH: Final = 200

# 5.9 の操作ごとの必須の項目と任意の項目（add_activity は wp_code と wp_ref のどちらか 1 つを別に要る）
_OPERATION_KEYS: Final[Mapping[str, tuple[frozenset[str], frozenset[str]]]] = MappingProxyType(
    {
        "add_element": (frozenset({"parent_code", "kind", "name", "ref"}), frozenset()),
        "update_element": (frozenset({"code", "fields"}), frozenset()),
        "remove_element": (frozenset({"code"}), frozenset()),
        "add_activity": (
            frozenset({"name", "duration_days", "estimate_hours", "ref"}),
            frozenset({"wp_code", "wp_ref", "requested_start"}),
        ),
        "update_activity": (frozenset({"activity_id", "fields"}), frozenset()),
        "remove_activity": (frozenset({"activity_id"}), frozenset()),
        "add_dependency": (frozenset({"predecessor", "successor"}), frozenset()),
        "remove_dependency": (frozenset({"predecessor", "successor"}), frozenset()),
    }
)
OPERATION_NAMES: Final = tuple(_OPERATION_KEYS)
# update_element・update_activity の fields に書ける項目（5.9。責任者は 4.1 の responsible_user_id）
ELEMENT_UPDATE_FIELDS: Final = ("name", "kind", "own_estimate_hours", "responsible_user_id")
ACTIVITY_UPDATE_FIELDS: Final = ("name", "duration_days", "estimate_hours", "requested_start")


class ChangeErrorCode(StrEnum):
    """変更内容を当てられない理由（6.1 のエラーのコード。HTTP の状態はサービス層が決める）。"""

    VALIDATION_ERROR = "VALIDATION_ERROR"  # 操作の形の誤り（未知の op・項目の欠け・未知の項目・型・値の範囲）
    REFERENCE_NOT_FOUND = "REFERENCE_NOT_FOUND"  # WBS ID・仮の参照名・活動の ID が解決できない
    DUPLICATE_REF = "DUPLICATE_REF"  # 同じ仮の参照名を 2 回作った
    WORK_PACKAGE_HAS_CHILDREN = "WORK_PACKAGE_HAS_CHILDREN"  # WP の下に要素を置こうとした（RULE-WBS-003）
    ACTIVITY_NOT_ON_WORK_PACKAGE = "ACTIVITY_NOT_ON_WORK_PACKAGE"  # 活動を WP 以外に置こうとした（RULE-WBS-003）
    SCHEDULE_CYCLE = "SCHEDULE_CYCLE"  # 依存を足すと循環になる（RULE-SCH-001）
    DUPLICATE_DEPENDENCY = "DUPLICATE_DEPENDENCY"  # 同じ組の依存が既にある（4.1 の組の一意）
    DEPENDENCY_NOT_FOUND = "DEPENDENCY_NOT_FOUND"  # 消す依存が無い


class ChangeError(ValueError):
    """変更内容を当てられない。``index`` は操作の位置（0 から。操作に依らない誤りは ``None``）、``details`` は関わる値。"""

    def __init__(
        self, code: ChangeErrorCode, message: str, *, index: int | None = None, details: Sequence[object] = ()
    ) -> None:
        where = "" if index is None else f"操作 {index}: "
        super().__init__(where + message)
        self.code = code
        self.index = index
        self.details = tuple(details)


# ---------------------------------------------------------------------------
# 計画のモデル
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlanElement:
    """WBS 要素（4.1 の ``wbs_elements`` のうち変更内容が触れる列）。新しく作った要素は ``id`` が負。"""

    id: int
    parent_id: int | None
    kind: ElementKind
    name: str
    wbs_code: str
    sibling_seq: int
    last_child_seq: int = 0
    sort_order: int = 0
    own_estimate_hours: Decimal | None = None
    responsible_user_id: int | None = None
    needs_renumber: bool = False


@dataclass(frozen=True)
class PlanActivity:
    """活動（4.1 の ``activities`` のうち利用者の入力の列）。新しく作った活動は ``id`` が負。マイルストーンは期間 0（4.2）。"""

    id: int
    work_package_id: int
    name: str
    duration_days: int
    is_milestone: bool = False
    estimate_hours: Decimal | None = None
    requested_start: date | None = None
    sort_order: int = 0


@dataclass(frozen=True)
class PlanModel:
    """計画のモデル（要素・活動・依存。``dependencies`` は（先行, 後続）の組）。

    - ``numbering_rule``：採番規則（4.1 の ``wbs_numbering_rules``）
    - ``top_last_child_seq``：最上位で今まで使った最大の順番（4.1 の ``wbs_numbering_rules.top_last_child_seq``。CR-003）。
      最上位に要素を足す操作があるのに ``None`` なら止める
    - ``code_history``：WBS ID の新旧対応（旧 ID の解決に使う。4.1 の ``wbs_code_history``）
    """

    elements: tuple[PlanElement, ...]
    activities: tuple[PlanActivity, ...] = ()
    dependencies: tuple[tuple[int, int], ...] = ()
    numbering_rule: NumberingRule = NumberingRule()
    top_last_child_seq: int | None = None
    code_history: tuple[CodeHistoryEntry, ...] = ()


@dataclass(frozen=True)
class AppliedChange:
    """変更内容を当てた結果。``before`` は当てる前のモデル（入力のまま）、``plan`` は当てた後のモデル。

    ``element_refs``・``activity_refs`` は仮の参照名 → 仮の ID（負）。サービス層は ``before`` と ``plan`` を ID で突き合わせて
    追加（負の ID）・変更・削除を保存する。
    """

    before: PlanModel
    plan: PlanModel
    element_refs: Mapping[str, int]
    activity_refs: Mapping[str, int]

    def ref_of(self, new_id: int) -> str | None:
        """仮の ID の仮の参照名（新しく作ったものでなければ ``None``）。"""
        for refs in (self.element_refs, self.activity_refs):
            for ref, value in refs.items():
                if value == new_id:
                    return ref
        return None


# ---------------------------------------------------------------------------
# 値の検査（operations は JSON から来るので、数は int・float・文字列・Decimal を受ける）
# ---------------------------------------------------------------------------


def _fail(index: int, message: str, code: ChangeErrorCode = ChangeErrorCode.VALIDATION_ERROR) -> ChangeError:
    return ChangeError(code, message, index=index)


def _name(value: object, what: str, limit: int, index: int) -> str:
    if not isinstance(value, str) or value.strip() == "" or len(value) > limit:
        raise _fail(index, f"{what} は空でない {limit} 文字以内の文字列: {value!r}")
    return value


def _ref_name(value: object, index: int) -> str:
    if not isinstance(value, str) or value.strip() == "":
        raise _fail(index, f"仮の参照名（ref）は空でない文字列: {value!r}")
    return value


def _code_text(value: object, what: str, index: int) -> str:
    if not isinstance(value, str) or value == "":
        raise _fail(index, f"{what} は空でない WBS ID の文字列: {value!r}")
    return value


def _hours(value: object, what: str, index: int) -> Decimal | None:
    """工数（人時）。``None`` は未見積。JSON の数は文字列を経て ``Decimal`` にする（2 進の誤差を持ち込まない）。"""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float | str | Decimal):
        raise _fail(index, f"{what} は数: {value!r}")
    try:
        hours = Decimal(str(value)) if isinstance(value, float | str) else Decimal(value)
    except InvalidOperation:
        raise _fail(index, f"{what} は数: {value!r}") from None
    if not hours.is_finite() or hours < 0 or hours > MAX_HOURS:
        raise _fail(index, f"{what} は 0 以上 {MAX_HOURS} 以下: {value!r}")
    if hours != hours.quantize(HOURS_QUANTUM):
        raise _fail(index, f"{what} は小数 2 桁まで（2.4 の Numeric(10,2)）: {value!r}")
    return hours


def _duration(value: object, index: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(index, f"期間（duration_days）は 0 以上の整数（稼働日）: {value!r}")
    return value


def _requested_start(value: object, index: int) -> date | None:
    if value is None or isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise _fail(index, f"希望開始日（requested_start）は YYYY-MM-DD の日付: {value!r}")


def _kind(value: object, index: int) -> ElementKind:
    try:
        return ElementKind(value)
    except ValueError:
        allowed = "・".join(kind.value for kind in ElementKind)
        raise _fail(index, f"区分（kind）は {allowed} のいずれか: {value!r}") from None


def _user_id(value: object, index: int) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _fail(index, f"責任者（responsible_user_id）は 1 以上の整数か null: {value!r}")
    return value


def _existing_activity_id(value: object, index: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _fail(index, f"活動の ID（activity_id）は 1 以上の整数: {value!r}")
    return value


def _fields(raw: object, allowed: Sequence[str], index: int) -> Mapping[str, Any]:
    if not isinstance(raw, Mapping) or not raw:
        raise _fail(index, f"fields は {'・'.join(allowed)} のうち 1 つ以上を持つオブジェクト: {raw!r}")
    unknown = sorted(str(key) for key in raw if key not in allowed)
    if unknown:
        raise _fail(index, f"fields に変えられない項目がある（変えられるのは {'・'.join(allowed)}）: {unknown}")
    return raw


def _check_keys(operation: Mapping[str, Any], index: int) -> str:
    op = operation.get("op")
    if not isinstance(op, str) or op not in _OPERATION_KEYS:
        raise _fail(index, f"op は {'・'.join(OPERATION_NAMES)} のいずれか: {op!r}")
    required, optional = _OPERATION_KEYS[op]
    keys = set(operation) - {"op"}
    missing = sorted(required - keys)
    if missing:
        raise _fail(index, f"{op} に必須の項目が無い: {missing}")
    unknown = sorted(str(key) for key in keys - required - optional)
    if unknown:
        raise _fail(index, f"{op} に無い項目がある: {unknown}")
    if op == "add_activity" and (("wp_code" in keys) == ("wp_ref" in keys)):
        raise _fail(index, "add_activity は wp_code と wp_ref のどちらか 1 つを持つ")
    return str(op)


# ---------------------------------------------------------------------------
# 当てる途中の状態
# ---------------------------------------------------------------------------


class _Working:
    """当てる途中の計画（要素・活動は挿入順を保つ辞書、依存は挿入順を保つ辞書のキー）。"""

    def __init__(self, plan: PlanModel) -> None:
        self.elements: dict[int, PlanElement] = {}
        for element in plan.elements:
            if isinstance(element.id, bool) or not isinstance(element.id, int) or element.id < 1:
                raise ChangeError(ChangeErrorCode.VALIDATION_ERROR, f"計画の WBS 要素の ID は 1 以上の整数: {element.id!r}")
            if element.id in self.elements:
                raise ChangeError(ChangeErrorCode.VALIDATION_ERROR, f"計画の WBS 要素の ID が重複している: {element.id}")
            try:
                kind = ElementKind(element.kind)
            except ValueError:
                raise ChangeError(
                    ChangeErrorCode.VALIDATION_ERROR, f"計画の WBS 要素 {element.id} の区分が不正: {element.kind!r}"
                ) from None
            self.elements[element.id] = replace(element, kind=kind)
        self.activities: dict[int, PlanActivity] = {}
        for activity in plan.activities:
            if isinstance(activity.id, bool) or not isinstance(activity.id, int) or activity.id < 1:
                raise ChangeError(ChangeErrorCode.VALIDATION_ERROR, f"計画の活動の ID は 1 以上の整数: {activity.id!r}")
            if activity.id in self.activities:
                raise ChangeError(ChangeErrorCode.VALIDATION_ERROR, f"計画の活動の ID が重複している: {activity.id}")
            self.activities[activity.id] = activity
        self.dependencies: dict[tuple[int, int], None] = {}
        for pair in plan.dependencies:
            predecessor_id, successor_id = pair
            if predecessor_id not in self.activities or successor_id not in self.activities:
                raise ChangeError(ChangeErrorCode.VALIDATION_ERROR, f"計画の依存 {pair} の活動が計画に無い")
            if (predecessor_id, successor_id) in self.dependencies:
                raise ChangeError(ChangeErrorCode.VALIDATION_ERROR, f"計画の依存 {pair} が重複している")
            self.dependencies[(predecessor_id, successor_id)] = None
        self.rule = plan.numbering_rule
        self.top_last_child_seq = plan.top_last_child_seq
        self.history = plan.code_history
        self.element_refs: dict[str, int] = {}
        self.activity_refs: dict[str, int] = {}
        self._next_id = -1

    def new_id(self) -> int:
        new_id = self._next_id
        self._next_id -= 1
        return new_id

    def claim_ref(self, ref: str, index: int) -> None:
        if ref in self.element_refs or ref in self.activity_refs:
            raise _fail(index, f"仮の参照名 {ref!r} は既に使われている", ChangeErrorCode.DUPLICATE_REF)

    def coded(self, element: PlanElement) -> CodedElement:
        return CodedElement(
            id=element.id,
            parent_id=element.parent_id,
            wbs_code=element.wbs_code,
            sibling_seq=element.sibling_seq,
            last_child_seq=element.last_child_seq,
            sort_order=element.sort_order,
            needs_renumber=element.needs_renumber,
        )

    def resolve_element(self, code: str, index: int) -> PlanElement:
        try:
            found = resolve_code(code, (self.coded(e) for e in self.elements.values()), self.history)
        except WbsCodeError as exc:
            raise _fail(index, str(exc)) from None
        if found is None:
            raise _fail(index, f"WBS ID {code!r} の要素が無い", ChangeErrorCode.REFERENCE_NOT_FOUND)
        return self.elements[found.element_id]

    def resolve_activity(self, end: object, index: int) -> int:
        """依存の端：活動の ID（1 以上の整数）か、この変更要求で作った活動の仮の参照名。"""
        if isinstance(end, str):
            if end not in self.activity_refs:
                raise _fail(index, f"活動の仮の参照名 {end!r} が無い", ChangeErrorCode.REFERENCE_NOT_FOUND)
            return self.activity_refs[end]
        activity_id = _existing_activity_id(end, index)
        if activity_id not in self.activities:
            raise _fail(index, f"活動 {activity_id} が無い", ChangeErrorCode.REFERENCE_NOT_FOUND)
        return activity_id

    def existing_activity(self, value: object, index: int) -> PlanActivity:
        activity_id = _existing_activity_id(value, index)
        found = self.activities.get(activity_id)
        if found is None:
            raise _fail(index, f"活動 {activity_id} が無い", ChangeErrorCode.REFERENCE_NOT_FOUND)
        return found

    def children_of(self, element_id: int | None) -> list[PlanElement]:
        return [e for e in self.elements.values() if e.parent_id == element_id]

    def activities_on(self, element_id: int) -> list[PlanActivity]:
        return [a for a in self.activities.values() if a.work_package_id == element_id]

    def subtree(self, element_id: int) -> list[int]:
        result: list[int] = []
        stack = [element_id]
        while stack:
            current = stack.pop()
            result.append(current)
            stack.extend(child.id for child in self.children_of(current))
        return result

    def drop_activity(self, activity_id: int) -> None:
        del self.activities[activity_id]
        for pair in [p for p in self.dependencies if activity_id in p]:
            del self.dependencies[pair]

    def model(self) -> PlanModel:
        return PlanModel(
            elements=tuple(self.elements.values()),
            activities=tuple(self.activities.values()),
            dependencies=tuple(self.dependencies),
            numbering_rule=self.rule,
            top_last_child_seq=self.top_last_child_seq,
            code_history=self.history,
        )


# ---------------------------------------------------------------------------
# 操作ごとの当て方
# ---------------------------------------------------------------------------


def _add_element(w: _Working, op: Mapping[str, Any], index: int) -> None:
    parent_code = op["parent_code"]
    if parent_code is not None and not isinstance(parent_code, str):
        raise _fail(index, f"parent_code は WBS ID の文字列（ルートは空）: {parent_code!r}")
    kind = _kind(op["kind"], index)
    name = _name(op["name"], "要素の名称（name）", MAX_ELEMENT_NAME_LENGTH, index)
    ref = _ref_name(op["ref"], index)
    w.claim_ref(ref, index)
    parent = w.resolve_element(parent_code, index) if parent_code else None
    if parent is not None and parent.kind is ElementKind.WORK_PACKAGE:
        raise _fail(
            index,
            f"ワークパッケージ {parent.wbs_code} の下に要素は置けない",
            ChangeErrorCode.WORK_PACKAGE_HAS_CHILDREN,
        )
    try:
        code = new_element_code(
            None if parent is None else w.coded(parent),
            w.rule,
            top_last_child_seq=w.top_last_child_seq if parent is None else None,
        )
    except WbsCodeError as exc:
        raise _fail(index, str(exc)) from None
    parent_id = None if parent is None else parent.id
    siblings = w.children_of(parent_id)
    new_id = w.new_id()
    if parent is None:
        w.top_last_child_seq = code.parent_last_child_seq
    else:
        w.elements[parent.id] = replace(parent, last_child_seq=code.parent_last_child_seq)
    w.elements[new_id] = PlanElement(
        id=new_id,
        parent_id=parent_id,
        kind=kind,
        name=name,
        wbs_code=code.wbs_code,
        sibling_seq=code.sibling_seq,
        last_child_seq=0,
        sort_order=max((e.sort_order for e in siblings), default=0) + 1,
        needs_renumber=code.needs_renumber,
    )
    w.element_refs[ref] = new_id


def _update_element(w: _Working, op: Mapping[str, Any], index: int) -> None:
    element = w.resolve_element(_code_text(op["code"], "code", index), index)
    fields = _fields(op["fields"], ELEMENT_UPDATE_FIELDS, index)
    changes: dict[str, Any] = {}
    if "name" in fields:
        changes["name"] = _name(fields["name"], "要素の名称（name）", MAX_ELEMENT_NAME_LENGTH, index)
    if "own_estimate_hours" in fields:
        changes["own_estimate_hours"] = _hours(fields["own_estimate_hours"], "自分の見積（own_estimate_hours）", index)
    if "responsible_user_id" in fields:
        changes["responsible_user_id"] = _user_id(fields["responsible_user_id"], index)
    if "kind" in fields:
        kind = _kind(fields["kind"], index)
        if kind is ElementKind.WORK_PACKAGE and w.children_of(element.id):
            raise _fail(
                index,
                f"子を持つ要素 {element.wbs_code} はワークパッケージにできない",
                ChangeErrorCode.WORK_PACKAGE_HAS_CHILDREN,
            )
        if kind is not ElementKind.WORK_PACKAGE and w.activities_on(element.id):
            raise _fail(
                index,
                f"活動を持つ要素 {element.wbs_code} はワークパッケージ以外にできない",
                ChangeErrorCode.ACTIVITY_NOT_ON_WORK_PACKAGE,
            )
        changes["kind"] = kind
    w.elements[element.id] = replace(element, **changes)


def _remove_element(w: _Working, op: Mapping[str, Any], index: int) -> None:
    element = w.resolve_element(_code_text(op["code"], "code", index), index)
    removed = w.subtree(element.id)
    for activity in [a for a in w.activities.values() if a.work_package_id in removed]:
        w.drop_activity(activity.id)
    for element_id in removed:
        del w.elements[element_id]
    # 消した要素の仮の参照名は名前を使ったまま残す（後の操作から引くと「無い」。同じ名前を作り直すと DUPLICATE_REF）


def _add_activity(w: _Working, op: Mapping[str, Any], index: int) -> None:
    if "wp_code" in op:
        wp = w.resolve_element(_code_text(op["wp_code"], "wp_code", index), index)
    else:
        wp_ref = _ref_name(op["wp_ref"], index)
        wp_id = w.element_refs.get(wp_ref)
        if wp_id is None or wp_id not in w.elements:
            raise _fail(index, f"要素の仮の参照名 {wp_ref!r} が無い", ChangeErrorCode.REFERENCE_NOT_FOUND)
        wp = w.elements[wp_id]
    if wp.kind is not ElementKind.WORK_PACKAGE:
        raise _fail(
            index,
            f"活動はワークパッケージにだけ置ける。{wp.wbs_code} は {wp.kind}",
            ChangeErrorCode.ACTIVITY_NOT_ON_WORK_PACKAGE,
        )
    name = _name(op["name"], "活動の名称（name）", MAX_ACTIVITY_NAME_LENGTH, index)
    duration = _duration(op["duration_days"], index)
    hours = _hours(op["estimate_hours"], "見積（estimate_hours）", index)
    start = _requested_start(op.get("requested_start"), index)
    ref = _ref_name(op["ref"], index)
    w.claim_ref(ref, index)
    new_id = w.new_id()
    w.activities[new_id] = PlanActivity(
        id=new_id,
        work_package_id=wp.id,
        name=name,
        duration_days=duration,
        is_milestone=duration == 0,
        estimate_hours=hours,
        requested_start=start,
        sort_order=max((a.sort_order for a in w.activities_on(wp.id)), default=0) + 1,
    )
    w.activity_refs[ref] = new_id


def _update_activity(w: _Working, op: Mapping[str, Any], index: int) -> None:
    activity = w.existing_activity(op["activity_id"], index)
    fields = _fields(op["fields"], ACTIVITY_UPDATE_FIELDS, index)
    changes: dict[str, Any] = {}
    if "name" in fields:
        changes["name"] = _name(fields["name"], "活動の名称（name）", MAX_ACTIVITY_NAME_LENGTH, index)
    if "duration_days" in fields:
        duration = _duration(fields["duration_days"], index)
        changes["duration_days"] = duration
        changes["is_milestone"] = duration == 0
    if "estimate_hours" in fields:
        changes["estimate_hours"] = _hours(fields["estimate_hours"], "見積（estimate_hours）", index)
    if "requested_start" in fields:
        changes["requested_start"] = _requested_start(fields["requested_start"], index)
    w.activities[activity.id] = replace(activity, **changes)


def _remove_activity(w: _Working, op: Mapping[str, Any], index: int) -> None:
    activity = w.existing_activity(op["activity_id"], index)
    w.drop_activity(activity.id)


def _add_dependency(w: _Working, op: Mapping[str, Any], index: int) -> None:
    predecessor = w.resolve_activity(op["predecessor"], index)
    successor = w.resolve_activity(op["successor"], index)
    if (predecessor, successor) in w.dependencies:
        raise _fail(
            index, f"依存 {predecessor}→{successor} は既にある", ChangeErrorCode.DUPLICATE_DEPENDENCY
        )
    graph = DependencyGraph(w.activities, w.dependencies)
    cycle = graph.cycle_if_added(predecessor, successor)
    if cycle:
        raise ChangeError(
            ChangeErrorCode.SCHEDULE_CYCLE,
            f"依存 {predecessor}→{successor} を足すと循環になる（循環に含まれる活動: {list(cycle)}）",
            index=index,
            details=cycle,
        )
    w.dependencies[(predecessor, successor)] = None


def _remove_dependency(w: _Working, op: Mapping[str, Any], index: int) -> None:
    predecessor = w.resolve_activity(op["predecessor"], index)
    successor = w.resolve_activity(op["successor"], index)
    if (predecessor, successor) not in w.dependencies:
        raise _fail(index, f"依存 {predecessor}→{successor} が無い", ChangeErrorCode.DEPENDENCY_NOT_FOUND)
    del w.dependencies[(predecessor, successor)]


_APPLY = MappingProxyType(
    {
        "add_element": _add_element,
        "update_element": _update_element,
        "remove_element": _remove_element,
        "add_activity": _add_activity,
        "update_activity": _update_activity,
        "remove_activity": _remove_activity,
        "add_dependency": _add_dependency,
        "remove_dependency": _remove_dependency,
    }
)


# RULE-CHG-001 変更内容の適用
def apply_operations(plan: PlanModel, operations: Sequence[Mapping[str, Any]]) -> AppliedChange:
    """変更内容（5.9 の操作の列）を計画のモデルへ順に当てる（CHG-01・CHG-04）。

    - 操作の形（op・必須の項目・未知の項目・値の型と範囲）を検査する。誤りは ``VALIDATION_ERROR``
    - WBS ID は今の ID か旧 ID（新旧対応）で、``ref`` は同じ変更要求の中で前の操作が作ったもので解決する。
      無ければ ``REFERENCE_NOT_FOUND``、同じ ``ref`` を 2 回作れば ``DUPLICATE_REF``
    - WP の下に要素を置く・子を持つ要素を WP にするのは ``WORK_PACKAGE_HAS_CHILDREN``、活動を WP 以外に置く・
      活動を持つ WP を WP 以外にするのは ``ACTIVITY_NOT_ON_WORK_PACKAGE``（RULE-WBS-003）
    - 依存は、両端の活動があり、同じ組が無く（``DUPLICATE_DEPENDENCY``）、足しても循環しない（``SCHEDULE_CYCLE``。
      ``details`` は循環の活動）ときだけ足す。消す依存が無ければ ``DEPENDENCY_NOT_FOUND``
    - 要素を消すと配下の要素・その活動・活動の依存も消える。活動を消すと依存も消える

    入力の ``plan`` は変えない（不変のデータ）。当てた後の木を RULE-WBS-003 で検査し直す（食い違いは ``ChangeError``）。
    """
    if isinstance(operations, str | bytes) or not isinstance(operations, Sequence):
        raise ChangeError(ChangeErrorCode.VALIDATION_ERROR, f"変更内容（operations）は操作の配列: {operations!r}")
    working = _Working(plan)
    for index, operation in enumerate(operations):
        if not isinstance(operation, Mapping):
            raise _fail(index, f"操作はオブジェクト: {operation!r}")
        op = _check_keys(operation, index)
        _APPLY[op](working, operation, index)

    after = working.model()
    issues = check_hierarchy(
        (WbsNode(id=e.id, parent_id=e.parent_id, kind=e.kind) for e in after.elements),
        (ActivityRef(id=a.id, work_package_id=a.work_package_id) for a in after.activities),
    )
    if issues:
        raise ChangeError(ChangeErrorCode.VALIDATION_ERROR, issues[0].message, details=issues)
    return AppliedChange(
        before=plan,
        plan=after,
        element_refs=MappingProxyType(dict(working.element_refs)),
        activity_refs=MappingProxyType(dict(working.activity_refs)),
    )


# ---------------------------------------------------------------------------
# RULE-CHG-002 影響分析
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FinishImpact:
    """プロジェクトの終了日の前・後とずれ（稼働日。後 − 前、正は遅れ）。活動が無ければ日付とずれは ``None``。"""

    before: date | None
    after: date | None
    shift_days: int | None


@dataclass(frozen=True)
class MilestoneShift:
    """日付がずれたマイルストーン（前後の両方にあるもの）。"""

    activity_id: int
    name: str
    before: date
    after: date
    shift_days: int


@dataclass(frozen=True)
class ActivityLabel:
    """結果に出す活動。新しく作った活動は ``activity_id`` が ``None`` で ``ref`` を持つ。"""

    activity_id: int | None
    ref: str | None
    name: str


@dataclass(frozen=True)
class CriticalPathImpact:
    """クリティカルパスに加わった活動と外れた活動（それぞれ後・前の計画のトポロジカル順）。"""

    added: tuple[ActivityLabel, ...]
    removed: tuple[ActivityLabel, ...]


@dataclass(frozen=True)
class EstimateImpact:
    """プロジェクトの総見積（RULE-EST-001 のプロジェクトの値。人時）の前・後。"""

    before: Decimal
    after: Decimal

    @property
    def difference(self) -> Decimal:
        return self.after - self.before


@dataclass(frozen=True)
class ElementLabel:
    """結果に出す要素。新しく作った要素は ``element_id`` が ``None`` で ``ref`` を持つ。"""

    element_id: int | None
    ref: str | None
    wbs_code: str
    name: str
    kind: ElementKind


@dataclass(frozen=True)
class ElementUpdate:
    """変わった要素と、変わった項目ごとの（前, 後）。"""

    element_id: int
    wbs_code: str
    name: str
    changes: Mapping[str, tuple[object, object]]


@dataclass(frozen=True)
class ElementImpact:
    """要素の追加（作った順）・変更（後の WBS の並び）・削除（前の WBS の並び。配下を含む）。"""

    added: tuple[ElementLabel, ...]
    updated: tuple[ElementUpdate, ...]
    removed: tuple[ElementLabel, ...]


@dataclass(frozen=True)
class ImpactAnalysis:
    """影響分析の結果（5.9 の形）。``applied`` は当てた結果（本計画には書かない）。"""

    project_finish: FinishImpact
    milestones: tuple[MilestoneShift, ...]
    critical_path: CriticalPathImpact
    total_estimate_hours: EstimateImpact
    elements: ElementImpact
    applied: AppliedChange

    def to_json(self) -> dict[str, Any]:
        """``change_requests.impact`` に保存する JSON の値（日付は ISO 8601、工数は小数 2 桁の文字列）。"""

        def day(value: date | None) -> str | None:
            return None if value is None else value.isoformat()

        def value_of(value: object) -> object:
            if isinstance(value, Decimal):
                return str(value.quantize(HOURS_QUANTUM))
            if isinstance(value, StrEnum):
                return value.value
            return value

        def activity(label: ActivityLabel) -> dict[str, Any]:
            return {"activity_id": label.activity_id, "ref": label.ref, "name": label.name}

        def element(label: ElementLabel) -> dict[str, Any]:
            return {
                "element_id": label.element_id,
                "ref": label.ref,
                "wbs_code": label.wbs_code,
                "name": label.name,
                "kind": label.kind.value,
            }

        return {
            "project_finish": {
                "before": day(self.project_finish.before),
                "after": day(self.project_finish.after),
                "shift_days": self.project_finish.shift_days,
            },
            "milestones": [
                {
                    "activity_id": m.activity_id,
                    "name": m.name,
                    "before": m.before.isoformat(),
                    "after": m.after.isoformat(),
                    "shift_days": m.shift_days,
                }
                for m in self.milestones
            ],
            "critical_path": {
                "added": [activity(a) for a in self.critical_path.added],
                "removed": [activity(a) for a in self.critical_path.removed],
            },
            "total_estimate_hours": {
                "before": value_of(self.total_estimate_hours.before),
                "after": value_of(self.total_estimate_hours.after),
            },
            "elements": {
                "added": [element(e) for e in self.elements.added],
                "updated": [
                    {
                        "element_id": u.element_id,
                        "wbs_code": u.wbs_code,
                        "name": u.name,
                        "changes": {
                            key: {"before": value_of(old), "after": value_of(new)}
                            for key, (old, new) in u.changes.items()
                        },
                    }
                    for u in self.elements.updated
                ],
                "removed": [element(e) for e in self.elements.removed],
            },
        }


def _schedule(plan: PlanModel, calendar: WorkCalendar, project_start: date) -> Schedule:
    return calculate_schedule(
        (
            ScheduleActivity(
                id=a.id, duration_days=a.duration_days, is_milestone=a.is_milestone, requested_start=a.requested_start
            )
            for a in plan.activities
        ),
        plan.dependencies,
        calendar=calendar,
        project_start=project_start,
    )


def _total_estimate(plan: PlanModel) -> Decimal:
    rollup = rollup_estimates(
        (
            WbsNode(id=e.id, parent_id=e.parent_id, kind=e.kind, own_estimate_hours=e.own_estimate_hours)
            for e in plan.elements
        ),
        (ActivityEstimate(id=a.id, work_package_id=a.work_package_id, estimate_hours=a.estimate_hours)
         for a in plan.activities),
    )
    return rollup.project_hours


def _preorder(plan: PlanModel) -> tuple[int, ...]:
    return display_preorder(
        CodedElement(id=e.id, parent_id=e.parent_id, wbs_code=e.wbs_code, sibling_seq=e.sibling_seq,
                     sort_order=e.sort_order)
        for e in plan.elements
    )


def _shift(calendar: WorkCalendar, before: date | None, after: date | None) -> int | None:
    if before is None or after is None:
        return None
    return working_day_difference(calendar, before, after)


# RULE-CHG-002 影響分析
def analyze_impact(
    plan: PlanModel,
    operations: Sequence[Mapping[str, Any]],
    *,
    calendar: WorkCalendar,
    project_start: date,
) -> ImpactAnalysis:
    """計画の写しに変更内容を当てて再計算し、前後の差を返す（5.9・CHG-02）。本計画（``plan``）は変えない。

    - 当て方は RULE-CHG-001（``apply_operations``）。当てられなければその ``ChangeError``
    - 前後の計画それぞれに RULE-SCH-002・003（``calculate_schedule``）と RULE-EST-001（``rollup_estimates``）を流す。
      循環があれば ``ScheduleCycleError``
    - ``project_finish``：終了日の前・後と、ずれの稼働日（``[前, 後)`` の稼働日の数。前へずれたら負）
    - ``milestones``：前後の両方にあって日付の変わったマイルストーン（後の計画のトポロジカル順）
    - ``critical_path``：後にだけクリティカルな活動（加わった）と、前にだけクリティカルな活動（外れた。消した活動を含む）
    - ``total_estimate_hours``：プロジェクトの総見積の前・後
    - ``elements``：追加した要素、``update_element`` の項目（名称・区分・自分の見積・責任者）が変わった要素、消した要素
    """
    applied = apply_operations(plan, operations)
    after = applied.plan
    before_names = {a.id: a.name for a in plan.activities}
    after_names = {a.id: a.name for a in after.activities}
    before_schedule = _schedule(plan, calendar, project_start)
    after_schedule = _schedule(after, calendar, project_start)

    finish = FinishImpact(
        before=before_schedule.project_finish_date,
        after=after_schedule.project_finish_date,
        shift_days=_shift(calendar, before_schedule.project_finish_date, after_schedule.project_finish_date),
    )

    milestones: list[MilestoneShift] = []
    for activity_id in after_schedule.order:
        now = after_schedule.activities[activity_id]
        was = before_schedule.activities.get(activity_id)
        if not now.is_milestone or was is None or not was.is_milestone or was.finish_date == now.finish_date:
            continue
        milestones.append(
            MilestoneShift(
                activity_id=activity_id,
                name=after_names[activity_id],
                before=was.finish_date,
                after=now.finish_date,
                shift_days=working_day_difference(calendar, was.finish_date, now.finish_date),
            )
        )

    def activity_label(names: Mapping[int, str], activity_id: int) -> ActivityLabel:
        if activity_id < 0:
            return ActivityLabel(activity_id=None, ref=applied.ref_of(activity_id), name=names[activity_id])
        return ActivityLabel(activity_id=activity_id, ref=None, name=names[activity_id])

    before_critical = set(before_schedule.critical_path)
    after_critical = set(after_schedule.critical_path)
    critical = CriticalPathImpact(
        added=tuple(activity_label(after_names, i) for i in after_schedule.critical_path if i not in before_critical),
        removed=tuple(activity_label(before_names, i) for i in before_schedule.critical_path if i not in after_critical),
    )

    estimate = EstimateImpact(before=_total_estimate(plan), after=_total_estimate(after))

    before_elements = {e.id: e for e in plan.elements}
    after_elements = {e.id: e for e in after.elements}
    added = tuple(
        ElementLabel(element_id=None, ref=applied.ref_of(e.id), wbs_code=e.wbs_code, name=e.name, kind=e.kind)
        for e in sorted((e for e in after.elements if e.id < 0), key=lambda e: -e.id)
    )
    updated: list[ElementUpdate] = []
    for element_id in _preorder(after):
        if element_id < 0:
            continue
        old, new = before_elements[element_id], after_elements[element_id]
        changes = {
            name: (getattr(old, name), getattr(new, name))
            for name in ELEMENT_UPDATE_FIELDS
            if getattr(old, name) != getattr(new, name)
        }
        if changes:
            updated.append(
                ElementUpdate(element_id=element_id, wbs_code=new.wbs_code, name=new.name,
                              changes=MappingProxyType(changes))
            )
    removed = tuple(
        ElementLabel(element_id=e.id, ref=None, wbs_code=e.wbs_code, name=e.name, kind=e.kind)
        for e in (before_elements[i] for i in _preorder(plan))
        if e.id not in after_elements
    )

    return ImpactAnalysis(
        project_finish=finish,
        milestones=tuple(milestones),
        critical_path=critical,
        total_estimate_hours=estimate,
        elements=ElementImpact(added=added, updated=tuple(updated), removed=removed),
        applied=applied,
    )
