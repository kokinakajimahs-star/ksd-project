// ガントの座標の計算（設計書 7.3）。横軸は稼働日の軸ではなく暦日で描く。
// サーバが返す暦日上の座標（日付＋その日の中の割合）をそのまま x にする（画面で稼働日を数え直さない）
import type { Judgement, LightningLine, OverlaidLine } from "../../api/prg";
import type { ReportLightning } from "../../api/rpt";
import type { CalendarPoint, Gantt, GanttBar, GanttRow } from "../../api/sch";

/** 表示の単位。単位は 1 日あたりの横幅だけを変える（7.3） */
export type GanttUnit = "day" | "week" | "month";

export const DAY_WIDTH: Record<GanttUnit, number> = { day: 24, week: 6, month: 2 };

export const UNIT_LABELS: Record<GanttUnit, string> = { day: "日", week: "週", month: "月" };

/** 行の高さは一定（7.3） */
export const ROW_HEIGHT = 28;
export const HEADER_HEIGHT = 32;
export const LABEL_WIDTH = 280;

const MS_PER_DAY = 86_400_000;
const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

/** YYYY-MM-DD を UTC の 0 時の時刻（ミリ秒）にする。形が違えば例外 */
export function parseIsoDate(value: string): number {
  const match = ISO_DATE.exec(value);
  if (match === null) {
    throw new Error(`日付の形が違う: ${value}`);
  }
  return Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
}

function formatIsoDate(time: number): string {
  return new Date(time).toISOString().slice(0, 10);
}

/** from から to までの暦日の数（to が前なら負） */
export function daysBetween(from: string, to: string): number {
  return Math.round((parseIsoDate(to) - parseIsoDate(from)) / MS_PER_DAY);
}

export function addDays(value: string, days: number): string {
  return formatIsoDate(parseIsoDate(value) + days * MS_PER_DAY);
}

/** 暦日 date の始まり（0 時）の x（描画域の左端が rangeStart の 0 時） */
export function xOfDate(rangeStart: string, date: string, dayWidth: number): number {
  return daysBetween(rangeStart, date) * dayWidth;
}

/** 暦日上の座標（日付＋その日の中の割合）の x */
export function xOfPoint(rangeStart: string, point: CalendarPoint, dayWidth: number): number {
  return (daysBetween(rangeStart, point.date) + point.fraction) * dayWidth;
}

export interface DateRange {
  /** 描く最初の暦日 */
  start: string;
  /** 描く最後の暦日（この日を含む） */
  end: string;
}

/** 描く暦日の範囲：サーバの範囲に、報告日線・イナズマ線の頂点などの日付（extraDates）を足して広げる。活動が無ければ null */
export function chartRange(gantt: Pick<Gantt, "range_start" | "range_end">, extraDates: string[]): DateRange | null {
  const dates = [gantt.range_start, gantt.range_end, ...extraDates].filter((d): d is string => d !== null);
  if (dates.length === 0 || gantt.range_start === null) {
    return null;
  }
  let start = dates[0]!;
  let end = dates[0]!;
  for (const d of dates) {
    if (parseIsoDate(d) < parseIsoDate(start)) {
      start = d;
    }
    if (parseIsoDate(d) > parseIsoDate(end)) {
      end = d;
    }
  }
  return { start, end };
}

/** 描く範囲を広げる線（GanttChart の lightning・reportLightning・pastLines と同じもの） */
export interface RangeLines {
  lightning?: LightningLine | null;
  reportLightning?: ReportLightning | null;
  pastLines?: OverlaidLine[];
}

/**
 * 描く範囲を広げる日付（chartRange の extraDates）：⑥⑦ の報告日線と、ガントの行に当たるイナズマ線の頂点（API-PRG-006 の線は
 * 活動、会議レポートの線は行の番号で当てる。両方あれば lightning）、⑧ の過去の線の報告日線と頂点。
 * GanttChart（描く範囲）と GanttPage（非稼働日を受け取る範囲。7.3）が同じ日付を使う
 */
export function chartExtraDates(rows: Pick<GanttRow, "row_index" | "activity_id">[], lines: RangeLines): string[] {
  const { lightning = null, reportLightning = null, pastLines = [] } = lines;
  const extra: string[] = [];
  if (lightning !== null) {
    const activities = new Set(rows.map((r) => r.activity_id));
    extra.push(lightning.report_line.calendar.date);
    for (const row of lightning.rows) {
      if (row.activity_id !== null && activities.has(row.activity_id)) {
        extra.push(row.point.calendar.date);
      }
    }
  } else if (reportLightning !== null) {
    const rowIndexes = new Set(rows.map((r) => r.row_index));
    extra.push(reportLightning.report_line.calendar.date);
    for (const v of reportLightning.vertices) {
      if (rowIndexes.has(v.row_index)) {
        extra.push(v.point.calendar.date);
      }
    }
  }
  for (const line of pastLines) {
    extra.push(line.report_line.calendar.date);
    for (const v of line.vertices) {
      extra.push(v.point.calendar.date);
    }
  }
  return extra;
}

