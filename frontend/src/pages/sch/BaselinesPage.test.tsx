import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setAuthToken } from "../../api/client";
import type { Me } from "../../api/cmn";
import type { Variance } from "../../api/sch";
import { makeMe, makeUser } from "../../test/fixtures";
import { errorResponse, jsonResponse, mockApi, type RecordedCall, type RouteHandler } from "../../test/mockApi";
import { makeScheduleBaseline } from "../../test/schFixtures";
import BaselinesPage, { latestBaseline, signed } from "./BaselinesPage";

const BASE = "/api/v1/projects/3";

function meWith(permissions: string[]): Me {
  return makeMe({
    user: makeUser({ id: 5, is_system_admin: false }),
    system_permissions: [],
    projects: [{ project_id: 3, roles: ["PM"], permissions, view_level: "ALL" }],
  });
}

const V1 = makeScheduleBaseline({ id: 61, version: 1, name: "当初計画" });
const V2 = makeScheduleBaseline({ id: 62, version: 2, name: "再計画", reason: "CHANGE_REQUEST", item_count: 5 });

function variance(baseline: { id: number; version: number; name: string }): Variance {
  return {
    baseline,
    rows: [
      {
        status: "MATCHED",
        activity_id: 21,
        wbs_code: "1.1",
        name: "資料作成",
        baseline_start: "2026-12-01",
        baseline_finish: "2026-12-14",
        current_start: "2026-12-03",
        current_finish: "2026-12-18",
        start_variance_days: 2,
        finish_variance_days: 4,
        baseline_estimate_hours: 40,
        current_estimate_hours: 36.5,
        estimate_variance_hours: -3.5,
      },
      {
        status: "ADDED",
        activity_id: 26,
        wbs_code: "1.2",
        name: "追加の調査",
        baseline_start: null,
        baseline_finish: null,
        current_start: "2026-12-15",
        current_finish: "2026-12-16",
        start_variance_days: null,
        finish_variance_days: null,
        baseline_estimate_hours: null,
        current_estimate_hours: 8,
        estimate_variance_hours: null,
      },
      {
        status: "DELETED",
        activity_id: null,
        wbs_code: "1.3",
        name: "消した作業",
        baseline_start: "2026-12-02",
        baseline_finish: "2026-12-04",
        current_start: null,
        current_finish: null,
        start_variance_days: null,
        finish_variance_days: null,
        baseline_estimate_hours: 16,
        current_estimate_hours: null,
        estimate_variance_hours: null,
      },
    ],
    restricted: false,
  };
}

function routes(me: Me, overrides: Record<string, RouteHandler> = {}): Record<string, RouteHandler> {
  return {
    "GET /api/v1/me": () => jsonResponse(200, me),
    [`GET ${BASE}/schedule-baselines`]: () => jsonResponse(200, { items: [V1, V2], total: 2, limit: 100, offset: 0 }),
    [`GET ${BASE}/schedule-baselines/61/variance`]: () => jsonResponse(200, variance(V1)),
    [`GET ${BASE}/schedule-baselines/62/variance`]: () => jsonResponse(200, variance(V2)),
    ...overrides,
  };
}

function renderAt(path = "/projects/3/baselines") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/projects/:projectId/baselines" element={<BaselinesPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

function sent(calls: RecordedCall[], method: string, path: string) {
  return calls.filter((c) => c.method === method && c.path === path);
}

