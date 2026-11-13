"""ガントの描画データの組み立て（app.domain.gantt）の試験。

設計書 5.1・7.3、要求仕様書 SCH-09・11・12・13。
2026-12 の曜日：12/01＝火、12/04＝金、12/05＝土、12/06＝日、12/07＝月。プロジェクトの開始日は 12/01（位置 0）。
既定のカレンダー（土日休み）での位置：12/01＝0、12/02＝1、12/03＝2、12/04＝3、12/07＝4、12/08＝5、12/09＝6。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction

import pytest

from app.domain.calendar import WorkCalendar, WorkdayAxis
from app.domain.gantt import (
    BarKind,
    BaselineBarInput,
    CalendarPoint,
    GanttActivity,
    GanttChart,
    MAX_GANTT_RANGE_DAYS,
    GanttElement,
    GanttError,
    GanttRangeError,
    build_gantt,
    end_coordinate,
    start_coordinate,
)

CAL = WorkCalendar()
START = date(2026, 12, 1)


def d(month_day: str) -> date:
    """'12/05' → 2026-12-05（'11/30' は 2026 年）。"""
    month, day = (int(x) for x in month_day.split("/"))
    return date(2026 if month >= 11 else 2027, month, day)


# 1.0（10）┬ 1.1（11, sort 2）
#          └ 1.2（12, sort 1）… 表示順では 1.2 が先
# 2.0（20）
ELEMENTS = [
    GanttElement(id=20, parent_id=None, wbs_code="2.0", name="運用", sort_order=2, sibling_seq=2),
    GanttElement(id=10, parent_id=None, wbs_code="1.0", name="開発", sort_order=1, sibling_seq=1),
    GanttElement(id=11, parent_id=10, wbs_code="1.1", name="設計", sort_order=2, sibling_seq=1),
    GanttElement(id=12, parent_id=10, wbs_code="1.2", name="実装", sort_order=1, sibling_seq=2),
]


def activity(activity_id: int, wp: int, sort: int, start: str | None, finish: str | None, **kw: object) -> GanttActivity:
    return GanttActivity(
        id=activity_id,
        work_package_id=wp,
        name=f"活動{activity_id}",
        sort_order=sort,
        is_milestone=bool(kw.pop("is_milestone", False)),
        start_date=None if start is None else d(start),
        finish_date=None if finish is None else d(finish),
        **kw,  # type: ignore[arg-type]
    )


def gantt(
    activities: list[GanttActivity],
    deps: Sequence[tuple[int, int]] = (),
    *,
    baseline: Mapping[int, BaselineBarInput] | None = None,
    report_date: date | None = None,
) -> GanttChart:
    return build_gantt(
        ELEMENTS, activities, deps, calendar=CAL, project_start=START, baseline=baseline, report_date=report_date
    )


# ---------------------------------------------------------------------------
# RULE-SCH-005 ガントの描画データの組み立て
# ---------------------------------------------------------------------------


def test_rows_follow_wbs_order_then_activity_order() -> None:
    """RULE-SCH-005：行の順は WBS の並び（先行順・兄弟は表示順）→ 活動の並び（sort_order、同じなら id）（SCH-11）。"""
    chart = gantt(
        [
            activity(1, 20, 1, "12/01", "12/01"),
            activity(2, 11, 2, "12/01", "12/01"),
            activity(3, 11, 1, "12/01", "12/01"),
            activity(4, 12, 1, "12/01", "12/01"),
            activity(6, 11, 2, "12/01", "12/01"),
        ]
    )
    assert [r.activity_id for r in chart.rows] == [4, 3, 2, 6, 1]
    assert [r.row_index for r in chart.rows] == [0, 1, 2, 3, 4]
    assert [(r.wbs_code, r.work_package_name, r.depth) for r in chart.rows][:2] == [("1.2", "実装", 2), ("1.1", "設計", 2)]
    assert chart.rows[-1].depth == 1


def test_planned_bar_positions_and_calendar_coordinates() -> None:
    """RULE-SCH-005：予定のバーは稼働日の位置と暦日の座標を両方持つ。終わりはその日の終業（金曜は金曜の終業）（7.3）。"""
    chart = gantt([activity(1, 11, 1, "12/03", "12/07", is_critical=True, total_float=0)])
    bar = chart.rows[0].planned
    assert bar is not None and bar.kind == BarKind.PLANNED and not bar.is_milestone
    assert (bar.start.position, bar.end.position) == (2, 5)
    assert bar.start.calendar == CalendarPoint(d("12/03"), Fraction(0))
    assert bar.end.calendar == CalendarPoint(d("12/07"), Fraction(1))
    assert (bar.start_date, bar.end_date) == (d("12/03"), d("12/07"))
    assert chart.rows[0].is_critical and chart.rows[0].total_float == 0

    friday = gantt([activity(1, 11, 1, "12/01", "12/04")]).rows[0].planned
    assert friday is not None
    assert friday.end.position == 4
    assert friday.end.calendar == CalendarPoint(d("12/04"), Fraction(1))  # 月曜の始業ではない


def test_milestone_is_single_point_at_end_of_day() -> None:
    """RULE-SCH-005：マイルストーンは 1 点（その日の終業）で、画面は菱形で描く（SCH-09・7.3）。"""
    chart = gantt([activity(1, 11, 1, "12/04", "12/04", is_milestone=True)])
    bar = chart.rows[0].planned
    assert bar is not None and bar.is_milestone and chart.rows[0].is_milestone
    assert bar.start == bar.end
    assert bar.start.position == 4
    assert bar.start.calendar == CalendarPoint(d("12/04"), Fraction(1))
    with pytest.raises(GanttError):
        gantt([activity(1, 11, 1, "12/04", "12/07", is_milestone=True)])


def test_unscheduled_activity_has_no_planned_bar() -> None:
    """RULE-SCH-005：日程計算の前の活動は予定のバーを出さない。片方の日付だけは入力の誤り。"""
    chart = gantt([activity(1, 11, 1, None, None)])
    assert chart.rows[0].planned is None
    assert chart.range_start is None and chart.non_working_days == ()
    with pytest.raises(GanttError):
        gantt([activity(1, 11, 1, "12/01", None)])


def test_actual_bar_finished_and_in_progress() -> None:
    """RULE-SCH-005：実績のバーは実績開始〜実績終了、未完了なら報告日まで（SCH-12）。"""
    finished = gantt(
        [activity(1, 11, 1, "12/01", "12/03", actual_start=d("12/02"), actual_finish=d("12/04"))]
    ).rows[0].actual
    assert finished is not None and finished.kind == BarKind.ACTUAL and not finished.open_ended
    assert (finished.start.position, finished.end.position) == (1, 4)
    assert (finished.start_date, finished.end_date) == (d("12/02"), d("12/04"))

    running = gantt(
        [activity(1, 11, 1, "12/01", "12/09", actual_start=d("12/02"))], report_date=d("12/08")
    ).rows[0].actual
    assert running is not None and running.open_ended
    assert running.end.position == 6
    assert running.end.calendar == CalendarPoint(d("12/08"), Fraction(1))
    assert running.end_date == d("12/08")


def test_actual_bar_until_non_working_report_date() -> None:
    """RULE-SCH-005：報告日が非稼働日なら、未完了の実績は前の稼働日の終業まで（5.4 の報告日線と同じ位置）。"""
    running = gantt(
        [activity(1, 11, 1, "12/01", "12/09", actual_start=d("12/02"))], report_date=d("12/05")
    ).rows[0].actual
    assert running is not None
    assert running.end.position == 4
    assert running.end.calendar == CalendarPoint(d("12/04"), Fraction(1))
    assert running.end_date == d("12/04")


def test_actual_bar_input_errors() -> None:
    """RULE-SCH-005：未完了の実績に報告日が無い・報告日が実績開始より前・終了が開始より前は推測せずに拒む。"""
    with pytest.raises(GanttError):
        gantt([activity(1, 11, 1, "12/01", "12/09", actual_start=d("12/02"))])
    with pytest.raises(GanttError):
        gantt([activity(1, 11, 1, "12/01", "12/09", actual_start=d("12/08"))], report_date=d("12/07"))
    with pytest.raises(GanttError):
        gantt([activity(1, 11, 1, "12/01", "12/09", actual_start=d("12/08"), actual_finish=d("12/07"))])
    with pytest.raises(GanttError):
        gantt([activity(1, 11, 1, "12/01", "12/09", actual_finish=d("12/07"))])


def test_no_actual_bar_before_start_and_milestone_actual_point() -> None:
    """RULE-SCH-005：実績開始の無い活動は実績のバーを出さない。マイルストーンの実績は実績終了の 1 点。"""
    chart = gantt(
        [
            activity(1, 11, 1, "12/01", "12/02"),
            activity(2, 11, 2, "12/04", "12/04", is_milestone=True, actual_finish=d("12/07")),
        ],
        report_date=d("12/08"),
    )
    assert chart.rows[0].actual is None
    point = chart.rows[1].actual
    assert point is not None and point.is_milestone and point.start == point.end
    assert point.end.calendar == CalendarPoint(d("12/07"), Fraction(1))


def test_baseline_bar_from_selected_baseline() -> None:
    """RULE-SCH-005：選んだベースラインのバーを同じ行に出す。基準に無い活動・未選択はバーなし（SCH-12）。"""
    acts = [activity(1, 11, 1, "12/03", "12/07"), activity(2, 11, 2, "12/08", "12/08", is_milestone=True)]
    chart = gantt(
        acts,
        baseline={
            1: BaselineBarInput(start_date=d("12/01"), finish_date=d("12/04"), is_milestone=False),
        },
    )
    base = chart.rows[0].baseline
    assert base is not None and base.kind == BarKind.BASELINE
    assert (base.start.position, base.end.position) == (0, 4)
    assert chart.rows[1].baseline is None
    assert all(row.baseline is None for row in gantt(acts).rows)


def test_dependency_links_from_predecessor_end_to_successor_start() -> None:
    """RULE-SCH-005：依存の矢印は先行の予定の終わりから後続の予定の始まりへ（SCH-13）。"""
    chart = gantt(
        [
            activity(1, 12, 1, "12/01", "12/04"),
            activity(2, 11, 1, "12/07", "12/08"),
            activity(3, 11, 2, "12/08", "12/08", is_milestone=True),
            activity(4, 11, 3, None, None),
        ],
        [(2, 3), (1, 2), (3, 4)],
    )
    links = {(link.predecessor_id, link.successor_id): link for link in chart.links}
    first = links[(1, 2)]
    assert (first.from_row, first.to_row) == (0, 1)
    assert first.start is not None and first.end is not None
    assert first.start.calendar == CalendarPoint(d("12/04"), Fraction(1))
    assert first.end.calendar == CalendarPoint(d("12/07"), Fraction(0))
    to_milestone = links[(2, 3)]
    assert to_milestone.end is not None and to_milestone.end.position == 6  # マイルストーンの点
    assert links[(3, 4)].end is None  # 予定の無い後続
    assert [(link.predecessor_id, link.successor_id) for link in chart.links] == [(1, 2), (2, 3), (3, 4)]
    with pytest.raises(GanttError):
        gantt([activity(1, 12, 1, "12/01", "12/04")], [(1, 99)])


def test_labels_assignees_and_progress() -> None:
    """RULE-SCH-005：バーに担当者名と進捗率のラベルを付ける。未算定は None（SCH-13）。"""
    chart = gantt(
        [
            activity(1, 11, 1, "12/01", "12/02", assignees=("佐藤", "鈴木"), percent_complete=Decimal("40")),
            activity(2, 11, 2, "12/01", "12/02"),
        ]
    )
    assert chart.rows[0].assignees == ("佐藤", "鈴木")
    assert chart.rows[0].percent_complete == Decimal("40")
    assert chart.rows[1].assignees == () and chart.rows[1].percent_complete is None


def test_non_working_days_in_drawn_range() -> None:
    """RULE-SCH-005：描く範囲（全てのバーの最も早い日〜最も遅い日）の非稼働日を返す（SCH-11・7.3 の ①）。"""
    cal = WorkCalendar.of(holidays=[d("12/09")])
    chart = build_gantt(
        ELEMENTS,
        [
            activity(1, 11, 1, "12/02", "12/04"),
            activity(2, 12, 1, "12/07", "12/10", actual_start=d("12/01"), actual_finish=d("12/03")),
        ],
        [],
        calendar=cal,
        project_start=START,
    )
    assert (chart.range_start, chart.range_end) == (d("12/01"), d("12/10"))
    assert chart.non_working_days == (d("12/05"), d("12/06"), d("12/09"))


def test_non_working_days_in_given_display_range() -> None:
    """RULE-SCH-005：表示範囲を渡せばその範囲の非稼働日を返す（設計書 1.1・CR-008）。応答の範囲はバーの範囲のまま。

    省いた端はバーの範囲の端。バーが無くても両端を渡せばその範囲の非稼働日を返す。
    """
    cal = WorkCalendar.of(holidays=[d("12/09"), d("12/23")])
    acts = [activity(1, 11, 1, "12/02", "12/04")]
    wide = build_gantt(ELEMENTS, acts, [], calendar=cal, project_start=START,
                       range_start=d("11/28"), range_end=d("12/24"))
    assert (wide.range_start, wide.range_end) == (d("12/02"), d("12/04"))  # バーの範囲
    assert wide.non_working_days == (d("11/28"), d("11/29"), d("12/05"), d("12/06"), d("12/09"), d("12/12"),
                                     d("12/13"), d("12/19"), d("12/20"), d("12/23"))
    narrow = build_gantt(ELEMENTS, acts, [], calendar=cal, project_start=START,
                         range_start=d("12/03"), range_end=d("12/03"))
    assert narrow.non_working_days == ()  # 渡した範囲だけ（バーの範囲で広げない）
    end_only = build_gantt(ELEMENTS, acts, [], calendar=cal, project_start=START, range_end=d("12/10"))
    assert end_only.non_working_days == (d("12/05"), d("12/06"), d("12/09"))  # 始まりはバーの始まり 12/02
    start_only = build_gantt(ELEMENTS, acts, [], calendar=cal, project_start=START, range_start=d("11/29"))
    assert start_only.non_working_days == (d("11/29"),)  # 終わりはバーの終わり 12/04
    empty = build_gantt(ELEMENTS, [], [], calendar=cal, project_start=START,
                        range_start=d("12/05"), range_end=d("12/09"))
    assert (empty.range_start, empty.range_end, empty.non_working_days) == (None, None,
                                                                            (d("12/05"), d("12/06"), d("12/09")))
    no_bar_one_end = build_gantt(ELEMENTS, [], [], calendar=cal, project_start=START, range_start=d("12/05"))
    assert no_bar_one_end.non_working_days == ()


def test_display_range_errors() -> None:
    """RULE-SCH-005：表示範囲の始まりが終わりより後、長さが上限（MAX_GANTT_RANGE_DAYS）を超える範囲は拒む（CR-008）。"""
    acts = [activity(1, 11, 1, "12/02", "12/04")]
    with pytest.raises(GanttRangeError) as both:
        build_gantt(ELEMENTS, acts, [], calendar=CAL, project_start=START, range_start=d("12/10"), range_end=d("12/09"))
    assert both.value.fields == ("range_start", "range_end")
    with pytest.raises(GanttRangeError) as start_only:  # 省いた終わり（バーの終わり 12/04）より後の始まり
        build_gantt(ELEMENTS, acts, [], calendar=CAL, project_start=START, range_start=d("12/05"))
    assert start_only.value.fields == ("range_start",)  # 誤りに関わるのは渡した端だけ
    with pytest.raises(GanttRangeError) as end_only:  # 省いた始まり（バーの始まり 12/02）より前の終わり
        build_gantt(ELEMENTS, acts, [], calendar=CAL, project_start=START, range_end=d("12/01"))
    assert end_only.value.fields == ("range_end",)
    # 片方だけ渡し、省いた端にバーが無ければ非稼働日は空（誤りにしない）
    assert build_gantt(ELEMENTS, [], [], calendar=CAL, project_start=START, range_start=d("12/05")).non_working_days == ()
    limit = START + timedelta(days=MAX_GANTT_RANGE_DAYS - 1)
    at_limit = build_gantt(ELEMENTS, acts, [], calendar=CAL, project_start=START, range_start=START, range_end=limit)
    assert at_limit.non_working_days[0] == d("12/05")
    with pytest.raises(GanttRangeError):
        build_gantt(ELEMENTS, acts, [], calendar=CAL, project_start=START, range_start=START,
                    range_end=limit + timedelta(days=1))
    assert issubclass(GanttRangeError, GanttError)


def test_bar_range_without_display_range_has_no_limit() -> None:
    """RULE-SCH-005：表示範囲を渡さなければ、バーの範囲が上限（MAX_GANTT_RANGE_DAYS）より長くても拒まない（6.2 の「省けばバーの範囲」）。

    上限は渡した範囲の歯止めで、渡した端があればバーの範囲の端と合わせた長さに掛かる（CR-008）。
    """
    finish = START + timedelta(days=MAX_GANTT_RANGE_DAYS + 30)
    long_act = GanttActivity(id=1, work_package_id=11, name="長い活動", sort_order=1, is_milestone=False,
                             start_date=START, finish_date=finish)
    chart = build_gantt(ELEMENTS, [long_act], [], calendar=CAL, project_start=START)
    assert chart.range_start is not None and chart.range_end is not None
    assert (chart.range_end - chart.range_start).days + 1 > MAX_GANTT_RANGE_DAYS
    assert chart.non_working_days[0] == d("12/05")  # 2026-12-05（土）から
    assert chart.non_working_days[-1] <= chart.range_end
    assert len(chart.non_working_days) > 2 * (MAX_GANTT_RANGE_DAYS // 7)  # バーの範囲の全ての週末
    with pytest.raises(GanttRangeError) as given:
        build_gantt(ELEMENTS, [long_act], [], calendar=CAL, project_start=START, range_start=START)
    assert given.value.fields == ("range_start",)


def test_input_errors() -> None:
    """RULE-SCH-005：置き場の WP が WBS に無い・活動の ID の重複・WBS の木の誤りは拒む。"""
    with pytest.raises(GanttError):
        gantt([activity(1, 99, 1, "12/01", "12/01")])
    with pytest.raises(GanttError):
        gantt([activity(1, 11, 1, "12/01", "12/01"), activity(1, 12, 1, "12/01", "12/01")])
    with pytest.raises(GanttError):
        build_gantt(
            [GanttElement(id=1, parent_id=2, wbs_code="1.1", name="x")],
            [],
            [],
            calendar=CAL,
            project_start=START,
        )


def test_position_to_calendar_coordinates() -> None:
    """RULE-SCH-005：位置 → 暦日の座標。始まりは添字 floor(p) の日の割合、終わりは添字 ceil(p)−1 の日の割合（7.3）。"""
    axis = WorkdayAxis(calendar=CAL, origin=START)
    assert start_coordinate(axis, 3) == CalendarPoint(d("12/04"), Fraction(0))
    assert end_coordinate(axis, 3) == CalendarPoint(d("12/03"), Fraction(1))
    assert start_coordinate(axis, Fraction(5, 2)) == CalendarPoint(d("12/03"), Fraction(1, 2))
    assert end_coordinate(axis, Fraction(5, 2)) == CalendarPoint(d("12/03"), Fraction(1, 2))
    assert end_coordinate(axis, Decimal("4.25")) == CalendarPoint(d("12/07"), Fraction(1, 4))
    assert start_coordinate(axis, -1) == CalendarPoint(d("11/30"), Fraction(0))  # 原点より前
    assert end_coordinate(axis, 0) == CalendarPoint(d("11/30"), Fraction(1))
    with pytest.raises(GanttError):
        start_coordinate(axis, float("nan"))
