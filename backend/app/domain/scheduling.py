"""日程計算：依存の循環検出・前進計算・後退計算とクリティカルパス・一括更新の検査
（設計書 5.1 の RULE-SCH-001・002・003・006、5.5。要求仕様書 7.5 BR-05、SCH-02・04・06・09・10、NFR-01）。

活動を稼働日の軸（5.4・``app.domain.calendar.WorkdayAxis``。原点はプロジェクトの開始日）の上の区間で扱う。
活動 a は開始位置 ``S_a`` と終了位置 ``F_a = S_a + d_a`` を持ち、マイルストーン m は 1 つの位置 ``M_m``（その日の終業）を持つ。

- 循環検出（RULE-SCH-001）：依存の有向グラフの強連結成分から、循環に含まれる活動を返す。依存を足す前は、
  足すと循環になるかを後続から先行への経路で判定し、その経路の活動を返す
- 前進計算（RULE-SCH-002）：トポロジカル順（同順位は活動の id の昇順）に::

      下限(a) = index(a.requested_start ?? project.start_date)            … 活動
      下限(m) = index(m.requested_start ?? project.start_date) + 1        … マイルストーン（その日の終業）
      ES(a) = max(下限(a), max{ EF(p) | p は先行 })   EF(a) = ES(a) + d_a
      M(m)  = max(下限(m), max{ EF(p) | p は先行 })   ※先行がマイルストーンなら EF(p) = M(p)

- 後退計算（RULE-SCH-003）：``E = max(EF の全て)``。後続の無い活動は ``LF = E``、ある活動は ``LF = min{ LS(s) }``、
  ``LS = LF − d``。マイルストーンは ``LM = min{ LS(s) }``（後続が無ければ E）。``total_float = LS − ES``、``≤ 0`` をクリティカルにする
- 結果の日付：開始日＝添字 ``ES`` の稼働日、終了日＝添字 ``EF − 1`` の稼働日、マイルストーンの日付＝添字 ``M − 1`` の稼働日
- 一括更新の検査（RULE-SCH-006）：複数の活動の変更を全て検査してから、変更後の活動と依存の全体を返す。
  1 件でも不正なら何も返さず、見つけた不正の全てを持つ ``BulkUpdateError`` で拒む

R1 の依存は終了→開始（FS）・ラグ 0 だけ（BR-05・OQ-04）。それ以外の入力は推測せずに拒む。
純粋に保つ（1.3）。DB・HTTP・時刻・乱数に触れない。DB の行はサービス層が下のデータクラスへ直して渡す。
計算は O(活動＋依存)（同順位を id の昇順に決める優先度つきの列の分だけ O(活動 log 活動) を足す。NFR-01）。
"""

from __future__ import annotations

import heapq
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from app.domain.calendar import WorkCalendar, WorkdayAxis

# R1 の依存の種類とラグ（4.1 の activity_dependencies.dep_type・lag_days。BR-05・OQ-04）
DEPENDENCY_TYPE_FS: Final = "FS"
DEPENDENCY_LAG_R1: Final = 0
# 活動の名称の長さの上限（4.1 の activities.name の列の長さ。docs/decisions/ASSUMPTIONS.md A-19）
MAX_ACTIVITY_NAME_LENGTH: Final = 200


class SchedulingError(ValueError):
    """日程計算の入力が不正（未定義の入力は推測せずに拒む）。"""


class ScheduleCycleError(SchedulingError):
    """依存が循環している（5.5：計算せず、循環に含まれる活動の一覧を ``SCHEDULE_CYCLE`` で返す。6.1 の 422）。"""

    code: Final = "SCHEDULE_CYCLE"

    def __init__(self, activity_ids: Sequence[int]) -> None:
        self.activity_ids: tuple[int, ...] = tuple(activity_ids)
        super().__init__(f"依存が循環している（循環に含まれる活動: {list(self.activity_ids)}）")


