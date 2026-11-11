"""工数の入出力（API-EFF-001〜007）。

工数（人時）は数（float）で返す（A-17・A-21・A-26 と同じ）。入力の工数は数で受け、サービス層が ``Decimal`` に直す
（小数 2 桁まで・負の値の禁止・1 日の合計は RULE-EFF-005 と割当の検査が見る）。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

NOTE_MAX = 4000
REASON_MAX = 4000
ASSIGNMENTS_MAX = 1000
CELLS_MAX = 1000
HOURS_MAX = 1_000_000

# 集計の軸（6.2 の API-EFF-005。``app.services.effort.AXES``）
AxisCode = Literal["wbs", "member", "organization", "week", "month"]


# ---------------------------------------------------------------------------
# 割当（API-EFF-001・002）
# ---------------------------------------------------------------------------


class AssignmentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: int
    planned_hours: float | None = Field(None, le=HOURS_MAX)


class AssignmentsReplace(BaseModel):
    """活動の担当と割当工数を置き換える（API-EFF-001）。載せなかった担当は外す。空の配列は担当を全て外す。"""

    model_config = ConfigDict(extra="forbid")

    assignments: list[AssignmentIn] = Field(max_length=ASSIGNMENTS_MAX)


class AssignmentOut(BaseModel):
    activity_id: int
    activity_name: str
    wbs_code: str
    user_id: int
    display_name: str
    planned_hours: float | None


class ActivityAssignmentsOut(BaseModel):
    """活動の割当。``planned_hours_total`` は割当工数の合計、``difference_hours`` は合計−見積（見積が空なら空。EFF-01）。"""

    activity_id: int
    estimate_hours: float | None
    planned_hours_total: float
    difference_hours: float | None
    assignments: list[AssignmentOut]


class AssignmentList(BaseModel):
    items: list[AssignmentOut]
    total: int
    limit: int
    offset: int


# ---------------------------------------------------------------------------
# 実績工数（API-EFF-003・004）
# ---------------------------------------------------------------------------


class TimeEntryCellIn(BaseModel):
    """1 マス（日×活動）。``hours`` で置き換える（0 は行を消す）。"""

    model_config = ConfigDict(extra="forbid")

    activity_id: int
    work_date: date
    hours: float = Field(le=HOURS_MAX)
    note: str | None = Field(None, max_length=NOTE_MAX)


class TimeEntriesPut(BaseModel):
    """自分の実績工数をまとめて書く（API-EFF-003）。"""

    model_config = ConfigDict(extra="forbid")

    entries: list[TimeEntryCellIn] = Field(min_length=1, max_length=CELLS_MAX)


class TimeEntryOut(BaseModel):
    id: int
    user_id: int
    project_id: int
    activity_id: int
    activity_name: str
    wbs_code: str
    work_date: date
    hours: float
    note: str | None


class DayTotalOut(BaseModel):
    work_date: date
    hours: float


class TimeEntriesWrittenOut(BaseModel):
    """書いた後の、入力のあった日の自分の行と 1 日の合計。``changed`` は作った・変えた・消した行の数。"""

    user_id: int
    changed: int
    entries: list[TimeEntryOut]
    day_totals: list[DayTotalOut]


class TimeEntryList(BaseModel):
    items: list[TimeEntryOut]
    total: int
    limit: int
    offset: int


# ---------------------------------------------------------------------------
# 集計・差異（API-EFF-005〜007）
# ---------------------------------------------------------------------------


class EffortValuesOut(BaseModel):
    """計画（今の見積）・基準（基準に無ければ空）・実績・ETC・EAC（EFF-08 の計画値と予測値を別に返す）。"""

    planned: float
    baseline: float | None
    actual: float
    etc: float
    eac: float


class ReportRefOut(BaseModel):
    id: int
    report_date: date
    status: str


class BaselineRefOut(BaseModel):
    id: int
    version: int
    name: str | None


class SummaryRowOut(BaseModel):
    """集計の 1 つの桶。軸ごとに意味のある列だけを埋める。

    - wbs：``element_id``・``parent_id``・``wbs_code``・``name``・``kind``（WBS の並び）
    - member：``user_id``（空は「未割当」）・``name``（表示名）
    - organization：``bucket_kind``（ORGANIZATION／NO_ORGANIZATION／UNASSIGNED）・``name``（所属）
    - week・month：``bucket_kind``（PERIOD／REMAINING／UNSCHEDULED）・``period_start``（PERIOD の初日）
    """

    bucket_kind: str
    element_id: int | None = None
    parent_id: int | None = None
    wbs_code: str | None = None
    kind: str | None = None
    user_id: int | None = None
    name: str | None = None
    period_start: date | None = None
    values: EffortValuesOut


class EffortSummaryOut(BaseModel):
    axis: AxisCode
    report: ReportRefOut | None
    report_specified: bool
    baseline: BaselineRefOut | None
    total: EffortValuesOut
    rows: list[SummaryRowOut]


class VarianceNoteOut(BaseModel):
    id: int
    element_id: int
    wbs_code: str
    status_report_id: int | None
    reason: str
    corrective_action_id: int | None
    created_by: int
    created_at: datetime


class VarianceRowOut(BaseModel):
    """閾値を超えた要素（EXCEEDED）か基準に無い要素（ADDED）。``rate`` は差異÷基準（追加・基準 0 は空）。"""

    element_id: int
    parent_id: int | None
    wbs_code: str
    name: str
    kind: str
    baseline: float | None
    eac: float
    variance: float | None
    rate: float | None
    status: str
    note: VarianceNoteOut | None


class EffortVarianceOut(BaseModel):
    report: ReportRefOut | None
    report_specified: bool
    baseline: BaselineRefOut
    rate_threshold: float
    hours_threshold: float
    items: list[VarianceRowOut]


class VarianceNoteIn(BaseModel):
    """差異の理由を書く（API-EFF-007）。``status_report_id`` の空は報告日を指定しない分析の理由。"""

    model_config = ConfigDict(extra="forbid")

    status_report_id: int | None = None
    reason: str = Field(min_length=1, max_length=REASON_MAX)
    corrective_action_id: int | None = None
