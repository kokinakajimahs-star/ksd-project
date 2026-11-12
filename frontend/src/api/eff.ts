// 工数の API の型と呼び出し（設計書 6.2 の API-EFF-001〜007）。
// タスクリスト（UI-SCH-001）が 001 を、タイムシート（UI-EFF-001）が 002〜004 を、工数の集計と差異（UI-EFF-002）が 005〜007 を使う。
// 形はサーバの schemas（backend/app/schemas/effort.py）に合わせる。工数（人時）は数で受け渡す
import { apiFetch } from "./client";
import type { Page, PageQuery } from "./cmn";

export interface AssignmentInput {
  user_id: number;
  /** 割当工数（人時）。空は未定 */
  planned_hours: number | null;
}

export interface Assignment {
  activity_id: number;
  activity_name: string;
  wbs_code: string;
  user_id: number;
  display_name: string;
  planned_hours: number | null;
}

/** 担当の置き換えの結果。割当工数の合計と見積との差（EFF-01）を付ける */
export interface ActivityAssignments {
  activity_id: number;
  estimate_hours: number | null;
  planned_hours_total: number;
  difference_hours: number | null;
  assignments: Assignment[];
}

export function replaceAssignments(
  projectId: number,
  activityId: number,
  assignments: AssignmentInput[],
): Promise<ActivityAssignments> {
  return apiFetch<ActivityAssignments>(`/projects/${projectId}/activities/${activityId}/assignments`, {
    method: "PUT",
    body: { assignments },
  });
}

export interface AssignmentQuery extends PageQuery {
  user_id?: number;
  activity_id?: number;
}

/** 割当の一覧（API-EFF-002。権限は project.view）。並びはガントの行の順→利用者 */
export function listAssignments(projectId: number, query: AssignmentQuery = {}): Promise<Page<Assignment>> {
  return apiFetch<Page<Assignment>>(`/projects/${projectId}/assignments`, {
    query: { user_id: query.user_id, activity_id: query.activity_id, limit: query.limit, offset: query.offset },
  });
}

// ---------------------------------------------------------------------------
// 実績工数（API-EFF-003・004）
// ---------------------------------------------------------------------------

export interface TimeEntry {
  id: number;
  user_id: number;
  project_id: number;
  activity_id: number;
  activity_name: string;
  wbs_code: string;
  /** 作業日（YYYY-MM-DD） */
  work_date: string;
  hours: number;
  note: string | null;
}

/** 1 マス（日×活動）。hours で置き換える（0 は行を消す。0 のマスにメモは書けない） */
export interface TimeEntryCellInput {
  activity_id: number;
  work_date: string;
  hours: number;
  note: string | null;
}

export interface DayTotal {
  work_date: string;
  hours: number;
}

/** 書いた後の、入力のあった日の自分の行と 1 日の合計。changed は作った・変えた・消した行の数 */
export interface TimeEntriesWritten {
  user_id: number;
  changed: number;
  entries: TimeEntry[];
  day_totals: DayTotal[];
}

export interface TimeEntryQuery extends PageQuery {
  user_id?: number;
  project_id?: number;
  /** 作業日の範囲（両端を含む。YYYY-MM-DD） */
  from?: string;
  to?: string;
}

/** 自分の実績工数をまとめて書く（API-EFF-003。権限は effort.enter_own）。1 件でも問題があれば何も書かない */
export function putTimeEntries(entries: TimeEntryCellInput[]): Promise<TimeEntriesWritten> {
  return apiFetch<TimeEntriesWritten>("/time-entries", { method: "PUT", body: { entries } });
}

/** 実績工数を読む（API-EFF-004。本人の行は effort.enter_own、他人の行は effort.view）。並びは作業日→活動→利用者 */
export function listTimeEntries(query: TimeEntryQuery = {}): Promise<Page<TimeEntry>> {
  return apiFetch<Page<TimeEntry>>("/time-entries", {
    query: {
      user_id: query.user_id,
      project_id: query.project_id,
      from: query.from,
      to: query.to,
      limit: query.limit,
      offset: query.offset,
    },
  });
}

// ---------------------------------------------------------------------------
// 集計・差異（API-EFF-005〜007）
// ---------------------------------------------------------------------------

/** 集計の軸（API-EFF-005 の axis） */
export type EffortAxis = "wbs" | "member" | "organization" | "week" | "month";