def _check_id(value: object, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SchedulingError(f"{what} は整数: {value!r}")
    return value


# ---------------------------------------------------------------------------
# 入力
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Dependency:
    """依存（4.1 の ``activity_dependencies`` の 1 行）。R1 は終了→開始（FS）・ラグ 0 だけを受ける。"""

    predecessor_id: int
    successor_id: int
    dep_type: str = DEPENDENCY_TYPE_FS
    lag_days: int = DEPENDENCY_LAG_R1

    def __post_init__(self) -> None:
        _check_id(self.predecessor_id, "先行の活動の ID")
        _check_id(self.successor_id, "後続の活動の ID")
        if self.dep_type != DEPENDENCY_TYPE_FS:
            raise SchedulingError(f"R1 の依存は終了→開始（FS）だけ（BR-05・OQ-04）: {self.dep_type!r}")
        if isinstance(self.lag_days, bool) or self.lag_days != DEPENDENCY_LAG_R1:
            raise SchedulingError(f"R1 の依存のラグは 0 だけ（BR-05・OQ-04）: {self.lag_days!r}")

    @property
    def pair(self) -> tuple[int, int]:
        return (self.predecessor_id, self.successor_id)


DependencyInput = Dependency | tuple[int, int]


def _as_dependency(dep: DependencyInput) -> Dependency:
    if isinstance(dep, Dependency):
        return dep
    predecessor_id, successor_id = dep
    return Dependency(predecessor_id=predecessor_id, successor_id=successor_id)


@dataclass(frozen=True)
class ScheduleActivity:
    """日程計算の入力になる活動（4.1 の ``activities`` のうち利用者の入力の列。4.2）。

    ``duration_days`` は稼働日の期間。マイルストーンは期間 0、それ以外は 1 以上（BR-04・4.2）。
    ``requested_start`` は希望開始日（空ならプロジェクトの開始日。5.5）。
    """

    id: int
    duration_days: int
    is_milestone: bool = False
    requested_start: date | None = None

    def __post_init__(self) -> None:
        _check_id(self.id, "活動の ID")
        d = self.duration_days
        if isinstance(d, bool) or not isinstance(d, int) or d < 0:
            raise SchedulingError(f"活動 {self.id} の期間（稼働日）は 0 以上の整数: {d!r}")
        if bool(self.is_milestone) != (d == 0):
            raise SchedulingError(
                f"活動 {self.id}：期間 0 の活動がマイルストーンで、マイルストーンの期間は 0（BR-04・4.2）"
                f"（is_milestone={self.is_milestone!r}, duration_days={d}）"
            )


# ---------------------------------------------------------------------------
# RULE-SCH-001 依存の循環検出
# ---------------------------------------------------------------------------


# RULE-SCH-001 依存の循環検出
class DependencyGraph:
    """活動を頂点、依存（先行→後続）を辺とする有向グラフ。

    - ``cycles()``：循環（強連結成分のうち 2 つ以上の活動を含むもの、または自分への依存）ごとの活動の ID
    - ``cycle_activity_ids()``：循環に含まれる活動の ID（昇順）。日程計算の前に使う（5.5）
    - ``cycle_if_added(p, s)``：依存 p→s を足すと循環になるなら、その循環の活動（s から p への経路。s, …, p の順）。
      ならなければ空。依存を足す前に使う（SCH-04）
    - ``topological_order()``：トポロジカル順（同順位は活動の id の昇順）。循環があれば ``ScheduleCycleError``

    両端が入力の活動に無い依存と、同じ組の依存の重複は入力の誤りとして拒む（4.1 の組の一意）。
    """

    def __init__(self, activity_ids: Iterable[int], dependencies: Iterable[DependencyInput] = ()) -> None:
        ids: list[int] = []
        seen: set[int] = set()
        for activity_id in activity_ids:
            _check_id(activity_id, "活動の ID")
            if activity_id in seen:
                raise SchedulingError(f"活動の ID が重複している: {activity_id}")
            seen.add(activity_id)
            ids.append(activity_id)
        self._ids: tuple[int, ...] = tuple(sorted(ids))
        succ: dict[int, list[int]] = {i: [] for i in self._ids}
        pred: dict[int, list[int]] = {i: [] for i in self._ids}
        pairs: set[tuple[int, int]] = set()
        for raw in dependencies:
            dep = _as_dependency(raw)
            for end in dep.pair:
                if end not in succ:
                    raise SchedulingError(f"依存 {dep.predecessor_id}→{dep.successor_id} の活動 {end} が入力に無い")
            if dep.pair in pairs:
                raise SchedulingError(f"依存 {dep.predecessor_id}→{dep.successor_id} が重複している")
            pairs.add(dep.pair)
            succ[dep.predecessor_id].append(dep.successor_id)
            pred[dep.successor_id].append(dep.predecessor_id)
        for lst in succ.values():
            lst.sort()
        for lst in pred.values():
            lst.sort()
        self._succ = succ
        self._pred = pred
        self._pairs = frozenset(pairs)

    # --- 参照 -----------------------------------------------------------------

    @property
    def activity_ids(self) -> tuple[int, ...]:
        """活動の ID（昇順）。"""
        return self._ids

    @property
    def dependency_pairs(self) -> frozenset[tuple[int, int]]:
        """依存の（先行, 後続）の組。"""
        return self._pairs

    def predecessors(self, activity_id: int) -> tuple[int, ...]:
        return tuple(self._pred[activity_id])

    def successors(self, activity_id: int) -> tuple[int, ...]:
        return tuple(self._succ[activity_id])

    def has_dependency(self, predecessor_id: int, successor_id: int) -> bool:
        return (predecessor_id, successor_id) in self._pairs

    def independent_activity_ids(self) -> tuple[int, ...]:
        """先行の無い活動（SCH-04 の「並行可能として識別できる」。昇順）。"""
        return tuple(i for i in self._ids if not self._pred[i])

    # --- 循環 -----------------------------------------------------------------

    def cycles(self) -> tuple[tuple[int, ...], ...]:
        """循環ごとの活動の ID（各循環は昇順、循環の並びは最小の ID の昇順）。循環が無ければ空。

        強連結成分を Tarjan の方法で求める（再帰を使わない。O(活動＋依存)）。
        """
        succ = self._succ
        index: dict[int, int] = {}
        low: dict[int, int] = {}
        on_stack: set[int] = set()
        stack: list[int] = []
        found: list[tuple[int, ...]] = []
        counter = 0
        for root in self._ids:
            if root in index:
                continue
            index[root] = low[root] = counter
            counter += 1
            stack.append(root)
            on_stack.add(root)
            work: list[tuple[int, Iterable[int]]] = [(root, iter(succ[root]))]
            while work:
                v, it = work[-1]
                descended = False
                for w in it:
                    if w not in index:
                        index[w] = low[w] = counter
                        counter += 1
                        stack.append(w)
                        on_stack.add(w)
                        work.append((w, iter(succ[w])))
                        descended = True
                        break
                    if w in on_stack:
                        low[v] = min(low[v], index[w])
                if descended:
                    continue
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[v])
                if low[v] == index[v]:
                    component: list[int] = []
                    while True:
                        w = stack.pop()
                        on_stack.discard(w)
                        component.append(w)
                        if w == v:
                            break
                    if len(component) > 1 or v in succ[v]:
                        found.append(tuple(sorted(component)))
        return tuple(sorted(found))

    def cycle_activity_ids(self) -> tuple[int, ...]:
        """循環に含まれる活動の ID（昇順）。循環が無ければ空。"""
        return tuple(sorted(i for cycle in self.cycles() for i in cycle))

    def cycle_if_added(self, predecessor_id: int, successor_id: int) -> tuple[int, ...]:
        """依存 ``predecessor_id → successor_id`` を足すと循環になるなら、その循環の活動を返す（無ければ空）。

        足すと循環になるのは、後続から先行へ既に経路があるとき（自分への依存を含む）。経路は幅優先で最短のもの
        （同じ長さなら ID の小さい活動を先にたどる）を ``(successor_id, …, predecessor_id)`` の順で返す。
        """
        for end in (predecessor_id, successor_id):
            if end not in self._succ:
                raise SchedulingError(f"活動 {end} が入力に無い")
        if predecessor_id == successor_id:
            return (predecessor_id,)
        parent: dict[int, int | None] = {successor_id: None}
        queue: deque[int] = deque([successor_id])
        while queue:
            v = queue.popleft()
            if v == predecessor_id:
                path: list[int] = []
                cur: int | None = v
                while cur is not None:
                    path.append(cur)
                    cur = parent[cur]
                return tuple(reversed(path))
            for w in self._succ[v]:
                if w not in parent:
                    parent[w] = v
                    queue.append(w)
        return ()

    def topological_order(self) -> tuple[int, ...]:
        """トポロジカル順（先行が必ず先。同順位は活動の id の昇順。5.5）。循環があれば ``ScheduleCycleError``。"""
        indegree = {i: len(self._pred[i]) for i in self._ids}
        ready = [i for i in self._ids if indegree[i] == 0]
        heapq.heapify(ready)
        order: list[int] = []
        while ready:
            v = heapq.heappop(ready)
            order.append(v)
            for w in self._succ[v]:
                indegree[w] -= 1
                if indegree[w] == 0:
                    heapq.heappush(ready, w)
        if len(order) != len(self._ids):
            raise ScheduleCycleError(self.cycle_activity_ids())
        return tuple(order)


