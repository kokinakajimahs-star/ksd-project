// ガント（UI-SCH-002）の WBS の要素の行と編集（設計書 7.3。CR-024・CR-025：図の上の操作だけで編集する。
// CR-027：編集できる人は開いたときから編集する）
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setAuthToken } from "../../../api/client";
import type { Me } from "../../../api/cmn";
import type { Dependency, Gantt } from "../../../api/sch";
import { HEADER_HEIGHT, ROW_HEIGHT } from "../../../components/gantt/scale";
import { makeMe, makeMember, makeUser } from "../../../test/fixtures";
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


function strip(): HTMLElement {
  return screen.getByRole("region", { name: "ガントの編集" });
}

/** 保存の後の読み直しが終わるまで待つ（終わるまで次の操作は受けない） */
async function settled() {
  await waitFor(() => expect(strip()).toHaveAttribute("aria-busy", "false"));
}

const WAIT_TEXT = "前の操作の保存・読み直しが終わっていません。終わってからもう一度操作します";

async function startEditing(permissions = PLANNER, overrides: Record<string, RouteHandler> = {}) {
  const api = mockApi(routes(permissions, overrides));
  const view = renderAt();
  await screen.findByRole("img", { name: "ガントチャート" });
  // 「編集する」への切り替えは無い（CR-027）。編集の帯が出るまで待つ
  await screen.findByRole("region", { name: "ガントの編集" });
  await waitFor(() => expect(view.container.querySelector('[data-layer="edit-rows"]')).not.toBeNull());
  const viewport = screen.getByTestId("gantt-viewport");
  return { ...api, ...view, viewport };
}

/** 行の見出しを右クリックしてメニューを開く（押した所の縦の位置で行が決まる） */
function openMenu(container: HTMLElement, key: string): HTMLElement {
  fireEvent.contextMenu(label(container, key).querySelector("rect")!, { clientX: 40, clientY: rowY(container, key) });
  return screen.getByRole("menu");
}

function menuNames(menu: HTMLElement): string[] {
  return [...menu.querySelectorAll('[role="menuitem"], [role="menuitemradio"]')].map((e) => e.textContent ?? "");
}

function choose(name: string) {
  const menu = within(screen.getByRole("menu"));
  fireEvent.click(menu.queryByRole("menuitem", { name }) ?? menu.getByRole("menuitemradio", { name }));
}

/** 差し込んだ入力の行に名前を入れて Enter */
function typeDraft(label: string, value: string) {
  const input = screen.getByRole("textbox", { name: label });
  expect(input).toHaveFocus();
  fireEvent.change(input, { target: { value } });
  fireEvent.keyDown(input, { key: "Enter" });
}

