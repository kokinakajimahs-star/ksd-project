// ガントとイナズマ線の描画（設計書 7.3）。SVG のレイヤ（下から）：
// ①非稼働日の帯 ②ベースラインのバー ③予定のバー（クリティカルは赤い枠） ④実績のバー ⑤依存の矢印
// ⑥報告日線 ⑦イナズマ線 ⑧過去の線。行の順はサーバが返す順（RULE-SCH-005）。見えている行だけを描く（NFR-02）
import { Fragment, useMemo, useState, type UIEvent } from "react";

import type { Judgement, LightningLine, OverlaidLine } from "../../api/prg";
import type { ReportLightning } from "../../api/rpt";
import type { AxisPoint, Gantt, GanttBar, GanttRow } from "../../api/sch";
import {
  chartRange,
  type DateRange,
  DAY_WIDTH,
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
}

const CRITICAL_COLOR = "#d32f2f";
const ARROW_ID = "gantt-arrowhead";
const PLOT_CLIP_ID = "gantt-plot-clip";

/** ⑥⑦ に描く線：報告日線と、判定の付いた頂点（描く行の位置と x） */
interface MainLine {
  reportDate: string;
  reportLine: AxisPoint;
  vertices: { position: number; point: AxisPoint; judgement: Judgement; title: string }[];
}

function percentText(value: number | null): string {
  return value === null ? "" : `${Math.round(value * 10) / 10}%`;
}

function barTitle(label: string, bar: GanttBar): string {
  return bar.is_milestone ? `${label} ${bar.end_date}` : `${label} ${bar.start_date}〜${bar.end_date}`;
}

function diamond(x: number, y: number, r: number): string {
  return `${x},${y - r} ${x + r},${y} ${x},${y + r} ${x - r},${y}`;
}