# RULE-SCH-001 依存の循環検出
def ensure_acyclic(graph: DependencyGraph) -> None:
    """循環があれば計算せず ``ScheduleCycleError``（循環に含まれる活動の一覧。5.5・BR-05）。"""
    cyclic = graph.cycle_activity_ids()
    if cyclic:
        raise ScheduleCycleError(cyclic)


# ---------------------------------------------------------------------------
# RULE-SCH-002 前進計算（自動スケジューリング）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EarlyTimes:
    """前進計算の結果（軸上の位置）。マイルストーンは ``early_start == early_finish == M(m)``（その日の終業）。"""

    activity_id: int
    is_milestone: bool
    duration_days: int
    lower_bound: int  # 下限(a)・下限(m)
    early_start: int  # ES(a)、マイルストーンは M(m)
    early_finish: int  # EF(a) = ES(a) + d、マイルストーンは M(m)


@dataclass(frozen=True)
class ForwardPass:
    """前進計算の結果。``order`` はトポロジカル順（同順位は id の昇順）。"""

    graph: DependencyGraph
    order: tuple[int, ...]
    early: Mapping[int, EarlyTimes]


def _index_activities(activities: Iterable[ScheduleActivity]) -> dict[int, ScheduleActivity]:
    by_id: dict[int, ScheduleActivity] = {}
    for activity in activities:
        if not isinstance(activity, ScheduleActivity):
            raise SchedulingError(f"活動は ScheduleActivity で渡す: {activity!r}")
        if activity.id in by_id:
            raise SchedulingError(f"活動の ID が重複している: {activity.id}")
        by_id[activity.id] = activity
    return by_id


