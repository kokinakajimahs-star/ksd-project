// ガントとイナズマ線の描画（設計書 7.3）。SVG のレイヤ（下から）：
// ①非稼働日の帯 ②ベースラインのバー ③予定のバー（クリティカルは赤い枠）と要約のバー ④実績のバー ⑤依存の矢印
// ⑥報告日線 ⑦イナズマ線 ⑧過去の線。活動の行の順はサーバが返す順（RULE-SCH-005）。見えている行だけを描く（NFR-02）。
// outline を渡すと WBS の要素の行を差し込んで描く（7.3 の「WBS の要素の行」。CR-024）。edit を渡すと編集の操作
// （行を選ぶ・名前を変える・バーのドラッグ・依存の矢印を引く・選ぶ）を受け、呼び手（GanttPage）へ渡す（7.3 の「編集」）
import {
  Fragment,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type MouseEvent as ReactMouseEvent,
  type UIEvent,
} from "react";

import type { Judgement, LightningLine, OverlaidLine } from "../../api/prg";
import type { ReportLightning } from "../../api/rpt";
import type { AxisPoint, Gantt, GanttBar, GanttRow } from "../../api/sch";
import { activityOutline, type ElementOutlineRow, type OutlineRow } from "./outline";
import {
  addDays,
  chartExtraDates,
  chartRange,
  type DateRange,
  DAY_WIDTH,
  editExtraDates,
  editRangeBase,
  HEADER_HEIGHT,
  judgementColor,
  JUDGEMENT_LABELS,
  LABEL_WIDTH,
  lightningPolyline,
  pastLineDash,
  rangeWidth,
  ROW_HEIGHT,
  ticks,
  vertexMark,
  visibleRows,
  xOfDate,
  xOfPoint,
  type GanttUnit,
  type LineVertex,
} from "./scale";

/** 行の選び方：クリック＝その行だけ、Ctrl（⌘）＋クリック＝足す・外す、Shift＋クリック＝範囲 */
export type SelectHow = "only" | "toggle" | "range";

/** バーのドラッグ：本体＝開始を動かす、右端＝終わりを動かす */
export type BarDrag = "move" | "resize";

/** 編集の状態と操作（GanttPage が持つ。7.3 の「編集」） */
export interface GanttChartEdit {
  /** 選んだ行の鍵（outline の key） */
  selected: ReadonlySet<string>;
  /** 矢印・操作の対象の行 */
  cursor: string | null;
  /** 名前を変えている行 */
  renaming: string | null;
  /** 選んだ依存の矢印（linkKey） */
  selectedLink: string | null;
  /** 希望開始日のある活動（活動 → 希望開始日）。行に印を付ける */
  requestedStart: ReadonlyMap<number, string>;
  /** 活動のバーと依存を動かせる（schedule.edit） */
  canEditSchedule: boolean;
  onSelectRow(key: string, how: SelectHow): void;
  onStartRename(key: string): void;
  onRename(key: string, name: string): void;
  onCancelRename(): void;
  onSelectLink(link: string | null): void;
  onKeyDown(event: KeyboardEvent<HTMLDivElement>): void;
  /** 日の単位に丸めたずれ（暦日。0 では呼ばない） */
  onBarDrop(activityId: number, how: BarDrag, days: number): void;
  onLinkDrop(predecessorId: number, successorId: number): void;
}

export interface GanttChartProps {
  gantt: Gantt;
  unit: GanttUnit;
  /** 選んだ報告日のイナズマ線（API-PRG-006） */
  lightning?: LightningLine | null;
  /** 重ねる過去の線（API-PRG-008） */
  pastLines?: OverlaidLine[];
  /**
   * 会議レポートのイナズマ線（API-RPT-001。今のガントの行へ置き直した頂点）。lightning の代わりに ⑥⑦ に描く。
   * 両方を渡したときは lightning を描く
   */
  reportLightning?: ReportLightning | null;
  /** 描く暦日の範囲を固定する（会議レポートの報告日の前後 4 週。7.4）。範囲の外へ出るバー・線は切り取る */
  fixedRange?: DateRange | null;
  /** 描画域の高さ（px）。この高さに入る行と前後の余分だけを描く */
  viewportHeight?: number;
  /** 描く行（要素の行を差し込んだもの。buildOutline）。省けば活動の行だけ */
  outline?: OutlineRow[];
  /** 編集の状態と操作。渡すと編集の操作を受け、範囲を後ろへ広げる（空の計画でも描く） */
  edit?: GanttChartEdit;
}

/** 依存の矢印の鍵 */
export function linkKey(predecessorId: number, successorId: number): string {
  return `${predecessorId}-${successorId}`;
}

const CRITICAL_COLOR = "#d32f2f";
// 要約のバー（7.3：濃い灰の細いバー。両端に下向きの爪）
const SUMMARY_COLOR = "#424242";
// 線と文字の色（A-53：Primer の値に合わせる）。7.3 の色の規則に関わる色（バー・クリティカル・非稼働日・報告日線とイナズマ線・
// 判定の色）は変えない。CSS 変数は main.tsx が読む Primer の値で、SVG の属性には変数を書けないので style で当てる。
// 変数が無いとき（試験など）の値を後ろに書く
const INK = "var(--fgColor-default, #1f2328)";
const INK_MUTED = "var(--fgColor-muted, #59636e)";
const LINE_MUTED = "var(--borderColor-emphasis, #818b98)";
const SURFACE = "var(--bgColor-default, #ffffff)";
const SURFACE_MUTED = "var(--bgColor-muted, #f6f8fa)";
const ACCENT = "var(--fgColor-accent, #0969da)";
const SELECTED_FILL = "var(--bgColor-accent-muted, #ddf4ff)";
const ARROW_ID = "gantt-arrowhead";
const PLOT_CLIP_ID = "gantt-plot-clip";
/** 行の見出しの字下げ（階層 1 つあたり）。要素の行を差し込むときは階層が見えるように広げる。活動の行だけ（会議レポート・
 * 共有ビュー）は今までどおり */
