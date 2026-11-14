import { fireEvent, render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  makeBar,
  makeGantt,
  makeGanttRow,
  makeLightningLine,
  makeLightningRow,
  makeOverlaidLine,
} from "../../test/schFixtures";
import { sampleElements } from "../../test/wbsFixtures";
import GanttChart from "./GanttChart";
import { buildOutline } from "./outline";
import { DAY_WIDTH, HEADER_HEIGHT, LABEL_WIDTH, ROW_HEIGHT } from "./scale";

const w = DAY_WIDTH.day;

function label(container: HTMLElement, activityId: number): Element | null {
  return container.querySelector(`[data-layer="bar-labels"] [data-activity-id="${activityId}"]`);
}

describe("バーの担当者名・進捗率のラベル（SCH-13・7.3）", () => {
  it("未完了の実績のバーが予定より右へ延びるときは、実績のバーの右に置く（濃い青のバーに重ねない）", () => {
    const gantt = makeGantt({
      range_start: "2026-12-01",
      range_end: "2026-12-31",
      rows: [
        makeGanttRow({
          row_index: 0,
          activity_id: 21,
          planned: makeBar("PLANNED", "2026-12-01", "2026-12-04"),
          actual: makeBar("ACTUAL", "2026-12-01", "2026-12-10", { open_ended: true }),
          assignees: ["実行 花子"],
          percent_complete: 70,
        }),
        makeGanttRow({
          row_index: 1,
          activity_id: 22,
          planned: makeBar("PLANNED", "2026-12-01", "2026-12-21"),
          actual: makeBar("ACTUAL", "2026-12-01", "2026-12-10", { open_ended: true }),
          assignees: ["計画 太郎"],
          percent_complete: 40,
        }),
      ],
    });
    const { container } = render(<GanttChart gantt={gantt} unit="day" />);

    const actual = container.querySelector('[data-layer="actual"] [data-activity-id="21"]')!;
    const actualRight = Number(actual.getAttribute("x")) + Number(actual.getAttribute("width"));
    expect(actualRight).toBe(LABEL_WIDTH + 10 * w);
    expect(label(container, 21)).toHaveAttribute("x", String(actualRight + 10));
    expect(label(container, 21)).toHaveTextContent("実行 花子 70%");
    // 実績が予定の内で止まっていれば、これまでどおり予定のバーの右
    expect(label(container, 22)).toHaveAttribute("x", String(LABEL_WIDTH + 21 * w + 10));
  });

  it("範囲を固定したとき（会議レポート）は、範囲の始まりより前で終わるバーのラベルを描かない（左端で切れた断片を残さない）", () => {
    const gantt = makeGantt({
      range_start: "2026-10-01",
      range_end: "2027-03-31",
      rows: [
        // 11/08 で終わる：ラベルが描画域の左端をまたいで切れていた
        makeGanttRow({
          row_index: 0,
          activity_id: 10,
          planned: makeBar("PLANNED", "2026-10-21", "2026-11-08"),
          assignees: ["実行担当A"],
          percent_complete: 100,
        }),
        // 11/11 の終わり（＝範囲の始まりの 0 時）で終わる：バーは見えない
        makeGanttRow({
          row_index: 1,
          activity_id: 11,
          planned: makeBar("PLANNED", "2026-10-21", "2026-11-11"),
          assignees: ["実行担当A"],
          percent_complete: 100,
        }),
        // 範囲の始まりをまたぐバーのラベルは描く
        makeGanttRow({
          row_index: 2,
          activity_id: 12,
          planned: makeBar("PLANNED", "2026-11-01", "2026-11-13"),
          assignees: ["実行担当A"],
          percent_complete: 100,
        }),
        // 範囲の終わりより後のバー：ラベルも範囲の右から始まる
        makeGanttRow({
          row_index: 3,
          activity_id: 13,
          planned: makeBar("PLANNED", "2027-01-10", "2027-01-20"),
          assignees: ["実行担当A"],
        }),
      ],
    });
    const { container } = render(
      <GanttChart gantt={gantt} unit="day" fixedRange={{ start: "2026-11-12", end: "2027-01-07" }} />,
    );

    expect(label(container, 10)).toBeNull();
    expect(label(container, 11)).toBeNull();
    expect(label(container, 12)).toHaveAttribute("x", String(LABEL_WIDTH + 2 * w + 10));
    expect(label(container, 13)).toBeNull();
  });
});