export default function GanttChart({
  gantt,
  unit,
  lightning = null,
  pastLines = [],
  reportLightning = null,
  fixedRange = null,
  viewportHeight = 480,
}: GanttChartProps) {
  const [scrollTop, setScrollTop] = useState(0);
  const dayWidth = DAY_WIDTH[unit];

  // 行の番号（row_index）→ 描く位置、活動 → 描く位置
  const { positionOfRow, positionOfActivity } = useMemo(() => {
    const byRow = new Map<number, number>();
    const byActivity = new Map<number, number>();
    gantt.rows.forEach((row, i) => {
      byRow.set(row.row_index, i);
      byActivity.set(row.activity_id, i);
    });
    return { positionOfRow: byRow, positionOfActivity: byActivity };
  }, [gantt.rows]);

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
        const row = position === undefined ? undefined : gantt.rows[position];
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
  }, [gantt.rows, lightning, reportLightning, positionOfActivity, positionOfRow]);

  const range = useMemo((): DateRange | null => {
    if (fixedRange !== null) {
      return fixedRange;
    }
    const extra: string[] = [];
    if (mainLine !== null) {
      extra.push(mainLine.reportLine.calendar.date);
      for (const v of mainLine.vertices) {
        extra.push(v.point.calendar.date);
      }
    }
    for (const line of pastLines) {
      extra.push(line.report_line.calendar.date);
      for (const v of line.vertices) {
        extra.push(v.point.calendar.date);
      }
    }
    return chartRange(gantt, extra);
  }, [gantt, mainLine, pastLines, fixedRange]);

  if (range === null || gantt.rows.length === 0) {
    return <p className="gantt-empty">描く活動がありません</p>;
  }

  const rowCount = gantt.rows.length;
  const top = HEADER_HEIGHT;
  const width = LABEL_WIDTH + rangeWidth(range, dayWidth);
  const height = top + rowCount * ROW_HEIGHT;
  const shown = visibleRows(scrollTop, viewportHeight - HEADER_HEIGHT, rowCount, ROW_HEIGHT);
  const visible = gantt.rows.slice(shown.first, shown.last + 1);

  const x = (point: AxisPoint) => LABEL_WIDTH + xOfPoint(range.start, point.calendar, dayWidth);
  const rowTop = (position: number) => top + position * ROW_HEIGHT;
  const rowCenter = (position: number) => rowTop(position) + ROW_HEIGHT / 2;
  const isVisible = (position: number) => position >= shown.first && position <= shown.last;

  const lightningVertices: (LineVertex & { judgement: Judgement; title: string })[] =
    mainLine === null
      ? []
      : mainLine.vertices.map((v) => ({ row: v.position, x: x(v.point), judgement: v.judgement, title: v.title }));
  // 範囲を固定したときは、行の見出しの列と範囲の外へはみ出す分を切り取る
  const plotClip = fixedRange === null ? undefined : `url(#${PLOT_CLIP_ID})`;

  function onScroll(event: UIEvent<HTMLDivElement>) {
    setScrollTop(event.currentTarget.scrollTop);
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

  return (
    <div
      className="gantt-viewport"
      style={{ height: viewportHeight, overflow: "auto", position: "relative" }}
      onScroll={onScroll}
      data-testid="gantt-viewport"
    >
      <svg
        role="img"
        aria-label="ガントチャート"
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        data-row-count={rowCount}
        data-unit={unit}
      >
        <defs>
          <marker id={ARROW_ID} viewBox="0 0 8 8" refX="8" refY="4" markerWidth="6" markerHeight="6" orient="auto">
            <path d="M0,0 L8,4 L0,8 z" fill="#616161" />
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
                x={LABEL_WIDTH + xOfDate(range.start, d, dayWidth)}
                y={top}
                width={dayWidth}
                height={rowCount * ROW_HEIGHT}
                fill="#eeeeee"
              />
            ))}
        </g>

        {/* ② ベースラインのバー（細い灰色） */}
        <g data-layer="baseline" clipPath={plotClip}>
          {visible.map((row, i) => row.baseline && bar(row, shown.first + i, row.baseline, "baseline"))}
        </g>

        {/* ③ 予定のバー（クリティカルは赤い枠。マイルストーンは菱形） */}
        <g data-layer="planned" clipPath={plotClip}>
          {visible.map((row, i) => row.planned && bar(row, shown.first + i, row.planned, "planned"))}
        </g>

        {/* ④ 実績のバー（未完了なら報告日まで） */}
        <g data-layer="actual" clipPath={plotClip}>
          {visible.map((row, i) => row.actual && bar(row, shown.first + i, row.actual, "actual"))}
        </g>

        {/* バーの上の担当者名と進捗率（SCH-13） */}
        <g data-layer="bar-labels" clipPath={plotClip}>
          {visible.map((row, i) => {
            const text = [row.assignees.join("、"), percentText(row.percent_complete)].filter((t) => t !== "").join(" ");
            if (text === "" || row.planned === null) {
              return null;
            }
            return (
              <text
                key={row.activity_id}
                data-activity-id={row.activity_id}
                x={x(row.planned.end) + 10}
                y={rowCenter(shown.first + i) + 4}
                fontSize={11}
                fill="#424242"
              >
                {text}
              </text>
            );
          })}
        </g>

        {/* ⑤ 依存の矢印（先行の終了から後続の開始へ） */}
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
            return (
              <path
                key={`${link.predecessor_id}-${link.successor_id}`}
                data-link={`${link.predecessor_id}-${link.successor_id}`}
                d={`M${x1},${rowCenter(from)} H${x1 + 6} V${rowCenter(to)} H${x2}`}
                fill="none"
                stroke="#616161"
                markerEnd={`url(#${ARROW_ID})`}
              />
            );
          })}
        </g>

        {/* ⑥ 報告日線（縦の破線） */}
        <g data-layer="report-line" clipPath={plotClip}>
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
        <g data-layer="lightning" clipPath={plotClip}>
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
        <g data-layer="past-lines" clipPath={plotClip}>
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
                <line x1={reportX} x2={reportX} y1={top} y2={height} stroke="#9e9e9e" strokeDasharray={dash} />
                <polyline
                  points={lightningPolyline(vertices, reportX, rowCount, ROW_HEIGHT, top)}
                  fill="none"
                  stroke="#757575"
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

        {/* 行の見出し（WBS ID・活動名・フロート） */}
        <g data-layer="row-labels">
          {visible.map((row, i) => {
            const position = shown.first + i;
            return (
              <g key={row.activity_id} data-row={row.row_index} data-activity-id={row.activity_id}>
                <rect x={0} y={rowTop(position)} width={LABEL_WIDTH} height={ROW_HEIGHT} fill="#ffffff" />
                <text
                  x={8 + Math.max(0, row.depth - 1) * 8}
                  y={rowCenter(position) + 4}
                  fontSize={12}
                  fill={row.is_critical ? CRITICAL_COLOR : "#212121"}
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
          <rect x={0} y={0} width={width} height={HEADER_HEIGHT} fill="#fafafa" />
          {ticks(range, unit).map((t) => (
            <text key={t.date} x={LABEL_WIDTH + xOfDate(range.start, t.date, dayWidth) + 2} y={20} fontSize={10}>
              {t.label}
            </text>
          ))}
        </g>
      </svg>
    </div>
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
