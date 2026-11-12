// 見積の API（設計書 6.2 の API-EST-001・002）。WBS の画面（UI-WBS-001・002）が見積を書き、根拠と履歴を読む。
// 形はサーバの schemas（backend/app/schemas/estimate.py）に合わせる
import { apiFetch } from "./client";
import type { PageQuery } from "./cmn";

export type EstimateTargetType = "ELEMENT" | "ACTIVITY";
/** 入力の単位：人時・人日（人日は 1 日の稼働時間で人時へ換算する。RULE-EST-002） */
export type InputUnit = "HOUR" | "DAY";
export type EstimateMethod = "BOTTOM_UP" | "ANALOGOUS" | "THREE_POINT" | "EXPERT" | "OTHER";

export interface EstimateInput {
  input_value: number;
  input_unit: InputUnit;
  method: EstimateMethod | null;
  assumptions: string | null;
  references: string | null;
}

export interface EstimateRecord {
  id: number;
  project_id: number;
  target_type: EstimateTargetType;
  target_id: number;
  /** 人時へ換算した値 */
  hours: number;
  input_value: number;
  input_unit: InputUnit;
  method: EstimateMethod | null;
  assumptions: string | null;
  references: string | null;
  entered_by: number;
  entered_by_name: string | null;
  entered_at: string;
}

export interface EstimateWritten {
  record: EstimateRecord;
  current_hours: number;
  wbs_status: string;
  project_estimate_hours: number;
}

/** 見積の履歴（古い順） */
export interface EstimateHistory {
  target_type: EstimateTargetType;
  target_id: number;
  target_exists: boolean;
  current_hours: number | null;
  items: EstimateRecord[];
  total: number;
  limit: number;
  offset: number;
}

export function putEstimate(
  projectId: number,
  targetType: EstimateTargetType,
  targetId: number,
  input: EstimateInput,
): Promise<EstimateWritten> {
  return apiFetch<EstimateWritten>(`/projects/${projectId}/estimates/${targetType}/${targetId}`, {
    method: "PUT",
    body: input,
  });
}

export function getEstimateHistory(
  projectId: number,
  targetType: EstimateTargetType,
  targetId: number,
  query: PageQuery = {},
): Promise<EstimateHistory> {
  return apiFetch<EstimateHistory>(`/projects/${projectId}/estimates/${targetType}/${targetId}/history`, {
    query: { limit: query.limit, offset: query.offset },
  });
}
