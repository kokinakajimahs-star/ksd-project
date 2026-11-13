// レポート・共有の画面（UI-RPT-001・002）の呼び名と小さな書式
import type { ReportSource, ShareLink, ShareScope } from "../../api/rpt";
import { serverTime } from "../../lib/format";

/** 進捗の値の源（API-RPT-001 の source）。確定済みは写し（BR-13）、確定前は計算値。計画の値は常に最新計画（CR-013） */
export function sourceLabel(source: ReportSource): string {
  return source === "SNAPSHOT" ? "確定時の写し" : "計算値（確定前）";
}

/** 共有リンクで見せる範囲（4.1 の share_links.scope。4.2 の「最新」の報告日・進捗を見せる。A-40） */
export const SHARE_SCOPE_LABELS: Record<ShareScope, string> = {
  REPORT: "定例会議レポート（最新の報告日）",
  GANTT: "ガントチャート（最新の進捗）",
};

export const SHARE_SCOPES: ShareScope[] = ["REPORT", "GANTT"];

/**
 * 共有リンクで閲覧できないときの案内（API-RPT-004 の 404）。サーバは無い・失効・期限切れを同じ応答で返し、
 * どれに当たったかを返さないので、画面も 1 つの案内だけを出す
 */
export const SHARE_UNAVAILABLE_HINT = "リンクが正しいか、まだ有効かを、共有した人に確かめてください";

/** 共有リンクの今の状態（一覧の表示用。閲覧の可否の正はサーバの RULE-AUTH-002） */
export type ShareLinkState = "ACTIVE" | "REVOKED" | "EXPIRED";

export const SHARE_LINK_STATE_LABELS: Record<ShareLinkState, string> = {
  ACTIVE: "有効",
  REVOKED: "失効",
  EXPIRED: "期限切れ",
};

/**
 * 共有リンクの状態：失効させた（revoked_at あり）を有効期限より先に見て、有効期限の時刻ちょうどからは期限切れ
 * （サーバの共有リンクの判定と同じ順と境界）。now はエポックからのミリ秒
 */
export function shareLinkState(link: Pick<ShareLink, "expires_at" | "revoked_at">, now: number): ShareLinkState {
  if (link.revoked_at !== null) {
    return "REVOKED";
  }
  return now >= serverTime(link.expires_at) ? "EXPIRED" : "ACTIVE";
}

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

/** 共有リンクの有効期限の入力の既定値：今から何日後か（サーバの上限 PMT_SHARE_LINK_MAX_DAYS（既定 90 日）の内。CR-019） */
export const SHARE_LINK_DEFAULT_DAYS = 14;

/**
 * now の days 日後の端末の時刻を、画面の datetime-local の値（`YYYY-MM-DDTHH:mm`。秒は切り捨てる）にする。
 * 日は端末の暦で足す（夏時間の切り替えをまたいでも同じ時刻）
 */
