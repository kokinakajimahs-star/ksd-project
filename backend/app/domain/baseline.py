"""ベースライン（設計書 5.1 の RULE-SCH-004。要求仕様書 SCH-17・SCH-18、7.12 BR-12）。

- ベースラインとの差異（RULE-SCH-004）：選んだスケジュールベースラインの活動の項目と最新の計画の活動を、活動の ID で突き合わせ、
  活動ごとに開始・終了の差（稼働日）と見積の差を出す。基準に無い活動は「追加」、基準にあって計画に無い活動
  （写しの活動の紐付けが空いた項目を含む。docs/decisions/ASSUMPTIONS.md A-19）は「削除」として区別する（SCH-18）
- 比べる版の選び方（SCH-17）：指定が無ければ最新の版（``version`` の最大）、指定があればその版

差は「最新 − 基準」で、正は遅れ・増加を表す。日付の差は稼働日で数える（``[基準, 最新)`` の稼働日の数。前へずれたら負）。
見積の差は両方に値があるときだけ求める（未見積・追加・削除の行は ``None``。追加・削除は ``status`` で区別する）。

- 再基準化（RULE-CHG-004）：承認済みの変更を反映した後の WBS と日程から、スコープとスケジュールの新しい版（理由 CHANGE_REQUEST）を作る。
  旧版は残す（BR-12：版は追加だけ。今ある版の番号を読むだけで、足す版だけを返す）
純粋に保つ（1.3）。DB・HTTP・時刻・乱数に触れない。DB の行はサービス層が下のデータクラスへ直して渡す。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum

from app.domain.calendar import WorkCalendar
from app.domain.estimate import ActivityEstimate, WbsNode, rollup_estimates
from app.domain.wbs_code import CodedElement, WbsCodeError, display_preorder


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
    """最新の計画の活動（4.1 の ``activities`` の日程の結果と見積。``wbs_code`` は活動の番号（5.2。CR-028））。"""

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


# ---------------------------------------------------------------------------
# 再基準化（RULE-CHG-004）
# ---------------------------------------------------------------------------

# 版の名前の長さの上限（4.1 の schedule_baselines.name の列の長さ。A-19）
MAX_BASELINE_NAME_LENGTH = 200
# スコープベースライン・スケジュールベースラインの版の理由のうち、変更要求の反映で作るもの（4.1 の reason）
REASON_CHANGE_REQUEST = "CHANGE_REQUEST"
# スケジュールベースラインの項目の種類（4.1 の schedule_baseline_items.item_type）
ITEM_TYPE_ACTIVITY = "ACTIVITY"
ITEM_TYPE_WORK_PACKAGE = "WORK_PACKAGE"
_WORK_PACKAGE = "WORK_PACKAGE"


@dataclass(frozen=True)
class RebaselineElement:
    """反映した後の WBS 要素（4.1 の ``wbs_elements`` のうち版に写す列と、WBS の並びに要る列）と辞書（WBS-22）。"""

    id: int
    parent_id: int | None
    wbs_code: str
    kind: str
    name: str
    own_estimate_hours: Decimal | None
    sibling_seq: int = 1
    sort_order: int = 0
    dictionary: Mapping[str, object] | None = None


@dataclass(frozen=True)
class RebaselineActivity:
    """反映して日程を計算し直した後の活動（4.1 の ``activities`` のうち版に写す列）。

    ``wbs_code`` は活動の番号（5.2。CR-028）。版の活動の項目へ写す（4.2）。番号は採番規則と WP の中の並びで決まるので呼び手が渡す。
    """

    id: int
    work_package_id: int
    name: str
    start_date: date | None
    finish_date: date | None
    duration_days: int
    is_milestone: bool
    estimate_hours: Decimal | None
    wbs_code: str = field(kw_only=True)


@dataclass(frozen=True)
class ScopeBaselineElementCopy:
    """スコープベースラインの要素の写し 1 件（4.1 の ``scope_baseline_elements``）。"""

    element_id: int
    parent_element_id: int | None
    wbs_code: str
    kind: str
    name: str
    own_estimate_hours: Decimal | None
    dictionary: Mapping[str, object] | None


@dataclass(frozen=True)
class ScheduleBaselineItemCopy:
    """スケジュールベースラインの項目の写し 1 件（4.1 の ``schedule_baseline_items``）。"""

    item_type: str
    activity_id: int | None
    element_id: int
    wbs_code: str
    name: str
    start_date: date | None
    finish_date: date | None
    duration_days: int | None
    is_milestone: bool
    estimate_hours: Decimal | None


@dataclass(frozen=True)
class NewScopeBaseline:
    """新しく足すスコープベースラインの版（4.1 の ``scope_baselines``。理由は CHANGE_REQUEST）。"""

    version: int
    reason: str
    change_request_id: int
    elements: tuple[ScopeBaselineElementCopy, ...]


@dataclass(frozen=True)
class NewScheduleBaseline:
    """新しく足すスケジュールベースラインの版（4.1 の ``schedule_baselines``。理由は CHANGE_REQUEST）。"""

    version: int
    name: str
    reason: str
    change_request_id: int
    items: tuple[ScheduleBaselineItemCopy, ...]


@dataclass(frozen=True)
class Rebaseline:
    """再基準化の結果。どちらも**足す版**だけで、旧版には触れない（BR-12：追加だけ・上書きも削除もしない）。"""

    scope: NewScopeBaseline
    schedule: NewScheduleBaseline


def _next_version(versions: Iterable[int], what: str) -> int:
    seen: set[int] = set()
    for version in versions:
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise BaselineError(f"{what}の版は 1 以上の整数: {version!r}")
        if version in seen:
            raise BaselineError(f"{what}の版の番号が重複している（プロジェクト内の連番。4.1）: {version}")
        seen.add(version)
    return max(seen, default=0) + 1


def _rollup(elements: Sequence[RebaselineElement], activities: Sequence[RebaselineActivity]) -> dict[int, Decimal]:
    """ボトムアップ集計（RULE-EST-001）と同じ WP の値（活動があれば活動の見積の合計、無ければ自分の見積。空は 0）。"""
    rollup = rollup_estimates(
        [WbsNode(id=e.id, parent_id=e.parent_id, kind=e.kind, own_estimate_hours=e.own_estimate_hours)  # type: ignore[arg-type]
         for e in elements],
        [ActivityEstimate(id=a.id, work_package_id=a.work_package_id, estimate_hours=a.estimate_hours)
         for a in activities],
    )
    return {element_id: r.hours for element_id, r in rollup.elements.items()}


# RULE-CHG-004 再基準化
def plan_rebaseline(
    *,
    change_request_id: int,
    scope_versions: Iterable[int],
    schedule_versions: Iterable[int],
    schedule_name: str,
    elements: Iterable[RebaselineElement],
    activities: Iterable[RebaselineActivity],
) -> Rebaseline:
    """承認済みの変更を反映した後の WBS と日程から、スコープとスケジュールの新しい版を作る（CHG-04・SCH-16・WBS-22・BR-12）。

    - 版の番号はそれぞれプロジェクトの中の連番：今ある版の最大＋1（無ければ 1）。理由は ``CHANGE_REQUEST``、元になった変更要求を持つ
    - スコープの版：全ての要素（WBS の並び）と辞書を写す
    - スケジュールの版：全ての活動（渡された順。サービス層が計画の並びで渡す）の開始日・終了日・期間・見積と、
      全ての WP（WBS の並び。見積は RULE-EST-001 の WP の値、日付は配下の活動の最も早い開始日〜最も遅い終了日、活動が無ければ空）。
      手動の版（API-SCH-011）と同じ写し方
    - **旧版は残す**：今ある版の番号を読むだけで、返すのは足す版だけ（上書き・削除の指示を持たない）

    版の番号が 1 未満・重複、名前が空・長すぎる、変更要求の ID が無い、WBS の木の誤りは ``BaselineError``。
    """
    if isinstance(change_request_id, bool) or not isinstance(change_request_id, int) or change_request_id < 1:
        raise BaselineError(f"元になった変更要求の ID は 1 以上の整数: {change_request_id!r}")
    if not isinstance(schedule_name, str) or schedule_name.strip() == "" or len(schedule_name) > MAX_BASELINE_NAME_LENGTH:
        raise BaselineError(f"スケジュールベースラインの名前は空でない {MAX_BASELINE_NAME_LENGTH} 文字以内: {schedule_name!r}")
    scope_version = _next_version(scope_versions, "スコープベースライン")
    schedule_version = _next_version(schedule_versions, "スケジュールベースライン")

    element_list = list(elements)
    activity_list = list(activities)
    by_id = {e.id: e for e in element_list}
    if len(by_id) != len(element_list):
        raise BaselineError("WBS 要素の ID が重複している")
    try:
        preorder = display_preorder(
            CodedElement(id=e.id, parent_id=e.parent_id, wbs_code=e.wbs_code, sibling_seq=e.sibling_seq,
                         sort_order=e.sort_order)
            for e in element_list
        )
    except WbsCodeError as exc:
        raise BaselineError(str(exc)) from None
    for activity in activity_list:
        wp = by_id.get(activity.work_package_id)
        if wp is None or wp.kind != _WORK_PACKAGE:
            raise BaselineError(f"活動 {activity.id} の置き場 {activity.work_package_id} が WP でない・無い")
        _check_hours(activity.estimate_hours, f"活動 {activity.id} の見積")
    try:
        wp_hours = _rollup(element_list, activity_list)
    except ValueError as exc:
        raise BaselineError(str(exc)) from None

    scope = NewScopeBaseline(
        version=scope_version,
        reason=REASON_CHANGE_REQUEST,
        change_request_id=change_request_id,
        elements=tuple(
            ScopeBaselineElementCopy(
                element_id=e.id,
                parent_element_id=e.parent_id,
                wbs_code=e.wbs_code,
                kind=e.kind,
                name=e.name,
                own_estimate_hours=e.own_estimate_hours,
                dictionary=None if e.dictionary is None else dict(e.dictionary),
            )
            for e in (by_id[i] for i in preorder)
        ),
    )

    items: list[ScheduleBaselineItemCopy] = [
        ScheduleBaselineItemCopy(
            item_type=ITEM_TYPE_ACTIVITY,
            activity_id=a.id,
            element_id=a.work_package_id,
            wbs_code=a.wbs_code,
            name=a.name,
            start_date=a.start_date,
            finish_date=a.finish_date,
            duration_days=a.duration_days,
            is_milestone=a.is_milestone,
            estimate_hours=a.estimate_hours,
        )
        for a in activity_list
    ]
    for element_id in preorder:
        wp = by_id[element_id]
        if wp.kind != _WORK_PACKAGE:
            continue
        children = [a for a in activity_list if a.work_package_id == wp.id]
        items.append(
            ScheduleBaselineItemCopy(
                item_type=ITEM_TYPE_WORK_PACKAGE,
                activity_id=None,
                element_id=wp.id,
                wbs_code=wp.wbs_code,
                name=wp.name,
                start_date=min((a.start_date for a in children if a.start_date is not None), default=None),
                finish_date=max((a.finish_date for a in children if a.finish_date is not None), default=None),
                duration_days=None,
                is_milestone=False,
                estimate_hours=wp_hours[wp.id],
            )
        )
    schedule = NewScheduleBaseline(
        version=schedule_version,
        name=schedule_name,
        reason=REASON_CHANGE_REQUEST,
        change_request_id=change_request_id,
        items=tuple(items),
    )
    return Rebaseline(scope=scope, schedule=schedule)
