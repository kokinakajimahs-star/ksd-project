import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setAuthToken } from "../../api/client";
import type { Me } from "../../api/cmn";
import type { WbsElement } from "../../api/wbs";
import { makeMe, makeMember, makeUser } from "../../test/fixtures";
import {
  errorResponse,
  jsonResponse,
  mockApi,
  pagedResponse,
  type RecordedCall,
  type RouteHandler,
} from "../../test/mockApi";
import { activityList, makeActivity } from "../../test/schFixtures";
import { makeEstimateRecord } from "../../test/wbsFixtures";
import { UNSAVED_EDITS_MESSAGE } from "../../lib/leaveGuard";
import ActivityListPage from "./ActivityListPage";
import { toCreateInput, type FormState } from "./activities/AddActivityForm";
import { toAssignmentInputs } from "./activities/AssignmentEditor";
import { assignmentTotals, collectChanges, draftOf, parseIdList } from "./activities/bulkEdit";

const BASE = "/api/v1/projects/3";

function meWith(permissions: string[]): Me {
  return makeMe({
    user: makeUser({ id: 5, is_system_admin: false }),
    system_permissions: [],
    projects: [{ project_id: 3, roles: ["PLANNER"], permissions, view_level: "ALL" }],
  });
}

const EDITOR = meWith(["project.view", "schedule.edit"]);
const VIEWER = meWith(["project.view"]);
/** 見積だけを書ける人（schedule.edit は持たない） */
const ESTIMATOR = meWith(["project.view", "estimate.edit"]);

const PLANNER = makeMember({ user_id: 2, display_name: "計画 太郎" });
const EXECUTOR = makeMember({ user_id: 9, login_id: "exec", display_name: "実行 花子", roles: ["EXECUTOR"] });

const A21 = makeActivity({
  id: 21,
  name: "資料作成",
  assignees: [{ user_id: 2, display_name: "計画 太郎", planned_hours: 24 }],
});
const A22 = makeActivity({
  id: 22,
  name: "関係者調整",
  requested_start: "2026-04-08",
  duration_days: 3,
  start_date: "2026-04-08",
  finish_date: "2026-04-10",
  predecessor_ids: [21],
  is_critical: false,
  total_float: 2,
});
const M23 = makeActivity({
  id: 23,
  name: "要件の承認",
  duration_days: 0,
  is_milestone: true,
  estimate_hours: null,
  predecessor_ids: [22],
});

