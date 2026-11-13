"""レポート・共有の入出力（API-RPT-001〜004）。

会議レポートの値は RULE-RPT-001（``app.domain.report``）の結果、共有リンクの検証は RULE-AUTH-002（``app.domain.authz``）が行う。
ガントは API-SCH-010 と同じ形（``GanttOut``）、イナズマ線の頂点は API-PRG-008 と同じ形（``OverlaidVertexOut``）で返す。
工数・進捗率・ずれ・軸の位置は数（float）で返す（A-17・A-21 と同じ）。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict

from app.schemas.progress import OverlaidVertexOut
from app.schemas.schedule import AxisPointOut, GanttOut

# 共有リンクで見せる範囲（4.1 の share_links.scope。``app.models.cmn.SHARE_LINK_SCOPES``）
ShareScopeCode = Literal["GANTT", "REPORT"]


# ---------------------------------------------------------------------------
# API-RPT-001 定例会議用レポートのデータ（7.4）
# ---------------------------------------------------------------------------


class ReportWindowOut(BaseModel):
    """上段のガントの表示の範囲（報告日の前後 4 週。暦日。両端を含む）。"""

    start: date
    end: date


class ReportLightningOut(BaseModel):
    """上段のイナズマ線（報告日の値。今のガントの行へ置き直したもの）。``missing_activity_ids`` は今のガントに無い活動（消した活動は null）。"""

    report_date: date
    report_line: AxisPointOut
    tolerance_days: float
    vertices: list[OverlaidVertexOut]
    missing_activity_ids: list[int | None]


class ReportDelayOut(BaseModel):
    """下段左：遅延一覧の 1 行（ずれの小さい順＝遅れの大きい順）。``forecast_entered`` は完了見込み日が入力の値か。"""

    activity_id: int
    wbs_code: str
    name: str
    deviation: float
    percent_complete: float | None
    planned_finish: date
    forecast_finish: date
    forecast_entered: bool
    delay_reason: str | None
    is_critical: bool


class ReportMilestoneOut(BaseModel):
    """下段中：マイルストーンの状況の 1 行。``slip_days`` は見込み日−予定日（稼働日）。"""

    activity_id: int | None
    wbs_code: str
    name: str
    planned_date: date
    forecast_date: date
    slip_days: int


class ReportKpiOut(BaseModel):
    """下段右：主要 KPI。求められない値は null。"""

    overall_progress: float | None
    behind_count: int
    eac_hours: float | None
    baseline_hours: float | None
    eac_variance_hours: float | None
    min_critical_float: int | None
    overdue_action_count: int


class MeetingReportOut(BaseModel):
    """定例会議レポートのデータ（RULE-RPT-001・7.4）。

    ``source`` は進捗の値の源：``SNAPSHOT``（確定済みの報告日の写し）か ``CALCULATED``（確定前の計算値）。計画の値（ガントの
    バー、マイルストーンの予定日・見込み日・ずれ）はどちらも表示した時点の最新計画（7.4。設計書 1.2・CR-013）。
    ``kpi`` は委託先だけで読む利用者（``restricted``）には返さない（プロジェクト全体の値のため）。
    """

    report_id: int
    report_date: date
    status: str
    source: str
    project_start: date
    window: ReportWindowOut
    gantt: GanttOut
    lightning: ReportLightningOut
    delays: list[ReportDelayOut]
    milestones: list[ReportMilestoneOut]
    kpi: ReportKpiOut | None
    restricted: bool


# ---------------------------------------------------------------------------
# API-RPT-002・003 共有リンク
# ---------------------------------------------------------------------------


class ShareLinkCreate(BaseModel):
    """共有リンクを作る。``expires_at`` は時差つきの時刻で、今より後（有効期限付き。RPT-03）。"""

    model_config = ConfigDict(extra="forbid")

    scope: ShareScopeCode
    expires_at: AwareDatetime


class ShareLinkOut(BaseModel):
    """共有リンク 1 件（トークンは含まない）。"""

    id: int
    project_id: int
    scope: str
    expires_at: datetime
    revoked_at: datetime | None
    created_by: int
    created_at: datetime


class ShareLinkList(BaseModel):
    """共有リンクの一覧（API-RPT-005。トークンは返さない。失効・期限切れのリンクも含む。設計書 1.1・CR-004）。"""

    project_id: int
    items: list[ShareLinkOut]
    total: int
    limit: int
    offset: int


class ShareLinkCreated(ShareLinkOut):
    """作った共有リンク。``token`` は**この応答でだけ**返す（DB には SHA-256 のハッシュだけを保存する。4.2）。"""

    token: str


# ---------------------------------------------------------------------------
# API-RPT-004 共有リンクで閲覧する
# ---------------------------------------------------------------------------


class SharedProjectOut(BaseModel):
    id: int
    code: str
    name: str


class SharedViewOut(BaseModel):
    """共有リンクで見せるもの（読むだけ）。

    ``scope`` が ``GANTT`` なら ``gantt``（最新の進捗・最新の基準）、``REPORT`` なら ``meeting_report``（4.2 の最新の報告日の
    会議レポート。報告日が 1 つも無ければ null）。
    利用者の表示名・ログイン ID・利用者の ID は含めない。ガントの行の ``assignees`` は担当者の所属（project_members の
    「組織／チーム」。空なら「所属なし」。1 つの行で同じ所属は 1 つ）で、API-SCH-010 の表示名とは違う（CR-019）。
    """

    scope: str
    expires_at: datetime
    project: SharedProjectOut
    gantt: GanttOut | None
    meeting_report: MeetingReportOut | None