# RULE-SCH-002 前進計算（自動スケジューリング）
def forward_pass(
    activities: Iterable[ScheduleActivity],
    dependencies: Iterable[DependencyInput],
    *,
    calendar: WorkCalendar,
    project_start: date,
) -> ForwardPass:
    """最早開始・最早終了を求める（5.5・BR-05・SCH-06・SCH-09）。

    - 軸の原点はプロジェクトの開始日（5.4）。下限は希望開始日（空ならプロジェクトの開始日）の ``index``。
      マイルストーンの下限はその日の終業（``+ 1``）
    - 先行の無い活動は下限に始まり、先行のある活動は下限と全ての先行の ``EF`` の遅いほうに始まる
    - 先行がマイルストーンなら ``EF(p) = M(p)``（マイルストーンの日の翌稼働日の始業から始まる）
    - 循環があれば計算せず ``ScheduleCycleError``
    """
    by_id = _index_activities(activities)
    graph = DependencyGraph(by_id.keys(), dependencies)
    ensure_acyclic(graph)
    order = graph.topological_order()
    axis = WorkdayAxis(calendar=calendar, origin=project_start)
    early: dict[int, EarlyTimes] = {}
    for activity_id in order:
        activity = by_id[activity_id]
        base = axis.index(activity.requested_start if activity.requested_start is not None else project_start)
        lower = base + 1 if activity.is_milestone else base
        start = lower
        for p in graph.predecessors(activity_id):
            start = max(start, early[p].early_finish)
        finish = start + activity.duration_days  # マイルストーンは d = 0 で M(m) のまま
        early[activity_id] = EarlyTimes(
            activity_id=activity_id,
            is_milestone=activity.is_milestone,
            duration_days=activity.duration_days,
            lower_bound=lower,
            early_start=start,
            early_finish=finish,
        )
    return ForwardPass(graph=graph, order=order, early=MappingProxyType(early))


# ---------------------------------------------------------------------------
# RULE-SCH-003 後退計算とクリティカルパス
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ActivityTimes:
    """前進・後退計算の結果（軸上の位置）。マイルストーンは ``late_start == late_finish == LM(m)``。"""

    activity_id: int
    is_milestone: bool
    duration_days: int
    early_start: int
    early_finish: int
    late_start: int
    late_finish: int
    total_float: int  # LS − ES（稼働日）
    is_critical: bool  # total_float ≤ 0


