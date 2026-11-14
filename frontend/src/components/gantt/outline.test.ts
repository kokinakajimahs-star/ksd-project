import { describe, expect, it } from "vitest";

import { makeBar, makeGanttRow, point } from "../../test/schFixtures";
import { makeElement, sampleElements } from "../../test/wbsFixtures";
import { activityKey, activityOutline, buildOutline, elementKey } from "./outline";

// 1.0 要件定義 ─ 1.1 業務整理（WP 101）・1.2 画面設計（WP 102）、2.0 管理
const rows = [
  makeGanttRow({ row_index: 0, activity_id: 21, work_package_id: 101, planned: makeBar("PLANNED", "2026-12-03", "2026-12-08") }),
  makeGanttRow({ row_index: 1, activity_id: 22, work_package_id: 101, planned: makeBar("PLANNED", "2026-12-01", "2026-12-04") }),
  makeGanttRow({ row_index: 2, activity_id: 23, work_package_id: 102, planned: makeBar("PLANNED", "2026-12-09", "2026-12-15") }),
];

describe("activityOutline", () => {
  it("活動の行だけを、字下げは今までどおり WP の階層で並べる", () => {
    const outline = activityOutline(rows);
    expect(outline.map((r) => r.key)).toEqual(["a21", "a22", "a23"]);
    expect(outline.map((r) => r.depth)).toEqual([2, 2, 2]);
  });
});

describe("buildOutline", () => {
  it("要素の行を配下の行の前に、WP の行の後にその活動をサーバの順で置く", () => {
    const outline = buildOutline(sampleElements(), rows, true);
    expect(outline.map((r) => r.key)).toEqual(["e100", "e101", "a21", "a22", "e102", "a23", "e103"]);
    expect(outline.map((r) => r.depth)).toEqual([1, 2, 3, 3, 2, 3, 1]);
  });

  it("要約のバーは配下の活動の予定のバーの最も早い開始〜最も遅い終了", () => {
    const outline = buildOutline(sampleElements(), rows, true);
    const span = (key: string) => {
      const row = outline.find((r) => r.key === key);
      return row?.kind === "element" ? row.span : undefined;
    };
    expect(span("e101")).toEqual({ start: point("2026-12-01", 0), end: point("2026-12-08", 1) });
    expect(span("e102")).toEqual({ start: point("2026-12-09", 0), end: point("2026-12-15", 1) });
    expect(span("e100")).toEqual({ start: point("2026-12-01", 0), end: point("2026-12-15", 1) });
    expect(span("e103")).toBeNull();
  });

  it("同じ日なら割合で比べる", () => {
    const early = makeGanttRow({
      activity_id: 31,
      work_package_id: 101,
      planned: { ...makeBar("PLANNED", "2026-12-02", "2026-12-02"), start: point("2026-12-02", 0.5), end: point("2026-12-02", 0.75) },
    });
    const late = makeGanttRow({
      activity_id: 32,
      work_package_id: 101,
      planned: { ...makeBar("PLANNED", "2026-12-02", "2026-12-02"), start: point("2026-12-02", 0.25), end: point("2026-12-02", 0.5) },
    });
    const wp = buildOutline(sampleElements(), [early, late], true).find((r) => r.key === "e101");
    expect(wp?.kind === "element" ? wp.span : undefined).toEqual({ start: point("2026-12-02", 0.25), end: point("2026-12-02", 0.75) });
  });

  it("予定の無い活動は要約のバーに入れないが、配下に活動があることには数える", () => {
    const outline = buildOutline(sampleElements(), [makeGanttRow({ activity_id: 21, work_package_id: 102, planned: null })], false);
    expect(outline.map((r) => r.key)).toEqual(["e100", "e102", "a21"]);
    const wp = outline.find((r) => r.key === "e102");
    expect(wp?.kind === "element" ? [wp.span, wp.hasActivities] : undefined).toEqual([null, true]);
  });

  it("編集していないときは、配下に活動の無い要素の行を出さない", () => {
    const outline = buildOutline(sampleElements(), rows.slice(0, 2), false);
    expect(outline.map((r) => r.key)).toEqual(["e100", "e101", "a21", "a22"]);
  });

  it("木に無い WP の活動は落とさずに末尾へ置き、親が木に無い要素は最上位として扱う", () => {
    const elements = [makeElement({ id: 102, parent_id: 999, kind: "WORK_PACKAGE", name: "画面設計", wbs_code: "1.2", depth: 2 })];
    const outline = buildOutline(elements, rows, false);
    expect(outline.map((r) => r.key)).toEqual(["e102", "a23", "a21", "a22"]);
    expect(outline.map((r) => r.depth)).toEqual([2, 3, 3, 3]);
  });

  it("鍵は要素と活動で重ならない", () => {
    expect(elementKey(21)).not.toBe(activityKey(21));
  });
});
