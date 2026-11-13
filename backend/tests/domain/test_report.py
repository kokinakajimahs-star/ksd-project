"""定例会議レポートの組み立て（app.domain.report）の試験。

設計書 5.1 の RULE-RPT-001、7.4（5.6 の試験の基準・5.7 の見込み）、要求仕様書 RPT-01。
既定は全ての曜日を稼働日、原点 2026-12-01、報告日 2026-12-10（L＝10）。5.6 の表の 4 行に、資料作成の後に置く
マイルストーン「公開判定」（12/21）を足す。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from app.domain.calendar import WorkCalendar
from app.domain.effort import EffortValues
from app.domain.gantt import GanttActivity, GanttChart, GanttElement, build_gantt
from app.domain.lightning import Judgement
from app.domain.report import (
    CorrectiveActionDue,
    ProgressNote,
    ReportError,
    build_meeting_report,
)
from app.domain.scheduling import ScheduleActivity, calculate_schedule
from app.domain.snapshot import SnapshotActivity, StatusReportStatus, finalize_status_report
from app.domain.workflow import CorrectiveActionStatus

ALL_DAYS = WorkCalendar("1111111")
ORIGIN = date(2026, 12, 1)
REPORT = date(2026, 12, 10)


def d(day: int) -> date:
    return date(2026, 12, day)


# 今の計画（日程計算の入力）。1 → 5（公開判定）の依存だけを持つ
SCHEDULE = [
    ScheduleActivity(1, 21),
    ScheduleActivity(2, 21),
    ScheduleActivity(3, 7, requested_start=d(12)),
    ScheduleActivity(5, 0, is_milestone=True),
    ScheduleActivity(4, 8),
]
DEPS = [(1, 5)]

# 報告日の値（ガントの行の順：WP 10 の 1・2・3・5、WP 20 の 4）。5.6 の表の 4 行と公開判定
ROWS = [
    SnapshotActivity(1, "1.1", "資料作成", d(1), d(21), 21, False, True, Decimal(25), d(1)),
    SnapshotActivity(2, "1.2", "関係者調整", d(1), d(21), 21, False, True, Decimal(60), d(1)),
    SnapshotActivity(3, "1.3", "公開準備", d(12), d(18), 7, False, False, Decimal(0)),
    SnapshotActivity(5, "1.4", "公開判定", d(21), d(21), 0, True, True, Decimal(0)),
    SnapshotActivity(4, "2.1", "環境整備", d(1), d(8), 8, False, False, Decimal(100), d(1), d(8)),
]

ELEMENTS = [
    GanttElement(id=10, parent_id=None, wbs_code="1.0", name="公開", sort_order=1, sibling_seq=1),
    GanttElement(id=20, parent_id=None, wbs_code="2.0", name="基盤", sort_order=2, sibling_seq=2),
]
WORK_PACKAGE = {1: 10, 2: 10, 3: 10, 5: 10, 4: 20}
SORT_ORDER = {1: 1, 2: 2, 3: 3, 5: 4, 4: 1}

ESTIMATES: dict[int, Decimal | None] = {1: Decimal(160), 2: Decimal(80), 3: Decimal(40), 4: Decimal(64), 5: None}


def gantt_for(
    rows: list[SnapshotActivity], schedule: list[ScheduleActivity] = SCHEDULE, deps: list[tuple[int, int]] = DEPS,
) -> GanttChart:
    """今の計画のガント（RULE-SCH-005）。日付・クリティカル・フロートは日程計算（RULE-SCH-002・003）の値。"""
    result = calculate_schedule(schedule, deps, calendar=ALL_DAYS, project_start=ORIGIN)
    ids = {a.id for a in schedule}
    return build_gantt(
        ELEMENTS,
        [
            GanttActivity(
                id=a.activity_id,
                work_package_id=WORK_PACKAGE[a.activity_id],
                name=a.name,
                sort_order=SORT_ORDER[a.activity_id],
                is_milestone=a.is_milestone,
                start_date=result.activities[a.activity_id].start_date,
                finish_date=result.activities[a.activity_id].finish_date,
                is_critical=result.activities[a.activity_id].times.is_critical,
                total_float=result.activities[a.activity_id].times.total_float,
                percent_complete=a.percent_complete,
                actual_start=a.actual_start,
                actual_finish=a.actual_finish,
            )
            for a in rows
            if a.activity_id in ids
        ],
        [dep for dep in deps if dep[0] in ids and dep[1] in ids],
        calendar=ALL_DAYS,
        project_start=ORIGIN,
        report_date=REPORT,
    )


def open_report(rows: list[SnapshotActivity] = ROWS, **kwargs: object):  # type: ignore[no-untyped-def]
    params: dict[str, object] = {
        "status": StatusReportStatus.OPEN,
        "report_date": REPORT,
        "calendar": ALL_DAYS,
        "project_start": ORIGIN,
        "gantt": gantt_for(rows),
        "schedule_activities": SCHEDULE,
        "dependencies": DEPS,
        "activities": rows,
        "tolerance_days": Decimal("0.5"),
        "extract_days": Decimal(2),
        "estimate_hours": ESTIMATES,
    }
    params.update(kwargs)
    return build_meeting_report(**params)  # type: ignore[arg-type]


def snapshot_of(rows: list[SnapshotActivity] = ROWS):  # type: ignore[no-untyped-def]
    return finalize_status_report(
        status=StatusReportStatus.OPEN,
        report_date=REPORT,
        activities=rows,
        calendar=ALL_DAYS,
        project_start=ORIGIN,
        tolerance_days=Decimal("0.5"),
        extract_days=Decimal(2),
    ).snapshot


# ---------------------------------------------------------------------------
# 上段：ガント＋イナズマ線
# ---------------------------------------------------------------------------


def test_open_report_lightning_follows_design_5_6_example() -> None:
    """RULE-RPT-001：確定前は計算値で組み立てる。イナズマ線は 5.6 の試験の基準（稲妻チャート.txt §6）と同じポイント・日付・判定。"""
    report = open_report()

    assert report.finalized is False
    assert report.report_date == REPORT
    assert report.snapshot.report_line == 10
    by_activity = {v.activity_id: v for v in report.lightning.vertices}
    expected = {
        1: (Decimal("5.25"), d(6), Judgement.BEHIND, Decimal("-4.75")),
        2: (Decimal("12.6"), d(13), Judgement.AHEAD, Decimal("2.6")),
        3: (Decimal(10), d(10), Judgement.NOT_STARTED, Decimal(0)),
        4: (Decimal(10), d(10), Judgement.COMPLETED, Decimal(0)),
    }
    for activity_id, (point, shown, judgement, deviation) in expected.items():
        vertex = by_activity[activity_id]
        assert vertex.point == point
        assert vertex.display_date == shown
        assert vertex.judgement is judgement
        assert vertex.deviation == deviation
    # 報告日線は報告日の終業
    assert report.lightning.report_line == 10
    assert report.lightning.report_line_coordinate.date == REPORT
    assert report.missing_activity_ids == ()


def test_lightning_vertices_follow_gantt_rows_and_window_is_four_weeks() -> None:
    """RULE-RPT-001：線の頂点はガントの行の順（RULE-SCH-005）に並び、上段の表示の範囲は報告日の前後 4 週（7.4）。"""
    report = open_report()

    assert [row.activity_id for row in report.gantt.rows] == [1, 2, 3, 5, 4]
    assert [(v.row_index, v.activity_id) for v in report.lightning.vertices] == [(0, 1), (1, 2), (2, 3), (3, 5), (4, 4)]
    assert report.window.start == date(2026, 11, 12)
    assert report.window.end == date(2027, 1, 7)


# ---------------------------------------------------------------------------
# 下段左：遅延一覧
# ---------------------------------------------------------------------------


def test_delay_list_is_ordered_by_deviation_with_reason_and_forecast() -> None:
    """RULE-RPT-001：遅延一覧は δ≦−抽出の閾値の活動をずれの大きい順に並べ、遅延理由と完了見込み（入力か 5.7 の 2 の式）を付ける。"""
    rows = [replace(ROWS[1], percent_complete=Decimal(30)) if r.activity_id == 2 else r for r in ROWS]  # P=6.3, δ=−3.7
    report = open_report(
        rows,
        progress_notes=[ProgressNote(activity_id=1, delay_reason="原稿の確認待ち", forecast_finish=d(24))],
    )

    assert [row.activity_id for row in report.delays] == [1, 2]
    first, second = report.delays
    assert (first.wbs_code, first.name, first.deviation) == ("1.1", "資料作成", Decimal("-4.75"))
    assert first.delay_reason == "原稿の確認待ち"
    assert first.forecast_finish == d(24)
    assert first.forecast_entered is True
    assert first.planned_finish == d(21)
    assert first.is_critical is True
    # 完了見込みの入力が無ければ L + ceil(d × (1 − 進捗率)) = 10 + ceil(21 × 0.7) = 25 → 12/25
    assert (second.name, second.deviation, second.percent_complete) == ("関係者調整", Decimal("-3.7"), Decimal(30))
    assert second.forecast_finish == d(25)
    assert second.forecast_entered is False
    assert second.delay_reason is None
    assert report.kpi.behind_count == 2


def test_delay_threshold_boundary_uses_extract_days() -> None:
    """RULE-RPT-001：抽出の閾値ちょうど（δ＝−閾値）は抽出し、それより小さい遅れは一覧に出さない（遅れの件数には数える）。"""
    exactly = open_report(extract_days=Decimal("4.75"))
    assert [row.activity_id for row in exactly.delays] == [1]

    above = open_report(extract_days=Decimal("4.76"))
    assert above.delays == ()
    assert above.kpi.behind_count == 1


# ---------------------------------------------------------------------------
# 下段中：マイルストーンの状況
# ---------------------------------------------------------------------------


def test_milestone_forecast_from_delay_impact() -> None:
    """RULE-RPT-001：マイルストーンの見込み日は遅延の見込みを全て当てた前進計算（5.7 の 4）、ずれは見込み日−予定日（稼働日）。"""
    report = open_report()

    # 資料作成の見込みの終了 10 + ceil(21 × 0.75) = 26 → 公開判定は 12/21 から 12/26 へ
    # 行は最新計画のガントの行（WBS ID は置き場の WP の 1.0。CR-013）
    (milestone,) = report.milestones
    assert (milestone.activity_id, milestone.wbs_code, milestone.name) == (5, "1.0", "公開判定")
    assert milestone.planned_date == d(21)
    assert milestone.forecast_date == d(26)
    assert milestone.slip_days == 5


def test_milestone_uses_entered_forecast_and_stays_when_not_affected() -> None:
    """RULE-RPT-001：完了見込み日の入力があればそれを当てる。遅延の影響を受けないマイルストーンは予定日のまま（ずれ 0）。"""
    entered = open_report(progress_notes=[ProgressNote(activity_id=1, forecast_finish=d(24))])
    (milestone,) = entered.milestones
    assert (milestone.forecast_date, milestone.slip_days) == (d(24), 3)

    on_time = [replace(r, percent_complete=Decimal(50)) if r.activity_id == 1 else r for r in ROWS]  # P=10.5, 予定どおり
    (milestone,) = open_report(on_time).milestones
    assert (milestone.planned_date, milestone.forecast_date, milestone.slip_days) == (d(21), d(21), 0)


def test_milestone_slip_counts_working_days() -> None:
    """RULE-RPT-001：ずれは稼働日で数える（土日を非稼働にした場合。PRG-10）。金曜の予定が翌週の水曜に延びれば 3 稼働日。"""
    weekdays = WorkCalendar("1111100")
    report_date = d(3)  # 木曜。L＝3
    schedule = [ScheduleActivity(1, 4), ScheduleActivity(2, 0, is_milestone=True)]
    rows = [
        SnapshotActivity(1, "1.1", "作業", d(1), d(4), 4, False, True, Decimal(0), d(1)),  # P=0, δ=−3
        SnapshotActivity(2, "1.2", "完了確認", d(4), d(4), 0, True, True, Decimal(0)),
    ]
    gantt = build_gantt(
        [GanttElement(id=10, parent_id=None, wbs_code="1.0", name="作業")],
        [
            GanttActivity(1, 10, "作業", 1, False, d(1), d(4), True, 0, actual_start=d(1)),
            GanttActivity(2, 10, "完了確認", 2, True, d(4), d(4), True, 0),
        ],
        [(1, 2)],
        calendar=weekdays,
        project_start=ORIGIN,
        report_date=report_date,
    )
    report = build_meeting_report(
        status=StatusReportStatus.OPEN,
        report_date=report_date,
        calendar=weekdays,
        project_start=ORIGIN,
        gantt=gantt,
        schedule_activities=schedule,
        dependencies=[(1, 2)],
        activities=rows,
    )

    # 見込みの終了 3 + ceil(4 × 1) = 7 → 添字 6 の稼働日（12/09 水曜）
    (milestone,) = report.milestones
    assert (milestone.planned_date, milestone.forecast_date, milestone.slip_days) == (d(4), d(9), 3)


# ---------------------------------------------------------------------------
# 下段右：主要 KPI
# ---------------------------------------------------------------------------


def test_overall_progress_is_estimate_weighted_average() -> None:
    """RULE-RPT-001：全体の進捗率＝見積で重み付けした進捗率の平均（小数 2 桁）。見積の無い活動の重みは 0。"""
    # (160×25 + 80×60 + 40×0 + 64×100) / (160 + 80 + 40 + 64) = 15200 / 344 = 44.186… → 44.19
    assert open_report().kpi.overall_progress == Decimal("44.19")


def test_overall_progress_excludes_not_computed_and_none_when_no_weight() -> None:
    """RULE-RPT-001：進捗率が未算定の活動は分子・分母の両方から外す。重みの合計が 0 なら全体の進捗率は None。"""
    rows = [replace(r, percent_complete=None) if r.activity_id == 2 else r for r in ROWS]
    report = open_report(rows)
    # (160×25 + 40×0 + 64×100) / (160 + 40 + 64) = 10400 / 264 = 39.393… → 39.39
    assert report.kpi.overall_progress == Decimal("39.39")
    assert {v.activity_id: v.judgement for v in report.lightning.vertices}[2] is Judgement.NOT_COMPUTED

    assert open_report(estimate_hours={}).kpi.overall_progress is None


def test_overall_progress_rejects_negative_estimate() -> None:
    """RULE-RPT-001：負の見積は推測せずに拒む。"""
    with pytest.raises(ReportError, match="見積"):
        open_report(estimate_hours={**ESTIMATES, 3: Decimal(-1)})


def test_kpi_eac_variance_critical_float_and_overdue_actions() -> None:
    """RULE-RPT-001：EAC と基準の差（EAC−基準）、クリティカルパスの最小フロート、是正措置の期限超過（報告日を今日とする）の件数。"""
    actions = [
        CorrectiveActionDue(1, d(9), CorrectiveActionStatus.IN_PROGRESS),  # 期限 < 報告日・未完了 → 超過
        CorrectiveActionDue(2, d(10), CorrectiveActionStatus.NOT_STARTED),  # 期限＝報告日 → 超過でない
        CorrectiveActionDue(3, d(1), CorrectiveActionStatus.DONE),  # 完了 → 超過でない
        CorrectiveActionDue(4, None, "NOT_STARTED"),  # 期限なし → 超過でない
    ]
    effort = EffortValues(
        planned=Decimal(344), baseline=Decimal(300), actual=Decimal(174), etc=Decimal(166), eac=Decimal(340)
    )
    kpi = open_report(effort=effort, corrective_actions=actions).kpi

    assert (kpi.eac_hours, kpi.baseline_hours, kpi.eac_variance_hours) == (Decimal(340), Decimal(300), Decimal(40))
    assert kpi.overdue_action_count == 1
    # 資料作成・関係者調整・公開判定がフロート 0 のクリティカル。公開準備（3）・環境整備（13）は外れる
    assert kpi.min_critical_float == 0
    assert kpi.behind_count == 1


def test_kpi_values_absent_without_inputs() -> None:
    """RULE-RPT-001：工数の合計が無い・基準が無いときの差、クリティカルな行の無いときの最小フロートは None。"""
    no_effort = open_report().kpi
    assert (no_effort.eac_hours, no_effort.baseline_hours, no_effort.eac_variance_hours) == (None, None, None)
    assert no_effort.overdue_action_count == 0

    no_baseline = open_report(effort=EffortValues(eac=Decimal(340))).kpi
    assert (no_baseline.eac_hours, no_baseline.eac_variance_hours) == (Decimal(340), None)

    plain = open_report()
    not_critical = replace(plain.gantt, rows=tuple(replace(r, is_critical=False) for r in plain.gantt.rows))
    assert open_report(gantt=not_critical).kpi.min_critical_float is None


def test_duplicate_corrective_action_is_rejected() -> None:
    """RULE-RPT-001：同じ是正措置が 2 つ・未定義の状態は拒む。"""
    with pytest.raises(ReportError, match="2 つ"):
        open_report(corrective_actions=[CorrectiveActionDue(1, d(9), "NOT_STARTED")] * 2)
    with pytest.raises(ReportError, match="是正措置 1"):
        open_report(corrective_actions=[CorrectiveActionDue(1, d(9), "CLOSED")])


# ---------------------------------------------------------------------------
# 確定済み：スナップショットの写しだけから組み立てる
# ---------------------------------------------------------------------------


def test_finalized_report_uses_snapshot_not_current_values() -> None:
    """RULE-RPT-001：確定済みはスナップショットの写しの値で組み立てる（BR-13）。今の進捗が変わっても報告日の値は変わらない。

    写しの後に消した活動（公開準備）は今のガントに無いので線から外し、``missing_activity_ids`` に返す。
    """
    snapshot = snapshot_of()
    # 確定の後：資料作成の進捗は 80% になり、公開準備を消した
    current = [replace(r, percent_complete=Decimal(80)) if r.activity_id == 1 else r for r in ROWS if r.activity_id != 3]
    schedule = [a for a in SCHEDULE if a.id != 3]
    report = build_meeting_report(
        status="FINALIZED",
        report_date=REPORT,
        snapshot=snapshot,
        calendar=ALL_DAYS,
        project_start=ORIGIN,
        gantt=gantt_for(current, schedule),
        schedule_activities=schedule,
        dependencies=DEPS,
        estimate_hours=ESTIMATES,
    )

    assert report.finalized is True
    assert report.snapshot is snapshot
    assert report.missing_activity_ids == (3,)
    # 今のガントの行（1・2・5・4）へ置き直す。値は写しのまま
    assert [(v.row_index, v.activity_id) for v in report.lightning.vertices] == [(0, 1), (1, 2), (2, 5), (3, 4)]
    vertex = report.lightning.vertices[0]
    assert (vertex.point, vertex.judgement) == (Decimal("5.25"), Judgement.BEHIND)
    assert [(row.activity_id, row.deviation) for row in report.delays] == [(1, Decimal("-4.75"))]
    assert report.kpi.behind_count == 1
    assert report.kpi.overall_progress == Decimal("44.19")  # 写しの進捗率（25%）で重み付けする


def test_finalized_report_takes_plan_values_from_latest_plan() -> None:
    """RULE-RPT-001：確定済みでも計画の値（マイルストーンの予定日・見込み日・ずれ）は最新計画から求め、進捗の値は写しのまま（7.4・CR-013）。

    確定の後に資料作成を 21 日から 25 日へ延ばした。公開判定の予定日は最新計画の 12/25（写しの 12/21 ではない）、見込み日は
    写しの進捗率 25% を最新計画へ当てた 10 + ceil(25 × 0.75) = 29 → 12/29、ずれは 4 稼働日（写しの予定日と混ぜると 8 になる）。
    """
    snapshot = snapshot_of()
    schedule = [ScheduleActivity(1, 25) if a.id == 1 else a for a in SCHEDULE]
    report = build_meeting_report(
        status=StatusReportStatus.FINALIZED,
        report_date=REPORT,
        snapshot=snapshot,
        calendar=ALL_DAYS,
        project_start=ORIGIN,
        gantt=gantt_for(ROWS, schedule),
        schedule_activities=schedule,
        dependencies=DEPS,
        estimate_hours=ESTIMATES,
    )

    (milestone,) = report.milestones
    milestone_row = next(r for r in report.gantt.rows if r.activity_id == 5)
    assert milestone_row.planned is not None and milestone_row.planned.end_date == d(25)  # ガントのバーも最新計画
    assert (milestone.activity_id, milestone.planned_date, milestone.forecast_date, milestone.slip_days) == (
        5, d(25), d(29), 4)
    # 進捗の値は写しのまま：線の頂点・判定、遅延一覧（予定の終了も写し）、KPI の進捗率と遅れの件数
    vertex = report.lightning.vertices[0]
    assert (vertex.activity_id, vertex.point, vertex.judgement) == (1, Decimal("5.25"), Judgement.BEHIND)
    assert [(r.activity_id, r.deviation, r.planned_finish) for r in report.delays] == [(1, Decimal("-4.75"), d(21))]
    assert (report.kpi.overall_progress, report.kpi.behind_count) == (Decimal("44.19"), 1)

    # 確定の後に消したマイルストーンは最新計画に無いので出さない
    without = [a for a in SCHEDULE if a.id != 5]
    gone = build_meeting_report(
        status=StatusReportStatus.FINALIZED,
        report_date=REPORT,
        snapshot=snapshot,
        calendar=ALL_DAYS,
        project_start=ORIGIN,
        gantt=gantt_for(ROWS, without, []),
        schedule_activities=without,
        dependencies=[],
    )
    assert gone.milestones == ()
    assert gone.missing_activity_ids == (5,)


def test_milestone_without_planned_date_in_latest_plan_is_rejected() -> None:
    """RULE-RPT-001：最新計画のマイルストーンに予定日（日程の計算結果）が無ければ、推測せずに拒む（CR-013）。"""
    plain = open_report()
    unscheduled = replace(
        plain.gantt,
        rows=tuple(replace(r, planned=None) if r.activity_id == 5 else r for r in plain.gantt.rows),
    )
    with pytest.raises(ReportError, match="予定日"):
        open_report(gantt=unscheduled)


def test_finalized_requires_matching_snapshot_only() -> None:
    """RULE-RPT-001：確定済みは写しが要り、今の活動の値は受けない。写しの報告日が違えば拒む。"""
    base = {
        "status": StatusReportStatus.FINALIZED,
        "report_date": REPORT,
        "calendar": ALL_DAYS,
        "project_start": ORIGIN,
        "gantt": gantt_for(ROWS),
        "schedule_activities": SCHEDULE,
        "dependencies": DEPS,
    }
    with pytest.raises(ReportError, match="スナップショットが無い"):
        build_meeting_report(**base)  # type: ignore[arg-type]
    with pytest.raises(ReportError, match="写しだけ"):
        build_meeting_report(**base, snapshot=snapshot_of(), activities=ROWS)  # type: ignore[arg-type]
    with pytest.raises(ReportError, match="報告日"):
        build_meeting_report(**{**base, "report_date": d(11)}, snapshot=snapshot_of())  # type: ignore[arg-type]


def test_open_report_rejects_snapshot_and_bad_inputs() -> None:
    """RULE-RPT-001：確定前に写しは無い。未定義の状態・同じ活動の進捗の入力が 2 つは拒む。"""
    with pytest.raises(ReportError, match="確定前"):
        open_report(snapshot=snapshot_of())
    with pytest.raises(ReportError, match="状態"):
        open_report(status="CLOSED")
    with pytest.raises(ReportError, match="2 つ"):
        open_report(progress_notes=[ProgressNote(activity_id=1), ProgressNote(activity_id=1, delay_reason="x")])
    with pytest.raises(ReportError, match="計算値"):
        open_report(extract_days=Decimal(-1))