function element(overrides: Partial<WbsElement>): WbsElement {
  return {
    id: 100,
    parent_id: null,
    kind: "DELIVERABLE",
    name: "基幹更改",
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

const TREE = {
  project_id: 3,
  wbs_status: "APPROVED",
  project_estimate_hours: 0,
  renumber_pending: 0,
  restricted: false,
  elements: [
    element({}),
    element({ id: 101, parent_id: 100, kind: "WORK_PACKAGE", name: "要件定義", wbs_code: "1.1", depth: 2 }),
    element({ id: 102, parent_id: 100, kind: "WORK_PACKAGE", name: "設計", wbs_code: "1.2", depth: 2 }),
  ],
};

function routes(me: Me, overrides: Record<string, RouteHandler> = {}): Record<string, RouteHandler> {
  return {
    "GET /api/v1/me": () => jsonResponse(200, me),
    [`GET ${BASE}/activities`]: () =>
      jsonResponse(
        200,
        activityList([A21, A22, M23], {
          dependencies: [
            { id: 31, predecessor_id: 21, successor_id: 22, dep_type: "FS", lag_days: 0 },
            { id: 32, predecessor_id: 22, successor_id: 23, dep_type: "FS", lag_days: 0 },
          ],
        }),
      ),
    [`GET ${BASE}/wbs`]: () => jsonResponse(200, TREE),
    [`GET ${BASE}/members`]: () =>
      jsonResponse(200, { project_id: 3, items: [PLANNER, EXECUTOR], total: 2, limit: 1000, offset: 0 }),
    ...overrides,
  };
}

function renderAt(path = "/projects/3/activities") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/projects/:projectId/activities" element={<ActivityListPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

function sent(calls: RecordedCall[], method: string, path: string) {
  return calls.filter((c) => c.method === method && c.path === path);
}

/** 一覧の頁（200 件ずつ）を読んだ要求 */
function pageReads(calls: RecordedCall[]) {
  return sent(calls, "GET", `${BASE}/activities`).filter((c) => c.url.includes("limit=200&"));
}

/** 依存の相手の選択肢のために全ての活動（limit 1000 で total まで）を読んだ要求 */
function allReads(calls: RecordedCall[]) {
  return sent(calls, "GET", `${BASE}/activities`).filter((c) => c.url.includes("limit=1000&"));
}

async function editable() {
  await screen.findByLabelText("21 の名称");
  // 担当の候補（API-PRJ-009）が届くまで待つ
  await waitFor(() => expect(within(screen.getByLabelText("21 の担当")).getAllByRole("option")).toHaveLength(2));
}

describe("UI-SCH-001 タスクリスト", () => {
  beforeEach(() => {
    setAuthToken("t-planner");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    setAuthToken(null);
  });

  it("活動を日程・依存・担当付きで一覧にする。先行の無い活動は並行可、マイルストーンは ◆（見るだけの人）", async () => {
    const { calls } = mockApi(routes(VIEWER));
    const { container } = renderAt();

    const table = await screen.findByRole("table", { name: "活動の一覧" });
    expect(container.querySelector('[data-screen-id="UI-SCH-001"]')).not.toBeNull();
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent("なし（並行可）");
    expect(rows[0]).toHaveTextContent("計画 太郎");
    expect(rows[0]).toHaveTextContent("（クリティカル）");
    expect(rows[1]).toHaveTextContent("2026-04-08");
    expect(within(rows[2]!).getByLabelText("マイルストーン")).toBeInTheDocument();

    // 見るだけの人には変える部品を出さず、WP・メンバーも読まない
    expect(screen.queryByRole("form", { name: "活動を足す" })).toBeNull();
    expect(screen.queryByRole("button", { name: "まとめて保存する" })).toBeNull();
    expect(screen.queryByRole("form", { name: "依存を足す" })).toBeNull();
    expect(screen.getByRole("table", { name: "依存の一覧" })).toHaveTextContent("21 資料作成");
    expect(sent(calls, "GET", `${BASE}/wbs`)).toHaveLength(0);
    expect(sent(calls, "GET", `${BASE}/members`)).toHaveLength(0);
  });

  it("日付・期間・先行・担当をまとめて編集し、変えた行の変えた項目だけを 1 回で保存する（SCH-02）", async () => {
    const { calls } = mockApi(
      routes(EDITOR, { [`PATCH ${BASE}/activities`]: () => jsonResponse(200, { items: [A21, A22] }) }),
    );
    renderAt();
    await editable();

    fireEvent.change(screen.getByLabelText("21 の希望開始日"), { target: { value: "2026-04-02" } });
    const assignees = screen.getByLabelText("21 の担当") as HTMLSelectElement;
    for (const option of Array.from(assignees.options)) {
      option.selected = true;
    }
    fireEvent.change(assignees);
    fireEvent.change(screen.getByLabelText("22 の期間"), { target: { value: "4" } });
    fireEvent.change(screen.getByLabelText("22 の先行"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "まとめて保存する" }));

    expect(await screen.findByRole("status")).toHaveTextContent("2 件の活動を保存し、日程を計算し直した");
    const patch = sent(calls, "PATCH", `${BASE}/activities`);
    expect(patch).toHaveLength(1);
    expect(patch[0]!.body).toEqual({
      changes: [
        { activity_id: 21, requested_start: "2026-04-02", assignee_user_ids: [2, 9] },
        { activity_id: 22, duration_days: 4, predecessor_ids: [] },
      ],
    });
    // 一覧の頁と、依存の相手の選択肢（全ての活動）を読み直す
    await waitFor(() => expect(pageReads(calls)).toHaveLength(2));
    await waitFor(() => expect(allReads(calls)).toHaveLength(2));
  });

  it("1 件でも不正ならサーバは何も保存しない。全ての理由を出す（422）", async () => {
    const { calls } = mockApi(
      routes(EDITOR, {
        [`PATCH ${BASE}/activities`]: () =>
          jsonResponse(422, {
            error: {
              code: "SCHEDULE_CYCLE",
              message: "保存できません（何も保存していません）",
              details: [{ message: "活動 21 と 22 の依存が循環しています" }, { message: "活動 23 の期間が不正です" }],
            },
          }),
      }),
    );
    renderAt();
    await editable();

    fireEvent.change(screen.getByLabelText("21 の先行"), { target: { value: "22" } });
    fireEvent.click(screen.getByRole("button", { name: "まとめて保存する" }));

    const alert = await screen.findByText("保存できません（何も保存していません）");
    expect(alert.closest("[role=alert]")).toHaveTextContent("活動 21 と 22 の依存が循環しています");
    expect(alert.closest("[role=alert]")).toHaveTextContent("活動 23 の期間が不正です");
    // 入力は残す（読み直さない）
    expect(screen.getByLabelText("21 の先行")).toHaveValue("22");
    expect(pageReads(calls)).toHaveLength(1);
    expect(allReads(calls)).toHaveLength(1);
  });

  it("送れない形の入力は送らずに理由を出す", async () => {
    const { calls } = mockApi(routes(EDITOR));
    renderAt();
    await editable();

    fireEvent.change(screen.getByLabelText("22 の期間"), { target: { value: "2.5" } });
    fireEvent.change(screen.getByLabelText("22 の先行"), { target: { value: "21;abc" } });
    fireEvent.click(screen.getByRole("button", { name: "まとめて保存する" }));

    const alert = await screen.findByText("入力に誤りがあります（何も保存していません）");
    expect(alert.closest("[role=alert]")).toHaveTextContent("期間は 0 以上");
    expect(alert.closest("[role=alert]")).toHaveTextContent("先行は活動 ID を ; で区切って");
    expect(sent(calls, "PATCH", `${BASE}/activities`)).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "編集を取り消す" }));
    expect(screen.getByLabelText("22 の期間")).toHaveValue("3");
  });

  it("WP の配下に活動を足す。開始日と期間を入れ、残りはサーバが求める（SCH-01・03）", async () => {
    const { calls } = mockApi(
      routes(EDITOR, { [`POST ${BASE}/activities`]: () => jsonResponse(201, makeActivity({ id: 24, name: "画面設計" })) }),
    );
    renderAt();
    const form = await screen.findByRole("form", { name: "活動を足す" });
    await waitFor(() => expect(within(form).getAllByRole("option")).toHaveLength(3));

    fireEvent.change(within(form).getByLabelText("置き場の WP"), { target: { value: "102" } });
    fireEvent.change(within(form).getByLabelText("名称"), { target: { value: "画面設計" } });
    fireEvent.change(within(form).getByLabelText("開始日"), { target: { value: "2026-04-13" } });
    fireEvent.change(within(form).getByLabelText("期間（稼働日）"), { target: { value: "5" } });
    fireEvent.click(within(form).getByRole("button", { name: "足す" }));

    expect(await screen.findByRole("status")).toHaveTextContent("活動 24（画面設計）を足し");
    expect(sent(calls, "POST", `${BASE}/activities`)[0]!.body).toEqual({
      work_package_id: 102,
      name: "画面設計",
      requested_start: "2026-04-13",
      duration_days: 5,
    });
  });

  it("希望開始日がプロジェクトの開始日より前なら、サーバの理由（422 BEFORE_PROJECT_START）を出す（CR-018）", async () => {
    const { calls } = mockApi(
      routes(EDITOR, {
        [`POST ${BASE}/activities`]: () =>
          jsonResponse(422, {
            error: {
              code: "BEFORE_PROJECT_START",
              message: "希望開始日はプロジェクトの開始日（2026-04-01）以降にします",
              details: [
                {
                  loc: ["body", "requested_start"],
                  requested_start: "2026-03-30",
                  project_start: "2026-04-01",
                  message: "希望開始日 2026-03-30 がプロジェクトの開始日 2026-04-01 より前です",
                },
              ],
            },
          }),
      }),
    );
    renderAt();
    const form = await screen.findByRole("form", { name: "活動を足す" });
    await waitFor(() => expect(within(form).getAllByRole("option")).toHaveLength(3));

    fireEvent.change(within(form).getByLabelText("置き場の WP"), { target: { value: "102" } });
    fireEvent.change(within(form).getByLabelText("名称"), { target: { value: "前倒し" } });
    fireEvent.change(within(form).getByLabelText("開始日"), { target: { value: "2026-03-30" } });
    fireEvent.change(within(form).getByLabelText("期間（稼働日）"), { target: { value: "2" } });
    fireEvent.click(within(form).getByRole("button", { name: "足す" }));

    const alert = await within(form).findByRole("alert");
    expect(alert).toHaveTextContent("希望開始日はプロジェクトの開始日（2026-04-01）以降にします");
    expect(alert).toHaveTextContent("希望開始日 2026-03-30 がプロジェクトの開始日 2026-04-01 より前です");
    expect(sent(calls, "POST", `${BASE}/activities`)).toHaveLength(1);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("マイルストーンは期間 0 で足す（SCH-09）。WP・名称が無ければ送らない", async () => {
    const { calls } = mockApi(
      routes(EDITOR, { [`POST ${BASE}/activities`]: () => jsonResponse(201, makeActivity({ id: 25, name: "リリース" })) }),
    );
    renderAt();
    const form = await screen.findByRole("form", { name: "活動を足す" });

    fireEvent.click(within(form).getByRole("button", { name: "足す" }));
    expect(await within(form).findByText("置き場の WP を選んでください")).toBeInTheDocument();
    expect(sent(calls, "POST", `${BASE}/activities`)).toHaveLength(0);

    await waitFor(() => expect(within(form).getAllByRole("option")).toHaveLength(3));
    fireEvent.change(within(form).getByLabelText("置き場の WP"), { target: { value: "101" } });
    fireEvent.change(within(form).getByLabelText("名称"), { target: { value: "リリース" } });
    fireEvent.change(within(form).getByLabelText("開始日"), { target: { value: "2026-04-30" } });
    fireEvent.click(within(form).getByLabelText("マイルストーン（期間 0）"));
    expect(within(form).getByLabelText("期間（稼働日）")).toHaveValue("0");
    fireEvent.click(within(form).getByRole("button", { name: "足す" }));

    await screen.findByRole("status");
    expect(sent(calls, "POST", `${BASE}/activities`)[0]!.body).toEqual({
      work_package_id: 101,
      name: "リリース",
      requested_start: "2026-04-30",
      duration_days: 0,
      is_milestone: true,
    });
  });

  it("依存を足す。循環する依存はサーバの理由を出す（SCH-04）", async () => {
    const { calls } = mockApi(
      routes(EDITOR, {
        [`POST ${BASE}/dependencies`]: () =>
          jsonResponse(422, {
            error: { code: "SCHEDULE_CYCLE", message: "依存が循環します", details: [{ message: "21 → 22 → 23 → 21" }] },
          }),
      }),
    );
    renderAt();
    const form = await screen.findByRole("form", { name: "依存を足す" });

    fireEvent.change(within(form).getByLabelText("先行"), { target: { value: "23" } });
    fireEvent.change(within(form).getByLabelText("後続"), { target: { value: "21" } });
    fireEvent.click(within(form).getByRole("button", { name: "依存を足す" }));

    const alert = await screen.findByText("依存が循環します");
    expect(alert.closest("[role=alert]")).toHaveTextContent("21 → 22 → 23 → 21");
    expect(sent(calls, "POST", `${BASE}/dependencies`)[0]!.body).toEqual({ predecessor_id: 23, successor_id: 21 });
  });

  it("依存を消すときは確かめてから消す", async () => {
    const { calls } = mockApi(
      routes(EDITOR, { [`DELETE ${BASE}/dependencies/31`]: () => jsonResponse(204, null) }),
    );
    renderAt();
    await screen.findByRole("form", { name: "依存を足す" });

    fireEvent.click(screen.getByRole("button", { name: "21→22 の依存を消す" }));
    const confirm = screen.getByRole("group", { name: "依存を消すことの確かめ" });
    fireEvent.click(within(confirm).getByRole("button", { name: "消す" }));

    expect(await screen.findByRole("status")).toHaveTextContent("依存を変え");
    expect(sent(calls, "DELETE", `${BASE}/dependencies/31`)).toHaveLength(1);
  });

  it("活動を消すときは確かめてから消す。やめれば消さない", async () => {
    const { calls } = mockApi(routes(EDITOR, { [`DELETE ${BASE}/activities/22`]: () => jsonResponse(204, null) }));
    renderAt();
    await editable();

    fireEvent.click(screen.getByRole("button", { name: "22 を消す" }));
    fireEvent.click(within(screen.getByRole("group", { name: "活動を消すことの確かめ" })).getByRole("button", { name: "やめる" }));
    expect(sent(calls, "DELETE", `${BASE}/activities/22`)).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "22 を消す" }));
    fireEvent.click(within(screen.getByRole("group", { name: "活動を消すことの確かめ" })).getByRole("button", { name: "消す" }));
    expect(await screen.findByRole("status")).toHaveTextContent("活動 22（関係者調整）を消した");
    expect(sent(calls, "DELETE", `${BASE}/activities/22`)).toHaveLength(1);
  });

  it("担当と割当工数を置き換え、割当工数の合計を見積と比べて出す（EFF-01）", async () => {
    const { calls } = mockApi(
      routes(EDITOR, {
        [`PUT ${BASE}/activities/21/assignments`]: () =>
          jsonResponse(200, {
            activity_id: 21,
            estimate_hours: 40,
            planned_hours_total: 44,
            difference_hours: 4,
            assignments: [],
          }),
      }),
    );
    renderAt();
    await editable();

    fireEvent.click(screen.getByRole("button", { name: "21 の担当と割当工数" }));
    const form = screen.getByRole("form", { name: "資料作成 の担当と割当工数" });
    expect(within(form).getByLabelText("割当工数と見積")).toHaveTextContent("割当工数の合計 24 人時／見積 40 人時／差 -16 人時");

    fireEvent.click(within(form).getByRole("button", { name: "担当を足す" }));
    fireEvent.change(within(form).getByLabelText("2 行目の担当者"), { target: { value: "9" } });
    fireEvent.change(within(form).getByLabelText("2 行目の割当工数"), { target: { value: "20" } });
    expect(within(form).getByLabelText("割当工数と見積")).toHaveTextContent("合計 44 人時／見積 40 人時／差 4 人時");
    fireEvent.click(within(form).getByRole("button", { name: "担当を保存する" }));

    expect(await screen.findByRole("status")).toHaveTextContent("活動 21 の担当を保存した");
    expect(sent(calls, "PUT", `${BASE}/activities/21/assignments`)[0]!.body).toEqual({
      assignments: [
        { user_id: 2, planned_hours: 24 },
        { user_id: 9, planned_hours: 20 },
      ],
    });
  });

  it("estimate.edit を持つ人は活動の見積を書き（API-EST-001 の ACTIVITY。EST-01）、一覧を読み直す", async () => {
    let saved = false;
    const { calls } = mockApi(
      routes(ESTIMATOR, {
        [`GET ${BASE}/activities`]: () =>
          jsonResponse(200, activityList([saved ? { ...A21, estimate_hours: 24 } : A21, A22, M23])),
        [`PUT ${BASE}/estimates/ACTIVITY/21`]: () => {
          saved = true;
          const record = makeEstimateRecord({
            id: 310,
            target_type: "ACTIVITY",
            target_id: 21,
            hours: 24,
            input_value: 3,
            input_unit: "DAY",
            method: "BOTTOM_UP",
            assumptions: "画面 3 枚",
            references: "見積表 第 2 版",
          });
          return jsonResponse(200, { record, current_hours: 24, wbs_status: "APPROVED", project_estimate_hours: 120 });
        },
      }),
    );
    renderAt();
    const table = await screen.findByRole("table", { name: "活動の一覧" });
    // schedule.edit が無いので、日程・担当を変える部品は出さない
    expect(screen.queryByRole("form", { name: "活動を足す" })).toBeNull();
    expect(screen.queryByLabelText("21 の名称")).toBeNull();
    expect(screen.queryByRole("button", { name: "21 を消す" })).toBeNull();
    expect(screen.queryByRole("button", { name: "まとめて保存する" })).toBeNull();

    fireEvent.click(await screen.findByRole("button", { name: "21 の見積" }));
    const panel = screen.getByRole("region", { name: "活動 21 の見積" });
    expect(panel).toHaveTextContent("今の見積：40 人時");
    const form = within(panel).getByRole("form", { name: "見積を書く（活動 21 資料作成）" });
    fireEvent.change(within(form).getByLabelText("見積"), { target: { value: "3" } });
    fireEvent.change(within(form).getByLabelText("単位"), { target: { value: "DAY" } });
    fireEvent.change(within(form).getByLabelText("見積手法"), { target: { value: "BOTTOM_UP" } });
    fireEvent.change(within(form).getByLabelText("前提"), { target: { value: "画面 3 枚" } });
    fireEvent.change(within(form).getByLabelText("参照資料"), { target: { value: "見積表 第 2 版" } });
    fireEvent.click(within(form).getByRole("button", { name: "見積を保存する" }));

    expect(await screen.findByRole("status")).toHaveTextContent(
      "活動 21（資料作成）の見積を 24 人時にした（プロジェクトの総見積 120 人時）",
    );
    const put = sent(calls, "PUT", `${BASE}/estimates/ACTIVITY/21`);
    expect(put).toHaveLength(1);
    expect(put[0]!.body).toEqual({
      input_value: 3,
      input_unit: "DAY",
      method: "BOTTOM_UP",
      assumptions: "画面 3 枚",
      references: "見積表 第 2 版",
    });
    // 一覧を読み直し、見積（人時）の列が変わる。書く部品は閉じる
    await waitFor(() => expect(sent(calls, "GET", `${BASE}/activities`)).toHaveLength(2));
    await waitFor(() =>
      expect(within(within(table).getAllByRole("row")[1]!).getAllByRole("cell")[9]).toHaveTextContent(/^24$/),
    );
    expect(screen.queryByRole("region", { name: "活動 21 の見積" })).toBeNull();
  });

  it("見積を書けなければサーバの文言を出し、一覧を読み直さない", async () => {
    const { calls } = mockApi(
      routes(ESTIMATOR, {
        [`PUT ${BASE}/estimates/ACTIVITY/22`]: () => errorResponse(404, "NOT_FOUND", "見積の対象が見つかりません"),
      }),
    );
    renderAt();
    fireEvent.click(await screen.findByRole("button", { name: "22 の見積" }));
    const form = screen.getByRole("form", { name: "見積を書く（活動 22 関係者調整）" });
    fireEvent.change(within(form).getByLabelText("見積"), { target: { value: "16" } });
    fireEvent.click(within(form).getByRole("button", { name: "見積を保存する" }));

    expect(await within(form).findByRole("alert")).toHaveTextContent("見積の対象が見つかりません");
    expect(sent(calls, "PUT", `${BASE}/estimates/ACTIVITY/22`)[0]!.body).toEqual({
      input_value: 16,
      input_unit: "HOUR",
      method: null,
      assumptions: null,
      references: null,
    });
    expect(sent(calls, "GET", `${BASE}/activities`)).toHaveLength(1);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("estimate.edit が無ければ見積の部品を出さない（見るだけの人・schedule.edit だけの人）", async () => {
    mockApi(routes(VIEWER));
    const viewer = renderAt();
    await screen.findByRole("table", { name: "活動の一覧" });
    expect(screen.queryByRole("button", { name: "21 の見積" })).toBeNull();
    expect(screen.queryByRole("columnheader", { name: "操作" })).toBeNull();
    viewer.unmount();

    mockApi(routes(EDITOR));
    renderAt();
    await editable();
    expect(screen.getByRole("button", { name: "21 を消す" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "21 の見積" })).toBeNull();
    expect(screen.queryByRole("form", { name: /見積を書く/ })).toBeNull();
  });

  it("プロジェクトの ID が正しくなければ見つからないことを示す", () => {
    const { calls } = mockApi({});
    const { container } = renderAt("/projects/0/activities");
    expect(container.querySelector('[data-screen-id="UI-SCH-001"]')).not.toBeNull();
    expect(screen.getByRole("alert")).toHaveTextContent("プロジェクトが見つかりません");
    expect(calls).toHaveLength(0);
  });

  it("一覧を読めなければ理由を出す", async () => {
    mockApi(
      routes(VIEWER, {
        [`GET ${BASE}/activities`]: () =>
          jsonResponse(403, { error: { code: "FORBIDDEN", message: "このプロジェクトを見る権限がありません", details: [] } }),
      }),
    );
    renderAt();
    expect(await screen.findByRole("alert")).toHaveTextContent("このプロジェクトを見る権限がありません");
  });
});

