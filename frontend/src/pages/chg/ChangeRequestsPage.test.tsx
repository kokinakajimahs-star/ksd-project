import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ChangeRequest } from "../../api/chg";
import { setAuthToken } from "../../api/client";
import type { Me } from "../../api/cmn";
import { makeChangeRequest, makeImpact, makeImpactBody, memberPage } from "../../test/chgFixtures";
import { makeMe, makeMember, makeUser } from "../../test/fixtures";
import {
  errorResponse,
  jsonResponse,
  mockApi,
  pagedResponse,
  type RecordedCall,
  type RouteHandler,
} from "../../test/mockApi";
import { makeActivity } from "../../test/schFixtures";
import ChangeRequestsPage, { canAnalyze } from "./ChangeRequestsPage";

const BASE = "/api/v1/projects/3";

function meWith(permissions: string[]): Me {
  return makeMe({
    user: makeUser({ id: 5, is_system_admin: false }),
    system_permissions: [],
    projects: [{ project_id: 3, roles: ["PLANNER"], permissions, view_level: "ALL" }],
  });
}

const PLANNER = ["project.view", "change.create"];
const PM = ["project.view", "change.create", "change.approve", "change.apply"];

const DRAFT = makeChangeRequest();
const SUBMITTED = makeChangeRequest({
  id: 72,
  number: 2,
  title: "日程の見直し",
  status: "SUBMITTED",
  submitted_at: "2026-11-10T02:00:00Z",
  approval_request_id: 501,
  impact: makeImpactBody({ project_finish: { before: "2026-09-30", after: "2026-09-29", shift_days: -1 } }),
});
const APPROVED = makeChangeRequest({ id: 73, number: 3, title: "承認済みの変更", status: "APPROVED" });

function page(items: ChangeRequest[]) {
  return { items, total: items.length, limit: 100, offset: 0 };
}

/** 依存の端に選べる今ある活動（API-SCH-001） */
const ACTIVITIES = [makeActivity({ id: 21, wbs_code: "1.1", name: "資料作成" }), makeActivity({ id: 22, wbs_code: "1.2", name: "設計" })];

function routes(me: Me, overrides: Record<string, RouteHandler> = {}): Record<string, RouteHandler> {
  return {
    "GET /api/v1/me": () => jsonResponse(200, me),
    [`GET ${BASE}/members`]: () =>
      jsonResponse(200, memberPage([makeMember({ user_id: 2, display_name: "計画 太郎" })])),
    [`GET ${BASE}/change-requests`]: () => jsonResponse(200, page([DRAFT, SUBMITTED, APPROVED])),
    [`GET ${BASE}/activities`]: (call) => pagedResponse(ACTIVITIES, call, { dependencies: [], restricted: false }),
    ...overrides,
  };
}

function renderAt(path = "/projects/3/change-requests") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/projects/:projectId/change-requests" element={<ChangeRequestsPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

function sent(calls: RecordedCall[], method: string, path: string) {
  return calls.filter((c) => c.method === method && c.path === path);
}

async function openRequest(number: number) {
  fireEvent.click(await screen.findByRole("button", { name: `変更要求 #${number} を開く` }));
  return screen.findByRole("heading", { name: new RegExp(`^変更要求 #${number}「`) });
}

