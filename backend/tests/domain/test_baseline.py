"""ベースラインとの差異（app.domain.baseline）の試験。

設計書 5.1、要求仕様書 SCH-17・SCH-18、7.12 BR-12。
2026-12 の曜日：12/01＝火、12/04＝金、12/05＝土、12/06＝日、12/07＝月。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.domain.baseline import (
    BaselineActivityItem,
    BaselineError,
    BaselineVersion,
    PlanActivity,
    VarianceStatus,
    compare_with_baseline,
    select_baseline,
    working_day_difference,
)
from app.domain.calendar import WorkCalendar

CAL = WorkCalendar()


def d(month_day: str) -> date:
    month, day = (int(x) for x in month_day.split("/"))
    return date(2026 if month >= 11 else 2027, month, day)


def item(activity_id: int | None, start: str, finish: str, hours: str | None = "8", **kw: object) -> BaselineActivityItem:
    return BaselineActivityItem(
        activity_id=activity_id,
        wbs_code=str(kw.get("wbs_code", "1.1")),
        name=str(kw.get("name", f"基準{activity_id}")),
        start_date=d(start),
        finish_date=d(finish),
        is_milestone=bool(kw.get("is_milestone", False)),
        estimate_hours=None if hours is None else Decimal(hours),
    )


def plan(activity_id: int, start: str, finish: str, hours: str | None = "8", **kw: object) -> PlanActivity:
    return PlanActivity(
        activity_id=activity_id,
        wbs_code=str(kw.get("wbs_code", "1.1")),
        name=str(kw.get("name", f"活動{activity_id}")),
        start_date=d(start),
        finish_date=d(finish),
        is_milestone=bool(kw.get("is_milestone", False)),
        estimate_hours=None if hours is None else Decimal(hours),
    )


# ---------------------------------------------------------------------------
# RULE-SCH-004 ベースラインとの差異
# ---------------------------------------------------------------------------


def test_working_day_difference() -> None:
    """RULE-SCH-004：日付の差は稼働日で数える（最新 − 基準。前へずれたら負。週末・休日を数えない）。"""
    assert working_day_difference(CAL, d("12/01"), d("12/01")) == 0
    assert working_day_difference(CAL, d("12/01"), d("12/03")) == 2
    assert working_day_difference(CAL, d("12/04"), d("12/07")) == 1  # 金→月は 1 稼働日
    assert working_day_difference(CAL, d("12/07"), d("12/04")) == -1
    holiday = WorkCalendar.of(holidays=[d("12/08")])
    assert working_day_difference(holiday, d("12/07"), d("12/09")) == 1


def test_matched_activity_start_finish_and_estimate_variance() -> None:
    """RULE-SCH-004：活動ごとに開始・終了の差（稼働日）と見積の差を出す（SCH-18）。"""
    (row,) = compare_with_baseline(
        [item(1, "12/01", "12/04", "8")],
        [plan(1, "12/03", "12/07", "12.50")],
        calendar=CAL,
    )
    assert row.status == VarianceStatus.MATCHED
    assert (row.start_variance_days, row.finish_variance_days) == (2, 1)
    assert (row.baseline_start, row.baseline_finish, row.current_start, row.current_finish) == (
        d("12/01"),
        d("12/04"),
        d("12/03"),
        d("12/07"),
    )
    assert row.estimate_variance_hours == Decimal("4.50")
    assert row.name == "活動1"  # 名称・WBS ID は最新の計画の値


def test_earlier_schedule_and_lower_estimate_are_negative() -> None:
    """RULE-SCH-004：前倒し・見積の減少は負の差。変わらなければ 0。"""
    rows = compare_with_baseline(
        [item(1, "12/07", "12/09", "16"), item(2, "12/01", "12/01", "8")],
        [plan(1, "12/03", "12/04", "10"), plan(2, "12/01", "12/01", "8")],
        calendar=CAL,
    )
    assert (rows[0].start_variance_days, rows[0].finish_variance_days, rows[0].estimate_variance_hours) == (
        -2,
        -3,
        Decimal("-6"),
    )
    assert (rows[1].start_variance_days, rows[1].finish_variance_days, rows[1].estimate_variance_hours) == (
        0,
        0,
        Decimal("0"),
    )


def test_estimate_variance_is_none_when_unestimated() -> None:
    """RULE-SCH-004：どちらかが未見積なら見積の差は求めない（None）。"""
    (row,) = compare_with_baseline([item(1, "12/01", "12/01", None)], [plan(1, "12/01", "12/01", "8")], calendar=CAL)
    assert row.estimate_variance_hours is None
    assert row.current_estimate_hours == Decimal("8") and row.baseline_estimate_hours is None


def test_added_and_deleted_activities_are_distinguished() -> None:
    """RULE-SCH-004：追加・削除された活動も区別して出す（SCH-18）。紐付けの空いた項目と計画に無い ID は削除。"""
    rows = compare_with_baseline(
        [
            item(None, "12/01", "12/02", "4", name="消した活動A"),
            item(1, "12/01", "12/02"),
            item(9, "12/03", "12/04", "6", name="消した活動B"),
        ],
        [plan(2, "12/07", "12/08", "3"), plan(1, "12/01", "12/02")],
        calendar=CAL,
    )
    assert [(r.status, r.activity_id, r.name) for r in rows] == [
        (VarianceStatus.ADDED, 2, "活動2"),
        (VarianceStatus.MATCHED, 1, "活動1"),
        (VarianceStatus.DELETED, None, "消した活動A"),
        (VarianceStatus.DELETED, 9, "消した活動B"),
    ]
    added, _, deleted_a, _ = rows
    assert added.start_variance_days is None and added.estimate_variance_hours is None
    assert added.current_start == d("12/07") and added.baseline_start is None
    assert deleted_a.baseline_estimate_hours == Decimal("4") and deleted_a.current_start is None
    assert deleted_a.finish_variance_days is None


def test_milestone_variance() -> None:
    """RULE-SCH-004：マイルストーンも日付の差を稼働日で出す（開始＝終了）。"""
    (row,) = compare_with_baseline(
        [item(5, "12/04", "12/04", "0", is_milestone=True)],
        [plan(5, "12/08", "12/08", "0", is_milestone=True)],
        calendar=CAL,
    )
    assert (row.start_variance_days, row.finish_variance_days) == (2, 2)


def test_input_errors() -> None:
    """RULE-SCH-004：同じ活動が 2 回・負の見積・float の見積は拒む。"""
    with pytest.raises(BaselineError):
        compare_with_baseline([item(1, "12/01", "12/01"), item(1, "12/02", "12/02")], [], calendar=CAL)
    with pytest.raises(BaselineError):
        compare_with_baseline([], [plan(1, "12/01", "12/01"), plan(1, "12/01", "12/01")], calendar=CAL)
    with pytest.raises(BaselineError):
        compare_with_baseline([item(1, "12/01", "12/01", "-1")], [], calendar=CAL)
    bad = PlanActivity(1, "1.1", "x", d("12/01"), d("12/01"), False, 1.5)  # type: ignore[arg-type]
    with pytest.raises(BaselineError):
        compare_with_baseline([], [bad], calendar=CAL)


def test_select_baseline_defaults_to_latest_version() -> None:
    """RULE-SCH-004：比べる版は指定が無ければ最新の版、指定があればその版（SCH-17）。"""
    versions = [BaselineVersion(id=11, version=1), BaselineVersion(id=13, version=3), BaselineVersion(id=12, version=2)]
    assert select_baseline(versions) == BaselineVersion(id=13, version=3)
    assert select_baseline(versions, 11) == BaselineVersion(id=11, version=1)
    assert select_baseline([]) is None
    with pytest.raises(BaselineError):
        select_baseline(versions, 99)
    with pytest.raises(BaselineError):
        select_baseline([BaselineVersion(id=1, version=1), BaselineVersion(id=2, version=1)])
