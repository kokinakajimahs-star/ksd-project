// レポート・共有の画面（UI-RPT-001・002）の呼び名と小さな書式
import type { ReportSource, ShareLinkFailure, ShareScope } from "../../api/rpt";

/** 値の源（API-RPT-001 の source）。確定済みは写し（BR-13）、確定前は計算値 */
export function sourceLabel(source: ReportSource): string {
  return source === "SNAPSHOT" ? "確定時の写し" : "計算値（確定前）";
}

/** 共有リンクで見せる範囲（4.1 の share_links.scope。4.2 の「最新」の報告日・進捗を見せる。A-40） */
export const SHARE_SCOPE_LABELS: Record<ShareScope, string> = {
  REPORT: "定例会議レポート（最新の報告日）",
  GANTT: "ガントチャート（最新の進捗）",
};

export const SHARE_SCOPES: ShareScope[] = ["REPORT", "GANTT"];

/** 共有リンクを閲覧できない理由（API-RPT-004 の 404 の details の reason） */
export const SHARE_FAILURE_LABELS: Record<ShareLinkFailure, string> = {
  NOT_FOUND: "リンクが正しくありません。共有した人に確かめてください",
  REVOKED: "このリンクは失効しています",
  EXPIRED: "このリンクは有効期限が切れています",
};

/** 小数 2 桁で丸めた数（サーバの値は丸め済みのことが多いが、浮動小数の端を見せない） */
function round2(value: number): number {
  return Math.round(value * 100) / 100;
}

/** 進捗率（%）。求められない値（null）は「算定できない」 */
export function percentText(value: number | null): string {
  return value === null ? "算定できない" : `${round2(value)}%`;
}

/** 工数（人時） */
export function hoursText(value: number): string {
  return `${round2(value)} 人時`;
}

/** 差の工数（人時）。正は + を付ける */
export function signedHoursText(value: number): string {
  const rounded = round2(value);
  return `${rounded > 0 ? "+" : ""}${rounded} 人時`;
}

/**
 * 画面の datetime-local の値（端末の時刻。`YYYY-MM-DDTHH:mm`）を、時差つきの時刻（UTC の ISO 8601）にする。
 * 空・読めない値は null（API-RPT-002 の expires_at は時差つきの時刻を要る）
 */
export function localDateTimeToIso(value: string): string | null {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$/.test(value)) {
    return null;
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

/**
 * 印刷用の CSS（設計書 7.4：A3 横の 1 枚）。下段は 左：遅延一覧・中：マイルストーン・右：主要 KPI の 3 列。
 * 印刷では画面の操作（ヘッダ・行き先・設定・共有リンク）を出さず、ガントは全ての行を紙の幅に合わせて縮める
 */
export const MEETING_REPORT_CSS = `
@page { size: A3 landscape; margin: 10mm; }
.meeting-report-sheet .report-lower {
  display: grid;
  grid-template-columns: 2fr 1.5fr 1fr;
  gap: 12px;
  align-items: start;
}
@media print {
  .app-header, .no-print { display: none !important; }
  .meeting-report-sheet .gantt-viewport { height: auto !important; overflow: visible !important; }
  .meeting-report-sheet .gantt-viewport svg { width: 100%; height: auto; }
  .meeting-report-sheet .report-lower { break-inside: avoid; }
}
`;
