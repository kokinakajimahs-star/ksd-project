import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setAuthToken } from "../../api/client";
import type { Me } from "../../api/cmn";
import type { Gantt } from "../../api/sch";
import { DAY_WIDTH, LABEL_WIDTH } from "../../components/gantt/scale";
import { makeMe, makeUser } from "../../test/fixtures";
import { errorResponse, jsonResponse, mockApi, type RecordedCall, type RouteHandler } from "../../test/mockApi";
import {
  makeBar,
  makeGantt,
  makeGanttRow,
  makeLightningLine,
  makeLightningRow,
  makeOverlaidLine,
  makeScheduleBaseline,
  makeStatusReport,
  point,
} from "../../test/schFixtures";
import GanttPage, { latestReport } from "./GanttPage";

const FUTURE = { v7_startTransition: true, v7_relativeSplatPath: true };
const BASE = "/api/v1/projects/3";

function meWith(permissions: string[]): Me {
  return makeMe({
    user: makeUser({ id: 5, is_system_admin: false }),
    system_permissions: [],
    projects: [{ project_id: 3, roles: ["PM"], permissions, view_level: "ALL" }],
  });
}

// 稲妻チャート.txt §6 の例（設計書 5.6 の試験の基準）：全ての曜日を稼働日、原点 12/01、報告日 12/10（L＝10）
const GANTT: Gantt = makeGantt({
  non_working_days: ["2026-12-05", "2026-12-06"],
  rows: [
    makeGanttRow({
      row_index: 0,
      activity_id: 21,
      name: "資料作成",
      is_critical: true,
      total_float: 0,
      baseline: makeBar("BASELINE", "2026-12-01", "2026-12-18"),
      actual: makeBar("ACTUAL", "2026-12-01", "2026-12-10", { open_ended: true }),
      assignees: ["計画 太郎", "実行 花子"],
      percent_complete: 25,
    }),
    makeGanttRow({ row_index: 1, activity_id: 22, name: "関係者調整", percent_complete: 60 }),
    makeGanttRow({
      row_index: 2,
      activity_id: 23,
      name: "公開準備",
      planned: makeBar("PLANNED", "2026-12-12", "2026-12-18"),
    }),
    makeGanttRow({
      row_index: 3,
      activity_id: 24,
      name: "環境整備",
      planned: makeBar("PLANNED", "2026-12-01", "2026-12-08"),
    }),
    makeGanttRow({
      row_index: 4,
      activity_id: 25,
      name: "公開",
      is_milestone: true,
      planned: makeBar("PLANNED", "2026-12-21", "2026-12-21", {
        is_milestone: true,
        start: point("2026-12-21", 1),
        end: point("2026-12-21", 1),
      }),
    }),
  ],
  links: [
    {
      predecessor_id: 24,
      successor_id: 23,
      from_row: 3,
      to_row: 2,
      start: point("2026-12-08", 1),
      end: point("2026-12-12", 0),
    },
  ],
});

const LIGHTNING = makeLightningLine([
  makeLightningRow(),
  makeLightningRow({
    row_index: 1,
    activity_id: 22,
    name: "関係者調整",
    percent_complete: 60,
    point: point("2026-12-13", 0.6, 12.6),
    deviation: 2.6,
    judgement: "AHEAD",
    counted_as: "AHEAD",
    display_date: "2026-12-13",
  }),
  makeLightningRow({
    row_index: 2,
    activity_id: 23,
    name: "公開準備",
    percent_complete: 0,
    point: point("2026-12-10", 1, 10),
    deviation: 0,
    judgement: "NOT_STARTED",
    counted_as: "ON_SCHEDULE",
    display_date: "2026-12-10",
  }),
  makeLightningRow({
    row_index: 3,
    activity_id: 24,
    name: "環境整備",
    percent_complete: 100,
    point: point("2026-12-10", 1, 10),
    deviation: 0,
    judgement: "COMPLETED",
    counted_as: "ON_SCHEDULE",
    display_date: "2026-12-10",
  }),
  makeLightningRow({
    row_index: 4,
    activity_id: 25,
    name: "公開",
    percent_complete: null,
    point: point("2026-12-10", 1, 10),
    deviation: 0,
    judgement: "NOT_COMPUTED",
    counted_as: "NOT_COMPUTED",
    display_date: "2026-12-10",
  }),
]);

const REPORTS = [
  makeStatusReport({ id: 40, report_date: "2026-12-03", status: "FINALIZED", snapshot_id: 7 }),
  makeStatusReport({ id: 41, report_date: "2026-12-10", status: "OPEN" }),
];

