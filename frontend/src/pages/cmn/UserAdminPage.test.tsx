import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setAuthToken } from "../../api/client";
import type { User, UserListItem } from "../../api/cmn";
import { makeMe, makeUser } from "../../test/fixtures";
import { errorResponse, jsonResponse, mockApi, type RecordedCall } from "../../test/mockApi";
import UserAdminPage, { collectChanges, isLocked } from "./UserAdminPage";

const ADMIN = makeUser({ id: 1, login_id: "admin", display_name: "管理者", is_system_admin: true });
const PLANNER = makeUser({
  id: 2,
  login_id: "planner",
  display_name: "計画 太郎",
  email: "planner@example.com",
  organization: "開発部",
  is_system_admin: false,
});

function usersPage(items: UserListItem[], total = items.length, offset = 0) {
  return { items, total, limit: 100, offset };
}

/** user.admin を持たず、プロジェクトで member.manage を持つ PM の /me */
const PM_ME = makeMe({
  user: makeUser({ id: 5, login_id: "pm", display_name: "PM", is_system_admin: false }),
  system_permissions: [],
  projects: [{ project_id: 3, roles: ["PM"], permissions: ["member.manage", "project.view"], view_level: "ALL" }],
});

function getUsersCalls(calls: RecordedCall[]) {
  return calls.filter((c) => c.method === "GET" && c.path === "/api/v1/users");
}

function renderPage() {
  return render(<UserAdminPage />);
}

