import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setAuthToken } from "../../api/client";
import type { Project } from "../../api/prj";
import { makeMe, makeProject, makeUser } from "../../test/fixtures";
import { errorResponse, jsonResponse, mockApi, type RecordedCall } from "../../test/mockApi";
import ProjectListPage from "./ProjectListPage";

const ADMIN = makeUser({ id: 1, login_id: "admin", display_name: "管理者", is_system_admin: true });
const PLANNER = makeUser({ id: 2, login_id: "planner", display_name: "計画 太郎", is_system_admin: false });
const RETIRED = makeUser({ id: 8, login_id: "retired", display_name: "退職 者", is_system_admin: false, is_active: false });

const P1 = makeProject({ id: 3, code: "P-001", name: "基幹更改", wbs_status: "BASELINED" });
const P2 = makeProject({
  id: 4,
  code: "P-002",
  name: "販売管理",
  target_finish_date: null,
  owner_user_id: 2,
  owner: { id: 2, login_id: "planner", display_name: "計画 太郎" },
});

function projectsPage(items: Project[], total = items.length, offset = 0) {
  return { items, total, limit: 100, offset };
}

function usersPage(items = [ADMIN, PLANNER, RETIRED]) {
  return { items, total: items.length, limit: 1000, offset: 0 };
}

/** project.create を持たず、P-001 で effort.view・audit.view を持たない実行担当者の /me */
const EXECUTOR_ME = makeMe({
  user: makeUser({ id: 5, login_id: "exec", display_name: "実行 花子", is_system_admin: false }),
  system_permissions: [],
  projects: [
    { project_id: 3, roles: ["EXECUTOR"], permissions: ["project.view", "progress.enter_own"], view_level: "ALL" },
    {
      project_id: 4,
      roles: ["PROGRESS_MANAGER"],
      permissions: ["project.view", "effort.view", "audit.view", "data.export"],
      view_level: "ALL",
    },
  ],
});

function projectCalls(calls: RecordedCall[]) {
  return calls.filter((c) => c.method === "GET" && c.path === "/api/v1/projects");
}

function renderPage() {
  return render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <ProjectListPage />
    </MemoryRouter>,
  );
}