/** 今のガントの行に当てられず描かない頂点の数（⑥⑦ の線と、⑧ の過去の線ごと） */
export interface UnplacedVertices {
  /** API-PRG-006 の線のうち、活動が無い（消した活動の写し）か今のガントの行に無い頂点の数（線が無ければ 0） */
  lightning: number;
  /** 過去の線ごと（並びは pastLines と同じ）：今のガントに無い活動（missing_activity_ids）と、行の番号が今の行に無い頂点の数 */
  pastLines: { reportId: number; reportDate: string; count: number }[];
}

/**
 * 描かない頂点を数える（docs/decisions/ASSUMPTIONS.md A-49 の 13・OQ-42 (21) の b。黙って落とさない）。
 * 当て方は GanttChart と同じ：API-PRG-006 の線は活動、過去の線は行の番号で今のガントの行へ当てる
 */
export function unplacedVertexCounts(
  rows: Pick<GanttRow, "row_index" | "activity_id">[],
  lines: Pick<RangeLines, "lightning" | "pastLines">,
): UnplacedVertices {
  const { lightning = null, pastLines = [] } = lines;
  const activities = new Set(rows.map((r) => r.activity_id));
  const rowIndexes = new Set(rows.map((r) => r.row_index));
  return {
    lightning:
      lightning === null
        ? 0
        : lightning.rows.filter((r) => r.activity_id === null || !activities.has(r.activity_id)).length,
    pastLines: pastLines.map((line) => ({
      reportId: line.report_id,
      reportDate: line.report_date,
      count: line.missing_activity_ids.length + line.vertices.filter((v) => !rowIndexes.has(v.row_index)).length,
    })),
  };
}

/** inner が outer に収まる（両端を含む） */
export function rangeContains(outer: DateRange, inner: DateRange): boolean {
  return parseIsoDate(outer.start) <= parseIsoDate(inner.start) && parseIsoDate(inner.end) <= parseIsoDate(outer.end);
}

/**
 * 非稼働日を受け取り直す表示範囲（7.3・API-SCH-010 の range_start・range_end）。非稼働日は、前回の要求で渡した範囲
 * （requested。渡していなければ null で、応答のバーの範囲）の分しか受け取っていない。描く範囲がそこに収まっていなければ
 * 描く範囲を返し、収まっていれば null（読み直さない）。描く活動が無ければ null
 */
export function rangeToRefetch(
  gantt: Pick<Gantt, "range_start" | "range_end">,
  extraDates: string[],
  requested: DateRange | null,
): DateRange | null {
  const drawn = chartRange(gantt, extraDates);
  if (drawn === null) {
    return null;
  }
  const covered =
    requested ??
    (gantt.range_start !== null && gantt.range_end !== null ? { start: gantt.range_start, end: gantt.range_end } : null);
  return covered !== null && rangeContains(covered, drawn) ? null : drawn;
}

/** 範囲を描く幅（最後の日を含む） */
export function rangeWidth(range: DateRange, dayWidth: number): number {
  return (daysBetween(range.start, range.end) + 1) * dayWidth;
}

/**
 * バーが範囲に掛かるか（描く x で見る。範囲は start の 0 時から end の翌日の 0 時まで）。バーは長さのある重なりが要り、
 * 範囲の端に触れるだけなら掛からない。マイルストーン（終わりの点に菱形を描く）は、その点が範囲の内（両端を含む）なら掛かる
 */
export function barInRange(bar: Pick<GanttBar, "is_milestone" | "start" | "end">, range: DateRange): boolean {
  const days = daysBetween(range.start, range.end) + 1;
  const start = xOfPoint(range.start, bar.start.calendar, 1);
  const end = xOfPoint(range.start, bar.end.calendar, 1);
  if (bar.is_milestone || end <= start) {
    const at = bar.is_milestone ? end : start;
    return at >= 0 && at <= days;
  }
  return start < days && end > 0;
}

/** 行の予定のバーか実績のバーが範囲に掛かるか（基準のバーは見ない） */
export function rowInRange(row: Pick<GanttRow, "planned" | "actual">, range: DateRange): boolean {
  return [row.planned, row.actual].some((bar) => bar !== null && barInRange(bar, range));
}

export interface RowWindow {
  /** 描く最初の行の番号（0 始まり） */
  first: number;
  /** 描く最後の行の番号（この行を含む。行が無ければ first − 1） */
  last: number;
}

/** 見えている行（縦方向の仮想化。7.3・NFR-02）。前後に overscan 行ずつ余分に描く */
export function visibleRows(
  scrollTop: number,
  viewportHeight: number,
  rowCount: number,
  rowHeight: number,
  overscan = 5,
): RowWindow {
  if (rowCount <= 0) {
    return { first: 0, last: -1 };
  }
  const top = Math.max(0, scrollTop);
  const first = Math.max(0, Math.floor(top / rowHeight) - overscan);
  const last = Math.min(rowCount - 1, Math.ceil((top + Math.max(0, viewportHeight)) / rowHeight) + overscan);
  return { first, last: Math.max(first - 1, last) };
}

