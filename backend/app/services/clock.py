"""「今」の時刻と業務の日付（設計書 1.3・NFR-08・CR-019）。

サービス層が「今」を決めてドメイン層へ引数で渡す。API 層は ``get_now`` を ``Depends`` で受け、
試験は ``app.dependency_overrides[get_now]`` で時刻を固定する（試験を時刻に依存させない。12 章）。

「今日」と、日付で指定する期間（API-CHG-010 など）は、業務の時間帯（設定の ``PMT_BUSINESS_TIMEZONE``。既定 ``Asia/Tokyo``）の
暦の日付とする（設計書 1.3・CR-019）。``get_now`` は UTC のまま、日付はここの ``business_date``・``business_period`` で求める
（API 層の ``app.api.deps.get_today`` が ``get_now`` と設定の時間帯からこれを呼ぶ）。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, tzinfo


def get_now() -> datetime:
    """UTC の現在時刻（aware）。"""
    return datetime.now(UTC)


def _check_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("時刻は時差つき（aware）の datetime で渡す")


def business_date(now: datetime, zone: tzinfo) -> date:
    """時刻 ``now``（時差つき）の、業務の時間帯 ``zone`` での暦の日付（「今日」）。"""
    _check_aware(now)
    return now.astimezone(zone).date()


def _day_start(day: date, zone: tzinfo) -> datetime | None:
    """業務の時間帯の暦の日 ``day`` の 0 時を UTC の時刻にしたもの。表せる範囲の外なら ``None``。"""
    try:
        return datetime.combine(day, time.min, tzinfo=zone).astimezone(UTC)
    except OverflowError:
        return None


def business_period(
    date_from: date | None, date_to: date | None, zone: tzinfo
) -> tuple[datetime | None, datetime | None]:
    """業務の時間帯 ``zone`` の日付で両端を含む期間を、UTC の時刻の範囲 ``[始まり, 終わり)`` にする。

    始まりは ``date_from`` の 0 時、終わりは ``date_to`` の翌日の 0 時（どちらも業務の時間帯）。省いた端と、時刻で表せる範囲を
    超える端（``date.min`` の 0 時が UTC で前の年に出る・``date.max`` の翌日）は ``None``（その側を限らない）。
    """
    start = None if date_from is None else _day_start(date_from, zone)
    end = None
    if date_to is not None and date_to < date.max:
        end = _day_start(date_to + timedelta(days=1), zone)
    return start, end
