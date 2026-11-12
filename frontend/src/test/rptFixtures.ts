// 試験用のデータ：レポート・共有の応答の形（backend/app/schemas/report.py の MeetingReportOut・ShareLinkOut・ShareLinkList・
// ShareLinkCreated・SharedViewOut）
import type {
  MeetingReport,
  ReportDelay,
  ReportKpi,
  ReportMilestone,
  ShareLink,
  ShareLinkCreated,
  ShareLinkList,
  SharedView,
} from "../api/rpt";
import { makeBar, makeGantt, makeGanttRow, point } from "./schFixtures";

// 稲妻チャート.txt §6 の例：報告日 12/10、資料作成の進捗ポイントは 12/6 の 1/4（δ＝−4.75）
export function makeReportDelay(overrides: Partial<ReportDelay> = {}): ReportDelay {
  return {
    activity_id: 21,
    wbs_code: "1.1",
    name: "資料作成",
    deviation: -4.75,
    percent_complete: 25,
    planned_finish: "2026-12-21",
    forecast_finish: "2026-12-26",
    forecast_entered: true,
    delay_reason: "レビュー待ち",
    is_critical: true,
    ...overrides,
  };
}

export function makeReportMilestone(overrides: Partial<ReportMilestone> = {}): ReportMilestone {
  return {
    activity_id: 24,
    wbs_code: "1.3",
    name: "公開",
    planned_date: "2026-12-21",
    forecast_date: "2026-12-28",
    slip_days: 5,
    ...overrides,
  };
}

export function makeReportKpi(overrides: Partial<ReportKpi> = {}): ReportKpi {
  return {
    overall_progress: 37.5,
    behind_count: 1,
    eac_hours: 130,
    baseline_hours: 120,
    eac_variance_hours: 10,
    min_critical_float: 0,
    overdue_action_count: 2,
    ...overrides,
  };
}

/** ガントの 2 行（資料作成・公開）と、資料作成の頂点（遅れ）・公開の頂点（予定どおり）を持つ会議レポート */
export function makeMeetingReport(overrides: Partial<MeetingReport> = {}): MeetingReport {
  return {
    report_id: 41,
    report_date: "2026-12-10",
    status: "OPEN",
    source: "CALCULATED",
    project_start: "2026-12-01",
    // 報告日の前後 4 週（暦日 28 日。両端を含む）
    window: { start: "2026-11-12", end: "2027-01-07" },
    gantt: makeGantt({
      report_date: "2026-12-10",
      range_start: "2026-10-01",
      range_end: "2027-03-31",
      rows: [
        makeGanttRow({
          row_index: 0,
          activity_id: 21,
          is_critical: true,
          total_float: 0,
          planned: makeBar("PLANNED", "2026-12-01", "2026-12-21"),
          percent_complete: 25,
        }),
        makeGanttRow({
          row_index: 1,
          activity_id: 24,
          wbs_code: "1.3",
          name: "公開",
          is_milestone: true,
          planned: makeBar("PLANNED", "2026-12-21", "2026-12-21", { is_milestone: true }),
        }),
      ],
    }),
    lightning: {
      report_date: "2026-12-10",
      report_line: point("2026-12-10", 1, 10),
      tolerance_days: 0.5,
      vertices: [
        {
          row_index: 0,
          activity_id: 21,
          point: point("2026-12-06", 0.25, 5.25),
          deviation: -4.75,
          judgement: "BEHIND",
          display_date: "2026-12-06",
        },
        {
          row_index: 1,
          activity_id: 24,
          point: point("2026-12-10", 1, 10),
          deviation: 0,
          judgement: "NOT_STARTED",
          display_date: "2026-12-10",
        },
      ],
      missing_activity_ids: [],
    },
    delays: [makeReportDelay()],
    milestones: [makeReportMilestone()],
    kpi: makeReportKpi(),
    restricted: false,
    ...overrides,
  };
}

/** 共有リンク 1 件（一覧の形。トークンは無い） */
export function makeShareLink(overrides: Partial<ShareLink> = {}): ShareLink {
  const { token: _token, ...link } = makeShareLinkCreated();
  return { ...link, ...overrides };
}

export function shareLinkList(items: ShareLink[]): ShareLinkList {
  return { project_id: 3, items, total: items.length, limit: 100, offset: 0 };
}

export function makeShareLinkCreated(overrides: Partial<ShareLinkCreated> = {}): ShareLinkCreated {
  return {
    id: 71,
    project_id: 3,
    scope: "REPORT",
    expires_at: "2026-12-31T09:00:00Z",
    revoked_at: null,
    created_by: 5,
    created_at: "2026-12-10T01:00:00Z",
    token: "tok-abc_123",
    ...overrides,
  };
}

export function makeSharedView(overrides: Partial<SharedView> = {}): SharedView {
  return {
    scope: "REPORT",
    expires_at: "2026-12-31T09:00:00Z",
    project: { id: 3, code: "PJ-3", name: "基幹刷新" },
    gantt: null,
    meeting_report: makeMeetingReport(),
    ...overrides,
  };
}
