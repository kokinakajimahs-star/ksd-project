// WBS の API（設計書 6.2 の API-WBS-001〜023）。
// 形はサーバの schemas（backend/app/schemas/wbs.py）に合わせる
import { apiDownload, apiFetch, type DownloadedFile } from "./client";
import type { Page, PageQuery } from "./cmn";

export type ElementKind = "DELIVERABLE" | "MANAGEMENT" | "COMPONENT" | "WORK_PACKAGE";

/** 集計の元（RULE-EST-001）：活動の見積の合計・自分の見積・子の要素の合計 */
export type RollupSource = "ACTIVITIES" | "OWN_ESTIMATE" | "CHILDREN";

export interface WbsElement {
  id: number;
  parent_id: number | null;
  kind: ElementKind;
  name: string;
  wbs_code: string;
  sibling_seq: number;
  sort_order: number;
  /** 最上位が 1 */
  depth: number;
  own_estimate_hours: number | null;
  rollup_hours: number;
  rollup_source: string;
  responsible_user_id: number | null;
  responsible_team: string | null;
  needs_renumber: boolean;
}

/** 採番規則（DB-WBS-002・5.2） */
export interface NumberingRule {
  separator: string;
  min_digits: number;
  prefix: string;
  pad_first_level: boolean;
}

/** 検査の件数（API-WBS-001 の木に付く。中身は API-WBS-012） */
export interface ValidationCounts {
  errors: number;
  warnings: number;
  missing_completion: number;
}

export interface WbsTree {
  project_id: number;
  wbs_status: string;
  numbering_rule: NumberingRule;
  project_estimate_hours: number;
  renumber_pending: number;
  validation: ValidationCounts;
  restricted: boolean;
  /** 先行順（兄弟は表示順） */
  elements: WbsElement[];
}

export interface ElementCreateInput {
  /** null は最上位 */
  parent_id: number | null;
  kind: ElementKind;
  name: string;
}

/** 載せた項目だけを変える（API-WBS-003） */
export interface ElementUpdateInput {
  name?: string;
  kind?: ElementKind;
  responsible_user_id?: number | null;
  responsible_team?: string | null;
}

export interface CodeChange {
  element_id: number;
  old_code: string;
  new_code: string;
}

export interface RenumberResult {
  /** 何も変わらなければ null */
  renumbered_at: string | null;
  changes: CodeChange[];
}

export interface CodeHistory {
  id: number;
  element_id: number;
  current_code: string;
  old_code: string;
  new_code: string;
  renumbered_at: string;
  renumbered_by: number;
}

export interface CodeHistoryList extends Page<CodeHistory> {
  project_id: number;
}

/** WBS 辞書の 4 項目（API-WBS-010 は載せた値で置き換える） */
export interface DictionaryInput {
  deliverable: string | null;
  work_scope: string | null;
  completion_criteria: string | null;
  assumptions: string | null;
}

/** WBS 辞書（WBS-11）。WBS ID・名称・区分・責任者・見積は要素の値 */
export interface Dictionary extends DictionaryInput {
  element_id: number;
  wbs_code: string;
  name: string;
  kind: ElementKind;
  responsible_user_id: number | null;
  responsible_team: string | null;
  own_estimate_hours: number | null;
}

export type FindingLevel = "ERROR" | "WARNING";
export type FindingCode = "SUM_MISMATCH" | "OUT_OF_SCOPE_LINKED" | "IN_SCOPE_UNCOVERED";
export type CompletionField = "deliverable" | "completion_criteria";

export interface Finding {
  level: FindingLevel;
  code: FindingCode;
  element_id: number | null;
  wbs_code: string | null;
  scope_item_id: number | null;
  own_estimate_hours: number | null;
  lower_hours: number | null;
}

export interface MissingCompletion {
  element_id: number;
  wbs_code: string;
  missing: CompletionField[];
}

export interface Validation {
  project_id: number;
  tolerance: number;
  counts: ValidationCounts;
  findings: Finding[];
  missing_completion: MissingCompletion[];
}

export interface ScopeLinks {
  element_id: number;
  scope_item_ids: number[];
}

export type CommentCategory = "OMISSION" | "DUPLICATION" | "AMBIGUITY" | "OTHER";
export type CommentStatus = "OPEN" | "RESOLVED" | "REJECTED";

export interface ReviewComment {
  id: number;
  review_id: number;
  /** null は WBS 全体への指摘 */
  element_id: number | null;
  category: CommentCategory;
  body: string;
  status: CommentStatus;
  author_id: number;
  resolution: string | null;
  updated_at: string;
}