describe("UI-SCH-001 保存前の編集と、依存の相手の選択肢（A-49 の 13・OQ-42 (21) の c）", () => {
  const A24 = makeActivity({ id: 24, name: "画面設計", predecessor_ids: [] });

  /** 一覧の頁は 200 件ずつで 2 頁（試験では頁の中身を少なくし、total だけを 2 頁にする）。全件は 4 件 */
  function twoPages(): RouteHandler {
    return (call) => {
      if (call.url.includes("limit=1000&")) {
        return pagedResponse([A21, A22, M23, A24], call, { dependencies: [], restricted: false });
      }
      const offset = Number(new URLSearchParams(call.url.split("?")[1]).get("offset"));
      return jsonResponse(200, activityList(offset === 0 ? [A21, A22, M23] : [A24], { total: 201, offset }));
    };
  }

  function renderWithGantt() {
    return render(
      <MemoryRouter initialEntries={["/projects/3/activities"]}>
        <Routes>
          <Route path="/projects/:projectId/activities" element={<ActivityListPage />} />
          <Route path="/projects/:projectId/gantt" element={<p>ガントの画面</p>} />
        </Routes>
      </MemoryRouter>,
    );
  }

  function beforeUnloadPrevented(): boolean {
    const event = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(event);
    return event.defaultPrevented;
  }

  beforeEach(() => {
    setAuthToken("t-planner");
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    setAuthToken(null);
  });

  it("保存前の編集があれば頁送りの前に確かめる。やめれば頁を移らず編集を残し、移れば次の頁を読む。編集が無ければ確かめない", async () => {
    const { calls } = mockApi(routes(EDITOR, { [`GET ${BASE}/activities`]: twoPages() }));
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderAt();
    await editable();
    const pager = screen.getByRole("navigation", { name: "頁送り" });
    expect(pager).toHaveTextContent("全 201 件中 1〜200 件");

    fireEvent.change(screen.getByLabelText("21 の名称"), { target: { value: "資料作成（改）" } });
    fireEvent.click(within(pager).getByRole("button", { name: "次へ" }));

    expect(confirm).toHaveBeenCalledTimes(1);
    expect(confirm).toHaveBeenCalledWith(UNSAVED_EDITS_MESSAGE);
    expect(pageReads(calls)).toHaveLength(1);
    expect(screen.getByLabelText("21 の名称")).toHaveValue("資料作成（改）");

    confirm.mockReturnValue(true);
    fireEvent.click(within(pager).getByRole("button", { name: "次へ" }));

    expect(await screen.findByLabelText("24 の名称")).toHaveValue("画面設計");
    expect(confirm).toHaveBeenCalledTimes(2);
    expect(pageReads(calls).map((c) => c.url)).toEqual([
      `${BASE}/activities?limit=200&offset=0`,
      `${BASE}/activities?limit=200&offset=200`,
    ]);
    expect(screen.queryByLabelText("21 の名称")).toBeNull();

    // 編集の無い頁では確かめずに移る
    fireEvent.click(within(screen.getByRole("navigation", { name: "頁送り" })).getByRole("button", { name: "前へ" }));
    expect(await screen.findByLabelText("21 の名称")).toHaveValue("資料作成");
    expect(confirm).toHaveBeenCalledTimes(2);
    expect(pageReads(calls)).toHaveLength(3);
  });

  it("保存前の編集があれば、リンクで他の画面へ移る前に確かめる。やめれば移らない。新しいタブで開くクリックは確かめない", async () => {
    mockApi(routes(EDITOR));
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderWithGantt();
    await editable();
    const links = screen.getByRole("navigation", { name: "行き先" });

    fireEvent.change(screen.getByLabelText("22 の期間"), { target: { value: "5" } });
    fireEvent.click(within(links).getByRole("link", { name: "ガント" }));

    expect(confirm).toHaveBeenCalledTimes(1);
    expect(confirm).toHaveBeenCalledWith(UNSAVED_EDITS_MESSAGE);
    expect(screen.queryByText("ガントの画面")).toBeNull();
    expect(screen.getByLabelText("22 の期間")).toHaveValue("5");

    // Ctrl を押したクリック（新しいタブ）は今の画面を離れないので確かめない。新しいタブで開くのはブラウザの動作で、
    // jsdom は移れないので、ここでは画面の後でクリックの既定の動作を止め、確かめないことだけを見る
    const stopDefault = (event: Event) => event.preventDefault();
    document.addEventListener("click", stopDefault);
    fireEvent.click(within(links).getByRole("link", { name: "ガント" }), { ctrlKey: true });
    document.removeEventListener("click", stopDefault);
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("ガントの画面")).toBeNull();

    confirm.mockReturnValue(true);
    fireEvent.click(within(links).getByRole("link", { name: "ガント" }));

    expect(await screen.findByText("ガントの画面")).toBeInTheDocument();
    expect(confirm).toHaveBeenCalledTimes(2);
  });

  it("保存前の編集が無ければ、リンクで確かめずに移る", async () => {
    mockApi(routes(EDITOR));
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderWithGantt();
    await editable();

    // 編集して取り消せば、編集は無い
    fireEvent.change(screen.getByLabelText("22 の期間"), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "編集を取り消す" }));
    fireEvent.click(within(screen.getByRole("navigation", { name: "行き先" })).getByRole("link", { name: "ガント" }));

    expect(await screen.findByText("ガントの画面")).toBeInTheDocument();
    expect(confirm).not.toHaveBeenCalled();
  });

  it("保存前の編集がある間だけ、ブラウザを閉じる・再読み込みの前に確かめる（beforeunload）", async () => {
    mockApi(routes(EDITOR, { [`PATCH ${BASE}/activities`]: () => jsonResponse(200, { items: [A22] }) }));
    const view = renderAt();
    await editable();
    expect(beforeUnloadPrevented()).toBe(false);

    fireEvent.change(screen.getByLabelText("22 の期間"), { target: { value: "5" } });
    expect(beforeUnloadPrevented()).toBe(true);

    // 保存して読み直せば編集は無い
    fireEvent.click(screen.getByRole("button", { name: "まとめて保存する" }));
    expect(await screen.findByRole("status")).toHaveTextContent("1 件の活動を保存し");
    await waitFor(() => expect(beforeUnloadPrevented()).toBe(false));

    fireEvent.change(screen.getByLabelText("22 の期間"), { target: { value: "6" } });
    expect(beforeUnloadPrevented()).toBe(true);
    // 画面を閉じれば確かめをやめる
    view.unmount();
    expect(beforeUnloadPrevented()).toBe(false);
  });

  it("依存の相手は今の頁でなく全ての活動から選ぶ（limit 1000 で total まで読み足す）。依存の一覧の名前も全ての活動から出す", async () => {
    // 全件は 1001 件：今の頁の 3 件・999 件の活動・最後の頁（2 頁目）にだけある活動 4000
    const fillers = Array.from({ length: 997 }, (_, i) => makeActivity({ id: 1000 + i, name: `活動 ${1000 + i}` }));
    const last = makeActivity({ id: 4000, name: "最後の活動" });
    const all = [A21, A22, M23, ...fillers, last];
    const { calls } = mockApi(
      routes(EDITOR, {
        [`GET ${BASE}/activities`]: (call) =>
          call.url.includes("limit=1000&")
            ? pagedResponse(all, call, { dependencies: [], restricted: false })
            : jsonResponse(
                200,
                activityList([A21, A22, M23], {
                  total: all.length,
                  dependencies: [{ id: 33, predecessor_id: 4000, successor_id: 21, dep_type: "FS", lag_days: 0 }],
                }),
              ),
        [`POST ${BASE}/dependencies`]: () =>
          jsonResponse(201, { id: 34, predecessor_id: 4000, successor_id: 22, dep_type: "FS", lag_days: 0 }),
      }),
    );
    renderAt();
    const form = await screen.findByRole("form", { name: "依存を足す" });

    await waitFor(() => expect(within(within(form).getByLabelText("先行")).getAllByRole("option")).toHaveLength(1002));
    expect(within(within(form).getByLabelText("後続")).getAllByRole("option")).toHaveLength(1002);
    expect(allReads(calls).map((c) => c.url)).toEqual([
      `${BASE}/activities?limit=1000&offset=0`,
      `${BASE}/activities?limit=1000&offset=1000`,
    ]);
    // 今の頁に無い活動も名前で出す
    expect(screen.getByRole("table", { name: "依存の一覧" })).toHaveTextContent("4000 最後の活動");

    fireEvent.change(within(form).getByLabelText("先行"), { target: { value: "4000" } });
    fireEvent.change(within(form).getByLabelText("後続"), { target: { value: "22" } });
    fireEvent.click(within(form).getByRole("button", { name: "依存を足す" }));

    expect(await screen.findByRole("status")).toHaveTextContent("依存を変え");
    expect(sent(calls, "POST", `${BASE}/dependencies`)[0]!.body).toEqual({ predecessor_id: 4000, successor_id: 22 });
  });

  it("見るだけの人は依存の相手を選ばないので、全ての活動を読まない", async () => {
    const { calls } = mockApi(routes(VIEWER));
    renderAt();
    await screen.findByRole("table", { name: "依存の一覧" });

    expect(pageReads(calls)).toHaveLength(1);
    expect(allReads(calls)).toHaveLength(0);
  });

  it("全ての活動を読めなければ理由を出す", async () => {
    mockApi(
      routes(EDITOR, {
        [`GET ${BASE}/activities`]: (call) =>
          call.url.includes("limit=1000&")
            ? errorResponse(403, "FORBIDDEN", "このプロジェクトを見る権限がありません")
            : jsonResponse(200, activityList([A21, A22, M23])),
      }),
    );
    renderAt();

    expect(await screen.findByRole("alert")).toHaveTextContent("このプロジェクトを見る権限がありません");
  });
});

