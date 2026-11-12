import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { CorrectiveAction } from "../../api/chg";
import { setAuthToken } from "../../api/client";
import type { Me } from "../../api/cmn";
import type { WbsElement } from "../../api/wbs";
import { makeCorrectiveAction, memberPage } from "../../test/chgFixtures";
import { makeMe, makeMember, makeUser } from "../../test/fixtures";
import { errorResponse, jsonResponse, mockApi, type RecordedCall, type RouteHandler } from "../../test/mockApi";
import { activityList, makeActivity, makeStatusReport } from "../../test/schFixtures";
import CorrectiveActionsPage from "./CorrectiveActionsPage";

const FUTURE = { v7_startTransition: true, v7_relativeSplatPath: true };
const BASE = "/api/v1/projects/3";

function meWith(permissions: string[]): Me {
  return makeMe({
    user: makeUser({ id: 5, is_system_admin: false }),
    system_permissions: [],
    projects: [{ project_id: 3, roles: ["PROGRESS_MANAGER"], permissions, view_level: "ALL" }],
  });
}

const MANAGER = ["project.view", "action.manage"];

function element(overrides: Partial<WbsElement>): WbsElement {
  return {
    id: 100,
    parent_id: null,
    kind: "DELIVERABLE",
    name: "システム",
    wbs_code: "1",
    sibling_seq: 1,
    sort_order: 1,
    depth: 1,
    own_estimate_hours: null,
    rollup_hours: 0,
    rollup_source: "NONE",
    responsible_user_id: null,
    responsible_team: null,
    needs_renumber: false,
    ...overrides,
  };
}

const A1 = makeCorrectiveAction();
const A2 = makeCorrectiveAction({
  id: 82,
  element_id: null,
  activity_id: 21,
  wbs_code: "1.1",
  cause: "レビューの指摘が多い",
  action: "レビューの観点を先に配る",
  owner_user_id: null,
  due_date: "2026-11-01",
  status: "IN_PROGRESS",
  overdue: true,
});

function page(items: CorrectiveAction[]) {
  return { items, total: items.length, limit: 100, offset: 0 };
}

function routes(me: Me, overrides: Record<string, RouteHandler> = {}): Record<string, RouteHandler> {
  return {
    "GET /api/v1/me": () => jsonResponse(200, me),
    [`GET ${BASE}/members`]: () =>
      jsonResponse(200, memberPage([makeMember({ user_id: 2, display_name: "進捗 花子" })])),
    [`GET ${BASE}/corrective-actions`]: () => jsonResponse(200, page([A1, A2])),
    [`GET ${BASE}/wbs`]: () =>
      jsonResponse(200, {
        project_id: 3,
        wbs_status: "BASELINED",
        project_estimate_hours: 0,
        renumber_pending: 0,
        restricted: false,
        elements: [
          element({}),
          element({ id: 102, parent_id: 100, kind: "WORK_PACKAGE", name: "設計", wbs_code: "1.2", depth: 2 }),
        ],
      }),
    [`GET ${BASE}/activities`]: () => jsonResponse(200, activityList([makeActivity()])),
    [`GET ${BASE}/status-reports`]: () =>
      jsonResponse(200, { items: [makeStatusReport({ id: 41, report_date: "2026-11-06" })], total: 1, limit: 1000, offset: 0 }),
    ...overrides,
  };
}

