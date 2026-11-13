import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { TimeEntry } from "../../api/eff";
import { setAuthToken } from "../../api/client";
import { makeAssignment, makeTimeEntry, page } from "../../test/effFixtures";
import { makeMe, makeProject, makeUser } from "../../test/fixtures";
import { errorResponse, jsonResponse, mockApi, type RouteHandler } from "../../test/mockApi";
import { mondayOf, parseHoursCents, resolveWeek, weekDays } from "./labels";
import TimesheetPage, { buildRows } from "./TimesheetPage";

// 実行担当者（利用者 7）。P-001（3）と P-002（5）で effort.enter_own を持ち、P-009（9）では読むだけ
const ME = makeMe({
  user: makeUser({ id: 7, login_id: "doer", display_name: "実行 花子", is_system_admin: false }),
  system_permissions: [],
  projects: [
    { project_id: 3, roles: ["MEMBER"], permissions: ["project.view", "effort.enter_own"], view_level: "ALL" },
    { project_id: 5, roles: ["MEMBER"], permissions: ["project.view", "effort.enter_own"], view_level: "ALL" },
    { project_id: 9, roles: ["VIEWER"], permissions: ["project.view"], view_level: "ALL" },
  ],
});

const PROJECTS = page([makeProject(), makeProject({ id: 5, code: "P-002", name: "販売管理" })]);
const ASSIGN_3 = page([
  makeAssignment(),
  makeAssignment({ activity_id: 22, activity_name: "レビュー", wbs_code: "1.1.2", planned_hours: 8 }),
]);
const ASSIGN_5 = page([makeAssignment({ activity_id: 51, activity_name: "設計", wbs_code: "2.1", planned_hours: null })]);

// 2026-11-09（月）〜 11-15（日）の自分の実績
const WEEK_ENTRIES = [
  makeTimeEntry({ id: 901, activity_id: 21, work_date: "2026-11-09", hours: 7.5, note: "下書き" }),
  makeTimeEntry({ id: 902, project_id: 5, activity_id: 51, activity_name: "設計", wbs_code: "2.1", work_date: "2026-11-09", hours: 0.5 }),
  makeTimeEntry({ id: 903, activity_id: 22, activity_name: "レビュー", wbs_code: "1.1.2", work_date: "2026-11-10", hours: 8 }),
];

function routes(overrides: Record<string, RouteHandler> = {}): Record<string, RouteHandler> {
  return {
    "GET /api/v1/me": () => jsonResponse(200, ME),
    "GET /api/v1/projects": () => jsonResponse(200, PROJECTS),
    "GET /api/v1/projects/3/assignments": () => jsonResponse(200, ASSIGN_3),
    "GET /api/v1/projects/5/assignments": () => jsonResponse(200, ASSIGN_5),
    "GET /api/v1/time-entries": () => jsonResponse(200, page(WEEK_ENTRIES)),
    ...overrides,
  };
}

function renderAt(path = "/timesheet?week=2026-11-09") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/timesheet" element={<TimesheetPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

function cell(label: string): HTMLInputElement {
  return screen.getByLabelText(label);
}

function dayTotal(day: string): HTMLElement {
  const footer = screen.getByRole("row", { name: /1 日の合計/ });
  const found = footer.querySelector(`[data-day="${day}"]`);
  if (!(found instanceof HTMLElement)) {
    throw new Error(`合計のマスが無い: ${day}`);
  }
  return found;
}

