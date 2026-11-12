// 試験用のデータ：進捗の応答の形（backend/app/schemas/progress.py の SlipOut・DelayOut・DelaysOut・ProgressEntryOut・
// ProgressItemsOut・ProgressMethodOut・FinalizeOut）
import type {
  Delay,
  Delays,
  FinalizeResult,
  ProgressEntry,
  ProgressItems,
  ProgressMethodResult,
  Slip,
} from "../api/prg";
import { makeStatusReport } from "./schFixtures";

export function makeSlip(overrides: Partial<Slip> = {}): Slip {
  return {
    activity_id: 25,
    name: "公開",
    is_milestone: true,
    before: 21,
    after: 24,
    before_date: "2026-12-21",
    after_date: "2026-12-24",
    slip_days: 3,
    ...overrides,
  };
}

/** 稲妻チャート.txt §6 の例の「資料作成」（12/01〜12/21・25%・報告日 12/10 で δ＝−4.75） */
export function makeDelay(overrides: Partial<Delay> = {}): Delay {
  return {
    activity_id: 21,
    wbs_code: "1.1",
    name: "資料作成",
    deviation: -4.75,
    percent_complete: 25,
    delay_reason: "レビュー待ち",
    forecast_finish: null,
    expected_finish: 26,
    expected_finish_date: "2026-12-26",
    fixed_finish: makeSlip({
      activity_id: 21,
      name: "資料作成",
      is_milestone: false,
      before: 21,
      after: 26,
      before_date: "2026-12-21",
      after_date: "2026-12-26",
      slip_days: 5,
    }),
    is_critical: true,
    transitive_successor_ids: [25],
    successor_slips: [makeSlip({ slip_days: 5, after: 26, after_date: "2026-12-26" })],
    milestone_slips: [makeSlip({ slip_days: 5, after: 26, after_date: "2026-12-26" })],
    project_finish: { before_date: "2026-12-21", after_date: "2026-12-26", slip_days: 5 },
    ...overrides,
  };
}

export function makeDelays(delays: Delay[], overrides: Partial<Delays> = {}): Delays {
  return {
    report: makeStatusReport(),
    source: "CALCULATED",
    report_line: 10,
    extract_days: 2,
    delays,
    combined_milestone_slips: [makeSlip({ slip_days: 5, after: 26, after_date: "2026-12-26" })],
    combined_project_finish: { before_date: "2026-12-21", after_date: "2026-12-26", slip_days: 5 },
    restricted: false,
    ...overrides,
  };
}

export function makeProgressEntry(overrides: Partial<ProgressEntry> = {}): ProgressEntry {
  return {
    status_report_id: 41,
    activity_id: 21,
    progress_method: "DIRECT",
    percent_complete: 25,
    actual_start: "2026-12-01",
    actual_finish: null,
    remaining_hours: null,
    remaining_hours_source: "AUTO",
    delay_reason: null,
    forecast_finish: null,
    entered_by: 5,
    entered_at: "2026-12-10T09:00:00Z",
    ...overrides,
  };
}

export function makeProgressItems(overrides: Partial<ProgressItems> = {}): ProgressItems {
  return { activity_id: 22, progress_method: "DELIVERABLE_RATIO", percent_complete: null, items: [], ...overrides };
}

export function makeProgressMethodResult(overrides: Partial<ProgressMethodResult> = {}): ProgressMethodResult {
  return {
    activity_id: 21,
    progress_method: "FIFTY_FIFTY",
    effective_method: "FIFTY_FIFTY",
    project_default: "DIRECT",
    ...overrides,
  };
}

export function makeFinalizeResult(overrides: Partial<FinalizeResult> = {}): FinalizeResult {
  return {
    report: makeStatusReport({
      status: "FINALIZED",
      finalized_by: 5,
      finalized_at: "2026-12-10T10:00:00Z",
      snapshot_id: 7,
    }),
    snapshot_id: 7,
    report_line: 10,
    tolerance_days: 0.5,
    extract_days: 2,
    plan_basis: "LATEST",
    item_count: 4,
    ...overrides,
  };
}