function renderAt(path = "/projects/3/corrective-actions") {
  return render(
    <MemoryRouter initialEntries={[path]} future={FUTURE}>
      <Routes>
        <Route path="/projects/:projectId/corrective-actions" element={<CorrectiveActionsPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

function sent(calls: RecordedCall[], method: string, path: string) {
  return calls.filter((c) => c.method === method && c.path === path);
}

describe("UI-CHG-002 是正措置", () => {
  beforeEach(() => {
    setAuthToken("t-pmgr");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    setAuthToken(null);
  });

  it("是正措置の一覧を WBS ID・原因・対策・担当・期限・状態・効果で出し、期限超過を示す（CHG-06）", async () => {
    mockApi(routes(meWith(["project.view"])));
    const { container } = renderAt();

    expect(container.querySelector('[data-screen-id="UI-CHG-002"]')).not.toBeNull();
    const table = await screen.findByRole("table", { name: "是正措置" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("1.2");
    expect(rows[0]).toHaveTextContent("仕様の確定が遅れた");
    expect(rows[0]).toHaveTextContent("仕様の確認会を週 2 回にする");
    expect(await within(rows[0]!).findByText("進捗 花子")).toBeInTheDocument();
    expect(rows[0]).toHaveTextContent("2026-11-20");
    expect(rows[0]).toHaveTextContent("未着手");
    expect(rows[0]).not.toHaveTextContent("期限超過");
    expect(rows[1]).toHaveTextContent("対応中");
    expect(rows[1]).toHaveTextContent("2026-11-01（期限超過）");
    expect(rows[1]).toHaveAttribute("data-overdue", "true");
  });

  it("状態・期限超過・WBS ID で絞り込む（API-CHG-008）", async () => {
    const { calls } = mockApi(routes(meWith(["project.view"])));
    renderAt();
    await screen.findByRole("table", { name: "是正措置" });

    fireEvent.change(screen.getByLabelText("状態"), { target: { value: "IN_PROGRESS" } });
    fireEvent.click(screen.getByLabelText("期限超過だけ"));
    fireEvent.change(screen.getByLabelText(/^WBS ID/), { target: { value: " 1.2 " } });
    fireEvent.click(screen.getByRole("button", { name: "絞り込む" }));

    await waitFor(() => {
      const last = sent(calls, "GET", `${BASE}/corrective-actions`).at(-1)!;
      const query = new URLSearchParams(last.url.split("?")[1]);
      expect(query.get("status")).toBe("IN_PROGRESS");
      expect(query.get("overdue")).toBe("true");
      expect(query.get("wbs_code")).toBe("1.2");
    });
  });

  it("project.view だけの人には、登録・更新を出さず、選択肢も読まない（7.2）", async () => {
    const { calls } = mockApi(routes(meWith(["project.view"])));
    renderAt();
    await screen.findByRole("table", { name: "是正措置" });
    await waitFor(() => expect(sent(calls, "GET", "/api/v1/me")).toHaveLength(1));

    expect(screen.queryByRole("form", { name: "是正措置を登録する" })).toBeNull();
    expect(screen.queryByRole("button", { name: /を更新する$/ })).toBeNull();
    expect(sent(calls, "GET", `${BASE}/wbs`)).toHaveLength(0);
    expect(sent(calls, "GET", `${BASE}/activities`)).toHaveLength(0);
  });

  it("action.manage を持つ人は、要素・活動・報告日に紐付けて登録できる（API-CHG-007）", async () => {
    const created = makeCorrectiveAction({ id: 83 });
    const { calls } = mockApi(
      routes(meWith(MANAGER), {
        [`POST ${BASE}/corrective-actions`]: () => jsonResponse(201, created),
      }),
    );
    renderAt();

    const form = await screen.findByRole("form", { name: "是正措置を登録する" });
    fireEvent.change(within(form).getByLabelText("WBS の要素"), { target: { value: "102" } });
    fireEvent.change(within(form).getByLabelText("活動"), { target: { value: "21" } });
    fireEvent.change(within(form).getByLabelText("報告日"), { target: { value: "41" } });
    fireEvent.change(within(form).getByLabelText("原因"), { target: { value: "仕様の確定が遅れた" } });
    fireEvent.change(within(form).getByLabelText("対策"), { target: { value: "確認会を増やす" } });
    await within(form).findByRole("option", { name: "進捗 花子" });
    fireEvent.change(within(form).getByLabelText("担当"), { target: { value: "2" } });
    fireEvent.change(within(form).getByLabelText("期限"), { target: { value: "2026-11-20" } });
    fireEvent.click(within(form).getByRole("button", { name: "登録する" }));

    expect(await screen.findByRole("status")).toHaveTextContent("是正措置 #83 を登録した（未着手）");
    const posts = sent(calls, "POST", `${BASE}/corrective-actions`);
    expect(posts).toHaveLength(1);
    expect(posts[0]!.body).toEqual({
      element_id: 102,
      activity_id: 21,
      status_report_id: 41,
      cause: "仕様の確定が遅れた",
      action: "確認会を増やす",
      owner_user_id: 2,
      due_date: "2026-11-20",
    });
  });

  it("要素にも活動にも紐付けない・原因や対策が空なら送らずに理由を出す", async () => {
    const { calls } = mockApi(routes(meWith(MANAGER)));
    renderAt();

    const form = await screen.findByRole("form", { name: "是正措置を登録する" });
    fireEvent.click(within(form).getByRole("button", { name: "登録する" }));

    const alert = within(form).getByRole("alert");
    expect(alert).toHaveTextContent("WBS の要素か活動の少なくとも一方に紐付けてください");
    expect(alert).toHaveTextContent("原因を入力してください");
    expect(alert).toHaveTextContent("対策を入力してください");
    expect(sent(calls, "POST", `${BASE}/corrective-actions`)).toHaveLength(0);
  });

  it("状態は RULE-CHG-005 の遷移だけを選べ、効果を書いて更新できる（API-CHG-009）", async () => {
    const updated = { ...A2, status: "DONE" as const, effect: "指摘が半減した", overdue: false };
    const { calls } = mockApi(
      routes(meWith(MANAGER), {
        [`PATCH ${BASE}/corrective-actions/82`]: () => jsonResponse(200, updated),
      }),
    );
    renderAt();

    fireEvent.click(await screen.findByRole("button", { name: "是正措置 #82 を更新する" }));
    const form = screen.getByRole("form", { name: "是正措置 #82 を更新する" });
    const status = within(form).getByLabelText("状態");
    expect(within(status).getAllByRole("option").map((o) => o.textContent)).toEqual(["対応中", "完了"]);
    fireEvent.change(status, { target: { value: "DONE" } });
    fireEvent.change(within(form).getByLabelText("効果"), { target: { value: "指摘が半減した" } });
    fireEvent.click(within(form).getByRole("button", { name: "更新する" }));

    expect(await screen.findByRole("status")).toHaveTextContent("是正措置 #82 を更新した（完了）");
    const patches = sent(calls, "PATCH", `${BASE}/corrective-actions/82`);
    expect(patches).toHaveLength(1);
    expect(patches[0]!.body).toEqual({ status: "DONE", effect: "指摘が半減した", owner_user_id: null, due_date: "2026-11-01" });
    expect(screen.queryByRole("form", { name: "是正措置 #82 を更新する" })).toBeNull();
  });

  it("未着手からは対応中へだけ進められる", async () => {
    mockApi(routes(meWith(MANAGER)));
    renderAt();

    fireEvent.click(await screen.findByRole("button", { name: "是正措置 #81 を更新する" }));
    const form = screen.getByRole("form", { name: "是正措置 #81 を更新する" });
    const options = within(within(form).getByLabelText("状態")).getAllByRole("option");
    expect(options.map((o) => o.textContent)).toEqual(["未着手", "対応中"]);
  });

  it("遷移できない（409）ときはサーバの文言を出す", async () => {
    mockApi(
      routes(meWith(MANAGER), {
        [`PATCH ${BASE}/corrective-actions/81`]: () => errorResponse(409, "INVALID_STATE", "その状態へは移れません"),
      }),
    );
    renderAt();

    fireEvent.click(await screen.findByRole("button", { name: "是正措置 #81 を更新する" }));
    const form = screen.getByRole("form", { name: "是正措置 #81 を更新する" });
    fireEvent.click(within(form).getByRole("button", { name: "更新する" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("その状態へは移れません");
  });

  it("ガント・変更要求への行き先を出す", async () => {
    mockApi(routes(meWith(["project.view"])));
    renderAt();
    await screen.findByRole("table", { name: "是正措置" });
    expect(screen.getByRole("link", { name: "ガントチャート" })).toHaveAttribute("href", "/projects/3/gantt");
    expect(screen.getByRole("link", { name: "変更要求" })).toHaveAttribute("href", "/projects/3/change-requests");
  });

  it("プロジェクトの ID が読めなければ、API を呼ばずに知らせる", () => {
    const { calls } = mockApi({});
    renderAt("/projects/0/corrective-actions");
    expect(screen.getByRole("alert")).toHaveTextContent("プロジェクトが見つかりません");
    expect(calls).toHaveLength(0);
  });
});