describe("UI-CMN-002 ユーザー管理", () => {
  beforeEach(() => {
    setAuthToken("t-admin");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    setAuthToken(null);
  });

  it("一覧と、user.admin の操作（作成・変更）を描画する", async () => {
    const { calls } = mockApi({
      "GET /api/v1/me": () => jsonResponse(200, makeMe({ user: ADMIN })),
      "GET /api/v1/users": () => jsonResponse(200, usersPage([ADMIN, { ...PLANNER, is_active: false }])),
    });

    const { container } = renderPage();

    const table = await screen.findByRole("table", { name: "ユーザーの一覧" });
    expect(container.querySelector('[data-screen-id="UI-CMN-002"]')).not.toBeNull();
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(3);
    expect(rows[2]).toHaveTextContent("planner");
    expect(rows[2]).toHaveTextContent("planner@example.com");
    expect(rows[2]).toHaveTextContent("開発部");
    expect(rows[2]).toHaveTextContent("無効");
    expect(screen.getByRole("form", { name: "ユーザーの作成" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "planner を変更する" })).toBeInTheDocument();
    expect(getUsersCalls(calls)[0]?.url).toBe("/api/v1/users?limit=100&offset=0");
  });

  it("member.manage だけの利用者には一覧だけを見せ、作成・変更の部品を出さない", async () => {
    mockApi({
      "GET /api/v1/me": () => jsonResponse(200, PM_ME),
      "GET /api/v1/users": () => jsonResponse(200, usersPage([ADMIN, PLANNER])),
    });

    renderPage();

    expect(await screen.findByRole("table", { name: "ユーザーの一覧" })).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "ユーザーの作成" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /を変更する$/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "操作" })).not.toBeInTheDocument();
  });

  it("member.manage だけの利用者に返る null（ロックの状態・作成の許可。CR-019）は「—」とし、ロック中を出さない", async () => {
    const hidden = { locked_until: null, can_create_project: null };
    mockApi({
      "GET /api/v1/me": () => jsonResponse(200, PM_ME),
      "GET /api/v1/users": () => jsonResponse(200, usersPage([{ ...ADMIN, ...hidden }, { ...PLANNER, ...hidden }])),
    });

    renderPage();

    const table = await screen.findByRole("table", { name: "ユーザーの一覧" });
    const rows = within(table).getAllByRole("row");
    expect(within(rows[2] as HTMLElement).getAllByRole("cell")[5]).toHaveTextContent("—");
    expect(within(rows[2] as HTMLElement).getAllByRole("cell")[6]).toHaveTextContent("有効");
    expect(table).not.toHaveTextContent("ロック中");
  });

  it("一覧を見る権限が無ければ（403）サーバの文言を出す", async () => {
    mockApi({
      "GET /api/v1/me": () => jsonResponse(200, makeMe({ user: PLANNER, system_permissions: [] })),
      "GET /api/v1/users": () => errorResponse(403, "FORBIDDEN", "この操作の権限がありません"),
    });

    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("この操作の権限がありません");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("ユーザーを作り（API-CMN-004）、一覧を読み直す", async () => {
    let users = [ADMIN];
    const created = makeUser({ id: 9, login_id: "exec", display_name: "実行 花子", is_system_admin: false });
    const { calls } = mockApi({
      "GET /api/v1/me": () => jsonResponse(200, makeMe({ user: ADMIN })),
      "GET /api/v1/users": () => jsonResponse(200, usersPage(users)),
      "POST /api/v1/users": () => {
        users = [ADMIN, created];
        return jsonResponse(201, created);
      },
    });

    renderPage();
    const form = await screen.findByRole("form", { name: "ユーザーの作成" });
    fireEvent.change(within(form).getByLabelText("ログイン名"), { target: { value: "exec" } });
    fireEvent.change(within(form).getByLabelText("表示名"), { target: { value: "実行 花子" } });
    fireEvent.change(within(form).getByLabelText("初期パスワード"), { target: { value: "initial-passphrase" } });
    fireEvent.change(within(form).getByLabelText("所属"), { target: { value: "委託先A" } });
    fireEvent.click(within(form).getByRole("button", { name: "作成する" }));

    expect(await screen.findByRole("status")).toHaveTextContent("ユーザー「exec」を作成しました");
    const post = calls.find((c) => c.method === "POST");
    expect(post?.body).toEqual({
      login_id: "exec",
      display_name: "実行 花子",
      password: "initial-passphrase",
      email: null,
      organization: "委託先A",
      is_system_admin: false,
      can_create_project: false,
    });
    await waitFor(() => expect(screen.getByRole("table", { name: "ユーザーの一覧" })).toHaveTextContent("実行 花子"));
    expect(getUsersCalls(calls)).toHaveLength(2);
    expect(within(form).getByLabelText("ログイン名")).toHaveValue("");
  });

  it("ログイン名が重なれば（409）サーバの文言を出し、入力を残す", async () => {
    mockApi({
      "GET /api/v1/me": () => jsonResponse(200, makeMe({ user: ADMIN })),
      "GET /api/v1/users": () => jsonResponse(200, usersPage([ADMIN])),
      "POST /api/v1/users": () => errorResponse(409, "DUPLICATE_LOGIN_ID", "ログイン名「admin」は既にあります"),
    });

    renderPage();
    const form = await screen.findByRole("form", { name: "ユーザーの作成" });
    fireEvent.change(within(form).getByLabelText("ログイン名"), { target: { value: "admin" } });
    fireEvent.change(within(form).getByLabelText("表示名"), { target: { value: "二人目" } });
    fireEvent.change(within(form).getByLabelText("初期パスワード"), { target: { value: "initial-passphrase" } });
    fireEvent.click(within(form).getByRole("button", { name: "作成する" }));

    expect(await within(form).findByRole("alert")).toHaveTextContent("ログイン名「admin」は既にあります");
    expect(within(form).getByLabelText("ログイン名")).toHaveValue("admin");
  });

  it("変えた項目だけを送って変更し（API-CMN-005）、無効にできる", async () => {
    const { calls } = mockApi({
      "GET /api/v1/me": () => jsonResponse(200, makeMe({ user: ADMIN })),
      "GET /api/v1/users": () => jsonResponse(200, usersPage([ADMIN, PLANNER])),
      "PATCH /api/v1/users/2": (call) =>
        jsonResponse(200, { ...PLANNER, ...(call.body as Partial<User>) }),
    });

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "planner を変更する" }));
    const form = screen.getByRole("form", { name: "ユーザーの変更" });
    expect(within(form).getByLabelText("表示名")).toHaveValue("計画 太郎");
    expect(within(form).getByLabelText("メール")).toHaveValue("planner@example.com");
    fireEvent.change(within(form).getByLabelText("表示名"), { target: { value: "計画 次郎" } });
    fireEvent.click(within(form).getByLabelText("有効"));
    fireEvent.click(within(form).getByRole("button", { name: "保存する" }));

    expect(await screen.findByRole("status")).toHaveTextContent("ユーザー「planner」を変更しました");
    const patch = calls.find((c) => c.method === "PATCH");
    expect(patch?.body).toEqual({ display_name: "計画 次郎", is_active: false });
    expect(screen.queryByRole("form", { name: "ユーザーの変更" })).not.toBeInTheDocument();
  });

  it("最後のシステム管理者を外す変更（422）はサーバの文言を出す", async () => {
    mockApi({
      "GET /api/v1/me": () => jsonResponse(200, makeMe({ user: ADMIN })),
      "GET /api/v1/users": () => jsonResponse(200, usersPage([ADMIN])),
      "PATCH /api/v1/users/1": () =>
        errorResponse(422, "LAST_SYSTEM_ADMIN", "有効なシステム管理者がいなくなります"),
    });

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "admin を変更する" }));
    const form = screen.getByRole("form", { name: "ユーザーの変更" });
    fireEvent.click(within(form).getByLabelText("システム管理者"));
    fireEvent.click(within(form).getByRole("button", { name: "保存する" }));

    expect(await within(form).findByRole("alert")).toHaveTextContent("有効なシステム管理者がいなくなります");
    expect(screen.getByRole("form", { name: "ユーザーの変更" })).toBeInTheDocument();
  });

  it("プロジェクトを作れる人を作り、パスワードの長さの注記を出す（9.1・NFR-04）", async () => {
    const { calls } = mockApi({
      "GET /api/v1/me": () => jsonResponse(200, makeMe({ user: ADMIN })),
      "GET /api/v1/users": () => jsonResponse(200, usersPage([ADMIN])),
      "POST /api/v1/users": (call) => jsonResponse(201, makeUser({ id: 9, ...(call.body as Partial<User>) })),
    });

    renderPage();
    const form = await screen.findByRole("form", { name: "ユーザーの作成" });
    expect(within(form).getByLabelText("初期パスワード")).toHaveAccessibleDescription(
      "12 文字以上（ログイン名と同じものは使えません）",
    );
    fireEvent.change(within(form).getByLabelText("ログイン名"), { target: { value: "creator" } });
    fireEvent.change(within(form).getByLabelText("表示名"), { target: { value: "作る人" } });
    fireEvent.change(within(form).getByLabelText("初期パスワード"), { target: { value: "initial-passphrase" } });
    fireEvent.click(within(form).getByLabelText("プロジェクトを作れる"));
    fireEvent.click(within(form).getByRole("button", { name: "作成する" }));

    expect(await screen.findByRole("status")).toHaveTextContent("ユーザー「creator」を作成しました");
    const post = calls.find((c) => c.method === "POST");
    expect(post?.body).toMatchObject({ login_id: "creator", can_create_project: true, is_system_admin: false });
    expect(within(form).getByLabelText("プロジェクトを作れる")).not.toBeChecked();
  });

  it("パスワードが方針に反すれば（400）、サーバの理由も出す", async () => {
    mockApi({
      "GET /api/v1/me": () => jsonResponse(200, makeMe({ user: ADMIN })),
      "GET /api/v1/users": () => jsonResponse(200, usersPage([ADMIN])),
      "POST /api/v1/users": () =>
        jsonResponse(400, {
          error: {
            code: "VALIDATION_ERROR",
            message: "入力に誤りがあります",
            details: [{ loc: ["body", "password"], message: "パスワードは 12 文字以上にしてください" }],
          },
        }),
    });

    renderPage();
    const form = await screen.findByRole("form", { name: "ユーザーの作成" });
    fireEvent.change(within(form).getByLabelText("ログイン名"), { target: { value: "exec" } });
    fireEvent.change(within(form).getByLabelText("表示名"), { target: { value: "実行 花子" } });
    fireEvent.change(within(form).getByLabelText("初期パスワード"), { target: { value: "short" } });
    fireEvent.click(within(form).getByRole("button", { name: "作成する" }));

    const alert = await within(form).findByRole("alert");
    expect(alert).toHaveTextContent("入力に誤りがあります");
    expect(alert).toHaveTextContent("パスワードは 12 文字以上にしてください");
    expect(within(form).getByLabelText("ログイン名")).toHaveValue("exec");
  });

  it("一覧にプロジェクトを作れる人とロック中を示し、変更でプロジェクトの作成を許せる", async () => {
    const lockedPlanner = { ...PLANNER, locked_until: "2999-01-01T00:00:00Z" };
    const creator = makeUser({ id: 3, login_id: "creator", is_system_admin: false, can_create_project: true,
      locked_until: "2000-01-01T00:00:00Z" });
    const { calls } = mockApi({
      "GET /api/v1/me": () => jsonResponse(200, makeMe({ user: ADMIN })),
      "GET /api/v1/users": () => jsonResponse(200, usersPage([ADMIN, lockedPlanner, creator])),
      "PATCH /api/v1/users/2": (call) => jsonResponse(200, { ...lockedPlanner, ...(call.body as Partial<User>) }),
    });

    renderPage();
    const table = await screen.findByRole("table", { name: "ユーザーの一覧" });
    expect(within(table).getByRole("columnheader", { name: "プロジェクトを作れる" })).toBeInTheDocument();
    const rows = within(table).getAllByRole("row");
    expect(rows[2]).toHaveTextContent("ロック中");
    expect(rows[3]).not.toHaveTextContent("ロック中");
    expect(within(rows[3] as HTMLElement).getAllByRole("cell")[5]).toHaveTextContent("○");

    fireEvent.click(screen.getByRole("button", { name: "planner を変更する" }));
    const form = screen.getByRole("form", { name: "ユーザーの変更" });
    expect(form).toHaveTextContent("新しいパスワードを設定するとロックを解きます");
    expect(within(form).getByLabelText("新しいパスワード（変えないときは空欄）")).toHaveAccessibleDescription(
      "12 文字以上（ログイン名と同じものは使えません）",
    );
    fireEvent.click(within(form).getByLabelText("プロジェクトを作れる"));
    fireEvent.click(within(form).getByRole("button", { name: "保存する" }));

    expect(await screen.findByRole("status")).toHaveTextContent("ユーザー「planner」を変更しました");
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ can_create_project: true });
  });

  it("何も変えずに保存すると、送らずにそう伝える", async () => {
    const { calls } = mockApi({
      "GET /api/v1/me": () => jsonResponse(200, makeMe({ user: ADMIN })),
      "GET /api/v1/users": () => jsonResponse(200, usersPage([ADMIN, PLANNER])),
    });

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "planner を変更する" }));
    const form = screen.getByRole("form", { name: "ユーザーの変更" });
    fireEvent.click(within(form).getByRole("button", { name: "保存する" }));

    expect(within(form).getByRole("alert")).toHaveTextContent("変更した項目がありません");
    expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(0);
  });

  it("101 件目からの頁を読む", async () => {
    const { calls } = mockApi({
      "GET /api/v1/me": () => jsonResponse(200, makeMe({ user: ADMIN })),
      "GET /api/v1/users": (call) =>
        jsonResponse(200, usersPage([call.url.endsWith("offset=100") ? PLANNER : ADMIN], 150,
          call.url.endsWith("offset=100") ? 100 : 0)),
    });

    renderPage();
    expect(await screen.findByText("全 150 件中 1〜100 件")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "次へ" }));

    expect(await screen.findByText("全 150 件中 101〜150 件")).toBeInTheDocument();
    expect(getUsersCalls(calls).map((c) => c.url)).toEqual([
      "/api/v1/users?limit=100&offset=0",
      "/api/v1/users?limit=100&offset=100",
    ]);
    expect(screen.getByRole("button", { name: "次へ" })).toBeDisabled();
  });
});

