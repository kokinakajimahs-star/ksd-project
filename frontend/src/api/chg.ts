// 変更管理の API の型と呼び出し（設計書 6.2 の API-CHG-001〜011）。
// 形はサーバの schemas（backend/app/schemas/change.py）に合わせる。日付は YYYY-MM-DD、日時は ISO 8601
import { apiFetch } from "./client";
import type { Page, PageQuery } from "./cmn";

// ---------------------------------------------------------------------------
// 変更要求（API-CHG-001〜006・011）
// ---------------------------------------------------------------------------

/** 変更要求の状態（5.10。RULE-CHG-003） */
export type ChangeRequestStatus = "DRAFT" | "SUBMITTED" | "APPROVED" | "REJECTED" | "APPLIED";

/** 変更内容の操作 1 件（5.9）。形の検査はサーバ（RULE-CHG-001）が行う */
export type ChangeOperation = { op: string } & Record<string, unknown>;

export interface ChangeRequest {
  id: number;
  project_id: number;
  /** プロジェクト内の連番 */
  number: number;
  title: string;
  reason: string;
  /** 対象の WBS ID（複数可。CHG-01） */
  target_codes: string[];
  operations: ChangeOperation[];
  status: ChangeRequestStatus;
  /** 提出のときに保存した影響分析（5.9 の形）。提出の前は null */
  impact: ImpactBody | null;
  created_by: number;
  submitted_at: string | null;
  applied_by: number | null;
  applied_at: string | null;
  approval_request_id: number | null;
}

export type ChangeRequestList = Page<ChangeRequest>;

export interface ChangeRequestInput {
  title: string;
  reason: string;
  target_codes: string[];
  operations: ChangeOperation[];
}

/** 載せた項目だけを変える（API-CHG-003。DRAFT のときだけ） */
export type ChangeRequestPatch = Partial<ChangeRequestInput>;

export interface ImpactActivity {
  activity_id: number | null;
  ref: string | null;
  name: string;
}

export interface ImpactElement {
  element_id: number | null;
  ref: string | null;
  wbs_code: string | null;
  name: string;
  kind: string;
}

export interface ImpactElementUpdate {
  element_id: number;
  wbs_code: string;
  name: string;
  changes: Record<string, { before: unknown; after: unknown }>;
}

export interface MilestoneShift {
  activity_id: number;
  name: string;
  before: string;
  after: string;
  shift_days: number;
}

/** 影響分析の結果（5.9：終了日・マイルストーン・クリティカルパス・総工数・要素の差） */
export interface ImpactBody {
  project_finish: { before: string | null; after: string | null; shift_days: number | null };
  milestones: MilestoneShift[];
  critical_path: { added: ImpactActivity[]; removed: ImpactActivity[] };
  /** 人時。サーバは小数 2 桁の文字列で返す */
  total_estimate_hours: { before: string | number | null; after: string | number | null };
  elements: { added: ImpactElement[]; updated: ImpactElementUpdate[]; removed: ImpactElement[] };
}

export interface Impact extends ImpactBody {
  change_request_id: number;
}

export interface BaselineVersion {
  id: number;
  version: number;
  reason: string;
}

/** 反映の結果（API-CHG-006）：反映した変更要求と、作ったスコープ・スケジュールの新しい版 */
export interface Applied {
  change_request: ChangeRequest;
  scope_baseline: BaselineVersion;
  schedule_baseline: BaselineVersion;
}

export interface ChangeRequestQuery extends PageQuery {
  status?: ChangeRequestStatus;
}

export function createChangeRequest(projectId: number, input: ChangeRequestInput): Promise<ChangeRequest> {
  return apiFetch<ChangeRequest>(`/projects/${projectId}/change-requests`, { method: "POST", body: input });
}

export function listChangeRequests(projectId: number, query: ChangeRequestQuery = {}): Promise<ChangeRequestList> {
  return apiFetch<ChangeRequestList>(`/projects/${projectId}/change-requests`, {
    query: { status: query.status, limit: query.limit, offset: query.offset },
  });
}

export function updateChangeRequest(
  projectId: number,
  changeRequestId: number,
  input: ChangeRequestPatch,
): Promise<ChangeRequest> {
  return apiFetch<ChangeRequest>(`/projects/${projectId}/change-requests/${changeRequestId}`, {
    method: "PATCH",
    body: input,
  });
}

/** 影響分析を流す（本計画にも変更要求にも書かない。API-CHG-004） */
export function analyzeChangeRequest(projectId: number, changeRequestId: number): Promise<Impact> {
  return apiFetch<Impact>(`/projects/${projectId}/change-requests/${changeRequestId}/impact`, { method: "POST" });
}

export function submitChangeRequest(projectId: number, changeRequestId: number): Promise<ChangeRequest> {
  return apiFetch<ChangeRequest>(`/projects/${projectId}/change-requests/${changeRequestId}/submit`, {
    method: "POST",
  });
}