@dataclass(frozen=True)
class BackwardPass:
    """後退計算の結果。``project_finish`` は ``E = max(EF の全て)``（活動が無ければ ``None``）。

    ``critical_path`` はクリティカルな活動の ID（トポロジカル順）。
    """

    order: tuple[int, ...]
    times: Mapping[int, ActivityTimes]
    project_finish: int | None
    critical_path: tuple[int, ...]


# RULE-SCH-003 後退計算とクリティカルパス
def backward_pass(forward: ForwardPass) -> BackwardPass:
    """最遅開始・最遅終了・トータルフロートとクリティカルを求める（5.5・BR-05・SCH-10）。

    - ``E = max(EF の全て)``（マイルストーンは ``M``）。後続の無い活動は ``LF = E``、ある活動は ``LF = min{ LS(s) }``、``LS = LF − d``
    - マイルストーンは ``LM = min{ LS(s) }``（後続が無ければ ``E``）。``LS = LF = LM``
    - ``total_float = LS − ES``（稼働日）。``total_float ≤ 0`` をクリティカルにする
    """
    graph = forward.graph
    early = forward.early
    if not forward.order:
        return BackwardPass(order=(), times=MappingProxyType({}), project_finish=None, critical_path=())
    project_finish = max(e.early_finish for e in early.values())
    late_start: dict[int, int] = {}
    late_finish: dict[int, int] = {}
    for activity_id in reversed(forward.order):
        successors = graph.successors(activity_id)
        lf = min((late_start[s] for s in successors), default=project_finish)
        late_finish[activity_id] = lf
        late_start[activity_id] = lf - early[activity_id].duration_days
    times: dict[int, ActivityTimes] = {}
    critical: list[int] = []
    for activity_id in forward.order:
        e = early[activity_id]
        total_float = late_start[activity_id] - e.early_start
        is_critical = total_float <= 0
        if is_critical:
            critical.append(activity_id)
        times[activity_id] = ActivityTimes(
            activity_id=activity_id,
            is_milestone=e.is_milestone,
            duration_days=e.duration_days,
            early_start=e.early_start,
            early_finish=e.early_finish,
            late_start=late_start[activity_id],
            late_finish=late_finish[activity_id],
            total_float=total_float,
            is_critical=is_critical,
        )
    return BackwardPass(
        order=forward.order,
        times=MappingProxyType(times),
        project_finish=project_finish,
        critical_path=tuple(critical),
    )


# ---------------------------------------------------------------------------
# 日程計算の全体（前進→後退→日付）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ActivitySchedule:
    """1 つの活動の日程（4.1 の ``activities`` の計算結果の列に当たる）。

    日付は 5.5 の「結果の日付」：開始日＝添字 ``ES`` の稼働日、終了日＝添字 ``EF − 1`` の稼働日、
    最遅開始日＝添字 ``LS``、最遅終了日＝添字 ``LF − 1``。マイルストーンの日付は添字 ``M − 1``（最遅は ``LM − 1``）で、
    開始日＝終了日（BR-04）。
    """

    times: ActivityTimes
    start_date: date
    finish_date: date
    early_start: date
    early_finish: date
    late_start: date
    late_finish: date

    @property
    def activity_id(self) -> int:
        return self.times.activity_id

    @property
    def is_milestone(self) -> bool:
        return self.times.is_milestone

    @property
    def total_float(self) -> int:
        return self.times.total_float

    @property
    def is_critical(self) -> bool:
        return self.times.is_critical


@dataclass(frozen=True)
class Schedule:
    """日程計算の結果。``activities`` はトポロジカル順（同順位は id の昇順）。"""

    activities: Mapping[int, ActivitySchedule]
    order: tuple[int, ...]
    project_finish: int | None  # E（軸上の位置）
    project_finish_date: date | None  # 添字 E − 1 の稼働日
    critical_path: tuple[int, ...]  # クリティカルな活動（トポロジカル順）


@dataclass
class _DateOf:
    """軸の添字 → 稼働日（同じ添字を何度も引くので覚えておく）。"""

    axis: WorkdayAxis
    cache: dict[int, date] = field(default_factory=dict)

    def __call__(self, i: int) -> date:
        found = self.cache.get(i)
        if found is None:
            found = self.axis.workday_at(i)
            self.cache[i] = found
        return found