describe("要素の行を差し込んでも、イナズマ線・過去の線は活動の行に当てる（CR-024。7.3）", () => {
  it("頂点は活動の行の中心に描き、線は要素の行をまたいで次の活動の頂点へ結ぶ", () => {
    // 1.0 要件定義 ─ 1.1 業務整理（WP 101：活動 21）・1.2 画面設計（WP 102：活動 22）。2.0 管理は活動が無いので出さない
    const gantt = makeGantt({
      range_start: "2026-12-01",
      range_end: "2026-12-21",
      rows: [
        makeGanttRow({ row_index: 0, activity_id: 21, work_package_id: 101, planned: makeBar("PLANNED", "2026-12-01", "2026-12-10") }),
        makeGanttRow({ row_index: 1, activity_id: 22, work_package_id: 102, planned: makeBar("PLANNED", "2026-12-07", "2026-12-18") }),
      ],
    });
    const outline = buildOutline(sampleElements(), gantt.rows, false);
    expect(outline.map((r) => r.key)).toEqual(["e100", "e101", "a21", "e102", "a22"]);
    const lightning = makeLightningLine([
      makeLightningRow({ row_index: 0, activity_id: 21 }),
      makeLightningRow({ row_index: 1, activity_id: 22, judgement: "AHEAD" }),
    ]);
    const past = makeOverlaidLine();
    const pastVertex = { ...past.vertices[0]!, row_index: 1, activity_id: 22 };
    const { container } = render(
      <GanttChart gantt={gantt} unit="day" lightning={lightning} pastLines={[{ ...past, vertices: [pastVertex] }]} outline={outline} />,
    );
    const center = (position: number) => HEADER_HEIGHT + position * ROW_HEIGHT + ROW_HEIGHT / 2;
    const cys = (selector: string) => [...container.querySelectorAll(selector)].map((c) => Number(c.getAttribute("cy")));

    expect(cys('[data-layer="lightning"] circle[data-judgement]')).toEqual([center(2), center(4)]);
    const ys = container
      .querySelector('[data-layer="lightning"] polyline')!
      .getAttribute("points")!
      .split(" ")
      .map((point) => Number(point.split(",")[1]));
    expect(ys).toEqual([HEADER_HEIGHT, center(2), center(4), HEADER_HEIGHT + 5 * ROW_HEIGHT]);
    // 過去の線は row_index で当てる（サーバの行は増やしていない）
    expect(cys('[data-layer="past-lines"] circle[data-judgement]')).toEqual([center(4)]);
    // 要素の行には頂点を描かない
    expect(cys('[data-layer="lightning"] circle[data-judgement]')).not.toContain(center(3));
  });
});

describe("活動の行の見出し・バーのダブルクリックで詳細と進捗を開く（CR-027。7.3）", () => {
  const gantt = makeGantt({
    range_start: "2026-12-01",
    range_end: "2026-12-31",
    rows: [
      makeGanttRow({
        row_index: 0,
        activity_id: 21,
        planned: makeBar("PLANNED", "2026-12-01", "2026-12-04"),
        actual: makeBar("ACTUAL", "2026-12-01", "2026-12-03"),
        baseline: makeBar("BASELINE", "2026-12-01", "2026-12-05"),
      }),
    ],
  });

  it("編集していなくても、見出し・予定・実績・基準のバーのダブルクリックでその活動を渡す", () => {
    const opened: number[] = [];
    const { container } = render(<GanttChart gantt={gantt} unit="day" onOpenActivity={(id) => opened.push(id)} />);
    fireEvent.doubleClick(container.querySelector('[data-layer="row-labels"] [data-activity-id="21"]')!);
    for (const name of ["planned", "actual", "baseline"]) {
      fireEvent.doubleClick(container.querySelector(`[data-layer="${name}"] [data-activity-id="21"]`)!);
    }
    expect(opened).toEqual([21, 21, 21, 21]);
  });

  it("渡さなければダブルクリックで何もしない（会議レポート・共有ビュー）", () => {
    const { container } = render(<GanttChart gantt={gantt} unit="day" />);
    expect(() =>
      fireEvent.doubleClick(container.querySelector('[data-layer="planned"] [data-activity-id="21"]')!),
    ).not.toThrow();
  });
});
