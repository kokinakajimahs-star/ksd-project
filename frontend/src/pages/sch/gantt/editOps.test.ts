import { describe, expect, it } from "vitest";

import { buildOutline } from "../../../components/gantt/outline";
import { makeActivity, makeGanttRow } from "../../../test/schFixtures";
import { makeElement, sampleElements } from "../../../test/wbsFixtures";
import {
  activityStepPlan,
  addPlan,
  deletionPlan,
  elementStepPlan,
  groupActivitiesPlan,
  groupElementsPlan,
  indentPlan,
  moveTargetPlan,
  outdentPlan,
  rangeKeys,
  stepSelection,
} from "./editOps";

// 1.0 要件定義（100）─ 1.1 業務整理（WP 101）・1.2 画面設計（WP 102）、2.0 管理（103）
const elements = sampleElements();
const activities = [
  makeActivity({ id: 21, work_package_id: 101, sort_order: 1 }),
  makeActivity({ id: 22, work_package_id: 101, sort_order: 2 }),
  makeActivity({ id: 23, work_package_id: 101, sort_order: 3 }),
  makeActivity({ id: 24, work_package_id: 102, sort_order: 1 }),
];

describe("indentPlan", () => {
  it("直前の兄弟の子の末尾へ動かす", () => {
    expect(indentPlan(elements, 103)).toEqual({ ok: true, value: { parentId: 100, position: null } });
  });

  it("最初の兄弟はインデントできない", () => {
    expect(indentPlan(elements, 100).ok).toBe(false);
    expect(indentPlan(elements, 101).ok).toBe(false);
  });

  it("直前の兄弟が WP なら、その下には置けない（RULE-WBS-003）", () => {
    const result = indentPlan(elements, 102);
    expect(result).toEqual({ ok: false, reason: expect.stringContaining("1.1 業務整理") });
  });

  it("見つからない要素は理由を返す", () => {
    expect(indentPlan(elements, 999).ok).toBe(false);
  });
});

describe("outdentPlan", () => {
  it("親の次の兄弟へ動かす", () => {
    expect(outdentPlan(elements, 101)).toEqual({ ok: true, value: { parentId: null, position: 1 } });
    expect(outdentPlan(elements, 102)).toEqual({ ok: true, value: { parentId: null, position: 1 } });
  });

  it("最上位の要素はアウトデントできない", () => {
    expect(outdentPlan(elements, 100).ok).toBe(false);
    expect(outdentPlan(elements, 999).ok).toBe(false);
  });
});

describe("elementStepPlan", () => {
  it("兄弟の中で 1 つ上・下へ（位置は自分を除いた兄弟の中の位置）", () => {
    expect(elementStepPlan(elements, 102, -1)).toEqual({ ok: true, value: { parentId: 100, position: 0 } });
    expect(elementStepPlan(elements, 101, 1)).toEqual({ ok: true, value: { parentId: 100, position: 1 } });
    expect(elementStepPlan(elements, 100, 1)).toEqual({ ok: true, value: { parentId: null, position: 1 } });
  });

  it("端からは動かせない", () => {
    expect(elementStepPlan(elements, 101, -1).ok).toBe(false);
    expect(elementStepPlan(elements, 102, 1).ok).toBe(false);
    expect(elementStepPlan(elements, 999, 1).ok).toBe(false);
  });
});

describe("activityStepPlan", () => {
  it("同じ WP の中で 1 つ上・下へ", () => {
    expect(activityStepPlan(activities, 22, -1)).toEqual({ ok: true, value: 0 });
    expect(activityStepPlan(activities, 22, 1)).toEqual({ ok: true, value: 2 });
  });

  it("WP の端からは動かせない（別の WP へは移す先を選ぶ）", () => {
    expect(activityStepPlan(activities, 21, -1).ok).toBe(false);
    expect(activityStepPlan(activities, 23, 1).ok).toBe(false);
    expect(activityStepPlan(activities, 24, -1).ok).toBe(false);
    expect(activityStepPlan(activities, 999, 1).ok).toBe(false);
  });
});

describe("groupActivitiesPlan", () => {
  it("最初の活動の WP と同じ親の下、その WP の次に作り、選んだ活動を表示順で移す", () => {
    expect(groupActivitiesPlan(elements, activities, [24, 22])).toEqual({
      ok: true,
      value: { parentId: 100, position: 1, ids: [22, 24] },
    });
  });

  it("選んでいない・WP が木に無いときは理由を返す", () => {
    expect(groupActivitiesPlan(elements, activities, []).ok).toBe(false);
    expect(groupActivitiesPlan(elements, [makeActivity({ id: 9, work_package_id: 555 })], [9]).ok).toBe(false);
  });
});

