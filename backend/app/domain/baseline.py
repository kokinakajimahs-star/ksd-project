"""ベースライン（設計書 5.1 の RULE-SCH-004。要求仕様書 SCH-17・SCH-18、7.12 BR-12）。

- ベースラインとの差異（RULE-SCH-004）：選んだスケジュールベースラインの活動の項目と最新の計画の活動を、活動の ID で突き合わせ、
  活動ごとに開始・終了の差（稼働日）と見積の差を出す。基準に無い活動は「追加」、基準にあって計画に無い活動
  （写しの活動の紐付けが空いた項目を含む。docs/decisions/ASSUMPTIONS.md A-19）は「削除」として区別する（SCH-18）
- 比べる版の選び方（SCH-17）：指定が無ければ最新の版（``version`` の最大）、指定があればその版

差は「最新 − 基準」で、正は遅れ・増加を表す。日付の差は稼働日で数える（``[基準, 最新)`` の稼働日の数。前へずれたら負）。
見積の差は両方に値があるときだけ求める（未見積・追加・削除の行は ``None``。追加・削除は ``status`` で区別する）。

再基準化（RULE-CHG-004）も同じファイルに置く（CHG のスコープ）。
純粋に保つ（1.3）。DB・HTTP・時刻・乱数に触れない。DB の行はサービス層が下のデータクラスへ直して渡す。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from app.domain.calendar import WorkCalendar


class BaselineError(ValueError):
    """ベースラインの入力が不正（未定義の入力は推測せずに拒む）。"""


class VarianceStatus(StrEnum):
    """差異の行の区分（SCH-18）。"""

    MATCHED = "MATCHED"  # 基準と最新の両方にある
    ADDED = "ADDED"  # 最新にだけある（基準の後に足した）
    DELETED = "DELETED"  # 基準にだけある（基準の後に消した）


@dataclass(frozen=True)
class BaselineVersion:
    """スケジュールベースラインの版（4.1 の ``schedule_baselines`` のうち選ぶのに要る列）。"""

    id: int
    version: int


@dataclass(frozen=True)
class BaselineActivityItem:
    """ベースラインの活動の項目（4.1 の ``schedule_baseline_items`` の ``item_type = ACTIVITY``）。

    ``activity_id`` は写した活動。活動を消した後は ``None``（A-19）。
    """

    activity_id: int | None
    wbs_code: str
    name: str
    start_date: date | None
    finish_date: date | None
    is_milestone: bool
    estimate_hours: Decimal | None


@dataclass(frozen=True)
class PlanActivity:
    """最新の計画の活動（4.1 の ``activities`` の日程の結果と見積。``wbs_code`` は置き場の WP の ID）。"""

    activity_id: int
    wbs_code: str
    name: str
    start_date: date | None
    finish_date: date | None
    is_milestone: bool
    estimate_hours: Decimal | None


@dataclass(frozen=True)
class ActivityVariance:
    """1 つの活動の差異（SCH-18）。差は「最新 − 基準」。求められない差は ``None``。"""

    status: VarianceStatus
    activity_id: int | None
    wbs_code: str
    name: str
    baseline_start: date | None
    baseline_finish: date | None
    current_start: date | None
    current_finish: date | None
    start_variance_days: int | None
    finish_variance_days: int | None
    baseline_estimate_hours: Decimal | None
    current_estimate_hours: Decimal | None
    estimate_variance_hours: Decimal | None


def working_day_difference(calendar: WorkCalendar, baseline: date, current: date) -> int:
    """基準の日から最新の日までの差（稼働日）。``current ≥ baseline`` なら ``[baseline, current)`` の稼働日の数、前なら負。

    5.4 の軸の ``index(current) − index(baseline)`` と同じ値（原点に依らない）。
    """
    if current >= baseline:
        return calendar.count_between(baseline, current)
    return -calendar.count_between(current, baseline)


def _check_hours(value: Decimal | None, what: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (Decimal, int)):
        raise BaselineError(f"{what} は Decimal で渡す（float は受けない）: {value!r}")
    hours = Decimal(value)
    if not hours.is_finite() or hours < 0:
        raise BaselineError(f"{what} は 0 以上の有限の値: {value!r}")
    return hours


def _difference(calendar: WorkCalendar, baseline: date | None, current: date | None) -> int | None:
    if baseline is None or current is None:
        return None
    return working_day_difference(calendar, baseline, current)


# RULE-SCH-004 ベースラインとの差異
def compare_with_baseline(
    baseline_items: Iterable[BaselineActivityItem],
    plan_activities: Iterable[PlanActivity],
    *,
    calendar: WorkCalendar,
) -> tuple[ActivityVariance, ...]:
    """選んだベースラインの活動の項目と最新の計画の活動を突き合わせ、活動ごとの差異を返す（SCH-18）。

    - 行の順：最新の計画の活動を渡された順（サービス層が計画の並びで渡す）に並べ、その後に削除された活動を基準の項目の順に並べる
    - ``MATCHED``：開始・終了の差（稼働日。``working_day_difference``）と見積の差（人時）
    - ``ADDED``：基準に無い活動。差は ``None``
    - ``DELETED``：計画に無い活動（``activity_id`` が ``None`` か、計画に無い ID）。差は ``None``
    - 同じ活動の ID が基準の項目か計画に 2 回あれば ``BaselineError``
    """
    items = list(baseline_items)
    by_activity: dict[int, BaselineActivityItem] = {}
    for item in items:
        _check_hours(item.estimate_hours, f"基準の項目 {item.wbs_code} {item.name} の見積")
        if item.activity_id is None:
            continue
        if item.activity_id in by_activity:
            raise BaselineError(f"基準の項目に活動 {item.activity_id} が 2 回ある")
        by_activity[item.activity_id] = item

    rows: list[ActivityVariance] = []
    planned_ids: set[int] = set()
    for activity in plan_activities:
        if activity.activity_id in planned_ids:
            raise BaselineError(f"計画に活動 {activity.activity_id} が 2 回ある")
        planned_ids.add(activity.activity_id)
        current_hours = _check_hours(activity.estimate_hours, f"活動 {activity.activity_id} の見積")
        base = by_activity.get(activity.activity_id)
        if base is None:
            rows.append(
                ActivityVariance(
                    status=VarianceStatus.ADDED,
                    activity_id=activity.activity_id,
                    wbs_code=activity.wbs_code,
                    name=activity.name,
                    baseline_start=None,
                    baseline_finish=None,
                    current_start=activity.start_date,
                    current_finish=activity.finish_date,
                    start_variance_days=None,
                    finish_variance_days=None,
                    baseline_estimate_hours=None,
                    current_estimate_hours=current_hours,
                    estimate_variance_hours=None,
                )
            )
            continue
        base_hours = _check_hours(base.estimate_hours, f"基準の活動 {activity.activity_id} の見積")
        rows.append(
            ActivityVariance(
                status=VarianceStatus.MATCHED,
                activity_id=activity.activity_id,
                wbs_code=activity.wbs_code,
                name=activity.name,
                baseline_start=base.start_date,
                baseline_finish=base.finish_date,
                current_start=activity.start_date,
                current_finish=activity.finish_date,
                start_variance_days=_difference(calendar, base.start_date, activity.start_date),
                finish_variance_days=_difference(calendar, base.finish_date, activity.finish_date),
                baseline_estimate_hours=base_hours,
                current_estimate_hours=current_hours,
                estimate_variance_hours=(
                    None if base_hours is None or current_hours is None else current_hours - base_hours
                ),
            )
        )

    for item in items:
        if item.activity_id is not None and item.activity_id in planned_ids:
            continue
        rows.append(
            ActivityVariance(
                status=VarianceStatus.DELETED,
                activity_id=item.activity_id,
                wbs_code=item.wbs_code,
                name=item.name,
                baseline_start=item.start_date,
                baseline_finish=item.finish_date,
                current_start=None,
                current_finish=None,
                start_variance_days=None,
                finish_variance_days=None,
                baseline_estimate_hours=_check_hours(item.estimate_hours, "基準の項目の見積"),
                current_estimate_hours=None,
                estimate_variance_hours=None,
            )
        )
    return tuple(rows)


def select_baseline(baselines: Sequence[BaselineVersion], baseline_id: int | None = None) -> BaselineVersion | None:
    """比べる版を選ぶ（SCH-17）。指定が無ければ最新の版（``version`` の最大）、版が 1 つも無ければ ``None``。

    指定の ID の版が無ければ ``BaselineError``（他のプロジェクトの版を指定した場合を含む。サービス層が 404 にする）。
    """
    if baseline_id is not None:
        for b in baselines:
            if b.id == baseline_id:
                return b
        raise BaselineError(f"ベースライン {baseline_id} が無い")
    if not baselines:
        return None
    versions = [b.version for b in baselines]
    if len(set(versions)) != len(versions):
        raise BaselineError("ベースラインの版の番号が重複している（プロジェクト内の連番。4.1）")
    return max(baselines, key=lambda b: b.version)
