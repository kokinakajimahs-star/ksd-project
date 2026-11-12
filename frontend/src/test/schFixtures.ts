// 試験用のデータ：スケジュール・進捗の応答の形（backend/app/schemas/schedule.py・progress.py の
// ActivityOut・ActivityList・GanttOut・BaselineOut・VarianceOut・StatusReportOut・LightningLineOut・LightningLinesOut）
import type { LightningLine, LightningRow, OverlaidLine, StatusReport } from "../api/prg";
import type { Activity, ActivityList, AxisPoint, Gantt, GanttBar, GanttRow, ScheduleBaseline } from "../api/sch";

export function makeActivity(overrides: Partial<Activity> = {}): Activity {
  return {
    id: 21,
    project_id: 3,
    work_package_id: 101,
    wbs_code: "1.1",
    work_package_name: "要件定義",
    name: "資料作成",
    sort_order: 1,
    requested_start: null,
    duration_days: 5,
    is_milestone: false,
    estimate_hours: 40,
    progress_method: null,
    start_date: "2026-04-01",
    finish_date: "2026-04-07",
    early_start: "2026-04-01",
    early_finish: "2026-04-07",
    late_start: "2026-04-01",
    late_finish: "2026-04-07",
    total_float: 0,
    is_critical: true,
    predecessor_ids: [],
    successor_ids: [],
    assignees: [],
    ...overrides,
  };
}

export function activityList(items: Activity[], overrides: Partial<ActivityList> = {}): ActivityList {
  return { items, total: items.length, limit: 200, offset: 0, dependencies: [], restricted: false, ...overrides };
}

/** 暦日上の座標（日付＋割合）と稼働日の軸の位置 */
export function point(date: string, fraction: number, position = 0): AxisPoint {
  return { position, calendar: { date, fraction } };
}

export function makeBar(kind: GanttBar["kind"], start: string, end: string, overrides: Partial<GanttBar> = {}): GanttBar {
  return {
    kind,
    is_milestone: false,
    start: point(start, 0),
    end: point(end, 1),
    start_date: start,
    end_date: end,
    open_ended: false,
    ...overrides,
  };
}

export function makeGanttRow(overrides: Partial<GanttRow> = {}): GanttRow {
  return {
    row_index: 0,
    activity_id: 21,
    name: "資料作成",
    work_package_id: 101,
    wbs_code: "1.1",
    work_package_name: "要件定義",
    depth: 2,
    is_milestone: false,
    is_critical: false,
    total_float: 3,
    planned: makeBar("PLANNED", "2026-12-01", "2026-12-21"),
    actual: null,
    baseline: null,
    assignees: [],
    percent_complete: null,
    ...overrides,
  };
}

export function makeGantt(overrides: Partial<Gantt> = {}): Gantt {
  return {
    project_start: "2026-12-01",
    report_date: null,
    baseline: null,
    range_start: "2026-12-01",
    range_end: "2026-12-21",
    non_working_days: [],
    rows: [makeGanttRow()],
    links: [],
    restricted: false,
    ...overrides,
  };
}

export function makeStatusReport(overrides: Partial<StatusReport> = {}): StatusReport {
  return {
    id: 41,
    project_id: 3,
    report_date: "2026-12-10",
    status: "OPEN",
    created_by: 5,
    created_at: "2026-12-10T09:00:00Z",
    finalized_by: null,
    finalized_at: null,
    entry_count: 0,
    snapshot_id: null,
    ...overrides,
  };
}

export function makeLightningRow(overrides: Partial<LightningRow> = {}): LightningRow {
  return {
    row_index: 0,
    activity_id: 21,
    wbs_code: "1.1",
    name: "資料作成",
    is_milestone: false,
    is_critical: false,
    start_date: "2026-12-01",
    finish_date: "2026-12-21",
    duration_days: 21,
    progress_method: "DIRECT",
    percent_complete: 25,
    actual_start: "2026-12-01",
    actual_finish: null,
    remaining_hours: null,
    delay_reason: null,
    forecast_finish: null,
    point: point("2026-12-06", 0.25, 5.25),
    deviation: -4.75,
    judgement: "BEHIND",
    counted_as: "BEHIND",
    display_date: "2026-12-06",
    ...overrides,
  };
}

export function makeLightningLine(rows: LightningRow[], overrides: Partial<LightningLine> = {}): LightningLine {
  return {
    report: makeStatusReport(),
    source: "CALCULATED",
    project_start: "2026-12-01",
    report_line: point("2026-12-10", 1, 10),
    tolerance_days: 0.5,
    rows,
    restricted: false,
    ...overrides,
  };
}

export function makeOverlaidLine(overrides: Partial<OverlaidLine> = {}): OverlaidLine {
  return {
    style_index: 0,
    report_id: 40,
    report_date: "2026-12-03",
    report_line: point("2026-12-03", 1, 3),
    tolerance_days: 0.5,
    vertices: [
      {
        row_index: 0,
        activity_id: 21,
        point: point("2026-12-02", 0.5, 1.5),
        deviation: -1.5,
        judgement: "BEHIND",
        display_date: "2026-12-02",
      },
    ],
    missing_activity_ids: [],
    ...overrides,
  };
}

export function makeScheduleBaseline(overrides: Partial<ScheduleBaseline> = {}): ScheduleBaseline {
  return {
    id: 61,
    project_id: 3,
    version: 1,
    name: "当初計画",
    reason: "MANUAL",
    change_request_id: null,
    created_by: 5,
    created_at: "2026-11-01T01:00:00Z",
    item_count: 4,
    ...overrides,
  };
}