/** 計画（今の見積）・基準（基準に無ければ null）・実績・ETC・EAC（EFF-08 の計画値と予測値を別に持つ） */
export interface EffortValues {
  planned: number;
  baseline: number | null;
  actual: number;
  etc: number;
  eac: number;
}

export interface EffortReportRef {
  id: number;
  report_date: string;
  status: string;
}

export interface EffortBaselineRef {
  id: number;
  version: number;
  name: string | null;
}

/**
 * 集計の 1 つの桶。軸ごとに意味のある列だけが埋まる。
 * - wbs：element_id・parent_id・wbs_code・name・kind（WBS の並び）
 * - member：user_id（null は「未割当」）・name（表示名）
 * - organization：bucket_kind（ORGANIZATION／NO_ORGANIZATION／UNASSIGNED）・name（所属）
 * - week・month：bucket_kind（PERIOD／REMAINING／UNSCHEDULED）・period_start（PERIOD の初日）
 */
export interface SummaryRow {
  bucket_kind: string;
  element_id: number | null;
  parent_id: number | null;
  wbs_code: string | null;
  kind: string | null;
  user_id: number | null;
  name: string | null;
  period_start: string | null;
  values: EffortValues;
}

export interface EffortSummary {
  axis: EffortAxis;
  report: EffortReportRef | null;
  report_specified: boolean;
  baseline: EffortBaselineRef | null;
  total: EffortValues;
  rows: SummaryRow[];
}

export interface VarianceNote {
  id: number;
  element_id: number;
  wbs_code: string;
  status_report_id: number | null;
  reason: string;
  corrective_action_id: number | null;
  created_by: number;
  created_at: string;
}

/** 閾値を超えた要素（EXCEEDED）か基準に無い要素（ADDED）。rate は差異÷基準の割合（追加・基準 0 は null） */
export interface EffortVarianceRow {
  element_id: number;
  parent_id: number | null;
  wbs_code: string;
  name: string;
  kind: string;
  baseline: number | null;
  eac: number;
  variance: number | null;
  rate: number | null;
  status: string;
  note: VarianceNote | null;
}

export interface EffortVariance {
  report: EffortReportRef | null;
  report_specified: boolean;
  baseline: EffortBaselineRef;
  /** 差異率の閾値（割合。0.1＝10%） */
  rate_threshold: number;
  /** 差異の絶対値の閾値（人時） */
  hours_threshold: number;
  items: EffortVarianceRow[];
}

export interface EffortBasisQuery {
  /** 報告日（省けば最新の報告日の進捗率と全ての実績） */
  report_id?: number;
  /** スケジュールベースライン（省けば最新の版） */
  baseline_id?: number;
}

export interface EffortSummaryQuery extends EffortBasisQuery {
  axis: EffortAxis;
}

/** 多軸集計（API-EFF-005。権限は effort.view）。どの軸でも桶の合計は total に一致する（EFF-09） */
export function getEffortSummary(projectId: number, query: EffortSummaryQuery): Promise<EffortSummary> {
  return apiFetch<EffortSummary>(`/projects/${projectId}/effort/summary`, {
    query: { axis: query.axis, report_id: query.report_id, baseline_id: query.baseline_id },
  });
}

/** 差異の分析（API-EFF-006。権限は effort.view）。スケジュールベースラインが無ければ 409 */
export function getEffortVariance(projectId: number, query: EffortBasisQuery = {}): Promise<EffortVariance> {
  return apiFetch<EffortVariance>(`/projects/${projectId}/effort/variance`, {
    query: { report_id: query.report_id, baseline_id: query.baseline_id },
  });
}

/** 差異の理由（API-EFF-007）。status_report_id の null は報告日を指定しない分析の理由 */
export interface VarianceNoteInput {
  status_report_id: number | null;
  reason: string;
  corrective_action_id: number | null;
}

/** 差異の理由と是正措置の紐付けを書く（API-EFF-007。権限は variance.note）。要素・報告日ごとに 1 行を上書きする */
export function putVarianceNote(projectId: number, elementId: number, input: VarianceNoteInput): Promise<VarianceNote> {
  return apiFetch<VarianceNote>(`/projects/${projectId}/effort/variance/${elementId}/note`, {
    method: "PUT",
    body: input,
  });
}
