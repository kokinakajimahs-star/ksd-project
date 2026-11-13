"""「今」と業務の日付の部品（``app.services.clock``。設計書 1.3・NFR-08・CR-019）の試験。

「今日」と日付で指定する期間は、業務の時間帯（既定 Asia/Tokyo）の暦の日付。日本の 0〜9 時は UTC の暦ではまだ前の日。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.config import Settings
from app.services.clock import business_date, business_period, get_now

TOKYO = ZoneInfo("Asia/Tokyo")
NEW_YORK = ZoneInfo("America/New_York")


def test_get_now_is_utc_aware() -> None:
    now = get_now()
    assert now.utcoffset() is not None and now.utcoffset() == UTC.utcoffset(now)


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 12, 9, 14, 59, 59, tzinfo=UTC), date(2026, 12, 9)),  # 12/09 23:59:59 JST
        (datetime(2026, 12, 9, 15, 0, tzinfo=UTC), date(2026, 12, 10)),  # 12/10 00:00 JST（UTC の日付は 12/09）
        (datetime(2026, 12, 9, 23, 59, tzinfo=UTC), date(2026, 12, 10)),  # 12/10 08:59 JST
        (datetime(2026, 12, 10, 0, 0, tzinfo=UTC), date(2026, 12, 10)),  # 12/10 09:00 JST
        (datetime(2026, 12, 10, 9, 0, tzinfo=TOKYO), date(2026, 12, 10)),  # 時差つきなら UTC でなくてよい
    ],
)
def test_business_date_is_calendar_date_in_business_zone(now: datetime, expected: date) -> None:
    assert business_date(now, TOKYO) == expected


def test_business_date_follows_zone_and_rejects_naive_time() -> None:
    moment = datetime(2026, 12, 9, 15, 0, tzinfo=UTC)
    assert business_date(moment, UTC) == date(2026, 12, 9)
    assert business_date(moment, NEW_YORK) == date(2026, 12, 9)  # 12/09 10:00 EST
    with pytest.raises(ValueError):
        business_date(datetime(2026, 12, 9, 15, 0), TOKYO)


def test_business_period_converts_inclusive_dates_to_utc_range() -> None:
    """両端を含む日付の期間 → ``[date_from の 0 時, date_to の翌日 0 時)``（業務の時間帯）を UTC にしたもの。"""
    assert business_period(date(2026, 12, 10), date(2026, 12, 10), TOKYO) == (
        datetime(2026, 12, 9, 15, 0, tzinfo=UTC), datetime(2026, 12, 10, 15, 0, tzinfo=UTC))
    assert business_period(date(2026, 12, 10), None, TOKYO) == (datetime(2026, 12, 9, 15, 0, tzinfo=UTC), None)
    assert business_period(None, date(2026, 12, 9), TOKYO) == (None, datetime(2026, 12, 9, 15, 0, tzinfo=UTC))
    assert business_period(None, None, TOKYO) == (None, None)
    # 夏時間のある時間帯は、その日の時差で直す（EST は -5、EDT は -4）
    assert business_period(date(2026, 12, 10), date(2026, 7, 1), NEW_YORK) == (
        datetime(2026, 12, 10, 5, 0, tzinfo=UTC), datetime(2026, 7, 2, 4, 0, tzinfo=UTC))


def test_business_period_leaves_unrepresentable_ends_open() -> None:
    """``date.min`` の 0 時（日本では UTC の前の年）・``date.max`` の翌日は表せないので、その側を限らない（500 にしない）。"""
    assert business_period(date.min, date.max, TOKYO) == (None, None)
    assert business_period(date.min, date.max, UTC) == (datetime(1, 1, 1, tzinfo=UTC), None)
    start, end = business_period(date.min, None, NEW_YORK)  # 時差が負なら UTC でも同じ日の中に収まる
    assert start is not None and start.date() == date.min and end is None


def test_settings_business_zone_defaults_to_tokyo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PMT_BUSINESS_TIMEZONE", raising=False)
    assert Settings().business_zone == TOKYO
    assert Settings(business_timezone="UTC").business_zone == ZoneInfo("UTC")
