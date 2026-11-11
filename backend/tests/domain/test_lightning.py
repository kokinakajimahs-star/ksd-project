"""進捗ポイント・特殊な描画ルール・判定・イナズマ線の組み立て（app.domain.lightning）の試験。

設計書 5.1 の RULE-PRG-002〜005、5.4・5.6（試験の基準）・7.3、要求仕様書 7.7 BR-07・7.8 BR-08・7.9 BR-09・7.13 BR-13、
PRG-08〜13・18。2026-12 の曜日：12/01＝火、12/04＝金、12/05＝土、12/06＝日、12/07＝月。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from fractions import Fraction

import pytest

from app.domain.calendar import WorkCalendar, WorkdayAxis
from app.domain.gantt import CalendarPoint
from app.domain.lightning import (
    DEFAULT_TOLERANCE_DAYS,
    Judgement,
    LightningActivity,
    LightningError,
    LightningLine,
    RecordedPoint,
    build_lightning_line,
    classify_deviation,
    judge_progress,
    overlay_lightning_lines,
    progress_point,
    restore_lightning_line,
    special_placement,
)
from app.models.prg import JUDGEMENTS

ALL_DAYS = WorkCalendar("1111111")  # 5.6 の試験の基準（全ての曜日を稼働日にする）
WEEKDAYS = WorkCalendar()  # 既定（土日休み）
ORIGIN = date(2026, 12, 1)
REPORT = date(2026, 12, 10)  # 全ての曜日が稼働日なら L＝10


def d(day: int) -> date:
    return date(2026, 12, day)


def act(
    activity_id: int,
    start: int,
    finish: int,
    duration: int,
    percent: Decimal | int | None,
    *,
    milestone: bool = False,
    actual_start: date | None = None,
    actual_finish: date | None = None,
) -> LightningActivity:
    return LightningActivity(
        activity_id=activity_id,
        start_date=d(start),
        finish_date=d(finish),
        duration_days=duration,
        is_milestone=milestone,
        percent_complete=None if percent is None else Decimal(percent),
        actual_start=actual_start,
        actual_finish=actual_finish,
    )


# 5.6 の試験の基準の 4 行（稲妻チャート.txt §6 の例）
SHIRYO = act(1, 1, 21, 21, 25)  # 資料作成
CHOSEI = act(2, 1, 21, 21, 60)  # 関係者調整
KOKAI = act(3, 12, 18, 7, 0)  # 公開準備
KANKYO = act(4, 1, 8, 8, 100, actual_start=d(1), actual_finish=d(8))  # 環境整備


def test_judgement_names_match_snapshot_column_values() -> None:
    """RULE-PRG-004：判定は 6 つで、名前は 4.1 の progress_snapshot_items.judgement の値（A-22・OQ-23）と同じ。"""
    assert tuple(j.value for j in Judgement) == JUDGEMENTS
    assert DEFAULT_TOLERANCE_DAYS == Decimal("0.5")  # BR-09 の既定


# ---------------------------------------------------------------------------
# 5.6 の試験の基準（PRG-09 の受入基準）
# ---------------------------------------------------------------------------


def test_design_5_6_reference_example() -> None:
    """RULE-PRG-002・RULE-PRG-003・RULE-PRG-004・RULE-PRG-005：5.6 の表（稲妻チャート.txt §6 の例）と同じ P・表示の日付・判定・ずれ。

    全ての曜日を稼働日、原点 2026-12-01、報告日 2026-12-10（L＝10）。
    """
    line = build_lightning_line(
        [SHIRYO, CHOSEI, KOKAI, KANKYO], calendar=ALL_DAYS, project_start=ORIGIN, report_date=REPORT
    )
    assert line.report_line == 10
    got = [(v.activity_id, v.point, v.display_date, v.judgement, v.deviation) for v in line.vertices]
    assert got == [
        (1, Decimal("5.25"), d(6), Judgement.BEHIND, Decimal("-4.75")),
        (2, Decimal("12.6"), d(13), Judgement.AHEAD, Decimal("2.6")),
        (3, Decimal(10), d(10), Judgement.NOT_STARTED, Decimal(0)),
        (4, Decimal(10), d(10), Judgement.COMPLETED, Decimal(0)),
    ]
    # 判定の上では完了・未着手（予定内）は予定どおりに数える（BR-09）
    assert [v.judgement.counted_as for v in line.vertices] == [
        Judgement.BEHIND,
        Judgement.AHEAD,
        Judgement.ON_SCHEDULE,
        Judgement.ON_SCHEDULE,
    ]


# ---------------------------------------------------------------------------
# RULE-PRG-002 進捗ポイントの計算
# ---------------------------------------------------------------------------


def test_point_formula_start_plus_duration_times_percent() -> None:
    """RULE-PRG-002：P＝S＋d×進捗率（S は開始予定日の始業の位置）。0% は S、100% は S＋d。"""
    axis = WorkdayAxis(ALL_DAYS, ORIGIN)
    assert progress_point(axis, act(1, 3, 12, 10, 0)) == Decimal(2)
    assert progress_point(axis, act(1, 3, 12, 10, 35)) == Decimal("5.5")
    assert progress_point(axis, act(1, 3, 12, 10, 100)) == Decimal(12)
    # 4.1 の Numeric(5,2) の進捗率でも誤差なく Numeric(12,4) に入る
    assert progress_point(axis, act(1, 1, 21, 21, Decimal("33.33"))) == Decimal("6.9993")


def test_point_counts_working_days_and_holiday_shifts_date_later() -> None:
    """RULE-PRG-002：稼働日の軸で数える（PRG-10）。期間中に休日を足すと、ポイントの日付が休日を飛ばして後ろへずれる。

    土日休み、12/01（火）開始・10 稼働日・50% → P＝5 → 添字 4 の稼働日。休日なしは 12/07（月）、12/03 を休日にすると 12/08。
    """
    activity = act(1, 1, 14, 10, 50)
    plain = WorkdayAxis(WEEKDAYS, ORIGIN)
    assert progress_point(plain, activity) == Decimal(5)
    assert plain.display_date(progress_point(plain, activity)) == d(7)
    with_holiday = WorkdayAxis(WorkCalendar.of(holidays=[d(3)]), ORIGIN)
    assert progress_point(with_holiday, activity) == Decimal(5)
    assert with_holiday.display_date(progress_point(with_holiday, activity)) == d(8)
    # 全ての曜日を稼働日にすると同じ P＝5 が 12/05 になる（土日を飛ばさない）
    assert WorkdayAxis(ALL_DAYS, ORIGIN).display_date(Decimal(5)) == d(5)


def test_milestone_point_is_end_of_its_day() -> None:
    """RULE-PRG-002：マイルストーンは M(m)＝その日の終業の位置（進捗率によらない）。"""
    axis = WorkdayAxis(ALL_DAYS, ORIGIN)
    assert progress_point(axis, act(9, 8, 8, 0, 0, milestone=True)) == Decimal(8)
    assert progress_point(axis, act(9, 8, 8, 0, None, milestone=True)) == Decimal(8)


def test_point_of_not_computed_activity_rejected() -> None:
    """RULE-PRG-002：進捗率が未算定の活動は式で置けない（線の上では報告日線に置く。RULE-PRG-004 の担当）。"""
    with pytest.raises(LightningError):
        progress_point(WorkdayAxis(ALL_DAYS, ORIGIN), act(1, 1, 10, 10, None))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"duration_days": 0, "is_milestone": False},  # 期間 0 はマイルストーン
        {"duration_days": 3, "is_milestone": True},  # マイルストーンの期間は 0
        {"percent_complete": Decimal(101)},
        {"percent_complete": Decimal(-1)},
        {"finish_date": d(1), "start_date": d(5)},
    ],
)
def test_invalid_activity_rejected(kwargs: dict[str, object]) -> None:
    """RULE-PRG-002：入力の検査（期間とマイルストーン・進捗率 0〜100・終了予定日は開始予定日以後）。"""
    base: dict[str, object] = {
        "activity_id": 1,
        "start_date": d(1),
        "finish_date": d(10),
        "duration_days": 10,
        "is_milestone": False,
        "percent_complete": Decimal(10),
    }
    base.update(kwargs)
    with pytest.raises(LightningError):
        LightningActivity(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# RULE-PRG-003 特殊な描画ルール
# ---------------------------------------------------------------------------


def test_completed_on_or_before_report_date_goes_on_line() -> None:
    """RULE-PRG-003：報告日以前に終了予定で完了（100）した活動は報告日線上（BR-08 の 1）。終了予定日＝報告日も含む。"""
    assert special_placement(act(1, 1, 10, 10, 100), REPORT) is Judgement.COMPLETED
    assert special_placement(act(1, 1, 8, 8, 100), REPORT) is Judgement.COMPLETED


def test_completed_after_report_date_follows_formula_and_is_ahead() -> None:
    """RULE-PRG-003：報告日より後に終了予定で完了した活動は式のとおり報告日線より右（前倒し）（BR-08 の 3）。"""
    activity = act(1, 1, 15, 15, 100)
    assert special_placement(activity, REPORT) is None
    judged = judge_progress(WorkdayAxis(ALL_DAYS, ORIGIN), activity, REPORT)
    assert (judged.point, judged.deviation, judged.judgement) == (Decimal(15), Decimal(5), Judgement.AHEAD)


def test_not_started_future_activity_goes_on_line() -> None:
    """RULE-PRG-003：報告日より後に開始予定で未着手（実績開始日なし・0%）の活動は報告日線上（BR-08 の 2）。"""
    assert special_placement(act(1, 11, 15, 5, 0), REPORT) is Judgement.NOT_STARTED


def test_not_started_rule_needs_all_three_conditions() -> None:
    """RULE-PRG-003：開始予定日＝報告日・実績開始日あり・進捗率が 0 でない、のどれかなら BR-08 の 2 に当たらない。"""
    assert special_placement(act(1, 10, 15, 6, 0), REPORT) is None  # 開始予定日が報告日（＞ではない）
    assert special_placement(act(1, 11, 15, 5, 0, actual_start=d(10)), REPORT) is None  # 着手した
    assert special_placement(act(1, 11, 15, 5, 20), REPORT) is None  # 進んでいる
    assert special_placement(act(1, 11, 15, 5, None), REPORT) is None  # 未算定


# ---------------------------------------------------------------------------
# RULE-PRG-004 遅れ・前倒しの判定
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("percent", "deviation", "judgement"),
    [
        (Decimal("47.25"), Decimal("-0.55"), Judgement.BEHIND),  # δ < −0.5
        (Decimal("47.5"), Decimal("-0.5"), Judgement.ON_SCHEDULE),  # δ ＝ −0.5（境界は予定どおり）
        (Decimal(50), Decimal(0), Judgement.ON_SCHEDULE),
        (Decimal("52.5"), Decimal("0.5"), Judgement.ON_SCHEDULE),  # δ ＝ ＋0.5（境界は予定どおり）
        (Decimal("52.75"), Decimal("0.55"), Judgement.AHEAD),  # δ > ＋0.5
    ],
)
def test_tolerance_boundaries(percent: Decimal, deviation: Decimal, judgement: Judgement) -> None:
    """RULE-PRG-004：δ＝P−L。δ < −許容幅 は遅れ、δ > 許容幅 は前倒し、境界を含むそれ以外は予定どおり（既定 0.5）。

    12/01 開始・20 日、L＝10 なので P＝20×進捗率。
    """
    judged = judge_progress(WorkdayAxis(ALL_DAYS, ORIGIN), act(1, 1, 20, 20, percent), REPORT)
    assert judged.deviation == deviation
    assert judged.judgement is judgement


def test_tolerance_is_configurable() -> None:
    """RULE-PRG-004：許容幅はプロジェクトで設定できる（BR-09）。0 なら少しの遅れも遅れ。負は拒む。"""
    axis = WorkdayAxis(ALL_DAYS, ORIGIN)
    activity = act(1, 1, 20, 20, Decimal("49.75"))  # P＝9.95、δ＝−0.05
    assert judge_progress(axis, activity, REPORT).judgement is Judgement.ON_SCHEDULE
    assert judge_progress(axis, activity, REPORT, Decimal(0)).judgement is Judgement.BEHIND
    assert classify_deviation(Decimal(-2), "2") is Judgement.ON_SCHEDULE
    with pytest.raises(LightningError):
        classify_deviation(Decimal(0), Decimal("-0.1"))


def test_not_computed_sits_on_report_line() -> None:
    """RULE-PRG-004：進捗率が未算定の活動は判定を「未算定」とし、線の上では L に置く（線を途切れさせない）。"""
    judged = judge_progress(WorkdayAxis(ALL_DAYS, ORIGIN), act(1, 1, 5, 5, None), REPORT)
    assert (judged.point, judged.deviation, judged.judgement) == (Decimal(10), Decimal(0), Judgement.NOT_COMPUTED)
    assert judged.judgement.counted_as is Judgement.NOT_COMPUTED


def test_milestones_are_judged_by_deviation() -> None:
    """RULE-PRG-004：マイルストーンは M(m) のずれで判定する。達成済みで予定日≦報告日は完了、先の未着手は未着手（予定内）。"""
    axis = WorkdayAxis(ALL_DAYS, ORIGIN)
    overdue = judge_progress(axis, act(9, 8, 8, 0, 0, milestone=True), REPORT)
    assert (overdue.point, overdue.deviation, overdue.judgement) == (Decimal(8), Decimal(-2), Judgement.BEHIND)
    due_today = judge_progress(axis, act(9, 10, 10, 0, 0, milestone=True), REPORT)
    assert (due_today.deviation, due_today.judgement) == (Decimal(0), Judgement.ON_SCHEDULE)
    done = judge_progress(axis, act(9, 8, 8, 0, 100, milestone=True, actual_finish=d(8)), REPORT)
    assert done.judgement is Judgement.COMPLETED
    future = judge_progress(axis, act(9, 15, 15, 0, 0, milestone=True), REPORT)
    assert (future.point, future.judgement) == (Decimal(10), Judgement.NOT_STARTED)


def test_started_future_activity_is_ahead() -> None:
    """RULE-PRG-004：開始予定が報告日より後でも、着手していれば式で置く（S＞L なので前倒し）。"""
    judged = judge_progress(WorkdayAxis(ALL_DAYS, ORIGIN), act(1, 12, 16, 5, 0, actual_start=d(10)), REPORT)
    assert (judged.point, judged.deviation, judged.judgement) == (Decimal(11), Decimal(1), Judgement.AHEAD)


def test_report_line_on_non_working_day_is_previous_workday_end() -> None:
    """RULE-PRG-004：報告日が非稼働日なら報告日線は前の稼働日の終業（BR-07）。土 12/05 の L は金 12/04 の終業＝4。"""
    axis = WorkdayAxis(WEEKDAYS, ORIGIN)
    judged = judge_progress(axis, act(1, 1, 14, 10, 40), d(5))  # P＝4
    assert (judged.point, judged.deviation, judged.judgement) == (Decimal(4), Decimal(0), Judgement.ON_SCHEDULE)


# ---------------------------------------------------------------------------
# RULE-PRG-005 イナズマ線の組み立て
# ---------------------------------------------------------------------------


def test_line_keeps_row_order_and_has_calendar_coordinates() -> None:
    """RULE-PRG-005：行の順にポイントを並べた折れ線と報告日線。位置と暦日上の座標（日付＋その日の中の割合）を両方返す（7.3）。"""
    line = build_lightning_line(
        [KANKYO, SHIRYO, CHOSEI], calendar=ALL_DAYS, project_start=ORIGIN, report_date=REPORT
    )
    assert [(v.row_index, v.activity_id) for v in line.vertices] == [(0, 4), (1, 1), (2, 2)]
    assert line.report_line_coordinate == CalendarPoint(date=d(10), fraction=Fraction(1))
    assert line.vertices[1].coordinate == CalendarPoint(date=d(6), fraction=Fraction(1, 4))  # 5.25
    assert line.vertices[2].coordinate == CalendarPoint(date=d(13), fraction=Fraction(3, 5))  # 12.6
    assert line.tolerance_days == Decimal("0.5")


def test_report_line_coordinate_on_weekend() -> None:
    """RULE-PRG-005：報告日線（PRG-08）は報告日の終業。土曜の報告日は金曜の終業に描く。"""
    line = build_lightning_line([act(1, 1, 14, 10, 40)], calendar=WEEKDAYS, project_start=ORIGIN, report_date=d(5))
    assert line.report_line == 4
    assert line.report_line_coordinate == CalendarPoint(date=d(4), fraction=Fraction(1))


def test_empty_line_and_duplicate_rows() -> None:
    """RULE-PRG-005：活動が無ければ頂点の無い線（報告日線だけ）。同じ活動が 2 行にある入力は拒む。"""
    empty = build_lightning_line([], calendar=ALL_DAYS, project_start=ORIGIN, report_date=REPORT)
    assert empty.vertices == () and empty.report_line == 10
    with pytest.raises(LightningError):
        build_lightning_line([SHIRYO, SHIRYO], calendar=ALL_DAYS, project_start=ORIGIN, report_date=REPORT)


def test_restore_uses_recorded_values_only() -> None:
    """RULE-PRG-005：過去の線は記録した値だけから描く（BR-13）。記録の行の順に並べ、計画・進捗から計算し直さない。"""
    recorded = [
        RecordedPoint(activity_id=2, row_order=1, point=Decimal("3.5"), deviation=Decimal("-1.5"), judgement=Judgement.BEHIND),
        RecordedPoint(activity_id=1, row_order=0, point=Decimal(5), deviation=Decimal(0), judgement=Judgement.COMPLETED),
    ]
    line = restore_lightning_line(
        report_date=d(5),
        report_line=5,
        tolerance_days=Decimal("0.5"),
        points=recorded,
        calendar=ALL_DAYS,
        project_start=ORIGIN,
    )
    assert [(v.row_index, v.activity_id, v.point, v.judgement) for v in line.vertices] == [
        (0, 1, Decimal(5), Judgement.COMPLETED),
        (1, 2, Decimal("3.5"), Judgement.BEHIND),
    ]
    assert line.vertices[1].display_date == d(4)
    with pytest.raises(LightningError):
        restore_lightning_line(
            report_date=d(5),
            report_line=5,
            tolerance_days=Decimal("0.5"),
            points=[recorded[0], RecordedPoint(3, 1, Decimal(1), Decimal(0), Judgement.ON_SCHEDULE)],
            calendar=ALL_DAYS,
            project_start=ORIGIN,
        )


def _line(report_day: int, activity_ids: list[int | None]) -> LightningLine:
    points = [
        RecordedPoint(activity_id=a, row_order=i, point=Decimal(report_day), deviation=Decimal(0), judgement=Judgement.ON_SCHEDULE)
        for i, a in enumerate(activity_ids)
    ]
    return restore_lightning_line(
        report_date=d(report_day),
        report_line=report_day,
        tolerance_days=Decimal("0.5"),
        points=points,
        calendar=ALL_DAYS,
        project_start=ORIGIN,
    )


def test_overlay_orders_by_report_date_and_maps_to_current_rows() -> None:
    """RULE-PRG-005：複数の報告日の線を重ねる（PRG-18）。古い順に線の種類を振り、頂点を今のガントの行へ置き直す。

    今のガントに無い活動（消した活動・紐付けの切れた写し）は外して返す。
    """
    later = _line(10, [1, 2, 3])
    earlier = _line(3, [2, None, 1])
    overlaid = overlay_lightning_lines([later, earlier], row_of={1: 5, 2: 0})
    assert [(o.style_index, o.line.report_date) for o in overlaid] == [(0, d(3)), (1, d(10))]
    assert [(v.row_index, v.activity_id) for v in overlaid[0].line.vertices] == [(0, 2), (5, 1)]
    assert overlaid[0].missing_activity_ids == (None,)
    assert [(v.row_index, v.activity_id) for v in overlaid[1].line.vertices] == [(0, 2), (5, 1)]
    assert overlaid[1].missing_activity_ids == (3,)


def test_overlay_rejects_same_report_date_twice() -> None:
    """RULE-PRG-005：同じ報告日の線を 2 本重ねる入力は拒む（線の種類が決まらない）。"""
    with pytest.raises(LightningError):
        overlay_lightning_lines([_line(3, [1]), _line(3, [1])], row_of={1: 0})
