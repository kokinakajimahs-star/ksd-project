import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../../App";
import { getAuthToken, setAuthToken } from "../../api/client";
import { SESSION_STORAGE_KEY, SessionProvider, loadStoredSession } from "../../auth/session";
import { makeLoginResponse, makeMe, makeUser, storeSession } from "../../test/fixtures";
import { errorResponse, jsonResponse, mockApi } from "../../test/mockApi";
import LoginPage, { LOGIN_FAILED_MESSAGE } from "./LoginPage";

function renderApp(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

function renderLogin() {
  return render(
    <MemoryRouter initialEntries={["/login"]}>
      <SessionProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/projects" element={<p>プロジェクト一覧へ来た</p>} />
        </Routes>
      </SessionProvider>
    </MemoryRouter>,
  );
}

function fillAndSubmit(loginId: string, password: string) {
  fireEvent.change(screen.getByLabelText("ログイン名"), { target: { value: loginId } });
  fireEvent.change(screen.getByLabelText("パスワード"), { target: { value: password } });
  fireEvent.click(screen.getByRole("button", { name: "ログイン" }));
}

describe("UI-CMN-001 ログイン", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    setAuthToken(null);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("/login で画面を描画する（data-screen-id と入力欄）", () => {
    const { container } = renderApp("/login");

    expect(container.querySelector('[data-screen-id="UI-CMN-001"]')).not.toBeNull();
    expect(screen.getByRole("heading", { level: 2, name: "ログイン" })).toBeInTheDocument();
    expect(screen.getByLabelText("ログイン名")).toBeRequired();
    expect(screen.getByLabelText("パスワード")).toHaveAttribute("type", "password");
  });

  it("ログインに成功すると、トークンを覚えてプロジェクト一覧（7.2）へ進む", async () => {
    const response = makeLoginResponse({ access_token: "t-1", user: makeUser({ display_name: "計画 太郎" }) });
    const { calls } = mockApi({ "POST /api/v1/auth/login": () => jsonResponse(200, response) });

    renderLogin();
    fillAndSubmit("planner", "secret");

    expect(await screen.findByText("プロジェクト一覧へ来た")).toBeInTheDocument();
    expect(calls).toHaveLength(1);
    expect(calls[0]?.body).toEqual({ login_id: "planner", password: "secret" });
    expect(getAuthToken()).toBe("t-1");
    const stored = JSON.parse(window.sessionStorage.getItem(SESSION_STORAGE_KEY) ?? "null") as { token: string };
    expect(stored.token).toBe("t-1");
  });

  it("401 ならロックを明かさない文言を出し、ログインしない・パスワードを消す", async () => {
    mockApi({
      "POST /api/v1/auth/login": () =>
        errorResponse(401, "INVALID_CREDENTIALS", "ログイン名またはパスワードが違うか、しばらくログインできません"),
    });

    renderLogin();
    fillAndSubmit("planner", "wrong");

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "ログイン名またはパスワードが違うか、しばらくログインできません",
    );
    expect(screen.queryByText("プロジェクト一覧へ来た")).not.toBeInTheDocument();
    expect(getAuthToken()).toBeNull();
    expect(window.sessionStorage.getItem(SESSION_STORAGE_KEY)).toBeNull();
    expect(screen.getByLabelText("パスワード")).toHaveValue("");
    expect(screen.getByLabelText("ログイン名")).toHaveValue("planner");
  });

  it("401 の文言はサーバの文言に依らず同じ（ロック中かどうか・ログイン名の有無を画面でも明かさない。NFR-04）", async () => {
    mockApi({
      "POST /api/v1/auth/login": () => errorResponse(401, "INVALID_CREDENTIALS", "アカウントはロックされています"),
    });

    renderLogin();
    fillAndSubmit("planner", "secret");

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(LOGIN_FAILED_MESSAGE);
    expect(alert).not.toHaveTextContent("ロック");
  });

  it("サーバに繋がらなければ接続できないことを出す", async () => {
    mockApi({
      "POST /api/v1/auth/login": () => {
        throw new TypeError("Failed to fetch");
      },
    });

    renderLogin();
    fillAndSubmit("planner", "secret");

    expect(await screen.findByRole("alert")).toHaveTextContent("サーバに接続できません");
  });

  it("ログインが要る画面はログインへ送り、ログインの後に元の画面へ戻す", async () => {
    const { calls } = mockApi({
      "POST /api/v1/auth/login": () => jsonResponse(200, makeLoginResponse({ access_token: "t-2" })),
      "GET /api/v1/me": () => jsonResponse(200, makeMe()),
      "GET /api/v1/approval-requests": () => jsonResponse(200, { items: [], total: 0, limit: 100, offset: 0 }),
    });

    const { container } = renderApp("/approvals");
    expect(container.querySelector('[data-screen-id="UI-CMN-001"]')).not.toBeNull();
    expect(calls).toHaveLength(0);

    fillAndSubmit("admin", "secret");

    expect(await screen.findByText("承認待ちはありません")).toBeInTheDocument();
    expect(container.querySelector('[data-screen-id="UI-CMN-003"]')).not.toBeNull();
    const meCall = calls.find((c) => c.path === "/api/v1/me");
    expect(meCall?.headers.Authorization).toBe("Bearer t-2");
  });

  it("ログアウトはトークンを失効させ（API-CMN-002）、ログインの画面へ戻る", async () => {
    storeSession({ token: "t-3", user: makeUser({ display_name: "計画 太郎", is_system_admin: false }) });
    const { calls } = mockApi({
      "GET /api/v1/me": () => jsonResponse(200, makeMe()),
      "GET /api/v1/approval-requests": () => jsonResponse(200, { items: [], total: 0, limit: 100, offset: 0 }),
      "POST /api/v1/auth/logout": () => jsonResponse(204, null),
    });

    const { container } = renderApp("/approvals");
    expect(await screen.findByText("承認待ちはありません")).toBeInTheDocument();
    expect(screen.getByText("計画 太郎")).toBeInTheDocument();
    // システム管理者でなければユーザー管理への行き先を出さない
    expect(screen.queryByRole("link", { name: "ユーザー管理" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "ログアウト" }));

    await waitFor(() => expect(container.querySelector('[data-screen-id="UI-CMN-001"]')).not.toBeNull());
    const logoutCall = calls.find((c) => c.path === "/api/v1/auth/logout");
    expect(logoutCall?.method).toBe("POST");
    expect(logoutCall?.headers.Authorization).toBe("Bearer t-3");
    expect(getAuthToken()).toBeNull();
    expect(window.sessionStorage.getItem(SESSION_STORAGE_KEY)).toBeNull();
  });

  it("トークンが失効していれば（401）ログインを忘れてログインの画面へ戻す", async () => {
    storeSession({ token: "t-expired" });
    mockApi({
      "GET /api/v1/me": () => errorResponse(401, "UNAUTHENTICATED", "ログインしてください"),
      "GET /api/v1/users": () => errorResponse(401, "UNAUTHENTICATED", "ログインしてください"),
    });

    const { container } = renderApp("/admin/users");

    await waitFor(() => expect(container.querySelector('[data-screen-id="UI-CMN-001"]')).not.toBeNull());
    expect(getAuthToken()).toBeNull();
    expect(window.sessionStorage.getItem(SESSION_STORAGE_KEY)).toBeNull();
  });

  it("覚えたログインは有効期限までだけ使う", () => {
    const now = new Date("2026-11-11T00:00:00Z");
    storeSession({ token: "t-old", expiresAt: "2026-11-10T23:59:59Z" });
    expect(loadStoredSession(window.sessionStorage, now)).toBeNull();
    expect(window.sessionStorage.getItem(SESSION_STORAGE_KEY)).toBeNull();

    storeSession({ token: "t-new", expiresAt: "2026-11-11T00:00:01Z" });
    expect(loadStoredSession(window.sessionStorage, now)?.token).toBe("t-new");

    window.sessionStorage.setItem(SESSION_STORAGE_KEY, "{壊れた");
    expect(loadStoredSession(window.sessionStorage, now)).toBeNull();
  });
});
