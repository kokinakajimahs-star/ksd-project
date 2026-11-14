// スケジュールの API の型と呼び出し（設計書 6.2 の API-SCH-001〜013）。
// 形はサーバの schemas（backend/app/schemas/schedule.py）に合わせる。日付は YYYY-MM-DD、工数は人時の数
import { apiFetch } from "./client";
import type { PageQuery } from "./cmn";

export interface Assignee {
  user_id: number;
  display_name: string;
  planned_hours: number | null;
}

/** 活動 1 件（API-SCH-001〜004）。start_date 以降は日程計算の結果（設計書 4.2） */
export interface Activity {
  id: number;
  project_id: number;
  work_package_id: number;
  /** 置き場の WP の WBS ID */
  wbs_code: string;
  work_package_name: string;
  name: string;
  sort_order: number;
  requested_start: string | null;
  duration_days: number;
  is_milestone: boolean;
  estimate_hours: number | null;
  progress_method: string | null;
  start_date: string | null;
  finish_date: string | null;
  early_start: string | null;
  early_finish: string | null;
  late_start: string | null;
  late_finish: string | null;
  /** トータルフロート（稼働日） */
  total_float: number | null;
  is_critical: boolean;
  predecessor_ids: number[];
  successor_ids: number[];
  assignees: Assignee[];
}

export interface Dependency {
  id: number;
  predecessor_id: number;
  successor_id: number;
  dep_type: string;
  lag_days: number;
}

export interface ActivityList {
  items: Activity[];
  total: number;
  limit: number;
  offset: number;
  /** 読める活動どうしの依存（API-SCH-007 で消すときの ID を持つ） */
  dependencies: Dependency[];
  /** 委託先など、自分の割当の活動だけが見えている */
  restricted: boolean;
}

/** 活動を足す（API-SCH-002）。開始日・終了日・期間のうち 2 つ（期間だけでもよい。SCH-03） */
export interface ActivityCreateInput {
  work_package_id: number;
  name: string;
  requested_start?: string | null;
  requested_finish?: string | null;
  duration_days?: number | null;
  is_milestone?: boolean | null;
}

/** 載せた項目だけを変える（API-SCH-003） */
export interface ActivityUpdateInput {
  name?: string;
  requested_start?: string | null;
  requested_finish?: string | null;
  duration_days?: number;
  is_milestone?: boolean;
  /** 移す先の WP（CR-024） */
  work_package_id?: number;
  /** WP の中の位置（0 から。CR-024） */
  position?: number;
  /** 今の予定開始日からこの日までを期間にする（日付・期間・マイルストーンの項目と一緒には送らない。CR-024） */
  planned_finish?: string;
}

/** 一括更新の 1 件（API-SCH-004）。載せた項目だけを変える。先行・担当は全体の置き換え */
export interface ActivityChange {
  activity_id: number;
  name?: string;
  requested_start?: string | null;
  duration_days?: number;
  predecessor_ids?: number[];
  assignee_user_ids?: number[];
}

export interface DependencyCreateInput {
  predecessor_id: number;
  successor_id: number;
}

export interface ScheduledActivity {
  activity_id: number;
  name: string;
  wbs_code: string;
  is_milestone: boolean;
  start_date: string;
  finish_date: string;
  early_start: string;
  early_finish: string;
  late_start: string;
  late_finish: string;
  total_float: number;
  is_critical: boolean;
}

/** 日程計算（API-SCH-008）とクリティカルパス（API-SCH-009）の結果 */
export interface ScheduleResult {
  project_finish_date: string | null;
  critical_path: number[];
  min_total_float: number | null;
  activities: ScheduledActivity[];
  restricted: boolean;
}

/** 暦日上の座標：日付と、その日の中の割合（設計書 7.3） */
export interface CalendarPoint {
  date: string;
  fraction: number;
}

/** 稼働日の軸の上の位置（設計書 5.4）と、その暦日上の座標（7.3） */
export interface AxisPoint {
  position: number;
  calendar: CalendarPoint;
}

export type BarKind = "BASELINE" | "PLANNED" | "ACTUAL";

export interface GanttBar {
  kind: BarKind;
  is_milestone: boolean;
  start: AxisPoint;
  end: AxisPoint;
  start_date: string;
  end_date: string;
  /** 実績が未完了で、報告日までのバー */
  open_ended: boolean;
}

export interface GanttRow {
  row_index: number;
  activity_id: number;
  name: string;
  work_package_id: number;
  wbs_code: string;
  work_package_name: string;
  depth: number;
  is_milestone: boolean;
  is_critical: boolean;
  total_float: number | null;
  planned: GanttBar | null;
  actual: GanttBar | null;
  baseline: GanttBar | null;
  assignees: string[];
  percent_complete: number | null;
}

export interface GanttLink {
  predecessor_id: number;
  successor_id: number;
  from_row: number;
  to_row: number;
  start: AxisPoint | null;
  end: AxisPoint | null;
}

export interface BaselineRef {
  id: number;
  version: number;
  name: string;
}

