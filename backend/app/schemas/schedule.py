"""スケジュールの入出力（API-SCH-001〜013）。

長さの上限は ``activities``・``schedule_baselines``（docs/decisions/ASSUMPTIONS.md A-19）に合わせる。
日付の組み合わせ（SCH-03）・マイルストーン（BR-04）・循環（RULE-SCH-001）の検査はサービス層が行う（1.3）。
工数は数（float）で返す（A-17 と同じ）。軸の位置（5.4）と暦日の中の割合（7.3）も数で返す。
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

NAME_MAX = 200
DURATION_MAX = 100000


class ActivityCreate(BaseModel):
    """活動を足す（API-SCH-002）。開始日・期間・終了日のうち 2 つ（期間だけでもよい。開始はプロジェクトの開始日と先行から）。"""

    model_config = ConfigDict(extra="forbid")

    work_package_id: int
    name: str = Field(min_length=1, max_length=NAME_MAX)
    requested_start: date | None = None
    requested_finish: date | None = None
    duration_days: int | None = Field(default=None, ge=0, le=DURATION_MAX)
    is_milestone: bool | None = None


class ActivityUpdate(BaseModel):
    """活動を変える（API-SCH-003）。載せた項目だけを変える。``requested_start`` の null は希望開始日を空にする。

    ``work_package_id``・``position`` は置き場の WP と WP の中の位置（0 から）、``planned_finish`` は今の予定開始日から数えた
    終了日（希望開始日は変えずに期間を決める）。CR-024
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=NAME_MAX)
    requested_start: date | None = None
    requested_finish: date | None = None
    duration_days: int | None = Field(default=None, ge=0, le=DURATION_MAX)
    is_milestone: bool | None = None
    work_package_id: int | None = None
    position: int | None = Field(default=None, ge=0)
    planned_finish: date | None = None


class ActivityChangeIn(BaseModel):
    """一括更新の 1 件（API-SCH-004。SCH-02 の日付・先行・担当と名称）。載せた項目だけを変える。先行・担当は全体の置き換え。"""

    model_config = ConfigDict(extra="forbid")

    activity_id: int
    name: str = Field(default="", min_length=1, max_length=NAME_MAX)
    requested_start: date | None = None
    duration_days: int = Field(default=0, ge=0, le=DURATION_MAX)
    predecessor_ids: list[int] = Field(default_factory=list)
    assignee_user_ids: list[int] = Field(default_factory=list)


class ActivityBulkUpdate(BaseModel):
    """活動をまとめて変える（API-SCH-004）。全件か無しか。"""

    model_config = ConfigDict(extra="forbid")

    changes: list[ActivityChangeIn] = Field(min_length=1, max_length=1000)


class AssigneeOut(BaseModel):
    user_id: int
    display_name: str
    planned_hours: float | None


class ActivityOut(BaseModel):
    """活動 1 件（日程・依存・担当付き）。``start_date`` 以降は日程計算の結果（4.2）。``wbs_code`` は活動の番号（置き場の WP の WBS ID ＋ WP の中の順番。5.2・CR-028）。"""

    id: int
    project_id: int
    work_package_id: int
    wbs_code: str
    work_package_name: str
    name: str
    sort_order: int
    requested_start: date | None
    duration_days: int
    is_milestone: bool
    estimate_hours: float | None
    progress_method: str | None
    start_date: date | None
    finish_date: date | None
    early_start: date | None
    early_finish: date | None
    late_start: date | None
    late_finish: date | None
    total_float: int | None
    is_critical: bool
    predecessor_ids: list[int]
    successor_ids: list[int]
    assignees: list[AssigneeOut]


class DependencyCreate(BaseModel):
    """依存を足す（API-SCH-006）。R1 は FS・ラグ 0 だけ（それ以外は 422）。"""

    model_config = ConfigDict(extra="forbid")

    predecessor_id: int
    successor_id: int
    dep_type: str = Field(default="FS", max_length=8)
    lag_days: int = 0