/** 判定の上で数える値（設計書 5.6：完了・未着手（予定内）は予定どおりに数える） */
export function countedAs(judgement: Judgement): Judgement {
  return judgement === "COMPLETED" || judgement === "NOT_STARTED" ? "ON_SCHEDULE" : judgement;
}

/** イナズマ線の頂点の色（7.3：遅れ＝赤、前倒し＝青、予定どおり＝緑、未算定＝灰） */
export function judgementColor(judgement: Judgement): string {
  switch (countedAs(judgement)) {
    case "BEHIND":
      return "#d32f2f";
    case "AHEAD":
      return "#1976d2";
    case "NOT_COMPUTED":
      return "#9e9e9e";
    default:
      return "#388e3c";
  }
}

/** 頂点に重ねる印（7.3：完了・未着手（予定内）は予定どおりの色に形を重ねて区別する） */
export interface VertexMark {
  /** check＝完了（白いチェック）、hole＝未着手（予定内）（白い穴で輪にする） */
  kind: "check" | "hole";
  /** SVG の path の d */
  d: string;
}

/** 中心 (cx, cy)・半径 r の頂点に重ねる印。完了・未着手（予定内）のほかは印を付けない */
export function vertexMark(judgement: Judgement, cx: number, cy: number, r: number): VertexMark | null {
  if (judgement === "COMPLETED") {
    return {
      kind: "check",
      d: `M ${cx - r * 0.5} ${cy} L ${cx - r * 0.1} ${cy + r * 0.4} L ${cx + r * 0.55} ${cy - r * 0.4}`,
    };
  }
  if (judgement === "NOT_STARTED") {
    const inner = r * 0.45;
    return {
      kind: "hole",
      d: `M ${cx - inner} ${cy} a ${inner} ${inner} 0 1 0 ${2 * inner} 0 a ${inner} ${inner} 0 1 0 ${-2 * inner} 0 Z`,
    };
  }
  return null;
}

export const JUDGEMENT_LABELS: Record<Judgement, string> = {
  COMPLETED: "完了",
  NOT_STARTED: "未着手（予定内）",
  BEHIND: "遅れ",
  AHEAD: "前倒し",
  ON_SCHEDULE: "予定どおり",
  NOT_COMPUTED: "未算定",
};

/** 過去の線の種類（7.3 の ⑧：選んだ報告日ごとに線の種類を変える）。style_index の順に繰り返す */
export const PAST_LINE_DASHES = ["6 3", "2 3", "10 3 2 3", "1 5", "12 4"] as const;

export function pastLineDash(styleIndex: number): string {
  const n = PAST_LINE_DASHES.length;
  return PAST_LINE_DASHES[((styleIndex % n) + n) % n]!;
}

export interface Tick {
  date: string;
  label: string;
}

/** 目盛り：日は毎日（月の初日と最初の日は 月/日）、週は月曜（と最初の日）、月は 1 日（と最初の日） */
export function ticks(range: DateRange, unit: GanttUnit): Tick[] {
  const result: Tick[] = [];
  const count = daysBetween(range.start, range.end);
  for (let i = 0; i <= count; i += 1) {
    const date = addDays(range.start, i);
    const d = new Date(parseIsoDate(date));
    const day = d.getUTCDate();
    const month = d.getUTCMonth() + 1;
    if (unit === "day") {
      result.push({ date, label: i === 0 || day === 1 ? `${month}/${day}` : String(day) });
    } else if (unit === "week") {
      if (i === 0 || d.getUTCDay() === 1) {
        result.push({ date, label: `${month}/${day}` });
      }
    } else if (i === 0 || day === 1) {
      result.push({ date, label: `${d.getUTCFullYear()}-${String(month).padStart(2, "0")}` });
    }
  }
  return result;
}

/** 折れ線の頂点：行の番号（描く順の位置）と x */
export interface LineVertex {
  row: number;
  x: number;
}

/**
 * イナズマ線の折れ線（7.3 の ⑦・PRG-12）。報告日線の上端から始め、行の順に頂点を結び、報告日線の下端で終える。
 * y は行の中央。top は行の描画域の上端
 */
export function lightningPolyline(
  vertices: LineVertex[],
  reportX: number,
  rowCount: number,
  rowHeight: number,
  top: number,
): string {
  const sorted = [...vertices].sort((a, b) => a.row - b.row);
  const points: [number, number][] = [[reportX, top]];
  for (const v of sorted) {
    points.push([v.x, top + v.row * rowHeight + rowHeight / 2]);
  }
  points.push([reportX, top + rowCount * rowHeight]);
  return points.map(([x, y]) => `${round(x)},${round(y)}`).join(" ");
}

function round(value: number): number {
  return Math.round(value * 100) / 100;
}