const INDENT = { activities: 8, outline: 12 } as const;
/** バーの右端の、終わりを動かす掴みの幅 */
const RESIZE_GRIP = 6;
/** これより小さい動きはクリック（選ぶだけ） */
const DRAG_THRESHOLD = 3;

/** ⑥⑦ に描く線：報告日線と、判定の付いた頂点（描く行の位置と x） */
interface MainLine {
  reportDate: string;
  reportLine: AxisPoint;
  vertices: { position: number; point: AxisPoint; judgement: Judgement; title: string }[];
}

type DragState =
  | { kind: "bar"; how: BarDrag; activityId: number; originX: number; days: number; moved: boolean }
  | { kind: "link"; activityId: number; fromX: number; fromY: number; toX: number; toY: number };

function percentText(value: number | null): string {
  return value === null ? "" : `${Math.round(value * 10) / 10}%`;
}

function barTitle(label: string, bar: GanttBar): string {
  return bar.is_milestone ? `${label} ${bar.end_date}` : `${label} ${bar.start_date}〜${bar.end_date}`;
}

function diamond(x: number, y: number, r: number): string {
  return `${x},${y - r} ${x + r},${y} ${x},${y + r} ${x - r},${y}`;
}

/** 要約のバー：上端 y・高さ h の帯の両端から下へ爪を出す */
function summaryPath(x1: number, x2: number, y: number, h: number): string {
  const claw = Math.min(5, Math.max(1, (x2 - x1) / 2));
  return `M${x1},${y} H${x2} V${y + h + 4} L${x2 - claw},${y + h} H${x1 + claw} L${x1},${y + h + 4} Z`;
}

function labelX(depth: number, indent: number): number {
  return 8 + Math.max(0, depth - 1) * indent;
}

/** 動かした幅を日に丸める（左右で同じ丸め。0.5 日ちょうどは遠い方へ） */
function daysOf(dx: number, dayWidth: number): number {
  const days = Math.round(Math.abs(dx) / dayWidth);
  return days === 0 ? 0 : dx < 0 ? -days : days;
}

function selectHow(event: ReactMouseEvent): SelectHow {
  return event.shiftKey ? "range" : event.ctrlKey || event.metaKey ? "toggle" : "only";
}