def calculate_schedule(
    activities: Iterable[ScheduleActivity],
    dependencies: Iterable[DependencyInput],
    *,
    calendar: WorkCalendar,
    project_start: date,
) -> Schedule:
    """前進計算・後退計算を流し、軸上の位置を日付へ直す（5.5。SCH-06・SCH-10 の自動反映で、サービス層が保存する）。

    循環があれば計算せず ``ScheduleCycleError``（5.5）。同じ入力からは同じ結果になる（SCH-06 の決定的）。
    """
    forward = forward_pass(activities, dependencies, calendar=calendar, project_start=project_start)
    backward = backward_pass(forward)
    date_of = _DateOf(WorkdayAxis(calendar=calendar, origin=project_start))
    result: dict[int, ActivitySchedule] = {}
    for activity_id in backward.order:
        t = backward.times[activity_id]
        if t.is_milestone:
            early_day = date_of(t.early_start - 1)
            late_day = date_of(t.late_start - 1)
            start, finish, late_s, late_f = early_day, early_day, late_day, late_day
        else:
            start = date_of(t.early_start)
            finish = date_of(t.early_finish - 1)
            late_s = date_of(t.late_start)
            late_f = date_of(t.late_finish - 1)
        result[activity_id] = ActivitySchedule(
            times=t,
            start_date=start,
            finish_date=finish,
            early_start=start,
            early_finish=finish,
            late_start=late_s,
            late_finish=late_f,
        )
    finish_position = backward.project_finish
    return Schedule(
        activities=MappingProxyType(result),
        order=backward.order,
        project_finish=finish_position,
        project_finish_date=None if finish_position is None else date_of(finish_position - 1),
        critical_path=backward.critical_path,
    )


# ---------------------------------------------------------------------------
# RULE-SCH-006 一括更新の検査
# ---------------------------------------------------------------------------


class _Unset:
    """「変えない」を表す印（``None`` は「空にする」の意味で使うので別に持つ）。"""

    _instance: _Unset | None = None

    def __new__(cls) -> _Unset:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "UNSET"

    def __bool__(self) -> bool:
        return False


UNSET: Final = _Unset()


@dataclass(frozen=True)
class EditableActivity:
    """一括更新の対象になる活動の今の値（4.1 の ``activities`` の利用者の入力の列と、先行・担当）。"""

    id: int
    name: str
    requested_start: date | None
    duration_days: int
    predecessor_ids: frozenset[int] = frozenset()
    assignee_user_ids: frozenset[int] = frozenset()

    @property
    def is_milestone(self) -> bool:
        """期間 0 の活動がマイルストーン（BR-04）。"""
        return self.duration_days == 0


@dataclass(frozen=True)
class ActivityChange:
    """1 つの活動への変更（SCH-02 の「日付・先行活動・担当」と名称）。``UNSET`` の項目は変えない。

    - ``requested_start``：希望開始日。``None`` は空にする（プロジェクトの開始日から。5.5）
    - ``duration_days``：期間（稼働日）。0 にするとマイルストーン（BR-04）
    - ``predecessor_ids``：先行の活動の全体（置き換え。足すだけ・消すだけの指定は持たない）
    - ``assignee_user_ids``：担当の利用者の全体（置き換え）
    """

    activity_id: int
    name: str | _Unset = UNSET
    requested_start: date | None | _Unset = UNSET
    duration_days: int | _Unset = UNSET
    predecessor_ids: Iterable[int] | _Unset = UNSET
    assignee_user_ids: Iterable[int] | _Unset = UNSET


class BulkIssueCode(StrEnum):
    """一括更新の検査で見つけた不正の種類。"""

    ACTIVITY_NOT_FOUND = "ACTIVITY_NOT_FOUND"  # プロジェクトに無い活動を変えようとした
    DUPLICATE_ACTIVITY = "DUPLICATE_ACTIVITY"  # 同じ活動への変更が 2 つ以上ある
    INVALID_NAME = "INVALID_NAME"  # 名称が空・長すぎる
    INVALID_DATE = "INVALID_DATE"  # 希望開始日が日付でない
    INVALID_DURATION = "INVALID_DURATION"  # 期間が 0 以上の整数でない
    PREDECESSOR_NOT_FOUND = "PREDECESSOR_NOT_FOUND"  # 先行がプロジェクトに無い
    SELF_DEPENDENCY = "SELF_DEPENDENCY"  # 自分を先行にした
    DUPLICATE_PREDECESSOR = "DUPLICATE_PREDECESSOR"  # 同じ先行を 2 回書いた
    ASSIGNEE_NOT_ASSIGNABLE = "ASSIGNEE_NOT_ASSIGNABLE"  # 担当にできない利用者（プロジェクトのメンバーでない など）
    DUPLICATE_ASSIGNEE = "DUPLICATE_ASSIGNEE"  # 同じ担当を 2 回書いた
    SCHEDULE_CYCLE = "SCHEDULE_CYCLE"  # 変更後の依存が循環する（RULE-SCH-001）


