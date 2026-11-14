import { render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import { setAuthToken } from "../api/client";
import { makeMe, makeProject, makeUser, storeSession } from "../test/fixtures";
import { errorResponse, jsonResponse, mockApi } from "../test/mockApi";
import ProjectNav from "./ProjectNav";

function renderNav(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <ProjectNav projectId={3} userId={2} />
    </MemoryRouter>,
  );
}

const member = (permissions: string[]) =>
  makeMe({
    user: makeUser({ id: 2, is_system_admin: false }),
    system_permissions: [],
    projects: [{ project_id: 3, roles: ["MEMBER"], permissions, view_level: "ALL" }],
  });

function labels(): string[] {
  const nav = screen.getByRole("navigation", { name: "プロジェクトのメニュー" });
  return within(nav)
    .getAllByRole("link")
    .map((link) => link.textContent ?? "");
}

describe("ProjectNav（プロジェクトの左メニュー。A-53）", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.sessionStorage.clear();
    setAuthToken(null);
  });

  it("UI-PRJ-001 の行き先を並べ、権限の要る行き先は持っている権限の分だけ出す", async () => {
    mockApi({ "GET /api/v1/me": () => jsonResponse(200, member(["project.view", "effort.view"])) });

    renderNav("/projects/3/gantt");

    await waitFor(() => expect(labels()).toContain("工数"));
    expect(labels()).toEqual(["設定", "WBS", "タスクリスト", "ガント", "ベースライン", "工数", "変更要求"]);
    expect(screen.getByRole("link", { name: "工数" })).toHaveAttribute("href", "/projects/3/effort");
  });

  it("自分の権限を読めなければ、権限の要らない行き先だけを出す", async () => {
    const { calls } = mockApi({ "GET /api/v1/me": () => errorResponse(500, "INTERNAL", "失敗") });

    renderNav("/projects/3/gantt");

    await waitFor(() => expect(calls).toHaveLength(1));
    expect(labels()).toEqual(["設定", "WBS", "タスクリスト", "ガント", "ベースライン", "変更要求"]);
  });

  it("今の画面（WBS の要素の画面を含む）を aria-current で示す", async () => {
    mockApi({ "GET /api/v1/me": () => jsonResponse(200, member(["project.view"])) });

    renderNav("/projects/3/wbs/101");

    expect(screen.getByRole("link", { name: "WBS" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "ガント" })).not.toHaveAttribute("aria-current");
    await waitFor(() => expect(labels()).toHaveLength(6));
  });

  it("App はプロジェクトの中の画面でだけ左メニューを出す（一覧・ログイン前には出さない）", async () => {
    const { calls } = mockApi({});
    const loggedOut = render(
      <MemoryRouter initialEntries={["/projects/3/gantt"]}>
        <App />
      </MemoryRouter>,
    );
    expect(screen.queryByRole("navigation", { name: "プロジェクトのメニュー" })).toBeNull();
    expect(calls).toHaveLength(0);
    loggedOut.unmount();

    storeSession();
    mockApi({
      "GET /api/v1/me": () =>
        jsonResponse(200, makeMe({ system_permissions: [], user: makeUser({ is_system_admin: false }) })),
      "GET /api/v1/projects": () => jsonResponse(200, { items: [makeProject()], total: 1, limit: 100, offset: 0 }),
    });
    render(
      <MemoryRouter initialEntries={["/projects"]}>
        <App />
      </MemoryRouter>,
    );
    expect(await screen.findByRole("table", { name: "プロジェクトの一覧" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "プロジェクトのメニュー" })).toBeNull();
  });
});
