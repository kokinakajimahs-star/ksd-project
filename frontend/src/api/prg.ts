// 進捗の API の型と呼び出し（設計書 6.2 の API-PRG-001〜009）。
// ガント（UI-SCH-002）が 002・006・008 を、進捗入力（UI-PRG-001）が 001〜007 を、遅延分析（UI-PRG-002）が 009 を使う。
// 形はサーバの schemas（backend/app/schemas/progress.py）に合わせる
import { apiFetch } from "./client";
import type { PageQuery } from "./cmn";
import type { ProgressMethod } from "./prj";
import type { AxisPoint } from "./sch";

export type StatusReportStatus = "OPEN" | "FINALIZED";

export interface StatusReport {
  id: number;
  project_id: number;
  report_date: string;
  status: StatusReportStatus;
  created_by: number;
  created_at: string;
  finalized_by: number | null;
  finalized_at: string | null;
  entry_count: number;
  snapshot_id: number | null;
}

export interface StatusReportList {
  items: StatusReport[];
  total: number;
  limit: number;
  offset: number;
}

/** 活動ごとの判定（設計書 5.6・BR-08・BR-09） */
export type Judgement = "COMPLETED" | "NOT_STARTED" | "BEHIND" | "AHEAD" | "ON_SCHEDULE" | "NOT_COMPUTED";

/** イナズマ線の 1 行（ガントの行の順）。point は進捗ポイント、deviation はずれ（point − 報告日線） */
export interface LightningRow {
  row_index: number;
  activity_id: number | null;
  wbs_code: string;
  name: string;
  is_milestone: boolean;
  is_critical: boolean;
  start_date: string | null;
  finish_date: string | null;
  duration_days: number | null;
  progress_method: string | null;
  percent_complete: number | null;
  actual_start: string | null;
  actual_finish: string | null;
  remaining_hours: number | null;
  delay_reason: string | null;
  forecast_finish: string | null;
  point: AxisPoint;
  deviation: number;
  judgement: Judgement;
  /** 判定の上で数える値（完了・未着手（予定内）は予定どおり） */
  counted_as: Judgement;
  display_date: string;
}

/** 報告日のイナズマ線（API-PRG-006） */
export interface LightningLine {
  report: StatusReport;
  source: "CALCULATED" | "SNAPSHOT";
  project_start: string;
  /** 報告日線（報告日の終業の位置。5.4） */
  report_line: AxisPoint;
  tolerance_days: number;
  rows: LightningRow[];
  restricted: boolean;
}

export interface OverlaidVertex {
  /** 今のガントの行の番号（RULE-SCH-005 の順） */
  row_index: number;
  activity_id: number | null;
  point: AxisPoint;
  deviation: number;
  judgement: Judgement;
  display_date: string;
}

/** 重ねる 1 本の線（PRG-18）。style_index は線の種類（報告日の古い順に 0, 1, …） */
export interface OverlaidLine {
  style_index: number;
  report_id: number;
  report_date: string;
  report_line: AxisPoint;
  tolerance_days: number;
  vertices: OverlaidVertex[];
  missing_activity_ids: (number | null)[];
}

export interface LightningLines {
  project_start: string;
  lines: OverlaidLine[];
  restricted: boolean;
}

export function listStatusReports(projectId: number, query: PageQuery = {}): Promise<StatusReportList> {
  return apiFetch<StatusReportList>(`/projects/${projectId}/status-reports`, {
    query: { limit: query.limit, offset: query.offset },
  });
}

export function getLightningLine(projectId: number, reportId: number): Promise<LightningLine> {
  return apiFetch<LightningLine>(`/projects/${projectId}/status-reports/${reportId}/lightning-line`);
}

/** 確定済みの複数の報告日の線（API-PRG-008） */
export function getLightningLines(projectId: number, reportIds: number[]): Promise<LightningLines> {
  return apiFetch<LightningLines>(`/projects/${projectId}/lightning-lines`, { query: { report_ids: reportIds } });
}

// ---------------------------------------------------------------------------
// 報告日を作る・確定する（API-PRG-001・007）
// ---------------------------------------------------------------------------

export function createStatusReport(projectId: number, reportDate: string): Promise<StatusReport> {
  return apiFetch<StatusReport>(`/projects/${projectId}/status-reports`, {
    method: "POST",
    body: { report_date: reportDate },
  });
}

/** 確定の結果：確定した報告日と、作ったスナップショットの要約 */
export interface FinalizeResult {
  report: StatusReport;
  snapshot_id: number;
  report_line: number;
  tolerance_days: number;
  extract_days: number;
  plan_basis: string;
  item_count: number;
}

export function finalizeStatusReport(projectId: number, reportId: number): Promise<FinalizeResult> {
  return apiFetch<FinalizeResult>(`/projects/${projectId}/status-reports/${reportId}/finalize`, { method: "POST" });
}

// ---------------------------------------------------------------------------
// 進捗の入力（API-PRG-003）
// ---------------------------------------------------------------------------

