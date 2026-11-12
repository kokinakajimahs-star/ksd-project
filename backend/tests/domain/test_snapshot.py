"""報告日の確定とスナップショット（app.domain.snapshot）の試験。

設計書 5.1 の RULE-PRG-007、5.6・5.10、要求仕様書 7.13 BR-13・PRG-17・PRG-01。
全ての曜日を稼働日、原点 2026-12-01、報告日 2026-12-10（L＝10）。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from app.domain.calendar import WorkCalendar
from app.domain.lightning import Judgement, LightningActivity, LightningLine, build_lightning_line, restore_lightning_line
from app.domain.snapshot import (
    PLAN_BASES,
    PLAN_BASIS_LATEST,
    SnapshotActivity,
    SnapshotError,
    SnapshotErrorCode,
    StatusReportStatus,
    ensure_report_open,
    finalize_status_report,
)
from app.models.prg import PLAN_BASES as DB_PLAN_BASES
from app.models.prg import STATUS_REPORT_STATUSES

ALL_DAYS = WorkCalendar("1111111")
ORIGIN = date(2026, 12, 1)
REPORT = date(2026, 12, 10)


def d(day: int) -> date:
    return date(2026, 12, day)


# 5.6 の試験の基準の 4 行を、写しに要る列を足して並べる（ガントの行の順）
ROWS = [
    SnapshotActivity(1, "1.1", "資料作成", d(1), d(21), 21, False, True, Decimal(25), d(1), None, Decimal(40), Decimal(120)),
    SnapshotActivity(2, "1.2", "関係者調整", d(1), d(21), 21, False, False, Decimal(60), d(1), None, Decimal(70), Decimal(48)),
    SnapshotActivity(3, "1.3", "公開準備", d(12), d(18), 7, False, False, Decimal(0)),
    SnapshotActivity(4, "2.1", "環境整備", d(1), d(8), 8, False, False, Decimal(100), d(1), d(8), Decimal(64), Decimal(0)),
]


def live_line(rows: list[SnapshotActivity]) -> LightningLine:
    """今の値からイナズマ線を計算する（RULE-PRG-005。確定前の線）。"""
    return build_lightning_line(
        [
            LightningActivity(
                a.activity_id, a.start_date, a.finish_date, a.duration_days, a.is_milestone, a.percent_complete,
                a.actual_start, a.actual_finish,
            )
            for a in rows
        ],
        calendar=ALL_DAYS,
        project_start=ORIGIN,
        report_date=REPORT,
        tolerance_days=Decimal("0.5"),
    )


def finalize(**kwargs: object):  # type: ignore[no-untyped-def]
    params: dict[str, object] = {
        "status": StatusReportStatus.OPEN,
        "report_date": REPORT,
        "activities": ROWS,
        "calendar": ALL_DAYS,
        "project_start": ORIGIN,
        "tolerance_days": Decimal("0.5"),
        "extract_days": Decimal(2),
    }
    params.update(kwargs)
    return finalize_status_report(**params)  # type: ignore[arg-type]


def test_status_and_plan_basis_values_match_table() -> None:
    """RULE-PRG-007：報告日の状態は OPEN/FINALIZED、計画の基準は R1 では LATEST だけ（4.1 の DB の検査の値と同じ）。"""
    assert tuple(s.value for s in StatusReportStatus) == STATUS_REPORT_STATUSES
    assert PLAN_BASES == DB_PLAN_BASES == (PLAN_BASIS_LATEST,)


def test_finalize_open_report_creates_snapshot_of_all_activities() -> None:
    """RULE-PRG-007：OPEN の報告日を確定すると FINALIZED になり、全活動の値とポイント・ずれ・判定の写しができる（BR-13・PRG-17）。

    ポイント・判定は 5.6 の試験の基準と同じ値（イナズマ線の組み立てと同じ計算）。
    """
    result = finalize()
    assert result.status is StatusReportStatus.FINALIZED
    snap = result.snapshot
    assert (snap.report_date, snap.report_line, snap.tolerance_days, snap.extract_days, snap.plan_basis) == (
        REPORT,
        10,
        Decimal("0.5"),
        Decimal(2),
        "LATEST",
    )
    assert [(i.row_order, i.activity_id, i.point, i.deviation, i.judgement) for i in snap.items] == [
        (0, 1, Decimal("5.25"), Decimal("-4.75"), Judgement.BEHIND),
        (1, 2, Decimal("12.6"), Decimal("2.6"), Judgement.AHEAD),
        (2, 3, Decimal(10), Decimal(0), Judgement.NOT_STARTED),
        (3, 4, Decimal(10), Decimal(0), Judgement.COMPLETED),
    ]
    first = snap.items[0]
    assert (first.wbs_code, first.name, first.start_date, first.finish_date, first.duration_days) == (
        "1.1",
        "資料作成",
        d(1),
        d(21),
        21,
    )
    assert (first.is_critical, first.percent_complete, first.actual_start, first.actual_hours, first.etc_hours) == (
        True,
        Decimal(25),
        d(1),
        Decimal(40),
        Decimal(120),
    )
    assert snap.items[3].actual_finish == d(8)


def test_snapshot_equals_live_line() -> None:
    """RULE-PRG-007：写しのポイント・ずれ・判定と報告日線は、同じ時点のイナズマ線（RULE-PRG-005）と一致する。"""
    snap = finalize().snapshot
    live = live_line(ROWS)
    assert snap.report_line == live.report_line
    assert [(i.point, i.deviation, i.judgement) for i in snap.items] == [
        (v.point, v.deviation, v.judgement) for v in live.vertices
    ]


def test_past_line_does_not_change_after_progress_or_plan_edits() -> None:
    """RULE-PRG-007：確定の後に進捗や計画を直しても、確定済みの線（写しから描く線）は変わらない（PRG-17 の受入基準・BR-13）。"""
    snap = finalize().snapshot
    restored = restore_lightning_line(
        report_date=snap.report_date,
        report_line=snap.report_line,
        tolerance_days=snap.tolerance_days,
        points=[i.recorded_point() for i in snap.items],
        calendar=ALL_DAYS,
        project_start=ORIGIN,
    )
    # 後から資料作成の進捗を 90% に、関係者調整の計画を 12/05 開始へ直すと、今の値から計算する線は変わるが、
    # 写しから描く線はそのまま
    edited = [replace(ROWS[0], percent_complete=Decimal(90)), replace(ROWS[1], start_date=d(5)), *ROWS[2:]]
    changed = live_line(edited)
    assert [v.point for v in changed.vertices][:2] == [Decimal("18.9"), Decimal("16.6")]
    again = restore_lightning_line(
        report_date=snap.report_date,
        report_line=snap.report_line,
        tolerance_days=snap.tolerance_days,
        points=[i.recorded_point() for i in snap.items],
        calendar=ALL_DAYS,
        project_start=ORIGIN,
    )
    assert restored == again
    assert [(v.activity_id, v.point, v.display_date, v.judgement) for v in restored.vertices] == [
        (1, Decimal("5.25"), d(6), Judgement.BEHIND),
        (2, Decimal("12.6"), d(13), Judgement.AHEAD),
        (3, Decimal(10), d(10), Judgement.NOT_STARTED),
        (4, Decimal(10), d(10), Judgement.COMPLETED),
    ]


def test_finalized_report_cannot_be_finalized_again() -> None:
    """RULE-PRG-007：確定の前提は OPEN。FINALIZED の報告日の確定は INVALID_STATE（戻さない・二度しない。5.10）。"""
    with pytest.raises(SnapshotError) as exc:
        finalize(status=StatusReportStatus.FINALIZED)
    assert exc.value.code is SnapshotErrorCode.INVALID_STATE
    with pytest.raises(SnapshotError) as exc:
        finalize(status="FINALIZED")
    assert exc.value.code is SnapshotErrorCode.INVALID_STATE


def test_input_to_finalized_report_is_rejected() -> None:
    """RULE-PRG-007：確定後の入力を拒む（BR-13。直すときは新しい報告日を作る）。OPEN の報告日へは入力できる。"""
    ensure_report_open(StatusReportStatus.OPEN)
    ensure_report_open("OPEN")
    with pytest.raises(SnapshotError) as exc:
        ensure_report_open(StatusReportStatus.FINALIZED)
    assert exc.value.code is SnapshotErrorCode.INVALID_STATE


@pytest.mark.parametrize(
    "kwargs",
    [
        {"status": "CLOSED"},
        {"plan_basis": "BASELINE"},  # R1 は最新計画だけ（OQ-03）
        {"extract_days": Decimal(-1)},
        {"tolerance_days": Decimal("-0.5")},
        {"activities": [ROWS[0], ROWS[0]]},  # 同じ活動が 2 行
    ],
)
def test_invalid_finalize_inputs_rejected(kwargs: dict[str, object]) -> None:
    """RULE-PRG-007：未定義の状態・計画の基準・負の閾値・重複した活動は VALIDATION_ERROR で拒む。"""
    with pytest.raises(SnapshotError) as exc:
        finalize(**kwargs)
    assert exc.value.code is SnapshotErrorCode.VALIDATION_ERROR


def test_finalize_with_no_activities() -> None:
    """RULE-PRG-007：活動が 0 件のプロジェクトでも確定でき、写しの活動は 0 行。"""
    snap = finalize(activities=[]).snapshot
    assert snap.items == () and snap.report_line == 10
