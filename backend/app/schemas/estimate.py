"""見積の入出力（API-EST-001〜003）。

工数は入力を小数 2 桁までの ``Decimal`` で受け、出力は数（float）で返す（``app.schemas.wbs`` と同じ。A-14）。
値の一覧（対象の種類・単位・手法）は 4.1 の ``estimate_records`` と ``app.domain.estimate`` の列挙と同じ。
前提・参照資料は 4.1 に長さの定めが無い（``Text``。A-32）ので上限を置かない。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.wbs import HOURS_MAX

TargetType = Literal["ELEMENT", "ACTIVITY"]
InputUnit = Literal["HOUR", "DAY"]
EstimateMethod = Literal["BOTTOM_UP", "ANALOGOUS", "THREE_POINT", "EXPERT", "OTHER"]
RollupSource = Literal["ACTIVITIES", "OWN_ESTIMATE", "CHILDREN"]


class EstimatePut(BaseModel):
    """見積を書く（API-EST-001）。``input_unit`` が DAY なら 1 日の稼働時間で人時へ換算する（RULE-EST-002）。"""

    model_config = ConfigDict(extra="forbid")

    input_value: Decimal = Field(ge=0, le=HOURS_MAX, decimal_places=2)
    input_unit: InputUnit
    method: EstimateMethod | None = None
    assumptions: str | None = None
    references: str | None = None


class EstimateRecordOut(BaseModel):
    """見積の記録 1 件（4.1 の ``estimate_records`` の 1 行）。``hours`` は人時へ換算した値。"""

    id: int
    project_id: int
    target_type: TargetType
    target_id: int
    hours: float
    input_value: float
    input_unit: InputUnit
    method: EstimateMethod | None
    assumptions: str | None
    references: str | None
    entered_by: int
    entered_by_name: str | None
    entered_at: datetime


class EstimateWrittenOut(BaseModel):
    """見積を書いた結果：足した記録と、対象の今の見積（人時）・WBS の状態・プロジェクトの総見積（RULE-EST-001）。"""

    record: EstimateRecordOut
    current_hours: float
    wbs_status: str
    project_estimate_hours: float


class EstimateHistoryOut(BaseModel):
    """見積の履歴（API-EST-002）。古い順（``entered_at``・``id``）。``current_hours`` は対象の今の見積（対象が無ければ空）。"""

    target_type: TargetType
    target_id: int
    target_exists: bool
    current_hours: float | None
    items: list[EstimateRecordOut]
    total: int
    limit: int
    offset: int


class RollupElementOut(BaseModel):
    """要素 1 件の集計（RULE-EST-001）。``hours`` は上位へ足す値、``lower_hours`` は下位の合計（下位が無ければ空）。"""

    element_id: int
    parent_id: int | None
    wbs_code: str
    name: str
    kind: str
    depth: int
    hours: float
    source: RollupSource
    lower_hours: float | None
    own_estimate_hours: float | None


class RollupActivityOut(BaseModel):
    """活動 1 件の見積（集計の葉）。``hours`` は集計に使った値（未見積は 0）、``estimate_hours`` は今の見積のまま。"""

    activity_id: int
    work_package_id: int
    name: str
    hours: float
    estimate_hours: float | None


class EstimateRollupOut(BaseModel):
    """ボトムアップの集計（API-EST-003）。要素は先行順（兄弟は表示順）、活動はその WP の中の表示順。"""

    project_id: int
    project_estimate_hours: float
    restricted: bool
    elements: list[RollupElementOut]
    activities: list[RollupActivityOut]
