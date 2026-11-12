"""変更管理の API の入出力（設計書 6.2 の API-CHG-001〜010、4.1 の change_requests・corrective_actions・audit_logs、5.9）。

変更内容（``operations``）は 5.9 の操作の配列で、操作ごとの形の検査は RULE-CHG-001 が行う（ここでは「オブジェクトの配列」だけを見る）。
長さの上限は 4.1 の列（title 200 文字）に合わせる。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ChangeRequestStatusCode = Literal["DRAFT", "SUBMITTED", "APPROVED", "REJECTED", "APPLIED"]
CorrectiveActionStatusCode = Literal["NOT_STARTED", "IN_PROGRESS", "DONE"]

_WBS_CODE = Field(min_length=1, max_length=100)


# ---------------------------------------------------------------------------
# 変更要求（API-CHG-001〜006）
# ---------------------------------------------------------------------------


class ChangeRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1)
    target_codes: list[str] = Field(min_length=1)
    operations: list[dict[str, Any]] = Field(default_factory=list)


class ChangeRequestPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    reason: str | None = Field(default=None, min_length=1)
    target_codes: list[str] | None = Field(default=None, min_length=1)
    operations: list[dict[str, Any]] | None = None


class ChangeRequestOut(BaseModel):
    id: int
    project_id: int
    number: int
    title: str
    reason: str
    target_codes: list[str]
    operations: list[dict[str, Any]]
    status: ChangeRequestStatusCode
    impact: dict[str, Any] | None
    created_by: int
    submitted_at: datetime | None
    applied_by: int | None
    applied_at: datetime | None
    approval_request_id: int | None


class ChangeRequestList(BaseModel):
    items: list[ChangeRequestOut]
    total: int
    limit: int
    offset: int


class ImpactOut(BaseModel):
    """影響分析の結果（5.9 の形。``change_requests.impact`` に保存する値と同じ）。"""

    change_request_id: int
    project_finish: dict[str, Any]
    milestones: list[dict[str, Any]]
    critical_path: dict[str, Any]
    total_estimate_hours: dict[str, Any]
    elements: dict[str, Any]


class BaselineVersionOut(BaseModel):
    id: int
    version: int
    reason: str


class AppliedOut(BaseModel):
    change_request: ChangeRequestOut
    scope_baseline: BaselineVersionOut
    schedule_baseline: BaselineVersionOut


# ---------------------------------------------------------------------------
# 是正措置（API-CHG-007〜009）
# ---------------------------------------------------------------------------


class CorrectiveActionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    element_id: int | None = None
    activity_id: int | None = None
    status_report_id: int | None = None
    cause: str = Field(min_length=1)
    action: str = Field(min_length=1)
    owner_user_id: int | None = None
    due_date: date | None = None


class CorrectiveActionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: CorrectiveActionStatusCode | None = None
    effect: str | None = None
    owner_user_id: int | None = None
    due_date: date | None = None


class CorrectiveActionOut(BaseModel):
    id: int
    project_id: int
    element_id: int | None
    activity_id: int | None
    status_report_id: int | None
    wbs_code: str | None
    cause: str
    action: str
    owner_user_id: int | None
    due_date: date | None
    status: CorrectiveActionStatusCode
    effect: str | None
    overdue: bool


class CorrectiveActionList(BaseModel):
    items: list[CorrectiveActionOut]
    total: int
    limit: int
    offset: int


# ---------------------------------------------------------------------------
# 変更履歴（API-CHG-010）
# ---------------------------------------------------------------------------


class AuditLogOut(BaseModel):
    id: int
    occurred_at: datetime
    user_id: int | None
    project_id: int | None
    entity_type: str
    entity_id: int
    wbs_code: str | None
    action: Literal["CREATE", "UPDATE", "DELETE"]
    before: dict[str, Any] | None
    after: dict[str, Any] | None


class AuditLogList(BaseModel):
    items: list[AuditLogOut]
    total: int
    limit: int
    offset: int
