import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { makeBar, makeGantt, makeGanttRow } from "../../test/schFixtures";
import GanttChart from "./GanttChart";
import { DAY_WIDTH, LABEL_WIDTH } from "./scale";

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
