"""WBS の入出力（API-WBS-001〜023）。

長さの上限は ``wbs_elements``・``wbs_numbering_rules``（docs/decisions/ASSUMPTIONS.md A-15）に合わせる。
区分・規則の形の検査のうち、ドメインの規則（RULE-WBS-001・003）が決めるものはサービス層が行う（1.3）。
工数は入力を小数 2 桁までの ``Decimal`` で受け、出力は数（float）で返す（A-14 の ``hours_per_day`` と同じ）。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

NAME_MAX = 200
TEAM_MAX = 200
ElementKind = Literal["DELIVERABLE", "MANAGEMENT", "COMPONENT", "WORK_PACKAGE"]
HOURS_MAX = Decimal("99999999.99")


class ElementCreate(BaseModel):
    """要素を足す（API-WBS-002）。``parent_id`` が無ければ最上位。"""

    model_config = ConfigDict(extra="forbid")

    parent_id: int | None = None
    kind: ElementKind
    name: str = Field(min_length=1, max_length=NAME_MAX)
    own_estimate_hours: Decimal | None = Field(default=None, ge=0, le=HOURS_MAX, decimal_places=2)
    responsible_user_id: int | None = None
    responsible_team: str | None = Field(default=None, max_length=TEAM_MAX)


class ElementUpdate(BaseModel):
    """要素の名称・区分・責任者・自分の見積を変える（API-WBS-003）。載せた項目だけを変える。"""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=NAME_MAX)
    kind: ElementKind | None = None
    own_estimate_hours: Decimal | None = Field(default=None, ge=0, le=HOURS_MAX, decimal_places=2)
    responsible_user_id: int | None = None
    responsible_team: str | None = Field(default=None, max_length=TEAM_MAX)


class ElementMove(BaseModel):
    """要素を移動する（API-WBS-004）。``position`` は移動先の兄弟の中の位置（0 から。省けば末尾）。"""

    model_config = ConfigDict(extra="forbid")

    parent_id: int | None
    position: int | None = Field(default=None, ge=0)


class ElementOut(BaseModel):
    """WBS の要素 1 件。``depth`` は最上位が 1。``rollup_hours`` は RULE-EST-001 の集計値。"""

    id: int
    parent_id: int | None
    kind: str
    name: str
    wbs_code: str
    sibling_seq: int
    sort_order: int
    depth: int
    own_estimate_hours: float | None
    rollup_hours: float
    rollup_source: str
    responsible_user_id: int | None
    responsible_team: str | None
    needs_renumber: bool


class ValidationCounts(BaseModel):
    """検査の件数（API-WBS-001 の木に付ける。中身は API-WBS-012）。"""

    errors: int
    warnings: int
    missing_completion: int


class NumberingRuleIO(BaseModel):
    """採番規則（DB-WBS-002・5.2）。"""

    model_config = ConfigDict(extra="forbid")

    separator: str = Field(min_length=1, max_length=8)
    min_digits: int = Field(ge=1, le=9)
    prefix: str = Field(max_length=20)
    pad_first_level: bool


class WbsTree(BaseModel):
    """WBS の木（API-WBS-001）。``elements`` は先行順（兄弟は表示順）。"""

    project_id: int
    wbs_status: str
    numbering_rule: NumberingRuleIO
    project_estimate_hours: float
    renumber_pending: int
    validation: ValidationCounts
    restricted: bool
    elements: list[ElementOut]


class CodeChangeOut(BaseModel):
    element_id: int
    old_code: str
    new_code: str


class RenumberOut(BaseModel):
    """改番の結果（API-WBS-007）。``changes`` は ID の変わった要素だけ（先行順）。"""

    renumbered_at: datetime | None
    changes: list[CodeChangeOut]


class CodeHistoryOut(BaseModel):
    id: int
    element_id: int
    current_code: str
    old_code: str
    new_code: str
    renumbered_at: datetime
    renumbered_by: int


class CodeHistoryList(BaseModel):
    project_id: int
    items: list[CodeHistoryOut]
    total: int
    limit: int
    offset: int


class DictionaryPut(BaseModel):
    """WBS 辞書を書く（API-WBS-010）。4 項目を載せた値で置き換える（省いた項目は空になる）。"""

    model_config = ConfigDict(extra="forbid")

    deliverable: str | None = None
    work_scope: str | None = None
    completion_criteria: str | None = None
    assumptions: str | None = None


class DictionaryOut(BaseModel):
    """WBS 辞書（WBS-11）。WBS ID・名称・区分・責任者・見積は要素の値。"""

    element_id: int
    wbs_code: str
    name: str
    kind: str
    responsible_user_id: int | None
    responsible_team: str | None
    own_estimate_hours: float | None
    deliverable: str | None
    work_scope: str | None
    completion_criteria: str | None
    assumptions: str | None


class FindingOut(BaseModel):
    level: str
    code: str
    element_id: int | None
    wbs_code: str | None
    scope_item_id: int | None
    own_estimate_hours: float | None
    lower_hours: float | None


class MissingCompletionOut(BaseModel):
    element_id: int
    wbs_code: str
    missing: list[str]


class ValidationOut(BaseModel):
    """100% ルール（RULE-WBS-004）と完了条件（RULE-WBS-005）の検査の結果（API-WBS-012）。"""

    project_id: int
    tolerance: float
    counts: ValidationCounts
    findings: list[FindingOut]
    missing_completion: list[MissingCompletionOut]


class ScopeLinksPut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope_item_ids: list[int]


class ScopeLinksOut(BaseModel):
    element_id: int
    scope_item_ids: list[int]


# ---------------------------------------------------------------------------
# レビュー（API-WBS-014〜017）・スコープベースライン（API-WBS-021・022）
# ---------------------------------------------------------------------------

CommentCategory = Literal["OMISSION", "DUPLICATION", "AMBIGUITY", "OTHER"]
CommentStatus = Literal["OPEN", "RESOLVED", "REJECTED"]


class ReviewCreate(BaseModel):
    """レビューを始める（API-WBS-014）。``reviewer_ids`` はレビューを頼むメンバー。"""

    model_config = ConfigDict(extra="forbid")

    reviewer_ids: list[int] = Field(min_length=1)


class CommentCreate(BaseModel):
    """指摘を書く（API-WBS-016）。``element_id`` が無ければ WBS 全体への指摘。"""

    model_config = ConfigDict(extra="forbid")

    element_id: int | None = None
    category: CommentCategory
    body: str = Field(min_length=1)


class CommentUpdate(BaseModel):
    """指摘の状態・対応を更新する（API-WBS-017）。載せた項目だけを変える。"""

    model_config = ConfigDict(extra="forbid")

    status: CommentStatus | None = None
    resolution: str | None = None


class CommentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    review_id: int
    element_id: int | None
    category: str
    body: str
    status: str
    author_id: int
    resolution: str | None
    updated_at: datetime


class ReviewOut(BaseModel):
    id: int
    project_id: int
    status: str
    requested_by: int
    requested_at: datetime
    reviewer_ids: list[int]
    closed_at: datetime | None
    comments: list[CommentOut]


class ReviewSummary(BaseModel):
    """レビューの一覧の 1 行（API-WBS-025。指摘は API-WBS-015 で読む）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    status: str
    requested_by: int
    requested_at: datetime
    reviewer_ids: list[int]
    closed_at: datetime | None