describe("UI-SCH-002 の WBS の要素の行と編集（CR-024・CR-025）", () => {
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
    // 閲覧だけの人には編集の帯・掴みを出さない
    expect(screen.queryByRole("region", { name: "ガントの編集" })).toBeNull();
    expect(container.querySelector('[data-layer="edit-rows"]')).toBeNull();
  });

  it("WBS を読めなければ要素の行を出さずに今までどおり描く", async () => {
    mockApi(routes(["project.view"], { [`GET ${BASE}/wbs`]: () => errorResponse(403, "FORBIDDEN", "権限がありません") }));
    const { container } = renderAt();
    await waitFor(() => expect(rowKeys(container)).toEqual(["a21", "a22", "a23"]));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("編集できる人には開いたときから活動の無い要素も出し、WBS の状態を帯に出す。帯に入力欄と操作のボタンを置かない（CR-025・CR-027）", async () => {
    const { container, calls } = await startEditing();
    expect(screen.queryByRole("button", { name: "編集する" })).toBeNull();
    expect(rowKeys(container)).toEqual(["e100", "e101", "a21", "a22", "e102", "a23", "e103"]);
    expect(sent(calls, "GET", `${BASE}/activities`)).toHaveLength(1);
    expect(within(strip()).getByText("作成中")).toBeInTheDocument();
    expect(within(strip()).queryAllByRole("textbox")).toHaveLength(0);
    expect(within(strip()).queryAllByRole("button")).toHaveLength(0);
    // 操作の手引きは畳んでおく
    expect(within(strip()).getByText("操作の手引き").closest("details")).not.toHaveAttribute("open");
    expect(within(strip()).queryAllByRole("combobox")).toHaveLength(0);
    expect(screen.queryByRole("region", { name: "選んだ行" })).toBeNull();
    // 希望開始日のある活動に印を付ける
    expect(container.querySelector('[data-requested-start="22"]')).not.toBeNull();
    expect(container.querySelector('[data-requested-start="21"]')).toBeNull();
  });

  it("選んだ行の要約を帯の 1 行に出し、保存して読み直した値に合わせる", async () => {
    let current = ACTIVITIES;
    const { container } = await startEditing(PLANNER, {
      [`GET ${BASE}/activities`]: () => jsonResponse(200, activityList(current, { dependencies: DEPENDENCIES })),
      [`PATCH ${BASE}/activities/21`]: () => {
        current = [{ ...ACTIVITIES[0]!, duration_days: 7 }, ...ACTIVITIES.slice(1)];
        return jsonResponse(200, current[0]);
      },
    });
    expect(within(strip()).getByText("選んだ行：なし（Enter で最上位に要素を足す）")).toBeInTheDocument();
    fireEvent.mouseDown(label(container, "a21"));
    expect(within(strip()).getByText(/^選んだ行：1\.1 聞き取り（予定 .+・期間 5 稼働日・余裕 0 日・先行 0 件・後続 1 件）$/)).toBeInTheDocument();
    fireEvent.mouseDown(container.querySelector('[data-handle="resize"][data-activity-id="21"]')!, { clientX: 400 });
    fireEvent.mouseMove(window, { clientX: 448 });
    fireEvent.mouseUp(window, { clientX: 448 });
    expect(await within(strip()).findByText(/期間 7 稼働日/)).toBeInTheDocument();
    fireEvent.mouseDown(label(container, "e101"));
    expect(within(strip()).getByText("選んだ行：1.1 業務整理（ワークパッケージ）")).toBeInTheDocument();
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
    expect(viewport).toHaveFocus();

    fireEvent.doubleClick(label(container, "e101"));
    const again = await screen.findByRole("textbox", { name: "名前" });
    fireEvent.change(again, { target: { value: "捨てる名前" } });
    fireEvent.keyDown(again, { key: "Escape" });
    expect(screen.queryByRole("textbox", { name: "名前" })).toBeNull();
    expect(sent(calls, "PATCH", `${BASE}/wbs/elements/101`)).toHaveLength(0);
  });

  it("活動の行で Enter：その活動の次に入力の行を差し込み、名前を入れて Enter で足して次の位置へ置く（API-SCH-002・003）", async () => {
    const { container, calls, viewport } = await startEditing(PLANNER, {
      [`POST ${BASE}/activities`]: () => jsonResponse(201, makeActivity({ id: 30, name: "議事録" })),
      [`PATCH ${BASE}/activities/30`]: () => jsonResponse(200, makeActivity({ id: 30, name: "議事録" })),
    });
    fireEvent.mouseDown(label(container, "e101"));
    fireEvent.keyDown(viewport, { key: "ArrowDown" });
    expect(label(container, "a21").querySelector("rect")?.getAttribute("style")).toContain("--bgColor-accent-muted");
    fireEvent.keyDown(viewport, { key: "Enter" });
    // 名前を変えるのではなく、a21 の下に入力の行を差し込む
    expect(screen.queryByRole("textbox", { name: "名前" })).toBeNull();
    expect(rowKeys(container)).toEqual(["e100", "e101", "a21", "", "a22", "e102", "a23", "e103"]);
    expect(screen.getByRole("textbox", { name: "足す行の名前" })).toHaveAttribute("placeholder", "活動の名前（Enter で足す・Esc でやめる）");

    typeDraft("足す行の名前", "議事録");
    await waitFor(() => expect(sent(calls, "PATCH", `${BASE}/activities/30`)).toHaveLength(1));
    expect(sent(calls, "POST", `${BASE}/activities`)[0]?.body).toEqual({ work_package_id: 101, name: "議事録", duration_days: 1 });
    expect(sent(calls, "PATCH", `${BASE}/activities/30`)[0]?.body).toEqual({ position: 1 });
    expect(await screen.findByText("1.1 業務整理 に活動「議事録」を足した（期間 1 稼働日）")).toBeInTheDocument();
    // 入力の行は消え、図に的が戻る（続けて Enter で次の行を足せる）
    expect(rowKeys(container)).not.toContain("");
    expect(viewport).toHaveFocus();
    await waitFor(() => expect(sent(calls, "GET", `${BASE}/gantt`).length).toBeGreaterThanOrEqual(2));
  });

  it("WP の末尾の活動・WP の行で Enter：WP の末尾に足す（位置は動かさない）。Insert でも足す", async () => {
    const { container, calls, viewport } = await startEditing(PLANNER, {
      [`POST ${BASE}/activities`]: () => jsonResponse(201, makeActivity({ id: 30 })),
    });
    fireEvent.mouseDown(label(container, "a22"));
    fireEvent.keyDown(viewport, { key: "Enter" });
    expect(rowKeys(container)).toEqual(["e100", "e101", "a21", "a22", "", "e102", "a23", "e103"]);
    typeDraft("足す行の名前", "まとめ");
    await waitFor(() => expect(sent(calls, "POST", `${BASE}/activities`)).toHaveLength(1));
    await settled();

    fireEvent.mouseDown(label(container, "e102"));
    fireEvent.keyDown(viewport, { key: "Insert" });
    expect(rowKeys(container)).toEqual(["e100", "e101", "a21", "a22", "e102", "a23", "", "e103"]);
    typeDraft("足す行の名前", "画面一覧");
    await waitFor(() => expect(sent(calls, "POST", `${BASE}/activities`)).toHaveLength(2));
    expect(sent(calls, "POST", `${BASE}/activities`).map((c) => c.body)).toEqual([
      { work_package_id: 101, name: "まとめ", duration_days: 1 },
      { work_package_id: 102, name: "画面一覧", duration_days: 1 },
    ]);
    expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(0);
  });

  it("WP 以外の要素の行で Enter：子の要素（WP）を配下の行の後に足す（API-WBS-002）", async () => {
    const { container, calls, viewport } = await startEditing(PLANNER, {
      [`POST ${BASE}/wbs/elements`]: () => jsonResponse(201, makeElement({ id: 110 })),
    });
    fireEvent.mouseDown(label(container, "e100"));
    fireEvent.keyDown(viewport, { key: "Enter" });
    expect(rowKeys(container)).toEqual(["e100", "e101", "a21", "a22", "e102", "a23", "", "e103"]);
    expect(screen.getByRole("textbox", { name: "足す行の名前" })).toHaveAttribute(
      "placeholder",
      "要素（ワークパッケージ）の名前（Enter で足す・Esc でやめる）",
    );
    typeDraft("足す行の名前", "移行設計");
    await waitFor(() => expect(sent(calls, "POST", `${BASE}/wbs/elements`)).toHaveLength(1));
    expect(sent(calls, "POST", `${BASE}/wbs/elements`)[0]?.body).toEqual({ parent_id: 100, kind: "WORK_PACKAGE", name: "移行設計" });
    expect(await screen.findByText("1.0 要件定義 の下にワークパッケージ「移行設計」を足した")).toBeInTheDocument();
  });

  it("入力の行は Esc・空のまま外へ出れば何も足さない。選択を外して Enter なら最上位に足す", async () => {
    const { container, calls, viewport } = await startEditing(PLANNER, {
      [`POST ${BASE}/wbs/elements`]: () => jsonResponse(201, makeElement({ id: 110, parent_id: null })),
    });
    fireEvent.mouseDown(label(container, "a21"));
    fireEvent.keyDown(viewport, { key: "Enter" });
    fireEvent.keyDown(screen.getByRole("textbox", { name: "足す行の名前" }), { key: "Escape" });
    expect(screen.queryByRole("textbox", { name: "足す行の名前" })).toBeNull();
    expect(viewport).toHaveFocus();
    fireEvent.keyDown(viewport, { key: "Enter" });
    act(() => viewport.focus());
    expect(screen.queryByRole("textbox", { name: "足す行の名前" })).toBeNull();
    expect(calls.filter((c) => c.method !== "GET")).toHaveLength(0);

    // Ctrl＋クリックで外す・Esc で外す：最上位の末尾に足す
    fireEvent.mouseDown(label(container, "a21"), { ctrlKey: true });
    fireEvent.mouseDown(label(container, "e100"));
    fireEvent.keyDown(viewport, { key: "Escape" });
    fireEvent.keyDown(viewport, { key: "Enter" });
    expect(rowKeys(container)).toEqual(["e100", "e101", "a21", "a22", "e102", "a23", "e103", ""]);
    typeDraft("足す行の名前", "移行");
    await waitFor(() => expect(sent(calls, "POST", `${BASE}/wbs/elements`)).toHaveLength(1));
    expect(sent(calls, "POST", `${BASE}/wbs/elements`)[0]?.body).toEqual({ parent_id: null, kind: "WORK_PACKAGE", name: "移行" });
  });

  it("schedule.edit だけの人が要素の行で Enter を押すと、入力の行を出さずに理由を示す", async () => {
    const { container, viewport } = await startEditing(["project.view", "schedule.edit"]);
    fireEvent.mouseDown(label(container, "e100"));
    fireEvent.keyDown(viewport, { key: "Enter" });
    expect(screen.queryByRole("textbox", { name: "足す行の名前" })).toBeNull();
    expect(screen.getByText("要素を足す権限（wbs.edit）がありません")).toBeInTheDocument();
  });

  it("活動の行の右クリックで、その行の操作のメニューを出す（Enter・F2 などのキーを添える）", async () => {
    const { container } = await startEditing();
    const menu = openMenu(container, "a22");
    expect(menu).toHaveAccessibleName("1.1 整理 の操作");
    expect(menuNames(menu)).toEqual([
      "詳細と進捗…",
      "下に活動を足すEnter",
      "名前を変えるF2",
      "後続をつなぐ…",
      "別の WP へ移す…",
      "希望開始日を指定…",
      "希望開始日を外す",
      "期間を指定…",
      "上へAlt+Shift+↑",
      "下へAlt+Shift+↓",
      "新しい WP にまとめる…",
      "先行「1.1 聞き取り」との依存を外す",
      "消すDelete",
    ]);
    // 右クリックした行を選ぶ。最初の項目に的を置く
    expect(container.querySelector('[data-layer="edit-rows"] [data-row-key="a22"]')?.getAttribute("data-selected")).toBe("true");
    expect(within(menu).getByRole("menuitem", { name: "詳細と進捗…" })).toHaveFocus();

    choose("下に活動を足す");
    expect(screen.queryByRole("menu")).toBeNull();
    expect(screen.getByRole("textbox", { name: "足す行の名前" })).toHaveFocus();
    expect(rowKeys(container)).toEqual(["e100", "e101", "a21", "a22", "", "e102", "a23", "e103"]);
  });

  it("Shift+F10 で選んだ行のメニューを開き、矢印で項目を選ぶ。Esc で閉じて図に的を戻す", async () => {
    const { container, viewport } = await startEditing();
    fireEvent.mouseDown(label(container, "a21"));
    fireEvent.keyDown(viewport, { key: "F10", shiftKey: true });
    const menu = screen.getByRole("menu", { name: "1.1 聞き取り の操作" });
    fireEvent.keyDown(within(menu).getByRole("menuitem", { name: "詳細と進捗…" }), { key: "ArrowDown" });
    expect(within(menu).getByRole("menuitem", { name: "下に活動を足す" })).toHaveFocus();
    fireEvent.keyDown(within(menu).getByRole("menuitem", { name: "下に活動を足す" }), { key: "ArrowDown" });
    expect(within(menu).getByRole("menuitem", { name: "名前を変える" })).toHaveFocus();
    fireEvent.keyDown(within(menu).getByRole("menuitem", { name: "名前を変える" }), { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
    await waitFor(() => expect(viewport).toHaveFocus());

    // メニューキーでも開き、外を押せば閉じる
    fireEvent.keyDown(viewport, { key: "ContextMenu" });
    expect(screen.getByRole("menu")).toBeInTheDocument();
    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("メニューの「後続をつなぐ…」の後、後続の行をクリックすると依存を足す（API-SCH-006）。活動以外は理由を示し、Esc でやめる", async () => {
    const { container, calls, viewport } = await startEditing(PLANNER, {
      [`POST ${BASE}/dependencies`]: () => jsonResponse(201, { id: 92, predecessor_id: 21, successor_id: 23, dep_type: "FS", lag_days: 0 }),
    });
    openMenu(container, "a21");
    choose("後続をつなぐ…");
    expect(screen.getByText("1.1 聞き取り の後続にする活動の行を選ぶ（クリック、または矢印で選んで Enter。Esc でやめる）")).toBeInTheDocument();
    expect(label(container, "a21").getAttribute("data-picking")).toBe("source");
    expect(viewport).toHaveFocus();

    fireEvent.mouseDown(label(container, "e102"));
    expect(screen.getByText("後続には別の活動の行を選びます（Esc でやめる）")).toBeInTheDocument();
    fireEvent.keyDown(viewport, { key: "Escape" });
    expect(label(container, "a21").getAttribute("data-picking")).toBeNull();
    expect(container.querySelector('[data-layer="edit-rows"] [data-row-key="a21"]')?.getAttribute("data-selected")).toBe("true");
    expect(sent(calls, "POST", `${BASE}/dependencies`)).toHaveLength(0);

    openMenu(container, "a21");
    choose("後続をつなぐ…");
    fireEvent.mouseDown(label(container, "a23"));
    await waitFor(() => expect(sent(calls, "POST", `${BASE}/dependencies`)).toHaveLength(1));
    expect(sent(calls, "POST", `${BASE}/dependencies`)[0]?.body).toEqual({ predecessor_id: 21, successor_id: 23 });
    expect(label(container, "a21").getAttribute("data-picking")).toBeNull();
  });

  it("メニューの「別の WP へ移す…」の後、矢印で移す先の行を選んで Enter（活動の行ならその次へ。API-SCH-003）", async () => {
    const { container, calls, viewport } = await startEditing(PLANNER, {
      [`PATCH ${BASE}/activities/21`]: () => jsonResponse(200, ACTIVITIES[0]),
    });
    openMenu(container, "a21");
    choose("別の WP へ移す…");
    fireEvent.keyDown(viewport, { key: "ArrowDown" });
    fireEvent.keyDown(viewport, { key: "ArrowDown" });
    fireEvent.keyDown(viewport, { key: "ArrowDown" });
    expect(container.querySelector('[data-layer="edit-rows"] [data-row-key="a23"]')?.getAttribute("data-selected")).toBe("true");
    fireEvent.keyDown(viewport, { key: "Enter" });
    await waitFor(() => expect(sent(calls, "PATCH", `${BASE}/activities/21`)).toHaveLength(1));
    expect(sent(calls, "PATCH", `${BASE}/activities/21`)[0]?.body).toEqual({ work_package_id: 102, position: 1 });
    expect(await screen.findByText("1.1 聞き取り を 1.2 画面設計 へ移した")).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "足す行の名前" })).toBeNull();
  });

  it("メニューの「希望開始日を指定…」「期間を指定…」は行の中の入力で、Enter で保存する。「希望開始日を外す」", async () => {
    const { container, calls } = await startEditing(PLANNER, {
      [`PATCH ${BASE}/activities/22`]: () =>
        jsonResponse(200, makeActivity({ id: 22, requested_start: "2026-12-08", start_date: "2026-12-08", duration_days: 4 })),
    });
    openMenu(container, "a22");
    choose("希望開始日を指定…");
    const date = screen.getByLabelText("希望開始日");
    expect(date).toHaveValue("2026-12-07");
    expect(date).toHaveFocus();
    fireEvent.change(date, { target: { value: "2026-12-08" } });
    fireEvent.keyDown(date, { key: "Enter" });
    await waitFor(() => expect(sent(calls, "PATCH", `${BASE}/activities/22`)).toHaveLength(1));
    expect(sent(calls, "PATCH", `${BASE}/activities/22`)[0]?.body).toEqual({ requested_start: "2026-12-08" });
    await settled();

    openMenu(container, "a22");
    choose("期間を指定…");
    const duration = screen.getByRole("spinbutton", { name: "期間（稼働日）" });
    expect(duration).toHaveValue(5);
    fireEvent.change(duration, { target: { value: "0" } });
    fireEvent.keyDown(duration, { key: "Enter" });
    expect(screen.getByText("期間は 1 以上の整数（稼働日）で入れます")).toBeInTheDocument();
    openMenu(container, "a22");
    choose("期間を指定…");
    fireEvent.change(screen.getByRole("spinbutton", { name: "期間（稼働日）" }), { target: { value: "4" } });
    fireEvent.keyDown(screen.getByRole("spinbutton", { name: "期間（稼働日）" }), { key: "Enter" });
    await waitFor(() => expect(sent(calls, "PATCH", `${BASE}/activities/22`)).toHaveLength(2));
    expect(sent(calls, "PATCH", `${BASE}/activities/22`)[1]?.body).toEqual({ duration_days: 4 });
    await settled();

    openMenu(container, "a22");
    choose("希望開始日を外す");
    await waitFor(() => expect(sent(calls, "PATCH", `${BASE}/activities/22`)).toHaveLength(3));
    expect(sent(calls, "PATCH", `${BASE}/activities/22`)[2]?.body).toEqual({ requested_start: null });
  });

  it("活動の行のメニューから先行との依存を外す（API-SCH-007）", async () => {
    const { container, calls } = await startEditing(PLANNER, {
      [`DELETE ${BASE}/dependencies/91`]: () => jsonResponse(204, null),
    });
    openMenu(container, "a22");
    choose("先行「1.1 聞き取り」との依存を外す");
    await waitFor(() => expect(sent(calls, "DELETE", `${BASE}/dependencies/91`)).toHaveLength(1));
  });

  it("要素の行のメニュー：区分を変える（API-WBS-003）・WBS 辞書を開く項目・WP 以外なら子の要素を足す", async () => {
    const { container, calls } = await startEditing(PLANNER, {
      [`PATCH ${BASE}/wbs/elements/100`]: () => jsonResponse(200, sampleElements()[0]),
    });
    const menu = openMenu(container, "e100");
    expect(menuNames(menu)).toEqual([
      "子の要素を足すEnter",
      "下に同じ階層の要素を足す",
      "名前を変えるF2",
      "○区分：成果物".replace("○", "●"),
      "○区分：管理領域",
      "○区分：構成要素",
      "○区分：ワークパッケージ",
      "インデントAlt+Shift+→",
      "アウトデントAlt+Shift+←",
      "上へAlt+Shift+↑",
      "下へAlt+Shift+↓",
      "新しい上位の要素にまとめる…",
      "WBS 辞書・担当・見積を開く",
      "消すDelete",
    ]);
    expect(within(menu).getByRole("menuitemradio", { name: "区分：成果物" })).toHaveAttribute("aria-checked", "true");
    expect(within(menu).getByRole("menuitem", { name: "WBS 辞書・担当・見積を開く" })).toHaveAttribute("href", "/projects/3/wbs/100");
    choose("区分：構成要素");
    await waitFor(() => expect(sent(calls, "PATCH", `${BASE}/wbs/elements/100`)).toHaveLength(1));
    expect(sent(calls, "PATCH", `${BASE}/wbs/elements/100`)[0]?.body).toEqual({ kind: "COMPONENT" });
    expect(await screen.findByText("1.0 要件定義 の区分を構成要素にした")).toBeInTheDocument();
  });

  it("要素の行のメニューの「下に同じ階層の要素を足す」：同じ区分で足し、選んだ要素の次へ置く（API-WBS-002・004）", async () => {
    const { container, calls } = await startEditing(PLANNER, {
      [`POST ${BASE}/wbs/elements`]: () => jsonResponse(201, makeElement({ id: 120, parent_id: 100, kind: "WORK_PACKAGE" })),
      [`POST ${BASE}/wbs/elements/120/move`]: () => jsonResponse(200, makeElement({ id: 120 })),
    });
    openMenu(container, "e101");
    choose("下に同じ階層の要素を足す");
    expect(rowKeys(container)).toEqual(["e100", "e101", "a21", "a22", "", "e102", "a23", "e103"]);
    typeDraft("足す行の名前", "現行調査");
    await waitFor(() => expect(sent(calls, "POST", `${BASE}/wbs/elements/120/move`)).toHaveLength(1));
    expect(sent(calls, "POST", `${BASE}/wbs/elements`)[0]?.body).toEqual({ parent_id: 100, kind: "WORK_PACKAGE", name: "現行調査" });
    expect(sent(calls, "POST", `${BASE}/wbs/elements/120/move`)[0]?.body).toEqual({ parent_id: 100, position: 1 });
  });

  it("足した後に位置へ置けなければ、足したことと置けなかったことを示す", async () => {
    const { container, viewport } = await startEditing(PLANNER, {
      [`POST ${BASE}/activities`]: () => jsonResponse(201, makeActivity({ id: 30 })),
      [`PATCH ${BASE}/activities/30`]: () => errorResponse(409, "CONFLICT", "他の人が変えました"),
    });
    fireEvent.mouseDown(label(container, "a21"));
    fireEvent.keyDown(viewport, { key: "Enter" });
    typeDraft("足す行の名前", "議事録");
    expect(
      await screen.findByText("他の人が変えました（活動「議事録」は 1.1 業務整理 の末尾に足した。選んだ活動の次へは置けなかった）"),
    ).toBeInTheDocument();
  });

  it("Alt+Shift+→ で要素をインデントし（API-WBS-004）、直前が WP ならメニューからでも理由を示して呼ばない", async () => {
    const { container, calls, viewport } = await startEditing(PLANNER, {
      [`POST ${BASE}/wbs/elements/103/move`]: () => jsonResponse(200, sampleElements()[3]),
    });
    fireEvent.mouseDown(label(container, "e103"));
    fireEvent.keyDown(viewport, { key: "ArrowRight", altKey: true, shiftKey: true });
    await waitFor(() => expect(sent(calls, "POST", `${BASE}/wbs/elements/103/move`)).toHaveLength(1));
    expect(sent(calls, "POST", `${BASE}/wbs/elements/103/move`)[0]?.body).toEqual({ parent_id: 100 });

    openMenu(container, "e102");
    choose("インデント");
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

  it("バーの本体を縦に別の活動のバーへ引くと依存の線に変わり、落とすと依存を足す（日付は動かさない。CR-025）", async () => {
    const { container, calls } = await startEditing(PLANNER, {
      [`POST ${BASE}/dependencies`]: () => jsonResponse(201, { id: 92, predecessor_id: 22, successor_id: 21, dep_type: "FS", lag_days: 0 }),
    });
    const grip = container.querySelector('[data-handle="move"][data-activity-id="22"]')!;
    fireEvent.mouseDown(grip, { clientX: barX(container, 22), clientY: rowY(container, "a22") });
    fireEvent.mouseMove(window, { clientX: barX(container, 21), clientY: rowY(container, "a21") });
    expect(container.querySelector('[data-ghost="link"]')).not.toBeNull();
    expect(container.querySelector('[data-ghost="move"]')).toBeNull();
    fireEvent.mouseUp(window, { clientX: barX(container, 21), clientY: rowY(container, "a21") });
    await waitFor(() => expect(sent(calls, "POST", `${BASE}/dependencies`)).toHaveLength(1));
    expect(sent(calls, "POST", `${BASE}/dependencies`)[0]?.body).toEqual({ predecessor_id: 22, successor_id: 21 });
    expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(0);
    await settled();

    // 行の見出しへ落としてもつなぐ
    fireEvent.mouseDown(container.querySelector('[data-handle="move"][data-activity-id="22"]')!, {
      clientX: barX(container, 22),
      clientY: rowY(container, "a22"),
    });
    fireEvent.mouseUp(window, { clientX: 40, clientY: rowY(container, "a23") });
    await waitFor(() => expect(sent(calls, "POST", `${BASE}/dependencies`)).toHaveLength(2));
    expect(sent(calls, "POST", `${BASE}/dependencies`)[1]?.body).toEqual({ predecessor_id: 22, successor_id: 23 });
  });

  it("縦に引いた後で元の行へ戻せば日付の移動に戻る。要素の行・空いた所に落とせば何もしない", async () => {
    const { container, calls } = await startEditing(PLANNER, {
      [`PATCH ${BASE}/activities/22`]: () => jsonResponse(200, makeActivity({ id: 22, requested_start: "2026-12-09", start_date: "2026-12-09" })),
    });
    const x0 = barX(container, 22);
    const grip = () => container.querySelector('[data-handle="move"][data-activity-id="22"]')!;
    fireEvent.mouseDown(grip(), { clientX: x0, clientY: rowY(container, "a22") });
    fireEvent.mouseUp(window, { clientX: x0, clientY: rowY(container, "e102") });
    fireEvent.mouseDown(grip(), { clientX: x0, clientY: rowY(container, "a22") });
    fireEvent.mouseUp(window, { clientX: x0 + 600, clientY: rowY(container, "a23") });
    expect(calls.filter((c) => c.method !== "GET")).toHaveLength(0);

    fireEvent.mouseDown(grip(), { clientX: x0, clientY: rowY(container, "a22") });
    fireEvent.mouseMove(window, { clientX: x0 + 48, clientY: rowY(container, "a21") });
    fireEvent.mouseMove(window, { clientX: x0 + 48, clientY: rowY(container, "a22") });
    expect(container.querySelector('[data-ghost="link"]')).toBeNull();
    expect(container.querySelector('[data-ghost="move"]')).toHaveTextContent("2026-12-09");
    fireEvent.mouseUp(window, { clientX: x0 + 48, clientY: rowY(container, "a22") });
    await waitFor(() => expect(sent(calls, "PATCH", `${BASE}/activities/22`)).toHaveLength(1));
    expect(sent(calls, "PATCH", `${BASE}/activities/22`)[0]?.body).toEqual({ requested_start: "2026-12-09" });
    expect(sent(calls, "POST", `${BASE}/dependencies`)).toHaveLength(0);
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

  it("右の丸からバーの外（要素の行・行の空いた所・目盛りの帯）へ落としても依存は足さない", async () => {
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

  it("矢印を選んで Delete、または矢印の右クリックの「依存を外す」で依存を外す（API-SCH-007）", async () => {
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
    await settled();

    fireEvent.contextMenu(container.querySelector('[data-link-hit="21-22"]')!, { clientX: 300, clientY: 120 });
    const menu = screen.getByRole("menu", { name: "依存の操作" });
    expect(menuNames(menu)).toEqual(["依存を外すDelete"]);
    choose("依存を外す");
    await waitFor(() => expect(sent(calls, "DELETE", `${BASE}/dependencies/91`)).toHaveLength(2));
  });

  it("選んだ活動のメニューで新しい WP にまとめる：名前の入力の行で名前を入れ、最初の活動の WP の次に作って表示順に 1 件ずつ移す", async () => {
    const { container, calls } = await startEditing(PLANNER, {
      [`POST ${BASE}/wbs/elements`]: () => jsonResponse(201, makeElement({ id: 120, parent_id: 100, kind: "WORK_PACKAGE" })),
      [`POST ${BASE}/wbs/elements/120/move`]: () => jsonResponse(200, makeElement({ id: 120 })),
      [`PATCH ${BASE}/activities/22`]: () => jsonResponse(200, ACTIVITIES[1]),
      [`PATCH ${BASE}/activities/23`]: () => jsonResponse(200, ACTIVITIES[2]),
    });
    fireEvent.mouseDown(label(container, "a23"));
    fireEvent.mouseDown(label(container, "a22"), { ctrlKey: true });
    const menu = openMenu(container, "a22");
    expect(menu).toHaveAccessibleName("選んだ行の操作");
    expect(menuNames(menu)).toEqual(["選んだ活動 2 件を新しい WP にまとめる…", "選んだ 2 行を消すDelete"]);
    choose("選んだ活動 2 件を新しい WP にまとめる…");
    // 選んだ最初の行（表示順）の上に入力の行を差し込む
    expect(rowKeys(container)).toEqual(["e100", "e101", "a21", "", "a22", "e102", "a23", "e103"]);
    typeDraft("まとめる先の名前", "調整");
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
    openMenu(container, "a21");
    choose("選んだ活動 2 件を新しい WP にまとめる…");
    typeDraft("まとめる先の名前", "調整");
    expect(
      await screen.findByText("他の人が変えました（新しい WP「調整」は作った。1 件は移した。移せなかった活動：整理）"),
    ).toBeInTheDocument();
  });

  it("選んだ要素を新しい上位の要素にまとめる（区分は構成要素で作る）。1 件でもまとめられる", async () => {
    const { container, calls } = await startEditing(PLANNER, {
      [`POST ${BASE}/wbs/elements`]: () => jsonResponse(201, makeElement({ id: 130, parent_id: 100, kind: "COMPONENT" })),
      [`POST ${BASE}/wbs/elements/130/move`]: () => jsonResponse(200, makeElement({ id: 130 })),
      [`POST ${BASE}/wbs/elements/101/move`]: () => jsonResponse(200, sampleElements()[1]),
      [`POST ${BASE}/wbs/elements/102/move`]: () => jsonResponse(200, sampleElements()[2]),
    });
    fireEvent.mouseDown(label(container, "e101"));
    fireEvent.mouseDown(label(container, "e102"), { ctrlKey: true });
    openMenu(container, "e102");
    choose("選んだ要素 2 件を新しい上位の要素にまとめる…");
    expect(screen.getByRole("textbox", { name: "まとめる先の名前" })).toHaveAttribute(
      "placeholder",
      "新しい上位の要素の名前（Enter でまとめる・Esc でやめる）",
    );
    typeDraft("まとめる先の名前", "設計");
    await screen.findByText("要素 2 件を新しい要素「設計」（構成要素）にまとめた");
    expect(calls.filter((c) => c.method !== "GET").map((c) => c.body)).toEqual([
      { parent_id: 100, kind: "COMPONENT", name: "設計" },
      { parent_id: 100, position: 0 },
      { parent_id: 130 },
      { parent_id: 130 },
    ]);
    await settled();

    openMenu(container, "e102");
    choose("新しい上位の要素にまとめる…");
    typeDraft("まとめる先の名前", "設計");
    await waitFor(() => expect(calls.filter((c) => c.method !== "GET")).toHaveLength(7));
    expect(calls.filter((c) => c.method !== "GET").slice(4).map((c) => c.body)).toEqual([
      { parent_id: 100, kind: "COMPONENT", name: "設計" },
      { parent_id: 100, position: 1 },
      { parent_id: 130 },
    ]);
  });

  it("活動と要素を混ぜて選んだらメニューにまとめる操作を出さない", async () => {
    const { container } = await startEditing();
    fireEvent.mouseDown(label(container, "e102"));
    fireEvent.mouseDown(label(container, "a21"), { ctrlKey: true });
    expect(menuNames(openMenu(container, "a21"))).toEqual(["選んだ 2 行を消すDelete"]);
  });

  it("Delete で確かめてから消す（Esc でやめる・確かめの「消す」で消す）：活動を先に 1 件ずつ（API-SCH-005）、次に要素を配下ごと（API-WBS-005）", async () => {
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
    const confirm = screen.getByRole("alertdialog", { name: "行を消すことの確かめ" });
    expect(confirm).toHaveTextContent(
      "要素 1 件（1.1 業務整理。配下の要素ごと）と活動 3 件（1.1 聞き取り、1.1 整理、1.2 画面案）を消す。消した行は戻せない",
    );
    expect(within(confirm).getByRole("button", { name: "消す" })).toHaveFocus();
    fireEvent.keyDown(within(confirm).getByRole("button", { name: "消す" }), { key: "Escape" });
    expect(screen.queryByRole("alertdialog")).toBeNull();
    await waitFor(() => expect(viewport).toHaveFocus());
    expect(calls.filter((c) => c.method === "DELETE")).toHaveLength(0);

    openMenu(container, "a23");
    choose("選んだ 4 行を消す");
    fireEvent.click(within(screen.getByRole("alertdialog", { name: "行を消すことの確かめ" })).getByRole("button", { name: "消す" }));
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
    expect(screen.queryByRole("alertdialog", { name: "行を消すことの確かめ" })).toBeNull();
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
    expect(screen.getByRole("alertdialog", { name: "行を消すことの確かめ" })).toHaveTextContent("活動 1 件（1.1 聞き取り）を消す");
    fireEvent.keyDown(viewport, { key: "ArrowDown" });
    expect(screen.queryByRole("alertdialog", { name: "行を消すことの確かめ" })).toBeNull();
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
    expect(strip()).toHaveAttribute("aria-busy", "true");
    down();
    expect(screen.getByText(WAIT_TEXT)).toBeInTheDocument();
    expect(sent(calls, "PATCH", `${BASE}/activities/21`)).toHaveLength(1);
    // 読み直しの間は入力の行も出さない
    fireEvent.keyDown(viewport, { key: "Enter" });
    expect(screen.queryByRole("textbox", { name: "足す行の名前" })).toBeNull();

    release();
    await settled();
    down();
    await waitFor(() => expect(sent(calls, "PATCH", `${BASE}/activities/21`)).toHaveLength(2));
  });

  it("活動の一覧を読めなければ編集できないことを示す", async () => {
    mockApi(routes(PLANNER, { [`GET ${BASE}/activities`]: () => errorResponse(500, "INTERNAL", "失敗") }));
    renderAt();
    await screen.findByRole("img", { name: "ガントチャート" });
    expect(await screen.findByText(/^活動の一覧を読めないので編集できません（.+）。画面を開き直してください$/)).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "ガントの編集" })).toBeNull();
  });

  it("schedule.edit だけの人：要素の行のメニューに要素の操作を出さず、バーは動かせる。図の空いた所のメニューは操作が無いことを示す", async () => {
    const { container } = await startEditing(["project.view", "schedule.edit"]);
    expect(container.querySelector('[data-handle="move"]')).not.toBeNull();
    expect(menuNames(openMenu(container, "e100"))).toEqual(["WBS 辞書・担当・見積を開く"]);
    fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" });
    // WP の行には活動を足せる
    expect(menuNames(openMenu(container, "e101"))).toEqual(["活動を足すEnter", "WBS 辞書・担当・見積を開く"]);
    fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" });
    // 最上位に要素を足すのは wbs.edit。目盛りの帯（空いた所）の右クリックでは行える項目が無い
    fireEvent.contextMenu(screen.getByTestId("gantt-viewport"), { clientX: 300, clientY: HEADER_HEIGHT / 2 });
    expect(menuNames(screen.getByRole("menu", { name: "図の操作" }))).toEqual(["ここで行える操作の権限がありません"]);
    // 行える項目が無くてもメニューが的を受け、Esc で閉じる（キーが図へ行かない）
    expect(screen.getByRole("menu")).toHaveFocus();
    fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
    expect(screen.getByTestId("gantt-viewport")).toHaveFocus();
  });

  it("wbs.edit だけの人にはバーの掴みを出さず、活動の行のメニューは詳細と進捗を開くことだけを出す", async () => {
    const { container } = await startEditing(["project.view", "wbs.edit"]);
    expect(container.querySelector('[data-handle="move"]')).toBeNull();
    expect(menuNames(openMenu(container, "e100"))).toContain("インデントAlt+Shift+→");
    fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" });
    // 活動の行で行えるのは詳細と進捗を開くことだけ（CR-027）
    expect(menuNames(openMenu(container, "a21"))).toEqual(["詳細と進捗…"]);
    expect(within(screen.getByRole("menu")).getByRole("menuitem", { name: "詳細と進捗…" })).toHaveFocus();
    fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
    expect(container.querySelector('[data-layer="edit-rows"] [data-row-key="a21"]')?.getAttribute("data-selected")).toBe("true");
  });

  it("基準化の後は、要素を変えると作成中に戻ることを示す", async () => {
    await startEditing(PLANNER, { [`GET ${BASE}/wbs`]: () => jsonResponse(200, makeTree(sampleElements(), { wbs_status: "BASELINED" })) });
    expect(screen.getByText("WBS は基準化済み。要素を変えると作成中に戻る（5.10）")).toBeInTheDocument();
  });

  it("空の計画でも編集では描き、Enter・図の右クリックで最上位の要素を足せる", async () => {
    const empty = makeGantt({ range_start: null, range_end: null, rows: [], links: [] });
    const { calls } = mockApi(
      routes(PLANNER, {
        [`GET ${BASE}/gantt`]: () => jsonResponse(200, empty),
        [`GET ${BASE}/wbs`]: () => jsonResponse(200, makeTree([])),
        [`GET ${BASE}/activities`]: () => jsonResponse(200, activityList([])),
        [`POST ${BASE}/wbs/elements`]: () => jsonResponse(201, makeElement({ id: 140, parent_id: null })),
      }),
    );
    const { container } = renderAt();
    await screen.findByRole("region", { name: "ガントの編集" });
    await waitFor(() => expect(container.querySelector('[data-layer="edit-rows"]')).not.toBeNull());
    expect(container.querySelector('svg[aria-label="ガントチャート"]')?.getAttribute("data-row-count")).toBe("0");
    expect(screen.getByText("WBS の要素がまだ無い。図の上で Enter を押す（または右クリックする）と最上位の要素を足す")).toBeInTheDocument();

    fireEvent.contextMenu(container.querySelector('[data-layer="header"] rect')!, { clientX: 100, clientY: 20 });
    expect(menuNames(screen.getByRole("menu", { name: "図の操作" }))).toEqual(["最上位に要素を足すEnter"]);
    choose("最上位に要素を足す");
    expect(container.querySelector('svg[aria-label="ガントチャート"]')?.getAttribute("data-row-count")).toBe("1");
    typeDraft("足す行の名前", "要件定義");
    await waitFor(() => expect(sent(calls, "POST", `${BASE}/wbs/elements`)).toHaveLength(1));
    expect(sent(calls, "POST", `${BASE}/wbs/elements`)[0]?.body).toEqual({ parent_id: null, kind: "WORK_PACKAGE", name: "要件定義" });
  });

  it("バー・掴み・行の空いた所を右クリックしても、その行のメニューを出す（縦の位置で行を決める）", async () => {
    const { container } = await startEditing();
    fireEvent.mouseDown(label(container, "a21"));
    fireEvent.contextMenu(container.querySelector('[data-handle="move"][data-activity-id="22"]')!, {
      clientX: barX(container, 22),
      clientY: rowY(container, "a22"),
    });
    expect(screen.getByRole("menu")).toHaveAccessibleName("1.1 整理 の操作");
    expect(container.querySelector('[data-layer="edit-rows"] [data-row-key="a22"]')?.getAttribute("data-selected")).toBe("true");
    expect(container.querySelector('[data-layer="edit-rows"] [data-row-key="a21"]')?.getAttribute("data-selected")).toBeNull();
    fireEvent.keyDown(document.activeElement!, { key: "Escape" });

    // 行の右の空いた所（描画域の上）・要素の行の要約のバー
    fireEvent.contextMenu(screen.getByTestId("gantt-viewport"), { clientX: barX(container, 22) + 600, clientY: rowY(container, "a23") });
    expect(screen.getByRole("menu")).toHaveAccessibleName("1.2 画面案 の操作");
    fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    fireEvent.contextMenu(container.querySelector('[data-layer="planned"] [data-element-id="102"]')!, {
      clientX: barX(container, 23),
      clientY: rowY(container, "e102"),
    });
    expect(screen.getByRole("menu")).toHaveAccessibleName("1.2 画面設計 の操作");
    fireEvent.keyDown(document.activeElement!, { key: "Escape" });

    // 目盛りの帯・行の無い所は空いた所のメニュー
    fireEvent.contextMenu(screen.getByTestId("gantt-viewport"), { clientX: 300, clientY: HEADER_HEIGHT / 2 });
    expect(screen.getByRole("menu")).toHaveAccessibleName("図の操作");
  });

  it("行の中の入力の上の右クリックはブラウザに任せる（打ちかけの名前で足さない・メニューを出さない）", async () => {
    const { container, calls, viewport } = await startEditing();
    fireEvent.mouseDown(label(container, "a21"));
    fireEvent.keyDown(viewport, { key: "Enter" });
    const input = screen.getByRole("textbox", { name: "足す行の名前" });
    fireEvent.change(input, { target: { value: "ab" } });
    const notPrevented = fireEvent.contextMenu(input, { clientX: 100, clientY: rowY(container, "") });
    expect(notPrevented).toBe(true);
    expect(screen.queryByRole("menu")).toBeNull();
    expect(input).toHaveFocus();
    expect(calls.filter((c) => c.method !== "GET")).toHaveLength(0);
  });

  it("別のウィンドウへ移った（的が入力に残る）だけなら、入力を確かめない", async () => {
    const { container, calls, viewport } = await startEditing();
    fireEvent.mouseDown(label(container, "a21"));
    fireEvent.keyDown(viewport, { key: "Enter" });
    const input = screen.getByRole("textbox", { name: "足す行の名前" });
    fireEvent.change(input, { target: { value: "議事" } });
    fireEvent.blur(input);
    expect(screen.getByRole("textbox", { name: "足す行の名前" })).toHaveValue("議事");
    expect(calls.filter((c) => c.method !== "GET")).toHaveLength(0);
  });

  it("後続を選んでいる間は Delete・F2・Alt+Shift+矢印を受けない。メニューを開けば選ぶのをやめて案内を消す", async () => {
    const { container, calls, viewport } = await startEditing();
    openMenu(container, "a21");
    choose("後続をつなぐ…");
    const hint = "1.1 聞き取り の後続にする活動の行を選ぶ（クリック、または矢印で選んで Enter。Esc でやめる）";
    expect(screen.getByText(hint)).toBeInTheDocument();
    fireEvent.keyDown(viewport, { key: "Delete" });
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(screen.getByText("行を選んでいる間はほかの操作をしません（Enter で決める・Esc でやめる）")).toBeInTheDocument();
    fireEvent.keyDown(viewport, { key: "ArrowDown", altKey: true, shiftKey: true });
    fireEvent.keyDown(viewport, { key: "F2" });
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(label(container, "a21").getAttribute("data-picking")).toBe("source");
    expect(calls.filter((c) => c.method !== "GET")).toHaveLength(0);

    openMenu(container, "a23");
    expect(label(container, "a21").getAttribute("data-picking")).toBeNull();
    expect(screen.queryByText(hint)).toBeNull();
    expect(screen.queryByText(/行を選んでいる間は/)).toBeNull();
  });

  it("縦に別の行へ半行より大きく引いたときだけ依存の線（元の行の中・半行に満たない移動は日付の移動。7.3）", async () => {
    const { container, calls } = await startEditing(PLANNER, {
      [`PATCH ${BASE}/activities/22`]: () => jsonResponse(200, makeActivity({ id: 22, requested_start: "2026-12-09", start_date: "2026-12-09" })),
    });
    const x0 = barX(container, 22);
    const top = rowY(container, "a22") - ROW_HEIGHT / 2;
    fireEvent.mouseDown(container.querySelector('[data-handle="move"][data-activity-id="22"]')!, { clientX: x0, clientY: top + 5 });
    fireEvent.mouseMove(window, { clientX: x0 + 48, clientY: top + ROW_HEIGHT - 3 });
    expect(container.querySelector('[data-ghost="link"]')).toBeNull();
    expect(container.querySelector('[data-ghost="move"]')).toHaveTextContent("2026-12-09");
    fireEvent.mouseUp(window, { clientX: x0 + 48, clientY: top + ROW_HEIGHT - 3 });
    await waitFor(() => expect(sent(calls, "PATCH", `${BASE}/activities/22`)).toHaveLength(1));
    expect(sent(calls, "PATCH", `${BASE}/activities/22`)[0]?.body).toEqual({ requested_start: "2026-12-09" });
    await settled();

    // 行の下端で掴み、下の行へ少しだけ（半行に満たず）はみ出しても日付の移動
    fireEvent.mouseDown(container.querySelector('[data-handle="move"][data-activity-id="22"]')!, { clientX: x0, clientY: top + ROW_HEIGHT - 2 });
    fireEvent.mouseMove(window, { clientX: x0 + 48, clientY: top + ROW_HEIGHT + 4 });
    expect(container.querySelector('[data-ghost="link"]')).toBeNull();
    fireEvent.mouseUp(window, { clientX: x0 + 48, clientY: top + ROW_HEIGHT + 4 });
    await waitFor(() => expect(sent(calls, "PATCH", `${BASE}/activities/22`)).toHaveLength(2));
    expect(sent(calls, "POST", `${BASE}/dependencies`)).toHaveLength(0);
  });

  it("メニューは図を送ると閉じる。キーボードで開いた直後に来る右クリックの事象では開き直さない", async () => {
    const { container, viewport } = await startEditing();
    openMenu(container, "a21");
    fireEvent.scroll(viewport);
    expect(screen.queryByRole("menu")).toBeNull();

    fireEvent.mouseDown(label(container, "a22"));
    fireEvent.keyDown(viewport, { key: "ContextMenu" });
    expect(screen.getByRole("menu")).toHaveAccessibleName("1.1 整理 の操作");
    fireEvent.contextMenu(viewport, { clientX: 0, clientY: 0 });
    expect(screen.getByRole("menu")).toHaveAccessibleName("1.1 整理 の操作");
  });

  it("WBS を読めなければ編集できないことを示す", async () => {
    mockApi(routes(PLANNER, { [`GET ${BASE}/wbs`]: () => errorResponse(500, "INTERNAL", "失敗") }));
    renderAt();
    await screen.findByRole("img", { name: "ガントチャート" });
    expect(await screen.findByText("WBS を読めないので編集できません。画面を開き直してください")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "ガントの編集" })).toBeNull();
  });

  it("活動の行の見出し・バーのダブルクリックは詳細と進捗を開き（名前の入力にしない）、名前・期間を保存して開いたまま読み直す（API-SCH-003。CR-027）", async () => {
    let current = ACTIVITIES;
    const { container, calls, viewport } = await startEditing(PLANNER, {
      [`GET ${BASE}/members`]: () => jsonResponse(200, { project_id: 3, items: [makeMember({ user_id: 5 })], total: 1, limit: 1000, offset: 0 }),
      [`GET ${BASE}/activities`]: () => jsonResponse(200, activityList(current, { dependencies: DEPENDENCIES })),
      [`PATCH ${BASE}/activities/22`]: () => {
        const saved = { ...ACTIVITIES[1]!, name: "要件の整理", duration_days: 4, start_date: "2026-12-08", finish_date: "2026-12-11" };
        current = [ACTIVITIES[0]!, saved, ...ACTIVITIES.slice(2)];
        return jsonResponse(200, saved);
      },
    });
    fireEvent.doubleClick(label(container, "a22"));
    const dialog = screen.getByRole("dialog", { name: "活動の詳細と進捗：1.1 整理" });
    expect(within(viewport).queryAllByRole("textbox")).toHaveLength(0);
    // 編集の部品が読んだ活動の一覧を使う（読み直さない）
    expect(sent(calls, "GET", `${BASE}/activities`)).toHaveLength(1);
    const form = within(dialog).getByRole("form", { name: "活動の詳細" });
    expect(within(form).getByLabelText("名前")).toHaveFocus();
    expect(within(form).getByLabelText("希望開始日")).toHaveValue("2026-12-07");
    expect(within(form).getByRole("button", { name: "詳細を保存する" })).toBeDisabled();
    // 報告日が無ければ、表示の設定で選ぶよう示す
    expect(within(dialog).getByText("報告日を選んでいない。表示の設定で報告日を選ぶ（作る）と、ここで進捗を書ける")).toBeInTheDocument();

    fireEvent.change(within(form).getByLabelText("期間（稼働日）"), { target: { value: "0" } });
    fireEvent.click(within(form).getByRole("button", { name: "詳細を保存する" }));
    expect(within(form).getByRole("alert")).toHaveTextContent("期間は 1 以上 100000 以下の整数（稼働日）で入力してください");
    fireEvent.change(within(form).getByLabelText("名前"), { target: { value: "要件の整理" } });
    fireEvent.change(within(form).getByLabelText("期間（稼働日）"), { target: { value: "4" } });
    fireEvent.click(within(form).getByRole("button", { name: "詳細を保存する" }));

    await waitFor(() => expect(sent(calls, "PATCH", `${BASE}/activities/22`)).toHaveLength(1));
    expect(sent(calls, "PATCH", `${BASE}/activities/22`)[0]?.body).toEqual({ name: "要件の整理", duration_days: 4 });
    expect(await within(dialog).findByText(/^詳細を保存した（予定 2026-12-08〜2026-12-11）。計算した開始は 2026-12-08/)).toBeInTheDocument();
    await waitFor(() => expect(sent(calls, "GET", `${BASE}/activities`)).toHaveLength(2));
    await waitFor(() => expect(sent(calls, "GET", `${BASE}/gantt`).length).toBeGreaterThanOrEqual(2));
    // 開いたまま、読み直した値を出す。作り直した入力欄に的があった所が消えても、的はダイアログに残る（Esc を受ける）
    await waitFor(() => expect(dialog).toHaveAccessibleName("活動の詳細と進捗：1.1 要件の整理"));
    const reloaded = within(dialog).getByRole("form", { name: "活動の詳細" });
    expect(within(reloaded).getByLabelText("名前")).toHaveValue("要件の整理");
    expect(within(reloaded).getByLabelText("期間（稼働日）")).toHaveValue("4");
    expect(within(reloaded).getByRole("button", { name: "詳細を保存する" })).toBeDisabled();
    expect(within(dialog).getByText("2026-12-08〜2026-12-11")).toBeInTheDocument();
    await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true));
    fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();

    // バー（日付を動かす掴み）のダブルクリックでも開く
    fireEvent.doubleClick(container.querySelector('[data-handle="move"][data-activity-id="21"]')!);
    expect(screen.getByRole("dialog", { name: "活動の詳細と進捗：1.1 聞き取り" })).toBeInTheDocument();
  });

  it("バーのダブルクリックのぶれでは日付を動かさない：2 回目の押下ではドラッグを始めず、横の動きが小さければ縦にぶれても動かさない（CR-027）", async () => {
    const { container, calls } = await startEditing();
    // 月の表示（1 日 = 2px）では、3px 未満の横のぶれでも以前は日付が動いた
    const settings = within(screen.getByRole("navigation", { name: "行き先" })).getByRole("button", { name: "表示の設定" });
    fireEvent.click(settings);
    fireEvent.change(within(screen.getByRole("dialog", { name: "表示の設定" })).getByLabelText("表示の単位"), { target: { value: "month" } });
    fireEvent.click(within(screen.getByRole("dialog", { name: "表示の設定" })).getByRole("button", { name: "閉じる" }));
    await waitFor(() => expect(container.querySelector("p.gantt-legend")).toHaveTextContent(/^単位：月／/));
    const grip = () => container.querySelector('[data-handle="move"][data-activity-id="21"]')!;
    const y = rowY(container, "a21");

    // 1 回目：横に 2px・縦に 4px ぶれて離す → 選ぶだけ
    fireEvent.mouseDown(grip(), { clientX: 400, clientY: y, detail: 1 });
    fireEvent.mouseMove(window, { clientX: 402, clientY: y + 4 });
    fireEvent.mouseUp(window, { clientX: 402, clientY: y + 4 });
    // 2 回目の押下：大きく動いてもドラッグにしない
    fireEvent.mouseDown(grip(), { clientX: 400, clientY: y, detail: 2 });
    fireEvent.mouseMove(window, { clientX: 430, clientY: y });
    expect(container.querySelector("[data-ghost]")).toBeNull();
    fireEvent.mouseUp(window, { clientX: 430, clientY: y });
    fireEvent.doubleClick(grip());
    expect(screen.getByRole("dialog", { name: "活動の詳細と進捗：1.1 聞き取り" })).toBeInTheDocument();
    expect(calls.filter((c) => c.method !== "GET")).toHaveLength(0);
  });

  it("マイルストーンの詳細には期間の欄を出さず、期間を送らない（BR-04）", async () => {
    const milestone = makeActivity({ id: 23, name: "画面案", work_package_id: 102, wbs_code: "1.2", sort_order: 1, is_milestone: true, duration_days: 0 });
    let current = [ACTIVITIES[0]!, ACTIVITIES[1]!, milestone];
    // 保存の後の読み直しは、こちらが離すまで返さない
    let release: () => void = () => undefined;
    let held: Promise<void> | null = null;
    const { container, calls } = await startEditing(PLANNER, {
      [`GET ${BASE}/members`]: () => jsonResponse(200, { project_id: 3, items: [], total: 0, limit: 1000, offset: 0 }),
      [`GET ${BASE}/activities`]: async () => {
        if (held !== null) {
          await held;
        }
        return jsonResponse(200, activityList(current, { dependencies: DEPENDENCIES }));
      },
      [`PATCH ${BASE}/activities/23`]: () => {
        held = new Promise((resolve) => {
          release = resolve;
        });
        current = [ACTIVITIES[0]!, ACTIVITIES[1]!, { ...milestone, name: "画面案の承認" }];
        return jsonResponse(200, current[2]);
      },
    });
    fireEvent.doubleClick(label(container, "a23"));
    const form = within(screen.getByRole("dialog", { name: "活動の詳細と進捗：1.2 画面案" })).getByRole("form", { name: "活動の詳細" });
    expect(within(form).queryByLabelText("期間（稼働日）")).toBeNull();
    fireEvent.change(within(form).getByLabelText("名前"), { target: { value: "画面案の承認" } });
    fireEvent.click(within(form).getByRole("button", { name: "詳細を保存する" }));
    await waitFor(() => expect(sent(calls, "PATCH", `${BASE}/activities/23`)).toHaveLength(1));
    expect(sent(calls, "PATCH", `${BASE}/activities/23`)[0]?.body).toEqual({ name: "画面案の承認" });
    // 読み直すまでは同じ変更を二重に送れない
    await waitFor(() => expect(screen.getByText(/^詳細を保存した/)).toBeInTheDocument());
    expect(within(form).getByRole("button", { name: "詳細を保存する" })).toBeDisabled();
    fireEvent.submit(form);
    expect(sent(calls, "PATCH", `${BASE}/activities/23`)).toHaveLength(1);
    release();
    await waitFor(() => expect(screen.getByRole("dialog")).toHaveAccessibleName("活動の詳細と進捗：1.2 画面案の承認"));
    expect(sent(calls, "PATCH", `${BASE}/activities/23`)).toHaveLength(1);
  });

  it("メニューの「詳細と進捗…」で開き、的はダイアログが受ける。担当と割当工数を保存し（API-EFF-001）、閉じると図へ的を戻す", async () => {
    const { container, calls, viewport } = await startEditing(PLANNER, {
      [`GET ${BASE}/members`]: () =>
        jsonResponse(200, {
          project_id: 3,
          items: [makeMember({ user_id: 5, display_name: "計画 太郎" }), makeMember({ user_id: 6, display_name: "退任 次郎", is_active: false })],
          total: 2,
          limit: 1000,
          offset: 0,
        }),
      [`PUT ${BASE}/activities/21/assignments`]: () =>
        jsonResponse(200, { activity_id: 21, estimate_hours: 40, planned_hours_total: 16, difference_hours: -24, assignments: [] }),
    });
    fireEvent.mouseDown(label(container, "a21"));
    openMenu(container, "a21");
    choose("詳細と進捗…");
    const dialog = screen.getByRole("dialog", { name: "活動の詳細と進捗：1.1 聞き取り" });
    // メニューを閉じても、的を図へ戻してダイアログから奪わない
    await waitFor(() => expect(within(dialog).getByLabelText("名前")).toHaveFocus());

    const assignment = await within(dialog).findByRole("form", { name: "聞き取り の担当と割当工数" });
    fireEvent.click(within(assignment).getByRole("button", { name: "担当を足す" }));
    // 有効なメンバーだけを選べる
    expect(within(within(assignment).getByLabelText("1 行目の担当者")).queryByRole("option", { name: "退任 次郎" })).toBeNull();
    fireEvent.change(within(assignment).getByLabelText("1 行目の担当者"), { target: { value: "5" } });
    fireEvent.change(within(assignment).getByLabelText("1 行目の割当工数"), { target: { value: "16" } });
    fireEvent.click(within(assignment).getByRole("button", { name: "担当を保存する" }));
    await waitFor(() => expect(sent(calls, "PUT", `${BASE}/activities/21/assignments`)).toHaveLength(1));
    expect(sent(calls, "PUT", `${BASE}/activities/21/assignments`)[0]?.body).toEqual({ assignments: [{ user_id: 5, planned_hours: 16 }] });
    expect(await within(dialog).findByText("担当と割当工数を保存した")).toBeInTheDocument();
    await waitFor(() => expect(sent(calls, "GET", `${BASE}/activities`)).toHaveLength(2));

    fireEvent.keyDown(within(dialog).getByLabelText("名前"), { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    await waitFor(() => expect(viewport).toHaveFocus());
  });
});