/** 提出を取り下げる（API-CHG-011。提出済みを下書きに戻し、保留中の承認申請を消す。影響分析は残る） */
export function withdrawChangeRequest(projectId: number, changeRequestId: number): Promise<ChangeRequest> {
  return apiFetch<ChangeRequest>(`/projects/${projectId}/change-requests/${changeRequestId}/withdraw`, {
    method: "POST",
  });
}

export function applyChangeRequest(projectId: number, changeRequestId: number): Promise<Applied> {
  return apiFetch<Applied>(`/projects/${projectId}/change-requests/${changeRequestId}/apply`, { method: "POST" });
}

// ---------------------------------------------------------------------------
// 是正措置（API-CHG-007〜009）
// ---------------------------------------------------------------------------

/** 是正措置の状態（5.10。RULE-CHG-005） */
export type CorrectiveActionStatus = "NOT_STARTED" | "IN_PROGRESS" | "DONE";

export interface CorrectiveAction {
  id: number;
  project_id: number;
  element_id: number | null;
  activity_id: number | null;
  status_report_id: number | null;
  /** 紐付けた要素（無ければ活動の WP）の WBS ID */
  wbs_code: string | null;
  cause: string;
  action: string;
  owner_user_id: number | null;
  due_date: string | null;
  status: CorrectiveActionStatus;
  effect: string | null;
  /** 期限超過（期限 ＜ 今日 かつ 未完了。RULE-CHG-005） */
  overdue: boolean;
}

export type CorrectiveActionList = Page<CorrectiveAction>;

/** 登録（API-CHG-007）。要素か活動の少なくとも一方に紐付ける */
export interface CorrectiveActionInput {
  element_id: number | null;
  activity_id: number | null;
  status_report_id: number | null;
  cause: string;
  action: string;
  owner_user_id: number | null;
  due_date: string | null;
}

/** 載せた項目だけを変える（API-CHG-009） */
export interface CorrectiveActionPatch {
  status?: CorrectiveActionStatus;
  effect?: string | null;
  owner_user_id?: number | null;
  due_date?: string | null;
}

export interface CorrectiveActionQuery extends PageQuery {
  status?: CorrectiveActionStatus;
  overdue?: boolean;
  wbs_code?: string;
}

export function createCorrectiveAction(projectId: number, input: CorrectiveActionInput): Promise<CorrectiveAction> {
  return apiFetch<CorrectiveAction>(`/projects/${projectId}/corrective-actions`, { method: "POST", body: input });
}

export function listCorrectiveActions(
  projectId: number,
  query: CorrectiveActionQuery = {},
): Promise<CorrectiveActionList> {
  return apiFetch<CorrectiveActionList>(`/projects/${projectId}/corrective-actions`, {
    query: {
      status: query.status,
      overdue: query.overdue,
      wbs_code: query.wbs_code,
      limit: query.limit,
      offset: query.offset,
    },
  });
}

export function updateCorrectiveAction(
  projectId: number,
  actionId: number,
  input: CorrectiveActionPatch,
): Promise<CorrectiveAction> {
  return apiFetch<CorrectiveAction>(`/projects/${projectId}/corrective-actions/${actionId}`, {
    method: "PATCH",
    body: input,
  });
}

// ---------------------------------------------------------------------------
// 変更履歴（API-CHG-010。追記だけで、更新・削除の口は無い）
// ---------------------------------------------------------------------------

export type AuditAction = "CREATE" | "UPDATE" | "DELETE";

export interface AuditLog {
  id: number;
  occurred_at: string;
  user_id: number | null;
  project_id: number | null;
  /** 対象の種類（表の名前） */
  entity_type: string;
  entity_id: number;
  /** 記録した時点の WBS ID */
  wbs_code: string | null;
  action: AuditAction;
  /** 追加は null。更新は変わった項目だけ */
  before: Record<string, unknown> | null;
  /** 削除は null。更新は変わった項目だけ */
  after: Record<string, unknown> | null;
}

export type AuditLogList = Page<AuditLog>;

/** 検索の条件（期間は UTC の日付で両端を含む） */
export interface AuditLogQuery extends PageQuery {
  date_from?: string;
  date_to?: string;
  user_id?: number;
  entity_type?: string;
  entity_id?: number;
  wbs_code?: string;
}

export function searchAuditLogs(projectId: number, query: AuditLogQuery = {}): Promise<AuditLogList> {
  return apiFetch<AuditLogList>(`/projects/${projectId}/audit-logs`, {
    query: {
      date_from: query.date_from,
      date_to: query.date_to,
      user_id: query.user_id,
      entity_type: query.entity_type,
      entity_id: query.entity_id,
      wbs_code: query.wbs_code,
      limit: query.limit,
      offset: query.offset,
    },
  });
}
