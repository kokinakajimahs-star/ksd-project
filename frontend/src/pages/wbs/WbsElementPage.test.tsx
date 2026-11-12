import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setAuthToken } from "../../api/client";
import type { Me } from "../../api/cmn";
import { makeMe, makeMember, makeUser } from "../../test/fixtures";
import { memberPage } from "../../test/chgFixtures";
import { errorResponse, jsonResponse, mockApi, type RecordedCall, type RouteHandler } from "../../test/mockApi";
import { estimateHistory, makeDictionary, makeEstimateRecord, makeTrace } from "../../test/wbsFixtures";
import WbsElementPage, { missingCompletion } from "./WbsElementPage";

const FUTURE = { v7_startTransition: true, v7_relativeSplatPath: true };
const BASE = "/api/v1/projects/3";

function meWith(permissions: string[]): Me {
  return makeMe({
    user: makeUser({ id: 5, is_system_admin: false }),
    system_permissions: [],
    projects: [{ project_id: 3, roles: ["PLANNER"], permissions, view_level: "ALL" }],
  });
}

const PLANNER = ["project.view", "wbs.edit", "estimate.edit"];

const SCOPE_ITEMS = {
  project_id: 3,
  items: [
    { id: 31, kind: "IN", title: "受注業務", description: null, sort_order: 1 },
    { id: 32, kind: "OUT", title: "会計連携", description: null, sort_order: 2 },
  ],
  total: 2,
  limit: 1000,
  offset: 0,
};

function routes(me: Me, overrides: Record<string, RouteHandler> = {}): Record<string, RouteHandler> {
  return {
    "GET /api/v1/me": () => jsonResponse(200, me),
    [`GET ${BASE}/members`]: () => jsonResponse(200, memberPage([makeMember()])),
    [`GET ${BASE}/wbs/elements/101/dictionary`]: () => jsonResponse(200, makeDictionary()),
    [`GET ${BASE}/estimates/ELEMENT/101/history`]: () => jsonResponse(200, estimateHistory([makeEstimateRecord()])),
    [`GET ${BASE}/wbs-codes/1.1/trace`]: () => jsonResponse(200, makeTrace()),
    [`GET ${BASE}/scope-items`]: () => jsonResponse(200, SCOPE_ITEMS),
    ...overrides,
  };
}

