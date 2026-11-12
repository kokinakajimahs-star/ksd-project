"""工数の ETC・EAC・多軸集計・差異の判定・実績工数の入力検査（app.domain.effort）の試験。

設計書 4.1（assignments・time_entries・progress_entries・schedule_baseline_items）・4.2・5.1・5.8、
要求仕様書 EFF-01・02・06〜10、7.10 BR-10・7.11 BR-11。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from app.domain.calendar import WorkCalendar
from app.domain.effort import (
    REMAINING,
    UNSCHEDULED,
    ActivityEffortInput,
    ActivityPeriod,
    AssignmentShare,
    EffortElement,
    EffortError,
    EffortRollup,
    EffortValues,
    EtcSource,
    OrganizationBucketKind,
    OrganizationKey,
    PeriodBucketKind,
    PeriodKey,
    PeriodUnit,
    TimeEntryChange,
    TimeEntryProblemCode,
    TimeEntryValue,
    VarianceStatus,
    aggregate_effort,
    check_time_entries,
    compute_etc,
    extract_variances,
    judge_variance,
    rollup_effort,
)
from app.domain.estimate import ElementKind, RollupSource

D = Decimal
DEL = ElementKind.DELIVERABLE
MGT = ElementKind.MANAGEMENT
CMP = ElementKind.COMPONENT
WP = ElementKind.WORK_PACKAGE
ALL_DAYS = WorkCalendar.of("1111111")
WEEKDAYS = WorkCalendar.of("1111100")

A, B, C = 101, 102, 103
U1, U2, U3 = 1, 2, 3


def values(planned: str, baseline: str | None, actual: str, etc: str, eac: str) -> EffortValues:
    return EffortValues(planned=D(planned), baseline=None if baseline is None else D(baseline), actual=D(actual),
                        etc=D(etc), eac=D(eac))


def same(a: EffortValues, b: EffortValues) -> bool:
    """値として等しいか（Decimal の桁の違いは問わない）。"""
    return (a.planned, a.baseline, a.actual, a.etc, a.eac) == (b.planned, b.baseline, b.actual, b.etc, b.eac)


def total_of(buckets: Mapping[Any, EffortValues]) -> EffortValues:
    total = EffortValues()
    for v in buckets.values():
        total = total.plus(v)
    return total


# ---------------------------------------------------------------------------
# RULE-EFF-001 残工数（ETC）
# ---------------------------------------------------------------------------


def test_entered_etc_has_priority() -> None:
    """RULE-EFF-001：担当者の入力値があれば、進捗率・実績に関わらずそれを使う（EFF-06「入力した ETC が優先される」）。"""
    etc = compute_etc(estimate_hours=D("40"), percent_complete=D("30"), actual_hours=D("10"), entered_hours=D("12.5"))
    assert (etc.hours, etc.source, etc.is_entered) == (D("12.5"), EtcSource.ENTERED, True)
    # 入力値 0 も入力として扱う（自動の 28 にしない）
    assert compute_etc(estimate_hours=D("40"), percent_complete=D("30"), actual_hours=D("0"), entered_hours=D("0")).hours == 0


def test_etc_from_percent_complete() -> None:
    """RULE-EFF-001：入力が無ければ ETC＝見積×（1−進捗率）（BR-10）。小数 2 桁へ四捨五入する。"""
    etc = compute_etc(estimate_hours=D("40"), percent_complete=D("25"), actual_hours=D("35"))
    assert (etc.hours, etc.source, etc.is_entered) == (D("30.00"), EtcSource.PROGRESS, False)
    assert compute_etc(estimate_hours=D("40"), percent_complete=D("0"), actual_hours=D("0")).hours == D("40")
    assert compute_etc(estimate_hours=D("10"), percent_complete=D("33.33"), actual_hours=D("1")).hours == D("6.67")
    assert compute_etc(estimate_hours=D("0.05"), percent_complete=D("50"), actual_hours=D("0")).hours == D("0.03")
    assert compute_etc(estimate_hours=D("40"), percent_complete=D("99.99"), actual_hours=D("0")).hours == D("0.00")
    # 未見積（None）は 0（RULE-EST-001 と同じ）
    assert compute_etc(estimate_hours=None, percent_complete=D("50"), actual_hours=D("3")).hours == 0


def test_etc_when_percent_not_computed() -> None:
    """RULE-EFF-001：進捗率が未算定なら ETC＝max(見積−実績, 0)（BR-10）。"""
    etc = compute_etc(estimate_hours=D("40"), percent_complete=None, actual_hours=D("15"))
    assert (etc.hours, etc.source) == (D("25"), EtcSource.ACTUAL)
    assert compute_etc(estimate_hours=D("40"), percent_complete=None, actual_hours=D("40")).hours == 0
    assert compute_etc(estimate_hours=D("40"), percent_complete=None, actual_hours=D("50.5")).hours == 0  # 0 未満にしない
    assert compute_etc(estimate_hours=None, percent_complete=None, actual_hours=D("0")).hours == 0


def test_completed_activity_etc_is_zero() -> None:
    """RULE-EFF-001：完了した活動（進捗率 100）の ETC は 0。入力した残工数があっても 0 にする（BR-10）。"""
    etc = compute_etc(estimate_hours=D("40"), percent_complete=D("100"), actual_hours=D("30"), entered_hours=D("5"))
    assert (etc.hours, etc.source) == (D("0"), EtcSource.COMPLETED)
    assert compute_etc(estimate_hours=D("40"), percent_complete=100, actual_hours=D("30")).source is EtcSource.COMPLETED


@pytest.mark.parametrize(
    ("estimate", "percent", "actual", "entered"),
    [
        (40.0, D("10"), D("0"), None),  # float は受けない
        (D("-1"), D("10"), D("0"), None),
        (D("40"), D("100.01"), D("0"), None),
        (D("40"), D("-0.01"), D("0"), None),
        (D("40"), D("10"), D("-1"), None),
        (D("40"), D("10"), D("0"), D("-0.5")),
        (D("40"), D("NaN"), D("0"), None),
        (D("40"), True, D("0"), None),
    ],
)
def test_etc_rejects_invalid_input(estimate: object, percent: object, actual: object, entered: object) -> None:
    """RULE-EFF-001：負の工数・0〜100 の外の進捗率・float・非数は拒む。"""
    with pytest.raises(EffortError):
        compute_etc(estimate_hours=estimate, percent_complete=percent, actual_hours=actual,  # type: ignore[arg-type]
                    entered_hours=entered)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# RULE-EFF-002 EACと上位への集計
# ---------------------------------------------------------------------------


def sample_elements() -> list[EffortElement]:
    """1 成果物
         11 WP（活動 A・B）
         12 WP（活動なし。自分の見積 10・基準 8）
       2 管理領域
         21 WP（活動 C。基準の後に足した）
       3 構成要素（子なし）
    """
    return [
        EffortElement(1, None, DEL, own_estimate_hours=D("99")),  # WP 以外の自分の見積は集計に使わない
        EffortElement(11, 1, WP, own_estimate_hours=D("77"), baseline_hours=D("70")),  # 活動を持つ WP の自分の見積・基準は使わない
        EffortElement(12, 1, WP, own_estimate_hours=D("10"), baseline_hours=D("8")),
        EffortElement(2, None, MGT),
        EffortElement(21, 2, WP),
        EffortElement(3, None, CMP),
    ]


def sample_activities(c_entered: str = "9") -> list[ActivityEffortInput]:
    return [
        # A：進捗率 50 → ETC 20、EAC 40
        ActivityEffortInput(A, 11, estimate_hours=D("40"), actual_hours=D("20"), percent_complete=D("50"), baseline_hours=D("40")),
        # B：未算定 → ETC max(16−4, 0)＝12、EAC 16
        ActivityEffortInput(B, 11, estimate_hours=D("16"), actual_hours=D("4"), percent_complete=None, baseline_hours=D("10")),
        # C：入力した残工数 → ETC 9、EAC 11。基準に無い
        ActivityEffortInput(C, 21, estimate_hours=D("8"), actual_hours=D("2"), percent_complete=D("10"),
                            entered_etc_hours=D(c_entered)),
    ]


def sample_rollup() -> EffortRollup:
    return rollup_effort(sample_elements(), sample_activities())


def test_eac_is_actual_plus_etc_and_rolls_up() -> None:
    """RULE-EFF-002：EAC＝実績＋ETC。WP は活動の合計、活動の無い WP は自分の見積（実績 0・ETC＝見積）、上位は下位の合計（5.8・EFF-07）。"""
    r = sample_rollup()
    assert same(r.activities[A].values, values("40", "40", "20", "20", "40"))
    assert same(r.activities[B].values, values("16", "10", "4", "12", "16"))
    assert same(r.activities[C].values, values("8", None, "2", "9", "11"))
    assert [r.activities[x].etc_source for x in (A, B, C)] == [EtcSource.PROGRESS, EtcSource.ACTUAL, EtcSource.ENTERED]
    assert same(r.elements[11].values, values("56", "50", "24", "32", "56"))
    assert same(r.elements[12].values, values("10", "8", "0", "10", "10"))
    assert same(r.elements[1].values, values("66", "58", "24", "42", "66"))
    assert same(r.elements[21].values, values("8", None, "2", "9", "11"))
    assert same(r.elements[2].values, values("8", None, "2", "9", "11"))
    assert same(r.elements[3].values, values("0", None, "0", "0", "0"))
    assert same(r.project, values("74", "58", "26", "51", "77"))
    assert r.roots == (1, 2, 3)
    assert [r.elements[e].source for e in (11, 12, 1, 3)] == [
        RollupSource.ACTIVITIES, RollupSource.OWN_ESTIMATE, RollupSource.CHILDREN, RollupSource.CHILDREN]
    # どの行でも EAC＝実績＋ETC
    for v in [a.values for a in r.activities.values()] + [e.values for e in r.elements.values()] + [r.project]:
        assert v.eac == v.actual + v.etc
    # 葉（活動と、活動の無い WP）の合計がプロジェクトの合計
    leaves = EffortValues()
    for _, _, v in r.leaf_values():
        leaves = leaves.plus(v)
    assert same(leaves, r.project)
    assert {(a, e) for a, e, _ in r.leaf_values()} == {(A, 11), (B, 11), (C, 21), (None, 12)}


def test_baseline_is_kept_apart_from_forecast() -> None:
    """RULE-EFF-002：予測（EAC）を更新しても基準の値は変わらない（EFF-08）。計画・基準・予測を別の値で持つ。"""
    before = sample_rollup()
    after = rollup_effort(sample_elements(), sample_activities(c_entered="3"))
    assert (after.activities[C].values.eac, after.project.eac) == (D("5"), D("71"))
    assert after.project.baseline == before.project.baseline == D("58")
    assert after.project.planned == before.project.planned == D("74")
    assert after.elements[1].values == before.elements[1].values


def test_rollup_without_activities_or_baseline() -> None:
    """RULE-EFF-002：活動の無い WP の自分の見積が空なら 0。基準の値が 1 つも無ければプロジェクトの基準は None。"""
    r = rollup_effort([EffortElement(1, None, DEL), EffortElement(11, 1, WP)])
    assert same(r.project, values("0", None, "0", "0", "0"))
    assert rollup_effort([]).project == EffortValues()


@pytest.mark.parametrize(
    ("elements", "activities"),
    [
        ([EffortElement(1, None, DEL)], [ActivityEffortInput(A, 1, D("1"), D("0"))]),  # WP 以外に置いた活動
        ([EffortElement(1, 2, WP), EffortElement(2, 1, CMP)], []),  # 循環
        ([EffortElement(1, None, WP)], [ActivityEffortInput(A, 9, D("1"), D("0"))]),  # 無い WP
        ([EffortElement(1, None, WP)], [ActivityEffortInput(A, 1, D("1"), D("-1"))]),  # 負の実績
        ([EffortElement(1, None, WP, own_estimate_hours=D("1"), baseline_hours=D("-1"))], []),  # 負の基準
        ([EffortElement(1, None, WP)], [ActivityEffortInput(A, 1, D("1"), D("0"), baseline_hours=D("-2"))]),
    ],
)
def test_rollup_rejects_invalid_tree_and_values(elements: list[EffortElement], activities: list[ActivityEffortInput]) -> None:
    """RULE-EFF-002：木の誤り（RULE-EST-001 と同じ検査）と負の値は EffortError。"""
    with pytest.raises(EffortError):
        rollup_effort(elements, activities)


# ---------------------------------------------------------------------------
# RULE-EFF-003 多軸集計
# ---------------------------------------------------------------------------


ASSIGNMENTS = [
    AssignmentShare(A, U1, D("30")),
    AssignmentShare(A, U2, D("10")),  # A は 3:1
    AssignmentShare(C, U2, None),
    AssignmentShare(C, U3, None),  # C は割当工数が空なので均等。B は割当なし（未割当）
]
TIME_ENTRIES = [
    TimeEntryValue(U1, A, date(2026, 12, 1), D("12")),
    TimeEntryValue(U2, A, date(2026, 12, 2), D("8")),
    TimeEntryValue(U3, B, date(2026, 11, 30), D("4")),  # 割当の無い活動の実績も入力した人に付く
    TimeEntryValue(U2, C, date(2026, 12, 3), D("2")),
    TimeEntryValue(U1, A, date(2026, 12, 11), D("5")),  # 報告日の後は入れない（5.8）
]
ORGANIZATIONS = {U1: "開発部", U2: "開発部", U3: None}
SCHEDULES = [
    ActivityPeriod(A, date(2026, 12, 1), date(2026, 12, 10), baseline_start=date(2026, 11, 28), baseline_finish=date(2026, 12, 2)),
    ActivityPeriod(B, date(2026, 11, 26), date(2026, 12, 5)),  # 基準の日付が無い → 基準は「日付なし」
]  # C は日付なし（日程を計算していない）


def sample_axes(unit: PeriodUnit = PeriodUnit.MONTH):  # type: ignore[no-untyped-def]
    return aggregate_effort(sample_rollup(), assignments=ASSIGNMENTS, time_entries=TIME_ENTRIES, organizations=ORGANIZATIONS,
                            schedules=SCHEDULES, calendar=ALL_DAYS, period_unit=unit, report_date=date(2026, 12, 10))


def test_every_axis_sums_to_project_total() -> None:
    """RULE-EFF-003：WBS・担当者・組織・期間（週・月）のどの軸でも、桶の合計がプロジェクトの合計に一致する（EFF-09 の受入基準）。"""
    for unit in PeriodUnit:
        axes = sample_axes(unit)
        assert same(axes.total, values("74", "58", "26", "51", "77"))
        assert same(total_of({r: axes.wbs[r] for r in axes.wbs_roots}), axes.total)
        assert same(total_of(axes.assignee), axes.total)
        assert same(total_of(axes.organization), axes.total)
        assert same(total_of(axes.period), axes.total)


def test_assignee_axis_splits_by_assigned_hours() -> None:
    """RULE-EFF-003：担当者の軸。計画・基準・ETC・EAC は割当工数の比で分け、実績は入力した人、割当が無ければ「未割当」（5.8 の表）。"""
    axes = sample_axes()
    # u1：A の 3/4（計画 30・基準 30・ETC 15・EAC 30）＋ 入力した実績 12
    assert same(axes.assignee[U1], values("30", "30", "12", "15", "30"))
    # u2：A の 1/4（10・10・5・10）＋ C の 1/2（4・基準なし・4.5・5.5）＋ 実績 8＋2
    assert same(axes.assignee[U2], values("14", "10", "10", "9.5", "15.5"))
    # u3：C の 1/2 ＋ B に入力した実績 4
    assert same(axes.assignee[U3], values("4", None, "4", "4.5", "5.5"))
    # 未割当：B（16・10・ETC 12・16）と活動の無い WP 12（10・8・10・10）
    assert same(axes.assignee[None], values("26", "18", "0", "22", "26"))


def test_organization_axis_groups_assignees() -> None:
    """RULE-EFF-003：組織の軸は担当者の軸をメンバーの所属でまとめる。所属の無い人と未割当は別の桶。"""
    axes = sample_axes()
    assert set(axes.organization) == {
        OrganizationKey(OrganizationBucketKind.ORGANIZATION, "開発部"),
        OrganizationKey(OrganizationBucketKind.NO_ORGANIZATION),
        OrganizationKey(OrganizationBucketKind.UNASSIGNED),
    }
    assert same(axes.organization[OrganizationKey(OrganizationBucketKind.ORGANIZATION, "開発部")],
                values("44", "40", "22", "24.5", "45.5"))
    assert same(axes.organization[OrganizationKey(OrganizationBucketKind.NO_ORGANIZATION)], values("4", None, "4", "4.5", "5.5"))
    assert same(axes.organization[OrganizationKey(OrganizationBucketKind.UNASSIGNED)], values("26", "18", "0", "22", "26"))


def test_month_axis_spreads_plan_over_working_days() -> None:
    """RULE-EFF-003：期間の軸。計画・基準は稼働日に均等に配り、実績は作業日、ETC は「残り」、EAC は実績＋「残り」（5.8 の表）。

    A：計画 12/01〜12/10（10 日）→ 12 月に 40。基準 11/28〜12/02（11 月 3 日・12 月 2 日）→ 24・16。
    B：計画 11/26〜12/05（11 月 5 日・12 月 5 日）→ 8・8。基準の日付が無い → 「日付なし」に 10。
    C と活動の無い WP 12：日付なし（計画 8＋10、基準 8）。
    """
    axes = sample_axes()
    nov, dec = PeriodKey(PeriodBucketKind.PERIOD, date(2026, 11, 1)), PeriodKey(PeriodBucketKind.PERIOD, date(2026, 12, 1))
    assert list(axes.period) == [nov, dec, REMAINING, UNSCHEDULED]
    assert same(axes.period[nov], values("8", "24", "4", "0", "4"))
    assert same(axes.period[dec], values("48", "16", "22", "0", "22"))
    assert same(axes.period[REMAINING], values("0", None, "0", "51", "51"))
    assert same(axes.period[UNSCHEDULED], values("18", "18", "0", "0", "0"))


def test_week_axis_counts_only_working_days() -> None:
    """RULE-EFF-003：週（月曜から）の桶。土日を非稼働にすると稼働日だけに配る。非稼働日のマイルストーンは終了日の週に置く。"""
    elements = [EffortElement(1, None, WP)]
    acts = [ActivityEffortInput(A, 1, D("10"), D("0")), ActivityEffortInput(B, 1, D("2"), D("0"))]
    schedules = [
        ActivityPeriod(A, date(2026, 12, 3), date(2026, 12, 9)),  # 木・金 ＋ 月・火・水（土日を除く 5 日）
        ActivityPeriod(B, date(2026, 12, 5), date(2026, 12, 5)),  # 土曜のマイルストーン
    ]
    axes = aggregate_effort(rollup_effort(elements, acts), schedules=schedules, calendar=WEEKDAYS, period_unit="WEEK")
    w1, w2 = PeriodKey(PeriodBucketKind.PERIOD, date(2026, 11, 30)), PeriodKey(PeriodBucketKind.PERIOD, date(2026, 12, 7))
    assert (axes.period[w1].planned, axes.period[w2].planned) == (D("4") + D("2"), D("6"))
    assert same(total_of(axes.period), axes.total)


def test_split_keeps_totals_to_the_hundredth() -> None:
    """RULE-EFF-003：割り切れない値は小数 2 桁で配り（最大剰余法）、桶の合計を崩さない。"""
    elements = [EffortElement(1, None, WP)]
    acts = [ActivityEffortInput(A, 1, D("10"), D("0"), percent_complete=D("0"))]
    shares = [AssignmentShare(A, U1), AssignmentShare(A, U2), AssignmentShare(A, U3)]
    axes = aggregate_effort(rollup_effort(elements, acts), assignments=shares,
                            schedules=[ActivityPeriod(A, date(2026, 11, 30), date(2026, 12, 2))], calendar=ALL_DAYS)
    assert [axes.assignee[u].planned for u in (U1, U2, U3)] == [D("3.34"), D("3.33"), D("3.33")]
    nov, dec = PeriodKey(PeriodBucketKind.PERIOD, date(2026, 11, 1)), PeriodKey(PeriodBucketKind.PERIOD, date(2026, 12, 1))
    # 11 月 1 日・12 月 2 日：3.333… と 6.666… → 剰余の大きい 12 月に 0.01 を配る
    assert (axes.period[nov].planned, axes.period[dec].planned) == (D("3.33"), D("6.67"))
    assert same(total_of(axes.assignee), axes.total) and same(total_of(axes.period), axes.total)


def test_axes_reject_inconsistent_input() -> None:
    """RULE-EFF-003：実績工数の行の合計が RULE-EFF-002 の実績と違う、集計に無い活動の行、同じ人の重複した割当は EffortError。"""
    rollup = sample_rollup()
    with pytest.raises(EffortError, match="一致しない"):
        aggregate_effort(rollup, time_entries=TIME_ENTRIES[:2], calendar=ALL_DAYS, report_date=date(2026, 12, 10))
    with pytest.raises(EffortError, match="一致しない"):  # 報告日を指定しなければ 12/11 の行も数える
        aggregate_effort(rollup, time_entries=TIME_ENTRIES, calendar=ALL_DAYS)
    with pytest.raises(EffortError):
        aggregate_effort(rollup, time_entries=[*TIME_ENTRIES[:4], TimeEntryValue(U1, 999, date(2026, 12, 1), D("1"))],
                         calendar=ALL_DAYS, report_date=date(2026, 12, 10))
    with pytest.raises(EffortError):
        aggregate_effort(rollup, assignments=[*ASSIGNMENTS, AssignmentShare(A, U1, D("1"))], time_entries=TIME_ENTRIES[:4],
                         calendar=ALL_DAYS)
    with pytest.raises(EffortError):
        aggregate_effort(rollup, time_entries=TIME_ENTRIES[:4], calendar=ALL_DAYS,
                         schedules=[ActivityPeriod(A, date(2026, 12, 5), date(2026, 12, 1))])
    with pytest.raises(EffortError):
        aggregate_effort(rollup, time_entries=TIME_ENTRIES[:4], calendar=ALL_DAYS, period_unit="DAY")


# ---------------------------------------------------------------------------
# RULE-EFF-004 差異の判定
# ---------------------------------------------------------------------------

RATE, HOURS = D("0.10"), D("8")  # 4.2 の既定（variance_rate・variance_hours）


@pytest.mark.parametrize(
    ("baseline", "eac", "status"),
    [
        ("100", "111", VarianceStatus.EXCEEDED),  # 差異 11 ＞ 8、差異率 11% ＞ 10%
        ("100", "110", VarianceStatus.WITHIN),  # 差異率がちょうど 10%（超えていない）
        ("50", "58", VarianceStatus.WITHIN),  # 差異がちょうど 8 人時（超えていない）
        ("50", "58.01", VarianceStatus.EXCEEDED),
        ("200", "215", VarianceStatus.WITHIN),  # 絶対値だけ超えた（差異率 7.5%）
        ("20", "27", VarianceStatus.WITHIN),  # 差異率だけ超えた（差異 7）
        ("100", "85", VarianceStatus.EXCEEDED),  # 減った向き（差異 −15、差異率 −15%）
        ("100", "90", VarianceStatus.WITHIN),  # 減った向きで差異率がちょうど −10%
        ("0", "9", VarianceStatus.EXCEEDED),  # 基準 0：差異率は割れないので超えたとみなす
        ("0", "0", VarianceStatus.WITHIN),
    ],
)
def test_variance_needs_both_thresholds(baseline: str, eac: str, status: VarianceStatus) -> None:
    """RULE-EFF-004：差異＝EAC−基準、差異率＝差異÷基準。差異率と絶対値の両方の閾値を超えた要素を抽出する（BR-11）。"""
    judged = judge_variance(baseline_hours=D(baseline), eac_hours=D(eac), rate_threshold=RATE, hours_threshold=HOURS)
    assert judged.status is status
    assert judged.variance == D(eac) - D(baseline)
    assert judged.rate == (None if D(baseline) == 0 else (D(eac) - D(baseline)) / D(baseline))
    assert judged.extracted is (status is not VarianceStatus.WITHIN)


def test_element_not_in_baseline_is_added() -> None:
    """RULE-EFF-004：基準に無い要素は差異率を計算せず「追加」として抽出する（BR-11）。"""
    judged = judge_variance(baseline_hours=None, eac_hours=D("3"), rate_threshold=RATE, hours_threshold=HOURS)
    assert (judged.status, judged.variance, judged.rate, judged.extracted) == (VarianceStatus.ADDED, None, None, True)


def test_extract_variances_keeps_input_order() -> None:
    """RULE-EFF-004：閾値を超えた要素と追加の要素だけを入力の順に返す。閾値はプロジェクトの設定で変えられる。"""
    targets = [
        ("1.1", values("0", "100", "0", "0", "120")),
        ("1.2", values("0", "100", "0", "0", "105")),
        ("2.1", values("0", None, "0", "0", "4")),
        ("2.2", values("0", "40", "0", "0", "20")),
    ]
    found = extract_variances(targets, rate_threshold=RATE, hours_threshold=HOURS)
    assert [(k, r.status) for k, r in found] == [
        ("1.1", VarianceStatus.EXCEEDED), ("2.1", VarianceStatus.ADDED), ("2.2", VarianceStatus.EXCEEDED)]
    # 閾値を緩めると 1.1（20%・20 人時）は抽出されない
    looser = extract_variances(targets, rate_threshold=D("0.25"), hours_threshold=D("8"))
    assert [k for k, _ in looser] == ["2.1", "2.2"]
    # RULE-EFF-002 の要素の値から抽出する：21・2（C は基準の後に足した）と、子の無い 3 は基準に無いので「追加」
    rollup = sample_rollup()
    from_rollup = extract_variances(((e, rollup.elements[e].values) for e in (1, 11, 12, 2, 21, 3)),
                                    rate_threshold=RATE, hours_threshold=HOURS)
    assert [(k, r.status) for k, r in from_rollup] == [
        (2, VarianceStatus.ADDED), (21, VarianceStatus.ADDED), (3, VarianceStatus.ADDED)]


@pytest.mark.parametrize(("rate", "hours"), [(D("-0.1"), D("8")), (D("0.1"), D("-1")), (0.1, D("8")), (D("0.1"), D("NaN"))])
def test_variance_rejects_invalid_thresholds(rate: object, hours: object) -> None:
    """RULE-EFF-004：負の閾値・float・非数の閾値は拒む。"""
    with pytest.raises(EffortError):
        judge_variance(baseline_hours=D("10"), eac_hours=D("20"), rate_threshold=rate, hours_threshold=hours)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# RULE-EFF-005 実績工数の入力検査
# ---------------------------------------------------------------------------

OTHER = 901  # 別のプロジェクトの活動（1 日の合計には数える）
EXISTING = [
    TimeEntryValue(U1, A, date(2026, 12, 8), D("6")),
    TimeEntryValue(U1, OTHER, date(2026, 12, 8), D("14")),
    TimeEntryValue(U1, A, date(2026, 12, 3), D("3")),
    TimeEntryValue(U1, C, date(2026, 12, 9), D("5")),  # 割当を外された活動の今の行
]
ASSIGNED = {A, B}


def change(activity: int, day: int, hours: object) -> TimeEntryChange:
    return TimeEntryChange(activity_id=activity, work_date=date(2026, 12, day), hours=hours)  # type: ignore[arg-type]


def codes(result) -> list[tuple[TimeEntryProblemCode, int | None, int]]:  # type: ignore[no-untyped-def]
    return [(p.code, p.activity_id, p.work_date.day) for p in result.problems]


def test_valid_entries_pass_and_day_total_may_reach_24() -> None:
    """RULE-EFF-005：自分の割当の活動への 0 以上の値は通る。1 日の合計はちょうど 24 時間まで（既存の行の置き換えを反映して数える）。"""
    result = check_time_entries([change(A, 8, D("4")), change(B, 8, D("6"))], existing=EXISTING, assigned_activity_ids=ASSIGNED)
    assert result.ok and result.problems == ()
    assert result.day_totals == {date(2026, 12, 8): D("24")}  # A は 6 → 4 に置き換え、別のプロジェクト 14、B 6
    assert result.changes == (change(A, 8, D("4")), change(B, 8, D("6")))
    assert check_time_entries([change(B, 10, 0)], existing=[], assigned_activity_ids=ASSIGNED).ok


def test_day_total_over_24_is_rejected() -> None:
    """RULE-EFF-005：1 日の合計が 24 時間を超えるとエラー（EFF-02 の受入基準）。"""
    result = check_time_entries([change(A, 8, D("4")), change(B, 8, D("6.01"))], existing=EXISTING, assigned_activity_ids=ASSIGNED)
    assert codes(result) == [(TimeEntryProblemCode.DAY_TOTAL_EXCEEDED, None, 8)]
    assert result.day_totals[date(2026, 12, 8)] == D("24.01")
    assert codes(check_time_entries([change(A, 11, D("24.01"))], existing=[], assigned_activity_ids=ASSIGNED)) == [
        (TimeEntryProblemCode.DAY_TOTAL_EXCEEDED, None, 11)]


def test_only_own_assigned_activities() -> None:
    """RULE-EFF-005：自分の割当の活動だけに入力できる。今の値と同じ値を送り直すだけなら変えていないので通す。"""
    result = check_time_entries([change(C, 10, D("1"))], existing=EXISTING, assigned_activity_ids=ASSIGNED)
    assert codes(result) == [(TimeEntryProblemCode.NOT_ASSIGNED, C, 10)]
    assert check_time_entries([change(C, 9, D("5"))], existing=EXISTING, assigned_activity_ids=ASSIGNED).ok
    assert codes(check_time_entries([change(C, 9, D("4"))], existing=EXISTING, assigned_activity_ids=ASSIGNED)) == [
        (TimeEntryProblemCode.NOT_ASSIGNED, C, 9)]


@pytest.mark.parametrize(
    ("hours", "code"),
    [
        (D("-1"), TimeEntryProblemCode.NEGATIVE_HOURS),
        (D("-0.01"), TimeEntryProblemCode.NEGATIVE_HOURS),
        (D("1.005"), TimeEntryProblemCode.INVALID_HOURS),
        (1.5, TimeEntryProblemCode.INVALID_HOURS),
        (D("NaN"), TimeEntryProblemCode.INVALID_HOURS),
        (None, TimeEntryProblemCode.INVALID_HOURS),
    ],
)
def test_negative_and_invalid_hours_are_rejected(hours: object, code: TimeEntryProblemCode) -> None:
    """RULE-EFF-005：負の値の禁止。数でない値・小数 3 桁目以降のある値も拒む（2.4 の Numeric(10,2)）。"""
    result = check_time_entries([change(A, 10, hours)], existing=[], assigned_activity_ids=ASSIGNED)
    assert codes(result) == [(code, A, 10)]
    assert result.changes == ()


def test_days_through_finalized_report_are_locked() -> None:
    """RULE-EFF-005：確定した報告日（最も新しいもの）以前の日の値は変えない（報告日の当日も。設計書 1.1・CR-005）。翌日から変えられる。"""
    finalized = [date(2026, 12, 1), date(2026, 12, 5)]
    locked = check_time_entries([change(A, 4, D("2"))], existing=EXISTING, assigned_activity_ids=ASSIGNED,
                                finalized_report_dates=finalized)
    assert codes(locked) == [(TimeEntryProblemCode.LOCKED_DATE, A, 4)]
    on_report_date = check_time_entries([change(A, 5, D("2"))], existing=EXISTING, assigned_activity_ids=ASSIGNED,
                                        finalized_report_dates=finalized)
    assert codes(on_report_date) == [(TimeEntryProblemCode.LOCKED_DATE, A, 5)]
    assert check_time_entries([change(A, 6, D("2"))], existing=EXISTING, assigned_activity_ids=ASSIGNED,
                              finalized_report_dates=finalized).ok
    # 今の値と同じ値（行が無いマスは 0）を送り直すのは「変える」に当たらない
    unchanged = check_time_entries([change(A, 3, D("3.00")), change(B, 2, D("0"))], existing=EXISTING,
                                   assigned_activity_ids=ASSIGNED, finalized_report_dates=finalized)
    assert unchanged.ok and unchanged.changes == ()
    assert codes(check_time_entries([change(A, 3, D("0"))], existing=EXISTING, assigned_activity_ids=ASSIGNED,
                                    finalized_report_dates=finalized)) == [(TimeEntryProblemCode.LOCKED_DATE, A, 3)]


def test_all_problems_are_collected() -> None:
    """RULE-EFF-005：全てのマスを検査して問題を全て返す（同じマスの重複も含む）。問題のあるマスは日の合計に入れない。"""
    result = check_time_entries(
        [change(A, 10, D("20")), change(A, 10, D("1")), change(C, 10, D("2")), change(B, 10, D("-3")), change(B, 10, D("5"))],
        existing=[], assigned_activity_ids=ASSIGNED, finalized_report_dates=[date(2026, 12, 5)],
    )
    assert codes(result) == [
        (TimeEntryProblemCode.DUPLICATE, A, 10),
        (TimeEntryProblemCode.NOT_ASSIGNED, C, 10),
        (TimeEntryProblemCode.NEGATIVE_HOURS, B, 10),
        (TimeEntryProblemCode.DUPLICATE, B, 10),
    ]
    assert not result.ok and result.day_totals == {date(2026, 12, 10): D("20")}