@dataclass(frozen=True)
class BulkIssue:
    """1 件の不正。``index`` は変更の列の中の位置（0 から。循環は特定の変更に帰さないので ``None``）。"""

    code: BulkIssueCode
    index: int | None
    activity_id: int | None
    field: str | None
    message: str
    related_ids: tuple[int, ...] = ()


class BulkUpdateError(SchedulingError):
    """一括更新に 1 件でも不正がある（SCH-02：全件を保存しない）。見つけた不正の全てを ``issues`` に持つ。"""

    def __init__(self, issues: Sequence[BulkIssue]) -> None:
        self.issues: tuple[BulkIssue, ...] = tuple(issues)
        super().__init__(f"一括更新に不正が {len(self.issues)} 件ある（全件を保存しない）")


@dataclass(frozen=True)
class BulkUpdatePlan:
    """検査が全て通った変更の、適用後の状態（サービス層が 1 回の保存で書き、日程を再計算する）。

    - ``activities``：変更した活動の適用後の値（変更の列の順）
    - ``dependencies``：適用後のプロジェクトの依存の全体（（先行, 後続）の昇順）
    - ``added_dependencies``・``removed_dependencies``：今の依存との差（昇順）
    """

    activities: tuple[EditableActivity, ...]
    dependencies: tuple[tuple[int, int], ...]
    added_dependencies: tuple[tuple[int, int], ...]
    removed_dependencies: tuple[tuple[int, int], ...]


def _ids_of(values: Iterable[object], what: str) -> list[object]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        raise SchedulingError(f"{what} は ID の列で渡す: {values!r}")
    return list(values)