export interface Review {
  id: number;
  project_id: number;
  /** OPEN・CLOSED */
  status: string;
  requested_by: number;
  requested_at: string;
  reviewer_ids: number[];
  closed_at: string | null;
  comments: ReviewComment[];
}

export interface CommentCreateInput {
  element_id: number | null;
  category: CommentCategory;
  body: string;
}

/** 載せた項目だけを変える（API-WBS-017） */
export interface CommentUpdateInput {
  status?: CommentStatus;
  resolution?: string | null;
}

export interface ScopeBaselineSummary {
  id: number;
  version: number;
  reason: string;
  approval_request_id: number | null;
  change_request_id: number | null;
  created_by: number;
  created_at: string;
  element_count: number;
}

export interface ScopeBaselineList extends Page<ScopeBaselineSummary> {
  project_id: number;
}

export interface ScopeBaselineElement {
  element_id: number;
  parent_element_id: number | null;
  wbs_code: string;
  kind: string;
  name: string;
  own_estimate_hours: number | null;
  /** 写した時点の辞書の 4 項目（辞書が無ければ null） */
  dictionary: Record<string, string | null> | null;
}

export interface ScopeBaseline extends ScopeBaselineSummary {
  restricted: boolean;
  elements: ScopeBaselineElement[];
}

export interface MatrixUser {
  user_id: number;
  login_id: string;
  display_name: string;
  /** 偽なら、メンバーから外した後も責任・割当が残っている人 */
  is_member: boolean;
}

export interface MatrixCell {
  user_id: number;
  is_responsible: boolean;
  is_assigned: boolean;
  planned_hours: number;
}

export interface MatrixRow {
  element_id: number;
  wbs_code: string;
  name: string;
  responsible_user_id: number | null;
  responsible_team: string | null;
  planned_hours: number;
  /** users の順 */
  cells: MatrixCell[];
}

export interface WithoutResponsible {
  element_id: number;
  wbs_code: string;
  name: string;
  responsible_team: string | null;
}

export interface ResponsibilityMatrix {
  project_id: number;
  restricted: boolean;
  users: MatrixUser[];
  rows: MatrixRow[];
  without_responsible: WithoutResponsible[];
}

export interface TraceElement {
  id: number;
  parent_id: number | null;
  kind: string;
  name: string;
  wbs_code: string;
}

/** WBS ID による横断照会（WBS-07。RULE-WBS-008） */
export interface WbsTrace {
  project_id: number;
  query: string;
  wbs_code: string;
  element_id: number;
  /** CURRENT＝今の ID、HISTORY＝新旧対応の旧 ID から解決した */
  resolved_by: string;
  restricted: boolean;
  elements: TraceElement[];
  activity_ids: number[];
  estimate_record_ids: number[];
  time_entry_ids: number[];
  actual_hours: number;
  /** 読めないとき・委託先には null */
  eac_hours: number | null;
  change_request_ids: number[];
  corrective_action_ids: number[];
}

export interface Approval {
  id: number;
  project_id: number;
  target_type: string;
  target_id: number;
  status: string;
  requested_by: number;
  requested_at: string;
  decided_by: number | null;
  decided_at: string | null;
  comment: string | null;
}

export interface ApprovalSubmitted extends Approval {
  wbs_status: string;
}

export function getWbsTree(projectId: number): Promise<WbsTree> {
  return apiFetch<WbsTree>(`/projects/${projectId}/wbs`);
}

export function createElement(projectId: number, input: ElementCreateInput): Promise<WbsElement> {
  return apiFetch<WbsElement>(`/projects/${projectId}/wbs/elements`, { method: "POST", body: input });
}

export function updateElement(projectId: number, elementId: number, input: ElementUpdateInput): Promise<WbsElement> {
  return apiFetch<WbsElement>(`/projects/${projectId}/wbs/elements/${elementId}`, { method: "PATCH", body: input });
}

/** 要素を移動する（API-WBS-004）。position は移動先の兄弟の中の位置（0 から）。null は末尾 */
export function moveElement(
  projectId: number,
  elementId: number,
  parentId: number | null,
  position: number | null,
): Promise<WbsElement> {
  const body = position === null ? { parent_id: parentId } : { parent_id: parentId, position };
  return apiFetch<WbsElement>(`/projects/${projectId}/wbs/elements/${elementId}/move`, { method: "POST", body });
}