describe("groupElementsPlan", () => {
  it("最初の要素と同じ親の下、最初の要素の位置に作る", () => {
    expect(groupElementsPlan(elements, [102, 101])).toEqual({ ok: true, value: { parentId: 100, position: 0, ids: [101, 102] } });
    expect(groupElementsPlan(elements, [103])).toEqual({ ok: true, value: { parentId: null, position: 1, ids: [103] } });
  });

  it("選んだ要素の配下は一緒に動くので外す", () => {
    expect(groupElementsPlan(elements, [100, 102, 103])).toEqual({
      ok: true,
      value: { parentId: null, position: 0, ids: [100, 103] },
    });
  });

  it("孫も外す", () => {
    const deep = [
      ...elements,
      makeElement({ id: 104, parent_id: 100, kind: "COMPONENT", name: "構成", wbs_code: "1.3", sort_order: 3, depth: 2 }),
      makeElement({ id: 105, parent_id: 104, kind: "WORK_PACKAGE", name: "部品", wbs_code: "1.3.1", depth: 3 }),
    ];
    expect(groupElementsPlan(deep, [100, 105])).toEqual({ ok: true, value: { parentId: null, position: 0, ids: [100] } });
  });

  it("選んでいなければ理由を返す", () => {
    expect(groupElementsPlan(elements, []).ok).toBe(false);
  });
});

describe("addPlan（CR-025：足す先と、名前の入力の行を差し込む位置）", () => {
  // e100（深さ 1）─ e101（WP：21・22・23）・e102（WP：24）、e103
  const outline = buildOutline(
    elements,
    [
      makeGanttRow({ row_index: 0, activity_id: 21, work_package_id: 101 }),
      makeGanttRow({ row_index: 1, activity_id: 22, work_package_id: 101 }),
      makeGanttRow({ row_index: 2, activity_id: 23, work_package_id: 101 }),
      makeGanttRow({ row_index: 3, activity_id: 24, work_package_id: 102 }),
    ],
    true,
  );

  it("表示の並びを確かめる", () => {
    expect(outline.map((r) => `${r.key}:${r.depth}`)).toEqual(["e100:1", "e101:2", "a21:3", "a22:3", "a23:3", "e102:2", "a24:3", "e103:1"]);
  });

  it("活動の行なら、同じ WP のその活動の次に活動を足す（WP の末尾なら位置は決めない）", () => {
    expect(addPlan(elements, activities, outline, "a21")).toEqual({
      ok: true,
      value: { kind: "activity", workPackage: elements[1], position: 1, index: 3, depth: 3 },
    });
    expect(addPlan(elements, activities, outline, "a23")).toEqual({
      ok: true,
      value: { kind: "activity", workPackage: elements[1], position: null, index: 5, depth: 3 },
    });
  });

  it("WP の行なら、その WP の末尾（配下の行の後）に活動を足す", () => {
    expect(addPlan(elements, activities, outline, "e101")).toEqual({
      ok: true,
      value: { kind: "activity", workPackage: elements[1], position: null, index: 5, depth: 3 },
    });
  });

  it("WP 以外の要素の行なら子の要素（WP）を配下の行の後に、何も選んでいなければ最上位の要素（WP）を末尾に足す", () => {
    expect(addPlan(elements, activities, outline, "e100")).toEqual({
      ok: true,
      value: { kind: "element", parent: elements[0], elementKind: "WORK_PACKAGE", position: null, index: 7, depth: 2 },
    });
    const top = { ok: true, value: { kind: "element", parent: null, elementKind: "WORK_PACKAGE", position: null, index: 8, depth: 1 } };
    expect(addPlan(elements, activities, outline, null)).toEqual(top);
    expect(addPlan(elements, activities, outline, "e999")).toEqual(top);
  });

  it("同じ階層：要素の行の配下の後に、同じ区分の要素を足して次の位置へ置く（兄弟の末尾なら位置は決めない）", () => {
    expect(addPlan(elements, activities, outline, "e101", true)).toEqual({
      ok: true,
      value: { kind: "element", parent: elements[0], elementKind: "WORK_PACKAGE", position: 1, index: 5, depth: 2 },
    });
    expect(addPlan(elements, activities, outline, "e102", true)).toEqual({
      ok: true,
      value: { kind: "element", parent: elements[0], elementKind: "WORK_PACKAGE", position: null, index: 7, depth: 2 },
    });
    expect(addPlan(elements, activities, outline, "e100", true)).toEqual({
      ok: true,
      value: { kind: "element", parent: null, elementKind: elements[0]!.kind, position: 1, index: 7, depth: 1 },
    });
  });

  it("活動の WP が木に無ければ理由を返す", () => {
    expect(addPlan(elements, [makeActivity({ id: 21, work_package_id: 999 })], outline, "a21").ok).toBe(false);
  });
});

