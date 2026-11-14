import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setAuthToken } from "../../api/client";
import type { Me } from "../../api/cmn";
import type { Gantt } from "../../api/sch";
import { DAY_WIDTH, LABEL_WIDTH } from "../../components/gantt/scale";
import { makeMe, makeUser } from "../../test/fixtures";
import { errorResponse, jsonResponse, mockApi, type RecordedCall, type RouteHandler } from "../../test/mockApi";
import { makeProgressEntry } from "../../test/prgFixtures";
import {
  activityList,
  makeActivity,
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
import GanttPage, { latestReport, withReport } from "./GanttPage";

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
    <MemoryRouter initialEntries={[path]}>
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

/** 行き先の並びの「表示の設定」を開く（CR-027） */
function openSettings(): HTMLElement {
  fireEvent.click(within(screen.getByRole("navigation", { name: "行き先" })).getByRole("button", { name: "表示の設定" }));
  return screen.getByRole("dialog", { name: "表示の設定" });
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
    // 完了・未着手（予定内）は同じ緑に印（形）を重ねて区別する（設計書 1.1 の 7.3）
    const marks = Array.from(layer(container, "lightning").querySelectorAll("path[data-mark]"));
    expect(marks.map((m) => m.getAttribute("data-mark"))).toEqual(["hole", "check"]);
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
    openSettings();
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

    openSettings();
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
    openSettings();
    const fieldset = screen.getByRole("group", { name: "重ねる過去の線（確定済みの報告日）" });
    expect(within(fieldset).getAllByRole("checkbox")).toHaveLength(1);
    fireEvent.click(within(fieldset).getByLabelText("2026-12-03"));

    await waitFor(() => expect(layer(container, "past-lines").children).toHaveLength(1));
    expect(sent(calls, "GET", `${BASE}/lightning-lines`)[0]!.url).toBe(`${BASE}/lightning-lines?report_ids=40`);
    const past = layer(container, "past-lines").querySelector('[data-report-id="40"]')!;
    expect(past.querySelector("polyline")).toHaveAttribute("stroke-dasharray", "6 3");
    // 全ての頂点が今の行に当たれば、描いていない頂点の注記は出さない
    expect(screen.queryByText(/描いていない/)).toBeNull();
  });

  it("今のガントの行に当てられない頂点は描かず、その数を線ごとに注記する（黙って落とさない。A-49 の 13）", async () => {
    mockApi(
      routes(meWith(["project.view"]), {
        [`GET ${BASE}/status-reports/41/lightning-line`]: () =>
          jsonResponse(
            200,
            makeLightningLine([
              ...LIGHTNING.rows,
              // 今の計画に無い活動と、消した活動の写し（活動が null）
              makeLightningRow({ row_index: 5, activity_id: 99, name: "消した活動" }),
              makeLightningRow({ row_index: 6, activity_id: null, name: "昔の活動" }),
            ]),
          ),
        [`GET ${BASE}/lightning-lines`]: () =>
          jsonResponse(200, {
            project_start: "2026-12-01",
            lines: [
              makeOverlaidLine({
                vertices: [
                  ...makeOverlaidLine().vertices,
                  { ...makeOverlaidLine().vertices[0]!, row_index: 9, activity_id: 26 },
                ],
                missing_activity_ids: [98, null],
              }),
            ],
            restricted: false,
          }),
      }),
    );
    const { container } = renderAt();
    // 描くのは今の行に当たる 5 つの頂点だけ
    await chart(container);
    expect(screen.getByText("今の計画に無い活動の頂点 2 件は描いていない")).toBeInTheDocument();

    openSettings();
    fireEvent.click(
      within(screen.getByRole("group", { name: "重ねる過去の線（確定済みの報告日）" })).getByLabelText("2026-12-03"),
    );

    // 過去の線：今のガントに無い活動 2 件と、行の番号が今の行に無い頂点 1 件
    expect(
      await screen.findByText("報告日 2026-12-03 の線：今の計画に無い活動の頂点 3 件は描いていない"),
    ).toBeInTheDocument();
    const past = layer(container, "past-lines").querySelector('[data-report-id="40"]')!;
    expect(past.querySelectorAll("circle")).toHaveLength(1);
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
    // 編集できる人は開いたときから編集するので、バーの終わりの後ろへ広げた範囲で 1 回読み直す（7.3。CR-027）
    await waitFor(() => expect(sent(calls, "GET", `${BASE}/gantt`)).toHaveLength(2));

    const dialog = openSettings();
    fireEvent.click(within(dialog).getByRole("button", { name: "日程を計算し直す" }));

    expect(await within(dialog).findByRole("status")).toHaveTextContent("プロジェクトの終了日 2026-12-21");
    expect(within(dialog).getByRole("status")).toHaveTextContent("クリティカルな活動 2 件");
    await waitFor(() => expect(sent(calls, "GET", `${BASE}/gantt`)).toHaveLength(3));
    expect(sent(calls, "GET", `${BASE}/status-reports/41/lightning-line`)).toHaveLength(2);
  });

  it("schedule.edit が無ければ再計算のボタンを出さない", async () => {
    mockApi(routes(meWith(["project.view"])));
    const { container } = renderAt();
    await chart(container);
    expect(within(openSettings()).queryByRole("button", { name: "日程を計算し直す" })).toBeNull();
  });

  it("循環があれば再計算の 422 の理由を出す", async () => {
    mockApi(
      routes(meWith(["project.view", "schedule.edit"]), {
        [`POST ${BASE}/schedule/calculate`]: () => errorResponse(422, "SCHEDULE_CYCLE", "依存が循環しています"),
      }),
    );
    const { container } = renderAt();
    await chart(container);

    const dialog = openSettings();
    fireEvent.click(within(dialog).getByRole("button", { name: "日程を計算し直す" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent("依存が循環しています");
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

  it("描画データが届くまで読み込み中を示し、届いたら消す", async () => {
    let release: () => void = () => undefined;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    mockApi(
      routes(meWith(["project.view"]), {
        [`GET ${BASE}/gantt`]: async () => {
          await held;
          return jsonResponse(200, GANTT);
        },
      }),
    );
    const { container } = renderAt();

    expect(screen.getByText("読み込み中…")).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: "ガントチャート" })).toBeNull();
    release();
    await chart(container);
    expect(screen.queryByText("読み込み中…")).toBeNull();
  });

  it("ガントと関係ない API の誤りでは、読み込み中を消さない（描画データが届くまで出す）", async () => {
    let release: () => void = () => undefined;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    mockApi(
      routes(meWith(["project.view"]), {
        [`GET ${BASE}/gantt`]: async () => {
          await held;
          return jsonResponse(200, GANTT);
        },
        [`GET ${BASE}/status-reports/41/lightning-line`]: () => errorResponse(404, "NOT_FOUND", "報告日が見つかりません"),
      }),
    );
    renderAt();

    expect(await screen.findByRole("alert")).toHaveTextContent("報告日が見つかりません");
    expect(screen.getByText("読み込み中…")).toBeInTheDocument();
    release();
    expect(await screen.findByRole("img", { name: "ガントチャート" })).toBeInTheDocument();
    expect(screen.queryByText("読み込み中…")).toBeNull();
  });

  it("描画データを読めなければ、読み込み中を消して理由を出す", async () => {
    mockApi(
      routes(meWith(["project.view"]), {
        [`GET ${BASE}/gantt`]: () => errorResponse(403, "FORBIDDEN", "この操作の権限がありません"),
      }),
    );
    renderAt();

    expect(await screen.findByRole("alert")).toHaveTextContent("この操作の権限がありません");
    expect(screen.queryByText("読み込み中…")).toBeNull();
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

  it("作った報告日は一覧の報告日の古い順の位置に入る（同じ ID は置き換える）", () => {
    const created = makeStatusReport({ id: 42, report_date: "2026-12-07" });
    expect(withReport(REPORTS, created).map((r) => r.id)).toEqual([40, 42, 41]);
    expect(withReport([], created)).toEqual([created]);
    expect(withReport([created], { ...created, entry_count: 3 })).toEqual([{ ...created, entry_count: 3 }]);
  });
});

describe("UI-SCH-002 ガントから報告日を作る（7.2。API-PRG-001）", () => {
  const NO_REPORTS = { items: [], total: 0, limit: 1000, offset: 0 };
  const CREATED = makeStatusReport({ id: 42, report_date: "2026-12-17" });

  beforeEach(() => {
    setAuthToken("t-pm");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    setAuthToken(null);
  });

  it("status_report.manage を持つ人は報告日が 1 件も無くても作れ、作った報告日を選んで進捗入力へのリンクを出す", async () => {
    const { calls } = mockApi(
      routes(meWith(["project.view", "status_report.manage"]), {
        [`GET ${BASE}/status-reports`]: () => jsonResponse(200, NO_REPORTS),
        [`POST ${BASE}/status-reports`]: () => jsonResponse(201, CREATED),
        [`GET ${BASE}/status-reports/42/lightning-line`]: () =>
          jsonResponse(200, makeLightningLine([], { report: CREATED, report_line: point("2026-12-17", 1, 17) })),
      }),
    );
    renderAt();
    await screen.findByRole("img", { name: "ガントチャート" });
    expect(screen.queryByRole("form", { name: "報告日を作る" })).toBeNull();
    const dialog = openSettings();
    const form = within(dialog).getByRole("form", { name: "報告日を作る" });
    expect(screen.queryByRole("link", { name: "進捗入力" })).toBeNull();

    fireEvent.change(within(form).getByLabelText("新しい報告日"), { target: { value: "2026-12-17" } });
    fireEvent.click(within(form).getByRole("button", { name: "報告日を作る" }));

    expect(await within(dialog).findByRole("status")).toHaveTextContent("報告日 2026-12-17 を作って選んだ");
    expect(sent(calls, "POST", `${BASE}/status-reports`)[0]!.body).toEqual({ report_date: "2026-12-17" });
    const select = screen.getByLabelText("報告日");
    expect(select).toHaveValue("42");
    expect(within(select).getByRole("option", { name: "2026-12-17" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "進捗入力" })).toHaveAttribute("href", "/projects/3/status-reports/42");
    // 選んだ報告日の線を読む（一覧は読み直さない）
    await waitFor(() => expect(sent(calls, "GET", `${BASE}/status-reports/42/lightning-line`)).toHaveLength(1));
    expect(sent(calls, "GET", `${BASE}/status-reports`)).toHaveLength(1);
  });

  it("同じ日付の報告日は 409、形の違う日付は 400 のサーバの文言を出し、報告日を選び直さない", async () => {
    let status = 409;
    mockApi(
      routes(meWith(["project.view", "status_report.manage"]), {
        [`POST ${BASE}/status-reports`]: () =>
          status === 409
            ? errorResponse(409, "DUPLICATE_STATUS_REPORT", "同じ日付の報告日が既にあります")
            : errorResponse(400, "VALIDATION_ERROR", "報告日の形が正しくありません"),
      }),
    );
    const { container } = renderAt();
    await chart(container);
    const form = within(openSettings()).getByRole("form", { name: "報告日を作る" });

    fireEvent.change(within(form).getByLabelText("新しい報告日"), { target: { value: "2026-12-10" } });
    fireEvent.click(within(form).getByRole("button", { name: "報告日を作る" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("同じ日付の報告日が既にあります");

    status = 400;
    fireEvent.click(within(form).getByRole("button", { name: "報告日を作る" }));
    await waitFor(() => expect(within(form).getByRole("alert")).toHaveTextContent("報告日の形が正しくありません"));
    expect(screen.getByLabelText("報告日")).toHaveValue("41");
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("status_report.manage が無ければ報告日を作る部品を出さない", async () => {
    mockApi(routes(meWith(["project.view", "schedule.edit", "progress.enter_all"])));
    const { container } = renderAt();
    await chart(container);
    const dialog = openSettings();
    expect(within(dialog).getByRole("button", { name: "日程を計算し直す" })).toBeInTheDocument();
    expect(within(dialog).queryByRole("form", { name: "報告日を作る" })).toBeNull();
  });
});

describe("UI-SCH-002 非稼働日を表示範囲で受け取る（7.3・API-SCH-010 の range_start・range_end）", () => {
  /** 範囲を渡されたら、その範囲の非稼働日を返す（応答の range_start・range_end はバーの範囲のまま） */
  function ganttFor(nonWorking: Record<string, string[]>): RouteHandler {
    return (call) => {
      const query = new URL(call.url, "http://localhost").searchParams;
      const key = `${query.get("range_start") ?? ""}..${query.get("range_end") ?? ""}`;
      return jsonResponse(200, { ...GANTT, non_working_days: nonWorking[key] ?? GANTT.non_working_days });
    };
  }

  function bandDates(container: HTMLElement): string[] {
    return Array.from(layer(container, "non-working").querySelectorAll("rect")).map((r) => r.getAttribute("data-date")!);
  }

  beforeEach(() => {
    setAuthToken("t-pm");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    setAuthToken(null);
  });

  it("報告日線・頂点で描く範囲がバーより広がれば、描く範囲を渡して読み直し、広がった部分の非稼働日を描く。読み直しは 1 回", async () => {
    // バーの範囲は 12/01〜12/21。報告日 12/24 の線と、頂点 12/28 で描く範囲は 12/01〜12/28 になる
    const late = makeStatusReport({ id: 43, report_date: "2026-12-24" });
    const { calls } = mockApi(
      routes(meWith(["project.view"]), {
        [`GET ${BASE}/gantt`]: ganttFor({
          "2026-12-01..2026-12-28": ["2026-12-05", "2026-12-06", "2026-12-26", "2026-12-27"],
        }),
        [`GET ${BASE}/status-reports`]: () => jsonResponse(200, { items: [late], total: 1, limit: 1000, offset: 0 }),
        [`GET ${BASE}/status-reports/43/lightning-line`]: () =>
          jsonResponse(
            200,
            makeLightningLine(
              [
                makeLightningRow({ point: point("2026-12-28", 0.5, 27.5), judgement: "AHEAD", counted_as: "AHEAD" }),
                // ガントの行に無い活動の頂点は描かないので、範囲も広げない
                makeLightningRow({ row_index: 9, activity_id: 99, point: point("2027-01-20", 0, 50) }),
              ],
              { report: late, report_line: point("2026-12-24", 1, 24) },
            ),
          ),
      }),
    );
    const { container } = renderAt();
    await screen.findByRole("img", { name: "ガントチャート" });

    await waitFor(() => expect(bandDates(container)).toEqual(["2026-12-05", "2026-12-06", "2026-12-26", "2026-12-27"]));
    const requests = sent(calls, "GET", `${BASE}/gantt`);
    expect(requests.map((c) => c.url)).toEqual([
      `${BASE}/gantt`,
      `${BASE}/gantt?range_start=2026-12-01&range_end=2026-12-28`,
    ]);
    // 描く範囲（12/01〜12/28）で描く
    expect(screen.getByRole("img", { name: "ガントチャート" })).toHaveAttribute(
      "width",
      String(LABEL_WIDTH + 28 * DAY_WIDTH.day),
    );
  });

  it("描く範囲がバーの範囲に収まれば読み直さない（範囲を渡さない）", async () => {
    const { calls } = mockApi(routes(meWith(["project.view"])));
    const { container } = renderAt();
    await chart(container);

    // 確定済みの過去の線（12/03）も範囲の中
    openSettings();
    fireEvent.click(within(screen.getByRole("group", { name: "重ねる過去の線（確定済みの報告日）" })).getByLabelText("2026-12-03"));
    await waitFor(() => expect(layer(container, "past-lines").children).toHaveLength(1));

    expect(sent(calls, "GET", `${BASE}/gantt`).map((c) => c.url)).toEqual([`${BASE}/gantt`]);
    expect(bandDates(container)).toEqual(["2026-12-05", "2026-12-06"]);
  });

  it("過去の線を選び直して範囲が広がれば読み直す。外しても読み直さず、基準の版を選び直すと同じ範囲で読む", async () => {
    // 報告日 12/03 の線で、頂点 11/25（バーの範囲 12/01 より前）まで遅れている
    const early = makeOverlaidLine({
      vertices: [
        {
          row_index: 0,
          activity_id: 21,
          point: point("2026-11-25", 0.5, -5.5),
          deviation: -8.5,
          judgement: "BEHIND",
          display_date: "2026-11-25",
        },
      ],
    });
    const { calls } = mockApi(
      routes(meWith(["project.view"]), {
        [`GET ${BASE}/gantt`]: ganttFor({
          "2026-11-25..2026-12-21": ["2026-11-28", "2026-11-29", "2026-12-05", "2026-12-06"],
        }),
        [`GET ${BASE}/lightning-lines`]: () =>
          jsonResponse(200, { project_start: "2026-12-01", lines: [early], restricted: false }),
      }),
    );
    const { container } = renderAt();
    await chart(container);
    expect(sent(calls, "GET", `${BASE}/gantt`)).toHaveLength(1);

    openSettings();
    const fieldset = screen.getByRole("group", { name: "重ねる過去の線（確定済みの報告日）" });
    fireEvent.click(within(fieldset).getByLabelText("2026-12-03"));
    await waitFor(() => expect(bandDates(container)).toEqual(["2026-11-28", "2026-11-29", "2026-12-05", "2026-12-06"]));
    expect(sent(calls, "GET", `${BASE}/gantt`).map((c) => c.url)).toEqual([
      `${BASE}/gantt`,
      `${BASE}/gantt?range_start=2026-11-25&range_end=2026-12-21`,
    ]);

    // 外すと描く範囲は狭まるが、受け取った範囲に収まるので読み直さない
    fireEvent.click(within(fieldset).getByLabelText("2026-12-03"));
    await waitFor(() => expect(layer(container, "past-lines").children).toHaveLength(0));
    expect(sent(calls, "GET", `${BASE}/gantt`)).toHaveLength(2);

    // 基準の版を選び直すと、最後に要求した表示範囲のまま読み直す
    fireEvent.change(screen.getByLabelText("比べる基準"), { target: { value: "61" } });
    await waitFor(() => expect(sent(calls, "GET", `${BASE}/gantt`)).toHaveLength(3));
    expect(sent(calls, "GET", `${BASE}/gantt`)[2]!.url).toBe(
      `${BASE}/gantt?baseline_id=61&range_start=2026-11-25&range_end=2026-12-21`,
    );
  });
});

describe("UI-SCH-002 表示の設定と活動の詳細と進捗（7.3。CR-027）", () => {
  const ACTIVITY_21 = makeActivity({
    id: 21,
    name: "資料作成",
    requested_start: "2026-12-01",
    duration_days: 15,
    start_date: "2026-12-01",
    finish_date: "2026-12-21",
    successor_ids: [22],
    assignees: [{ user_id: 5, display_name: "計画 太郎", planned_hours: 30 }],
  });
  const ACTIVITY_22 = makeActivity({ id: 22, name: "関係者調整", predecessor_ids: [21], is_critical: false, total_float: 2 });
  const ACTIVITIES = { [`GET ${BASE}/activities`]: () => jsonResponse(200, activityList([ACTIVITY_21, ACTIVITY_22])) };

  function rowLabel(container: HTMLElement, activityId: number): Element {
    return container.querySelector(`[data-layer="row-labels"] [data-activity-id="${activityId}"]`)!;
  }

  beforeEach(() => {
    setAuthToken("t-pm");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    setAuthToken(null);
  });

  it("表示の設定は行き先の並びのボタンで開くダイアログに置き、図の上には今の設定を 1 行で示す。Esc で閉じてボタンへ的を戻す", async () => {
    mockApi(routes(meWith(["project.view"])));
    const { container } = renderAt();
    await chart(container);
    // 図の上に選ぶ欄を置かない
    expect(screen.queryByLabelText("表示の単位")).toBeNull();
    expect(screen.queryByRole("group", { name: "重ねる過去の線（確定済みの報告日）" })).toBeNull();
    const summary = container.querySelector("p.gantt-legend")!;
    expect(summary).toHaveTextContent(/^単位：日／基準の版はまだ無い／報告日：2026-12-10（許容幅 0.5 日）$/);

    const button = within(screen.getByRole("navigation", { name: "行き先" })).getByRole("button", { name: "表示の設定" });
    button.focus();
    const dialog = openSettings();
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(within(dialog).getByLabelText("表示の単位")).toHaveFocus();
    fireEvent.change(within(dialog).getByLabelText("表示の単位"), { target: { value: "week" } });
    fireEvent.click(within(within(dialog).getByRole("group", { name: "重ねる過去の線（確定済みの報告日）" })).getByLabelText("2026-12-03"));
    // 選ぶとその場で図に効く
    await waitFor(() => expect(layer(container, "past-lines").children).toHaveLength(1));
    expect(summary).toHaveTextContent(/^単位：週／基準の版はまだ無い／報告日：2026-12-10（許容幅 0.5 日）／過去の線 1 本$/);

    fireEvent.keyDown(within(dialog).getByLabelText("表示の単位"), { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(button).toHaveFocus();
  });

  it("閲覧だけの人も活動の行の見出しのダブルクリックで詳細と進捗を開け、値だけを出す（活動は API-SCH-001 から読む）", async () => {
    const { calls } = mockApi(routes(meWith(["project.view"]), ACTIVITIES));
    const { container } = renderAt();
    await chart(container);
    expect(sent(calls, "GET", `${BASE}/activities`)).toHaveLength(0);

    fireEvent.doubleClick(rowLabel(container, 21));
    const dialog = await screen.findByRole("dialog", { name: "活動の詳細と進捗：1.1 資料作成" });
    expect(sent(calls, "GET", `${BASE}/activities`)).toHaveLength(1);
    expect(within(dialog).queryAllByRole("textbox")).toHaveLength(0);
    expect(within(dialog).queryByRole("button", { name: /保存/ })).toBeNull();
    expect(within(dialog).getByText("2026-12-01〜2026-12-21")).toBeInTheDocument();
    expect(within(dialog).getByText("0 日（クリティカル）")).toBeInTheDocument();
    expect(within(dialog).getByText("1.1 関係者調整")).toBeInTheDocument();
    expect(within(dialog).getByText("15 稼働日")).toBeInTheDocument();
    expect(within(dialog).getByText("計画 太郎（30 人時）")).toBeInTheDocument();
    expect(within(dialog).getByText("40 人時")).toBeInTheDocument();
    // 進捗：未確定の報告日でも、書く権限が無ければ値だけ
    expect(within(dialog).getByRole("heading", { name: "進捗（報告日 2026-12-10）" })).toBeInTheDocument();
    expect(within(dialog).getByText("25%")).toBeInTheDocument();
    expect(within(dialog).getByText("この活動の進捗を書く権限が無い")).toBeInTheDocument();
    // メンバーは読まない（担当を書けない人）
    expect(sent(calls, "GET", `${BASE}/members`)).toHaveLength(0);

    fireEvent.click(within(dialog).getByRole("button", { name: "閉じる" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("progress.enter_own で担当の人は、予定のバーのダブルクリックで開いたダイアログで進捗を書け、保存してもダイアログを開いたまま読み直す", async () => {
    const { calls } = mockApi(
      routes(meWith(["project.view", "progress.enter_own"]), {
        ...ACTIVITIES,
        [`PUT ${BASE}/status-reports/41/progress/21`]: () => jsonResponse(200, makeProgressEntry({ percent_complete: 40 })),
      }),
    );
    const { container } = renderAt();
    await chart(container);

    fireEvent.doubleClick(layer(container, "planned").querySelector('[data-activity-id="21"]')!);
    const dialog = await screen.findByRole("dialog", { name: "活動の詳細と進捗：1.1 資料作成" });
    const form = within(dialog).getByRole("form", { name: "進捗の入力" });
    // 算定方式は schedule.edit を持つ人だけ
    expect(within(dialog).queryByRole("form", { name: "算定方式" })).toBeNull();
    fireEvent.change(within(form).getByLabelText("進捗率（%）"), { target: { value: "40" } });
    fireEvent.click(within(form).getByRole("button", { name: "進捗を保存する" }));

    await waitFor(() => expect(sent(calls, "PUT", `${BASE}/status-reports/41/progress/21`)).toHaveLength(1));
    expect(sent(calls, "PUT", `${BASE}/status-reports/41/progress/21`)[0]!.body).toMatchObject({ percent_complete: 40 });
    expect(await within(dialog).findByText(/^「資料作成」の進捗を保存した/)).toBeInTheDocument();
    await waitFor(() => expect(sent(calls, "GET", `${BASE}/status-reports/41/lightning-line`)).toHaveLength(2));
    await waitFor(() => expect(sent(calls, "GET", `${BASE}/gantt`)).toHaveLength(2));
    await waitFor(() => expect(sent(calls, "GET", `${BASE}/activities`)).toHaveLength(2));
    expect(screen.getByRole("dialog", { name: "活動の詳細と進捗：1.1 資料作成" })).toBeInTheDocument();
  });

  it("判定の一覧の活動の名前からも開け、Esc で閉じて名前へ的を戻す。担当でない活動・確定済みの報告日には値だけを出す", async () => {
    mockApi(
      routes(meWith(["project.view", "progress.enter_own"]), {
        ...ACTIVITIES,
        [`GET ${BASE}/status-reports/40/lightning-line`]: () =>
          jsonResponse(200, makeLightningLine(LIGHTNING.rows, { report: REPORTS[0]! })),
      }),
    );
    const { container } = renderAt();
    await chart(container);

    const name = within(screen.getByRole("table", { name: "判定の一覧" })).getByRole("button", { name: "関係者調整" });
    name.focus();
    fireEvent.click(name);
    const dialog = await screen.findByRole("dialog", { name: "活動の詳細と進捗：1.1 関係者調整" });
    expect(within(dialog).queryByRole("form", { name: "進捗の入力" })).toBeNull();
    expect(within(dialog).getByText("この活動の進捗を書く権限が無い")).toBeInTheDocument();
    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(name).toHaveFocus();

    // 確定済みの報告日では、担当の活動でも値だけ
    const settings = openSettings();
    fireEvent.change(within(settings).getByLabelText("報告日"), { target: { value: "40" } });
    fireEvent.click(within(settings).getByRole("button", { name: "閉じる" }));
    await waitFor(() => expect(container.querySelector("p.gantt-legend")).toHaveTextContent("報告日：2026-12-03（確定）"));
    fireEvent.doubleClick(rowLabel(container, 21));
    const finalized = await screen.findByRole("dialog", { name: "活動の詳細と進捗：1.1 資料作成" });
    expect(within(finalized).getByRole("heading", { name: "進捗（報告日 2026-12-03・確定済み）" })).toBeInTheDocument();
    expect(within(finalized).queryByRole("form", { name: "進捗の入力" })).toBeNull();
    expect(within(finalized).getByText("確定済みの報告日なので値だけを出す")).toBeInTheDocument();
  });

  it("算定方式だけを書ける人（schedule.edit で担当でない）には、進捗の値を出したうえで算定方式のフォームを出す", async () => {
    mockApi(
      routes(meWith(["project.view", "schedule.edit", "progress.enter_own"]), {
        ...ACTIVITIES,
        [`GET ${BASE}/members`]: () => jsonResponse(200, { project_id: 3, items: [], total: 0, limit: 1000, offset: 0 }),
      }),
    );
    const { container } = renderAt();
    await chart(container);

    fireEvent.doubleClick(rowLabel(container, 22));
    const dialog = await screen.findByRole("dialog", { name: "活動の詳細と進捗：1.1 関係者調整" });
    expect(within(dialog).getByText("60%")).toBeInTheDocument();
    expect(within(dialog).getByRole("form", { name: "算定方式" })).toBeInTheDocument();
    expect(within(dialog).queryByRole("form", { name: "進捗の入力" })).toBeNull();
    expect(within(dialog).getByText("この活動の進捗の値を書く権限が無い（算定方式は変えられる）")).toBeInTheDocument();
  });

  it("確定したかは読み直したイナズマ線の報告日で見る（画面を開いた後に確定されたら、書く欄を出さない）", async () => {
    mockApi(
      routes(meWith(["project.view", "progress.enter_all"]), {
        ...ACTIVITIES,
        [`GET ${BASE}/status-reports/41/lightning-line`]: () =>
          jsonResponse(200, makeLightningLine(LIGHTNING.rows, { report: { ...REPORTS[1]!, status: "FINALIZED" } })),
      }),
    );
    const { container } = renderAt();
    await chart(container);

    fireEvent.doubleClick(rowLabel(container, 21));
    const dialog = await screen.findByRole("dialog", { name: "活動の詳細と進捗：1.1 資料作成" });
    expect(within(dialog).getByRole("heading", { name: "進捗（報告日 2026-12-10・確定済み）" })).toBeInTheDocument();
    expect(within(dialog).queryByRole("form", { name: "進捗の入力" })).toBeNull();
    expect(within(dialog).getByText("確定済みの報告日なので値だけを出す")).toBeInTheDocument();
  });

  it("イナズマ線を読めなければ、進捗の欄に読み込み中を出し続けずにその旨を示す", async () => {
    mockApi(
      routes(meWith(["project.view", "progress.enter_all"]), {
        ...ACTIVITIES,
        [`GET ${BASE}/status-reports/41/lightning-line`]: () => errorResponse(500, "INTERNAL", "失敗"),
      }),
    );
    const { container } = renderAt();
    await screen.findByRole("img", { name: "ガントチャート" });
    await screen.findByText("失敗");

    fireEvent.doubleClick(rowLabel(container, 21));
    const dialog = await screen.findByRole("dialog", { name: "活動の詳細と進捗：1.1 資料作成" });
    expect(
      await within(dialog).findByText("この報告日のイナズマ線を読めないので、進捗を出せない。画面を開き直してください"),
    ).toBeInTheDocument();
    expect(within(dialog).queryByText("読み込み中…")).toBeNull();
  });

  it("報告日を選んでいなければ、進捗の欄に表示の設定で選ぶよう示す", async () => {
    mockApi(
      routes(meWith(["project.view", "progress.enter_all"]), {
        ...ACTIVITIES,
        [`GET ${BASE}/status-reports`]: () => jsonResponse(200, { items: [], total: 0, limit: 1000, offset: 0 }),
      }),
    );
    const { container } = renderAt();
    await screen.findByRole("img", { name: "ガントチャート" });
    expect(container.querySelector("p.gantt-legend")).toHaveTextContent("報告日：選んでいない");

    fireEvent.doubleClick(rowLabel(container, 22));
    const dialog = await screen.findByRole("dialog", { name: "活動の詳細と進捗：1.1 関係者調整" });
    expect(within(dialog).getByText("報告日を選んでいない。表示の設定で報告日を選ぶ（作る）と、ここで進捗を書ける")).toBeInTheDocument();
    expect(within(dialog).queryByRole("form", { name: "進捗の入力" })).toBeNull();
  });
});