export function localDateTimeAfterDays(now: Date, days: number): string {
  const later = new Date(now.getTime());
  later.setDate(later.getDate() + days);
  const pad = (n: number) => String(n).padStart(2, "0");
  const day = `${later.getFullYear()}-${pad(later.getMonth() + 1)}-${pad(later.getDate())}`;
  return `${day}T${pad(later.getHours())}:${pad(later.getMinutes())}`;
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
 * 印刷で載せる遅延一覧・マイルストーンの状況の行の数（A3 横の 1 枚に下段を収める。どちらもサーバは件数を限らず、
 * 活動 2,000 件の試しで遅延 31 件・マイルストーン 100 件になり 1 枚を超えた）。超えた分は省き、省いた件数を書き添える
 */
export const PRINT_LIST_LIMIT = 15;

/**
 * 印刷で 1 枚に載せる高さ（mm）。A3 横・余白 10mm の版面は 400 × 277mm で、端数で 2 枚目へ溢れないよう 2mm を残す。
 * 見出し・注記・下段を置いた残りの高さをガントに使う
 */
export const PRINT_SHEET_HEIGHT_MM = 275;

/**
 * 印刷用の CSS（設計書 7.4：A3 横の 1 枚）。下段は 左：遅延一覧・中：マイルストーン・右：主要 KPI の 3 列。
 * 印刷では画面の操作（ヘッダ・行き先・設定・共有リンク）を出さない。行の数にかかわらず 1 枚に収めるため、画面の区画を
 * 版面の高さ（PRINT_SHEET_HEIGHT_MM）の縦の並びにし、ガントは全ての行の高さへ伸ばさずに残りの高さの枠へ、viewBox の
 * 縦横比を保ったまま縮めて収める。下段の一覧は 1 行を 1 段に収め（長い文は省略記号で切る）、先頭の PRINT_LIST_LIMIT 行だけを載せる
 */
export const MEETING_REPORT_CSS = `
@page { size: A3 landscape; margin: 10mm; }
.meeting-report-sheet .report-lower {
  display: grid;
  grid-template-columns: 2fr 1.5fr 1fr;
  gap: 12px;
  align-items: start;
}
.meeting-report-sheet .print-only { display: none; }
@media print {
  .app-header, .no-print { display: none !important; }
  body { margin: 0; }
  .meeting-report-page, .shared-view-page {
    display: flex;
    flex-direction: column;
    height: ${PRINT_SHEET_HEIGHT_MM}mm;
    overflow: hidden;
  }
  .meeting-report-page > *, .shared-view-page > * { flex: none; }
  .meeting-report-page > h2, .shared-view-page > h2 { font-size: 14pt; margin: 0 0 2mm; }
  .shared-view-page > p { font-size: 8pt; margin: 0 0 1mm; }
  .meeting-report-page > .meeting-report-sheet, .shared-view-page > .meeting-report-sheet {
    flex: 1 1 0;
    min-height: 0;
    display: flex;
    flex-direction: column;
    font-size: 8pt;
  }
  .meeting-report-sheet > * { flex: none; }
  .meeting-report-sheet p { margin: 0 0 1mm; }
  .meeting-report-sheet h3 { font-size: 10pt; margin: 2mm 0 1mm; }
  .meeting-report-sheet .print-only { display: block; }
  .meeting-report-sheet > .report-upper { flex: 1 1 0; min-height: 0; display: flex; flex-direction: column; }
  .meeting-report-sheet > .report-upper > * { flex: none; }
  .meeting-report-sheet > .report-upper > .gantt-viewport {
    flex: 1 1 0;
    min-height: 0;
    height: auto !important;
    overflow: hidden !important;
  }
  .meeting-report-sheet .gantt-viewport svg { display: block; width: 100%; height: 100%; }
  .meeting-report-sheet .gantt-legend { display: flex; gap: 4mm; list-style: none; margin: 1mm 0; padding: 0; }
  .meeting-report-sheet .report-lower {
    grid-template-columns: minmax(0, 2fr) minmax(0, 1.5fr) minmax(0, 1fr);
    gap: 4mm;
    break-inside: avoid;
  }
  .meeting-report-sheet .report-lower table { width: 100%; table-layout: fixed; font-size: 7pt; }
  .meeting-report-sheet .report-lower td { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .meeting-report-sheet .report-lower tbody tr:nth-child(n + ${PRINT_LIST_LIMIT + 1}) { display: none; }
  .meeting-report-sheet table[aria-label="遅延一覧"] th:nth-child(1) { width: 8%; }
  .meeting-report-sheet table[aria-label="遅延一覧"] th:nth-child(2) { width: 16%; }
  .meeting-report-sheet table[aria-label="遅延一覧"] th:nth-child(3) { width: 8%; }
  .meeting-report-sheet table[aria-label="遅延一覧"] th:nth-child(4) { width: 7%; }
  .meeting-report-sheet table[aria-label="遅延一覧"] th:nth-child(5) { width: 21%; }
  .meeting-report-sheet table[aria-label="遅延一覧"] th:nth-child(6) { width: 12%; }
  .meeting-report-sheet table[aria-label="遅延一覧"] th:nth-child(7) { width: 17%; }
  .meeting-report-sheet table[aria-label="遅延一覧"] th:nth-child(8) { width: 11%; }
  .meeting-report-sheet table[aria-label="マイルストーンの状況"] th:nth-child(1) { width: 46%; }
}
`;
