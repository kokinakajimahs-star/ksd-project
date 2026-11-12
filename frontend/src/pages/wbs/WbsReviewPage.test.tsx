import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, setAuthToken } from "../../api/client";
import type { Me } from "../../api/cmn";
import { makeMe, makeMember, makePendingApproval, makeUser } from "../../test/fixtures";
import { memberPage } from "../../test/chgFixtures";
import { errorResponse, jsonResponse, mockApi, type RecordedCall, type RouteHandler } from "../../test/mockApi";
import { makeComment, makeReview, makeScopeBaselineSummary, makeTree, makeValidation } from "../../test/wbsFixtures";
import WbsReviewPage, { describeApprovalError, toleranceText } from "./WbsReviewPage";

const FUTURE = { v7_startTransition: true, v7_relativeSplatPath: true };
const BASE = "/api/v1/projects/3";

function meWith(permissions: string[], id = 5): Me {
  return makeMe({
    user: makeUser({ id, is_system_admin: false }),
    system_permissions: [],
    projects: [{ project_id: 3, roles: ["PLANNER"], permissions, view_level: "ALL" }],
  });
}

const PLANNER = ["project.view", "wbs.edit", "wbs.review", "estimate.edit"];
const REVIEWER = ["project.view", "wbs.review"];
const APPROVER = ["project.view", "wbs.approve", "wbs.review"];

const SCOPE_ITEMS = {
  project_id: 3,
  items: [{ id: 31, kind: "IN", title: "受注業務", description: null, sort_order: 1 }],
  total: 1,
  limit: 1000,
  offset: 0,
};

function routes(me: Me, overrides: Record<string, RouteHandler> = {}): Record<string, RouteHandler> {
  return {
    "GET /api/v1/me": () => jsonResponse(200, me),
    [`GET ${BASE}/members`]: () =>
      jsonResponse(200, memberPage([makeMember(), makeMember({ user_id: 4, login_id: "rev", display_name: "査読 花子" })])),
    [`GET ${BASE}/scope-items`]: () => jsonResponse(200, SCOPE_ITEMS),
    [`GET ${BASE}/wbs`]: () => jsonResponse(200, makeTree()),
    [`GET ${BASE}/wbs/validation`]: () => jsonResponse(200, makeValidation()),
    [`GET ${BASE}/scope-baselines`]: () => jsonResponse(200, { project_id: 3, items: [], total: 0, limit: 50, offset: 0 }),
    [`GET ${BASE}/wbs/reviews/50`]: () => jsonResponse(200, makeReview()),
    ...overrides,
  };
}

function LocationProbe() {
  const location = useLocation();
  return <p data-testid="location">{`${location.pathname}${location.search}`}</p>;
}

function renderAt(path = "/projects/3/wbs-review") {
  return render(
    <MemoryRouter initialEntries={[path]} future={FUTURE}>
      <Routes>
        <Route
          path="/projects/:projectId/wbs-review"
          element={
            <>
              <WbsReviewPage />
              <LocationProbe />
            </>
          }
        />
      </Routes>
    </MemoryRouter>,
  );
}

function sent(calls: RecordedCall[], method: string, path: string) {
  return calls.filter((c) => c.method === method && c.path === path);
}