class ReviewList(BaseModel):
    project_id: int
    items: list[ReviewSummary]
    total: int
    limit: int
    offset: int


class BaselineSummary(BaseModel):
    id: int
    version: int
    reason: str
    approval_request_id: int | None
    change_request_id: int | None
    created_by: int
    created_at: datetime
    element_count: int


class BaselineList(BaseModel):
    project_id: int
    items: list[BaselineSummary]
    total: int
    limit: int
    offset: int


class BaselineElementOut(BaseModel):
    element_id: int
    parent_element_id: int | None
    wbs_code: str
    kind: str
    name: str
    own_estimate_hours: float | None
    dictionary: dict[str, str | None] | None


class BaselineOut(BaselineSummary):
    """スコープベースラインの 1 版（WBS-22）。要素は写した時点の値（版の中の木は ``parent_element_id`` でたどる）。"""

    restricted: bool
    elements: list[BaselineElementOut]


# ---------------------------------------------------------------------------
# 責任分担表（API-WBS-011）・横断照会（API-WBS-023）
# ---------------------------------------------------------------------------


class MatrixUserOut(BaseModel):
    """責任分担表の列（利用者）。``is_member`` が偽なら、メンバーから外した後も責任・割当が残っている人。"""

    user_id: int
    login_id: str
    display_name: str
    is_member: bool


