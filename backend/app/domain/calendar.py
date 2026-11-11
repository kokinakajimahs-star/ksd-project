"""稼働日の判定・日付の数え方・稼働日の軸（設計書 5.1 の RULE-CAL-001・002、5.4。要求仕様書 7.4 BR-04・7.7 BR-07）。

- 稼働日（RULE-CAL-001）：非稼働の曜日でも休日でもない日。``working_weekdays`` は月曜から日曜の順の 7 文字で
  ``1`` が稼働日（4.1 の ``calendars.working_weekdays``。既定 ``1111100``＝土日休み）。
  開始日・期間・終了日のうち 2 つから残りを求める。開始日は次の稼働日へ、終了日は前の稼働日へ寄せる
- 稼働日の軸（RULE-CAL-002）：原点（プロジェクトの開始日）から稼働日だけを並べた軸。稼働日 d は ``[index(d), index(d)+1)`` を占める。
  報告日線 ``line(r)``、位置 p の表示の日付（添字 ``max(ceil(p) − 1, 0)`` の稼働日）

純粋に保つ（1.3）。DB・HTTP・時刻・乱数に触れない。カレンダーの行はサービス層が下のデータクラスへ直して渡す。
数え方は曜日の周期（7 日）と休日の二分探索で求め、日数に比例する繰り返しをしない（5.5 の性能 NFR-01 のため）。
"""

from __future__ import annotations

import math
from bisect import bisect_left
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction

# 4.1 の calendars.working_weekdays の既定（月〜金が稼働日）
DEFAULT_WORKING_WEEKDAYS = "1111100"

# 軸上の位置。進捗ポイント（5.6 の S＋d×進捗率）は小数になるので、誤差の無い Decimal・Fraction も受ける
Position = int | float | Decimal | Fraction

_ONE_DAY = timedelta(days=1)


class CalendarError(ValueError):
    """カレンダーの定義、または日付の数え方の入力が不正（未定義の入力は推測せずに拒む）。"""


@dataclass(frozen=True)
class ActivityDates:
    """開始日・期間（稼働日）・終了日の組（RULE-CAL-001 の結果。日付は稼働日へ寄せた後）。"""

    start: date
    duration: int
    end: date

    @property
    def is_milestone(self) -> bool:
        """期間 0 はマイルストーン（BR-04）。"""
        return self.duration == 0