describe("UI-PRJ-001 プロジェクト一覧", () => {
  beforeEach(() => {
    setAuthToken("t-admin");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    setAuthToken(null);
  });

  it("見られるプロジェクトと 7.2 の行き先、project.create の作成の部品を描画する", async () => {
    const { calls } = mockApi({
      "GET /api/v1/me": () => jsonResponse(200, makeMe({ user: ADMIN })),
      "GET /api/v1/projects": () => jsonResponse(200, projectsPage([P1, P2])),
      "GET /api/v1/users": () => jsonResponse(200, usersPage()),
    });

    const { container } = renderPage();

    const table = await screen.findByRole("table", { name: "プロジェクトの一覧" });
    expect(container.querySelector('[data-screen-id="UI-PRJ-001"]')).not.toBeNull();
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(3);
    expect(rows[1]).toHaveTextContent("P-001");
    expect(rows[1]).toHaveTextContent("基幹更改");
    expect(rows[1]).toHaveTextContent("2026-04-01〜2026-09-30");
    expect(rows[1]).toHaveTextContent("管理者");
    expect(rows[1]).toHaveTextContent("基準化済み");
    expect(rows[2]).toHaveTextContent("2026-04-01〜");
    expect(rows[2]).toHaveTextContent("計画 太郎");
    expect(rows[2]).toHaveTextContent("作成中");

    const links = within(rows[1]!).getByRole("navigation", { name: "P-001 の行き先" });
    expect(within(links).getByRole("link", { name: "設定" })).toHaveAttribute("href", "/projects/3/settings");
    expect(within(links).getByRole("link", { name: "WBS" })).toHaveAttribute("href", "/projects/3/wbs");
    expect(within(links).getByRole("link", { name: "ガント" })).toHaveAttribute("href", "/projects/3/gantt");
    // システム管理者は全ての権限を持つ（9.2）
    expect(within(links).getByRole("link", { name: "工数" })).toHaveAttribute("href", "/projects/3/effort");
    expect(within(links).getByRole("link", { name: "変更履歴" })).toHaveAttribute("href", "/projects/3/audit-logs");
    expect(within(links).getByRole("link", { name: "入出力" })).toHaveAttribute("href", "/projects/3/import-export");

    expect(screen.getByRole("form", { name: "プロジェクトの作成" })).toBeInTheDocument();
    expect(projectCalls(calls)[0]?.url).toBe("/api/v1/projects?limit=100&offset=0");
  });

  it("project.create が無ければ作成の部品を出さず、権限の要る行き先はプロジェクトごとの権限で出し分ける", async () => {
    const { calls } = mockApi({
      "GET /api/v1/me": () => jsonResponse(200, EXECUTOR_ME),
      "GET /api/v1/projects": () => jsonResponse(200, projectsPage([P1, P2])),
    });

    renderPage();

    const table = await screen.findByRole("table", { name: "プロジェクトの一覧" });
    expect(screen.queryByRole("form", { name: "プロジェクトの作成" })).not.toBeInTheDocument();
    const p1 = within(table).getByRole("navigation", { name: "P-001 の行き先" });
    expect(within(p1).getByRole("link", { name: "設定" })).toBeInTheDocument();
    expect(within(p1).getByRole("link", { name: "タスクリスト" })).toBeInTheDocument();
    expect(within(p1).queryByRole("link", { name: "工数" })).not.toBeInTheDocument();
    expect(within(p1).queryByRole("link", { name: "変更履歴" })).not.toBeInTheDocument();
    expect(within(p1).queryByRole("link", { name: "入出力" })).not.toBeInTheDocument();
    const p2 = within(table).getByRole("navigation", { name: "P-002 の行き先" });
    expect(within(p2).getByRole("link", { name: "工数" })).toBeInTheDocument();
    expect(within(p2).getByRole("link", { name: "変更履歴" })).toBeInTheDocument();
    expect(within(p2).getByRole("link", { name: "入出力" })).toBeInTheDocument();
    // 作成の部品を出さないので、責任者の候補（API-CMN-003）も読まない
    expect(calls.some((c) => c.path === "/api/v1/users")).toBe(false);
  });

  it("見られるプロジェクトが無ければそう伝える", async () => {
    mockApi({
      "GET /api/v1/me": () => jsonResponse(200, EXECUTOR_ME),
      "GET /api/v1/projects": () => jsonResponse(200, projectsPage([])),
    });

    renderPage();

    expect(await screen.findByText("見られるプロジェクトがありません")).toBeInTheDocument();
  });

  it("一覧を読めなければサーバの文言を出す", async () => {
    mockApi({
      "GET /api/v1/me": () => jsonResponse(200, EXECUTOR_ME),
      "GET /api/v1/projects": () => errorResponse(500, "INTERNAL_ERROR", "サーバの内部でエラーが起きました"),
    });

    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("サーバの内部でエラーが起きました");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("プロジェクトを作り（API-PRJ-001）、一覧を読み直す。責任者は有効なユーザーから選ぶ", async () => {
    let projects = [P1];
    const created = makeProject({ id: 9, code: "P-009", name: "新規開発" });
    const { calls } = mockApi({
      "GET /api/v1/me": () => jsonResponse(200, makeMe({ user: ADMIN })),
      "GET /api/v1/projects": () => jsonResponse(200, projectsPage(projects)),
      "GET /api/v1/users": () => jsonResponse(200, usersPage()),
      "POST /api/v1/projects": () => {
        projects = [P1, created];
        return jsonResponse(201, created);
      },
    });

    renderPage();
    const form = await screen.findByRole("form", { name: "プロジェクトの作成" });
    const owner = within(form).getByLabelText("責任者");
    await waitFor(() => expect(within(owner).getAllByRole("option")).toHaveLength(2));
    // 無効なユーザーは候補に出さない
    expect(within(owner).queryByRole("option", { name: /retired/ })).not.toBeInTheDocument();
    expect(owner).toHaveValue("1");

    fireEvent.change(within(form).getByLabelText("コード"), { target: { value: "P-009" } });
    fireEvent.change(within(form).getByLabelText("名称"), { target: { value: "新規開発" } });
    fireEvent.change(within(form).getByLabelText("目的"), { target: { value: "新しい事業を支える" } });
    fireEvent.change(within(form).getByLabelText("開始日"), { target: { value: "2026-12-01" } });
    fireEvent.change(owner, { target: { value: "2" } });
    fireEvent.click(within(form).getByRole("button", { name: "作成する" }));

    expect(await screen.findByRole("status")).toHaveTextContent("プロジェクト「P-009」を作成しました");
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({
      code: "P-009",
      name: "新規開発",
      purpose: "新しい事業を支える",
      requirements: null,
      key_deliverables: null,
      start_date: "2026-12-01",
      target_finish_date: null,
      owner_user_id: 2,
    });
    await waitFor(() => expect(screen.getByRole("table", { name: "プロジェクトの一覧" })).toHaveTextContent("新規開発"));
    expect(projectCalls(calls)).toHaveLength(2);
    expect(within(form).getByLabelText("コード")).toHaveValue("");
  });

  it("ユーザーの一覧を読めない（403）ときは、自分を責任者にして作れる", async () => {
    const creator = makeUser({ id: 6, login_id: "lead", display_name: "リーダー", is_system_admin: false });
    const { calls } = mockApi({
      "GET /api/v1/me": () =>
        jsonResponse(200, makeMe({ user: creator, system_permissions: ["project.create"], projects: [] })),
      "GET /api/v1/projects": () => jsonResponse(200, projectsPage([])),
      "GET /api/v1/users": () => errorResponse(403, "FORBIDDEN", "この操作の権限がありません"),
      "POST /api/v1/projects": () => jsonResponse(201, makeProject({ id: 10, code: "P-010" })),
    });

    renderPage();
    const form = await screen.findByRole("form", { name: "プロジェクトの作成" });
    await waitFor(() => expect(calls.some((c) => c.path === "/api/v1/users")).toBe(true));
    const owner = within(form).getByLabelText("責任者");
    expect(within(owner).getAllByRole("option").map((o) => o.textContent)).toEqual(["リーダー（lead）"]);

    fireEvent.change(within(form).getByLabelText("コード"), { target: { value: "P-010" } });
    fireEvent.change(within(form).getByLabelText("名称"), { target: { value: "小規模改修" } });
    fireEvent.change(within(form).getByLabelText("開始日"), { target: { value: "2027-01-04" } });
    fireEvent.click(within(form).getByRole("button", { name: "作成する" }));

    expect(await screen.findByRole("status")).toHaveTextContent("プロジェクト「P-010」を作成しました");
    expect((calls.find((c) => c.method === "POST")?.body as { owner_user_id: number }).owner_user_id).toBe(6);
    // 403 は責任者の候補を絞るだけで、画面のエラーにしない
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("コードが重なれば（409）サーバの文言を出し、入力を残す", async () => {
    mockApi({
      "GET /api/v1/me": () => jsonResponse(200, makeMe({ user: ADMIN })),
      "GET /api/v1/projects": () => jsonResponse(200, projectsPage([P1])),
      "GET /api/v1/users": () => jsonResponse(200, usersPage()),
      "POST /api/v1/projects": () =>
        errorResponse(409, "DUPLICATE_PROJECT_CODE", "そのプロジェクトのコードは既に使われています"),
    });

    renderPage();
    const form = await screen.findByRole("form", { name: "プロジェクトの作成" });
    fireEvent.change(within(form).getByLabelText("コード"), { target: { value: "P-001" } });
    fireEvent.change(within(form).getByLabelText("名称"), { target: { value: "二つ目" } });
    fireEvent.change(within(form).getByLabelText("開始日"), { target: { value: "2026-12-01" } });
    fireEvent.click(within(form).getByRole("button", { name: "作成する" }));

    expect(await within(form).findByRole("alert")).toHaveTextContent("そのプロジェクトのコードは既に使われています");
    expect(within(form).getByLabelText("コード")).toHaveValue("P-001");
  });

  it("入力の誤り（400）は、項目ごとの理由（details）も出す", async () => {
    mockApi({
      "GET /api/v1/me": () => jsonResponse(200, makeMe({ user: ADMIN })),
      "GET /api/v1/projects": () => jsonResponse(200, projectsPage([P1])),
      "GET /api/v1/users": () => jsonResponse(200, usersPage()),
      "POST /api/v1/projects": () =>
        jsonResponse(400, {
          error: {
            code: "VALIDATION_ERROR",
            message: "入力に誤りがあります",
            details: [{ loc: ["body", "owner_user_id"], message: "責任者が見つからないか、無効な利用者です" }],
          },
        }),
    });

    renderPage();
    const form = await screen.findByRole("form", { name: "プロジェクトの作成" });
    fireEvent.change(within(form).getByLabelText("コード"), { target: { value: "P-011" } });
    fireEvent.change(within(form).getByLabelText("名称"), { target: { value: "責任者が退職" } });
    fireEvent.change(within(form).getByLabelText("開始日"), { target: { value: "2026-12-01" } });
    fireEvent.click(within(form).getByRole("button", { name: "作成する" }));

    const alert = await within(form).findByRole("alert");
    expect(alert).toHaveTextContent("入力に誤りがあります");
    expect(alert).toHaveTextContent("責任者が見つからないか、無効な利用者です");
  });

  it("101 件目からの頁を読む", async () => {
    const { calls } = mockApi({
      "GET /api/v1/me": () => jsonResponse(200, EXECUTOR_ME),
      "GET /api/v1/projects": (call) =>
        call.url.endsWith("offset=100")
          ? jsonResponse(200, projectsPage([P2], 120, 100))
          : jsonResponse(200, projectsPage([P1], 120, 0)),
    });

    renderPage();
    expect(await screen.findByText("全 120 件中 1〜100 件")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "次へ" }));

    expect(await screen.findByText("全 120 件中 101〜120 件")).toBeInTheDocument();
    expect(projectCalls(calls).map((c) => c.url)).toEqual([
      "/api/v1/projects?limit=100&offset=0",
      "/api/v1/projects?limit=100&offset=100",
    ]);
  });
});