describe("collectChanges（API-CMN-005 に載せる項目）", () => {
  const unchanged = {
    displayName: PLANNER.display_name,
    email: PLANNER.email ?? "",
    organization: PLANNER.organization ?? "",
    password: "",
    isSystemAdmin: PLANNER.is_system_admin,
    canCreateProject: PLANNER.can_create_project,
    isActive: PLANNER.is_active,
  };

  it("変えていなければ空", () => {
    expect(collectChanges(PLANNER, unchanged)).toEqual({});
  });

  it("空欄にしたメール・所属は null で送り、パスワードは入れたときだけ送る", () => {
    expect(
      collectChanges(PLANNER, { ...unchanged, email: "", organization: "  ", password: "new-passphrase" }),
    ).toEqual({
      email: null,
      organization: null,
      password: "new-passphrase",
    });
  });

  it("もともと値の無いメールを空欄のままにしても送らない", () => {
    const user = makeUser({ email: null, organization: null });
    expect(
      collectChanges(user, {
        displayName: user.display_name,
        email: "",
        organization: "",
        password: "",
        isSystemAdmin: user.is_system_admin,
        canCreateProject: user.can_create_project,
        isActive: user.is_active,
      }),
    ).toEqual({});
  });

  it("プロジェクトを作れるかを変えたときだけ can_create_project を送る", () => {
    expect(collectChanges(PLANNER, { ...unchanged, canCreateProject: true })).toEqual({ can_create_project: true });
  });
});

describe("isLocked（NFR-04 のロック中）", () => {
  const now = Date.parse("2026-11-13T09:00:00Z");

  it("locked_until が今より後ならロック中。ちょうど・過ぎた・空はロック中でない", () => {
    expect(isLocked({ locked_until: "2026-11-13T09:15:00Z" }, now)).toBe(true);
    expect(isLocked({ locked_until: "2026-11-13T09:00:00Z" }, now)).toBe(false);
    expect(isLocked({ locked_until: "2026-11-13T08:59:59Z" }, now)).toBe(false);
    expect(isLocked({ locked_until: null }, now)).toBe(false);
  });
});