# RULE-SCH-006 一括更新の検査
def check_bulk_update(
    activities: Iterable[EditableActivity],
    changes: Sequence[ActivityChange],
    *,
    assignable_user_ids: Iterable[int],
) -> BulkUpdatePlan:
    """複数の活動の変更を全て検査してから適用した状態を返す。1 件でも不正なら ``BulkUpdateError``（SCH-02）。

    - ``activities``：プロジェクトの全ての活動の今の値（先行は今の依存）。依存は活動の ``predecessor_ids`` から組み立てる
    - ``assignable_user_ids``：担当にできる利用者（プロジェクトのメンバーで有効な利用者。サービス層が決めて渡す）
    - 検査：活動の存在・重複、名称、希望開始日、期間、先行の存在・自分・重複、担当の可否・重複を変更ごとに全て見て、
      最後に変更後の依存の全体で循環（RULE-SCH-001）を見る。不正は全て集めてから拒む（最初の 1 件で止めない）
    - マイルストーンは期間で決まる（期間 0 がマイルストーン。BR-04）
    """
    current: dict[int, EditableActivity] = {}
    for activity in activities:
        if activity.id in current:
            raise SchedulingError(f"活動の ID が重複している: {activity.id}")
        current[activity.id] = activity
    assignable = frozenset(assignable_user_ids)

    issues: list[BulkIssue] = []
    updated: dict[int, EditableActivity] = {}
    order: list[int] = []
    seen: set[int] = set()

    def issue(
        code: BulkIssueCode,
        index: int | None,
        activity_id: int | None,
        field_name: str | None,
        message: str,
        related: Sequence[int] = (),
    ) -> None:
        issues.append(BulkIssue(code, index, activity_id, field_name, message, tuple(related)))

    for index, change in enumerate(changes):
        activity_id = change.activity_id
        if activity_id not in current:
            issue(BulkIssueCode.ACTIVITY_NOT_FOUND, index, activity_id, "activity_id",
                  f"活動 {activity_id} がプロジェクトに無い")
            continue
        if activity_id in seen:
            issue(BulkIssueCode.DUPLICATE_ACTIVITY, index, activity_id, "activity_id",
                  f"活動 {activity_id} への変更が 2 つ以上ある")
            continue
        seen.add(activity_id)
        before = current[activity_id]
        name, requested_start, duration = before.name, before.requested_start, before.duration_days
        predecessors, assignees = before.predecessor_ids, before.assignee_user_ids
        ok = True

        if not isinstance(change.name, _Unset):
            text = change.name
            if not isinstance(text, str) or text.strip() == "" or len(text) > MAX_ACTIVITY_NAME_LENGTH:
                issue(BulkIssueCode.INVALID_NAME, index, activity_id, "name",
                      f"名称は空でない {MAX_ACTIVITY_NAME_LENGTH} 文字以内の文字列")
                ok = False
            else:
                name = text

        if not isinstance(change.requested_start, _Unset):
            value = change.requested_start
            if value is not None and not isinstance(value, date):
                issue(BulkIssueCode.INVALID_DATE, index, activity_id, "requested_start", "希望開始日は日付か空")
                ok = False
            else:
                requested_start = value

        if not isinstance(change.duration_days, _Unset):
            d = change.duration_days
            if isinstance(d, bool) or not isinstance(d, int) or d < 0:
                issue(BulkIssueCode.INVALID_DURATION, index, activity_id, "duration_days",
                      "期間（稼働日）は 0 以上の整数（0 はマイルストーン）")
                ok = False
            else:
                duration = d

        if not isinstance(change.predecessor_ids, _Unset):
            new_preds: set[int] = set()
            for p in _ids_of(change.predecessor_ids, "先行"):
                if isinstance(p, bool) or not isinstance(p, int) or p not in current:
                    issue(BulkIssueCode.PREDECESSOR_NOT_FOUND, index, activity_id, "predecessor_ids",
                          f"先行の活動 {p!r} がプロジェクトに無い")
                    ok = False
                elif p == activity_id:
                    issue(BulkIssueCode.SELF_DEPENDENCY, index, activity_id, "predecessor_ids",
                          "自分を先行にはできない（循環）", (activity_id,))
                    ok = False
                elif p in new_preds:
                    issue(BulkIssueCode.DUPLICATE_PREDECESSOR, index, activity_id, "predecessor_ids",
                          f"先行の活動 {p} が 2 回ある")
                    ok = False
                else:
                    new_preds.add(p)
            predecessors = frozenset(new_preds)

        if not isinstance(change.assignee_user_ids, _Unset):
            new_assignees: set[int] = set()
            for u in _ids_of(change.assignee_user_ids, "担当"):
                if isinstance(u, bool) or not isinstance(u, int) or u not in assignable:
                    issue(BulkIssueCode.ASSIGNEE_NOT_ASSIGNABLE, index, activity_id, "assignee_user_ids",
                          f"利用者 {u!r} は担当にできない（プロジェクトのメンバーでない・無効）")
                    ok = False
                elif u in new_assignees:
                    issue(BulkIssueCode.DUPLICATE_ASSIGNEE, index, activity_id, "assignee_user_ids",
                          f"担当の利用者 {u} が 2 回ある")
                    ok = False
                else:
                    new_assignees.add(u)
            assignees = frozenset(new_assignees)

        if ok:
            updated[activity_id] = EditableActivity(
                id=activity_id,
                name=name,
                requested_start=requested_start,
                duration_days=duration,
                predecessor_ids=predecessors,
                assignee_user_ids=assignees,
            )
            order.append(activity_id)

    if issues:
        raise BulkUpdateError(issues)

    after = {**current, **updated}
    before_pairs = {(p, a.id) for a in current.values() for p in a.predecessor_ids}
    after_pairs = {(p, a.id) for a in after.values() for p in a.predecessor_ids}
    graph = DependencyGraph(after.keys(), sorted(after_pairs))
    for cycle in graph.cycles():
        issue(BulkIssueCode.SCHEDULE_CYCLE, None, None, "predecessor_ids",
              f"変更後の依存が循環する（循環に含まれる活動: {list(cycle)}）", cycle)
    if issues:
        raise BulkUpdateError(issues)

    return BulkUpdatePlan(
        activities=tuple(updated[i] for i in order),
        dependencies=tuple(sorted(after_pairs)),
        added_dependencies=tuple(sorted(after_pairs - before_pairs)),
        removed_dependencies=tuple(sorted(before_pairs - after_pairs)),
    )
