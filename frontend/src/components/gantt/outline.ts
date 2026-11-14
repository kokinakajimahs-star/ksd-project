// ガントの表示の行（設計書 7.3 の「WBS の要素の行」。CR-024）。API-WBS-001 の木から要素の行を、その配下の行の前に差し込む。
// 活動の行の順と row_index はサーバ（RULE-SCH-005）のまま。要約のバーは配下の活動の予定のバーの位置の最小〜最大で、
// 画面は稼働日を数えない（7.3）
import type { AxisPoint, GanttRow } from "../../api/sch";
import type { WbsElement } from "../../api/wbs";
import { parseIsoDate } from "./scale";

/** 要素の行の要約のバー（配下の活動の予定のバーの最も早い開始〜最も遅い終了） */
export interface SummarySpan {
  start: AxisPoint;
  end: AxisPoint;
}

export interface ElementOutlineRow {
  kind: "element";
  key: string;
  element: WbsElement;
  /** 最上位が 1 */
  depth: number;
  span: SummarySpan | null;
  /** 配下に活動の行がある */
  hasActivities: boolean;
}

export interface ActivityOutlineRow {
  kind: "activity";
  key: string;
  row: GanttRow;
  depth: number;
}

export type OutlineRow = ElementOutlineRow | ActivityOutlineRow;

export function elementKey(id: number): string {
  return `e${id}`;
}

export function activityKey(id: number): string {
  return `a${id}`;
}

/** 活動の行だけの表示（要素の行を出さないとき。会議レポート・共有ビュー・WBS を読めないとき）。字下げは今までどおり WP の階層 */
export function activityOutline(rows: GanttRow[]): ActivityOutlineRow[] {
  return rows.map((row) => ({ kind: "activity", key: activityKey(row.activity_id), row, depth: row.depth }));
}

function pointValue(point: AxisPoint): number {
  return parseIsoDate(point.calendar.date) + point.calendar.fraction;
}

function widen(span: SummarySpan | null, start: AxisPoint, end: AxisPoint): SummarySpan {
  if (span === null) {
    return { start, end };
  }
  return {
    start: pointValue(start) < pointValue(span.start) ? start : span.start,
    end: pointValue(end) > pointValue(span.end) ? end : span.end,
  };
}

/**
 * 要素の行と活動の行を WBS の並びで並べる。
 * - 要素は API-WBS-001 の先行順（兄弟は表示順）。WP の行の後にその WP の活動の行（サーバの順）を置く
 * - includeEmpty が偽なら、配下に活動の無い要素の行を出さない（編集していないとき）
 * - 木に無い WP の活動の行（読める範囲が違うときなど）は、落とさずに末尾へサーバの順で置く
 */
export function buildOutline(elements: WbsElement[], rows: GanttRow[], includeEmpty: boolean): OutlineRow[] {
  const activitiesOf = new Map<number, GanttRow[]>();
  for (const row of rows) {
    const list = activitiesOf.get(row.work_package_id);
    if (list === undefined) {
      activitiesOf.set(row.work_package_id, [row]);
    } else {
      list.push(row);
    }
  }
  const childrenOf = new Map<number | null, WbsElement[]>();
  const known = new Set(elements.map((e) => e.id));
  for (const element of elements) {
    // 親が木に無い要素（読める範囲の外）は最上位として扱う
    const parent = element.parent_id !== null && known.has(element.parent_id) ? element.parent_id : null;
    const list = childrenOf.get(parent);
    if (list === undefined) {
      childrenOf.set(parent, [element]);
    } else {
      list.push(element);
    }
  }

  const placed = new Set<number>();
  function walk(element: WbsElement): { rows: OutlineRow[]; span: SummarySpan | null; hasActivities: boolean } {
    const below: OutlineRow[] = [];
    let span: SummarySpan | null = null;
    let hasActivities = false;
    for (const row of activitiesOf.get(element.id) ?? []) {
      placed.add(row.activity_id);
      below.push({ kind: "activity", key: activityKey(row.activity_id), row, depth: element.depth + 1 });
      hasActivities = true;
      if (row.planned !== null) {
        span = widen(span, row.planned.start, row.planned.end);
      }
    }
    for (const child of childrenOf.get(element.id) ?? []) {
      const sub = walk(child);
      below.push(...sub.rows);
      hasActivities = hasActivities || sub.hasActivities;
      if (sub.span !== null) {
        span = widen(span, sub.span.start, sub.span.end);
      }
    }
    if (!includeEmpty && !hasActivities) {
      return { rows: [], span: null, hasActivities: false };
    }
    const head: ElementOutlineRow = {
      kind: "element",
      key: elementKey(element.id),
      element,
      depth: element.depth,
      span,
      hasActivities,
    };
    return { rows: [head, ...below], span, hasActivities };
  }

  const result: OutlineRow[] = [];
  for (const root of childrenOf.get(null) ?? []) {
    result.push(...walk(root).rows);
  }
  for (const row of rows) {
    if (!placed.has(row.activity_id)) {
      result.push({ kind: "activity", key: activityKey(row.activity_id), row, depth: row.depth + 1 });
    }
  }
  return result;
}
