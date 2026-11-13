import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setAuthToken } from "../../api/client";
import type { Me, PendingApproval } from "../../api/cmn";
import { makeMe, makePendingApproval, makeUser } from "../../test/fixtures";
import { errorResponse, jsonResponse, mockApi, pagedResponse, type RecordedCall } from "../../test/mockApi";
import ApprovalsPage from "./ApprovalsPage";

/** プロジェクト 3 で APPROVER（wbs.approve は持つが change.approve は持たない。9.2 の既定） */
const APPROVER_ME: Me = makeMe({
  user: makeUser({ id: 4, login_id: "approver", display_name: "承認 一郎", is_system_admin: false }),
  system_permissions: [],
  projects: [
    { project_id: 3, roles: ["APPROVER"], permissions: ["project.view", "wbs.approve", "wbs.review"], view_level: "ALL" },
    { project_id: 5, roles: ["PM"], permissions: ["project.view", "wbs.approve", "change.approve"], view_level: "ALL" },
  ],
});

const WBS_APPROVAL = makePendingApproval({ id: 10, project_id: 3, target_type: "WBS", target_id: 3 });
const CR_APPROVAL = makePendingApproval({
  id: 11,
  project_id: 5,
  target_type: "CHANGE_REQUEST",
  target_id: 42,
  project_code: "P-005",
  project_name: "物流刷新",
  requested_by_name: "PM 花子",
});
const CR_NO_RIGHT = makePendingApproval({ id: 12, project_id: 3, target_type: "CHANGE_REQUEST", target_id: 43 });

function pendingPage(items: PendingApproval[]) {
  return { items, total: items.length, limit: 100, offset: 0 };
}

function listCalls(calls: RecordedCall[]) {
  return calls.filter((c) => c.method === "GET" && c.path === "/api/v1/approval-requests");
}

function renderPage() {
  return render(
    <MemoryRouter>
      <ApprovalsPage />
    </MemoryRouter>,
  );
}

