"""報告日の確定とスナップショット（設計書 5.1 の RULE-PRG-007、5.10。要求仕様書 7.13 BR-13、PRG-17・PRG-01）。

報告日の状態（4.1 の ``status_reports.status``。5.10）::

    OPEN → FINALIZED    確定でスナップショットを作る。戻さない

- 確定の前提：報告日が ``OPEN`` であること。``FINALIZED`` の報告日をもう一度確定しようとすると ``INVALID_STATE``（409）
- 確定で、その時点の全活動の計画日付・期間・進捗率・実績日付・実績工数・ETC と、進捗ポイント・ずれ・判定の写しを作る（BR-13）。
  ポイント・ずれ・判定はイナズマ線の組み立て（RULE-PRG-005）と同じ計算で求め、行の順もそのまま記録する
- 確定した報告日の入力値は変えない（``ensure_report_open``。直すときは新しい報告日を作る。BR-13）
- 過去の線はこの写しだけから描く（``app.domain.lightning.restore_lightning_line``。今の計画・進捗から計算し直さない）

純粋に保つ（1.3）。DB・HTTP・時刻・乱数に触れない。確定した人・時刻はサービス層が記録する。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from fractions import Fraction
from typing import Final

from app.domain.calendar import WorkCalendar
from app.domain.lightning import (
    DEFAULT_TOLERANCE_DAYS,
    Judgement,
    LightningActivity,
    LightningError,
    RecordedPoint,
    build_lightning_line,
)
from app.domain.progress import round_percent

# 計画の基準（4.1 の progress_snapshots.plan_basis。R1 は最新計画だけ。OQ-03）
PLAN_BASIS_LATEST: Final = "LATEST"
PLAN_BASES: Final = (PLAN_BASIS_LATEST,)
# 遅延の抽出の閾値の既定（BR-09。projects.settings.delay_extract_days の既定）
DEFAULT_EXTRACT_DAYS: Final = Decimal(2)


class StatusReportStatus(StrEnum):
    """報告日の状態（4.1 の ``status_reports.status``）。"""

    OPEN = "OPEN"
    FINALIZED = "FINALIZED"


class SnapshotErrorCode(StrEnum):
    """確定・入力の拒否の理由（6.1 のエラーのコード）。"""

    INVALID_STATE = "INVALID_STATE"  # 409：確定済みの報告日を確定する・確定済みの報告日へ入力する
    VALIDATION_ERROR = "VALIDATION_ERROR"  # 400：写しの入力が不正


class SnapshotError(ValueError):
    """確定・入力の拒否。``code`` は拒否の理由。"""

    def __init__(self, code: SnapshotErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SnapshotActivity:
    """確定の時点の活動の値（4.1 の ``activities`` の最新計画・WP の WBS ID・報告日の進捗・実績工数・ETC）。

    ``percent_complete`` は RULE-PRG-001 の丸める前の進捗率（``Fraction`` か ``Decimal``。``None`` は未算定）。
    ポイント・ずれ・判定はこの値から求め、写しの ``percent_complete`` には 4.1 の精度（小数 2 桁）へ丸めた値を置く
    （設計書 1.1 の 5.6・CR-009）。``etc_hours`` は RULE-EFF-001 の ETC（サービス層が求めて渡す。無ければ ``None``）。
    """

    activity_id: int
    wbs_code: str
    name: str
    start_date: date
    finish_date: date
    duration_days: int
    is_milestone: bool
    is_critical: bool
    percent_complete: Decimal | Fraction | None
    actual_start: date | None = None
    actual_finish: date | None = None
    actual_hours: Decimal | None = None
    etc_hours: Decimal | None = None


@dataclass(frozen=True)
class SnapshotItem:
    """スナップショットの活動（4.1 の ``progress_snapshot_items`` の 1 行）。``row_order`` はガントの行の順（0 から）。"""

    activity_id: int
    row_order: int
    wbs_code: str
    name: str
    start_date: date
    finish_date: date
    duration_days: int
    is_milestone: bool
    is_critical: bool
    percent_complete: Decimal | None
    actual_start: date | None
    actual_finish: date | None
    actual_hours: Decimal | None
    etc_hours: Decimal | None
    point: Decimal
    deviation: Decimal
    judgement: Judgement

    def recorded_point(self) -> RecordedPoint:
        """過去の線を描くための記録（RULE-PRG-005 の ``restore_lightning_line`` へ渡す）。"""
        return RecordedPoint(
            activity_id=self.activity_id,
            row_order=self.row_order,
            point=self.point,
            deviation=self.deviation,
            judgement=self.judgement,
        )


@dataclass(frozen=True)
class Snapshot:
    """スナップショット（4.1 の ``progress_snapshots`` の 1 行と、その活動の全て）。"""

    report_date: date
    report_line: int
    tolerance_days: Decimal
    extract_days: Decimal
    plan_basis: str
    items: tuple[SnapshotItem, ...]


@dataclass(frozen=True)
class Finalization:
    """確定の結果：報告日の次の状態（``FINALIZED``）と作ったスナップショット。"""

    status: StatusReportStatus
    snapshot: Snapshot


def _status(value: StatusReportStatus | str) -> StatusReportStatus:
    try:
        return StatusReportStatus(value)
    except ValueError as exc:
        raise SnapshotError(SnapshotErrorCode.VALIDATION_ERROR, f"報告日の状態が不正: {value!r}") from exc


def ensure_report_open(status: StatusReportStatus | str) -> None:
    """報告日へ入力できるか（BR-13）。確定済みなら ``INVALID_STATE``（直すときは新しい報告日を作る）。"""
    if _status(status) is not StatusReportStatus.OPEN:
        raise SnapshotError(
            SnapshotErrorCode.INVALID_STATE,
            "確定した報告日の入力値は変えられない。直すときは新しい報告日を作る（BR-13）",
        )


# RULE-PRG-007 報告日の確定とスナップショット
def finalize_status_report(
    *,
    status: StatusReportStatus | str,
    report_date: date,
    activities: Sequence[SnapshotActivity],
    calendar: WorkCalendar,
    project_start: date,
    tolerance_days: Decimal | int | str = DEFAULT_TOLERANCE_DAYS,
    extract_days: Decimal | int | str = DEFAULT_EXTRACT_DAYS,
    plan_basis: str = PLAN_BASIS_LATEST,
) -> Finalization:
    """報告日を確定し、スナップショットを作る（5.10・BR-13・PRG-17）。

    - ``status``：今の報告日の状態。``OPEN`` でなければ ``INVALID_STATE``（確定は戻さない・二度しない）
    - ``activities``：プロジェクトの全活動をガントの行の順（RULE-SCH-005）に並べたもの。同じ活動が 2 つあれば拒む
    - ポイント・ずれ・判定と報告日線はイナズマ線の組み立て（RULE-PRG-005）で求め、その値をそのまま写す
    - 許容幅・抽出の閾値は確定の時点の値（``projects.settings``）を写す。``plan_basis`` は R1 では ``LATEST`` だけ
    """
    if _status(status) is not StatusReportStatus.OPEN:
        raise SnapshotError(SnapshotErrorCode.INVALID_STATE, "確定済みの報告日は確定できない（OPEN → FINALIZED。戻さない）")
    if plan_basis not in PLAN_BASES:
        raise SnapshotError(SnapshotErrorCode.VALIDATION_ERROR, f"計画の基準が不正（R1 は LATEST だけ。OQ-03）: {plan_basis!r}")
    try:
        extract = Decimal(str(extract_days)) if not isinstance(extract_days, Decimal) else extract_days
    except ArithmeticError as exc:
        raise SnapshotError(SnapshotErrorCode.VALIDATION_ERROR, f"抽出の閾値が不正: {extract_days!r}") from exc
    if not extract.is_finite() or extract < 0:
        raise SnapshotError(SnapshotErrorCode.VALIDATION_ERROR, f"抽出の閾値（稼働日）は 0 以上: {extract_days!r}")

    try:
        line = build_lightning_line(
            [
                LightningActivity(
                    activity_id=a.activity_id,
                    start_date=a.start_date,
                    finish_date=a.finish_date,
                    duration_days=a.duration_days,
                    is_milestone=a.is_milestone,
                    percent_complete=a.percent_complete,
                    actual_start=a.actual_start,
                    actual_finish=a.actual_finish,
                )
                for a in activities
            ],
            calendar=calendar,
            project_start=project_start,
            report_date=report_date,
            tolerance_days=tolerance_days,
        )
    except LightningError as exc:
        raise SnapshotError(SnapshotErrorCode.VALIDATION_ERROR, f"スナップショットの活動が不正: {exc}") from exc

    items = tuple(
        SnapshotItem(
            activity_id=a.activity_id,
            row_order=vertex.row_index,
            wbs_code=a.wbs_code,
            name=a.name,
            start_date=a.start_date,
            finish_date=a.finish_date,
            duration_days=a.duration_days,
            is_milestone=a.is_milestone,
            is_critical=a.is_critical,
            percent_complete=round_percent(a.percent_complete),
            actual_start=a.actual_start,
            actual_finish=a.actual_finish,
            actual_hours=a.actual_hours,
            etc_hours=a.etc_hours,
            point=vertex.point,
            deviation=vertex.deviation,
            judgement=vertex.judgement,
        )
        for a, vertex in zip(activities, line.vertices, strict=True)
    )
    return Finalization(
        status=StatusReportStatus.FINALIZED,
        snapshot=Snapshot(
            report_date=report_date,
            report_line=line.report_line,
            tolerance_days=line.tolerance_days,
            extract_days=extract,
            plan_basis=plan_basis,
            items=items,
        ),
    )