export default function GanttChart({
  gantt,
  unit,
  lightning = null,
  pastLines = [],
  reportLightning = null,
  fixedRange = null,
  viewportHeight = 480,
  outline,
  edit,
}: GanttChartProps) {
  const [scrollTop, setScrollTop] = useState(0);
  const [drag, setDrag] = useState<DragState | null>(null);
  const viewportRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const dayWidth = DAY_WIDTH[unit];
  const editing = edit !== undefined;
  const indent = outline === undefined ? INDENT.activities : INDENT.outline;

  const rows = useMemo(() => outline ?? activityOutline(gantt.rows), [outline, gantt.rows]);

  // 行の番号（row_index）→ 描く位置、活動 → 描く位置、行の番号 → 活動の行
  const { positionOfRow, positionOfActivity, ganttRowOf } = useMemo(() => {
    const byRow = new Map<number, number>();
    const byActivity = new Map<number, number>();
    const rowOf = new Map<number, GanttRow>();
    rows.forEach((r, i) => {
      if (r.kind === "activity") {
        byRow.set(r.row.row_index, i);
        byActivity.set(r.row.activity_id, i);
        rowOf.set(r.row.row_index, r.row);
      }
    });
    return { positionOfRow: byRow, positionOfActivity: byActivity, ganttRowOf: rowOf };
  }, [rows]);

  // ⑥⑦ に描く線。API-PRG-006 の線は活動で、会議レポートの線は行の番号で、今のガントの行へ当てる
  const mainLine = useMemo((): MainLine | null => {
    if (lightning !== null) {
      const vertices: MainLine["vertices"] = [];
      for (const row of lightning.rows) {
        const position = row.activity_id === null ? undefined : positionOfActivity.get(row.activity_id);
        if (position !== undefined) {
          vertices.push({
            position,
            point: row.point,
            judgement: row.judgement,
            title: `${row.wbs_code} ${row.name}：${JUDGEMENT_LABELS[row.judgement]}（${row.display_date}）`,
          });
        }
      }
      return { reportDate: lightning.report.report_date, reportLine: lightning.report_line, vertices };
    }
    if (reportLightning !== null) {
      const vertices: MainLine["vertices"] = [];
      for (const v of reportLightning.vertices) {
        const position = positionOfRow.get(v.row_index);
        const row = ganttRowOf.get(v.row_index);
        if (position !== undefined && row !== undefined) {
          vertices.push({
            position,
            point: v.point,
            judgement: v.judgement,
            title: `${row.wbs_code} ${row.name}：${JUDGEMENT_LABELS[v.judgement]}（${v.display_date}）`,
          });
        }
      }
      return { reportDate: reportLightning.report_date, reportLine: reportLightning.report_line, vertices };
    }
    return null;
  }, [lightning, reportLightning, positionOfActivity, positionOfRow, ganttRowOf]);

  // 範囲を広げる日付は GanttPage（非稼働日を受け取る範囲）と同じ関数で集める（7.3）。編集するときは後ろへ広げる
  const range = useMemo((): DateRange | null => {
    if (fixedRange !== null) {
      return fixedRange;
    }
    const extra = chartExtraDates(gantt.rows, { lightning, reportLightning, pastLines });
    return editing ? chartRange(editRangeBase(gantt), [...extra, ...editExtraDates(gantt)]) : chartRange(gantt, extra);
  }, [gantt, lightning, reportLightning, pastLines, fixedRange, editing]);

  // ドラッグの間の受け手は window に付ける（描画域の外へ出ても追う）。最新の値は ref から読む（state の更新関数の中で
  // 呼び手の操作を呼ばない：React は更新関数を 2 回呼ぶことがある）
  const latest = useRef({ edit, rows, range, scrollTop });
  useEffect(() => {
    latest.current = { edit, rows, range, scrollTop };
  });
  const dragRef = useRef<DragState | null>(null);
  function startDrag(next: DragState | null) {
    dragRef.current = next;
    setDrag(next);
  }
  const dragging = drag !== null;
  useEffect(() => {
    if (!dragging) {
      return;
    }
    const svgPoint = (event: MouseEvent) => {
      const rect = svgRef.current?.getBoundingClientRect();
      return { x: event.clientX - (rect?.left ?? 0), y: event.clientY - (rect?.top ?? 0) };
    };
    const update = (next: DragState | null) => {
      dragRef.current = next;
      setDrag(next);
    };
    function onMove(event: MouseEvent) {
      const d = dragRef.current;
      if (d === null) {
        return;
      }
      if (d.kind === "bar") {
        const dx = event.clientX - d.originX;
        update({ ...d, days: daysOf(dx, dayWidth), moved: d.moved || Math.abs(dx) >= DRAG_THRESHOLD });
      } else {
        const p = svgPoint(event);
        update({ ...d, toX: p.x, toY: p.y });
      }
    }
    function onUp(event: MouseEvent) {
      const d = dragRef.current;
      update(null);
      const { edit: current, rows: currentRows, range: currentRange, scrollTop: currentScroll } = latest.current;
      if (d === null || current === undefined) {
        return;
      }
      if (d.kind === "bar") {
        const dx = event.clientX - d.originX;
        const days = daysOf(dx, dayWidth);
        if ((d.moved || Math.abs(dx) >= DRAG_THRESHOLD) && days !== 0) {
          current.onBarDrop(d.activityId, d.how, days);
        }
        return;
      }
      // 落とした先は別の活動の予定のバー（7.3）。目盛りの帯（縦に送っても上に残る）の上は行に当てない
      const p = svgPoint(event);
      if (currentRange === null || p.y - currentScroll < HEADER_HEIGHT) {
        return;
      }
      const target = currentRows[Math.floor((p.y - HEADER_HEIGHT) / ROW_HEIGHT)];
      if (target?.kind !== "activity" || target.row.activity_id === d.activityId || target.row.planned === null) {
        return;
      }
      const planned = target.row.planned;
      const slack = planned.is_milestone ? 10 : 4;
      const x1 = LABEL_WIDTH + xOfPoint(currentRange.start, planned.start.calendar, dayWidth) - slack;
      const x2 = LABEL_WIDTH + xOfPoint(currentRange.start, planned.end.calendar, dayWidth) + slack;
      if (p.x >= x1 && p.x <= x2) {
        current.onLinkDrop(d.activityId, target.row.activity_id);
      }
    }
    function onKey(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        update(null);
      }
    }
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    window.addEventListener("keydown", onKey, true);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      window.removeEventListener("keydown", onKey, true);
    };
  }, [dragging, dayWidth]);

  // 上下の矢印などで選んだ行が見えていなければ、図を縦に送る（描画の済んでいない画面では何もしない）
  const cursorKey = edit?.cursor ?? null;
  useEffect(() => {
    const viewport = viewportRef.current;
    const position = cursorKey === null ? -1 : latest.current.rows.findIndex((r) => r.key === cursorKey);
    if (viewport === null || position < 0 || viewport.clientHeight <= HEADER_HEIGHT) {
      return;
    }
    const rowTopY = position * ROW_HEIGHT;
    const shownHeight = viewport.clientHeight - HEADER_HEIGHT;
    if (rowTopY < viewport.scrollTop) {
      viewport.scrollTop = rowTopY;
    } else if (rowTopY + ROW_HEIGHT > viewport.scrollTop + shownHeight) {
      viewport.scrollTop = rowTopY + ROW_HEIGHT - shownHeight;
    }
  }, [cursorKey]);

  if (range === null || (rows.length === 0 && !editing)) {
    return <p className="gantt-empty">描く活動がありません</p>;
  }

  const rowCount = rows.length;
  const top = HEADER_HEIGHT;
  const width = LABEL_WIDTH + rangeWidth(range, dayWidth);
  const height = top + rowCount * ROW_HEIGHT;
  const shown = visibleRows(scrollTop, viewportHeight - HEADER_HEIGHT, rowCount, ROW_HEIGHT);
  const visible = rows.slice(shown.first, shown.last + 1).map((r, i) => ({ r, position: shown.first + i }));
  const visibleActivities = visible.flatMap(({ r, position }) => (r.kind === "activity" ? [{ row: r.row, key: r.key, position }] : []));
  const visibleElements = visible.flatMap(({ r, position }) => (r.kind === "element" ? [{ r, position }] : []));

  const x = (point: AxisPoint) => LABEL_WIDTH + xOfPoint(range.start, point.calendar, dayWidth);
  const xDate = (date: string) => LABEL_WIDTH + xOfDate(range.start, date, dayWidth);
  const rowTop = (position: number) => top + position * ROW_HEIGHT;
  const rowCenter = (position: number) => rowTop(position) + ROW_HEIGHT / 2;
  const isVisible = (position: number) => position >= shown.first && position <= shown.last;

  const lightningVertices: (LineVertex & { judgement: Judgement; title: string })[] =
    mainLine === null
      ? []
      : mainLine.vertices.map((v) => ({ row: v.position, x: x(v.point), judgement: v.judgement, title: v.title }));
  // 範囲を固定したときは、行の見出しの列と範囲の外へはみ出す分を切り取る
  const plotClip = fixedRange === null ? undefined : `url(#${PLOT_CLIP_ID})`;
  const canDrag = edit !== undefined && edit.canEditSchedule;
  const cursorActivity =
    edit !== undefined && edit.selected.size === 1 && edit.cursor !== null && edit.selected.has(edit.cursor)
      ? visibleActivities.find((a) => a.key === edit.cursor)
      : undefined;

  function onScroll(event: UIEvent<HTMLDivElement>) {
    setScrollTop(event.currentTarget.scrollTop);
  }

  function focusViewport() {
    viewportRef.current?.focus({ preventScroll: true });
  }

  function onRowMouseDown(key: string, event: ReactMouseEvent) {
    if (edit === undefined || event.button !== 0) {
      return;
    }
    focusViewport();
    edit.onSelectRow(key, selectHow(event));
  }

  function onBarMouseDown(row: GanttRow, key: string, how: BarDrag, event: ReactMouseEvent) {
    if (edit === undefined || event.button !== 0) {
      return;
    }
    event.stopPropagation();
    event.preventDefault();
    focusViewport();
    const choose = selectHow(event);
    if (choose !== "only" || !edit.selected.has(key) || edit.selected.size !== 1) {
      edit.onSelectRow(key, choose);
    }
    if (choose === "only") {
      startDrag({ kind: "bar", how, activityId: row.activity_id, originX: event.clientX, days: 0, moved: false });
    }
  }

  function onLinkHandleMouseDown(activityId: number, fromX: number, fromY: number, event: ReactMouseEvent) {
    if (event.button !== 0) {
      return;
    }
    event.stopPropagation();
    event.preventDefault();
    focusViewport();
    startDrag({ kind: "link", activityId, fromX, fromY, toX: fromX, toY: fromY });
  }

  function bar(row: GanttRow, position: number, b: GanttBar, kind: "baseline" | "planned" | "actual") {
    const x1 = x(b.start);
    const x2 = x(b.end);
    const y = rowTop(position);
    const label = kind === "baseline" ? "基準" : kind === "planned" ? "予定" : "実績";
    const critical = kind === "planned" && row.is_critical;
    if (b.is_milestone) {
      const r = kind === "baseline" ? 4 : kind === "planned" ? 7 : 5;
      const fill = kind === "baseline" ? "#bdbdbd" : kind === "planned" ? "#90caf9" : "#1565c0";
      return (
        <polygon
          key={`${kind}-${row.activity_id}`}
          data-kind={b.kind}
          data-activity-id={row.activity_id}
          data-milestone="true"
          data-critical={critical ? "true" : undefined}
          points={diamond(x2, y + ROW_HEIGHT / 2, r)}
          fill={fill}
          stroke={critical ? CRITICAL_COLOR : "#424242"}
          strokeWidth={critical ? 2 : 1}
        >
          <title>{barTitle(`${row.name} ${label}`, b)}</title>
        </polygon>
      );
    }
    const geometry =
      kind === "baseline"
        ? { y: y + ROW_HEIGHT - 7, h: 4, fill: "#bdbdbd" }
        : kind === "planned"
          ? { y: y + 5, h: 12, fill: "#90caf9" }
          : { y: y + 9, h: 6, fill: "#1565c0" };
    return (
      <rect
        key={`${kind}-${row.activity_id}`}
        data-kind={b.kind}
        data-activity-id={row.activity_id}
        data-critical={critical ? "true" : undefined}
        data-open-ended={b.open_ended ? "true" : undefined}
        x={x1}
        y={geometry.y}
        width={Math.max(1, x2 - x1)}
        height={geometry.h}
        fill={geometry.fill}
        stroke={critical ? CRITICAL_COLOR : "none"}
        strokeWidth={critical ? 2 : 0}
      >
        <title>{barTitle(`${row.name} ${label}`, b)}</title>
      </rect>
    );
  }

  function summaryBar(r: ElementOutlineRow, position: number) {
    if (r.span === null) {
      return null;
    }
    const x1 = x(r.span.start);
    const x2 = Math.max(x1 + 1, x(r.span.end));
    return (
      <path
        key={`summary-${r.element.id}`}
        data-kind="SUMMARY"
        data-element-id={r.element.id}
        d={summaryPath(x1, x2, rowTop(position) + 8, 5)}
        fill={SUMMARY_COLOR}
        // 編集では行を選ぶクリックを下の受け手へ通す
        pointerEvents={editing ? "none" : undefined}
      >
        <title>{`${r.element.wbs_code} ${r.element.name} ${r.span.start.calendar.date}〜${r.span.end.calendar.date}`}</title>
      </path>
    );
  }

  // ドラッグ中の影（落とす先の日付を添える）
  function dragGhost() {
    if (drag === null) {
      return null;
    }
    if (drag.kind === "link") {
      return (
        <line
          data-ghost="link"
          x1={drag.fromX}
          y1={drag.fromY}
          x2={drag.toX}
          y2={drag.toY}
          strokeDasharray="4 3"
          style={{ stroke: ACCENT }}
          strokeWidth={1.5}
          pointerEvents="none"
        />
      );
    }
    const position = positionOfActivity.get(drag.activityId);
    const row = position === undefined ? undefined : rows[position];
    if (!drag.moved || position === undefined || row?.kind !== "activity" || row.row.planned === null) {
      return null;
    }
    const planned = row.row.planned;
    const dx = drag.days * dayWidth;
    const x1 = x(planned.start) + (drag.how === "move" ? dx : 0);
    // 右端を予定開始日より前へ寄せたときは、送る値（開始日。期間 1 稼働日）と同じに見せる
    const x2 = drag.how === "move" ? x(planned.end) + dx : Math.max(x(planned.end) + dx, x1 + dayWidth);
    const proposed = drag.how === "move" ? addDays(planned.start_date, drag.days) : addDays(planned.end_date, drag.days);
    const date = drag.how === "resize" && proposed < planned.start_date ? planned.start_date : proposed;
    const y = rowTop(position);
    return (
      <g data-ghost={drag.how} pointerEvents="none">
        {planned.is_milestone ? (
          <polygon points={diamond(x2, y + ROW_HEIGHT / 2, 7)} fill="none" strokeDasharray="3 2" style={{ stroke: ACCENT }} />
        ) : (
          <rect
            x={Math.min(x1, x2)}
            y={y + 4}
            width={Math.max(1, Math.abs(x2 - x1))}
            height={14}
            fill="none"
            strokeDasharray="3 2"
            style={{ stroke: ACCENT }}
          />
        )}
        <text x={(drag.how === "move" ? x1 : x2) + 4} y={y - 2} fontSize={11} style={{ fill: ACCENT }}>
          {date}
        </text>
      </g>
    );
  }

  const renamingPosition = edit === undefined || edit.renaming === null ? -1 : rows.findIndex((r) => r.key === edit.renaming);
  const renamingRow = renamingPosition < 0 ? undefined : rows[renamingPosition];

  return (
    <div
      ref={viewportRef}
      className="gantt-viewport"
      // 編集では行が少なければ縮める（下の選んだ行の欄を図に寄せる）。会議レポートは高さを固定したまま（7.4 の印刷）
      style={{ ...(editing ? { maxHeight: viewportHeight } : { height: viewportHeight }), overflow: "auto", position: "relative" }}
      onScroll={onScroll}
      data-testid="gantt-viewport"
      tabIndex={editing ? 0 : undefined}
      aria-label={editing ? "ガントの編集（上下の矢印で行を選ぶ。F2 で名前・Insert で行を足す・Delete で消す）" : undefined}
      onKeyDown={edit?.onKeyDown}
    >
      <svg
        ref={svgRef}
        role="img"
        aria-label="ガントチャート"
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        data-row-count={rowCount}
        data-unit={unit}
        style={drag !== null ? { cursor: drag.kind === "link" ? "crosshair" : drag.how === "move" ? "grabbing" : "ew-resize" } : undefined}
      >
        <defs>
          <marker id={ARROW_ID} viewBox="0 0 8 8" refX="8" refY="4" markerWidth="6" markerHeight="6" orient="auto">
            <path d="M0,0 L8,4 L0,8 z" style={{ fill: INK_MUTED }} />
          </marker>
          <clipPath id={PLOT_CLIP_ID}>
            <rect x={LABEL_WIDTH} y={0} width={width - LABEL_WIDTH} height={height} />
          </clipPath>
        </defs>

        {/* ① 非稼働日の帯 */}
        <g data-layer="non-working" clipPath={plotClip}>
          {gantt.non_working_days
            .filter((d) => d >= range.start && d <= range.end)
            .map((d) => (
              <rect
                key={d}
                data-date={d}
                x={xDate(d)}
                y={top}
                width={dayWidth}
                height={rowCount * ROW_HEIGHT}
                fill="#eeeeee"
              />
            ))}
        </g>

        {/* 編集：選んだ行の地と、行を選ぶ受け手 */}
        {edit !== undefined && (
          <g data-layer="edit-rows">
            {visible.map(({ r, position }) => (
              <rect
                key={r.key}
                data-row-key={r.key}
                data-selected={edit.selected.has(r.key) ? "true" : undefined}
                x={LABEL_WIDTH}
                y={rowTop(position)}
                width={width - LABEL_WIDTH}
                height={ROW_HEIGHT}
                style={{ fill: edit.selected.has(r.key) ? SELECTED_FILL : "transparent" }}
                fillOpacity={edit.selected.has(r.key) ? 0.6 : 1}
                onMouseDown={(e) => onRowMouseDown(r.key, e)}
              />
            ))}
          </g>
        )}

        {/* ② ベースラインのバー（細い灰色） */}
        <g data-layer="baseline" clipPath={plotClip} pointerEvents={editing ? "none" : undefined}>
          {visibleActivities.map(({ row, position }) => row.baseline && bar(row, position, row.baseline, "baseline"))}
        </g>

        {/* ③ 予定のバー（クリティカルは赤い枠。マイルストーンは菱形）と要素の行の要約のバー */}
        <g data-layer="planned" clipPath={plotClip} pointerEvents={editing ? "none" : undefined}>
          {visibleActivities.map(({ row, position }) => row.planned && bar(row, position, row.planned, "planned"))}
          {visibleElements.map(({ r, position }) => summaryBar(r, position))}
        </g>

        {/* ④ 実績のバー（未完了なら報告日まで） */}
        <g data-layer="actual" clipPath={plotClip} pointerEvents={editing ? "none" : undefined}>
          {visibleActivities.map(({ row, position }) => row.actual && bar(row, position, row.actual, "actual"))}
        </g>

        {/* バーの上の担当者名と進捗率（SCH-13） */}
        <g data-layer="bar-labels" clipPath={plotClip} pointerEvents={editing ? "none" : undefined}>
          {visibleActivities.map(({ row, position }) => {
            const text = [row.assignees.join("、"), percentText(row.percent_complete)].filter((t) => t !== "").join(" ");
            if (text === "" || row.planned === null) {
              return null;
            }
            // 予定と実績のうち右へ延びる方のバーの右端の右に置く（未完了の実績のバーは報告日まで延びるので、予定の右端に
            // 置くと濃い青のバーに重なって読めない）
            const barEnd = Math.max(x(row.planned.end), row.actual === null ? -Infinity : x(row.actual.end));
            const labelLeft = barEnd + 10;
            // 範囲を固定したとき（会議レポート）は、範囲の始まりより前で終わるバーのラベルを描かない（左端で切れた断片を残さない）。
            // 右端より右から始まるラベルも描かない
            if (fixedRange !== null && (barEnd <= LABEL_WIDTH || labelLeft >= width)) {
              return null;
            }
            return (
              <text
                key={row.activity_id}
                data-activity-id={row.activity_id}
                x={labelLeft}
                y={rowCenter(position) + 4}
                fontSize={11}
                style={{ fill: INK_MUTED }}
              >
                {text}
              </text>
            );
          })}
        </g>

        {/* ⑤ 依存の矢印（先行の終了から後続の開始へ）。編集では選べる（太い透明な線で受ける） */}
        <g data-layer="links" clipPath={plotClip}>
          {gantt.links.map((link) => {
            const from = positionOfRow.get(link.from_row);
            const to = positionOfRow.get(link.to_row);
            if (link.start === null || link.end === null || from === undefined || to === undefined) {
              return null;
            }
            if (Math.max(from, to) < shown.first || Math.min(from, to) > shown.last) {
              return null;
            }
            const x1 = x(link.start);
            const x2 = x(link.end);
            const key = linkKey(link.predecessor_id, link.successor_id);
            const d = `M${x1},${rowCenter(from)} H${x1 + 6} V${rowCenter(to)} H${x2}`;
            const chosen = edit?.selectedLink === key;
            return (
              <Fragment key={key}>
                <path
                  data-link={key}
                  data-selected={chosen ? "true" : undefined}
                  d={d}
                  fill="none"
                  style={{ stroke: chosen ? ACCENT : INK_MUTED }}
                  strokeWidth={chosen ? 2 : 1}
                  markerEnd={`url(#${ARROW_ID})`}
                />
                {canDrag && (
                  <path
                    data-link-hit={key}
                    d={d}
                    fill="none"
                    stroke="transparent"
                    strokeWidth={10}
                    pointerEvents="stroke"
                    style={{ cursor: "pointer" }}
                    onMouseDown={(e) => {
                      if (e.button === 0) {
                        e.stopPropagation();
                        focusViewport();
                        edit?.onSelectLink(key);
                      }
                    }}
                  >
                    <title>{`依存 ${link.predecessor_id}→${link.successor_id}（選んで Delete で外す）`}</title>
                  </path>
                )}
              </Fragment>
            );
          })}
        </g>

        {/* ⑥ 報告日線（縦の破線） */}
        <g data-layer="report-line" clipPath={plotClip} pointerEvents={editing ? "none" : undefined}>
          {mainLine !== null && (
            <line
              x1={x(mainLine.reportLine)}
              x2={x(mainLine.reportLine)}
              y1={top}
              y2={height}
              stroke="#e65100"
              strokeDasharray="4 3"
            >
              <title>{`報告日 ${mainLine.reportDate}`}</title>
            </line>
          )}
        </g>

        {/* ⑦ イナズマ線（判定で頂点の色を変える） */}
        <g data-layer="lightning" clipPath={plotClip} pointerEvents={editing ? "none" : undefined}>
          {mainLine !== null && (
            <>
              <polyline
                points={lightningPolyline(lightningVertices, x(mainLine.reportLine), rowCount, ROW_HEIGHT, top)}
                fill="none"
                stroke="#e65100"
                strokeWidth={1.5}
              />
              {lightningVertices
                .filter((v) => isVisible(v.row))
                .map((v) => (
                  <Fragment key={v.row}>
                    <circle
                      data-judgement={v.judgement}
                      cx={v.x}
                      cy={rowCenter(v.row)}
                      r={4}
                      fill={judgementColor(v.judgement)}
                    >
                      <title>{v.title}</title>
                    </circle>
                    <VertexMarkPath judgement={v.judgement} cx={v.x} cy={rowCenter(v.row)} r={4} />
                  </Fragment>
                ))}
            </>
          )}
        </g>

        {/* ⑧ 過去の線（選んだ報告日ごと。線の種類を変える） */}
        <g data-layer="past-lines" clipPath={plotClip} pointerEvents={editing ? "none" : undefined}>
          {pastLines.map((line) => {
            const vertices: (LineVertex & { judgement: OverlaidLine["vertices"][number]["judgement"] })[] = [];
            for (const v of line.vertices) {
              const position = positionOfRow.get(v.row_index);
              if (position !== undefined) {
                vertices.push({ row: position, x: x(v.point), judgement: v.judgement });
              }
            }
            const dash = pastLineDash(line.style_index);
            const reportX = x(line.report_line);
            return (
              <g key={line.report_id} data-report-id={line.report_id} data-dash={dash}>
                <line
                  x1={reportX}
                  x2={reportX}
                  y1={top}
                  y2={height}
                  style={{ stroke: LINE_MUTED }}
                  strokeDasharray={dash}
                />
                <polyline
                  points={lightningPolyline(vertices, reportX, rowCount, ROW_HEIGHT, top)}
                  fill="none"
                  style={{ stroke: INK_MUTED }}
                  strokeDasharray={dash}
                >
                  <title>{`報告日 ${line.report_date} の線`}</title>
                </polyline>
                {vertices
                  .filter((v) => isVisible(v.row))
                  .map((v) => (
                    <Fragment key={v.row}>
                      <circle
                        data-judgement={v.judgement}
                        cx={v.x}
                        cy={rowCenter(v.row)}
                        r={2.5}
                        fill={judgementColor(v.judgement)}
                      />
                      <VertexMarkPath judgement={v.judgement} cx={v.x} cy={rowCenter(v.row)} r={2.5} />
                    </Fragment>
                  ))}
              </g>
            );
          })}
        </g>

        {/* 編集：希望開始日の印・バーの掴み（本体・右端）・後続をつなぐ丸・ドラッグの影 */}
        {edit !== undefined && (
          <g data-layer="edit-handles">
            {visibleActivities.map(({ row, position }) => {
              const requested = edit.requestedStart.get(row.activity_id);
              if (requested === undefined) {
                return null;
              }
              const mx = xDate(requested);
              const y = rowTop(position);
              return (
                <polygon
                  key={`requested-${row.activity_id}`}
                  data-requested-start={row.activity_id}
                  points={`${mx - 4},${y + 1} ${mx + 4},${y + 1} ${mx},${y + 6}`}
                  style={{ fill: INK }}
                >
                  <title>{`希望開始日 ${requested}`}</title>
                </polygon>
              );
            })}
            {canDrag &&
              visibleActivities.map(({ row, key, position }) => {
                if (row.planned === null) {
                  return null;
                }
                const x1 = x(row.planned.start);
                const x2 = x(row.planned.end);
                const y = rowTop(position);
                if (row.planned.is_milestone) {
                  return (
                    <rect
                      key={`grip-${row.activity_id}`}
                      data-handle="move"
                      data-activity-id={row.activity_id}
                      x={x2 - 8}
                      y={y + 4}
                      width={16}
                      height={ROW_HEIGHT - 8}
                      fill="transparent"
                      style={{ cursor: "grab" }}
                      onMouseDown={(e) => onBarMouseDown(row, key, "move", e)}
                    >
                      <title>{`${row.name}：ドラッグで日付を動かす`}</title>
                    </rect>
                  );
                }
                const body = Math.max(1, x2 - x1 - RESIZE_GRIP);
                return (
                  <Fragment key={`grip-${row.activity_id}`}>
                    <rect
                      data-handle="move"
                      data-activity-id={row.activity_id}
                      x={x1}
                      y={y + 4}
                      width={body}
                      height={14}
                      fill="transparent"
                      style={{ cursor: "grab" }}
                      onMouseDown={(e) => onBarMouseDown(row, key, "move", e)}
                    >
                      <title>{`${row.name}：ドラッグで開始を動かす`}</title>
                    </rect>
                    <rect
                      data-handle="resize"
                      data-activity-id={row.activity_id}
                      x={x1 + body}
                      y={y + 4}
                      width={RESIZE_GRIP + 2}
                      height={14}
                      fill="transparent"
                      style={{ cursor: "ew-resize" }}
                      onMouseDown={(e) => onBarMouseDown(row, key, "resize", e)}
                    >
                      <title>{`${row.name}：ドラッグで終わりを動かす`}</title>
                    </rect>
                  </Fragment>
                );
              })}
            {canDrag && cursorActivity !== undefined && cursorActivity.row.planned !== null && (
              <circle
                data-handle="link"
                data-activity-id={cursorActivity.row.activity_id}
                cx={x(cursorActivity.row.planned.end) + (cursorActivity.row.planned.is_milestone ? 12 : 8)}
                cy={rowCenter(cursorActivity.position)}
                r={4.5}
                strokeWidth={1.5}
                style={{ fill: SURFACE, stroke: ACCENT, cursor: "crosshair" }}
                onMouseDown={(e) =>
                  onLinkHandleMouseDown(
                    cursorActivity.row.activity_id,
                    x(cursorActivity.row.planned!.end) + (cursorActivity.row.planned!.is_milestone ? 12 : 8),
                    rowCenter(cursorActivity.position),
                    e,
                  )
                }
              >
                <title>後続の活動のバーへドラッグして依存をつなぐ</title>
              </circle>
            )}
            {dragGhost()}
          </g>
        )}

        {/* 行の見出し（WBS ID・名前。活動はフロートも。要素の行は太字） */}
        <g data-layer="row-labels">
          {visible.map(({ r, position }) => {
            const chosen = edit?.selected.has(r.key) ?? false;
            const isCursor = edit?.cursor === r.key;
            const common = {
              onMouseDown: (e: ReactMouseEvent) => onRowMouseDown(r.key, e),
              onDoubleClick: edit === undefined ? undefined : () => edit.onStartRename(r.key),
            };
            const background = (
              <rect
                x={0}
                y={rowTop(position)}
                width={LABEL_WIDTH}
                height={ROW_HEIGHT}
                style={{ fill: chosen ? SELECTED_FILL : SURFACE, stroke: isCursor ? ACCENT : "none" }}
              />
            );
            if (r.kind === "element") {
              return (
                <g key={r.key} data-element-id={r.element.id} data-row-key={r.key} {...common}>
                  {background}
                  <text
                    x={labelX(r.depth, indent)}
                    y={rowCenter(position) + 4}
                    fontSize={12}
                    fontWeight={600}
                    style={{ fill: INK }}
                  >
                    {`${r.element.wbs_code} ${r.element.name}`}
                  </text>
                </g>
              );
            }
            const row = r.row;
            return (
              <g key={r.key} data-row={row.row_index} data-activity-id={row.activity_id} data-row-key={r.key} {...common}>
                {background}
                <text
                  x={labelX(r.depth, indent)}
                  y={rowCenter(position) + 4}
                  fontSize={12}
                  style={{ fill: row.is_critical ? CRITICAL_COLOR : INK }}
                >
                  {`${row.wbs_code} ${row.name}`}
                  {row.total_float !== null ? `（余裕 ${row.total_float} 日）` : ""}
                </text>
              </g>
            );
          })}
        </g>

        {/* 目盛り（縦に送っても上に残す） */}
        <g data-layer="header" transform={`translate(0, ${scrollTop})`}>
          <rect x={0} y={0} width={width} height={HEADER_HEIGHT} style={{ fill: SURFACE_MUTED }} />
          <line x1={0} x2={width} y1={HEADER_HEIGHT - 0.5} y2={HEADER_HEIGHT - 0.5} style={{ stroke: LINE_MUTED }} />
          {ticks(range, unit).map((t, i) => {
            const tx = LABEL_WIDTH + xOfDate(range.start, t.date, dayWidth) + 2;
            if (unit !== "day") {
              return (
                <text key={t.date} x={tx} y={20} fontSize={10} style={{ fill: INK_MUTED }}>
                  {t.label}
                </text>
              );
            }
            // 日の単位は 2 段：上に年月（範囲の最初と月の 1 日）、下に日。1 段だと「11/12」が隣の日の数字に重なる
            const [year, month, day] = t.date.split("-");
            return (
              <g key={t.date}>
                {(i === 0 || day === "01") && (
                  <text x={tx} y={13} fontSize={10} style={{ fill: INK_MUTED }}>
                    {`${year}年${Number(month)}月`}
                  </text>
                )}
                <text x={tx} y={27} fontSize={10} style={{ fill: INK_MUTED }}>
                  {Number(day)}
                </text>
              </g>
            );
          })}
        </g>
      </svg>
      {edit !== undefined && renamingRow !== undefined && (
        <RenameInput
          key={renamingRow.key}
          initial={renamingRow.kind === "element" ? renamingRow.element.name : renamingRow.row.name}
          style={{
            position: "absolute",
            top: rowTop(renamingPosition) + 2,
            left: labelX(renamingRow.depth, indent) - 4,
            width: LABEL_WIDTH - labelX(renamingRow.depth, indent),
            height: ROW_HEIGHT - 4,
            boxSizing: "border-box",
            fontSize: 12,
          }}
          onCommit={(name, keepFocus) => {
            edit.onRename(renamingRow.key, name);
            if (keepFocus) {
              focusViewport();
            }
          }}
          onCancel={() => {
            edit.onCancelRename();
            focusViewport();
          }}
        />
      )}
    </div>
  );
}

