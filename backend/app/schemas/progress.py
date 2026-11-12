"""進捗の入出力（API-PRG-001〜009）。

値の一覧（算定方式・項目の種類・判定）と長さの上限は ``app.models.sch``・``app.models.prg``（A-19・A-22）に合わせる。
進捗率の算定（RULE-PRG-001）・確定（RULE-PRG-007）・判定（RULE-PRG-004）・遅延（RULE-PRG-006）はサービス層とドメイン層が行う（1.3）。
工数・進捗率・軸の位置（5.4）・暦日の中の割合（7.3）は数（float）で返す（A-17・A-21 と同じ）。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.schedule import AxisPointOut

TITLE_MAX = 200
DELAY_REASON_MAX = 4000
HOURS_MAX = 1_000_000
WEIGHT_MAX = 1_000_000
ITEMS_MAX = 1000
REPORT_IDS_MAX = 50

# 進捗率の算定方式（要求仕様書 7.6 BR-06。``app.models.sch.PROGRESS_METHODS``）
ProgressMethodCode = Literal[
    "DIRECT", "DELIVERABLE_RATIO", "APPROVED_WORK_RATIO", "CHECK_ITEM_RATIO", "WEIGHTED_MILESTONE", "ZERO_HUNDRED",
    "FIFTY_FIFTY",
]
# 進捗の算定項目の種類（4.1 の progress_items.item_type。``app.models.prg.PROGRESS_ITEM_TYPES``）
ProgressItemTypeCode = Literal["DELIVERABLE", "WORK", "CHECK", "MILESTONE"]


# ---------------------------------------------------------------------------
# 報告日（API-PRG-001・002・007）
# ---------------------------------------------------------------------------


class StatusReportCreate(BaseModel):
    """報告日を作る（API-PRG-001）。"""

    model_config = ConfigDict(extra="forbid")

    report_date: date


class StatusReportOut(BaseModel):
    """報告日 1 件。``entry_count`` はその報告日の進捗の入力の行の数。``snapshot_id`` は確定で作ったスナップショット（OPEN は空）。"""

    id: int
    project_id: int
    report_date: date
    status: str
    created_by: int
    created_at: datetime
    finalized_by: int | None
    finalized_at: datetime | None
    entry_count: int
    snapshot_id: int | None


class StatusReportList(BaseModel):
    items: list[StatusReportOut]
    total: int
    limit: int
    offset: int


class FinalizeOut(BaseModel):
    """報告日の確定（API-PRG-007）の結果：確定した報告日と、作ったスナップショットの要約。"""

    report: StatusReportOut
    snapshot_id: int
    report_line: int
    tolerance_days: float
    extract_days: float
    plan_basis: str
    item_count: int


# ---------------------------------------------------------------------------
# 進捗の入力（API-PRG-003）
# ---------------------------------------------------------------------------


class ProgressEntryIn(BaseModel):
    """1 つの報告日の 1 つの活動の進捗（API-PRG-003）。PUT なので全ての項目を置き換える（載せない項目は空になる）。

    ``percent_complete`` は直接入力（DIRECT）の活動だけが持てる（他の方式は RULE-PRG-001 が求める）。
    ``remaining_hours`` の空は自動（BR-10。EFF-06）。
    """

    model_config = ConfigDict(extra="forbid")

    percent_complete: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    actual_start: date | None = None
    actual_finish: date | None = None
    remaining_hours: float | None = Field(default=None, ge=0, le=HOURS_MAX, allow_inf_nan=False)
    delay_reason: str | None = Field(default=None, max_length=DELAY_REASON_MAX)
    forecast_finish: date | None = None


class ProgressEntryOut(BaseModel):
    """保存した進捗。``percent_complete`` は RULE-PRG-001 で求めた進捗率（空は未算定）。``progress_method`` は効いている方式。"""

    status_report_id: int
    activity_id: int
    progress_method: str
    percent_complete: float | None
    actual_start: date | None
    actual_finish: date | None
    remaining_hours: float | None
    remaining_hours_source: Literal["ENTERED", "AUTO"]
    delay_reason: str | None
    forecast_finish: date | None
    entered_by: int
    entered_at: datetime


# ---------------------------------------------------------------------------
# 算定項目・算定方式（API-PRG-004・005）
# ---------------------------------------------------------------------------


class ProgressItemIn(BaseModel):
    """算定項目 1 件。``id`` を載せればその項目を変える（完了の時刻を保つ）、載せなければ新しい項目。"""

    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    item_type: ProgressItemTypeCode
    title: str = Field(min_length=1, max_length=TITLE_MAX)
    weight: float = Field(default=1, gt=0, le=WEIGHT_MAX, allow_inf_nan=False)
    done: bool = False
    approved: bool = False


class ProgressItemsReplace(BaseModel):
    """活動の算定項目を置き換える（API-PRG-004）。載せなかった既存の項目は消す。"""

    model_config = ConfigDict(extra="forbid")

    items: list[ProgressItemIn] = Field(max_length=ITEMS_MAX)


class ProgressItemOut(BaseModel):
    id: int
    item_type: str
    title: str
    weight: float
    done: bool
    approved: bool
    done_at: datetime | None


class ProgressItemsOut(BaseModel):
    """活動の算定項目と、効いている方式・その方式で求めた今の進捗率（4.2 の最新の報告日の入力で。空は未算定）。"""

    activity_id: int
    progress_method: str
    percent_complete: float | None
    items: list[ProgressItemOut]


class ProgressMethodIn(BaseModel):
    """活動の算定方式を変える（API-PRG-005）。null はプロジェクトの既定に従う（4.1）。"""

    model_config = ConfigDict(extra="forbid")

    progress_method: ProgressMethodCode | None


class ProgressMethodOut(BaseModel):
    """``progress_method`` は活動に置いた方式（空はプロジェクトの既定）、``effective_method`` は効いている方式。"""

    activity_id: int
    progress_method: str | None
    effective_method: str
    project_default: str


# ---------------------------------------------------------------------------
# イナズマ線（API-PRG-006・008）
# ---------------------------------------------------------------------------


class LightningRowOut(BaseModel):
    """イナズマ線の 1 行（ガントの行の順）。``point`` は進捗ポイント、``deviation`` はずれ（``point − report_line``）、
    ``judgement`` は 5.6 の判定、``counted_as`` は判定の上で数える値（完了・未着手（予定内）は予定どおり）。

    進捗の入力（``remaining_hours``・``delay_reason``・``forecast_finish``）は、その報告日の入力（確定済みでも入力の行は残る）。
    """

    row_index: int
    activity_id: int | None
    wbs_code: str
    name: str
    is_milestone: bool
    is_critical: bool
    start_date: date | None
    finish_date: date | None
    duration_days: int | None
    progress_method: str | None
    percent_complete: float | None
    actual_start: date | None
    actual_finish: date | None
    remaining_hours: float | None
    delay_reason: str | None
    forecast_finish: date | None
    point: AxisPointOut
    deviation: float
    judgement: str
    counted_as: str
    display_date: date


class LightningLineOut(BaseModel):
    """報告日のイナズマ線（API-PRG-006）。``source`` は CALCULATED（OPEN：今の計画とこの報告日の入力から計算）か
    SNAPSHOT（FINALIZED：スナップショットの写しだけから。BR-13）。``report_line`` は報告日線（報告日の終業の位置。5.4）。"""

    report: StatusReportOut
    source: Literal["CALCULATED", "SNAPSHOT"]
    project_start: date
    report_line: AxisPointOut
    tolerance_days: float
    rows: list[LightningRowOut]
    restricted: bool


class OverlaidVertexOut(BaseModel):
    """重ねる線の頂点。``row_index`` は今のガントの行の番号（RULE-SCH-005 の順）。"""

    row_index: int
    activity_id: int | None
    point: AxisPointOut
    deviation: float
    judgement: str
    display_date: date


class OverlaidLineOut(BaseModel):
    """重ねる 1 本の線（PRG-18）。``style_index`` は線の種類（報告日の古い順に 0, 1, …）。
    ``missing_activity_ids`` は今のガントに無い活動（消した活動の写しは null）。"""

    style_index: int
    report_id: int
    report_date: date
    report_line: AxisPointOut
    tolerance_days: float
    vertices: list[OverlaidVertexOut]
    missing_activity_ids: list[int | None]


class LightningLinesOut(BaseModel):
    project_start: date
    lines: list[OverlaidLineOut]
    restricted: bool


# ---------------------------------------------------------------------------
# 遅延（API-PRG-009）
# ---------------------------------------------------------------------------


class SlipOut(BaseModel):
    """活動（マイルストーン）の終わりのずれ。``before``・``after`` は軸の位置、日付は終了日（マイルストーンの日付）。"""

    activity_id: int
    name: str
    is_milestone: bool
    before: int
    after: int
    before_date: date
    after_date: date
    slip_days: int


class FinishSlipOut(BaseModel):
    before_date: date | None
    after_date: date | None
    slip_days: int


class DelayOut(BaseModel):
    """抽出した遅延活動 1 件とその個別の影響（5.7 の 2・3）。``forecast_finish`` は入力した完了見込み日（PRG-03）、
    ``expected_finish`` は見込みの終了（位置と日付）。``project_finish`` は委託先には返さない（null）。"""

    activity_id: int
    wbs_code: str
    name: str
    deviation: float
    percent_complete: float | None
    delay_reason: str | None
    forecast_finish: date | None
    expected_finish: int
    expected_finish_date: date
    fixed_finish: SlipOut
    is_critical: bool
    transitive_successor_ids: list[int]
    successor_slips: list[SlipOut]
    milestone_slips: list[SlipOut]
    project_finish: FinishSlipOut | None


class DelaysOut(BaseModel):
    """遅延活動と影響（API-PRG-009）。``delays`` はずれの小さい順（遅れの大きい順）。``combined_*`` は全ての見込みを同時に当てた結果。"""

    report: StatusReportOut
    source: Literal["CALCULATED", "SNAPSHOT"]
    report_line: int
    extract_days: float
    delays: list[DelayOut]
    combined_milestone_slips: list[SlipOut]
    combined_project_finish: FinishSlipOut | None
    restricted: bool

