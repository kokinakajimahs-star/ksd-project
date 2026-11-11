"""プロジェクト基盤（PRJ）の入出力（API-PRJ-001〜011）。

長さの上限は ``projects``・``scope_items``・``calendar_holidays``・``project_members`` の列（docs/decisions/ASSUMPTIONS.md A-11）に合わせる。
ロールのコード（3 章）の検査はサービス層が行う（スキーマ層はドメインに依存しない。1.3）。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PROJECT_CODE_MAX = 50
NAME_MAX = 200
TITLE_MAX = 200
HOLIDAY_NAME_MAX = 200
ORGANIZATION_MAX = 200
TEAM_MAX = 200

# 進捗率の算定方式のコード（要求仕様書 7.6 BR-06。``projects.settings.progress_method_default``）
ProgressMethod = Literal[
    "DIRECT",
    "DELIVERABLE_RATIO",
    "APPROVED_WORK_RATIO",
    "CHECK_ITEM_RATIO",
    "WEIGHTED_MILESTONE",
    "ZERO_HUNDRED",
    "FIFTY_FIFTY",
]
ScopeItemKind = Literal["IN", "OUT"]


# ---------------------------------------------------------------------------
# プロジェクト（API-PRJ-001〜004）
# ---------------------------------------------------------------------------


class ProjectSettingsOut(BaseModel):
    """プロジェクトの設定（設計書 4.2 の ``projects.settings``）。"""

    progress_method_default: ProgressMethod
    delay_tolerance_days: float
    delay_extract_days: float
    variance_rate: float
    variance_hours: float
    hundred_percent_tolerance: float


class ProjectSettingsPatch(BaseModel):
    """設定のうち載せた項目だけを変える（API-PRJ-004）。値は 0 以上の有限の数（null は書けない）。

    - ``delay_tolerance_days``：遅れ・前倒しの判定の許容幅（稼働日。BR-09）
    - ``delay_extract_days``：大きく遅れた活動の抽出の閾値（稼働日。BR-09）
    - ``variance_rate``・``variance_hours``：差異の判定の閾値（割合・人時。BR-11）
    - ``hundred_percent_tolerance``：100% ルールの合計の差異の許容幅（割合。BR-03）
    """

    model_config = ConfigDict(extra="forbid")

    progress_method_default: ProgressMethod | None = None
    delay_tolerance_days: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    delay_extract_days: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    variance_rate: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    variance_hours: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    hundred_percent_tolerance: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class ProjectCreate(BaseModel):
    """プロジェクトを作る入力（API-PRJ-001）。必須は ``code``・名称・開始日・責任者（PRJ-01。``code`` は OQ-11）。"""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=PROJECT_CODE_MAX)
    name: str = Field(min_length=1, max_length=NAME_MAX)
    purpose: str | None = None
    requirements: str | None = None
    key_deliverables: str | None = None
    start_date: date
    target_finish_date: date | None = None
    owner_user_id: int


class ProjectUpdate(BaseModel):
    """基本情報・設定を変える入力（API-PRJ-004）。載せた項目だけを変える。

    ``code``・``name``・``start_date``・``owner_user_id``・``settings`` に null は書けない（サービス層が 400 にする）。
    ``settings`` は載せた項目だけを今の設定に重ねる。WBS の状態（``wbs_status``）はここでは変えない（5.10 の遷移で変わる）。
    """

    model_config = ConfigDict(extra="forbid")

    code: str | None = Field(default=None, min_length=1, max_length=PROJECT_CODE_MAX)
    name: str | None = Field(default=None, min_length=1, max_length=NAME_MAX)
    purpose: str | None = None
    requirements: str | None = None
    key_deliverables: str | None = None
    start_date: date | None = None
    target_finish_date: date | None = None
    owner_user_id: int | None = None
    settings: ProjectSettingsPatch | None = None


class UserSummary(BaseModel):
    """一覧・詳細に添える利用者の名前。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    login_id: str
    display_name: str