describe("UI-EFF-001 工数入力（週のタイムシート）", () => {
  beforeEach(() => {
    setAuthToken("t-doer");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
    setAuthToken(null);
  });

  it("自分の割当の活動を行に、月曜〜日曜を列にして、今の実績と 1 日・週の合計を出す（EFF-02）", async () => {
    const { calls } = mockApi(routes());
    const { container } = renderAt();

    const table = await screen.findByRole("table", { name: "週のタイムシート" });
    expect(container.querySelector('[data-screen-id="UI-EFF-001"]')).not.toBeNull();
    // effort.enter_own を持つプロジェクトだけの、自分の割当を読む。実績は自分の行をその週の範囲で読む
    expect(calls.map((c) => c.url)).toEqual([
      "/api/v1/me",
      "/api/v1/projects?limit=1000&offset=0",
      "/api/v1/projects/3/assignments?user_id=7&limit=1000&offset=0",
      "/api/v1/projects/5/assignments?user_id=7&limit=1000&offset=0",
      "/api/v1/time-entries?user_id=7&from=2026-11-09&to=2026-11-15&limit=1000&offset=0",
    ]);

    const headers = within(table).getAllByRole("columnheader").map((h) => h.textContent);
    expect(headers).toEqual([
      "プロジェクト",
      "WBS ID",
      "活動",
      "2026-11-09（月）",
      "2026-11-10（火）",
      "2026-11-11（水）",
      "2026-11-12（木）",
      "2026-11-13（金）",
      "2026-11-14（土）",
      "2026-11-15（日）",
      "週の計",
    ]);
    const rows = within(table).getAllByRole("row").slice(1, -1);
    expect(rows.map((r) => r.getAttribute("data-activity-id"))).toEqual(["21", "22", "51"]);
    expect(rows[0]).toHaveTextContent("P-001 基幹更改");
    expect(rows[2]).toHaveTextContent("P-002 販売管理");

    expect(cell("1.1.1 資料作成 2026-11-09").value).toBe("7.5");
    expect(cell("2.1 設計 2026-11-09").value).toBe("0.5");
    expect(cell("1.1.2 レビュー 2026-11-10").value).toBe("8");
    expect(cell("1.1.1 資料作成 2026-11-10").value).toBe("");
    expect(dayTotal("2026-11-09")).toHaveTextContent("8");
    expect(dayTotal("2026-11-10")).toHaveTextContent("8");
    expect(dayTotal("2026-11-11")).toHaveTextContent("0");
    expect(screen.getByRole("row", { name: /1 日の合計/ })).toHaveTextContent(/16$/);
    expect(screen.getByRole("button", { name: "保存する" })).toBeDisabled();
  });

  it("変えたマスだけをまとめて送り（メモは残す・0 は行を消す）、応答でその日の行を置き換える（API-EFF-003）", async () => {
    const written: TimeEntry[] = [
      makeTimeEntry({ id: 901, activity_id: 21, work_date: "2026-11-09", hours: 6, note: "下書き" }),
      makeTimeEntry({ id: 902, project_id: 5, activity_id: 51, activity_name: "設計", wbs_code: "2.1", work_date: "2026-11-09", hours: 0.5 }),
      makeTimeEntry({ id: 904, activity_id: 21, work_date: "2026-11-12", hours: 3.25 }),
    ];
    const { calls } = mockApi(
      routes({
        "PUT /api/v1/time-entries": () =>
          jsonResponse(200, {
            user_id: 7,
            changed: 3,
            entries: written,
            day_totals: [
              { work_date: "2026-11-09", hours: 6.5 },
              { work_date: "2026-11-10", hours: 0 },
              { work_date: "2026-11-12", hours: 3.25 },
            ],
          }),
      }),
    );
    renderAt();
    await screen.findByRole("table", { name: "週のタイムシート" });

    fireEvent.change(cell("1.1.1 資料作成 2026-11-09"), { target: { value: "6" } });
    fireEvent.change(cell("1.1.2 レビュー 2026-11-10"), { target: { value: "" } });
    fireEvent.change(cell("1.1.1 資料作成 2026-11-12"), { target: { value: " 3.25 " } });
    // 同じ値に戻したマスは送らない
    fireEvent.change(cell("2.1 設計 2026-11-09"), { target: { value: "0.50" } });
    expect(dayTotal("2026-11-09")).toHaveTextContent("6.5");
    expect(dayTotal("2026-11-10")).toHaveTextContent("0");
    expect(screen.getByText(/保存していない入力があります/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "次の週" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "保存する" }));

    expect(await screen.findByRole("status")).toHaveTextContent("保存しました（3 件）");
    const put = calls.filter((c) => c.method === "PUT");
    expect(put).toHaveLength(1);
    expect(put[0]?.path).toBe("/api/v1/time-entries");
    expect(put[0]?.body).toEqual({
      entries: [
        { activity_id: 21, work_date: "2026-11-09", hours: 6, note: "下書き" },
        { activity_id: 21, work_date: "2026-11-12", hours: 3.25, note: null },
        { activity_id: 22, work_date: "2026-11-10", hours: 0, note: null },
      ],
    });
    expect(cell("1.1.1 資料作成 2026-11-09").value).toBe("6");
    expect(cell("1.1.2 レビュー 2026-11-10").value).toBe("");
    expect(cell("1.1.1 資料作成 2026-11-12").value).toBe("3.25");
    expect(screen.queryByText(/保存していない入力があります/)).toBeNull();
    expect(screen.getByRole("button", { name: "次の週" })).toBeEnabled();
  });

  it("1 日の合計が 24 時間を超える日は示して送らない。ちょうど 24 は通す（EFF-02・RULE-EFF-005）", async () => {
    const { calls } = mockApi(
      routes({
        "PUT /api/v1/time-entries": () => jsonResponse(200, { user_id: 7, changed: 1, entries: [], day_totals: [] }),
      }),
    );
    renderAt();
    await screen.findByRole("table", { name: "週のタイムシート" });

    // 11-09 は 7.5 + 0.5 = 8。16 を足すとちょうど 24（超えていない）
    fireEvent.change(cell("1.1.2 レビュー 2026-11-09"), { target: { value: "16" } });
    expect(dayTotal("2026-11-09")).toHaveTextContent("24");
    expect(dayTotal("2026-11-09")).not.toHaveTextContent("時間超");

    // 16.01 で 24.01（超える）
    fireEvent.change(cell("1.1.2 レビュー 2026-11-09"), { target: { value: "16.01" } });
    expect(dayTotal("2026-11-09")).toHaveTextContent("24.01（24 時間超）");
    fireEvent.click(screen.getByRole("button", { name: "保存する" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("1 日の合計が 24 時間を超えています");
    expect(alert).toHaveTextContent("2026-11-09：24.01 時間");
    expect(calls.filter((c) => c.method === "PUT")).toHaveLength(0);

    fireEvent.change(cell("1.1.2 レビュー 2026-11-09"), { target: { value: "16" } });
    fireEvent.click(screen.getByRole("button", { name: "保存する" }));
    expect(await screen.findByRole("status")).toHaveTextContent("保存しました（1 件）");
    expect(calls.filter((c) => c.method === "PUT")[0]?.body).toEqual({
      entries: [{ activity_id: 22, work_date: "2026-11-09", hours: 16, note: null }],
    });
  });

  it("読めない値（負・小数 3 桁・数でない）は示して送らない", async () => {
    const { calls } = mockApi(routes());
    renderAt();
    await screen.findByRole("table", { name: "週のタイムシート" });

    fireEvent.change(cell("1.1.1 資料作成 2026-11-11"), { target: { value: "-1" } });
    fireEvent.change(cell("2.1 設計 2026-11-13"), { target: { value: "1.234" } });
    expect(cell("1.1.1 資料作成 2026-11-11")).toHaveAttribute("aria-invalid", "true");
    fireEvent.click(screen.getByRole("button", { name: "保存する" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("工数は 0 以上の数（小数 2 桁まで）で入力してください");
    expect(alert).toHaveTextContent("1.1.1 資料作成・2026-11-11：「-1」");
    expect(alert).toHaveTextContent("2.1 設計・2026-11-13：「1.234」");
    expect(calls.filter((c) => c.method === "PUT")).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "入力を取り消す" }));
    expect(cell("1.1.1 資料作成 2026-11-11").value).toBe("");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("サーバが拒んだとき（確定した報告日より前・1 日 24 時間超など）は文言を出し、入力を残す", async () => {
    mockApi(
      routes({
        "PUT /api/v1/time-entries": () =>
          errorResponse(422, "DAY_TOTAL_EXCEEDED", "1 日の合計が 24 時間を超えます（2026-11-11）"),
      }),
    );
    renderAt();
    await screen.findByRole("table", { name: "週のタイムシート" });

    fireEvent.change(cell("1.1.1 資料作成 2026-11-11"), { target: { value: "4" } });
    fireEvent.click(screen.getByRole("button", { name: "保存する" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("1 日の合計が 24 時間を超えます（2026-11-11）");
    expect(cell("1.1.1 資料作成 2026-11-11").value).toBe("4");
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("週を移ると、その週の実績を読み直す", async () => {
    const { calls } = mockApi(routes());
    renderAt();
    await screen.findByRole("table", { name: "週のタイムシート" });

    fireEvent.click(screen.getByRole("button", { name: "次の週" }));
    expect(await screen.findByText("2026-11-16 〜 2026-11-22")).toBeInTheDocument();
    await screen.findByLabelText("1.1.1 資料作成 2026-11-16");
    fireEvent.click(screen.getByRole("button", { name: "前の週" }));
    await screen.findByLabelText("1.1.1 資料作成 2026-11-09");

    const reads = calls.filter((c) => c.path === "/api/v1/time-entries").map((c) => c.url);
    expect(reads).toEqual([
      "/api/v1/time-entries?user_id=7&from=2026-11-09&to=2026-11-15&limit=1000&offset=0",
      "/api/v1/time-entries?user_id=7&from=2026-11-16&to=2026-11-22&limit=1000&offset=0",
      "/api/v1/time-entries?user_id=7&from=2026-11-09&to=2026-11-15&limit=1000&offset=0",
    ]);
  });

  it("問い合わせの week が無ければ今日を含む週を出す（週は月曜始まり）", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(2026, 10, 12, 10, 0, 0));
    const { calls } = mockApi(routes());
    renderAt("/timesheet");

    await screen.findByRole("table", { name: "週のタイムシート" });
    expect(screen.getByText("2026-11-09 〜 2026-11-15")).toBeInTheDocument();
    expect(calls.at(-1)?.url).toBe("/api/v1/time-entries?user_id=7&from=2026-11-09&to=2026-11-15&limit=1000&offset=0");
  });

  it("割当が外れた活動の実績は読むだけの行として出す", async () => {
    mockApi(
      routes({
        "GET /api/v1/time-entries": () =>
          jsonResponse(
            200,
            page([
              ...WEEK_ENTRIES,
              makeTimeEntry({ id: 905, activity_id: 30, activity_name: "旧作業", wbs_code: "1.3", work_date: "2026-11-11", hours: 2 }),
            ]),
          ),
      }),
    );
    renderAt();
    const table = await screen.findByRole("table", { name: "週のタイムシート" });

    const rows = within(table).getAllByRole("row").slice(1, -1);
    // 割当が外れた活動はそのプロジェクト（P-001）の末尾
    expect(rows.map((r) => r.getAttribute("data-activity-id"))).toEqual(["21", "22", "30", "51"]);
    expect(rows[2]).toHaveTextContent("割当が外れた活動。読むだけ");
    expect(cell("1.3 旧作業 2026-11-11")).toBeDisabled();
    expect(cell("1.3 旧作業 2026-11-11").value).toBe("2");
    expect(dayTotal("2026-11-11")).toHaveTextContent("2");
  });

  it("effort.enter_own を持つプロジェクトが無ければ、割当・実績を読まずにその旨を出す", async () => {
    const { calls } = mockApi(
      routes({
        "GET /api/v1/me": () =>
          jsonResponse(
            200,
            makeMe({
              user: makeUser({ id: 8, is_system_admin: false }),
              system_permissions: [],
              projects: [{ project_id: 9, roles: ["VIEWER"], permissions: ["project.view"], view_level: "ALL" }],
            }),
          ),
      }),
    );
    const { container } = renderAt();

    expect(await screen.findByText("工数を入力できるプロジェクトがありません")).toBeInTheDocument();
    expect(container.querySelector('[data-screen-id="UI-EFF-001"]')).not.toBeNull();
    expect(calls.map((c) => c.path)).toEqual(["/api/v1/me"]);
    expect(screen.queryByRole("button", { name: "保存する" })).toBeNull();
  });

  it("割当の活動が無ければその旨を出す", async () => {
    mockApi(
      routes({
        "GET /api/v1/projects/3/assignments": () => jsonResponse(200, page([])),
        "GET /api/v1/projects/5/assignments": () => jsonResponse(200, page([])),
        "GET /api/v1/time-entries": () => jsonResponse(200, page([])),
      }),
    );
    renderAt();

    expect(await screen.findByText("入力できる活動がありません（自分の割当の活動がありません）")).toBeInTheDocument();
    expect(screen.queryByRole("table", { name: "週のタイムシート" })).toBeNull();
  });

  it("読み込みの誤りは文言を出す", async () => {
    mockApi(routes({ "GET /api/v1/time-entries": () => errorResponse(403, "FORBIDDEN", "この操作の権限がありません") }));
    renderAt();

    expect(await screen.findByRole("alert")).toHaveTextContent("この操作の権限がありません");
    expect(screen.queryByRole("table", { name: "週のタイムシート" })).toBeNull();
  });
});

describe("UI-EFF-001 の週と入力の計算", () => {
  it("週は月曜始まり。日曜はその前の月曜の週", () => {
    expect(mondayOf("2026-11-09")).toBe("2026-11-09");
    expect(mondayOf("2026-11-12")).toBe("2026-11-09");
    expect(mondayOf("2026-11-15")).toBe("2026-11-09");
    expect(mondayOf("2026-11-16")).toBe("2026-11-16");
    // 月・年をまたぐ
    expect(mondayOf("2027-01-01")).toBe("2026-12-28");
    expect(weekDays("2026-12-28")).toEqual([
      "2026-12-28",
      "2026-12-29",
      "2026-12-30",
      "2026-12-31",
      "2027-01-01",
      "2027-01-02",
      "2027-01-03",
    ]);
  });

  it("問い合わせの week は読める日付ならその週、読めなければ今日の週", () => {
    expect(resolveWeek("2026-11-12", "2026-12-01")).toBe("2026-11-09");
    expect(resolveWeek(null, "2026-12-01")).toBe("2026-11-30");
    expect(resolveWeek("2026-02-30", "2026-12-01")).toBe("2026-11-30");
    expect(resolveWeek("x", "2026-12-01")).toBe("2026-11-30");
  });

  it("マスの入力：空は 0、0 以上で小数 2 桁まで。それ以外は読めない", () => {
    expect(parseHoursCents("")).toBe(0);
    expect(parseHoursCents("  ")).toBe(0);
    expect(parseHoursCents("0")).toBe(0);
    expect(parseHoursCents("7.5")).toBe(750);
    expect(parseHoursCents("0.25")).toBe(25);
    expect(parseHoursCents("24")).toBe(2400);
    expect(parseHoursCents("-1")).toBeNull();
    expect(parseHoursCents("1.234")).toBeNull();
    expect(parseHoursCents("abc")).toBeNull();
    expect(parseHoursCents("1e2")).toBeNull();
  });

  it("行はプロジェクトの順→割当の順。割当に無い実績の活動はそのプロジェクトの末尾、知らないプロジェクトは最後", () => {
    const rows = buildRows(
      [
        { projectId: 5, items: [makeAssignment({ activity_id: 51, wbs_code: "2.1", activity_name: "設計" })] },
        { projectId: 3, items: [makeAssignment({ activity_id: 21 })] },
      ],
      [
        makeTimeEntry({ project_id: 9, activity_id: 91, wbs_code: "9.1", activity_name: "他" }),
        makeTimeEntry({ project_id: 3, activity_id: 30, wbs_code: "1.3", activity_name: "旧作業" }),
        makeTimeEntry({ project_id: 3, activity_id: 21 }),
      ],
    );
    expect(rows.map((r) => [r.activityId, r.assigned])).toEqual([
      [51, true],
      [21, true],
      [30, false],
      [91, false],
    ]);
  });
});
