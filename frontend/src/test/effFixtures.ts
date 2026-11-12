// 試験用のデータ：工数の応答の形（backend/app/schemas/effort.py の AssignmentOut・TimeEntryOut・EffortSummaryOut・
// EffortVarianceOut・VarianceNoteOut）
import type {
  Assignment,
  EffortSummary,
  EffortValues,
  EffortVariance,
  EffortVarianceRow,
  SummaryRow,
  TimeEntry,
  VarianceNote,
} from "../api/eff";

export function makeAssignment(overrides: Partial<Assignment> = {}): Assignment {
  return {
    activity_id: 21,
    activity_name: "資料作成",
    wbs_code: "1.1.1",
    user_id: 7,
    display_name: "実行 花子",
    planned_hours: 16,
    ...overrides,
  };
}

export function makeTimeEntry(overrides: Partial<TimeEntry> = {}): TimeEntry {
  return {
    id: 901,
    user_id: 7,
    project_id: 3,
    activity_id: 21,
    activity_name: "資料作成",
    wbs_code: "1.1.1",
    work_date: "2026-11-09",
    hours: 7.5,
    note: null,
    ...overrides,
  };
}

export function page<T>(items: T[], total = items.length) {
  return { items, total, limit: 1000, offset: 0 };
}

export function makeValues(overrides: Partial<EffortValues> = {}): EffortValues {
  return { planned: 0, baseline: 0, actual: 0, etc: 0, eac: 0, ...overrides };
}

export function makeSummaryRow(overrides: Partial<SummaryRow> = {}): SummaryRow {
  return {
    bucket_kind: "ELEMENT",
    element_id: null,
    parent_id: null,
    wbs_code: null,
    kind: null,
    user_id: null,
    name: null,
    period_start: null,
    values: makeValues(),
    ...overrides,
  };
}

export function makeSummary(overrides: Partial<EffortSummary> = {}): EffortSummary {
  return {
    axis: "wbs",
    report: { id: 41, report_date: "2026-12-10", status: "OPEN" },
    report_specified: false,
    baseline: { id: 61, version: 1, name: "初版" },
    total: makeValues(),
    rows: [],
    ...overrides,
  };
}

export function makeVarianceNote(overrides: Partial<VarianceNote> = {}): VarianceNote {
  return {
    id: 301,
    element_id: 12,
    wbs_code: "1.2",
    status_report_id: null,
    reason: "仕様の追加",
    corrective_action_id: null,
    created_by: 2,
    created_at: "2026-12-10T01:00:00Z",
    ...overrides,
  };
}

export function makeVarianceRow(overrides: Partial<EffortVarianceRow> = {}): EffortVarianceRow {
  return {
    element_id: 12,
    parent_id: 10,
    wbs_code: "1.2",
    name: "設計",
    kind: "WORK_PACKAGE",
    baseline: 80,
    eac: 100,
    variance: 20,
    rate: 0.25,
    status: "EXCEEDED",
    note: null,
    ...overrides,
  };
}

export function makeVariance(items: EffortVarianceRow[], overrides: Partial<EffortVariance> = {}): EffortVariance {
  return {
    report: { id: 41, report_date: "2026-12-10", status: "OPEN" },
    report_specified: false,
    baseline: { id: 61, version: 1, name: "初版" },
    rate_threshold: 0.1,
    hours_threshold: 8,
    items,
    ...overrides,
  };
}