/** PUT なので全ての項目を置き換える（載せない項目は空になる）。進捗率は直接入力（DIRECT）の活動だけが持てる */
export interface ProgressEntryInput {
  percent_complete: number | null;
  actual_start: string | null;
  actual_finish: string | null;
  /** 空は自動（BR-10。EFF-06） */
  remaining_hours: number | null;
  delay_reason: string | null;
  forecast_finish: string | null;
}

export interface ProgressEntry {
  status_report_id: number;
  activity_id: number;
  progress_method: ProgressMethod;
  percent_complete: number | null;
  actual_start: string | null;
  actual_finish: string | null;
  remaining_hours: number | null;
  remaining_hours_source: "ENTERED" | "AUTO";
  delay_reason: string | null;
  forecast_finish: string | null;
  entered_by: number;
  entered_at: string;
}

export function putProgress(
  projectId: number,
  reportId: number,
  activityId: number,
  input: ProgressEntryInput,
): Promise<ProgressEntry> {
  return apiFetch<ProgressEntry>(`/projects/${projectId}/status-reports/${reportId}/progress/${activityId}`, {
    method: "PUT",
    body: input,
  });
}

// ---------------------------------------------------------------------------
// 算定項目・算定方式（API-PRG-004・005）
// ---------------------------------------------------------------------------

/** 進捗の算定項目の種類（4.1 の progress_items.item_type） */
export type ProgressItemType = "DELIVERABLE" | "WORK" | "CHECK" | "MILESTONE";

/** 算定項目 1 件。id を載せればその項目を変え、載せなければ新しい項目 */
export interface ProgressItemInput {
  id?: number;
  item_type: ProgressItemType;
  title: string;
  weight: number;
  done: boolean;
  approved: boolean;
}

export interface ProgressItem {
  id: number;
  item_type: ProgressItemType;
  title: string;
  weight: number;
  done: boolean;
  approved: boolean;
  done_at: string | null;
}

export interface ProgressItems {
  activity_id: number;
  progress_method: ProgressMethod;
  percent_complete: number | null;
  items: ProgressItem[];
}

/** 活動の算定項目を置き換える。載せなかった既存の項目は消える */
export function replaceProgressItems(
  projectId: number,
  activityId: number,
  items: ProgressItemInput[],
): Promise<ProgressItems> {
  return apiFetch<ProgressItems>(`/projects/${projectId}/activities/${activityId}/progress-items`, {
    method: "PUT",
    body: { items },
  });
}

export interface ProgressMethodResult {
  activity_id: number;
  /** 活動に置いた方式（空はプロジェクトの既定） */
  progress_method: ProgressMethod | null;
  effective_method: ProgressMethod;
  project_default: ProgressMethod;
}

/** 活動の算定方式を変える。null はプロジェクトの既定に従う */
export function setProgressMethod(
  projectId: number,
  activityId: number,
  method: ProgressMethod | null,
): Promise<ProgressMethodResult> {
  return apiFetch<ProgressMethodResult>(`/projects/${projectId}/activities/${activityId}/progress-method`, {
    method: "PUT",
    body: { progress_method: method },
  });
}

// ---------------------------------------------------------------------------
// 遅延（API-PRG-009）
// ---------------------------------------------------------------------------

/** 活動（マイルストーン）の終わりのずれ。before・after は軸の位置、日付は終了日 */
export interface Slip {
  activity_id: number;
  name: string;
  is_milestone: boolean;
  before: number;
  after: number;
  before_date: string;
  after_date: string;
  slip_days: number;
}

export interface FinishSlip {
  before_date: string | null;
  after_date: string | null;
  slip_days: number;
}

/** 抽出した遅延活動 1 件とその個別の影響（設計書 5.7 の 2・3） */
export interface Delay {
  activity_id: number;
  wbs_code: string;
  name: string;
  deviation: number;
  percent_complete: number | null;
  delay_reason: string | null;
  /** 入力した完了見込み日（PRG-03） */
  forecast_finish: string | null;
  /** 見込みの終了（軸の位置と日付） */
  expected_finish: number;
  expected_finish_date: string;
  fixed_finish: Slip;
  is_critical: boolean;
  transitive_successor_ids: number[];
  successor_slips: Slip[];
  milestone_slips: Slip[];
  /** 委託先には返さない（null） */
  project_finish: FinishSlip | null;
}

/** 遅延活動と影響。delays はずれの小さい順（遅れの大きい順）。combined_* は全ての見込みを同時に当てた結果 */
export interface Delays {
  report: StatusReport;
  source: "CALCULATED" | "SNAPSHOT";
  report_line: number;
  extract_days: number;
  delays: Delay[];
  combined_milestone_slips: Slip[];
  combined_project_finish: FinishSlip | null;
  restricted: boolean;
}

export function getDelays(projectId: number, reportId: number): Promise<Delays> {
  return apiFetch<Delays>(`/projects/${projectId}/status-reports/${reportId}/delays`);
}
