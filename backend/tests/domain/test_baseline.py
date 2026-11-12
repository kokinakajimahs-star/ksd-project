"""ベースラインとの差異（app.domain.baseline）の試験。

設計書 5.1（RULE-SCH-004・RULE-CHG-004）・4.1（scope_baselines・schedule_baselines）、要求仕様書 SCH-16〜18、CHG-04、WBS-22、7.12 BR-12。
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
    RebaselineActivity,
    RebaselineElement,
    VarianceStatus,
    compare_with_baseline,
    plan_rebaseline,
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


# ---------------------------------------------------------------------------
# RULE-CHG-004 再基準化
# ---------------------------------------------------------------------------

# 反映した後の WBS：1.0（成果物）の下に WP 1.2（表示順 1）と WP 1.1（表示順 2）、活動の無い WP 1.3
REBASE_ELEMENTS = (
    RebaselineElement(id=1, parent_id=None, wbs_code="1.0", kind="DELIVERABLE", name="システム",
                      own_estimate_hours=None, sibling_seq=1, sort_order=1),
    RebaselineElement(id=2, parent_id=1, wbs_code="1.1", kind="WORK_PACKAGE", name="設計WP",
                      own_estimate_hours=Decimal(99), sibling_seq=1, sort_order=2,
                      dictionary={"deliverable": "設計書", "completion_criteria": "レビュー済み"}),
    RebaselineElement(id=3, parent_id=1, wbs_code="1.2", kind="WORK_PACKAGE", name="実装WP",
                      own_estimate_hours=None, sibling_seq=2, sort_order=1),
    RebaselineElement(id=4, parent_id=1, wbs_code="1.3", kind="WORK_PACKAGE", name="試験WP",
                      own_estimate_hours=Decimal("12.5"), sibling_seq=3, sort_order=3),
)
REBASE_ACTIVITIES = (
    RebaselineActivity(id=10, work_package_id=2, name="設計", start_date=d("12/01"), finish_date=d("12/03"),
                       duration_days=3, is_milestone=False, estimate_hours=Decimal(24)),
    RebaselineActivity(id=11, work_package_id=3, name="実装", start_date=d("12/04"), finish_date=d("12/10"),
                       duration_days=5, is_milestone=False, estimate_hours=Decimal(40)),
    RebaselineActivity(id=12, work_package_id=3, name="完了", start_date=d("12/10"), finish_date=d("12/10"),
                       duration_days=0, is_milestone=True, estimate_hours=None),
)


def rebaseline(**kw: object):  # type: ignore[no-untyped-def]
    args: dict[str, object] = {
        "change_request_id": 7,
        "scope_versions": [1, 2],
        "schedule_versions": [1],
        "schedule_name": "変更要求 #3 の反映",
        "elements": REBASE_ELEMENTS,
        "activities": REBASE_ACTIVITIES,
    }
    args.update(kw)
    return plan_rebaseline(**args)  # type: ignore[arg-type]


def test_rebaseline_creates_next_versions_with_change_request_reason() -> None:
    """RULE-CHG-004：スコープとスケジュールの新しい版を、それぞれ今ある版の最大＋1 で作る（理由 CHANGE_REQUEST・元の変更要求を持つ。BR-12）。"""
    result = rebaseline(scope_versions=[1, 3, 2], schedule_versions=[2, 1])
    assert (result.scope.version, result.scope.reason, result.scope.change_request_id) == (4, "CHANGE_REQUEST", 7)
    assert (result.schedule.version, result.schedule.reason, result.schedule.change_request_id) == (
        3, "CHANGE_REQUEST", 7)
    assert result.schedule.name == "変更要求 #3 の反映"


def test_rebaseline_first_version_is_one() -> None:
    """RULE-CHG-004：版が 1 つも無ければ版 1 から（境界）。"""
    result = rebaseline(scope_versions=[], schedule_versions=())
    assert (result.scope.version, result.schedule.version) == (1, 1)


def test_rebaseline_keeps_old_versions() -> None:
    """RULE-CHG-004：旧版を残す。今ある版の番号を読むだけで、返すのは足す版だけ（旧版の番号を再利用・上書きしない）。"""
    existing = [1, 2]
    result = rebaseline(scope_versions=existing, schedule_versions=existing)
    assert existing == [1, 2]
    assert result.scope.version not in existing and result.schedule.version not in existing
    assert set(vars(result)) == {"scope", "schedule"}  # 足す版の他に、旧版への指示を持たない


def test_rebaseline_scope_copies_elements_and_dictionary_in_wbs_order() -> None:
    """RULE-CHG-004：スコープの版には全ての要素と辞書を WBS の並び（先行順・兄弟は表示順）で写す（WBS-22）。"""
    result = rebaseline()
    copies = result.scope.elements
    assert [c.element_id for c in copies] == [1, 3, 2, 4]
    design = copies[2]
    assert (design.parent_element_id, design.wbs_code, design.kind, design.name, design.own_estimate_hours) == (
        1, "1.1", "WORK_PACKAGE", "設計WP", Decimal(99))
    assert design.dictionary == {"deliverable": "設計書", "completion_criteria": "レビュー済み"}
    assert copies[0].dictionary is None


def test_rebaseline_schedule_copies_activities_and_work_packages() -> None:
    """RULE-CHG-004：スケジュールの版には全ての活動（渡された順）と全ての WP（WBS の並び）を写す。
    WP の見積は RULE-EST-001 の値（活動があれば活動の合計で自分の見積は使わない、無ければ自分の見積）、
    日付は配下の活動の最も早い開始日〜最も遅い終了日、活動が無ければ空（手動の版と同じ写し方）。"""
    items = rebaseline().schedule.items
    activities = [i for i in items if i.item_type == "ACTIVITY"]
    assert [(i.activity_id, i.element_id, i.wbs_code, i.start_date, i.finish_date, i.duration_days, i.is_milestone,
             i.estimate_hours) for i in activities] == [
        (10, 2, "1.1", d("12/01"), d("12/03"), 3, False, Decimal(24)),
        (11, 3, "1.2", d("12/04"), d("12/10"), 5, False, Decimal(40)),
        (12, 3, "1.2", d("12/10"), d("12/10"), 0, True, None),
    ]
    wps = [i for i in items if i.item_type == "WORK_PACKAGE"]
    assert [(i.element_id, i.activity_id, i.start_date, i.finish_date, i.duration_days, i.estimate_hours)
            for i in wps] == [
        (3, None, d("12/04"), d("12/10"), None, Decimal(40)),
        (2, None, d("12/01"), d("12/03"), None, Decimal(24)),
        (4, None, None, None, None, Decimal("12.5")),
    ]


@pytest.mark.parametrize(
    "kw",
    [
        {"scope_versions": [1, 1]},
        {"schedule_versions": [0]},
        {"schedule_versions": [True]},
        {"change_request_id": 0},
        {"change_request_id": None},
        {"schedule_name": " "},
        {"schedule_name": "あ" * 201},
        {"activities": (RebaselineActivity(id=99, work_package_id=1, name="x", start_date=None, finish_date=None,
                                           duration_days=1, is_milestone=False, estimate_hours=None),)},
        {"elements": (*REBASE_ELEMENTS, RebaselineElement(id=5, parent_id=42, wbs_code="9.9", kind="COMPONENT",
                                                          name="x", own_estimate_hours=None))},
    ],
)
def test_rebaseline_rejects_invalid_input(kw: dict[str, object]) -> None:
    """RULE-CHG-004：版の番号の重複・1 未満、元の変更要求が無い、名前が空・201 文字、WP でない置き場の活動、木の誤りは拒む。"""
    with pytest.raises(BaselineError):
        rebaseline(**kw)


def test_rebaseline_name_length_boundary() -> None:
    """RULE-CHG-004：版の名前は 200 文字まで通す（4.1 の列の長さ。A-19）。"""
    assert rebaseline(schedule_name="あ" * 200).schedule.name == "あ" * 200