/** 名前を変える入力（Enter で保存、Esc で取り消す。外へ出たら保存する） */
function RenameInput({
  initial,
  style,
  onCommit,
  onCancel,
}: {
  initial: string;
  style: CSSProperties;
  onCommit: (name: string, keepFocus: boolean) => void;
  onCancel: () => void;
}) {
  const done = useRef(false);
  return (
    <input
      aria-label="名前"
      defaultValue={initial}
      autoFocus
      style={style}
      onFocus={(e) => e.currentTarget.select()}
      onKeyDown={(e) => {
        e.stopPropagation();
        // IME の確定の Enter は保存にしない（Safari は確定の後に keyCode 229 の keydown を出すことがある）
        if (e.key === "Enter" && !e.nativeEvent.isComposing && e.keyCode !== 229) {
          e.preventDefault();
          done.current = true;
          onCommit(e.currentTarget.value, true);
        } else if (e.key === "Escape") {
          e.preventDefault();
          done.current = true;
          onCancel();
        }
      }}
      onBlur={(e) => {
        if (!done.current) {
          done.current = true;
          onCommit(e.currentTarget.value, false);
        }
      }}
    />
  );
}

/** 頂点に重ねる印（7.3：完了＝白いチェック、未着手（予定内）＝白い穴。色は予定どおりのまま） */
function VertexMarkPath({ judgement, cx, cy, r }: { judgement: Judgement; cx: number; cy: number; r: number }) {
  const mark = vertexMark(judgement, cx, cy, r);
  if (mark === null) {
    return null;
  }
  return (
    <path
      data-mark={mark.kind}
      d={mark.d}
      fill={mark.kind === "hole" ? "#ffffff" : "none"}
      stroke={mark.kind === "check" ? "#ffffff" : "none"}
      strokeWidth={Math.max(1, r * 0.35)}
      strokeLinecap="round"
      strokeLinejoin="round"
      pointerEvents="none"
    />
  );
}
