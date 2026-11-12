// 工数の画面（UI-EFF-001・002）の上限・呼び名と小さな計算。
// 上限はサーバの検査（backend/app/schemas/effort.py・app/domain/effort.py）と同じ値にする。画面の検査は入力の補助で、正の検査はサーバが行う
import type { EffortAxis, SummaryRow } from "../../api/eff";

/** 一覧の limit の上限（設計書 6.1） */
export const LIST_LIMIT_MAX = 1000;
/** 1 日の合計の上限（人時。EFF-02。ちょうど 24 は通す） */
export const DAY_HOURS_MAX = 24;
/** 差異の理由の長さの上限 */
export const REASON_MAX = 4000;

const WEEKDAY_LABELS = ["日", "月", "火", "水", "木", "金", "土"];
const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;
const DAY_MS = 24 * 60 * 60 * 1000;

/** YYYY-MM-DD を UTC の 0 時の Date にする。暦に無い日・形の違う値は null */
export function parseIsoDate(value: string): Date | null {
  const m = ISO_DATE.exec(value);
  if (m === null) {
    return null;
  }
  const date = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
  return toIsoDate(date) === value ? date : null;
}

function toIsoDate(date: Date): string {
  return date.toISOString().slice(0, 10);
}

/** 端末の今日（利用者の暦の日付）を YYYY-MM-DD にする */
export function localIsoDate(now: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

/** 日付に日数を足す（YYYY-MM-DD → YYYY-MM-DD） */
export function addDays(isoDate: string, days: number): string {
  const date = parseIsoDate(isoDate);
  if (date === null) {
    throw new Error(`日付の形が違う: ${isoDate}`);
  }
  return toIsoDate(new Date(date.getTime() + days * DAY_MS));
}

/** その日を含む週の月曜（集計の週の軸と同じ始まり。backend/app/domain/effort.py の週の桶） */
export function mondayOf(isoDate: string): string {
  const date = parseIsoDate(isoDate);
  if (date === null) {
    throw new Error(`日付の形が違う: ${isoDate}`);
  }
  const sinceMonday = (date.getUTCDay() + 6) % 7;
  return addDays(isoDate, -sinceMonday);
}

/** 表示する週の月曜。問い合わせの week が日付ならその週、無い・読めないなら今日の週 */
export function resolveWeek(requested: string | null, today: string): string {
  if (requested !== null && parseIsoDate(requested) !== null) {
    return mondayOf(requested);
  }
  return mondayOf(today);
}

/** 月曜から日曜までの 7 日 */
export function weekDays(monday: string): string[] {
  return Array.from({ length: 7 }, (_, i) => addDays(monday, i));
}

/** 日付と曜日の見出し（例：2026-11-09（月）。NFR-09 の日付の形） */
export function dayLabel(isoDate: string): string {
  const date = parseIsoDate(isoDate);
  return date === null ? isoDate : `${isoDate}（${WEEKDAY_LABELS[date.getUTCDay()]}）`;
}

const HOURS_TEXT = /^\d+(\.\d{1,2})?$/;

/** 工数のマスの入力を百分の 1 時間の整数にする。空は 0（行を消す）。負・小数 3 桁以上・数でないものは null */
export function parseHoursCents(text: string): number | null {
  const trimmed = text.trim();
  if (trimmed === "") {
    return 0;
  }
  if (!HOURS_TEXT.test(trimmed)) {
    return null;
  }
  return Math.round(Number(trimmed) * 100);
}

/** サーバの工数（数）を百分の 1 時間の整数にする（足し算で誤差を出さないため） */
export function toCents(hours: number): number {
  return Math.round(hours * 100);
}

/** 百分の 1 時間の整数を表示の文字にする（7.5・8・0.25） */
export function centsText(cents: number): string {
  return String(cents / 100);
}

/** 工数（人時）の表示。小数 2 桁で丸め、null は「—」 */
export function hoursText(hours: number | null): string {
  if (hours === null) {
    return "—";
  }
  return String(Math.round(hours * 100) / 100);
}

/** 差（人時）の表示。正は + を付ける */
export function signedHoursText(hours: number | null): string {
  if (hours === null) {
    return "—";
  }
  const rounded = Math.round(hours * 100) / 100;
  return rounded > 0 ? `+${rounded}` : String(rounded);
}

/** 割合（0.1）を百分率（+10.0%）にする。null は「—」 */
export function rateText(rate: number | null, signed = true): string {
  if (rate === null) {
    return "—";
  }
  const percent = Math.round(rate * 1000) / 10;
  const text = `${percent.toFixed(1)}%`;
  return signed && percent > 0 ? `+${text}` : text;
}

/** 集計の軸の呼び名（API-EFF-005 の axis。EFF-09） */
export const AXIS_LABELS: Record<EffortAxis, string> = {
  wbs: "WBS",
  member: "担当者",
  organization: "組織",
  week: "週",
  month: "月",
};

export const AXES: EffortAxis[] = ["wbs", "member", "organization", "week", "month"];

/** 集計の桶の呼び名（WBS の軸は別に WBS ID と名称を出す） */
export function bucketLabel(axis: EffortAxis, row: SummaryRow): string {
  switch (row.bucket_kind) {
    case "UNASSIGNED":
      return "未割当";
    case "NO_ORGANIZATION":
      return "所属なし";
    case "REMAINING":
      return "残り（報告日より後の ETC）";
    case "UNSCHEDULED":
      return "日付なし（日程の無い計画・基準）";
    case "BASELINE_REMOVED":
      return "基準にあって今は無い";
    case "PERIOD":
      if (row.period_start === null) {
        return "—";
      }
      return axis === "month" ? row.period_start.slice(0, 7) : `${row.period_start} の週`;
    default:
      return row.name ?? "—";
  }
}

/** 差異の判定の呼び名（RULE-EFF-004・BR-11） */
export function varianceStatusLabel(status: string): string {
  switch (status) {
    case "EXCEEDED":
      return "閾値超過";
    case "ADDED":
      return "基準に無い";
    case "WITHIN":
      return "閾値内";
    default:
      return status;
  }
}

/** WBS の軸の行の深さ（親をたどった数）。親が一覧に無ければそこで止める */
export function depthOf(rows: readonly SummaryRow[]): Map<number, number> {
  const parentOf = new Map<number, number | null>();
  for (const r of rows) {
    if (r.element_id !== null) {
      parentOf.set(r.element_id, r.parent_id);
    }
  }
  const depth = new Map<number, number>();
  for (const id of parentOf.keys()) {
    let d = 0;
    let parent = parentOf.get(id) ?? null;
    const seen = new Set<number>([id]);
    while (parent !== null && parentOf.has(parent) && !seen.has(parent)) {
      seen.add(parent);
      d += 1;
      parent = parentOf.get(parent) ?? null;
    }
    depth.set(id, d);
  }
  return depth;
}