describe("UI-CMN-003 承認待ち一覧", () => {
  beforeEach(() => {
    setAuthToken("t-approver");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    setAuthToken(null);
  });

  it("承認待ちを描画し、対象の画面への行き先を出す", async () => {
    const { calls } = mockApi({
      "GET /api/v1/me": () => jsonResponse(200, APPROVER_ME),
      "GET /api/v1/approval-requests": () => jsonResponse(200, pendingPage([WBS_APPROVAL, CR_APPROVAL])),
    });

    const { container } = renderPage();

    const table = await screen.findByRole("table", { name: "承認待ちの一覧" });
    expect(container.querySelector('[data-screen-id="UI-CMN-003"]')).not.toBeNull();
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(3);
    expect(rows[1]).toHaveTextContent("P-001 基幹更改");
    expect(rows[1]).toHaveTextContent("計画 太郎");
    expect(within(rows[1]!).getByRole("link", { name: "WBS" })).toHaveAttribute("href", "/projects/3/wbs-review");
    expect(within(rows[2]!).getByRole("link", { name: "変更要求" })).toHaveAttribute(
      "href",
      "/projects/5/change-requests",
    );
    // WBS は「差戻し」、変更要求は「却下」（WBS-21・CHG-03）
    expect(within(rows[1]!).getByRole("button", { name: "差戻し" })).toBeInTheDocument();
    expect(within(rows[2]!).getByRole("button", { name: "却下" })).toBeInTheDocument();
    expect(listCalls(calls)[0]?.url).toBe("/api/v1/approval-requests?limit=1000&offset=0");
  });

  it("1000 件を超える承認待ちも total まで頁を読み足して全件を出す（6.1・A-49 の 2）", async () => {
    const all = Array.from({ length: 1001 }, (_, i) =>
      makePendingApproval({ id: 1000 + i, project_id: 3, target_type: "WBS", target_id: 3 }),
    );
    const { calls } = mockApi({
      "GET /api/v1/me": () => jsonResponse(200, APPROVER_ME),
      "GET /api/v1/approval-requests": (call) => pagedResponse(all, call),
    });

    const { container } = renderPage();

    expect(await screen.findByText("全 1001 件")).toBeInTheDocument();
    expect(container.querySelectorAll("tbody tr")).toHaveLength(1001);
    expect(container.querySelector('input[aria-label="申請 2000 のコメント"]')).not.toBeNull();
    expect(listCalls(calls).map((c) => c.url)).toEqual([
      "/api/v1/approval-requests?limit=1000&offset=0",
      "/api/v1/approval-requests?limit=1000&offset=1000",
    ]);
  });

  it("判断の権限の無い申請には判断の部品を出さない", async () => {
    mockApi({
      "GET /api/v1/me": () => jsonResponse(200, APPROVER_ME),
      "GET /api/v1/approval-requests": () => jsonResponse(200, pendingPage([CR_NO_RIGHT])),
    });

    renderPage();

    const table = await screen.findByRole("table", { name: "承認待ちの一覧" });
    expect(table).toHaveTextContent("判断の権限がありません");
    expect(within(table).queryByRole("button")).not.toBeInTheDocument();
  });

  it("システム管理者は全ての申請を判断できる", async () => {
    mockApi({
      "GET /api/v1/me": () => jsonResponse(200, makeMe({ projects: [] })),
      "GET /api/v1/approval-requests": () => jsonResponse(200, pendingPage([CR_NO_RIGHT])),
    });

    renderPage();

    expect(await screen.findByRole("button", { name: "承認" })).toBeInTheDocument();
  });

  it("コメントを付けて承認し（API-WBS-019）、一覧を読み直す", async () => {
    let pending = [WBS_APPROVAL];
    const { calls } = mockApi({
      "GET /api/v1/me": () => jsonResponse(200, APPROVER_ME),
      "GET /api/v1/approval-requests": () => jsonResponse(200, pendingPage(pending)),
      "POST /api/v1/approval-requests/10/decision": () => {
        pending = [];
        return jsonResponse(200, { ...WBS_APPROVAL, status: "APPROVED", wbs_status: "APPROVED", scope_baseline: null });
      },
    });

    renderPage();
    fireEvent.change(await screen.findByLabelText("申請 10 のコメント"), { target: { value: "問題ありません" } });
    fireEvent.click(screen.getByRole("button", { name: "承認" }));

    expect(await screen.findByRole("status")).toHaveTextContent("P-001 のWBSの申請を承認しました");
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ decision: "APPROVE", comment: "問題ありません" });
    expect(await screen.findByText("承認待ちはありません")).toBeInTheDocument();
    expect(listCalls(calls)).toHaveLength(2);
  });

  it("変更要求を却下する（コメントが空欄なら null）", async () => {
    const { calls } = mockApi({
      "GET /api/v1/me": () => jsonResponse(200, APPROVER_ME),
      "GET /api/v1/approval-requests": () => jsonResponse(200, pendingPage([CR_APPROVAL])),
      "POST /api/v1/approval-requests/11/decision": () =>
        jsonResponse(200, { ...CR_APPROVAL, status: "REJECTED", wbs_status: null, scope_baseline: null }),
    });

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "却下" }));

    expect(await screen.findByRole("status")).toHaveTextContent("P-005 の変更要求の申請を却下しました");
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ decision: "REJECT", comment: null });
  });

  it("自分の申請（403）・判断済み（409）はサーバの文言を出し、一覧を読み直す", async () => {
    const { calls } = mockApi({
      "GET /api/v1/me": () => jsonResponse(200, APPROVER_ME),
      "GET /api/v1/approval-requests": () => jsonResponse(200, pendingPage([WBS_APPROVAL])),
      "POST /api/v1/approval-requests/10/decision": () =>
        errorResponse(403, "SELF_APPROVAL_FORBIDDEN", "自分の申請は判断できません"),
    });

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "差戻し" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("自分の申請は判断できません");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    await waitFor(() => expect(listCalls(calls)).toHaveLength(2));
  });

  it("取り下げで消えた申請（404。API-CHG-011）の判断は、サーバの文言を出して一覧を読み直す", async () => {
    let pending = [CR_APPROVAL];
    const { calls } = mockApi({
      "GET /api/v1/me": () => jsonResponse(200, APPROVER_ME),
      "GET /api/v1/approval-requests": () => jsonResponse(200, pendingPage(pending)),
      "POST /api/v1/approval-requests/11/decision": () => {
        // 判断の前に申請者が取り下げ、承認申請の行が消えている
        pending = [];
        return errorResponse(404, "NOT_FOUND", "承認申請が見つかりません");
      },
    });

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "承認" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("承認申請が見つかりません");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(await screen.findByText("承認待ちはありません")).toBeInTheDocument();
    expect(listCalls(calls)).toHaveLength(2);
  });

  it("承認待ちが無ければそう伝える", async () => {
    mockApi({
      "GET /api/v1/me": () => jsonResponse(200, APPROVER_ME),
      "GET /api/v1/approval-requests": () => jsonResponse(200, pendingPage([])),
    });

    renderPage();

    expect(await screen.findByText("承認待ちはありません")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("読めなければサーバの文言を出す", async () => {
    mockApi({
      "GET /api/v1/me": () => jsonResponse(200, APPROVER_ME),
      "GET /api/v1/approval-requests": () => errorResponse(500, "INTERNAL_ERROR", "サーバの内部で誤りが起きました"),
    });

    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("サーバの内部で誤りが起きました");
  });
});
