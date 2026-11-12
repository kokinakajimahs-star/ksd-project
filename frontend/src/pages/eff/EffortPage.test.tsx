import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { EffortSummary } from "../../api/eff";
import { setAuthToken } from "../../api/client";
import { makeCorrectiveAction } from "../../test/chgFixtures";
import {
  makeSummary,
  makeSummaryRow,
  makeValues,
  makeVariance,
  makeVarianceNote,
  makeVarianceRow,
  page,
} from "../../test/effFixtures";
import { makeMe, makeUser } from "../../test/fixtures";
import { errorResponse, jsonResponse, mockApi, type RouteHandler } from "../../test/mockApi";
import { makeScheduleBaseline, makeStatusReport } from "../../test/schFixtures";
import { bucketLabel, depthOf, rateText } from "./labels";
import EffortPage, { baselineText, reportBasisText } from "./EffortPage";

const FUTURE = { v7_startTransition: true, v7_relativeSplatPath: true };
const BASE = "/api/v1/projects/3";

function meWith(permissions: string[]) {
  return makeMe({
    user: makeUser({ id: 5, is_system_admin: false }),
    system_permissions: [],
    projects: [{ project_id: 3, roles: ["PM"], permissions: ["project.view", ...permissions], view_level: "ALL" }],
  });
}

// プロジェクト全体の値（どの軸でも合計は同じ。EFF-09）
const TOTAL = makeValues({ planned: 120, baseline: 100, actual: 50, etc: 80, eac: 130 });

// WBS の軸：上位の要素は下位の合計（EFF-07）
const WBS_SUMMARY = makeSummary({
  total: TOTAL,
  rows: [
    makeSummaryRow({ element_id: 1, wbs_code: "1", name: "基幹更改", kind: "DELIVERABLE", values: TOTAL }),
    makeSummaryRow({
      element_id: 10,
      parent_id: 1,
      wbs_code: "1.1",
      name: "要件定義",
      kind: "WORK_PACKAGE",
      values: makeValues({ planned: 40, baseline: 40, actual: 30, etc: 0, eac: 30 }),
    }),
    makeSummaryRow({
      element_id: 12,
      parent_id: 1,
      wbs_code: "1.2",
      name: "設計",
      kind: "WORK_PACKAGE",
      values: makeValues({ planned: 80, baseline: 60, actual: 20, etc: 80, eac: 100 }),
    }),
  ],
});

const MEMBER_SUMMARY = makeSummary({
  axis: "member",
  total: TOTAL,
  rows: [
    makeSummaryRow({ bucket_kind: "MEMBER", user_id: 7, name: "実行 花子", values: makeValues({ planned: 100, baseline: 90, actual: 50, etc: 60, eac: 110 }) }),
    makeSummaryRow({ bucket_kind: "UNASSIGNED", values: makeValues({ planned: 20, baseline: 10, actual: 0, etc: 20, eac: 20 }) }),
  ],
});

const VARIANCE = makeVariance([
  makeVarianceRow(),
  makeVarianceRow({
    element_id: 14,
    wbs_code: "1.4",
    name: "移行",
    baseline: null,
    eac: 12,
    variance: null,
    rate: null,
    status: "ADDED",
    note: makeVarianceNote({ element_id: 14, wbs_code: "1.4", reason: "変更要求で追加", corrective_action_id: 81 }),
  }),
]);

function routes(overrides: Record<string, RouteHandler> = {}): Record<string, RouteHandler> {
  return {
    "GET /api/v1/me": () => jsonResponse(200, meWith(["effort.view"])),
    [`GET ${BASE}/status-reports`]: () =>
      jsonResponse(200, page([makeStatusReport({ id: 40, report_date: "2026-12-03", status: "FINALIZED" }), makeStatusReport()])),
    [`GET ${BASE}/schedule-baselines`]: () =>
      jsonResponse(200, page([makeScheduleBaseline(), makeScheduleBaseline({ id: 62, version: 2, name: "変更後" })])),
    [`GET ${BASE}/effort/summary`]: (call) =>
      jsonResponse(200, call.url.includes("axis=member") ? MEMBER_SUMMARY : WBS_SUMMARY),
    [`GET ${BASE}/effort/variance`]: () => jsonResponse(200, VARIANCE),
    [`GET ${BASE}/corrective-actions`]: () => jsonResponse(200, page([makeCorrectiveAction()])),
    ...overrides,
  };
}