describe("UI-SCH-003 ベースラインと差異", () => {
  beforeEach(() => {
    setAuthToken("t-pm");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    setAuthToken(null);
  });

  it("版の一覧を出し、既定で最新の版と最新計画の差異を出す（SCH-17・18）", async () => {
    const { calls } = mockApi(routes(meWith(["project.view"])));
    const { container } = renderAt();

    expect(container.querySelector('[data-screen-id="UI-SCH-003"]')).not.toBeNull();
    const versions = await screen.findByRole("table", { name: "ベースラインの版" });
    expect(within(versions).getAllByRole("row")).toHaveLength(3);
    expect(within(versions).getByText("変更要求の反映")).toBeInTheDocument();

    expect(await screen.findByRole("heading", { name: "第 2 版「再計画」と最新計画の差異" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "第 2 版と比べる" })).toBeChecked();
    expect(sent(calls, "GET", `${BASE}/schedule-baselines/61/variance`)).toHaveLength(0);
  });

  it("活動ごとに開始・終了の差（稼働日）と見積の差を出し、追加・削除を区別する", async () => {
    mockApi(routes(meWith(["project.view"])));
    renderAt();

    const table = await screen.findByRole("table", { name: "基準と最新計画の差異" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent("両方にある");
    expect(rows[0]).toHaveTextContent("+2 日");
    expect(rows[0]).toHaveTextContent("+4 日");
    expect(rows[0]).toHaveTextContent("-3.5 人時");
    expect(rows[1]).toHaveTextContent("追加された");
    expect(rows[1]).toHaveAttribute("data-status", "ADDED");
    expect(rows[2]).toHaveTextContent("削除された");
    expect(rows[2]).toHaveTextContent("消した作業");

    fireEvent.change(screen.getByLabelText("区別で絞り込む"), { target: { value: "DELETED" } });
    expect(within(table).getAllByRole("row").slice(1)).toHaveLength(1);
  });

  it("比べる版を選び直すと、その版の差異を読む", async () => {
    const { calls } = mockApi(routes(meWith(["project.view"])));
    renderAt();
    await screen.findByRole("heading", { name: "第 2 版「再計画」と最新計画の差異" });

    fireEvent.click(screen.getByRole("radio", { name: "第 1 版と比べる" }));

    expect(await screen.findByRole("heading", { name: "第 1 版「当初計画」と最新計画の差異" })).toBeInTheDocument();
    expect(sent(calls, "GET", `${BASE}/schedule-baselines/61/variance`)).toHaveLength(1);
  });

  it("baseline.manage を持つ人は版を保存できる（SCH-16）。保存した版を比べる", async () => {
    const V3 = makeScheduleBaseline({ id: 63, version: 3, name: "第3四半期" });
    let saved = false;
    const { calls } = mockApi(
      routes(meWith(["project.view", "baseline.manage"]), {
        [`POST ${BASE}/schedule-baselines`]: () => {
          saved = true;
          return jsonResponse(201, V3);
        },
        [`GET ${BASE}/schedule-baselines`]: () =>
          jsonResponse(200, { items: saved ? [V1, V2, V3] : [V1, V2], total: saved ? 3 : 2, limit: 100, offset: 0 }),
        [`GET ${BASE}/schedule-baselines/63/variance`]: () => jsonResponse(200, variance(V3)),
      }),
    );
    renderAt();
    const form = await screen.findByRole("form", { name: "ベースラインを保存する" });

    fireEvent.change(within(form).getByLabelText("版の名前"), { target: { value: " 第3四半期 " } });
    fireEvent.click(within(form).getByRole("button", { name: "保存する" }));

    expect(await screen.findByRole("status")).toHaveTextContent("第 3 版「第3四半期」を保存した");
    expect(sent(calls, "POST", `${BASE}/schedule-baselines`)[0]!.body).toEqual({ name: "第3四半期" });
    expect(await screen.findByRole("heading", { name: "第 3 版「第3四半期」と最新計画の差異" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByRole("radio")).toHaveLength(3));
  });

  it("版の名前が空なら送らない。サーバの拒否は理由を出す", async () => {
    const { calls } = mockApi(
      routes(meWith(["project.view", "baseline.manage"]), {
        [`POST ${BASE}/schedule-baselines`]: () => errorResponse(403, "FORBIDDEN", "権限がありません"),
      }),
    );
    renderAt();
    const form = await screen.findByRole("form", { name: "ベースラインを保存する" });

    fireEvent.click(within(form).getByRole("button", { name: "保存する" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("版の名前を入力してください");
    expect(sent(calls, "POST", `${BASE}/schedule-baselines`)).toHaveLength(0);

    fireEvent.change(within(form).getByLabelText("版の名前"), { target: { value: "x" } });
    fireEvent.click(within(form).getByRole("button", { name: "保存する" }));
    expect(await within(form).findByText("権限がありません")).toBeInTheDocument();
  });

  it("baseline.manage が無ければ保存の部品を出さない", async () => {
    mockApi(routes(meWith(["project.view"])));
    renderAt();
    await screen.findByRole("table", { name: "ベースラインの版" });
    expect(screen.queryByRole("form", { name: "ベースラインを保存する" })).toBeNull();
  });

  it("版が無ければその旨を出し、差異は読まない", async () => {
    const { calls } = mockApi(
      routes(meWith(["project.view"]), {
        [`GET ${BASE}/schedule-baselines`]: () => jsonResponse(200, { items: [], total: 0, limit: 100, offset: 0 }),
      }),
    );
    renderAt();
    expect(await screen.findByText("保存した版はまだありません")).toBeInTheDocument();
    expect(calls.some((c) => c.path.endsWith("/variance"))).toBe(false);
  });

  it("最新の版・差の表示", () => {
    expect(latestBaseline([V2, V1])?.id).toBe(62);
    expect(latestBaseline([])).toBeNull();
    expect(signed(2, " 日")).toBe("+2 日");
    expect(signed(-3.5, " 人時")).toBe("-3.5 人時");
    expect(signed(0, " 日")).toBe("0 日");
    expect(signed(null, " 日")).toBe("—");
  });
});