# RULE-CAL-001 稼働日の判定と日付の数え方
@dataclass(frozen=True)
class WorkCalendar:
    """稼働カレンダー（4.1 の calendars・calendar_holidays を写したもの）。

    ``working_weekdays``：月曜から日曜の順の 7 文字。``1`` が稼働、``0`` が非稼働。稼働の曜日が 1 つも無いと
    稼働日が存在せず数えられないので拒む。``holidays``：休日（日付）。非稼働の曜日に重なる休日は数えに影響しない。
    """

    working_weekdays: str = DEFAULT_WORKING_WEEKDAYS
    holidays: frozenset[date] = frozenset()
    # 曜日ごとの稼働（月＝0 … 日＝6）と、稼働の曜日に重なる休日だけを昇順に並べたもの（数えに使う）
    _mask: tuple[int, ...] = field(init=False, repr=False, compare=False)
    _effective_holidays: tuple[date, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        weekdays = self.working_weekdays
        if not isinstance(weekdays, str) or len(weekdays) != 7 or any(c not in "01" for c in weekdays):
            raise CalendarError(f"working_weekdays は 0 と 1 の 7 文字（月〜日）で指定する: {weekdays!r}")
        if "1" not in weekdays:
            raise CalendarError("稼働の曜日が 1 つも無いカレンダーでは日付を数えられない")
        holidays = frozenset(self.holidays)
        mask = tuple(int(c) for c in weekdays)
        object.__setattr__(self, "holidays", holidays)
        object.__setattr__(self, "_mask", mask)
        object.__setattr__(self, "_effective_holidays", tuple(sorted(h for h in holidays if mask[h.weekday()])))

    @classmethod
    def of(cls, working_weekdays: str = DEFAULT_WORKING_WEEKDAYS, holidays: Iterable[date] = ()) -> WorkCalendar:
        """休日を任意の列（DB の行から取り出した日付の列など）で受けて作る。"""
        return cls(working_weekdays=working_weekdays, holidays=frozenset(holidays))

    # --- 判定 -----------------------------------------------------------------

    def is_working_day(self, d: date) -> bool:
        """稼働日か（非稼働の曜日でも休日でもない日。BR-04）。"""
        return bool(self._mask[d.weekday()]) and d not in self.holidays

    # --- 寄せ -----------------------------------------------------------------

    def roll_forward(self, d: date) -> date:
        """d が稼働日なら d、でなければ次の稼働日（開始日の寄せ。BR-04）。"""
        if self.is_working_day(d):
            return d
        # [d, e] に稼働日が 1 件ある最初の e
        return self._first_at_or_after(d, 1, lambda e: self.count_between(d, e + _ONE_DAY) >= 1)

    def roll_backward(self, d: date) -> date:
        """d が稼働日なら d、でなければ前の稼働日（終了日の寄せ。BR-04）。"""
        if self.is_working_day(d):
            return d
        # [e, d) に稼働日が 1 件ある最後の e
        return self._last_at_or_before(d, 1, lambda e: self.count_between(e, d) >= 1)

    # --- 数え方 ---------------------------------------------------------------

    def count_between(self, start: date, end_exclusive: date) -> int:
        """半開区間 ``[start, end_exclusive)`` の稼働日の数。``end_exclusive < start`` は誤り。"""
        if end_exclusive < start:
            raise CalendarError(f"区間の終わり {end_exclusive} が始め {start} より前")
        weeks, rest = divmod((end_exclusive - start).days, 7)
        first_weekday = start.weekday()
        count = weeks * sum(self._mask) + sum(self._mask[(first_weekday + i) % 7] for i in range(rest))
        holidays = self._effective_holidays
        return count - (bisect_left(holidays, end_exclusive) - bisect_left(holidays, start))

    def count_inclusive(self, first: date, last: date) -> int:
        """閉区間 ``[first, last]`` の稼働日の数（``last < first`` は 0）。"""
        if last < first:
            return 0
        return self.count_between(first, last + _ONE_DAY)

    def add_working_days(self, d: date, n: int) -> date:
        """稼働日 d から n 稼働日後（n が負なら前）の稼働日。n＝0 は d 自身。d が稼働日でなければ誤り。"""
        if not self.is_working_day(d):
            raise CalendarError(f"{d} は稼働日ではない（先に roll_forward・roll_backward で寄せる）")
        if n == 0:
            return d
        if n > 0:
            after = d + _ONE_DAY
            # (d, e] の稼働日が n 件になる最初の e（その日は必ず稼働日）
            return self._first_at_or_after(after, n, lambda e: self.count_between(after, e + _ONE_DAY) >= n)
        # [e, d) の稼働日が −n 件になる最後の e（その日は必ず稼働日）
        return self._last_at_or_before(d, -n, lambda e: self.count_between(e, d) >= -n)

    # --- 開始日・期間・終了日 -------------------------------------------------

    def resolve_dates(
        self,
        *,
        start: date | None = None,
        duration: int | None = None,
        end: date | None = None,
    ) -> ActivityDates:
        """開始日・期間（稼働日）・終了日のうち 2 つから残りを求める（SCH-03・BR-04・5.4）。

        - 期間 d（d≧1）の活動は開始日の始業から終了日の終業までを占め、終了日は開始日から数えて d 番目の稼働日
        - 期間 0 はマイルストーンで、日付は 1 つ（開始日＝終了日）
        - 開始日は次の稼働日へ、終了日は前の稼働日へ寄せる（寄せた後の値を返す）
        - 与える値がちょうど 2 つでないとき・期間が負のとき・寄せた終了日が開始日より前のときは ``CalendarError``
        """
        if sum(v is not None for v in (start, duration, end)) != 2:
            raise CalendarError("開始日・期間・終了日のうち、ちょうど 2 つを与える")
        if duration is not None and duration < 0:
            raise CalendarError(f"期間（稼働日）は 0 以上: {duration}")

        if start is not None and duration is not None:
            s = self.roll_forward(start)
            e = s if duration == 0 else self.add_working_days(s, duration - 1)
            return ActivityDates(start=s, duration=duration, end=e)

        if end is not None and duration is not None:
            e = self.roll_backward(end)
            s = e if duration == 0 else self.add_working_days(e, -(duration - 1))
            return ActivityDates(start=s, duration=duration, end=e)

        assert start is not None and end is not None
        s = self.roll_forward(start)
        e = self.roll_backward(end)
        if e < s:
            raise CalendarError(f"終了日 {end} が開始日 {start} より前（稼働日へ寄せた後: {s} 〜 {e}）")
        return ActivityDates(start=s, duration=self.count_inclusive(s, e), end=e)

    # --- 探索（単調な条件の境目を二分探索で求める） ---------------------------

    def _span_for(self, n: int) -> int:
        """どの日から数えても、続く日数の中に稼働日が n 件以上ある日数（曜日の周期と休日の数からの上界）。

        7m 日には稼働の曜日がちょうど m×W 日あり、休日はそのうち高々 H 日を消す。m＝ceil((n＋H)/W)＋1 なら足りる。
        """
        per_week = sum(self._mask)
        weeks = -(-(n + len(self._effective_holidays)) // per_week) + 1
        return 7 * weeks

    def _first_at_or_after(self, lo: date, n: int, ok: Callable[[date], bool]) -> date:
        """lo 以降で ok が真になる最初の日（ok は日付の昇順に 偽…偽 真…真）。n は ok が要る稼働日の件数。"""
        hi = lo + timedelta(days=self._span_for(n))
        if not ok(hi):  # 上界の取り方の誤りを黙って誤った日付にしない
            raise AssertionError(f"探索の上界が足りない: {lo} から {n} 件")
        left, right = lo, hi
        while left < right:
            mid = left + timedelta(days=(right - left).days // 2)
            if ok(mid):
                right = mid
            else:
                left = mid + _ONE_DAY
        return left

    def _last_at_or_before(self, hi_exclusive: date, n: int, ok: Callable[[date], bool]) -> date:
        """hi_exclusive より前で ok が真になる最後の日（ok は日付の昇順に 真…真 偽…偽）。n は ok が要る稼働日の件数。"""
        lo = hi_exclusive - timedelta(days=self._span_for(n))
        if not ok(lo):
            raise AssertionError(f"探索の下界が足りない: {hi_exclusive} の前に {n} 件")
        left, right = lo, hi_exclusive - _ONE_DAY
        while left < right:
            mid = left + timedelta(days=(right - left).days // 2 + 1)
            if ok(mid):
                left = mid
            else:
                right = mid - _ONE_DAY
        return left


# RULE-CAL-002 稼働日の軸
@dataclass(frozen=True)
class WorkdayAxis:
    """稼働日だけを並べた軸（5.4・BR-07）。原点はプロジェクトの開始日で、それより前の日付は負の位置になる。

    - ``index(d)``：原点以上 d 未満の稼働日の数（d が原点より前なら、d 以上 原点未満の稼働日の数の負）。
      稼働日 d は ``[index(d), index(d)+1)`` を占め、非稼働日の ``index`` は次の稼働日の始業（＝前の稼働日の終業）
    - ``workday_at(i)``：添字 i の稼働日（``index`` が i の稼働日）
    - ``report_line(r)``：報告日線 ``index(r) + (r が稼働日なら 1、でなければ 0)``＝報告日の終業の位置
    - ``display_date(p)``：位置 p の表示の日付＝添字 ``max(ceil(p) − 1, 0)`` の稼働日
    """

    calendar: WorkCalendar
    origin: date

    def index(self, d: date) -> int:
        """日付 → 軸上の位置（その日の始業）。"""
        if d >= self.origin:
            return self.calendar.count_between(self.origin, d)
        return -self.calendar.count_between(d, self.origin)

    def workday_at(self, i: int) -> date:
        """軸上の添字 i → 区間 ``[i, i+1)`` を占める稼働日。"""
        first = self.calendar.roll_forward(self.origin)  # 原点以降の最初の稼働日。index(first) は 0
        return self.calendar.add_working_days(first, i)

    def report_line(self, report_date: date) -> int:
        """報告日線の位置（報告日の終業。非稼働日なら前の稼働日の終業＝原点から報告日までの稼働日の数）。"""
        return self.index(report_date) + (1 if self.calendar.is_working_day(report_date) else 0)

    def display_date(self, position: Position) -> date:
        """位置 p の表示の日付：その日の終業が p 以上で最も早い稼働日（境目は前の稼働日の終業に当てる）。"""
        try:
            ceiling = math.ceil(position)
        except (ValueError, OverflowError, ArithmeticError) as exc:
            raise CalendarError(f"位置が有限の数ではない: {position}") from exc
        return self.workday_at(max(ceiling - 1, 0))