/** ガントの描画データ（API-SCH-010。RULE-SCH-005）。行の順はサーバの順（7.3） */
export interface Gantt {
  project_start: string;
  report_date: string | null;
  baseline: BaselineRef | null;
  /** バーの範囲（バーが無ければ null）。表示範囲を渡しても変わらない */
  range_start: string | null;
  range_end: string | null;
  /** 表示範囲（渡さなければバーの範囲）の非稼働日 */
  non_working_days: string[];
  rows: GanttRow[];
  links: GanttLink[];
  restricted: boolean;
}

export interface ScheduleBaseline {
  id: number;
  project_id: number;
  version: number;
  name: string;
  reason: string;
  change_request_id: number | null;
  created_by: number;
  created_at: string;
  item_count: number;
}

export interface ScheduleBaselineList {
  items: ScheduleBaseline[];
  total: number;
  limit: number;
  offset: number;
}

export type VarianceStatus = "MATCHED" | "ADDED" | "DELETED";

/** 1 つの活動の差異（差は「最新 − 基準」。日付の差は稼働日、見積は人時） */
export interface VarianceRow {
  status: VarianceStatus;
  activity_id: number | null;
  wbs_code: string;
  name: string;
  baseline_start: string | null;
  baseline_finish: string | null;
  current_start: string | null;
  current_finish: string | null;
  start_variance_days: number | null;
  finish_variance_days: number | null;
  baseline_estimate_hours: number | null;
  current_estimate_hours: number | null;
  estimate_variance_hours: number | null;
}

export interface Variance {
  baseline: BaselineRef;
  rows: VarianceRow[];
  restricted: boolean;
}

function pageQuery(query: PageQuery) {
  return { limit: query.limit, offset: query.offset };
}

export function listActivities(projectId: number, query: PageQuery = {}): Promise<ActivityList> {
  return apiFetch<ActivityList>(`/projects/${projectId}/activities`, { query: pageQuery(query) });
}

export function createActivity(projectId: number, input: ActivityCreateInput): Promise<Activity> {
  return apiFetch<Activity>(`/projects/${projectId}/activities`, { method: "POST", body: input });
}

export function updateActivity(projectId: number, activityId: number, input: ActivityUpdateInput): Promise<Activity> {
  return apiFetch<Activity>(`/projects/${projectId}/activities/${activityId}`, { method: "PATCH", body: input });
}

export function bulkUpdateActivities(projectId: number, changes: ActivityChange[]): Promise<{ items: Activity[] }> {
  return apiFetch<{ items: Activity[] }>(`/projects/${projectId}/activities`, { method: "PATCH", body: { changes } });
}

export async function deleteActivity(projectId: number, activityId: number): Promise<void> {
  await apiFetch<null>(`/projects/${projectId}/activities/${activityId}`, { method: "DELETE" });
}

export function createDependency(projectId: number, input: DependencyCreateInput): Promise<Dependency> {
  return apiFetch<Dependency>(`/projects/${projectId}/dependencies`, { method: "POST", body: input });
}

export async function deleteDependency(projectId: number, dependencyId: number): Promise<void> {
  await apiFetch<null>(`/projects/${projectId}/dependencies/${dependencyId}`, { method: "DELETE" });
}

export function calculateSchedule(projectId: number): Promise<ScheduleResult> {
  return apiFetch<ScheduleResult>(`/projects/${projectId}/schedule/calculate`, { method: "POST" });
}

export function getCriticalPath(projectId: number): Promise<ScheduleResult> {
  return apiFetch<ScheduleResult>(`/projects/${projectId}/schedule/critical-path`);
}

/** ガントの表示範囲（暦日。両端を含む） */
export interface GanttDisplayRange {
  start: string;
  end: string;
}

/**
 * baselineId を省けば最新の版（版が無ければ基準のバーは無い）。range を渡せば、非稼働日はその表示範囲の分を返す
 * （省けばバーの範囲。応答の range_start・range_end はどちらでもバーの範囲。設計書 6.2・7.3）
 */
export function getGantt(projectId: number, baselineId?: number, range?: GanttDisplayRange): Promise<Gantt> {
  return apiFetch<Gantt>(`/projects/${projectId}/gantt`, {
    query: { baseline_id: baselineId, range_start: range?.start, range_end: range?.end },
  });
}

export function createScheduleBaseline(projectId: number, name: string): Promise<ScheduleBaseline> {
  return apiFetch<ScheduleBaseline>(`/projects/${projectId}/schedule-baselines`, { method: "POST", body: { name } });
}

export function listScheduleBaselines(projectId: number, query: PageQuery = {}): Promise<ScheduleBaselineList> {
  return apiFetch<ScheduleBaselineList>(`/projects/${projectId}/schedule-baselines`, { query: pageQuery(query) });
}

export function getScheduleVariance(projectId: number, baselineId: number): Promise<Variance> {
  return apiFetch<Variance>(`/projects/${projectId}/schedule-baselines/${baselineId}/variance`);
}