describe("タスクリストの入力の扱い", () => {
  it("活動 ID の並びを読む（; ・, ・空白で区切る。重なりは 1 つ）", () => {
    expect(parseIdList("")).toEqual([]);
    expect(parseIdList("21; 22,23 21")).toEqual([21, 22, 23]);
    expect(parseIdList("21;x")).toBeNull();
    expect(parseIdList("0")).toBeNull();
  });

  it("変えていない活動・項目は送らない。先行・担当は並びの順を問わない", () => {
    const drafts = {
      21: { ...draftOf(A21) },
      22: { ...draftOf(A22), predecessors: "21", requestedStart: "" },
    };
    expect(collectChanges([A21, A22], drafts)).toEqual({
      changes: [{ activity_id: 22, requested_start: null }],
      errors: [],
    });
    expect(collectChanges([A21], { 21: { ...draftOf(A21), name: "  " } }).errors).toEqual([
      "活動 21（資料作成）：名称を入力してください",
    ]);
  });

  it("割当工数の合計と見積との差（空は 0 として足す。見積が無ければ差は無い）", () => {
    expect(assignmentTotals([24, 20.5, null], 40)).toEqual({ total: 44.5, difference: 4.5 });
    expect(assignmentTotals([], null)).toEqual({ total: 0, difference: null });
  });

  it("活動を足す入力：終了日と期間から足す・期間の形を確かめる", () => {
    const base: FormState = {
      workPackageId: "101",
      name: "設計",
      requestedStart: "",
      requestedFinish: "2026-04-30",
      duration: "3",
      isMilestone: false,
    };
    expect(toCreateInput(base)).toEqual({
      input: { work_package_id: 101, name: "設計", requested_finish: "2026-04-30", duration_days: 3 },
      errors: [],
    });
    expect(toCreateInput({ ...base, duration: "-1" }).input).toBeNull();
  });

  it("担当の入力：重なる担当・数でない工数は送らない。空の工数は未定", () => {
    expect(toAssignmentInputs([{ userId: "2", plannedHours: "" }])).toEqual({
      inputs: [{ user_id: 2, planned_hours: null }],
      errors: [],
    });
    expect(
      toAssignmentInputs([
        { userId: "2", plannedHours: "8" },
        { userId: "2", plannedHours: "4" },
      ]).errors,
    ).toEqual(["2 行目：同じ担当者が重なっています"]);
    expect(toAssignmentInputs([{ userId: "2", plannedHours: "abc" }]).inputs).toBeNull();
  });
});