function renderAt(path = "/projects/3/effort") {
  return render(
    <MemoryRouter initialEntries={[path]} future={FUTURE}>
      <Routes>
        <Route path="/projects/:projectId/effort" element={<EffortPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

function bodyRows(table: HTMLElement): HTMLElement[] {
  const body = table.querySelector("tbody");
  return body === null ? [] : within(body).getAllByRole("row");
}

function footRow(table: HTMLElement): HTMLElement {
  const row = table.querySelector("tfoot tr");
  if (!(row instanceof HTMLElement)) {
    throw new Error("合計の行が無い");
  }
  return row;
}

describe("UI-EFF-002 工数の集計と差異", () => {
  beforeEach(() => {
    setAuthToken("t-pm");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    setAuthToken(null);
  });

  it("WBS の軸で計画・基準・実績・ETC・EAC を別の列に並べ、上位の要素と合計を出す（EFF-07・EFF-08）", async () => {
    const { calls } = mockApi(routes());
    const { container } = renderAt();

    const table = await screen.findByRole("table", { name: "工数の集計（WBS）" });
    expect(container.querySelector('[data-screen-id="UI-EFF-002"]')).not.toBeNull();
    expect(calls.map((c) => c.url)).toEqual(
      expect.arrayContaining([
        `${BASE}/effort/summary?axis=wbs`,
        `${BASE}/effort/variance`,
        `${BASE}/status-reports?limit=1000&offset=0`,
      ]),
    );
    const headers = within(table).getAllByRole("columnheader").map((h) => h.textContent);
    expect(headers).toEqual(["WBS ID", "名称", "計画", "基準", "実績", "ETC（残）", "EAC", "EAC−基準"]);

    const rows = bodyRows(table);
    expect(rows.map((r) => within(r).getAllByRole("cell").map((c) => c.textContent))).toEqual([
      ["1", "基幹更改", "120", "100", "50", "80", "130", "+30"],
      ["1.1", "要件定義", "40", "40", "30", "0", "30", "-10"],
      ["1.2", "設計", "80", "60", "20", "80", "100", "+40"],
    ]);
    // 下位の要素は字下げする
    expect(within(rows[1]!).getAllByRole("cell")[1]).toHaveStyle({ paddingLeft: "1.5em" });
    expect([...footRow(table).children].map((c) => c.textContent)).toEqual([
      "合計",
      "120",
      "100",
      "50",
      "80",
      "130",
      "+30",
    ]);
    expect(screen.getByText(/最新の報告日 2026-12-10（入力中）の進捗率と、全ての実績/)).toHaveTextContent(
      "基準：第 1 版（初版）",
    );
  });

  it("軸を変えると集計を読み直す。どの軸でも合計の行は同じ値（EFF-09）", async () => {
    const { calls } = mockApi(routes());
    renderAt();
    const wbs = await screen.findByRole("table", { name: "工数の集計（WBS）" });
    const wbsTotal = footRow(wbs).textContent;

    fireEvent.change(screen.getByLabelText("集計の軸"), { target: { value: "member" } });
    const member = await screen.findByRole("table", { name: "工数の集計（担当者）" });

    expect(calls.map((c) => c.url)).toContain(`${BASE}/effort/summary?axis=member`);
    expect(within(member).getAllByRole("columnheader")[0]).toHaveTextContent("担当者");
    expect(bodyRows(member).map((r) => within(r).getAllByRole("cell")[0]?.textContent)).toEqual(["実行 花子", "未割当"]);
    expect(footRow(member).textContent).toBe(wbsTotal);
    // 差異の分析は軸に依らないので読み直さない
    expect(calls.filter((c) => c.path === `${BASE}/effort/variance`)).toHaveLength(1);
  });

  it("報告日と基準を選ぶと、その条件で集計と差異を読み直す", async () => {
    const { calls } = mockApi(
      routes({
        [`GET ${BASE}/effort/summary`]: (call) =>
          jsonResponse(
            200,
            call.url.includes("report_id=41")
              ? makeSummary({ ...WBS_SUMMARY, report_specified: true, baseline: { id: 62, version: 2, name: "変更後" } })
              : WBS_SUMMARY,
          ),
      }),
    );
    renderAt();
    await screen.findByRole("table", { name: "工数の集計（WBS）" });

    // 報告日は新しい順、基準は版の新しい順
    const reportSelect = screen.getByLabelText("報告日");
    expect(within(reportSelect).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "指定しない（最新の報告日の進捗率・全ての実績）",
      "2026-12-10（入力中）",
      "2026-12-03（確定済み）",
    ]);
    const baselineSelect = screen.getByLabelText("基準");
    expect(within(baselineSelect).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "最新のベースライン",
      "第 2 版（変更後）",
      "第 1 版（当初計画）",
    ]);

    fireEvent.change(reportSelect, { target: { value: "41" } });
    fireEvent.change(baselineSelect, { target: { value: "62" } });

    expect(await screen.findByText(/報告日 2026-12-10（入力中）の進捗率と、報告日までの実績/)).toHaveTextContent(
      "基準：第 2 版（変更後）",
    );
    const urls = calls.map((c) => c.url);
    expect(urls).toContain(`${BASE}/effort/summary?axis=wbs&report_id=41&baseline_id=62`);
    expect(urls).toContain(`${BASE}/effort/variance?report_id=41&baseline_id=62`);
  });

  it("差異の分析：閾値を超えた要素と基準に無い要素を、差異・差異率・判定・理由・是正措置と並べる（EFF-10）", async () => {
    mockApi(routes());
    renderAt();

    const table = await screen.findByRole("table", { name: "閾値を超えた要素" });
    expect(screen.getByText(/差異率 10.0% と 8 人時の両方を超えた要素/)).toBeInTheDocument();
    const rows = bodyRows(table);
    expect(rows.map((r) => r.getAttribute("data-element-id"))).toEqual(["12", "14"]);
    expect(within(rows[0]!).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "1.2",
      "設計",
      "80",
      "100",
      "+20",
      "+25.0%",
      "閾値超過",
      "未記入",
      "なし",
    ]);
    // effort.view だけの人には紐付けの選択肢を読まないので、是正措置は #ID で出す
    expect(within(rows[1]!).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "1.4",
      "移行",
      "—",
      "12",
      "—",
      "—",
      "基準に無い",
      "変更要求で追加",
      "#81",
    ]);
    // variance.note が無ければ理由を書く部品を出さない
    expect(screen.queryByRole("button", { name: /理由を書く/ })).toBeNull();
    expect(screen.queryByRole("columnheader", { name: "操作" })).toBeNull();
  });

  it("variance.note を持つ人は差異の理由と是正措置の紐付けを書ける（API-EFF-007）", async () => {
    const saved = makeVarianceNote({ id: 302, element_id: 12, reason: "仕様の追加で設計が増えた", corrective_action_id: 81 });
    const { calls } = mockApi(
      routes({
        "GET /api/v1/me": () => jsonResponse(200, meWith(["effort.view", "variance.note"])),
        [`PUT ${BASE}/effort/variance/12/note`]: () => jsonResponse(200, saved),
      }),
    );
    renderAt();
    await screen.findByRole("table", { name: "閾値を超えた要素" });
    // 紐付けの選択肢（是正措置）を読んだ後は、既存の紐付けを対策の文で出す
    await waitFor(() => expect(screen.getByText("#81 仕様の確認会を週 2 回にする")).toBeInTheDocument());

    fireEvent.click(await screen.findByRole("button", { name: "1.2 の理由を書く" }));
    const form = screen.getByRole("form", { name: "1.2 設計 の差異の理由" });
    expect(form).toHaveTextContent("報告日を指定しない分析の理由として記録する");
    fireEvent.change(within(form).getByLabelText("差異の理由"), { target: { value: " 仕様の追加で設計が増えた " } });
    fireEvent.change(within(form).getByLabelText("紐付ける是正措置"), { target: { value: "81" } });
    fireEvent.click(within(form).getByRole("button", { name: "記録する" }));

    expect(await screen.findByRole("status")).toHaveTextContent("1.2 設計 の差異の理由を記録しました");
    const put = calls.filter((c) => c.method === "PUT");
    expect(put).toHaveLength(1);
    expect(put[0]?.body).toEqual({ status_report_id: null, reason: "仕様の追加で設計が増えた", corrective_action_id: 81 });
    const row = bodyRows(screen.getByRole("table", { name: "閾値を超えた要素" }))[0]!;
    expect(row).toHaveTextContent("仕様の追加で設計が増えた");
    expect(row).toHaveTextContent("#81 仕様の確認会を週 2 回にする");
    expect(within(row).getByRole("button", { name: "1.2 の理由を直す" })).toBeInTheDocument();
    expect(screen.queryByRole("form")).toBeNull();
  });

  it("報告日を選んでいれば、その報告日の理由として書く。理由が空なら送らない", async () => {
    const { calls } = mockApi(
      routes({
        "GET /api/v1/me": () => jsonResponse(200, meWith(["effort.view", "variance.note"])),
        [`PUT ${BASE}/effort/variance/14/note`]: () =>
          jsonResponse(200, makeVarianceNote({ element_id: 14, wbs_code: "1.4", status_report_id: 41, reason: "追加分" })),
      }),
    );
    renderAt();
    await screen.findByRole("table", { name: "閾値を超えた要素" });
    fireEvent.change(screen.getByLabelText("報告日"), { target: { value: "41" } });
    await waitFor(() =>
      expect(calls.map((c) => c.url)).toContain(`${BASE}/effort/variance?report_id=41`),
    );

    fireEvent.click(await screen.findByRole("button", { name: "1.4 の理由を直す" }));
    const form = screen.getByRole("form", { name: "1.4 移行 の差異の理由" });
    expect(form).toHaveTextContent("報告日 2026-12-10 の分析の理由として記録する");
    expect(within(form).getByLabelText("差異の理由")).toHaveValue("変更要求で追加");

    fireEvent.change(within(form).getByLabelText("差異の理由"), { target: { value: "   " } });
    fireEvent.click(within(form).getByRole("button", { name: "記録する" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("差異の理由を入力してください");
    expect(calls.filter((c) => c.method === "PUT")).toHaveLength(0);

    fireEvent.change(within(form).getByLabelText("差異の理由"), { target: { value: "追加分" } });
    fireEvent.change(within(form).getByLabelText("紐付ける是正措置"), { target: { value: "" } });
    fireEvent.click(within(form).getByRole("button", { name: "記録する" }));
    await screen.findByRole("status");
    expect(calls.filter((c) => c.method === "PUT")[0]?.body).toEqual({
      status_report_id: 41,
      reason: "追加分",
      corrective_action_id: null,
    });
  });

  it("理由の記録をサーバが拒んだら（無い是正措置など）文言を出し、入力を残す", async () => {
    mockApi(
      routes({
        "GET /api/v1/me": () => jsonResponse(200, meWith(["effort.view", "variance.note"])),
        [`PUT ${BASE}/effort/variance/12/note`]: () => errorResponse(404, "NOT_FOUND", "是正措置が見つかりません"),
      }),
    );
    renderAt();
    fireEvent.click(await screen.findByRole("button", { name: "1.2 の理由を書く" }));
    const form = screen.getByRole("form", { name: "1.2 設計 の差異の理由" });
    fireEvent.change(within(form).getByLabelText("差異の理由"), { target: { value: "見積の誤り" } });
    fireEvent.click(within(form).getByRole("button", { name: "記録する" }));

    expect(await within(form).findByRole("alert")).toHaveTextContent("是正措置が見つかりません");
    expect(within(form).getByLabelText("差異の理由")).toHaveValue("見積の誤り");
  });

  it("基準が無いときは差異の分析の誤り（409）を出し、集計は出す", async () => {
    mockApi(
      routes({
        [`GET ${BASE}/effort/variance`]: () =>
          errorResponse(409, "INVALID_STATE", "スケジュールベースラインがありません"),
        [`GET ${BASE}/effort/summary`]: () => jsonResponse(200, makeSummary({ ...WBS_SUMMARY, baseline: null })),
      }),
    );
    renderAt();

    expect(await screen.findByRole("alert")).toHaveTextContent("スケジュールベースラインがありません");
    expect(screen.getByRole("table", { name: "工数の集計（WBS）" })).toBeInTheDocument();
    expect(screen.getByText(/基準なし（スケジュールベースラインがありません）/)).toBeInTheDocument();
    expect(screen.queryByRole("table", { name: "閾値を超えた要素" })).toBeNull();
  });

  it("閾値を超えた要素が無ければその旨を出す", async () => {
    mockApi(routes({ [`GET ${BASE}/effort/variance`]: () => jsonResponse(200, makeVariance([])) }));
    renderAt();
    expect(await screen.findByText("閾値を超えた要素はありません")).toBeInTheDocument();
  });

  it("effort.view が無ければサーバの 403 の文言を出す", async () => {
    mockApi(
      routes({
        "GET /api/v1/me": () => jsonResponse(200, meWith([])),
        [`GET ${BASE}/effort/summary`]: () => errorResponse(403, "FORBIDDEN", "この操作の権限がありません"),
        [`GET ${BASE}/effort/variance`]: () => errorResponse(403, "FORBIDDEN", "この操作の権限がありません"),
      }),
    );
    renderAt();

    await waitFor(() => expect(screen.getAllByRole("alert")).toHaveLength(2));
    expect(screen.getAllByRole("alert")[0]).toHaveTextContent("この操作の権限がありません");
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("ID が正しくなければ API を呼ばずに見つからないことを示す", () => {
    const { calls } = mockApi({});
    const { container } = renderAt("/projects/x/effort");
    expect(container.querySelector('[data-screen-id="UI-EFF-002"]')).not.toBeNull();
    expect(screen.getByRole("alert")).toHaveTextContent("プロジェクトが見つかりません");
    expect(calls).toHaveLength(0);
  });

  it("ベースライン・是正措置へ進める（7.2）", async () => {
    mockApi(routes());
    renderAt();
    await screen.findByRole("table", { name: "工数の集計（WBS）" });
    expect(screen.getByRole("link", { name: "ベースライン" })).toHaveAttribute("href", "/projects/3/baselines");
    expect(screen.getByRole("link", { name: "是正措置" })).toHaveAttribute("href", "/projects/3/corrective-actions");
  });
});

describe("UI-EFF-002 の表示の計算", () => {
  it("軸ごとの桶の呼び名（未割当・所属なし・期間・残り・日付なし）", () => {
    const row = (overrides: Parameters<typeof makeSummaryRow>[0]) => makeSummaryRow(overrides);
    expect(bucketLabel("member", row({ bucket_kind: "MEMBER", user_id: 7, name: "実行 花子" }))).toBe("実行 花子");
    expect(bucketLabel("member", row({ bucket_kind: "UNASSIGNED" }))).toBe("未割当");
    expect(bucketLabel("organization", row({ bucket_kind: "ORGANIZATION", name: "開発部" }))).toBe("開発部");
    expect(bucketLabel("organization", row({ bucket_kind: "NO_ORGANIZATION" }))).toBe("所属なし");
    expect(bucketLabel("week", row({ bucket_kind: "PERIOD", period_start: "2026-12-07" }))).toBe("2026-12-07 の週");
    expect(bucketLabel("month", row({ bucket_kind: "PERIOD", period_start: "2026-12-01" }))).toBe("2026-12");
    expect(bucketLabel("week", row({ bucket_kind: "REMAINING" }))).toBe("残り（報告日より後の ETC）");
    expect(bucketLabel("month", row({ bucket_kind: "UNSCHEDULED" }))).toBe("日付なし（日程の無い計画・基準）");
  });

  it("差異率は割合を百分率で出す。閾値は符号を付けない", () => {
    expect(rateText(0.25)).toBe("+25.0%");
    expect(rateText(-0.125)).toBe("-12.5%");
    expect(rateText(0)).toBe("0.0%");
    expect(rateText(null)).toBe("—");
    expect(rateText(0.1, false)).toBe("10.0%");
  });

  it("WBS の行の深さは親をたどった数（親が一覧に無ければそこで止める）", () => {
    const depth = depthOf([
      makeSummaryRow({ element_id: 1 }),
      makeSummaryRow({ element_id: 10, parent_id: 1 }),
      makeSummaryRow({ element_id: 100, parent_id: 10 }),
      makeSummaryRow({ element_id: 200, parent_id: 99 }),
    ]);
    expect([...depth.entries()]).toEqual([
      [1, 0],
      [10, 1],
      [100, 2],
      [200, 0],
    ]);
  });

  it("基準と報告日の表示", () => {
    expect(baselineText(null)).toBe("基準なし（スケジュールベースラインがありません）");
    expect(baselineText({ id: 61, version: 1, name: null })).toBe("第 1 版");
    const base: Pick<EffortSummary, "report" | "report_specified"> = { report: null, report_specified: false };
    expect(reportBasisText(base)).toBe("報告日なし（進捗率は未入力、実績は全件）");
    expect(
      reportBasisText({ report: { id: 40, report_date: "2026-12-03", status: "FINALIZED" }, report_specified: true }),
    ).toBe("報告日 2026-12-03（確定済み）の進捗率と、報告日までの実績");
  });
});