function routes(me: Me, overrides: Record<string, RouteHandler> = {}): Record<string, RouteHandler> {
  return {
    "GET /api/v1/me": () => jsonResponse(200, me),
    [`GET ${BASE}/gantt`]: () => jsonResponse(200, GANTT),
    [`GET ${BASE}/schedule-baselines`]: () =>
      jsonResponse(200, {
        items: [makeScheduleBaseline(), makeScheduleBaseline({ id: 62, version: 2, name: "再計画" })],
        total: 2,
        limit: 1000,
        offset: 0,
      }),
    [`GET ${BASE}/status-reports`]: () => jsonResponse(200, { items: REPORTS, total: 2, limit: 1000, offset: 0 }),
    [`GET ${BASE}/status-reports/41/lightning-line`]: () => jsonResponse(200, LIGHTNING),
    [`GET ${BASE}/lightning-lines`]: () =>
      jsonResponse(200, { project_start: "2026-12-01", lines: [makeOverlaidLine()], restricted: false }),
    ...overrides,
  };
}

function renderAt(path = "/projects/3/gantt") {
  return render(
    <MemoryRouter initialEntries={[path]} future={FUTURE}>
      <Routes>
        <Route path="/projects/:projectId/gantt" element={<GanttPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

function sent(calls: RecordedCall[], method: string, path: string) {
  return calls.filter((c) => c.method === method && c.path === path);
}

function layer(container: HTMLElement, name: string): Element {
  const element = container.querySelector(`[data-layer="${name}"]`);
  if (element === null) {
    throw new Error(`レイヤ ${name} が無い`);
  }
  return element;
}

async function chart(container: HTMLElement) {
  await screen.findByRole("img", { name: "ガントチャート" });
  await waitFor(() => expect(layer(container, "lightning").querySelectorAll("circle")).toHaveLength(5));
}

describe("UI-SCH-002 ガントチャート・イナズマ線", () => {
  beforeEach(() => {
    setAuthToken("t-pm");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    setAuthToken(null);
  });

  it("7.3 のレイヤを下から順に描く", async () => {
    mockApi(routes(meWith(["project.view"])));
    const { container } = renderAt();
    await chart(container);

    expect(container.querySelector('[data-screen-id="UI-SCH-002"]')).not.toBeNull();
    const order = Array.from(container.querySelectorAll("svg > g[data-layer]")).map((g) => g.getAttribute("data-layer"));
    expect(order.slice(0, 9)).toEqual([
      "non-working",
      "baseline",
      "planned",
      "actual",
      "bar-labels",
      "links",
      "report-line",
      "lightning",
      "past-lines",
    ]);
  });

  it("非稼働日を灰色の帯にし、予定・実績・基準のバーを同じ行に描く。クリティカルは赤い枠、マイルストーンは菱形", async () => {
    mockApi(routes(meWith(["project.view"])));
    const { container } = renderAt();
    await chart(container);
    const w = DAY_WIDTH.day;

    const bands = layer(container, "non-working").querySelectorAll("rect");
    expect(bands).toHaveLength(2);
    expect(bands[0]).toHaveAttribute("x", String(LABEL_WIDTH + 4 * w));
    expect(bands[0]).toHaveAttribute("width", String(w));

    const critical = layer(container, "planned").querySelector('[data-activity-id="21"]')!;
    expect(critical).toHaveAttribute("data-critical", "true");
    expect(critical).toHaveAttribute("stroke", "#d32f2f");
    expect(critical).toHaveAttribute("x", String(LABEL_WIDTH));
    expect(critical).toHaveAttribute("width", String(21 * w));
    expect(layer(container, "planned").querySelector('[data-activity-id="22"]')).not.toHaveAttribute("data-critical");

    expect(layer(container, "baseline").querySelector('[data-activity-id="21"]')).toHaveAttribute("fill", "#bdbdbd");
    expect(layer(container, "actual").querySelector('[data-activity-id="21"]')).toHaveAttribute(
      "data-open-ended",
      "true",
    );

    const milestone = layer(container, "planned").querySelector('[data-activity-id="25"]')!;
    expect(milestone.tagName.toLowerCase()).toBe("polygon");
    expect(milestone).toHaveAttribute("data-milestone", "true");
  });

  it("依存の矢印と、バーの上の担当者名・進捗率を描く（SCH-13）", async () => {
    mockApi(routes(meWith(["project.view"])));
    const { container } = renderAt();
    await chart(container);

    const link = layer(container, "links").querySelector('[data-link="24-23"]')!;
    expect(link.getAttribute("marker-end")).toBe("url(#gantt-arrowhead)");
    const label = layer(container, "bar-labels").querySelector('[data-activity-id="21"]')!;
    expect(label).toHaveTextContent("計画 太郎、実行 花子 25%");
  });

  it("報告日線とイナズマ線を描き、頂点の色を判定で変える（稲妻チャート.txt §6 の例）", async () => {
    mockApi(routes(meWith(["project.view"])));
    const { container } = renderAt();
    await chart(container);
    const w = DAY_WIDTH.day;

    // 報告日 12/10 の終業＝原点から 10 日分
    const reportLine = layer(container, "report-line").querySelector("line")!;
    expect(reportLine).toHaveAttribute("x1", String(LABEL_WIDTH + 10 * w));
    expect(reportLine).toHaveAttribute("stroke-dasharray", "4 3");

    const vertices = Array.from(layer(container, "lightning").querySelectorAll("circle"));
    expect(vertices.map((c) => c.getAttribute("data-judgement"))).toEqual([
      "BEHIND",
      "AHEAD",
      "NOT_STARTED",
      "COMPLETED",
      "NOT_COMPUTED",
    ]);
    expect(vertices.map((c) => c.getAttribute("fill"))).toEqual(["#d32f2f", "#1976d2", "#388e3c", "#388e3c", "#9e9e9e"]);
    // P＝5.25（12/06 の 1/4）・P＝12.6（12/13 の 6 割）
    expect(Number(vertices[0]!.getAttribute("cx"))).toBeCloseTo(LABEL_WIDTH + 5.25 * w);
    expect(Number(vertices[1]!.getAttribute("cx"))).toBeCloseTo(LABEL_WIDTH + 12.6 * w);
    // 折れ線は報告日線の上端から始まり、下端で終わる
    const polyline = layer(container, "lightning").querySelector("polyline")!.getAttribute("points")!.split(" ");
    expect(polyline).toHaveLength(7);
    expect(polyline[0]!.startsWith(`${LABEL_WIDTH + 10 * w},`)).toBe(true);
    expect(polyline[6]!.startsWith(`${LABEL_WIDTH + 10 * w},`)).toBe(true);
  });

  it("判定を一覧にし、判定で絞り込める（PRG-13）。未算定には入力を促す", async () => {
    mockApi(routes(meWith(["project.view"])));
    const { container } = renderAt();
    await chart(container);

    const table = screen.getByRole("table", { name: "判定の一覧" });
    expect(within(table).getAllByRole("row")).toHaveLength(6);
    expect(within(table).getByText("未算定（進捗の入力が要る）")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("判定で絞り込む"), { target: { value: "BEHIND" } });

    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveTextContent("資料作成");
    expect(rows[0]).toHaveTextContent("-4.75");
    expect(rows[0]).toHaveTextContent("2026-12-06");
  });

  it("表示の単位（日・週・月）は 1 日あたりの横幅だけを変える", async () => {
    mockApi(routes(meWith(["project.view"])));
    const { container } = renderAt();
    await chart(container);
    const svg = () => screen.getByRole("img", { name: "ガントチャート" });

    const height = svg().getAttribute("height");
    expect(height).toBe(String(32 + 5 * 28));
    expect(svg()).toHaveAttribute("width", String(LABEL_WIDTH + 21 * DAY_WIDTH.day));
    fireEvent.change(screen.getByLabelText("表示の単位"), { target: { value: "week" } });
    expect(svg()).toHaveAttribute("width", String(LABEL_WIDTH + 21 * DAY_WIDTH.week));
    fireEvent.change(screen.getByLabelText("表示の単位"), { target: { value: "month" } });
    expect(svg()).toHaveAttribute("width", String(LABEL_WIDTH + 21 * DAY_WIDTH.month));
    // 行の高さ（＝図の高さ）は単位で変わらない
    expect(svg()).toHaveAttribute("height", height);
  });

  it("比べる基準の版を選ぶと、その版でガントを読み直す（SCH-12）", async () => {
    const { calls } = mockApi(routes(meWith(["project.view"])));
    const { container } = renderAt();
    await chart(container);
    expect(sent(calls, "GET", `${BASE}/gantt`)[0]!.url).toBe(`${BASE}/gantt`);

    fireEvent.change(screen.getByLabelText("比べる基準"), { target: { value: "61" } });

    await waitFor(() => expect(sent(calls, "GET", `${BASE}/gantt`)).toHaveLength(2));
    expect(sent(calls, "GET", `${BASE}/gantt`)[1]!.url).toBe(`${BASE}/gantt?baseline_id=61`);
  });

  it("確定済みの報告日を選ぶと、過去の線を線の種類を変えて重ねる（PRG-18）", async () => {
    const { calls } = mockApi(routes(meWith(["project.view"])));
    const { container } = renderAt();
    await chart(container);
    expect(layer(container, "past-lines").children).toHaveLength(0);

    // OPEN の報告日は選べない
    const fieldset = screen.getByRole("group", { name: "重ねる過去の線（確定済みの報告日）" });
    expect(within(fieldset).getAllByRole("checkbox")).toHaveLength(1);
    fireEvent.click(within(fieldset).getByLabelText("2026-12-03"));

    await waitFor(() => expect(layer(container, "past-lines").children).toHaveLength(1));
    expect(sent(calls, "GET", `${BASE}/lightning-lines`)[0]!.url).toBe(`${BASE}/lightning-lines?report_ids=40`);
    const past = layer(container, "past-lines").querySelector('[data-report-id="40"]')!;
    expect(past.querySelector("polyline")).toHaveAttribute("stroke-dasharray", "6 3");
  });

  it("schedule.edit を持つ人は日程を計算し直せる（SCH-06・10）。持たない人にはボタンを出さない", async () => {
    const { calls } = mockApi(
      routes(meWith(["project.view", "schedule.edit"]), {
        [`POST ${BASE}/schedule/calculate`]: () =>
          jsonResponse(200, {
            project_finish_date: "2026-12-21",
            critical_path: [21, 25],
            min_total_float: 0,
            activities: [],
            restricted: false,
          }),
      }),
    );
    const { container } = renderAt();
    await chart(container);

    fireEvent.click(screen.getByRole("button", { name: "日程を計算し直す" }));

    expect(await screen.findByRole("status")).toHaveTextContent("プロジェクトの終了日 2026-12-21");
    expect(screen.getByRole("status")).toHaveTextContent("クリティカルな活動 2 件");
    await waitFor(() => expect(sent(calls, "GET", `${BASE}/gantt`)).toHaveLength(2));
    expect(sent(calls, "GET", `${BASE}/status-reports/41/lightning-line`)).toHaveLength(2);
  });

  it("schedule.edit が無ければ再計算のボタンを出さない", async () => {
    mockApi(routes(meWith(["project.view"])));
    const { container } = renderAt();
    await chart(container);
    expect(screen.queryByRole("button", { name: "日程を計算し直す" })).toBeNull();
  });

  it("循環があれば再計算の 422 の理由を出す", async () => {
    mockApi(
      routes(meWith(["project.view", "schedule.edit"]), {
        [`POST ${BASE}/schedule/calculate`]: () => errorResponse(422, "SCHEDULE_CYCLE", "依存が循環しています"),
      }),
    );
    const { container } = renderAt();
    await chart(container);

    fireEvent.click(screen.getByRole("button", { name: "日程を計算し直す" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("依存が循環しています");
  });

  it("活動 2,000 件でも見えている行だけを描く（7.3・NFR-02）", async () => {
    const rows = Array.from({ length: 2000 }, (_, i) =>
      makeGanttRow({ row_index: i, activity_id: 1000 + i, name: `活動 ${i}` }),
    );
    mockApi(
      routes(meWith(["project.view"]), {
        [`GET ${BASE}/gantt`]: () => jsonResponse(200, makeGantt({ rows })),
        [`GET ${BASE}/status-reports`]: () => jsonResponse(200, { items: [], total: 0, limit: 1000, offset: 0 }),
      }),
    );
    const { container } = renderAt();
    const svg = await screen.findByRole("img", { name: "ガントチャート" });

    expect(svg).toHaveAttribute("data-row-count", "2000");
    const drawn = layer(container, "planned").children.length;
    expect(drawn).toBeGreaterThan(0);
    expect(drawn).toBeLessThan(40);

    // 下へ送ると、その位置の行を描く
    const viewport = screen.getByTestId("gantt-viewport");
    viewport.scrollTop = 28 * 1500;
    fireEvent.scroll(viewport);
    await waitFor(() =>
      expect(layer(container, "planned").querySelector('[data-activity-id="2500"]')).not.toBeNull(),
    );
    expect(layer(container, "planned").querySelector('[data-activity-id="1000"]')).toBeNull();
  });

  it("報告日が無ければイナズマ線を出さない。活動が無ければその旨を出す", async () => {
    mockApi(
      routes(meWith(["project.view"]), {
        [`GET ${BASE}/gantt`]: () => jsonResponse(200, makeGantt({ rows: [], range_start: null, range_end: null })),
        [`GET ${BASE}/status-reports`]: () => jsonResponse(200, { items: [], total: 0, limit: 1000, offset: 0 }),
      }),
    );
    renderAt();

    expect(await screen.findByText("描く活動がありません")).toBeInTheDocument();
    expect(screen.queryByRole("table", { name: "判定の一覧" })).toBeNull();
  });

  it("プロジェクトの ID が正しくなければ見つからないことを示す", () => {
    const { calls } = mockApi({});
    const { container } = renderAt("/projects/abc/gantt");
    expect(container.querySelector('[data-screen-id="UI-SCH-002"]')).not.toBeNull();
    expect(screen.getByRole("alert")).toHaveTextContent("プロジェクトが見つかりません");
    expect(calls).toHaveLength(0);
  });

  it("既定で開く報告日は最も新しい報告日", () => {
    expect(latestReport(REPORTS)?.id).toBe(41);
    expect(latestReport([])).toBeNull();
  });
});
