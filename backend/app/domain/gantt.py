"""ガントの描画データの組み立て（設計書 5.1 の RULE-SCH-005、7.3。要求仕様書 SCH-11・12・13）。

- 行の順：WBS の並び（先行順・兄弟は表示順）→ 活動の並び（WP の中の ``sort_order``、同じなら id）。行は活動 1 つにつき 1 行
- バー：予定（計算した開始日〜終了日）・実績（実績開始〜実績終了。未完了なら報告日まで）・基準（選んだベースラインの項目）。
  マイルストーンは 1 点（その日の終業。5.5 の ``M``）で、画面は菱形で描く（7.3・SCH-09）
- 依存線：先行の予定の終わりから後続の予定の始まりへ（SCH-13）
- ラベル：担当者の名前と進捗率（未算定は ``None``）（SCH-13）
- 非稼働日の帯：描く範囲（全てのバーの最も早い日〜最も遅い日）の非稼働日（7.3 の ①・SCH-11）

位置は稼働日の軸（5.4・RULE-CAL-002。原点はプロジェクトの開始日）の上の位置と、その位置の暦日上の座標
（日付＋その日の中の割合 0〜1）を両方返す（7.3：画面は暦日の座標で描く）。区間の始まりはその日の始業側（割合 0 から）、
終わりはその日の終業側（割合 1 まで）の座標にする。金曜に終わるバーは月曜の始業ではなく金曜の終業で終わる。

純粋に保つ（1.3）。DB・HTTP・時刻・乱数に触れない。報告日は引数で受ける。DB の行はサービス層が下のデータクラスへ直して渡す。
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum
from fractions import Fraction
from typing import Final

from app.domain.calendar import Position, WorkCalendar, WorkdayAxis
from app.domain.wbs_code import CodedElement, WbsCodeError, display_preorder

_ONE_DAY = timedelta(days=1)

# 非稼働日を返す表示範囲の長さの上限（暦日。両端を含む）。業務の値ではなく、1 回の応答の大きさの歯止め（ASSUMPTIONS.md A-48）
MAX_GANTT_RANGE_DAYS: Final = 3660


class GanttError(ValueError):
    """ガントの組み立ての入力が不正（未定義の入力は推測せずに拒む）。"""


class GanttRangeError(GanttError):
    """表示範囲（``range_start``・``range_end``）が不正：始まりが終わりより後、または長さが上限を超える。

    ``fields`` は誤りに関わる、渡された端の名前（``range_start``・``range_end`` の順）。
    """

    def __init__(self, message: str, fields: tuple[str, ...]) -> None:
        super().__init__(message)
        self.fields = fields


# ---------------------------------------------------------------------------
# 入力
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GanttElement:
    """WBS 要素のうち行の順と表示に要る列（4.1 の ``wbs_elements``）。最上位の要素は ``parent_id`` が ``None``。"""

    id: int
    parent_id: int | None
    wbs_code: str
    name: str
    sort_order: int = 0
    sibling_seq: int = 1


@dataclass(frozen=True)
class GanttActivity:
    """活動の描画に要る値（4.1 の ``activities`` の計算結果、担当、最新の進捗の入力）。

    ``start_date``・``finish_date`` は日程計算（RULE-SCH-002）の結果（計算の前は ``None`` で、予定のバーを出さない）。
    ``assignees`` は担当者の表示名（並べたい順）。``percent_complete`` は進捗率（0〜100。``None`` は未算定）。
    """

    id: int
    work_package_id: int
    name: str
    sort_order: int
    is_milestone: bool
    start_date: date | None
    finish_date: date | None
    is_critical: bool = False
    total_float: int | None = None
    assignees: tuple[str, ...] = ()
    percent_complete: Decimal | None = None
    actual_start: date | None = None
    actual_finish: date | None = None


@dataclass(frozen=True)
class BaselineBarInput:
    """選んだスケジュールベースラインの活動の項目（4.1 の ``schedule_baseline_items`` の ACTIVITY）。"""

    start_date: date | None
    finish_date: date | None
    is_milestone: bool


# ---------------------------------------------------------------------------
# 出力
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CalendarPoint:
    """暦日上の座標：日付と、その日の中の割合（0＝始業、1＝終業）。"""

    date: date
    fraction: Fraction


@dataclass(frozen=True)
class AxisPoint:
    """稼働日の軸の上の位置と、その暦日上の座標。"""

    position: Position
    calendar: CalendarPoint


class BarKind(StrEnum):
    """バーの種類（7.3 のレイヤ ②〜④）。"""

    BASELINE = "BASELINE"  # ベースライン（細い灰色）
    PLANNED = "PLANNED"  # 予定（クリティカルは赤い枠）
    ACTUAL = "ACTUAL"  # 実績


@dataclass(frozen=True)
class GanttBar:
    """1 本のバー。マイルストーンは ``start == end``（その日の終業の 1 点）。

    ``start_date``・``end_date`` はバーの最初の日と最後の日。実績で未完了のバーは ``open_ended`` が真で、
    ``end_date`` は報告日（の前の稼働日）。
    """

    kind: BarKind
    is_milestone: bool
    start: AxisPoint
    end: AxisPoint
    start_date: date
    end_date: date
    open_ended: bool = False


@dataclass(frozen=True)
class GanttRow:
    """ガントの 1 行（1 つの活動）。``row_index`` は 0 から。``wbs_code``・``work_package_name`` は活動の置き場の WP。"""

    row_index: int
    activity_id: int
    name: str
    work_package_id: int
    wbs_code: str
    work_package_name: str
    depth: int  # WP の階層（最上位の要素が 1）
    is_milestone: bool
    is_critical: bool
    total_float: int | None
    planned: GanttBar | None
    actual: GanttBar | None
    baseline: GanttBar | None
    assignees: tuple[str, ...]
    percent_complete: Decimal | None


@dataclass(frozen=True)
class GanttLink:
    """依存の矢印（先行の予定の終わり → 後続の予定の始まり）。予定のバーが無い端の座標は ``None``。"""

    predecessor_id: int
    successor_id: int
    from_row: int
    to_row: int
    start: AxisPoint | None
    end: AxisPoint | None


@dataclass(frozen=True)
class GanttChart:
    """ガントの描画データ（API-SCH-010 が返す）。

    ``range_start``・``range_end`` はバーの範囲（バーが 1 本も無ければ ``None``）。``non_working_days`` は表示範囲
    （``build_gantt`` に渡した範囲。省いた端はバーの範囲の端）の非稼働日（設計書 1.1・CR-008）。
    """

    rows: tuple[GanttRow, ...]
    links: tuple[GanttLink, ...]
    range_start: date | None
    range_end: date | None
    non_working_days: tuple[date, ...]


# ---------------------------------------------------------------------------
# 位置 ⇔ 暦日の座標
# ---------------------------------------------------------------------------


@dataclass
class _Axis:
    """稼働日の軸に、日付 ⇔ 添字の引き当ての覚えを足したもの（同じ日付・添字を何度も引くため）。"""

    axis: WorkdayAxis
    _index: dict[date, int] = field(default_factory=dict)
    _day: dict[int, date] = field(default_factory=dict)

    def index(self, d: date) -> int:
        found = self._index.get(d)
        if found is None:
            found = self.axis.index(d)
            self._index[d] = found
        return found

    def workday_at(self, i: int) -> date:
        found = self._day.get(i)
        if found is None:
            found = self.axis.workday_at(i)
            self._day[i] = found
        return found

    def start_of_day(self, d: date) -> int:
        """日 d の始業の位置（非稼働日なら次の稼働日の始業。BR-04 の開始日の寄せ）。"""
        return self.index(d)

    def end_of_day(self, d: date) -> int:
        """日 d の終業の位置（非稼働日なら前の稼働日の終業。BR-04 の終了日の寄せ。5.4 の ``line(d)`` と同じ）。"""
        return self.index(d) + (1 if self.axis.calendar.is_working_day(d) else 0)

    def start_point(self, p: Position) -> AxisPoint:
        return AxisPoint(position=p, calendar=_start_coordinate(p, self.workday_at))

    def end_point(self, p: Position) -> AxisPoint:
        return AxisPoint(position=p, calendar=_end_coordinate(p, self.workday_at))


def _as_fraction(p: Position) -> Fraction:
    if isinstance(p, (float, Decimal)) and not math.isfinite(p):
        raise GanttError(f"位置が有限の数ではない: {p}")
    return Fraction(p)


def _start_coordinate(position: Position, workday_at: Callable[[int], date]) -> CalendarPoint:
    p = _as_fraction(position)
    k = math.floor(p)
    return CalendarPoint(date=workday_at(k), fraction=p - k)


def _end_coordinate(position: Position, workday_at: Callable[[int], date]) -> CalendarPoint:
    p = _as_fraction(position)
    k = math.ceil(p) - 1
    return CalendarPoint(date=workday_at(k), fraction=p - k)


def start_coordinate(axis: WorkdayAxis, position: Position) -> CalendarPoint:
    """区間の始まりの位置 p → 暦日の座標。添字 ``floor(p)`` の稼働日の、割合 ``p − floor(p)``（整数の位置はその日の始業）。"""
    return _start_coordinate(position, axis.workday_at)


def end_coordinate(axis: WorkdayAxis, position: Position) -> CalendarPoint:
    """区間の終わりの位置 p → 暦日の座標。添字 ``ceil(p) − 1`` の稼働日の、割合 ``p − (ceil(p) − 1)``。

    整数の位置は前の稼働日の終業（割合 1）になる（5.4 の「位置 p の表示の日付」と同じ日）。
    """
    return _end_coordinate(position, axis.workday_at)


# ---------------------------------------------------------------------------
# RULE-SCH-005 ガントの描画データの組み立て
# ---------------------------------------------------------------------------


def _span_bar(ax: _Axis, kind: BarKind, first: date, last: date) -> GanttBar:
    start = ax.start_of_day(first)
    end = ax.end_of_day(last)
    if end < start:
        raise GanttError(f"{kind} のバーの終わり {last} が始まり {first} より前")
    return GanttBar(
        kind=kind,
        is_milestone=False,
        start=ax.start_point(start),
        end=ax.end_point(end),
        start_date=first,
        end_date=last,
    )


def _milestone_bar(ax: _Axis, kind: BarKind, day: date) -> GanttBar:
    point = ax.end_point(ax.end_of_day(day))
    return GanttBar(kind=kind, is_milestone=True, start=point, end=point, start_date=day, end_date=day)


def _planned_bar(ax: _Axis, kind: BarKind, start: date | None, finish: date | None, is_milestone: bool) -> GanttBar | None:
    if start is None and finish is None:
        return None
    if start is None or finish is None:
        raise GanttError(f"{kind} のバーの開始日と終了日は両方そろえる（片方だけ: {start}, {finish}）")
    if is_milestone:
        if start != finish:
            raise GanttError(f"マイルストーンの日付は 1 つ（開始日＝終了日。BR-04）: {start}, {finish}")
        return _milestone_bar(ax, kind, start)
    return _span_bar(ax, kind, start, finish)


def _actual_bar(ax: _Axis, activity: GanttActivity, report_date: date | None) -> GanttBar | None:
    """実績開始〜実績終了（未完了なら報告日まで）のバー（SCH-12）。マイルストーンは実績終了の 1 点。"""
    a_start, a_finish = activity.actual_start, activity.actual_finish
    if activity.is_milestone:
        return None if a_finish is None else _milestone_bar(ax, BarKind.ACTUAL, a_finish)
    if a_start is None:
        if a_finish is not None:
            raise GanttError(f"活動 {activity.id}：実績開始日の無い実績終了日 {a_finish}")
        return None
    if a_finish is not None:
        if a_finish < a_start:
            raise GanttError(f"活動 {activity.id}：実績終了日 {a_finish} が実績開始日 {a_start} より前")
        return _span_bar(ax, BarKind.ACTUAL, a_start, a_finish)
    if report_date is None:
        raise GanttError(f"活動 {activity.id}：未完了の実績のバーは報告日まで描くので、報告日が要る（SCH-12）")
    if report_date < a_start:
        raise GanttError(f"活動 {activity.id}：報告日 {report_date} が実績開始日 {a_start} より前")
    start = ax.start_of_day(a_start)
    end = max(ax.end_of_day(report_date), start)
    last = ax.workday_at(end - 1) if end > start else a_start
    return GanttBar(
        kind=BarKind.ACTUAL,
        is_milestone=False,
        start=ax.start_point(start),
        end=ax.end_point(end),
        start_date=a_start,
        end_date=last,
        open_ended=True,
    )


# RULE-SCH-005 ガントの描画データの組み立て
def build_gantt(
    elements: Iterable[GanttElement],
    activities: Iterable[GanttActivity],
    dependencies: Iterable[tuple[int, int]],
    *,
    calendar: WorkCalendar,
    project_start: date,
    baseline: Mapping[int, BaselineBarInput] | None = None,
    report_date: date | None = None,
    range_start: date | None = None,
    range_end: date | None = None,
) -> GanttChart:
    """ガントの描画データを組み立てる（7.3・SCH-11・12・13）。

    - ``elements``：プロジェクトの WBS 要素の全て。``activities``：活動の全て（置き場の WP は ``elements`` に要る）
    - ``dependencies``：依存の（先行, 後続）の組
    - ``baseline``：選んだベースラインの項目を活動の ID で引くもの（選ばなければ ``None`` で、基準のバーを出さない）
    - ``report_date``：報告日。未完了の実績のバーの終わりに使う（未完了の実績があるのに無ければ ``GanttError``）
    - ``range_start``・``range_end``：表示範囲（6.2 の API-SCH-010。設計書 1.1・CR-008）。この範囲の非稼働日を返す。
      省いた端はバーの範囲の端（両方省けばバーの範囲。省いた端にバーが無ければ空）。どちらかの端を渡したときだけ、
      始まりが終わりより後、または長さが ``MAX_GANTT_RANGE_DAYS`` を超える範囲を ``GanttRangeError`` で拒む
      （両方省いたバーの範囲には上限を掛けない）。バーが無くても両端を渡せばその範囲の非稼働日を返す
    """
    if range_start is not None and range_end is not None and range_start > range_end:
        raise GanttRangeError(f"表示範囲の始まり {range_start} が終わり {range_end} より後", ("range_start", "range_end"))
    element_list = list(elements)
    by_element = {e.id: e for e in element_list}
    if len(by_element) != len(element_list):
        raise GanttError("WBS 要素の ID が重複している")
    try:
        preorder = display_preorder(
            CodedElement(
                id=e.id,
                parent_id=e.parent_id,
                wbs_code=e.wbs_code,
                sibling_seq=e.sibling_seq,
                sort_order=e.sort_order,
            )
            for e in element_list
        )
    except WbsCodeError as exc:
        raise GanttError(f"WBS の木が不正: {exc}") from exc
    wbs_rank = {element_id: rank for rank, element_id in enumerate(preorder)}
    depth: dict[int, int] = {}
    for element_id in preorder:
        parent = by_element[element_id].parent_id
        depth[element_id] = 1 if parent is None else depth[parent] + 1

    activity_list = list(activities)
    seen: set[int] = set()
    for activity in activity_list:
        if activity.id in seen:
            raise GanttError(f"活動の ID が重複している: {activity.id}")
        seen.add(activity.id)
        if activity.work_package_id not in by_element:
            raise GanttError(f"活動 {activity.id} の置き場の WP {activity.work_package_id} が WBS に無い")
    ordered = sorted(activity_list, key=lambda a: (wbs_rank[a.work_package_id], a.sort_order, a.id))

    ax = _Axis(WorkdayAxis(calendar=calendar, origin=project_start))
    baseline_items = baseline or {}
    rows: list[GanttRow] = []
    row_of: dict[int, int] = {}
    for row_index, activity in enumerate(ordered):
        wp = by_element[activity.work_package_id]
        planned = _planned_bar(ax, BarKind.PLANNED, activity.start_date, activity.finish_date, activity.is_milestone)
        item = baseline_items.get(activity.id)
        base = None if item is None else _planned_bar(
            ax, BarKind.BASELINE, item.start_date, item.finish_date, item.is_milestone
        )
        rows.append(
            GanttRow(
                row_index=row_index,
                activity_id=activity.id,
                name=activity.name,
                work_package_id=wp.id,
                wbs_code=wp.wbs_code,
                work_package_name=wp.name,
                depth=depth[wp.id],
                is_milestone=activity.is_milestone,
                is_critical=activity.is_critical,
                total_float=activity.total_float,
                planned=planned,
                actual=_actual_bar(ax, activity, report_date),
                baseline=base,
                assignees=tuple(activity.assignees),
                percent_complete=activity.percent_complete,
            )
        )
        row_of[activity.id] = row_index

    links: list[GanttLink] = []
    for predecessor_id, successor_id in sorted(set(dependencies)):
        if predecessor_id not in row_of or successor_id not in row_of:
            raise GanttError(f"依存 {predecessor_id}→{successor_id} の活動が入力に無い")
        from_bar = rows[row_of[predecessor_id]].planned
        to_bar = rows[row_of[successor_id]].planned
        links.append(
            GanttLink(
                predecessor_id=predecessor_id,
                successor_id=successor_id,
                from_row=row_of[predecessor_id],
                to_row=row_of[successor_id],
                start=None if from_bar is None else from_bar.end,
                end=None if to_bar is None else to_bar.start,
            )
        )

    bars = [bar for row in rows for bar in (row.planned, row.actual, row.baseline) if bar is not None]
    bar_start = min((min(bar.start.calendar.date, bar.start_date) for bar in bars), default=None)
    bar_end = max((max(bar.end.calendar.date, bar.end_date) for bar in bars), default=None)
    shown_start = range_start if range_start is not None else bar_start
    shown_end = range_end if range_end is not None else bar_end
    given = tuple(name for name, value in (("range_start", range_start), ("range_end", range_end)) if value is not None)
    if given and shown_start is not None and shown_end is not None:
        if shown_start > shown_end:
            raise GanttRangeError(f"表示範囲の始まり {shown_start} が終わり {shown_end} より後", given)
        if (shown_end - shown_start).days + 1 > MAX_GANTT_RANGE_DAYS:
            raise GanttRangeError(
                f"表示範囲が長すぎる（{shown_start}〜{shown_end}。上限 {MAX_GANTT_RANGE_DAYS} 日）", given)
    return GanttChart(
        rows=tuple(rows),
        links=tuple(links),
        range_start=bar_start,
        range_end=bar_end,
        non_working_days=_non_working_days(calendar, shown_start, shown_end),
    )


def _non_working_days(calendar: WorkCalendar, start: date | None, end: date | None) -> tuple[date, ...]:
    """表示範囲 ``start``〜``end``（両端を含む）の非稼働日。どちらかの端が無ければ空。"""
    if start is None or end is None:
        return ()
    non_working: list[date] = []
    day = start
    while day <= end:
        if not calendar.is_working_day(day):
            non_working.append(day)
        day += _ONE_DAY
    return tuple(non_working)
