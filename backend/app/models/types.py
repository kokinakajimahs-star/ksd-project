"""モデルで共通に使う列の型（設計書 2.4）。

- ``UTCDateTime``：時刻を UTC で保存し、読むときも UTC の aware な ``datetime`` で返す。
  SQLite は時差を保存しないため、書くときに UTC へ直して保存し、読んだ値に UTC を付ける。
  PostgreSQL では ``timestamp with time zone`` になる。時差の無い（naive な）値は、どの時差かを決められないので拒む（fail fast）
- ``utc_now``：作成の時刻などの既定値（UTC の現在時刻）。モデルの既定値にだけ使う（ドメイン層は時刻に触れない。NFR-08）
- ``in_list``：値の一覧の列の検査（CHECK）の式
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Dialect
from sqlalchemy.types import TypeDecorator


def utc_now() -> datetime:
    """UTC の現在時刻（aware）。"""
    return datetime.now(UTC)


def in_list(column: str, values: tuple[str, ...]) -> str:
    """値の一覧が設計書に書かれている列の検査（CHECK）の式：``<列> IN ('A', 'B', …)``。"""
    return f"{column} IN (" + ", ".join(f"'{value}'" for value in values) + ")"


class UTCDateTime(TypeDecorator[datetime]):
    """UTC で保存し、UTC の aware な ``datetime`` で返す時刻の列。"""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, datetime):
            raise TypeError(f"時刻の列には datetime を渡す（{type(value).__name__} が渡された）")
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("時刻は時差付き（aware）の datetime で渡す。時差の無い値は UTC かどうかを決められない")
        return value.astimezone(UTC)

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
