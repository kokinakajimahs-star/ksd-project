"""稼働日の判定・日付の数え方・稼働日の軸（app.domain.calendar）の試験。

設計書 5.1・5.4・5.6（試験の基準）、要求仕様書 7.4 BR-04・7.7 BR-07、PRJ-04・SCH-03・PRG-08・PRG-10。
2026-12 の曜日：12/01＝火、12/05＝土、12/06＝日、12/07＝月。
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction

import pytest

from app.domain.calendar import (
    DEFAULT_WORKING_WEEKDAYS,
    ActivityDates,
    CalendarError,
    WorkCalendar,
    WorkdayAxis,
)

ALL_DAYS = "1111111"  # 5.6 の試験の基準（全ての曜日を稼働日にする）


def d(month_day: str) -> date:
    """'12/05' → 2026-12-05（'01/04' のように 1 月なら 2027 年）。"""
    month, day = (int(x) for x in month_day.split("/"))
    return date(2026 if month >= 11 else 2027, month, day)


def brute_count(cal: WorkCalendar, start: date, end_exclusive: date) -> int:
    """1 日ずつ数えた稼働日の数（実装の式と突き合わせるための素朴な版）。"""
    n, cur = 0, start
    while cur < end_exclusive:
        n += cal.is_working_day(cur)
        cur += timedelta(days=1)
    return n


# ---------------------------------------------------------------------------
# RULE-CAL-001 稼働日の判定と日付の数え方
# ---------------------------------------------------------------------------


def test_default_calendar_is_weekdays_only() -> None:
    """RULE-CAL-001：既定のカレンダーは土日休み（4.1 の既定 1111100。PRJ-04 の受入基準）。"""
    cal = WorkCalendar()
    assert cal.working_weekdays == DEFAULT_WORKING_WEEKDAYS == "1111100"
    assert [cal.is_working_day(d(f"12/{i:02d}")) for i in range(1, 8)] == [
        True,  # 火
        True,  # 水
        True,  # 木
        True,  # 金
        False,  # 土
        False,  # 日
        True,  # 月
    ]


def test_holiday_is_not_working_day() -> None:
    """RULE-CAL-001：稼働日は非稼働の曜日でも休日でもない日（BR-04）。休日を足すと稼働の曜日でも稼働日でなくなる。"""
    cal = WorkCalendar.of(holidays=[d("12/03")])
    assert not cal.is_working_day(d("12/03"))
    assert cal.is_working_day(d("12/02"))
    assert cal.is_working_day(d("12/04"))
    # 非稼働の曜日を稼働にすれば、その曜日は稼働日になる
    assert WorkCalendar("1111110").is_working_day(d("12/05"))


@pytest.mark.parametrize("weekdays", ["111110", "11111000", "11111a0", "", "0000000"])
def test_invalid_working_weekdays_rejected(weekdays: str) -> None:
    """RULE-CAL-001：working_weekdays は 0/1 の 7 文字。稼働の曜日が 0 件のカレンダーは数えられないので拒む。"""
    with pytest.raises(CalendarError):
        WorkCalendar(weekdays)


def test_roll_forward_and_backward() -> None:
    """RULE-CAL-001：非稼働日の寄せ。開始日は次の稼働日へ、終了日は前の稼働日へ（BR-04）。稼働日はそのまま。"""
    cal = WorkCalendar()
    assert cal.roll_forward(d("12/05")) == d("12/07")  # 土 → 月
    assert cal.roll_forward(d("12/06")) == d("12/07")  # 日 → 月
    assert cal.roll_backward(d("12/05")) == d("12/04")  # 土 → 金
    assert cal.roll_backward(d("12/06")) == d("12/04")  # 日 → 金
    assert cal.roll_forward(d("12/02")) == d("12/02")
    assert cal.roll_backward(d("12/02")) == d("12/02")

    # 休日が週末に続くと、さらに先（前）へ寄せる
    cal = WorkCalendar.of(holidays=[d("12/04"), d("12/07"), d("12/08")])
    assert cal.roll_forward(d("12/05")) == d("12/09")
    assert cal.roll_backward(d("12/07")) == d("12/03")


def test_count_matches_day_by_day_counting() -> None:
    """RULE-CAL-001：稼働日の数は 1 日ずつ数えたものと一致する（週の端・休日・非稼働の曜日に重なる休日を含む）。"""
    calendars = [
        WorkCalendar(),
        WorkCalendar(ALL_DAYS),
        WorkCalendar.of("1010100", [d("12/02"), d("12/04"), d("12/25")]),  # 12/02（水）は非稼働の曜日に重なる休日
        WorkCalendar.of(holidays=[d("12/05"), d("12/31"), d("01/01"), d("01/02")]),  # 12/05（土）は非稼働日に重なる
    ]
    base = d("11/20")
    for cal in calendars:
        for i in range(0, 30):
            for length in range(0, 50):
                start = base + timedelta(days=i)
                end = start + timedelta(days=length)
                assert cal.count_between(start, end) == brute_count(cal, start, end), (cal, start, end)
    with pytest.raises(CalendarError):
        WorkCalendar().count_between(d("12/02"), d("12/01"))
    assert WorkCalendar().count_inclusive(d("12/02"), d("12/01")) == 0
    assert WorkCalendar().count_inclusive(d("12/01"), d("12/07")) == 5


def test_add_working_days() -> None:
    """RULE-CAL-001：稼働日から n 稼働日後・前。非稼働日と休日を飛ばす。起点が稼働日でなければ拒む。"""
    cal = WorkCalendar.of(holidays=[d("12/09")])
    assert cal.add_working_days(d("12/04"), 0) == d("12/04")
    assert cal.add_working_days(d("12/04"), 1) == d("12/07")  # 金 → 月（週末を飛ばす）
    assert cal.add_working_days(d("12/07"), 2) == d("12/10")  # 12/09 の休日を飛ばす
    assert cal.add_working_days(d("12/07"), -1) == d("12/04")
    assert cal.add_working_days(d("12/10"), -2) == d("12/07")
    with pytest.raises(CalendarError):
        cal.add_working_days(d("12/05"), 1)


def test_resolve_from_start_and_duration_5_6_standard() -> None:
    """RULE-CAL-001：開始日＋期間 → 終了日。5.6 の試験の基準（全ての曜日が稼働日）の計画と一致する。"""
    cal = WorkCalendar(ALL_DAYS)
    assert cal.resolve_dates(start=d("12/01"), duration=21) == ActivityDates(d("12/01"), 21, d("12/21"))  # 資料作成
    assert cal.resolve_dates(start=d("12/12"), duration=7) == ActivityDates(d("12/12"), 7, d("12/18"))  # 公開準備
    assert cal.resolve_dates(start=d("12/01"), end=d("12/08")).duration == 8  # 環境整備
    assert cal.resolve_dates(end=d("12/21"), duration=21).start == d("12/01")


def test_resolve_end_is_dth_working_day() -> None:
    """RULE-CAL-001：期間 d の活動の終了日は、開始日から数えて d 番目の稼働日（BR-04）。週末・休日を飛ばす。"""
    cal = WorkCalendar()
    assert cal.resolve_dates(start=d("12/01"), duration=1).end == d("12/01")  # 期間 1 は開始日＝終了日
    assert cal.resolve_dates(start=d("12/01"), duration=4).end == d("12/04")
    assert cal.resolve_dates(start=d("12/01"), duration=5).end == d("12/07")  # 火水木金・月
    # 休日を足すと終了日が後ろへずれる（PRJ-04 の受入基準：休日を足すと日程計算の結果が変わる）
    holiday_cal = WorkCalendar.of(holidays=[d("12/03")])
    assert holiday_cal.resolve_dates(start=d("12/01"), duration=5).end == d("12/08")


def test_resolve_from_start_and_end_counts_working_days() -> None:
    """RULE-CAL-001：開始日＋終了日 → 期間（両端を含む稼働日の数）。非稼働日の入力は寄せてから数える。"""
    cal = WorkCalendar()
    assert cal.resolve_dates(start=d("12/01"), end=d("12/07")) == ActivityDates(d("12/01"), 5, d("12/07"))
    # 開始日 土 → 月、終了日 日 → 金
    assert cal.resolve_dates(start=d("12/05"), end=d("12/13")) == ActivityDates(d("12/07"), 5, d("12/11"))


def test_resolve_from_end_and_duration() -> None:
    """RULE-CAL-001：終了日＋期間 → 開始日。終了日が非稼働日なら前の稼働日へ寄せる。"""
    cal = WorkCalendar()
    assert cal.resolve_dates(end=d("12/11"), duration=5) == ActivityDates(d("12/07"), 5, d("12/11"))
    assert cal.resolve_dates(end=d("12/13"), duration=5) == ActivityDates(d("12/07"), 5, d("12/11"))  # 日 → 金
    assert cal.resolve_dates(end=d("12/08"), duration=2) == ActivityDates(d("12/07"), 2, d("12/08"))
    assert cal.resolve_dates(end=d("12/07"), duration=2) == ActivityDates(d("12/04"), 2, d("12/07"))  # 週末を飛ばす


def test_resolve_milestone_has_single_date() -> None:
    """RULE-CAL-001：期間 0 はマイルストーンで、日付は 1 つ（開始日＝終了日。BR-04）。非稼働日は寄せる。"""
    cal = WorkCalendar()
    m = cal.resolve_dates(start=d("12/02"), duration=0)
    assert m == ActivityDates(d("12/02"), 0, d("12/02"))
    assert m.is_milestone
    assert cal.resolve_dates(start=d("12/05"), duration=0) == ActivityDates(d("12/07"), 0, d("12/07"))
    assert cal.resolve_dates(end=d("12/05"), duration=0) == ActivityDates(d("12/04"), 0, d("12/04"))
    assert not cal.resolve_dates(start=d("12/02"), duration=1).is_milestone


def test_resolve_round_trip_over_calendars() -> None:
    """RULE-CAL-001：3 つのうち 2 つから求めた残りは、別の 2 つから求め直しても一致する（境界の網羅）。"""
    calendars = [
        WorkCalendar(),
        WorkCalendar(ALL_DAYS),
        WorkCalendar.of("1010100", [d("12/09"), d("12/25")]),
        WorkCalendar.of(holidays=[d("12/29"), d("12/30"), d("12/31"), d("01/01"), d("01/02")]),  # 年をまたぐ休み
    ]
    for cal in calendars:
        for i in range(0, 21):
            start = d("12/01") + timedelta(days=i)
            for duration in range(1, 30):
                r = cal.resolve_dates(start=start, duration=duration)
                assert cal.is_working_day(r.start) and cal.is_working_day(r.end)
                assert cal.count_inclusive(r.start, r.end) == duration
                assert cal.resolve_dates(start=start, end=r.end) == r
                assert cal.resolve_dates(end=r.end, duration=duration) == r


@pytest.mark.parametrize(
    "kwargs",
    [
        {"start": date(2026, 12, 1)},
        {"duration": 3},
        {},
        {"start": date(2026, 12, 1), "duration": 3, "end": date(2026, 12, 3)},
        {"start": date(2026, 12, 1), "duration": -1},
        {"end": date(2026, 12, 1), "duration": -1},
    ],
)
def test_resolve_rejects_undefined_input(kwargs: dict[str, object]) -> None:
    """RULE-CAL-001：ちょうど 2 つでない入力・負の期間は推測せずに拒む（SCH-03 は 2 つから残りを求める）。"""
    with pytest.raises(CalendarError):
        WorkCalendar().resolve_dates(**kwargs)  # type: ignore[arg-type]


def test_resolve_rejects_end_before_start() -> None:
    """RULE-CAL-001：終了日が開始日より前は拒む。寄せた結果で逆転するとき（土〜日の入力）も拒む。"""
    cal = WorkCalendar()
    with pytest.raises(CalendarError):
        cal.resolve_dates(start=d("12/03"), end=d("12/02"))
    with pytest.raises(CalendarError):
        cal.resolve_dates(start=d("12/05"), end=d("12/06"))  # 開始 → 月 12/07、終了 → 金 12/04


# ---------------------------------------------------------------------------
# RULE-CAL-002 稼働日の軸
# ---------------------------------------------------------------------------


def test_5_6_standard_report_line_and_display_dates() -> None:
    """RULE-CAL-002：5.6 の試験の基準。全ての曜日が稼働日、原点 12/01、報告日 12/10 で L＝10。表示の日付は表のとおり。"""
    axis = WorkdayAxis(WorkCalendar(ALL_DAYS), d("12/01"))
    assert axis.report_line(d("12/10")) == 10
    # 資料作成：P＝0＋21×25%＝5.25 → 12/06
    assert axis.display_date(Decimal("5.25")) == d("12/06")
    assert axis.display_date(0 + 21 * 0.25) == d("12/06")
    # 関係者調整：P＝0＋21×60%＝12.6 → 12/13（float の誤差があっても同じ日）
    assert axis.display_date(Decimal("12.6")) == d("12/13")
    assert axis.display_date(21 * 0.6) == d("12/13")
    # 公開準備（未着手・予定内）・環境整備（完了）：P＝L＝10 → 報告日 12/10
    assert axis.display_date(10) == d("12/10")
    # 計画の開始位置（S）は index で求める
    assert axis.index(d("12/01")) == 0
    assert axis.index(d("12/12")) == 11


def test_index_counts_working_days_from_origin() -> None:
    """RULE-CAL-002：index(d)＝原点以上 d 未満の稼働日の数。原点より前は d 以上 原点未満の稼働日の数の負。"""
    axis = WorkdayAxis(WorkCalendar(), d("12/07"))  # 原点 月
    assert axis.index(d("12/07")) == 0
    assert axis.index(d("12/08")) == 1
    assert axis.index(d("12/11")) == 4  # 金
    assert axis.index(d("12/14")) == 5  # 翌週の月
    assert axis.index(d("12/04")) == -1  # 前の金
    assert axis.index(d("12/03")) == -2
    assert axis.index(d("12/05")) == 0  # 土：次の稼働日（原点の月）の始業
    assert axis.index(d("12/06")) == 0
    assert axis.index(d("11/30")) == -5  # 前の週の月


def test_non_working_day_index_is_next_working_day_start() -> None:
    """RULE-CAL-002：非稼働日の index は次の稼働日の始業（＝前の稼働日の終業）の位置。"""
    axis = WorkdayAxis(WorkCalendar.of(holidays=[d("12/14")]), d("12/01"))
    for non_working in (d("12/12"), d("12/13"), d("12/14")):  # 土・日・休日（月）
        assert axis.index(non_working) == axis.index(d("12/15")) == axis.index(d("12/11")) + 1


def test_workday_at_inverts_index() -> None:
    """RULE-CAL-002：添字 i の稼働日は [i, i+1) を占める（index の逆）。負の添字は原点より前の稼働日。"""
    cal = WorkCalendar.of(holidays=[d("12/09"), d("11/27")])
    axis = WorkdayAxis(cal, d("12/01"))
    day = d("11/16")
    while day <= d("01/31"):
        if cal.is_working_day(day):
            assert axis.workday_at(axis.index(day)) == day
        day += timedelta(days=1)
    assert axis.workday_at(0) == d("12/01")
    assert axis.workday_at(-1) == d("11/30")
    assert axis.workday_at(-2) == d("11/26")  # 11/27（金）は休日、11/28・29 は週末


def test_origin_on_non_working_day() -> None:
    """RULE-CAL-002：原点（プロジェクトの開始日）が非稼働日なら、添字 0 は次の稼働日、添字 −1 は前の稼働日。"""
    axis = WorkdayAxis(WorkCalendar(), d("12/05"))  # 土
    assert axis.index(d("12/05")) == 0
    assert axis.index(d("12/07")) == 0
    assert axis.workday_at(0) == d("12/07")
    assert axis.workday_at(-1) == d("12/04")
    assert axis.index(d("12/04")) == -1
    assert axis.report_line(d("12/05")) == 0  # 原点から報告日までの稼働日は 0 件


def test_report_line_is_end_of_report_day() -> None:
    """RULE-CAL-002：報告日線＝index(r)＋(r が稼働日なら 1、でなければ 0)。非稼働日の報告日は前の稼働日の終業（BR-07）。"""
    axis = WorkdayAxis(WorkCalendar(), d("12/01"))
    assert axis.report_line(d("12/01")) == 1
    assert axis.report_line(d("12/04")) == 4  # 金の終業
    assert axis.report_line(d("12/05")) == 4  # 土：前の稼働日（金）の終業
    assert axis.report_line(d("12/06")) == 4
    assert axis.report_line(d("12/07")) == 5
    # 原点から報告日までの稼働日の数に等しい
    for r in (d("12/01"), d("12/05"), d("12/10"), d("12/20")):
        assert axis.report_line(r) == axis.calendar.count_inclusive(d("12/01"), r)


def test_display_date_on_boundaries() -> None:
    """RULE-CAL-002：表示の日付は添字 max(ceil(p)−1, 0) の稼働日。境目はちょうど終業する前の稼働日（BR-07）。"""
    axis = WorkdayAxis(WorkCalendar(), d("12/01"))
    assert axis.display_date(4) == d("12/04")  # 金の終業
    assert axis.display_date(Fraction(4001, 1000)) == d("12/07")  # 境目を越えると次の稼働日（週末を飛ばす）
    assert axis.display_date(Decimal("3.5")) == d("12/04")
    assert axis.display_date(1) == d("12/01")
    assert axis.display_date(Decimal("0.01")) == d("12/01")
    assert axis.display_date(0) == d("12/01")  # max(…, 0)
    assert axis.display_date(-3.5) == d("12/01")
    with pytest.raises(CalendarError):
        axis.display_date(float("nan"))


def test_display_date_of_report_line_is_report_date() -> None:
    """RULE-CAL-002：報告日線上のポイントは報告日の日付になる（BR-07 の例）。非稼働日の報告日は前の稼働日の日付。"""
    axis = WorkdayAxis(WorkCalendar.of(holidays=[d("12/08")]), d("12/01"))
    for r in (d("12/01"), d("12/04"), d("12/07"), d("12/09"), d("12/18")):
        assert axis.display_date(axis.report_line(r)) == r
    assert axis.display_date(axis.report_line(d("12/05"))) == d("12/04")  # 土 → 金
    assert axis.display_date(axis.report_line(d("12/08"))) == d("12/07")  # 休日 → 前の稼働日


def test_holiday_shifts_display_date_later() -> None:
    """RULE-CAL-002：期間中に休日を足すと、進捗ポイントの日付が休日を飛ばして後ろへずれる（PRG-10 の受入基準）。"""
    origin = d("12/01")
    p = Decimal("5.25")  # 5.6 の資料作成の P
    assert WorkdayAxis(WorkCalendar(ALL_DAYS), origin).display_date(p) == d("12/06")
    with_holiday = WorkdayAxis(WorkCalendar.of(ALL_DAYS, [d("12/03")]), origin)
    assert with_holiday.display_date(p) == d("12/07")
    on_the_day = WorkdayAxis(WorkCalendar.of(ALL_DAYS, [d("12/06")]), origin)
    assert on_the_day.display_date(p) == d("12/07")  # 表示の日付そのものが休日なら次の稼働日
    # 報告日線も休日の分だけ稼働日の数が減る（PRJ-04：休日を足すと進捗ポイントの結果が変わる）
    assert with_holiday.report_line(d("12/10")) == 9