class DependencyOut(BaseModel):
    id: int
    predecessor_id: int
    successor_id: int
    dep_type: str
    lag_days: int


class ActivityList(BaseModel):
    items: list[ActivityOut]
    total: int
    limit: int
    offset: int
    dependencies: list[DependencyOut]
    restricted: bool


class BulkUpdateOut(BaseModel):
    items: list[ActivityOut]


class ScheduledActivityOut(BaseModel):
    """日程計算の結果の 1 件（日付は 5.5 の結果の日付、``total_float`` は稼働日）。"""

    activity_id: int
    name: str
    wbs_code: str
    is_milestone: bool
    start_date: date
    finish_date: date
    early_start: date
    early_finish: date
    late_start: date
    late_finish: date
    total_float: int
    is_critical: bool


class ScheduleOut(BaseModel):
    """日程計算（API-SCH-008）とクリティカルパス（API-SCH-009）の結果。``critical_path`` はトポロジカル順の活動の ID。"""

    project_finish_date: date | None
    critical_path: list[int]
    min_total_float: int | None
    activities: list[ScheduledActivityOut]
    restricted: bool


class CalendarPointOut(BaseModel):
    date: date
    fraction: float


class AxisPointOut(BaseModel):
    """稼働日の軸の上の位置（5.4）と、その暦日上の座標（7.3）。"""

    position: float
    calendar: CalendarPointOut


class GanttBarOut(BaseModel):
    kind: str
    is_milestone: bool
    start: AxisPointOut
    end: AxisPointOut
    start_date: date
    end_date: date
    open_ended: bool


class GanttRowOut(BaseModel):
    row_index: int
    activity_id: int
    name: str
    work_package_id: int
    wbs_code: str
    work_package_name: str
    depth: int
    is_milestone: bool
    is_critical: bool
    total_float: int | None
    planned: GanttBarOut | None
    actual: GanttBarOut | None
    baseline: GanttBarOut | None
    assignees: list[str]
    percent_complete: float | None


class GanttLinkOut(BaseModel):
    predecessor_id: int
    successor_id: int
    from_row: int
    to_row: int
    start: AxisPointOut | None
    end: AxisPointOut | None


class BaselineRef(BaseModel):
    id: int
    version: int
    name: str


class GanttOut(BaseModel):
    """ガントの描画データ（RULE-SCH-005）。行の順はサーバの順（7.3）。"""

    project_start: date
    report_date: date | None
    baseline: BaselineRef | None
    range_start: date | None
    range_end: date | None
    non_working_days: list[date]
    rows: list[GanttRowOut]
    links: list[GanttLinkOut]
    restricted: bool


class BaselineCreate(BaseModel):
    """スケジュールベースラインを保存する（API-SCH-011）。``name`` は比較の対象を選ぶための版の名前（SCH-17）。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=NAME_MAX)


class BaselineOut(BaseModel):
    id: int
    project_id: int
    version: int
    name: str
    reason: str
    change_request_id: int | None
    created_by: int
    created_at: datetime
    item_count: int


class BaselineList(BaseModel):
    items: list[BaselineOut]
    total: int
    limit: int
    offset: int


class VarianceRowOut(BaseModel):
    """1 つの活動の差異（差は「最新 − 基準」。日付は稼働日、見積は人時）。``status`` は MATCHED・ADDED・DELETED。"""

    status: str
    activity_id: int | None
    wbs_code: str
    name: str
    baseline_start: date | None
    baseline_finish: date | None
    current_start: date | None
    current_finish: date | None
    start_variance_days: int | None
    finish_variance_days: int | None
    baseline_estimate_hours: float | None
    current_estimate_hours: float | None
    estimate_variance_hours: float | None


class VarianceOut(BaseModel):
    baseline: BaselineRef
    rows: list[VarianceRowOut]
    restricted: bool