describe("UI-WBS-003 WBSの検証・レビュー・承認", () => {
  beforeEach(() => {
    setAuthToken("t-user");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    setAuthToken(null);
  });

  it("100% ルールの判定と、成果物・完了条件の欠けた WP を出す（WBS-16・13）", async () => {
    mockApi(routes(meWith(["project.view"])));
    const { container } = renderAt();

    expect(container.querySelector('[data-screen-id="UI-WBS-003"]')).not.toBeNull();
    const findings = await screen.findByRole("table", { name: "100% ルールの検査の結果" });
    const rows = within(findings).getAllByRole("row").slice(1);
    expect(rows[0]).toHaveTextContent("警告");
    expect(rows[0]).toHaveTextContent("合計の差異");
    expect(within(rows[0]!).getByRole("link", { name: "1.0" })).toHaveAttribute("href", "/projects/3/wbs/100");
    expect(rows[0]).toHaveTextContent("80 人時");
    expect(rows[0]).toHaveTextContent("56 人時");
    expect(rows[1]).toHaveTextContent("エラー");
    expect(rows[1]).toHaveTextContent("対象内の漏れ");
    expect(await within(rows[1]!).findByText("受注業務")).toBeInTheDocument();
    expect(screen.getByText(/合計の差異の許容幅 5%/)).toBeInTheDocument();

    const missing = screen.getByRole("list", { name: "成果物・完了条件の欠けたワークパッケージ" });
    expect(missing).toHaveTextContent("1.1：完了条件が未定義");
    expect(within(missing).getByRole("link", { name: "1.1" })).toHaveAttribute("href", "/projects/3/wbs/101");
    expect(screen.getByText("WBS の状態：作成中")).toBeInTheDocument();

    // project.view だけの人には申請・レビューの開始・指摘の部品を出さない（7.2）
    expect(screen.queryByRole("button", { name: "承認を申請する" })).toBeNull();
    expect(screen.queryByRole("form", { name: "レビューを始める" })).toBeNull();
    expect(screen.getByText(/開いているレビューはありません/)).toBeInTheDocument();
  });

  it("レビューを始めると、頼む人を送って始めたレビューを URL で開く（API-WBS-014・015・WBS-20）", async () => {
    const { calls } = mockApi(
      routes(meWith(PLANNER), {
        [`POST ${BASE}/wbs/reviews`]: () => jsonResponse(201, makeReview({ comments: [] })),
        [`GET ${BASE}/wbs/reviews/50`]: () => jsonResponse(200, makeReview({ comments: [] })),
      }),
    );
    renderAt();
    const form = await screen.findByRole("form", { name: "レビューを始める" });
    fireEvent.click(within(form).getByRole("button", { name: "レビューを始める" }));
    expect(await within(screen.getByRole("region", { name: "レビュー" })).findByRole("alert")).toHaveTextContent("1 人以上");

    fireEvent.click(await within(form).findByLabelText("査読 花子"));
    fireEvent.click(within(form).getByRole("button", { name: "レビューを始める" }));

    expect(await screen.findByText("レビュー #50（実施中）")).toBeInTheDocument();
    expect(screen.getByTestId("location")).toHaveTextContent("/projects/3/wbs-review?review=50");
    expect(sent(calls, "POST", `${BASE}/wbs/reviews`).map((c) => c.body)).toEqual([{ reviewer_ids: [4] }]);
    expect(screen.getByText(/レビューする人：査読 花子/)).toBeInTheDocument();
    // 始めた後は WBS の状態を読み直す
    await waitFor(() => expect(sent(calls, "GET", `${BASE}/wbs`).length).toBeGreaterThanOrEqual(2));
  });

  it("wbs.review を持つ人は指摘を書け、状態の更新の部品は wbs.edit が無いので出さない（API-WBS-016）", async () => {
    let comments = [makeComment()];
    const { calls } = mockApi(
      routes(meWith(REVIEWER, 4), {
        [`GET ${BASE}/wbs/reviews/50`]: () => jsonResponse(200, makeReview({ comments })),
        [`POST ${BASE}/wbs/reviews/50/comments`]: (call) => {
          const added = makeComment({ id: 502, element_id: null, category: "AMBIGUITY", body: (call.body as { body: string }).body });
          comments = [...comments, added];
          return jsonResponse(201, added);
        },
      }),
    );
    renderAt("/projects/3/wbs-review?review=50");
    const table = await screen.findByRole("table", { name: "指摘" });
    expect(within(table).getAllByRole("row")[1]).toHaveTextContent("1.1 業務整理");
    expect(within(table).getAllByRole("row")[1]).toHaveTextContent("漏れ");
    expect(within(table).getAllByRole("row")[1]).toHaveTextContent("未対応");
    expect(within(table).queryByRole("combobox")).toBeNull();

    const form = screen.getByRole("form", { name: "指摘を書く" });
    fireEvent.change(within(form).getByLabelText("分類"), { target: { value: "AMBIGUITY" } });
    fireEvent.change(within(form).getByLabelText("内容"), { target: { value: "範囲が曖昧" } });
    fireEvent.click(within(form).getByRole("button", { name: "指摘を書く" }));

    await waitFor(() => expect(within(screen.getByRole("table", { name: "指摘" })).getAllByRole("row")).toHaveLength(3));
    expect(sent(calls, "POST", `${BASE}/wbs/reviews/50/comments`).map((c) => c.body)).toEqual([
      { element_id: null, category: "AMBIGUITY", body: "範囲が曖昧" },
    ]);
    expect(within(screen.getByRole("table", { name: "指摘" })).getAllByRole("row")[2]).toHaveTextContent("WBS 全体");
  });

  it("wbs.edit を持つ人は指摘の状態と対応を更新でき、閉じたレビューでは書けない（API-WBS-017）", async () => {
    let closed = false;
    const { calls } = mockApi(
      routes(meWith(PLANNER), {
        [`GET ${BASE}/wbs/reviews/50`]: () =>
          jsonResponse(200, closed ? makeReview({ status: "CLOSED", closed_at: "2026-11-11T00:00:00Z" }) : makeReview()),
        [`PATCH ${BASE}/wbs/reviews/50/comments/501`]: () => {
          closed = true;
          return jsonResponse(200, makeComment({ status: "RESOLVED", resolution: "移行を足した" }));
        },
      }),
    );
    renderAt("/projects/3/wbs-review?review=50");
    const status = await screen.findByLabelText("指摘 #501 の状態");
    fireEvent.change(status, { target: { value: "RESOLVED" } });
    fireEvent.change(screen.getByLabelText("指摘 #501 の対応"), { target: { value: "移行を足した" } });
    fireEvent.click(screen.getByRole("button", { name: "指摘 #501 を更新する" }));

    expect(await screen.findByText("レビュー #50（終了）")).toBeInTheDocument();
    expect(sent(calls, "PATCH", `${BASE}/wbs/reviews/50/comments/501`).map((c) => c.body)).toEqual([
      { status: "RESOLVED", resolution: "移行を足した" },
    ]);
    expect(screen.queryByLabelText("指摘 #501 の状態")).toBeNull();
    expect(screen.queryByRole("form", { name: "指摘を書く" })).toBeNull();
  });

  it("承認の申請：前提を満たさなければ前提ごとの件数を出し、満たせば申請する（API-WBS-018・WBS-21・13）", async () => {
    let ready = false;
    const { calls } = mockApi(
      routes(meWith(PLANNER), {
        [`POST ${BASE}/wbs/approval-requests`]: () =>
          ready
            ? jsonResponse(201, { ...makePendingApproval({ id: 12 }), wbs_status: "PENDING_APPROVAL" })
            : jsonResponse(422, {
                error: {
                  code: "WBS_NOT_READY",
                  message: "承認を申請する前提を満たしていません",
                  details: [
                    { condition: "missing_completion", count: 1 },
                    { condition: "open_comments", count: 2 },
                  ],
                },
              }),
      }),
    );
    renderAt();
    const button = await screen.findByRole("button", { name: "承認を申請する" });
    fireEvent.click(button);
    const alert = await within(screen.getByRole("region", { name: "承認" })).findByRole("alert");
    expect(alert).toHaveTextContent("承認を申請する前提を満たしていません");
    expect(alert).toHaveTextContent("成果物・完了条件が未定義の WP：1 件");
    expect(alert).toHaveTextContent("未対応の指摘：2 件");

    ready = true;
    fireEvent.click(button);
    expect(await screen.findByRole("status")).toHaveTextContent("承認を申請した（申請 #12。WBS の状態：承認待ち）");
    expect(sent(calls, "POST", `${BASE}/wbs/approval-requests`)).toHaveLength(2);
  });

  it("wbs.approve を持つ人には、このプロジェクトの WBS の申請だけを出して判断させる（API-WBS-019・020）", async () => {
    let decided = false;
    const { calls } = mockApi(
      routes(meWith(APPROVER), {
        "GET /api/v1/approval-requests": () =>
          jsonResponse(200, {
            items: decided
              ? []
              : [
                  makePendingApproval({ id: 10 }),
                  makePendingApproval({ id: 11, target_type: "CHANGE_REQUEST", target_id: 71 }),
                  makePendingApproval({ id: 13, project_id: 5, target_id: 5 }),
                ],
            total: decided ? 0 : 3,
            limit: 1000,
            offset: 0,
          }),
        "POST /api/v1/approval-requests/10/decision": () => {
          decided = true;
          return jsonResponse(200, {});
        },
        [`GET ${BASE}/scope-baselines`]: () =>
          jsonResponse(200, {
            project_id: 3,
            items: decided ? [makeScopeBaselineSummary()] : [],
            total: decided ? 1 : 0,
            limit: 50,
            offset: 0,
          }),
      }),
    );
    renderAt();
    const table = await screen.findByRole("table", { name: "判断を待つ WBS の申請" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveTextContent("#10");
    expect(rows[0]).toHaveTextContent("計画 太郎");
    expect(screen.queryByRole("button", { name: "承認を申請する" })).toBeNull();

    fireEvent.change(within(rows[0]!).getByLabelText("申請 #10 へのコメント"), { target: { value: "妥当" } });
    fireEvent.click(within(rows[0]!).getByRole("button", { name: "承認" }));

    expect(await screen.findByRole("status")).toHaveTextContent("申請 #10 を承認した");
    expect(sent(calls, "POST", "/api/v1/approval-requests/10/decision").map((c) => c.body)).toEqual([
      { decision: "APPROVE", comment: "妥当" },
    ]);
    expect(await screen.findByText("このプロジェクトの WBS で判断を待つ申請はありません")).toBeInTheDocument();
    expect(await screen.findByRole("table", { name: "スコープベースラインの版" })).toHaveTextContent("初回の承認");
  });

  it("自分の申請の判断はサーバの 403 の文言を出す（WBS-21）", async () => {
    mockApi(
      routes(meWith(APPROVER), {
        "GET /api/v1/approval-requests": () =>
          jsonResponse(200, { items: [makePendingApproval({ id: 10 })], total: 1, limit: 1000, offset: 0 }),
        "POST /api/v1/approval-requests/10/decision": () =>
          errorResponse(403, "SELF_APPROVAL_FORBIDDEN", "自分の申請は判断できません"),
      }),
    );
    renderAt();
    fireEvent.click(await screen.findByRole("button", { name: "差戻し" }));
    expect(await screen.findByText("自分の申請は判断できません")).toBeInTheDocument();
  });

  it("スコープベースラインの版を選ぶと、写した時点の要素と辞書を出す（API-WBS-021・022・WBS-22）", async () => {
    mockApi(
      routes(meWith(["project.view"]), {
        [`GET ${BASE}/scope-baselines`]: () =>
          jsonResponse(200, {
            project_id: 3,
            items: [makeScopeBaselineSummary(), makeScopeBaselineSummary({ id: 82, version: 2, reason: "RE_APPROVAL" })],
            total: 2,
            limit: 50,
            offset: 0,
          }),
        [`GET ${BASE}/scope-baselines/82`]: () =>
          jsonResponse(200, {
            ...makeScopeBaselineSummary({ id: 82, version: 2, reason: "RE_APPROVAL", element_count: 1 }),
            restricted: false,
            elements: [
              {
                element_id: 101,
                parent_element_id: 100,
                wbs_code: "1.1",
                kind: "WORK_PACKAGE",
                name: "業務整理",
                own_estimate_hours: 40,
                dictionary: { deliverable: "業務フロー図", work_scope: null, completion_criteria: "承認", assumptions: null },
              },
            ],
          }),
      }),
    );
    renderAt();
    const versions = await screen.findByRole("table", { name: "スコープベースラインの版" });
    expect(within(versions).getAllByRole("row")[2]).toHaveTextContent("再承認");
    fireEvent.click(within(versions).getByRole("button", { name: "第 2 版を見る" }));

    const elements = await screen.findByRole("table", { name: "第 2 版の要素" });
    const row = within(elements).getAllByRole("row")[1]!;
    expect(row).toHaveTextContent("1.1");
    expect(row).toHaveTextContent("ワークパッケージ");
    expect(row).toHaveTextContent("40 人時");
    expect(row).toHaveTextContent("業務フロー図");
    expect(row).toHaveTextContent("承認");
  });
});

describe("UI-WBS-003 の部品の関数", () => {
  it("toleranceText は割合を百分率にする", () => {
    expect(toleranceText(0)).toBe("0%");
    expect(toleranceText(0.05)).toBe("5%");
    expect(toleranceText(0.125)).toBe("12.5%");
  });

  it("describeApprovalError は WBS_NOT_READY の前提を件数の行にし、他は文言の details のまま", () => {
    const notReady = new ApiError(422, {
      code: "WBS_NOT_READY",
      message: "前提を満たしていません",
      details: [{ condition: "hundred_percent_errors", count: 3 }, { condition: "unknown", count: 1 }],
    });
    expect(describeApprovalError(notReady)).toEqual({
      message: "前提を満たしていません",
      details: ["100% ルールのエラー：3 件", "unknown：1 件"],
    });
    const conflict = new ApiError(409, { code: "INVALID_STATE", message: "承認待ちです", details: [] });
    expect(describeApprovalError(conflict)).toEqual({ message: "承認待ちです", details: [] });
  });
});