export async function deleteElement(projectId: number, elementId: number): Promise<void> {
  await apiFetch<null>(`/projects/${projectId}/wbs/elements/${elementId}`, { method: "DELETE" });
}

export function replaceNumberingRule(projectId: number, rule: NumberingRule): Promise<NumberingRule> {
  return apiFetch<NumberingRule>(`/projects/${projectId}/wbs/numbering-rule`, { method: "PUT", body: rule });
}

export function renumberWbs(projectId: number): Promise<RenumberResult> {
  return apiFetch<RenumberResult>(`/projects/${projectId}/wbs/renumber`, { method: "POST" });
}

export function listCodeHistory(projectId: number, query: PageQuery = {}): Promise<CodeHistoryList> {
  return apiFetch<CodeHistoryList>(`/projects/${projectId}/wbs/code-history`, {
    query: { limit: query.limit, offset: query.offset },
  });
}

export function getDictionary(projectId: number, elementId: number): Promise<Dictionary> {
  return apiFetch<Dictionary>(`/projects/${projectId}/wbs/elements/${elementId}/dictionary`);
}

export function replaceDictionary(projectId: number, elementId: number, input: DictionaryInput): Promise<Dictionary> {
  return apiFetch<Dictionary>(`/projects/${projectId}/wbs/elements/${elementId}/dictionary`, {
    method: "PUT",
    body: input,
  });
}

export function getResponsibilityMatrix(projectId: number): Promise<ResponsibilityMatrix> {
  return apiFetch<ResponsibilityMatrix>(`/projects/${projectId}/wbs/responsibility-matrix`, {
    query: { format: "json" },
  });
}

/** 責任分担表の CSV（API-WBS-011 の format=csv） */
export function downloadResponsibilityMatrixCsv(projectId: number): Promise<DownloadedFile> {
  return apiDownload(`/projects/${projectId}/wbs/responsibility-matrix`, { query: { format: "csv" } });
}

export function getValidation(projectId: number): Promise<Validation> {
  return apiFetch<Validation>(`/projects/${projectId}/wbs/validation`);
}

export function replaceScopeLinks(projectId: number, elementId: number, scopeItemIds: number[]): Promise<ScopeLinks> {
  return apiFetch<ScopeLinks>(`/projects/${projectId}/wbs/elements/${elementId}/scope-links`, {
    method: "PUT",
    body: { scope_item_ids: scopeItemIds },
  });
}

export function startReview(projectId: number, reviewerIds: number[]): Promise<Review> {
  return apiFetch<Review>(`/projects/${projectId}/wbs/reviews`, { method: "POST", body: { reviewer_ids: reviewerIds } });
}

export function getReview(projectId: number, reviewId: number): Promise<Review> {
  return apiFetch<Review>(`/projects/${projectId}/wbs/reviews/${reviewId}`);
}

export function addReviewComment(projectId: number, reviewId: number, input: CommentCreateInput): Promise<ReviewComment> {
  return apiFetch<ReviewComment>(`/projects/${projectId}/wbs/reviews/${reviewId}/comments`, {
    method: "POST",
    body: input,
  });
}

export function updateReviewComment(
  projectId: number,
  reviewId: number,
  commentId: number,
  input: CommentUpdateInput,
): Promise<ReviewComment> {
  return apiFetch<ReviewComment>(`/projects/${projectId}/wbs/reviews/${reviewId}/comments/${commentId}`, {
    method: "PATCH",
    body: input,
  });
}

export function requestWbsApproval(projectId: number): Promise<ApprovalSubmitted> {
  return apiFetch<ApprovalSubmitted>(`/projects/${projectId}/wbs/approval-requests`, { method: "POST" });
}

export function listScopeBaselines(projectId: number, query: PageQuery = {}): Promise<ScopeBaselineList> {
  return apiFetch<ScopeBaselineList>(`/projects/${projectId}/scope-baselines`, {
    query: { limit: query.limit, offset: query.offset },
  });
}

export function getScopeBaseline(projectId: number, baselineId: number): Promise<ScopeBaseline> {
  return apiFetch<ScopeBaseline>(`/projects/${projectId}/scope-baselines/${baselineId}`);
}

/** WBS ID（今の ID か旧 ID）で横断照会する（API-WBS-023）。ID はパスの 1 区切りとして符号化する */
export function traceWbsCode(projectId: number, wbsCode: string): Promise<WbsTrace> {
  return apiFetch<WbsTrace>(`/projects/${projectId}/wbs-codes/${encodeURIComponent(wbsCode)}/trace`);
}
