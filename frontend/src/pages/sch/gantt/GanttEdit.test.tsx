// ガント（UI-SCH-002）の WBS の要素の行と編集（設計書 7.3。CR-024）
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setAuthToken } from "../../../api/client";
import type { Me } from "../../../api/cmn";
import type { Dependency, Gantt } from "../../../api/sch";
import { HEADER_HEIGHT, ROW_HEIGHT } from "../../../components/gantt/scale";
import { makeMe, makeUser } from "../../../test/fixtures";
import { errorResponse, jsonResponse, mockApi, type RecordedCall, type RouteHandler } from "../../../test/mockApi";
import { activityList, makeActivity, makeBar, makeGantt, makeGanttRow } from "../../../test/schFixtures";
import { makeElement, makeTree, sampleElements } from "../../../test/wbsFixtures";
import GanttPage from "../GanttPage";

const BASE = "/api/v1/projects/3";
const PLANNER = ["project.view", "schedule.edit", "wbs.edit"];

function meWith(permissions: string[]): Me {
  return makeMe({
    user: makeUser({ id: 5, is_system_admin: false }),
    system_permissions: [],
    projects: [{ project_id: 3, roles: ["PM"], permissions, view_level: "ALL" }],
  });
}

// 1.0 要件定義 ─ 1.1 業務整理（WP 101：21・22）・1.2 画面設計（WP 102：23）、2.0 管理（活動なし）
const GANTT: Gantt = makeGantt({
  range_start: "2026-12-01",
  range_end: "2026-12-10",
  rows: [
    makeGanttRow({ row_index: 0, activity_id: 21, name: "聞き取り", work_package_id: 101, planned: makeBar("PLANNED", "2026-12-01", "2026-12-04") }),
    makeGanttRow({ row_index: 1, activity_id: 22, name: "整理", work_package_id: 101, planned: makeBar("PLANNED", "2026-12-07", "2026-12-09") }),
    makeGanttRow({
      row_index: 2,
      activity_id: 23,
      name: "画面案",
      work_package_id: 102,
      wbs_code: "1.2",
      planned: makeBar("PLANNED", "2026-12-03", "2026-12-10"),
    }),
  ],
  links: [{ predecessor_id: 21, successor_id: 22, from_row: 0, to_row: 1, start: null, end: null }],
});

const ACTIVITIES = [
  makeActivity({ id: 21, name: "聞き取り", work_package_id: 101, sort_order: 1, successor_ids: [22] }),
  makeActivity({ id: 22, name: "整理", work_package_id: 101, sort_order: 2, requested_start: "2026-12-07", predecessor_ids: [21] }),
  makeActivity({ id: 23, name: "画面案", work_package_id: 102, wbs_code: "1.2", sort_order: 1 }),
];
const DEPENDENCIES: Dependency[] = [{ id: 91, predecessor_id: 21, successor_id: 22, dep_type: "FS", lag_days: 0 }];

function routes(permissions: string[], overrides: Record<string, RouteHandler> = {}): Record<string, RouteHandler> {
  return {
    "GET /api/v1/me": () => jsonResponse(200, meWith(permissions)),
    [`GET ${BASE}/gantt`]: () => jsonResponse(200, GANTT),
    [`GET ${BASE}/schedule-baselines`]: () => jsonResponse(200, { items: [], total: 0, limit: 1000, offset: 0 }),
    [`GET ${BASE}/status-reports`]: () => jsonResponse(200, { items: [], total: 0, limit: 1000, offset: 0 }),
    [`GET ${BASE}/wbs`]: () => jsonResponse(200, makeTree()),
    [`GET ${BASE}/activities`]: () => jsonResponse(200, activityList(ACTIVITIES, { dependencies: DEPENDENCIES })),
    ...overrides,
  };
}

