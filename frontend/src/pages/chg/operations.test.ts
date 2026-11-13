import { describe, expect, it } from "vitest";

import { nextActionStatuses, parseTargetCodes, shiftLabel } from "./labels";
import { buildOperation, describeOperation, emptyDraft, type OperationDraft } from "./operations";

function draft(overrides: Partial<OperationDraft>): OperationDraft {
  return { ...emptyDraft(overrides.op), ...overrides };
}

describe("変更内容の操作の組み立て（5.9）", () => {
  it("add_element：親の WBS ID（空はルート）・区分・名称・仮の名前", () => {
    expect(buildOperation(draft({ op: "add_element", parentCode: "1.2", kind: "WORK_PACKAGE", name: "追加", ref: "e1" })))
      .toEqual({
        ok: true,
        operation: { op: "add_element", parent_code: "1.2", kind: "WORK_PACKAGE", name: "追加", ref: "e1" },
      });
    const root = buildOperation(draft({ op: "add_element", kind: "DELIVERABLE", name: "成果物", ref: "r" }));
    expect(root).toEqual({
      ok: true,
      operation: { op: "add_element", parent_code: "", kind: "DELIVERABLE", name: "成果物", ref: "r" },
    });
    expect(buildOperation(draft({ op: "add_element", name: "x", ref: "r" }))).toEqual({
      ok: false,
      message: "区分を選んでください",
    });
    expect(buildOperation(draft({ op: "add_element", kind: "COMPONENT", name: "x" }))).toEqual({
      ok: false,
      message: "仮の名前を入力してください",
    });
  });

  it("update_element：入力した項目だけを fields に入れ、1 つも無ければ止める", () => {
    expect(
      buildOperation(draft({ op: "update_element", code: "1.2", ownEstimateHours: "16.5", responsibleUserId: "7" })),
    ).toEqual({
      ok: true,
      operation: { op: "update_element", code: "1.2", fields: { own_estimate_hours: 16.5, responsible_user_id: 7 } },
    });
    expect(buildOperation(draft({ op: "update_element", code: "1.2" }))).toMatchObject({ ok: false });
    expect(buildOperation(draft({ op: "update_element", code: "1.2", ownEstimateHours: "-1" }))).toEqual({
      ok: false,
      message: "自分の見積は 0 以上の数で入力してください",
    });
    expect(buildOperation(draft({ op: "update_element", code: "1.2", responsibleUserId: "0" }))).toEqual({
      ok: false,
      message: "責任者は 1 以上の整数で入力してください",
    });
  });

  it("add_activity：置き場は WBS ID か仮の名前のどちらか 1 つ。見積の空は未見積（null）", () => {
    expect(
      buildOperation(
        draft({ op: "add_activity", wpCode: "1.2", name: "追加の設計", durationDays: "3", estimateHours: "24", ref: "a1" }),
      ),
    ).toEqual({
      ok: true,
      operation: { op: "add_activity", wp_code: "1.2", name: "追加の設計", duration_days: 3, estimate_hours: 24, ref: "a1" },
    });
    expect(
      buildOperation(
        draft({ op: "add_activity", wpBy: "ref", wpRef: "e1", name: "x", durationDays: "0", ref: "a2", requestedStart: "2026-12-01" }),
      ),
    ).toEqual({
      ok: true,
      operation: {
        op: "add_activity",
        wp_ref: "e1",
        name: "x",
        duration_days: 0,
        estimate_hours: null,
        ref: "a2",
        requested_start: "2026-12-01",
      },
    });
    expect(buildOperation(draft({ op: "add_activity", wpCode: "1.2", name: "x", durationDays: "1.5", ref: "a" }))).toEqual({
      ok: false,
      message: "期間（稼働日）は 0 以上の整数で入力してください",
    });
  });

  it("update_activity・remove_activity：活動の ID は 1 以上の整数", () => {
    expect(buildOperation(draft({ op: "update_activity", activityId: "21", durationDays: "8" }))).toEqual({
      ok: true,
      operation: { op: "update_activity", activity_id: 21, fields: { duration_days: 8 } },
    });
    expect(buildOperation(draft({ op: "remove_activity", activityId: "21" }))).toEqual({
      ok: true,
      operation: { op: "remove_activity", activity_id: 21 },
    });
    expect(buildOperation(draft({ op: "remove_activity", activityId: "abc" }))).toEqual({
      ok: false,
      message: "活動の ID は 1 以上の整数で入力してください",
    });
  });

  it("依存の端は指し方で型を決める：今ある活動は整数の ID、仮の名前は文字列（OQ-38 (2)・A-49 の 1）", () => {
    expect(
      buildOperation(
        draft({ op: "add_dependency", predecessorId: "21", successorBy: "ref", successorRef: " a1 " }),
      ),
    ).toEqual({
      ok: true,
      operation: { op: "add_dependency", predecessor: 21, successor: "a1" },
    });
    // 既定の指し方は今ある活動（一覧から選ぶ）
    expect(emptyDraft("add_dependency")).toMatchObject({ predecessorBy: "id", successorBy: "id" });
    expect(buildOperation(draft({ op: "remove_dependency", predecessorId: "21" }))).toEqual({
      ok: false,
      message: "後続の活動を選んでください",
    });
    expect(buildOperation(draft({ op: "add_dependency", predecessorBy: "ref", successorId: "22" }))).toEqual({
      ok: false,
      message: "先行の仮の名前を入力してください",
    });
  });

  it("数字だけの仮の名前も文字列の ref として送る（活動の ID とみなさない）", () => {
    expect(
      buildOperation(
        draft({
          op: "add_dependency",
          predecessorBy: "ref",
          predecessorRef: "21",
          successorBy: "ref",
          successorRef: "007",
        }),
      ),
    ).toEqual({
      ok: true,
      operation: { op: "add_dependency", predecessor: "21", successor: "007" },
    });
    expect(
      buildOperation(draft({ op: "remove_dependency", predecessorId: "21", successorBy: "ref", successorRef: "22" })),
    ).toEqual({
      ok: true,
      operation: { op: "remove_dependency", predecessor: 21, successor: "22" },
    });
  });

  it("操作を 1 行で説明する", () => {
    expect(describeOperation({ op: "add_element", parent_code: "", kind: "WORK_PACKAGE", name: "追加", ref: "e1" })).toBe(
      "要素を足す：ルート の下に「追加」（ワークパッケージ、仮の名前 e1）",
    );
    expect(describeOperation({ op: "update_element", code: "1.2", fields: { kind: "COMPONENT", name: "新" } })).toBe(
      "要素を変える：1.2（区分＝構成要素、名称＝新）",
    );
    expect(
      describeOperation({ op: "add_activity", wp_ref: "e1", name: "x", duration_days: 2, estimate_hours: null, ref: "a" }),
    ).toBe("活動を足す：仮の名前 e1 の WP に「x」（2 稼働日、未見積、仮の名前 a）");
    expect(describeOperation({ op: "add_dependency", predecessor: 21, successor: "a1" })).toBe(
      "依存を足す：活動 #21 → 「a1」",
    );
    expect(describeOperation({ op: "unknown", x: 1 })).toBe('{"op":"unknown","x":1}');
  });
});

describe("変更管理の画面の小さな計算", () => {
  it("是正措置の状態の遷移（RULE-CHG-005）：今の状態のままと、次に進める・戻せる状態だけを選べる", () => {
    expect(nextActionStatuses("NOT_STARTED")).toEqual(["NOT_STARTED", "IN_PROGRESS"]);
    expect(nextActionStatuses("IN_PROGRESS")).toEqual(["IN_PROGRESS", "DONE"]);
    expect(nextActionStatuses("DONE")).toEqual(["DONE", "IN_PROGRESS"]);
  });

  it("対象の WBS ID はカンマ・読点・空白で区切り、空と重複を除く", () => {
    expect(parseTargetCodes("1.2, 1.3、1.2\n2.0  ")).toEqual(["1.2", "1.3", "2.0"]);
    expect(parseTargetCodes("  ")).toEqual([]);
  });

  it("ずれの表示は正に + を付ける", () => {
    expect(shiftLabel(3)).toBe("+3 稼働日");
    expect(shiftLabel(-2)).toBe("-2 稼働日");
    expect(shiftLabel(0)).toBe("0 稼働日");
    expect(shiftLabel(null)).toBe("—");
  });
});
