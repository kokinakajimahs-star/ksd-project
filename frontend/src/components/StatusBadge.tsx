// 状態のバッジ（A-53：Primer の Label の形）。色は意味で選ぶ（tone）。見た目だけの部品で、文字はそのまま読める
// （表のセル・定義リストの中に置いても、試験やスクリーンリーダーが読む名前は中の文字のまま）
import type { ReactNode } from "react";

export type BadgeTone = "neutral" | "accent" | "attention" | "success" | "done" | "danger";

/** WBS の状態（設計書 4.1 projects.wbs_status）の色 */
export const WBS_STATUS_TONES: Record<string, BadgeTone> = {
  DRAFT: "neutral",
  IN_REVIEW: "accent",
  PENDING_APPROVAL: "attention",
  BASELINED: "success",
};

/** 変更要求の状態の色 */
export const CHANGE_STATUS_TONES: Record<string, BadgeTone> = {
  DRAFT: "neutral",
  SUBMITTED: "attention",
  APPROVED: "success",
  REJECTED: "danger",
  APPLIED: "done",
};

/** 是正措置の状態の色 */
export const ACTION_STATUS_TONES: Record<string, BadgeTone> = {
  NOT_STARTED: "neutral",
  IN_PROGRESS: "accent",
  DONE: "success",
};

export default function StatusBadge({ tone, children }: { tone: BadgeTone | undefined; children: ReactNode }) {
  return (
    <span className="badge" data-tone={tone ?? "neutral"}>
      {children}
    </span>
  );
}