class MatrixCellOut(BaseModel):
    user_id: int
    is_responsible: bool
    is_assigned: bool
    planned_hours: float


class MatrixRowOut(BaseModel):
    """責任分担表の 1 行（WP）。``cells`` は ``users`` の順。"""

    element_id: int
    wbs_code: str
    name: str
    responsible_user_id: int | None
    responsible_team: str | None
    planned_hours: float
    cells: list[MatrixCellOut]


class WithoutResponsibleOut(BaseModel):
    element_id: int
    wbs_code: str
    name: str
    responsible_team: str | None


class ResponsibilityMatrixOut(BaseModel):
    """責任分担表（WBS-10。RULE-WBS-007）。行は WP の先行順、列はメンバーの加えた順（メンバーでない人は後ろ）。"""

    project_id: int
    restricted: bool
    users: list[MatrixUserOut]
    rows: list[MatrixRowOut]
    without_responsible: list[WithoutResponsibleOut]


class TraceElementOut(BaseModel):
    id: int
    parent_id: int | None
    kind: str
    name: str
    wbs_code: str


class WbsTraceOut(BaseModel):
    """WBS ID による横断照会（WBS-07。RULE-WBS-008）。``elements`` は解決した要素と配下（先行順）。

    ``eac_hours`` は EAC（RULE-EFF-002）を読めないとき・委託先には ``null``。
    """

    project_id: int
    query: str
    wbs_code: str
    element_id: int
    resolved_by: str
    restricted: bool
    elements: list[TraceElementOut]
    activity_ids: list[int]
    estimate_record_ids: list[int]
    time_entry_ids: list[int]
    actual_hours: float
    eac_hours: float | None
    change_request_ids: list[int]
    corrective_action_ids: list[int]


# ---------------------------------------------------------------------------
# 承認の申請・判断・承認待ちの一覧（API-WBS-018〜020）
# ---------------------------------------------------------------------------


class ApprovalOut(BaseModel):
    """承認申請（DB-WBS-008）。WBS の申請の ``target_id`` はプロジェクトの ID。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    target_type: str
    target_id: int
    status: str
    requested_by: int
    requested_at: datetime
    decided_by: int | None
    decided_at: datetime | None
    comment: str | None


class ApprovalSubmitOut(ApprovalOut):
    """承認の申請（API-WBS-018）の結果。``wbs_status`` は申請の後の WBS の状態。"""

    wbs_status: str


class DecisionIn(BaseModel):
    """承認・差戻し（API-WBS-019）。``comment`` は判断のコメント（WBS-21）。"""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["APPROVE", "REJECT"]
    comment: str | None = None


class DecisionOut(ApprovalOut):
    """承認・差戻しの結果。WBS の申請なら ``wbs_status`` と、承認で作った版 ``scope_baseline``（差戻しなら null）。"""

    wbs_status: str | None
    scope_baseline: BaselineSummary | None


class PendingApprovalOut(ApprovalOut):
    project_code: str
    project_name: str
    requested_by_name: str


class PendingApprovalList(BaseModel):
    """自分が判断できる承認待ちの一覧（API-WBS-020）。申請の古い順。"""

    items: list[PendingApprovalOut]
    total: int
    limit: int
    offset: int
