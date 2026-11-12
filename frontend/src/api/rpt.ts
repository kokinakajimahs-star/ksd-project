// レポート・共有の API の型と呼び出し（設計書 6.2 の API-RPT-001〜005）。
// 定例会議レポート（UI-RPT-001）が 001〜003・005 を、共有ビュー（UI-RPT-002）が 004 を使う。
// 形はサーバの schemas（backend/app/schemas/report.py）に合わせる。日付は YYYY-MM-DD、時刻は ISO 8601（UTC）、工数は人時の数
import { apiFetch } from "./client";
import type { Page, PageQuery } from "./cmn";
import type { OverlaidVertex, StatusReportStatus } from "./prg";
import type { AxisPoint, Gantt } from "./sch";

/** 上段のガントの表示の範囲（報告日の前後 4 週。暦日。両端を含む） */
export interface ReportWindow {
  start: string;
  end: string;
}

/** 上段のイナズマ線：報告日の値を今のガントの行へ置き直したもの。missing_activity_ids は今のガントに無い活動 */
export interface ReportLightning {
  report_date: string;
  report_line: AxisPoint;
  tolerance_days: number;
  vertices: OverlaidVertex[];
  missing_activity_ids: (number | null)[];
}

/** 下段左：遅延一覧の 1 行（ずれの小さい順＝遅れの大きい順）。forecast_entered は完了見込み日が入力の値か */
export interface ReportDelay {
  activity_id: number;
  wbs_code: string;
  name: string;
  deviation: number;
  percent_complete: number | null;
  planned_finish: string;
  forecast_finish: string;
  forecast_entered: boolean;
  delay_reason: string | null;
  is_critical: boolean;
}

/** 下段中：マイルストーンの状況の 1 行。slip_days は見込み日 − 予定日（稼働日） */
export interface ReportMilestone {
  activity_id: number | null;
  wbs_code: string;
  name: string;
  planned_date: string;
  forecast_date: string;
  slip_days: number;
}

/** 下段右：主要 KPI。求められない値は null */
export interface ReportKpi {
  overall_progress: number | null;
  behind_count: number;
  eac_hours: number | null;
  baseline_hours: number | null;
  eac_variance_hours: number | null;
  min_critical_float: number | null;
  overdue_action_count: number;
}

export type ReportSource = "SNAPSHOT" | "CALCULATED";

/** 定例会議レポートのデータ（API-RPT-001。RULE-RPT-001・設計書 7.4） */
export interface MeetingReport {
  report_id: number;
  report_date: string;
  status: StatusReportStatus;
  /** 値の源：確定済みの写し（SNAPSHOT）か、確定前の計算値（CALCULATED） */
  source: ReportSource;
  project_start: string;
  window: ReportWindow;
  gantt: Gantt;
  lightning: ReportLightning;
  delays: ReportDelay[];
  milestones: ReportMilestone[];
  /** 委託先だけで読む利用者（restricted）には返らない */
  kpi: ReportKpi | null;
  restricted: boolean;
}

/** 共有リンクで見せる範囲（4.1 の share_links.scope） */
export type ShareScope = "GANTT" | "REPORT";

export interface ShareLinkInput {
  scope: ShareScope;
  /** 時差つきの時刻で、今より後 */
  expires_at: string;
}

/** 共有リンク 1 件（トークンは含まない） */
export interface ShareLink {
  id: number;
  project_id: number;
  scope: ShareScope;
  expires_at: string;
  revoked_at: string | null;
  created_by: number;
  created_at: string;
}

/** 共有リンクの一覧（API-RPT-005。新しい順。失効・期限切れも含む。トークンは含まない） */
export interface ShareLinkList extends Page<ShareLink> {
  project_id: number;
}

/** 作った共有リンク。token は作った応答でだけ返る（サーバはハッシュだけを持つ） */
export interface ShareLinkCreated extends ShareLink {
  token: string;
}

export interface SharedProject {
  id: number;
  code: string;
  name: string;
}

/** 共有リンクで見せるもの（API-RPT-004。読むだけ）。GANTT なら gantt、REPORT なら meeting_report（報告日が無ければ null） */
export interface SharedView {
  scope: ShareScope;
  expires_at: string;
  project: SharedProject;
  gantt: Gantt | null;
  meeting_report: MeetingReport | null;
}

/** 共有リンクを閲覧できない理由（API-RPT-004 の 404 の details の reason） */
export type ShareLinkFailure = "NOT_FOUND" | "REVOKED" | "EXPIRED";

export function getMeetingReport(projectId: number, reportId: number): Promise<MeetingReport> {
  return apiFetch<MeetingReport>(`/projects/${projectId}/status-reports/${reportId}/meeting-report`);
}

export function createShareLink(projectId: number, input: ShareLinkInput): Promise<ShareLinkCreated> {
  return apiFetch<ShareLinkCreated>(`/projects/${projectId}/share-links`, { method: "POST", body: input });
}

export function listShareLinks(projectId: number, query: PageQuery = {}): Promise<ShareLinkList> {
  return apiFetch<ShareLinkList>(`/projects/${projectId}/share-links`, {
    query: { limit: query.limit, offset: query.offset },
  });
}

export async function revokeShareLink(projectId: number, linkId: number): Promise<void> {
  await apiFetch<null>(`/projects/${projectId}/share-links/${linkId}`, { method: "DELETE" });
}

/** 共有リンクで閲覧する（認証は要らない。6.1）。トークンは URL のパスに入れるため符号化する */
export function getSharedView(token: string): Promise<SharedView> {
  return apiFetch<SharedView>(`/shared/${encodeURIComponent(token)}`);
}