describe("UI-CHG-001 変更要求（作成・影響分析・提出・反映）", () => {
  beforeEach(() => {
    setAuthToken("t-planner");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    setAuthToken(null);
  });

  it("変更要求の一覧を番号・件名・状態・対象・作成者で出す", async () => {
    mockApi(routes(meWith(PLANNER)));
    const { container } = renderAt();

    expect(container.querySelector('[data-screen-id="UI-CHG-001"]')).not.toBeNull();
    const table = await screen.findByRole("table", { name: "変更要求" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent("#1");
    expect(rows[0]).toHaveTextContent("設計の作業を足す");
    expect(rows[0]).toHaveTextContent("下書き");
    expect(rows[0]).toHaveTextContent("1.2");
    expect(await within(rows[0]!).findByText("計画 太郎")).toBeInTheDocument();
    expect(rows[1]).toHaveTextContent("提出済み（承認待ち）");
    expect(rows[2]).toHaveTextContent("承認済み");
  });

  it("状態で絞り込むと status を付けて読み直す（API-CHG-002）", async () => {
    const { calls } = mockApi(routes(meWith(PLANNER)));
    renderAt();
    await screen.findByRole("table", { name: "変更要求" });

    fireEvent.change(screen.getByLabelText("状態で絞り込む"), { target: { value: "APPROVED" } });
    await waitFor(() => {
      expect(calls.some((c) => c.method === "GET" && c.url.includes("status=APPROVED"))).toBe(true);
    });
  });

  it("change.create を持つ人は、対象・理由・変更内容（5.9 の操作）を付けて下書きを作れる（API-CHG-001）", async () => {
    const created = makeChangeRequest({ id: 74, number: 4, title: "WP を足す", target_codes: ["1.2", "1.3"] });
    const { calls } = mockApi(
      routes(meWith(PLANNER), {
        [`POST ${BASE}/change-requests`]: () => jsonResponse(201, created),
      }),
    );
    renderAt();

    fireEvent.click(await screen.findByRole("button", { name: "変更要求を作る" }));
    const form = screen.getByRole("form", { name: "変更要求を作る" });
    fireEvent.change(within(form).getByLabelText("件名"), { target: { value: "WP を足す" } });
    fireEvent.change(within(form).getByLabelText("理由"), { target: { value: "範囲の追加" } });
    fireEvent.change(within(form).getByLabelText(/対象の WBS ID/), { target: { value: "1.2, 1.3" } });

    // 操作 1：要素を足す（WP）
    fireEvent.change(within(form).getByLabelText("親の WBS ID（空ならルート）"), { target: { value: "1" } });
    fireEvent.change(within(form).getByLabelText(/^区分/), { target: { value: "WORK_PACKAGE" } });
    fireEvent.change(within(form).getByLabelText(/^名称/), { target: { value: "追加の WP" } });
    fireEvent.change(within(form).getByLabelText(/^仮の名前/), { target: { value: "e1" } });
    fireEvent.click(within(form).getByRole("button", { name: "操作を足す" }));

    // 操作 2：その WP に活動を足す
    fireEvent.change(within(form).getByLabelText("操作"), { target: { value: "add_activity" } });
    fireEvent.change(within(form).getByLabelText("置き場の指定"), { target: { value: "ref" } });
    fireEvent.change(within(form).getByLabelText("置き場の WP の仮の名前"), { target: { value: "e1" } });
    fireEvent.change(within(form).getByLabelText(/^名称/), { target: { value: "追加の設計" } });
    fireEvent.change(within(form).getByLabelText(/^期間（稼働日）/), { target: { value: "3" } });
    fireEvent.change(within(form).getByLabelText(/^見積（人時）/), { target: { value: "24" } });
    fireEvent.change(within(form).getByLabelText(/^仮の名前/), { target: { value: "a1" } });
    fireEvent.click(within(form).getByRole("button", { name: "操作を足す" }));

    const ops = within(form).getByRole("list", { name: "変更内容の操作" });
    expect(within(ops).getAllByRole("listitem")).toHaveLength(2);
    expect(ops).toHaveTextContent("要素を足す：1 の下に「追加の WP」（ワークパッケージ、仮の名前 e1）");

    fireEvent.click(within(form).getByRole("button", { name: "下書きとして保存する" }));

    expect(await screen.findByRole("status")).toHaveTextContent("変更要求 #4「WP を足す」を下書きとして作った");
    const posts = sent(calls, "POST", `${BASE}/change-requests`);
    expect(posts).toHaveLength(1);
    expect(posts[0]!.body).toEqual({
      title: "WP を足す",
      reason: "範囲の追加",
      target_codes: ["1.2", "1.3"],
      operations: [
        { op: "add_element", parent_code: "1", kind: "WORK_PACKAGE", name: "追加の WP", ref: "e1" },
        { op: "add_activity", wp_ref: "e1", name: "追加の設計", duration_days: 3, estimate_hours: 24, ref: "a1" },
      ],
    });
    expect(screen.queryByRole("form", { name: "変更要求を作る" })).toBeNull();
  });

  it("依存の端は、今ある活動（一覧から選ぶ → 整数の ID）か仮の名前（文字列。数字だけでも）かを選んで指す（OQ-38 (2)）", async () => {
    // 今ある活動は 1000 件を超えても全件を選択肢に出す（2 頁目の活動 #4000 を選ぶ）
    const many = [...ACTIVITIES, ...Array.from({ length: 999 }, (_, i) => makeActivity({ id: 3001 + i, name: `活動${i}` }))];
    const all = [...many, makeActivity({ id: 4000, wbs_code: "2.1", name: "移行" })];
    const created = makeChangeRequest({ id: 75, number: 5, title: "依存を足す" });
    const { calls } = mockApi(
      routes(meWith(PLANNER), {
        [`GET ${BASE}/activities`]: (call) => pagedResponse(all, call, { dependencies: [], restricted: false }),
        [`POST ${BASE}/change-requests`]: () => jsonResponse(201, created),
      }),
    );
    renderAt();

    fireEvent.click(await screen.findByRole("button", { name: "変更要求を作る" }));
    const form = screen.getByRole("form", { name: "変更要求を作る" });
    fireEvent.change(within(form).getByLabelText("件名"), { target: { value: "依存を足す" } });
    fireEvent.change(within(form).getByLabelText("理由"), { target: { value: "順序の見直し" } });
    fireEvent.change(within(form).getByLabelText(/対象の WBS ID/), { target: { value: "1.1" } });
    fireEvent.change(within(form).getByLabelText("操作"), { target: { value: "add_dependency" } });
    expect(within(form).queryByText(/数字だけの入力は ID とみなす/)).toBeNull();

    // 先行：今ある活動（既定の指し方）を一覧から選ぶ
    expect(within(form).getByLabelText("先行の指し方")).toHaveValue("id");
    const predecessor = within(form).getByLabelText("先行の活動");
    await waitFor(() => expect(predecessor.querySelectorAll("option")).toHaveLength(1 + all.length));
    fireEvent.change(predecessor, { target: { value: "4000" } });
    // 後続：この変更要求で足す活動の仮の名前（数字だけ）
    fireEvent.change(within(form).getByLabelText("後続の指し方"), { target: { value: "ref" } });
    expect(within(form).queryByLabelText("後続の活動")).toBeNull();
    fireEvent.change(within(form).getByLabelText("後続の仮の名前"), { target: { value: "21" } });
    fireEvent.click(within(form).getByRole("button", { name: "操作を足す" }));

    const ops = within(form).getByRole("list", { name: "変更内容の操作" });
    expect(ops).toHaveTextContent("依存を足す：活動 #4000 → 「21」");
    fireEvent.click(within(form).getByRole("button", { name: "下書きとして保存する" }));

    await screen.findByRole("status");
    expect((sent(calls, "POST", `${BASE}/change-requests`)[0]!.body as { operations: unknown[] }).operations).toEqual([
      { op: "add_dependency", predecessor: 4000, successor: "21" },
    ]);
    expect(sent(calls, "GET", `${BASE}/activities`).map((c) => c.url)).toEqual([
      `${BASE}/activities?limit=1000&offset=0`,
      `${BASE}/activities?limit=1000&offset=1000`,
    ]);
  });

  it("今ある活動の一覧を読めなければ理由を出し、仮の名前では指せる", async () => {
    mockApi(
      routes(meWith(PLANNER), {
        [`GET ${BASE}/activities`]: () => errorResponse(403, "FORBIDDEN", "このプロジェクトを見る権限がありません"),
      }),
    );
    renderAt();

    fireEvent.click(await screen.findByRole("button", { name: "変更要求を作る" }));
    const form = screen.getByRole("form", { name: "変更要求を作る" });
    fireEvent.change(within(form).getByLabelText("操作"), { target: { value: "add_dependency" } });
    expect(await within(form).findByRole("alert")).toHaveTextContent("このプロジェクトを見る権限がありません");
    expect(within(form).getByLabelText("先行の活動")).toBeDisabled();

    fireEvent.change(within(form).getByLabelText("先行の指し方"), { target: { value: "ref" } });
    fireEvent.change(within(form).getByLabelText("先行の仮の名前"), { target: { value: "a1" } });
    fireEvent.change(within(form).getByLabelText("後続の指し方"), { target: { value: "ref" } });
    fireEvent.change(within(form).getByLabelText("後続の仮の名前"), { target: { value: "a2" } });
    fireEvent.click(within(form).getByRole("button", { name: "操作を足す" }));
    expect(within(form).getByRole("list", { name: "変更内容の操作" })).toHaveTextContent("依存を足す：「a1」 → 「a2」");
  });

  it("件名・理由・対象が空なら送らずに理由を出す", async () => {
    const { calls } = mockApi(routes(meWith(PLANNER)));
    renderAt();

    fireEvent.click(await screen.findByRole("button", { name: "変更要求を作る" }));
    const form = screen.getByRole("form", { name: "変更要求を作る" });
    fireEvent.click(within(form).getByRole("button", { name: "下書きとして保存する" }));

    const alert = within(form).getByRole("alert");
    expect(alert).toHaveTextContent("件名を入力してください");
    expect(alert).toHaveTextContent("理由を入力してください");
    expect(alert).toHaveTextContent("対象の WBS ID を 1 つ以上入力してください");
    expect(sent(calls, "POST", `${BASE}/change-requests`)).toHaveLength(0);
  });

  it("操作の必須の欄が欠けていれば足さずに理由を出す", async () => {
    mockApi(routes(meWith(PLANNER)));
    renderAt();

    fireEvent.click(await screen.findByRole("button", { name: "変更要求を作る" }));
    const form = screen.getByRole("form", { name: "変更要求を作る" });
    fireEvent.click(within(form).getByRole("button", { name: "操作を足す" }));
    expect(within(form).getByRole("alert")).toHaveTextContent("区分を選んでください");
    expect(within(form).getByText("操作はまだありません")).toBeInTheDocument();
  });

  it("サーバが変更内容の誤りを返したら、その文言を出す（422）", async () => {
    mockApi(
      routes(meWith(PLANNER), {
        [`POST ${BASE}/change-requests`]: () => errorResponse(422, "REFERENCE_NOT_FOUND", "WBS ID 9.9 が無い"),
      }),
    );
    renderAt();

    fireEvent.click(await screen.findByRole("button", { name: "変更要求を作る" }));
    const form = screen.getByRole("form", { name: "変更要求を作る" });
    fireEvent.change(within(form).getByLabelText("件名"), { target: { value: "x" } });
    fireEvent.change(within(form).getByLabelText("理由"), { target: { value: "y" } });
    fireEvent.change(within(form).getByLabelText(/対象の WBS ID/), { target: { value: "9.9" } });
    fireEvent.click(within(form).getByRole("button", { name: "下書きとして保存する" }));

    expect(await within(form).findByRole("alert")).toHaveTextContent("WBS ID 9.9 が無い");
  });

  it("下書きを開くと、変更内容を出し、直す（API-CHG-003）・提出する（API-CHG-005）を出す", async () => {
    const edited = { ...DRAFT, title: "設計の作業を足す（改）" };
    const submitted = { ...DRAFT, status: "SUBMITTED" as const, submitted_at: "2026-11-12T01:00:00Z", approval_request_id: 502 };
    // サーバの状態：直す・提出するたびに一覧の値も変わる
    let current: ChangeRequest = DRAFT;
    const { calls } = mockApi(
      routes(meWith(PLANNER), {
        [`GET ${BASE}/change-requests`]: () => jsonResponse(200, page([current, SUBMITTED, APPROVED])),
        [`PATCH ${BASE}/change-requests/71`]: () => {
          current = edited;
          return jsonResponse(200, edited);
        },
        [`POST ${BASE}/change-requests/71/submit`]: () => {
          current = submitted;
          return jsonResponse(200, submitted);
        },
      }),
    );
    renderAt();

    await openRequest(1);
    expect(screen.getByRole("list", { name: "変更内容" })).toHaveTextContent("活動を消す：活動 #21（依存も消す）");

    fireEvent.click(screen.getByRole("button", { name: "直す" }));
    const form = screen.getByRole("form", { name: "変更要求 #1 を直す" });
    expect(within(form).getByLabelText("件名")).toHaveValue("設計の作業を足す");
    fireEvent.change(within(form).getByLabelText("件名"), { target: { value: "設計の作業を足す（改）" } });
    fireEvent.click(within(form).getByRole("button", { name: "保存する" }));

    expect(await screen.findByRole("status")).toHaveTextContent("変更要求 #1 を保存した");
    const patches = sent(calls, "PATCH", `${BASE}/change-requests/71`);
    expect(patches).toHaveLength(1);
    expect(patches[0]!.body).toEqual({
      title: "設計の作業を足す（改）",
      reason: "顧客の追加要望",
      target_codes: ["1.2"],
      operations: [{ op: "remove_activity", activity_id: 21 }],
    });

    fireEvent.click(screen.getByRole("button", { name: "提出する" }));
    expect(await screen.findByText(/変更要求 #1 を提出した/)).toBeInTheDocument();
    expect(sent(calls, "POST", `${BASE}/change-requests/71/submit`)).toHaveLength(1);
    // 提出した後は直せない・提出できない（5.10）
    expect(screen.queryByRole("button", { name: "直す" })).toBeNull();
    expect(screen.queryByRole("button", { name: "提出する" })).toBeNull();
  });

  it("影響分析を流すと、終了日・マイルストーン・クリティカルパス・総工数・要素の差を出す（API-CHG-004。CHG-02）", async () => {
    const { calls } = mockApi(
      routes(meWith(["project.view"]), {
        [`POST ${BASE}/change-requests/71/impact`]: () => jsonResponse(200, makeImpact(71)),
      }),
    );
    renderAt();

    await openRequest(1);
    fireEvent.click(screen.getByRole("button", { name: "影響分析を流す" }));

    const impact = await screen.findByRole("region", { name: "影響分析（試算。本計画は変えない）" });
    expect(impact).toHaveTextContent("2026-09-30 → 2026-10-05（+3 稼働日）");
    expect(impact).toHaveTextContent("400.00 人時 → 424.50 人時（+24.5 人時）");
    const milestones = within(impact).getByRole("table", { name: "ずれるマイルストーン" });
    expect(within(milestones).getAllByRole("row")[1]).toHaveTextContent("設計完了2026-06-302026-07-03+3 稼働日");
    const critical = within(impact).getByRole("list", { name: "クリティカルパスの変化" });
    expect(critical).toHaveTextContent("加わる：追加の設計（新規 a1）");
    expect(critical).toHaveTextContent("外れる：旧レビュー（#22）");
    const elements = within(impact).getByRole("list", { name: "要素の変化" });
    expect(elements).toHaveTextContent("追加：新規 e1 追加の WP（ワークパッケージ）");
    expect(elements).toHaveTextContent("変更：1.2 設計（own_estimate_hours: 8.00 → 16.00）");
    expect(sent(calls, "POST", `${BASE}/change-requests/71/impact`)).toHaveLength(1);
  });

  it("project.view だけの人には、作る・直す・提出する・反映するを出さない（7.2）", async () => {
    mockApi(routes(meWith(["project.view"])));
    renderAt();

    await openRequest(1);
    expect(screen.queryByRole("button", { name: "変更要求を作る" })).toBeNull();
    expect(screen.queryByRole("button", { name: "直す" })).toBeNull();
    expect(screen.queryByRole("button", { name: "提出する" })).toBeNull();
    expect(screen.getByRole("button", { name: "影響分析を流す" })).toBeInTheDocument();

    await openRequest(3);
    expect(screen.queryByRole("button", { name: "反映する（再基準化）" })).toBeNull();
  });

  it("提出済みは承認待ち一覧へ案内し、提出のときの影響分析を出す", async () => {
    mockApi(routes(meWith(PM)));
    renderAt();

    await openRequest(2);
    const detail = screen.getByRole("heading", { name: /^変更要求 #2「/ }).closest("section")!;
    expect(within(detail).getByRole("link", { name: "承認待ち一覧" })).toHaveAttribute("href", "/approvals");
    expect(within(detail).queryByRole("button", { name: "提出する" })).toBeNull();
    expect(within(detail).queryByRole("button", { name: "反映する（再基準化）" })).toBeNull();
    expect(within(detail).getByRole("region", { name: "提出のときの影響分析" })).toHaveTextContent("-1 稼働日");
  });

  it("change.create を持つ人は提出済みを取り下げ、下書きに戻して一覧を読み直す（API-CHG-011。CHG-03）", async () => {
    const withdrawn: ChangeRequest = { ...SUBMITTED, status: "DRAFT", submitted_at: null, approval_request_id: null };
    let current: ChangeRequest = SUBMITTED;
    const { calls } = mockApi(
      routes(meWith(PLANNER), {
        [`GET ${BASE}/change-requests`]: () => jsonResponse(200, page([DRAFT, current, APPROVED])),
        [`POST ${BASE}/change-requests/72/withdraw`]: () => {
          current = withdrawn;
          return jsonResponse(200, withdrawn);
        },
      }),
    );
    renderAt();

    await openRequest(2);
    // 下書き・承認済みには取り下げを出さない
    expect(screen.queryByRole("button", { name: "直す" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "提出を取り下げる" }));

    expect(await screen.findByRole("status")).toHaveTextContent(
      "変更要求 #2 の提出を取り下げた（下書きに戻した。承認申請は取り消した）",
    );
    expect(sent(calls, "POST", `${BASE}/change-requests/72/withdraw`)).toHaveLength(1);
    expect(sent(calls, "POST", `${BASE}/change-requests/72/withdraw`)[0]!.body).toBeUndefined();
    await waitFor(() => expect(sent(calls, "GET", `${BASE}/change-requests`)).toHaveLength(2));
    const row = within(screen.getByRole("table", { name: "変更要求" })).getAllByRole("row")[2]!;
    expect(row).toHaveTextContent("下書き");
    // 下書きに戻ったので、直す・提出するを出し、取り下げは出さない。提出のときの影響分析は残る
    expect(screen.getByRole("button", { name: "直す" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "提出する" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "提出を取り下げる" })).toBeNull();
    expect(screen.getByRole("region", { name: "提出のときの影響分析" })).toHaveTextContent("-1 稼働日");

    await openRequest(1);
    expect(screen.queryByRole("button", { name: "提出を取り下げる" })).toBeNull();
    await openRequest(3);
    expect(screen.queryByRole("button", { name: "提出を取り下げる" })).toBeNull();
  });

  it("change.create の無い人には取り下げを出さない（7.2）", async () => {
    mockApi(routes(meWith(["project.view", "change.approve"])));
    renderAt();

    await openRequest(2);
    expect(screen.getByRole("link", { name: "承認待ち一覧" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "提出を取り下げる" })).toBeNull();
  });

  it("取り下げられない（409。先に判断された など）ときはサーバの文言を出す", async () => {
    mockApi(
      routes(meWith(PLANNER), {
        [`POST ${BASE}/change-requests/72/withdraw`]: () => errorResponse(409, "INVALID_STATE", "提出済みではない"),
      }),
    );
    renderAt();

    await openRequest(2);
    fireEvent.click(screen.getByRole("button", { name: "提出を取り下げる" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("提出済みではない");
  });

  it("change.apply を持つ人は承認済みを反映でき、新しい版を知らせる（API-CHG-006。CHG-04）", async () => {
    const appliedRequest: ChangeRequest = {
      ...APPROVED,
      status: "APPLIED",
      applied_by: 5,
      applied_at: "2026-11-12T03:00:00Z",
    };
    const applied = {
      change_request: appliedRequest,
      scope_baseline: { id: 11, version: 2, reason: "CHANGE_REQUEST" },
      schedule_baseline: { id: 63, version: 3, reason: "CHANGE_REQUEST" },
    };
    let current: ChangeRequest = APPROVED;
    const { calls } = mockApi(
      routes(meWith(PM), {
        [`GET ${BASE}/change-requests`]: () => jsonResponse(200, page([DRAFT, SUBMITTED, current])),
        [`POST ${BASE}/change-requests/73/apply`]: () => {
          current = appliedRequest;
          return jsonResponse(200, applied);
        },
      }),
    );
    renderAt();

    await openRequest(3);
    fireEvent.click(screen.getByRole("button", { name: "反映する（再基準化）" }));

    expect(await screen.findByRole("status")).toHaveTextContent(
      "変更要求 #3 を反映した。スコープ基準の第 2 版とスケジュール基準の第 3 版を作った（旧版は残る）",
    );
    expect(sent(calls, "POST", `${BASE}/change-requests/73/apply`)).toHaveLength(1);
    // 反映した後は、影響分析も反映も出さない
    expect(screen.queryByRole("button", { name: "反映する（再基準化）" })).toBeNull();
    expect(screen.queryByRole("button", { name: "影響分析を流す" })).toBeNull();
  });

  it("状態が合わない（409）ときはサーバの文言を出す", async () => {
    mockApi(
      routes(meWith(PM), {
        [`POST ${BASE}/change-requests/73/apply`]: () => errorResponse(409, "INVALID_STATE", "承認済みではない"),
      }),
    );
    renderAt();

    await openRequest(3);
    fireEvent.click(screen.getByRole("button", { name: "反映する（再基準化）" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("承認済みではない");
  });

  it("一覧を読めない（403）ときはサーバの文言を出す", async () => {
    mockApi(
      routes(meWith([]), {
        [`GET ${BASE}/change-requests`]: () => errorResponse(403, "FORBIDDEN", "権限がありません"),
      }),
    );
    renderAt();
    expect(await screen.findByRole("alert")).toHaveTextContent("権限がありません");
  });

  it("プロジェクトの ID が読めなければ、API を呼ばずに知らせる", () => {
    const { calls } = mockApi({});
    const { container } = renderAt("/projects/abc/change-requests");
    expect(container.querySelector('[data-screen-id="UI-CHG-001"]')).not.toBeNull();
    expect(screen.getByRole("alert")).toHaveTextContent("プロジェクトが見つかりません");
    expect(calls).toHaveLength(0);
  });

  it("影響分析を流せるのは、反映済み・却下でないとき", () => {
    expect(canAnalyze("DRAFT")).toBe(true);
    expect(canAnalyze("SUBMITTED")).toBe(true);
    expect(canAnalyze("APPROVED")).toBe(true);
    expect(canAnalyze("APPLIED")).toBe(false);
    expect(canAnalyze("REJECTED")).toBe(false);
  });
});