function renderAt() {
  return render(
    <MemoryRouter initialEntries={["/projects/3/gantt"]}>
      <Routes>
        <Route path="/projects/:projectId/gantt" element={<GanttPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

function sent(calls: RecordedCall[], method: string, path: string) {
  return calls.filter((c) => c.method === method && c.path === path);
}

function rowKeys(container: HTMLElement): string[] {
  return [...container.querySelectorAll('[data-layer="row-labels"] > g')].map((g) => g.getAttribute("data-row-key") ?? "");
}

function label(container: HTMLElement, key: string): Element {
  const g = container.querySelector(`[data-layer="row-labels"] > g[data-row-key="${key}"]`);
  if (g === null) {
    throw new Error(`行 ${key} が無い`);
  }
  return g;
}

/** 行の中心の y（描画域の上端が 0。jsdom の getBoundingClientRect は 0 を返す） */
function rowY(container: HTMLElement, key: string): number {
  return HEADER_HEIGHT + rowKeys(container).indexOf(key) * ROW_HEIGHT + ROW_HEIGHT / 2;
}

/** 活動の予定のバーの中ほどの x（本体の掴みから） */
function barX(container: HTMLElement, activityId: number): number {
  const grip = container.querySelector(`[data-handle="move"][data-activity-id="${activityId}"]`);
  if (grip === null) {
    throw new Error(`活動 ${activityId} のバーが無い`);
  }
  return Number(grip.getAttribute("x")) + Number(grip.getAttribute("width")) / 2;
}

/** 保存の後の読み直しが終わるまで待つ（終わるまで次の操作は受けない） */
async function settled() {
  await waitFor(() => expect(screen.getByRole("button", { name: "名前を変える" })).toBeEnabled());
}

const WAIT_TEXT = "前の操作の保存・読み直しが終わっていません。終わってからもう一度操作します";

async function startEditing(permissions = PLANNER, overrides: Record<string, RouteHandler> = {}) {
  const api = mockApi(routes(permissions, overrides));
  const view = renderAt();
  await screen.findByRole("img", { name: "ガントチャート" });
  fireEvent.click(await screen.findByRole("button", { name: "編集する" }));
  await screen.findByRole("toolbar", { name: "編集の操作" });
  const viewport = screen.getByTestId("gantt-viewport");
  return { ...api, ...view, viewport };
}

describe("UI-SCH-002 の WBS の要素の行と編集（CR-024）", () => {
  beforeEach(() => {
    setAuthToken("t-pm");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    setAuthToken(null);
  });

  it("要素の行を配下の行の前に差し込み、要約のバーを③に描く。編集していなければ活動の無い要素は出さない", async () => {
    mockApi(routes(["project.view"]));
    const { container } = renderAt();
    await waitFor(() => expect(rowKeys(container)).toEqual(["e100", "e101", "a21", "a22", "e102", "a23"]));

    const summaries = container.querySelectorAll('[data-layer="planned"] [data-kind="SUMMARY"]');
    expect([...summaries].map((s) => s.getAttribute("data-element-id"))).toEqual(["100", "101", "102"]);
    expect(label(container, "e101").querySelector("text")?.getAttribute("font-weight")).toBe("600");
    // 要約のバーには基準・実績・ラベルを描かない
    expect(container.querySelectorAll('[data-layer="baseline"] [data-element-id]')).toHaveLength(0);
    // 閲覧だけの人には編集の切り替えを出さない
    expect(screen.queryByRole("button", { name: "編集する" })).toBeNull();
  });

  it("WBS を読めなければ要素の行を出さずに今までどおり描く", async () => {
    mockApi(routes(["project.view"], { [`GET ${BASE}/wbs`]: () => errorResponse(403, "FORBIDDEN", "権限がありません") }));
    const { container } = renderAt();
    await waitFor(() => expect(rowKeys(container)).toEqual(["a21", "a22", "a23"]));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("編集に切り替えると活動の無い要素も出し、WBS の状態を帯に出す", async () => {
    const { container, calls } = await startEditing();
    expect(rowKeys(container)).toEqual(["e100", "e101", "a21", "a22", "e102", "a23", "e103"]);
    expect(sent(calls, "GET", `${BASE}/activities`)).toHaveLength(1);
    expect(within(screen.getByRole("toolbar", { name: "編集の操作" })).getByText("作成中")).toBeInTheDocument();
    // 希望開始日のある活動に印を付ける
    expect(container.querySelector('[data-requested-start="22"]')).not.toBeNull();
    expect(container.querySelector('[data-requested-start="21"]')).toBeNull();
  });

  it("行を選び、F2 で名前を変える（活動は API-SCH-003、要素は API-WBS-003）。Esc で取り消す", async () => {
    const { container, calls, viewport } = await startEditing(PLANNER, {
      [`PATCH ${BASE}/activities/21`]: () => jsonResponse(200, ACTIVITIES[0]),
      [`PATCH ${BASE}/wbs/elements/101`]: () => jsonResponse(200, sampleElements()[1]),
    });
    fireEvent.mouseDown(label(container, "a21"));
    fireEvent.keyDown(viewport, { key: "F2" });
    const input = screen.getByRole("textbox", { name: "名前" });
    fireEvent.change(input, { target: { value: "現行の聞き取り" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(sent(calls, "PATCH", `${BASE}/activities/21`)).toHaveLength(1));
    expect(sent(calls, "PATCH", `${BASE}/activities/21`)[0]?.body).toEqual({ name: "現行の聞き取り" });

    fireEvent.doubleClick(label(container, "e101"));
    const again = await screen.findByRole("textbox", { name: "名前" });
    fireEvent.change(again, { target: { value: "捨てる名前" } });
    fireEvent.keyDown(again, { key: "Escape" });
    expect(screen.queryByRole("textbox", { name: "名前" })).toBeNull();
    expect(sent(calls, "PATCH", `${BASE}/wbs/elements/101`)).toHaveLength(0);
  });

  it("上下の矢印で行を選び、Insert で足す欄へ。選んだ行の WP の末尾に活動を足す（期間の既定は 1 稼働日）", async () => {
    const { container, calls, viewport } = await startEditing(PLANNER, {
      [`POST ${BASE}/activities`]: () => jsonResponse(201, makeActivity({ id: 30 })),
    });
    fireEvent.mouseDown(label(container, "e101"));
    fireEvent.keyDown(viewport, { key: "ArrowDown" });
    expect(label(container, "a21").querySelector("rect")?.getAttribute("style")).toContain("--bgColor-accent-muted");
    fireEvent.keyDown(viewport, { key: "Insert" });
    const input = screen.getByRole("textbox", { name: "足す行の名前" });
    expect(input).toHaveFocus();
    expect(screen.getByRole("form", { name: "行を足す" })).toHaveTextContent("1.1 業務整理 に活動を足す");

    fireEvent.change(input, { target: { value: "議事録" } });
    fireEvent.submit(screen.getByRole("form", { name: "行を足す" }));
    await waitFor(() => expect(sent(calls, "POST", `${BASE}/activities`)).toHaveLength(1));
    expect(sent(calls, "POST", `${BASE}/activities`)[0]?.body).toEqual({ work_package_id: 101, name: "議事録", duration_days: 1 });
    // 続けて足せるように入力を空にして戻る。保存の後に読み直す
    await waitFor(() => expect(input).toHaveValue(""));
    expect(input).toHaveFocus();
    await waitFor(() => expect(sent(calls, "GET", `${BASE}/gantt`).length).toBeGreaterThanOrEqual(2));
  });

  it("WP 以外の要素の行なら子の要素を足す（区分を選ぶ。既定は WP）", async () => {
    const { container, calls } = await startEditing(PLANNER, {
      [`POST ${BASE}/wbs/elements`]: () => jsonResponse(201, makeElement({ id: 110 })),
    });
    fireEvent.mouseDown(label(container, "e100"));
    expect(screen.getByRole("form", { name: "行を足す" })).toHaveTextContent("1.0 要件定義 の下に要素を足す");
    expect(screen.getByRole("combobox", { name: "足す要素の区分" })).toHaveValue("WORK_PACKAGE");
    fireEvent.change(screen.getByRole("textbox", { name: "足す行の名前" }), { target: { value: "移行設計" } });
    fireEvent.submit(screen.getByRole("form", { name: "行を足す" }));
    await waitFor(() => expect(sent(calls, "POST", `${BASE}/wbs/elements`)).toHaveLength(1));
    expect(sent(calls, "POST", `${BASE}/wbs/elements`)[0]?.body).toEqual({ parent_id: 100, kind: "WORK_PACKAGE", name: "移行設計" });
  });

  it("Alt+Shift+→ で要素をインデントし（API-WBS-004）、直前が WP なら理由を示して呼ばない", async () => {
    const { container, calls, viewport } = await startEditing(PLANNER, {
      [`POST ${BASE}/wbs/elements/103/move`]: () => jsonResponse(200, sampleElements()[3]),
    });
    fireEvent.mouseDown(label(container, "e103"));
    fireEvent.keyDown(viewport, { key: "ArrowRight", altKey: true, shiftKey: true });
    await waitFor(() => expect(sent(calls, "POST", `${BASE}/wbs/elements/103/move`)).toHaveLength(1));
    expect(sent(calls, "POST", `${BASE}/wbs/elements/103/move`)[0]?.body).toEqual({ parent_id: 100 });

    fireEvent.mouseDown(label(container, "e102"));
    fireEvent.click(screen.getByRole("button", { name: "インデント" }));
    expect(await screen.findByText(/1\.1 業務整理）はワークパッケージなので/)).toBeInTheDocument();
  });

  it("Alt+Shift+↓ で活動を同じ WP の中で下へ（API-SCH-003 の position）", async () => {
    const { container, calls, viewport } = await startEditing(PLANNER, {
      [`PATCH ${BASE}/activities/21`]: () => jsonResponse(200, ACTIVITIES[0]),
    });
    fireEvent.mouseDown(label(container, "a21"));
    fireEvent.keyDown(viewport, { key: "ArrowDown", altKey: true, shiftKey: true });
    await waitFor(() => expect(sent(calls, "PATCH", `${BASE}/activities/21`)).toHaveLength(1));
    expect(sent(calls, "PATCH", `${BASE}/activities/21`)[0]?.body).toEqual({ position: 1 });
  });

  it("バーの本体のドラッグは希望開始日に、計算した開始が違えばその旨を示す", async () => {
    const { container, calls } = await startEditing(PLANNER, {
      [`PATCH ${BASE}/activities/22`]: () =>
        jsonResponse(200, makeActivity({ id: 22, requested_start: "2026-12-09", start_date: "2026-12-10", finish_date: "2026-12-14" })),
    });
    const grip = container.querySelector('[data-handle="move"][data-activity-id="22"]');
    expect(grip).not.toBeNull();
    fireEvent.mouseDown(grip!, { clientX: 400, clientY: rowY(container, "a22") });
    fireEvent.mouseMove(window, { clientX: 448, clientY: rowY(container, "a22") });
    expect(container.querySelector('[data-ghost="move"]')).toHaveTextContent("2026-12-09");
    fireEvent.mouseUp(window, { clientX: 448, clientY: rowY(container, "a22") });
    await waitFor(() => expect(sent(calls, "PATCH", `${BASE}/activities/22`)).toHaveLength(1));
    expect(sent(calls, "PATCH", `${BASE}/activities/22`)[0]?.body).toEqual({ requested_start: "2026-12-09" });
    expect(await screen.findByText(/希望開始日を 2026-12-09 にした。計算した開始は 2026-12-10/)).toBeInTheDocument();
  });

  it("バーの右端のドラッグは planned_finish に。Esc で取り消すと呼ばない", async () => {
    const { container, calls } = await startEditing(PLANNER, {
      [`PATCH ${BASE}/activities/21`]: () => jsonResponse(200, makeActivity({ id: 21, duration_days: 5, finish_date: "2026-12-05" })),
    });
    const grip = container.querySelector('[data-handle="resize"][data-activity-id="21"]');
    fireEvent.mouseDown(grip!, { clientX: 400 });
    fireEvent.mouseMove(window, { clientX: 450 });
    fireEvent.keyDown(window, { key: "Escape" });
    fireEvent.mouseUp(window, { clientX: 450 });
    expect(container.querySelector("[data-ghost]")).toBeNull();
    expect(sent(calls, "PATCH", `${BASE}/activities/21`)).toHaveLength(0);

    fireEvent.mouseDown(container.querySelector('[data-handle="resize"][data-activity-id="21"]')!, { clientX: 400 });
    fireEvent.mouseMove(window, { clientX: 424 });
    fireEvent.mouseUp(window, { clientX: 424 });
    await waitFor(() => expect(sent(calls, "PATCH", `${BASE}/activities/21`)).toHaveLength(1));
    expect(sent(calls, "PATCH", `${BASE}/activities/21`)[0]?.body).toEqual({ planned_finish: "2026-12-05" });
    expect(await screen.findByText("終わりを 2026-12-05 にした（期間 5 稼働日）")).toBeInTheDocument();
  });

  it("動かさずに離せば選ぶだけ", async () => {
    const { container, calls } = await startEditing();
    fireEvent.mouseDown(container.querySelector('[data-handle="move"][data-activity-id="23"]')!, { clientX: 400 });
    fireEvent.mouseUp(window, { clientX: 401 });
    expect(container.querySelector('[data-layer="edit-rows"] [data-row-key="a23"]')?.getAttribute("data-selected")).toBe("true");
    expect(calls.filter((c) => c.method !== "GET")).toHaveLength(0);
  });

  it("選んだ活動のバーの右の丸を別の活動のバーへドラッグすると依存を足す。循環は理由を示す", async () => {
    const { container, calls } = await startEditing(PLANNER, {
      [`POST ${BASE}/dependencies`]: () => errorResponse(422, "SCHEDULE_CYCLE", "依存が循環します"),
    });
    fireEvent.mouseDown(label(container, "a22"));
    const handle = container.querySelector('[data-handle="link"][data-activity-id="22"]');
    expect(handle).not.toBeNull();
    fireEvent.mouseDown(handle!, { clientX: 500, clientY: rowY(container, "a22") });
    fireEvent.mouseMove(window, { clientX: barX(container, 21), clientY: rowY(container, "a21") });
    expect(container.querySelector('[data-ghost="link"]')).not.toBeNull();
    fireEvent.mouseUp(window, { clientX: barX(container, 21), clientY: rowY(container, "a21") });
    await waitFor(() => expect(sent(calls, "POST", `${BASE}/dependencies`)).toHaveLength(1));
    expect(sent(calls, "POST", `${BASE}/dependencies`)[0]?.body).toEqual({ predecessor_id: 22, successor_id: 21 });
    expect(await screen.findByText("依存が循環します")).toBeInTheDocument();
  });

  it("バーの外（要素の行・行の空いた所・目盛りの帯）へ落としても依存は足さない", async () => {
    const { container, calls } = await startEditing();
    fireEvent.mouseDown(label(container, "a21"));
    const drop = (clientX: number, clientY: number) => {
      fireEvent.mouseDown(container.querySelector('[data-handle="link"]')!, { clientX: 500, clientY: rowY(container, "a21") });
      fireEvent.mouseUp(window, { clientX, clientY });
    };
    drop(barX(container, 23), rowY(container, "e102"));
    drop(barX(container, 22) + 400, rowY(container, "a22"));
    drop(barX(container, 22), HEADER_HEIGHT / 2);
    expect(sent(calls, "POST", `${BASE}/dependencies`)).toHaveLength(0);
  });

  it("矢印を選んで Delete で依存を外す（API-SCH-007）", async () => {
    const gantt = {
      ...GANTT,
      links: [{ ...GANTT.links[0]!, start: makeBar("PLANNED", "2026-12-04", "2026-12-04").end, end: makeBar("PLANNED", "2026-12-07", "2026-12-07").start }],
    };
    const { container, calls, viewport } = await startEditing(PLANNER, {
      [`GET ${BASE}/gantt`]: () => jsonResponse(200, gantt),
      [`DELETE ${BASE}/dependencies/91`]: () => jsonResponse(204, null),
    });
    const hit = await waitFor(() => {
      const element = container.querySelector('[data-link-hit="21-22"]');
      expect(element).not.toBeNull();
      return element!;
    });
    fireEvent.mouseDown(hit);
    expect(container.querySelector('[data-link="21-22"]')?.getAttribute("data-selected")).toBe("true");
    fireEvent.keyDown(viewport, { key: "Delete" });
    await waitFor(() => expect(sent(calls, "DELETE", `${BASE}/dependencies/91`)).toHaveLength(1));
  });

  it("選んだ活動を新しい WP にまとめる：最初の活動の WP の次に作り、表示順に 1 件ずつ移す", async () => {
    const { container, calls } = await startEditing(PLANNER, {
      [`POST ${BASE}/wbs/elements`]: () => jsonResponse(201, makeElement({ id: 120, parent_id: 100, kind: "WORK_PACKAGE" })),
      [`POST ${BASE}/wbs/elements/120/move`]: () => jsonResponse(200, makeElement({ id: 120 })),
      [`PATCH ${BASE}/activities/22`]: () => jsonResponse(200, ACTIVITIES[1]),
      [`PATCH ${BASE}/activities/23`]: () => jsonResponse(200, ACTIVITIES[2]),
    });
    fireEvent.mouseDown(label(container, "a23"));
    fireEvent.mouseDown(label(container, "a22"), { ctrlKey: true });
    const form = screen.getByRole("form", { name: "まとめる" });
    expect(form).toHaveTextContent("選んだ活動 2 件を新しい WP にまとめる");
    fireEvent.change(within(form).getByRole("textbox", { name: "まとめる先の名前" }), { target: { value: "調整" } });
    fireEvent.submit(form);
    await screen.findByText("活動 2 件を新しい WP「調整」にまとめた");
    const writes = calls.filter((c) => c.method !== "GET");
    expect(writes.map((c) => `${c.method} ${c.path}`)).toEqual([
      `POST ${BASE}/wbs/elements`,
      `POST ${BASE}/wbs/elements/120/move`,
      `PATCH ${BASE}/activities/22`,
      `PATCH ${BASE}/activities/23`,
    ]);
    expect(writes[0]?.body).toEqual({ parent_id: 100, kind: "WORK_PACKAGE", name: "調整" });
    expect(writes[1]?.body).toEqual({ parent_id: 100, position: 1 });
    expect(writes[2]?.body).toEqual({ work_package_id: 120 });
  });

  it("まとめる途中で失敗したら、そこまでの移動を残して移せなかった行を示す", async () => {
    const { container } = await startEditing(PLANNER, {
      [`POST ${BASE}/wbs/elements`]: () => jsonResponse(201, makeElement({ id: 120, parent_id: 100, kind: "WORK_PACKAGE" })),
      [`POST ${BASE}/wbs/elements/120/move`]: () => jsonResponse(200, makeElement({ id: 120 })),
      [`PATCH ${BASE}/activities/21`]: () => jsonResponse(200, ACTIVITIES[0]),
      [`PATCH ${BASE}/activities/22`]: () => errorResponse(409, "CONFLICT", "他の人が変えました"),
    });
    fireEvent.mouseDown(label(container, "a21"));
    fireEvent.mouseDown(label(container, "a22"), { shiftKey: true });
    const form = screen.getByRole("form", { name: "まとめる" });
    fireEvent.change(within(form).getByRole("textbox", { name: "まとめる先の名前" }), { target: { value: "調整" } });
    fireEvent.submit(form);
    expect(
      await screen.findByText("他の人が変えました（新しい WP「調整」は作った。1 件は移した。移せなかった活動：整理）"),
    ).toBeInTheDocument();
  });

  it("選んだ要素を新しい上位の要素にまとめる（区分は WP 以外。既定は構成要素）", async () => {
    const { container, calls } = await startEditing(PLANNER, {
      [`POST ${BASE}/wbs/elements`]: () => jsonResponse(201, makeElement({ id: 130, parent_id: 100, kind: "COMPONENT" })),
      [`POST ${BASE}/wbs/elements/130/move`]: () => jsonResponse(200, makeElement({ id: 130 })),
      [`POST ${BASE}/wbs/elements/101/move`]: () => jsonResponse(200, sampleElements()[1]),
      [`POST ${BASE}/wbs/elements/102/move`]: () => jsonResponse(200, sampleElements()[2]),
    });
    fireEvent.mouseDown(label(container, "e101"));
    fireEvent.mouseDown(label(container, "e102"), { ctrlKey: true });
    const form = screen.getByRole("form", { name: "まとめる" });
    expect(within(form).getByRole("combobox", { name: "まとめる先の区分" })).toHaveValue("COMPONENT");
    expect(within(form).queryByRole("option", { name: "ワークパッケージ" })).toBeNull();
    fireEvent.change(within(form).getByRole("textbox", { name: "まとめる先の名前" }), { target: { value: "設計" } });
    fireEvent.submit(form);
    await screen.findByText("要素 2 件を新しい要素「設計」にまとめた");
    const writes = calls.filter((c) => c.method !== "GET");
    expect(writes.map((c) => c.body)).toEqual([
      { parent_id: 100, kind: "COMPONENT", name: "設計" },
      { parent_id: 100, position: 0 },
      { parent_id: 130 },
      { parent_id: 130 },
    ]);
  });

  it("要素 1 件でも新しい上位の要素にまとめられる（その要素の位置に作る）", async () => {
    const { container, calls } = await startEditing(PLANNER, {
      [`POST ${BASE}/wbs/elements`]: () => jsonResponse(201, makeElement({ id: 130, parent_id: 100, kind: "COMPONENT" })),
      [`POST ${BASE}/wbs/elements/130/move`]: () => jsonResponse(200, makeElement({ id: 130 })),
      [`POST ${BASE}/wbs/elements/102/move`]: () => jsonResponse(200, sampleElements()[2]),
    });
    fireEvent.mouseDown(label(container, "e102"));
    const form = screen.getByRole("form", { name: "まとめる" });
    expect(form).toHaveTextContent("選んだ要素 1 件を新しい上位の要素にまとめる");
    fireEvent.change(within(form).getByRole("textbox", { name: "まとめる先の名前" }), { target: { value: "設計" } });
    fireEvent.submit(form);
    await screen.findByText("要素 1 件を新しい要素「設計」にまとめた");
    expect(calls.filter((c) => c.method !== "GET").map((c) => c.body)).toEqual([
      { parent_id: 100, kind: "COMPONENT", name: "設計" },
      { parent_id: 100, position: 1 },
      { parent_id: 130 },
    ]);
  });

  it("活動と要素を混ぜて選んだらまとめる欄を出さない", async () => {
    const { container } = await startEditing();
    fireEvent.mouseDown(label(container, "e102"));
    fireEvent.mouseDown(label(container, "a21"), { ctrlKey: true });
    expect(screen.queryByRole("form", { name: "まとめる" })).toBeNull();
  });

  it("Delete で確かめてから消す：選んだ活動を先に 1 件ずつ（API-SCH-005）、次に要素を配下ごと（API-WBS-005）", async () => {
    const { container, calls, viewport } = await startEditing(PLANNER, {
      [`DELETE ${BASE}/activities/21`]: () => jsonResponse(204, null),
      [`DELETE ${BASE}/activities/22`]: () => jsonResponse(204, null),
      [`DELETE ${BASE}/activities/23`]: () => jsonResponse(204, null),
      [`DELETE ${BASE}/wbs/elements/101`]: () => jsonResponse(204, null),
    });
    fireEvent.mouseDown(label(container, "e101"));
    fireEvent.mouseDown(label(container, "a22"), { shiftKey: true });
    fireEvent.mouseDown(label(container, "a23"), { ctrlKey: true });
    fireEvent.keyDown(viewport, { key: "Delete" });
    const confirm = screen.getByRole("group", { name: "行を消すことの確かめ" });
    expect(confirm).toHaveTextContent(
      "要素 1 件（1.1 業務整理。配下の要素ごと）と活動 3 件（1.1 聞き取り、1.1 整理、1.2 画面案）を消す。消した行は戻せない",
    );
    fireEvent.click(within(confirm).getByRole("button", { name: "やめる" }));
    expect(calls.filter((c) => c.method === "DELETE")).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "消す" }));
    fireEvent.click(within(screen.getByRole("group", { name: "行を消すことの確かめ" })).getByRole("button", { name: "消す" }));
    await waitFor(() =>
      expect(calls.filter((c) => c.method === "DELETE").map((c) => c.path)).toEqual([
        `${BASE}/activities/21`,
        `${BASE}/activities/22`,
        `${BASE}/activities/23`,
        `${BASE}/wbs/elements/101`,
      ]),
    );
  });

  it("選んでいない活動が残る WP を含む要素は、理由を示して消さない（API-WBS-005 は 422。OQ-18 (1)）", async () => {
    const { container, calls, viewport } = await startEditing();
    fireEvent.mouseDown(label(container, "e100"));
    fireEvent.keyDown(viewport, { key: "Delete" });
    expect(screen.queryByRole("group", { name: "行を消すことの確かめ" })).toBeNull();
    expect(
      screen.getByText(
        "活動のある WP は消せません：1.1 業務整理、1.2 画面設計（先に活動を消すか、別の WP へ移す。活動も一緒に選べば、活動を先に消す）",
      ),
    ).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "DELETE")).toHaveLength(0);
  });

  it("確かめの後に矢印で選び直すと確かめを取り消す（確かめていない行を消さない）", async () => {
    const { container, calls, viewport } = await startEditing();
    fireEvent.mouseDown(label(container, "a21"));
    fireEvent.keyDown(viewport, { key: "Delete" });
    expect(screen.getByRole("group", { name: "行を消すことの確かめ" })).toHaveTextContent("活動 1 件（1.1 聞き取り）を消す");
    fireEvent.keyDown(viewport, { key: "ArrowDown" });
    expect(screen.queryByRole("group", { name: "行を消すことの確かめ" })).toBeNull();
    expect(calls.filter((c) => c.method === "DELETE")).toHaveLength(0);
  });

  it("保存の後、読み直しが終わるまで次の操作を受けない（古い木・一覧で位置を数えない）", async () => {
    let wbsCalls = 0;
    let release: () => void = () => undefined;
    const { container, calls, viewport } = await startEditing(PLANNER, {
      [`GET ${BASE}/wbs`]: () => {
        wbsCalls += 1;
        if (wbsCalls === 1) {
          return jsonResponse(200, makeTree());
        }
        return new Promise<Response>((resolve) => {
          release = () => resolve(jsonResponse(200, makeTree()));
        });
      },
      [`PATCH ${BASE}/activities/21`]: () => jsonResponse(200, ACTIVITIES[0]),
    });
    const down = () => fireEvent.keyDown(viewport, { key: "ArrowDown", altKey: true, shiftKey: true });
    fireEvent.mouseDown(label(container, "a21"));
    down();
    await waitFor(() => expect(sent(calls, "PATCH", `${BASE}/activities/21`)).toHaveLength(1));
    expect(await screen.findByText("読み直しています…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "下へ" })).toBeDisabled();
    down();
    expect(screen.getByText(WAIT_TEXT)).toBeInTheDocument();
    expect(sent(calls, "PATCH", `${BASE}/activities/21`)).toHaveLength(1);

    release();
    await settled();
    down();
    await waitFor(() => expect(sent(calls, "PATCH", `${BASE}/activities/21`)).toHaveLength(2));
  });

  it("活動の一覧を読めなければ編集できないことを示す", async () => {
    mockApi(routes(PLANNER, { [`GET ${BASE}/activities`]: () => errorResponse(500, "INTERNAL", "失敗") }));
    renderAt();
    await screen.findByRole("img", { name: "ガントチャート" });
    fireEvent.click(await screen.findByRole("button", { name: "編集する" }));
    expect(await screen.findByText(/^活動の一覧を読めないので編集できません（.+）。編集を終えてから入り直してください$/)).toBeInTheDocument();
    expect(screen.queryByRole("toolbar", { name: "編集の操作" })).toBeNull();
  });

  it("選択を外すと最上位に足す（Ctrl＋クリックで外す・Esc で外す）", async () => {
    const { container, viewport } = await startEditing();
    const form = () => screen.getByRole("form", { name: "行を足す" });
    fireEvent.mouseDown(label(container, "e100"));
    expect(form()).toHaveTextContent("1.0 要件定義 の下に要素を足す");
    fireEvent.mouseDown(label(container, "e100"), { ctrlKey: true });
    expect(form()).toHaveTextContent("最上位に要素を足す");
    fireEvent.mouseDown(label(container, "a21"));
    expect(form()).toHaveTextContent("1.1 業務整理 に活動を足す");
    fireEvent.keyDown(viewport, { key: "Escape" });
    expect(form()).toHaveTextContent("最上位に要素を足す");
  });

  it("選んだ活動の欄は、保存して読み直した値に合わせる（古い値で巻き戻さない）", async () => {
    let current = ACTIVITIES;
    const { container } = await startEditing(PLANNER, {
      [`GET ${BASE}/activities`]: () => jsonResponse(200, activityList(current, { dependencies: DEPENDENCIES })),
      [`PATCH ${BASE}/activities/21`]: () => {
        current = [{ ...ACTIVITIES[0]!, duration_days: 7 }, ...ACTIVITIES.slice(1)];
        return jsonResponse(200, current[0]);
      },
    });
    fireEvent.mouseDown(label(container, "a21"));
    const duration = () => within(screen.getByRole("region", { name: "選んだ行" })).getByRole("spinbutton", { name: "期間（稼働日）" });
    expect(duration()).toHaveValue(5);
    fireEvent.mouseDown(container.querySelector('[data-handle="resize"][data-activity-id="21"]')!, { clientX: 400 });
    fireEvent.mouseMove(window, { clientX: 448 });
    fireEvent.mouseUp(window, { clientX: 448 });
    await waitFor(() => expect(duration()).toHaveValue(7));
  });

  it("選んだ活動の欄：希望開始日を外す・別の WP へ移す・先行を外す", async () => {
    const { container, calls } = await startEditing(PLANNER, {
      [`PATCH ${BASE}/activities/22`]: () => jsonResponse(200, makeActivity({ id: 22, start_date: "2026-12-05" })),
      [`DELETE ${BASE}/dependencies/91`]: () => jsonResponse(204, null),
    });
    fireEvent.mouseDown(label(container, "a22"));
    const panel = screen.getByRole("region", { name: "選んだ行" });
    fireEvent.click(within(panel).getByRole("button", { name: "希望開始日を外す" }));
    await waitFor(() => expect(sent(calls, "PATCH", `${BASE}/activities/22`)).toHaveLength(1));
    expect(sent(calls, "PATCH", `${BASE}/activities/22`)[0]?.body).toEqual({ requested_start: null });
    await settled();

    const moveForm = within(panel).getByRole("form", { name: "移す先の WP" });
    const options = within(moveForm).getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(["（選ぶ）", "1.2 画面設計"]);
    fireEvent.change(within(moveForm).getByRole("combobox"), { target: { value: "102" } });
    fireEvent.submit(moveForm);
    await waitFor(() => expect(sent(calls, "PATCH", `${BASE}/activities/22`)).toHaveLength(2));
    expect(sent(calls, "PATCH", `${BASE}/activities/22`)[1]?.body).toEqual({ work_package_id: 102 });
    await settled();

    fireEvent.click(within(panel).getByRole("button", { name: "先行 1.1 聞き取り を外す" }));
    await waitFor(() => expect(sent(calls, "DELETE", `${BASE}/dependencies/91`)).toHaveLength(1));
  });

  it("schedule.edit だけの人には要素の操作（インデント）を出さず、バーは動かせる", async () => {
    const { container } = await startEditing(["project.view", "schedule.edit"]);
    expect(screen.queryByRole("button", { name: "インデント" })).toBeNull();
    expect(container.querySelector('[data-handle="move"]')).not.toBeNull();
    fireEvent.mouseDown(label(container, "e100"));
    expect(screen.queryByRole("form", { name: "行を足す" })).toBeNull();
    // Insert でも足す欄は出さず、理由を示す
    fireEvent.keyDown(screen.getByTestId("gantt-viewport"), { key: "Insert" });
    expect(screen.getByText("要素を足す権限（wbs.edit）がありません")).toBeInTheDocument();
  });

  it("wbs.edit だけの人にはバーの掴みを出さない", async () => {
    const { container } = await startEditing(["project.view", "wbs.edit"]);
    expect(container.querySelector('[data-handle="move"]')).toBeNull();
    expect(screen.getByRole("button", { name: "インデント" })).toBeInTheDocument();
  });

  it("基準化の後は、要素を変えると作成中に戻ることを示す", async () => {
    await startEditing(PLANNER, { [`GET ${BASE}/wbs`]: () => jsonResponse(200, makeTree(sampleElements(), { wbs_status: "BASELINED" })) });
    expect(screen.getByText("WBS は基準化済み。要素を変えると作成中に戻る（5.10）")).toBeInTheDocument();
  });

  it("空の計画でも編集では描き、最上位の要素を足せる", async () => {
    const empty = makeGantt({ range_start: null, range_end: null, rows: [], links: [] });
    mockApi(
      routes(PLANNER, {
        [`GET ${BASE}/gantt`]: () => jsonResponse(200, empty),
        [`GET ${BASE}/wbs`]: () => jsonResponse(200, makeTree([])),
        [`GET ${BASE}/activities`]: () => jsonResponse(200, activityList([])),
      }),
    );
    const { container } = renderAt();
    // 編集していなければ今までどおり
    expect(await screen.findByText("描く活動がありません")).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "編集する" }));
    await screen.findByRole("toolbar", { name: "編集の操作" });
    expect(container.querySelector('svg[aria-label="ガントチャート"]')?.getAttribute("data-row-count")).toBe("0");
    expect(screen.getByText("WBS の要素がまだ無い。「行を足す」で最上位の要素を作る")).toBeInTheDocument();
    expect(screen.getByRole("form", { name: "行を足す" })).toHaveTextContent("最上位に要素を足す");
  });

  it("WBS を読めなければ編集できないことを示す", async () => {
    mockApi(routes(PLANNER, { [`GET ${BASE}/wbs`]: () => errorResponse(500, "INTERNAL", "失敗") }));
    renderAt();
    await screen.findByRole("img", { name: "ガントチャート" });
    fireEvent.click(await screen.findByRole("button", { name: "編集する" }));
    expect(await screen.findByText("WBS を読めないので編集できません。読み直してください")).toBeInTheDocument();
    expect(screen.queryByRole("toolbar", { name: "編集の操作" })).toBeNull();
  });
});