class ProjectOut(BaseModel):
    """プロジェクトの基本情報・WBS の状態・設定（API-PRJ-001〜004 の出力）。"""

    id: int
    code: str
    name: str
    purpose: str | None
    requirements: str | None
    key_deliverables: str | None
    start_date: date
    target_finish_date: date | None
    owner_user_id: int
    owner: UserSummary
    wbs_status: str
    settings: ProjectSettingsOut


class ProjectList(BaseModel):
    """自分が見られるプロジェクトの一覧（``limit``・``offset`` の範囲）と総数。"""

    items: list[ProjectOut]
    total: int
    limit: int
    offset: int


# ---------------------------------------------------------------------------
# スコープ項目（API-PRJ-005・006）
# ---------------------------------------------------------------------------


class ScopeItemIn(BaseModel):
    """置き換えの 1 項目。``id`` を書けば既存の項目を変え（ID を保つ）、書かなければ足す。並び順は配列の順。"""

    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    kind: ScopeItemKind
    title: str = Field(min_length=1, max_length=TITLE_MAX)
    description: str | None = None


class ScopeItemsReplace(BaseModel):
    """スコープ項目を置き換える入力（API-PRJ-006）。載せなかった既存の項目は消える。"""

    model_config = ConfigDict(extra="forbid")

    items: list[ScopeItemIn]


class ScopeItemOut(BaseModel):
    """スコープ項目（``scope_items`` の行）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    title: str
    description: str | None
    sort_order: int


class ScopeItemList(BaseModel):
    """スコープ項目の一覧（並び順。``limit``・``offset`` の範囲）と総数。"""

    project_id: int
    items: list[ScopeItemOut]
    total: int
    limit: int
    offset: int


# ---------------------------------------------------------------------------
# 稼働カレンダー（API-PRJ-007・008）
# ---------------------------------------------------------------------------


class CalendarHolidayIO(BaseModel):
    """休日（日付と名前）。"""

    model_config = ConfigDict(extra="forbid")

    holiday_date: date
    name: str | None = Field(default=None, max_length=HOLIDAY_NAME_MAX)


class CalendarOut(BaseModel):
    """稼働カレンダー（API-PRJ-007・008 の出力）。休日は日付の順。"""

    project_id: int
    working_weekdays: str
    hours_per_day: float
    holidays: list[CalendarHolidayIO]


class CalendarReplace(BaseModel):
    """稼働カレンダーを更新する入力（API-PRJ-008）。休日は丸ごと置き換える。

    ``working_weekdays``：月曜から日曜の順の 7 文字で ``1`` が稼働日（4.1）。``hours_per_day``：0 より大きく 24 以下・小数 2 桁まで。
    稼働の曜日が 1 つも無いカレンダーは RULE-CAL-001 が拒む（422 ``NO_WORKING_DAYS``）。
    """

    model_config = ConfigDict(extra="forbid")

    working_weekdays: str = Field(pattern=r"^[01]{7}$")
    hours_per_day: Decimal = Field(gt=0, le=24, max_digits=4, decimal_places=2, allow_inf_nan=False)
    holidays: list[CalendarHolidayIO]


# ---------------------------------------------------------------------------
# メンバー（API-PRJ-009〜011）
# ---------------------------------------------------------------------------


class MemberUpsert(BaseModel):
    """メンバーを足す・ロールと所属を変える入力（API-PRJ-010）。``roles`` は 3 章のロールのコードを 1 つ以上。"""

    model_config = ConfigDict(extra="forbid")

    roles: list[str] = Field(min_length=1)
    organization: str | None = Field(default=None, max_length=ORGANIZATION_MAX)
    team: str | None = Field(default=None, max_length=TEAM_MAX)


class MemberOut(BaseModel):
    """メンバー（``project_members`` の行と、その利用者の名前・有効か）。ロールは 3 章の順。"""

    user_id: int
    login_id: str
    display_name: str
    is_active: bool
    roles: list[str]
    organization: str | None
    team: str | None


class MemberList(BaseModel):
    """メンバーの一覧（利用者の ID の順。``limit``・``offset`` の範囲）と総数。"""

    project_id: int
    items: list[MemberOut]
    total: int
    limit: int
    offset: int
