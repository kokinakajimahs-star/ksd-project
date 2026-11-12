// 進捗の画面（UI-PRG-001・002）の呼び名と小さな計算
import type { ProgressItemType, StatusReport } from "../../api/prg";
import type { ProgressMethod } from "../../api/prj";
import { PROGRESS_METHODS } from "../prj/labels";

/** 報告日の状態（5.10：OPEN → FINALIZED） */
export function reportStatusLabel(status: StatusReport["status"]): string {
  return status === "FINALIZED" ? "確定済み" : "入力中";
}

/** 算定方式の呼び名（BR-06）。知らない値・空はそのまま */
export function progressMethodLabel(code: string | null): string {
  if (code === null) {
    return "—";
  }
  return PROGRESS_METHODS.find((m) => m.code === code)?.label ?? code;
}

/** 算定項目の種類の呼び名（4.1 の progress_items.item_type） */
export const ITEM_TYPE_LABELS: Record<ProgressItemType, string> = {
  DELIVERABLE: "成果物",
  WORK: "作業",
  CHECK: "検証項目",
  MILESTONE: "マイルストーン",
};

export const ITEM_TYPES: ProgressItemType[] = ["DELIVERABLE", "WORK", "CHECK", "MILESTONE"];

/** 項目を数える方式が数える項目の種類（BR-06）。項目を数えない方式（直接入力・0/100 法・50/50 法）は null */
export function countedItemType(method: string | null): ProgressItemType | null {
  switch (method as ProgressMethod | null) {
    case "DELIVERABLE_RATIO":
      return "DELIVERABLE";
    case "APPROVED_WORK_RATIO":
      return "WORK";
    case "CHECK_ITEM_RATIO":
      return "CHECK";
    case "WEIGHTED_MILESTONE":
      return "MILESTONE";
    default:
      return null;
  }
}

/** ずれ（稼働日）の表示。正は + を付け、小数 2 桁で丸める */
export function signedDays(value: number): string {
  const rounded = Math.round(value * 100) / 100;
  return rounded > 0 ? `+${rounded}` : String(rounded);
}

/** 報告日の一覧の並び：報告日の新しい順 */
export function newestFirst(reports: StatusReport[]): StatusReport[] {
  return [...reports].sort((a, b) => (a.report_date < b.report_date ? 1 : a.report_date > b.report_date ? -1 : 0));
}