function renderAt(path = "/projects/3/wbs/101") {
  return render(
    <MemoryRouter initialEntries={[path]} future={FUTURE}>
      <Routes>
        <Route path="/projects/:projectId/wbs/:elementId" element={<WbsElementPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

function sent(calls: RecordedCall[], method: string, path: string) {
  return calls.filter((c) => c.method === method && c.path === path);
}

describe("UI-WBS-002 WBS辞書・横断照会", () => {
  beforeEach(() => {
    setAuthToken("t-planner");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    setAuthToken(null);
  });

  it("辞書の 8 項目を出し、WBS ID・名称・責任者・見積は要素の値を出す。完了条件の欠けた WP を示す（WBS-11・13）", async () => {
    mockApi(routes(meWith(["project.view"])));
    const { container } = renderAt();

    expect(container.querySelector('[data-screen-id="UI-WBS-002"]')).not.toBeNull();
    const dictionary = await screen.findByLabelText("WBS辞書");
    const terms = within(dictionary).getAllByRole("term").map((t) => t.textContent);
    expect(terms).toEqual(["WBS ID", "名称", "区分", "成果物", "作業範囲", "責任者", "完了条件", "見積工数", "前提条件"]);
    expect(dictionary).toHaveTextContent("1.1");
    expect(dictionary).toHaveTextContent("業務フロー図");
    expect(dictionary).toHaveTextContent("40 人時");
    expect(await within(dictionary).findByText("計画 太郎")).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent("完了条件が未定義のワークパッケージ");

    // project.view だけの人には書く部品を出さない（7.2）
    expect(screen.queryByRole("form", { name: "辞書を書く" })).toBeNull();
    expect(screen.queryByRole("form", { name: /見積を書く/ })).toBeNull();
    expect(screen.queryByRole("form", { name: "スコープ項目を紐付ける" })).toBeNull();
  });

  it("見積の根拠と履歴を出し、見積を書くと履歴を読み直す（API-EST-001・002・EST-08）", async () => {
    let records = [makeEstimateRecord()];
    const { calls } = mockApi(
      routes(meWith(PLANNER), {
        [`GET ${BASE}/estimates/ELEMENT/101/history`]: () => jsonResponse(200, estimateHistory(records)),
        [`PUT ${BASE}/estimates/ELEMENT/101`]: () => {
          const record = makeEstimateRecord({ id: 302, hours: 48, input_value: 48, input_unit: "HOUR", method: "EXPERT" });
          records = [...records, record];
          return jsonResponse(200, { record, current_hours: 48, wbs_status: "DRAFT", project_estimate_hours: 64 });
        },
      }),
    );
    renderAt();
    const history = await screen.findByRole("table", { name: "見積の履歴" });
    const first = within(history).getAllByRole("row")[1]!;
    expect(first).toHaveTextContent("40 人時");
    expect(first).toHaveTextContent("5 人日");
    expect(first).toHaveTextContent("類推");
    expect(first).toHaveTextContent("前回の更改と同じ規模");
    expect(first).toHaveTextContent("前回の実績表");
    expect(first).toHaveTextContent("計画 太郎");

    const form = await screen.findByRole("form", { name: "見積を書く（1.1 業務整理）" });
    fireEvent.change(within(form).getByLabelText("見積"), { target: { value: "48" } });
    fireEvent.change(within(form).getByLabelText("見積手法"), { target: { value: "EXPERT" } });
    fireEvent.click(within(form).getByRole("button", { name: "見積を保存する" }));

    expect(await screen.findByRole("status")).toHaveTextContent("見積を 48 人時にした（プロジェクトの総見積 64 人時）");
    expect(sent(calls, "PUT", `${BASE}/estimates/ELEMENT/101`)[0]!.body).toEqual({
      input_value: 48,
      input_unit: "HOUR",
      method: "EXPERT",
      assumptions: null,
      references: null,
    });
    await waitFor(() => expect(within(screen.getByRole("table", { name: "見積の履歴" })).getAllByRole("row")).toHaveLength(3));
  });

  it("辞書の 4 項目を書く（空欄は null。API-WBS-010）", async () => {
    const { calls } = mockApi(
      routes(meWith(PLANNER), {
        [`PUT ${BASE}/wbs/elements/101/dictionary`]: () =>
          jsonResponse(200, makeDictionary({ completion_criteria: "利用部門の承認", work_scope: null })),
      }),
    );
    renderAt();
    const form = await screen.findByRole("form", { name: "辞書を書く" });
    expect(within(form).getByLabelText("成果物")).toHaveValue("業務フロー図");

    fireEvent.change(within(form).getByLabelText("作業範囲"), { target: { value: "  " } });
    fireEvent.change(within(form).getByLabelText("完了条件"), { target: { value: "利用部門の承認" } });
    fireEvent.click(within(form).getByRole("button", { name: "辞書を保存する" }));

    expect(await screen.findByRole("status")).toHaveTextContent("辞書を保存した");
    expect(sent(calls, "PUT", `${BASE}/wbs/elements/101/dictionary`)[0]!.body).toEqual({
      deliverable: "業務フロー図",
      work_scope: null,
      completion_criteria: "利用部門の承認",
      assumptions: null,
    });
    expect(screen.queryByRole("note")).toBeNull();
  });

  it("スコープ項目の紐付けを選んだ項目で置き換え、400 の理由を出す（API-WBS-013・PRJ-02）", async () => {
    let fail = true;
    const { calls } = mockApi(
      routes(meWith(PLANNER), {
        [`PUT ${BASE}/wbs/elements/101/scope-links`]: () =>
          fail
            ? jsonResponse(400, {
                error: { code: "VALIDATION_ERROR", message: "入力に誤りがあります", details: [{ message: "無いスコープ項目です" }] },
              })
            : jsonResponse(200, { element_id: 101, scope_item_ids: [31] }),
      }),
    );
    renderAt();
    const form = await screen.findByRole("form", { name: "スコープ項目を紐付ける" });
    fireEvent.click(within(form).getByLabelText("対象内：受注業務"));
    fireEvent.click(within(form).getByRole("button", { name: "紐付けを保存する" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("無いスコープ項目です");

    fail = false;
    fireEvent.click(within(form).getByRole("button", { name: "紐付けを保存する" }));
    expect(await screen.findByRole("status")).toHaveTextContent("1 件に置き換えた");
    expect(screen.getByText(/紐付けた項目：/)).toHaveTextContent("受注業務");
    expect(sent(calls, "PUT", `${BASE}/wbs/elements/101/scope-links`).map((c) => c.body)).toEqual([
      { scope_item_ids: [31] },
      { scope_item_ids: [31] },
    ]);
  });

  it("開いた要素の WBS ID で横断照会し、旧 ID でも照会できる（API-WBS-023・WBS-07）", async () => {
    const { calls } = mockApi(
      routes(meWith(["project.view"]), {
        [`GET ${BASE}/wbs-codes/2.1/trace`]: () =>
          jsonResponse(200, makeTrace({ query: "2.1", resolved_by: "HISTORY", eac_hours: null, change_request_ids: [] })),
        [`GET ${BASE}/wbs-codes/9.9/trace`]: () => errorResponse(404, "NOT_FOUND", "WBS ID が見つかりません"),
      }),
    );
    renderAt();
    const result = await screen.findByLabelText("横断照会の結果");
    expect(result).toHaveTextContent("2 件（#21、#22）");
    expect(result).toHaveTextContent("12.5 人時（記録 3 件）");
    expect(result).toHaveTextContent("44 人時");
    expect(within(result).getByRole("link", { name: "変更要求へ" })).toHaveAttribute("href", "/projects/3/change-requests");
    expect(within(result).getByRole("link", { name: "1.1 業務整理" })).toHaveAttribute("href", "/projects/3/wbs/101");

    const form = screen.getByRole("form", { name: "WBS ID で照会する" });
    fireEvent.change(within(form).getByLabelText(/WBS ID/), { target: { value: "2.1" } });
    fireEvent.click(within(form).getByRole("button", { name: "照会する" }));
    expect(await screen.findByText(/「2\.1」を旧 ID（新旧対応から解決）として、今の ID「1\.1」の要素に解決した/)).toBeInTheDocument();
    expect(screen.getByLabelText("横断照会の結果")).toHaveTextContent("—（読めない）");

    fireEvent.change(within(form).getByLabelText(/WBS ID/), { target: { value: "9.9" } });
    fireEvent.click(within(form).getByRole("button", { name: "照会する" }));
    expect(await screen.findByText("WBS ID が見つかりません")).toBeInTheDocument();
    expect(screen.queryByLabelText("横断照会の結果")).toBeNull();
    expect(sent(calls, "GET", `${BASE}/wbs-codes/1.1/trace`)).toHaveLength(1);
  });

  it("要素が無ければ（404）その文言を出す", async () => {
    mockApi(
      routes(meWith(["project.view"]), {
        [`GET ${BASE}/wbs/elements/101/dictionary`]: () => errorResponse(404, "NOT_FOUND", "WBS要素が見つかりません"),
        [`GET ${BASE}/estimates/ELEMENT/101/history`]: () => errorResponse(404, "NOT_FOUND", "見積の対象が見つかりません"),
      }),
    );
    renderAt();
    expect(await screen.findByText("WBS要素が見つかりません")).toBeInTheDocument();
    expect(screen.getByText("見積の記録はまだありません")).toBeInTheDocument();
  });
});

describe("missingCompletion", () => {
  it("WP の成果物・完了条件の空を返し、WP 以外は問わない（WBS-13）", () => {
    expect(missingCompletion(makeDictionary({ deliverable: " ", completion_criteria: null }))).toEqual(["成果物", "完了条件"]);
    expect(missingCompletion(makeDictionary({ completion_criteria: "承認" }))).toEqual([]);
    expect(missingCompletion(makeDictionary({ kind: "DELIVERABLE", deliverable: null }))).toEqual([]);
  });
});