describe("moveTargetPlan（CR-025：活動を移す先）", () => {
  it("WP の行ならその WP の末尾へ、活動の行ならその活動の次へ（位置は自分を除いた活動の中）", () => {
    expect(moveTargetPlan(elements, activities, 21, "e102")).toEqual({ ok: true, value: { workPackage: elements[2], position: 1 } });
    expect(moveTargetPlan(elements, activities, 21, "a24")).toEqual({ ok: true, value: { workPackage: elements[2], position: 1 } });
    expect(moveTargetPlan(elements, activities, 23, "a21")).toEqual({ ok: true, value: { workPackage: elements[1], position: 1 } });
  });

  it("WP 以外の要素・自分・木に無い行は理由を返す", () => {
    const refused = moveTargetPlan(elements, activities, 21, "e100");
    expect(refused).toEqual({ ok: false, reason: "1.0 要件定義 はワークパッケージではないので、活動を置けません（RULE-WBS-003）" });
    expect(moveTargetPlan(elements, activities, 21, "a21").ok).toBe(false);
    expect(moveTargetPlan(elements, activities, 21, "e999").ok).toBe(false);
    expect(moveTargetPlan(elements, activities, 99, "e102").ok).toBe(false);
  });
});

describe("stepSelection と rangeKeys", () => {
  const outline = buildOutline(
    elements,
    [makeGanttRow({ activity_id: 21, work_package_id: 101 }), makeGanttRow({ row_index: 1, activity_id: 24, work_package_id: 102 })],
    true,
  );

  it("上下の矢印で表示順の次の行を選び、端で止まる", () => {
    expect(outline.map((r) => r.key)).toEqual(["e100", "e101", "a21", "e102", "a24", "e103"]);
    expect(stepSelection(outline, "e101", 1)).toBe("a21");
    expect(stepSelection(outline, "e101", -1)).toBe("e100");
    expect(stepSelection(outline, "e100", -1)).toBe("e100");
    expect(stepSelection(outline, "e103", 1)).toBe("e103");
    expect(stepSelection(outline, null, 1)).toBe("e100");
    expect(stepSelection(outline, null, -1)).toBe("e103");
    expect(stepSelection([], null, 1)).toBeNull();
  });

  it("範囲は表示順で両端を含む", () => {
    expect(rangeKeys(outline, "a24", "e101")).toEqual(["e101", "a21", "e102", "a24"]);
    expect(rangeKeys(outline, "e999", "a21")).toEqual(["a21"]);
  });
});

describe("deletionPlan", () => {
  it("選んだ活動は先に 1 件ずつ消し、要素は配下ごと（選んだ要素の配下の要素は入れない）", () => {
    expect(deletionPlan(elements, activities, ["e100", "e101", "a21", "a22", "a23", "a24"])).toEqual({
      activityIds: [21, 22, 23, 24],
      elementIds: [100],
      blockedWorkPackages: [],
    });
    expect(deletionPlan(elements, activities, ["a23", "a21"])).toEqual({ activityIds: [21, 23], elementIds: [], blockedWorkPackages: [] });
  });

  it("選んでいない活動を配下に残す要素は、その WP を示す（API-WBS-005 は 422 で拒む。OQ-18 (1)）", () => {
    const plan = deletionPlan(elements, activities, ["e100", "a21", "a24"]);
    expect(plan.blockedWorkPackages.map((e) => e.id)).toEqual([101]);
    expect(deletionPlan(elements, activities, ["e101", "a22", "a24"]).blockedWorkPackages.map((e) => e.id)).toEqual([101]);
  });

  it("活動の無い要素は止めない", () => {
    expect(deletionPlan(elements, activities, ["e103"])).toEqual({ activityIds: [], elementIds: [103], blockedWorkPackages: [] });
  });

  it("何も選んでいなければ空", () => {
    expect(deletionPlan(elements, activities, [])).toEqual({ activityIds: [], elementIds: [], blockedWorkPackages: [] });
  });
});
