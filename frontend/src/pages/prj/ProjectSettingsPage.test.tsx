import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setAuthToken } from "../../api/client";
import type { Me } from "../../api/cmn";
import type { CalendarInput, Member, Project, RoleGrants, ScopeItem, ScopeItemInput } from "../../api/prj";
import { makeMe, makeMember, makeProject, makeUser } from "../../test/fixtures";
import { errorResponse, jsonResponse, mockApi, type RecordedCall, type RouteHandler } from "../../test/mockApi";
import { PROJECT_PERMISSIONS } from "./labels";
import ProjectSettingsPage, { parseProjectId } from "./ProjectSettingsPage";
import { collectProjectChanges, formOf } from "./settings/BasicInfoSection";
import { addHoliday, formatWeekdays, parseWeekdays } from "./settings/CalendarSection";
import { orderRoles } from "./settings/MembersSection";
import { tableOf, toRoleGrants } from "./settings/RolePermissionsSection";
import { toScopeInputs } from "./settings/ScopeSection";

const FUTURE = { v7_startTransition: true, v7_relativeSplatPath: true };
const BASE = "/api/v1/projects/3";

const PM_USER = makeUser({ id: 5, login_id: "pm", display_name: "PM 一郎", is_system_admin: false });
const PLANNER_USER = makeUser({ id: 2, login_id: "planner", display_name: "計画 太郎", is_system_admin: false });
const EXEC_USER = makeUser({ id: 9, login_id: "exec", display_name: "実行 花子", is_system_admin: false });

const PM_PERMISSIONS = PROJECT_PERMISSIONS.map((p) => p.code);

function meWith(permissions: string[], user = PM_USER): Me {
  return makeMe({
    user,
    system_permissions: [],
    projects: [{ project_id: 3, roles: ["PM"], permissions, view_level: "ALL" }],
  });
}

const PROJECT = makeProject({
  id: 3,
  owner_user_id: 5,
  owner: { id: 5, login_id: "pm", display_name: "PM 一郎" },
});

const SCOPE_ITEMS: ScopeItem[] = [
  { id: 11, kind: "IN", title: "販売管理の更改", description: "受注から請求まで", sort_order: 1 },
  { id: 12, kind: "OUT", title: "会計システム", description: null, sort_order: 2 },
];

const CALENDAR = {
  project_id: 3,
  working_weekdays: "1111100",
  hours_per_day: 8,
  holidays: [{ holiday_date: "2026-05-04", name: "みどりの日" }],
};

const PM_MEMBER = makeMember({ user_id: 5, login_id: "pm", display_name: "PM 一郎", roles: ["PM"], organization: null });
const PLANNER_MEMBER = makeMember();

/** 9.2 の既定（試験に要る分）：PM は全て、VIEWER は project.view と data.export */
const ROLE_PERMISSIONS = {
  project_id: 3,
  roles: [
    { role: "PM", permissions: PM_PERMISSIONS },
    { role: "PLANNER", permissions: ["project.view", "wbs.edit"] },
    { role: "EXECUTOR", permissions: ["project.view"] },
    { role: "PROGRESS_MANAGER", permissions: ["project.view"] },
    { role: "APPROVER", permissions: ["project.view"] },
    { role: "CONTRACTOR", permissions: ["project.view"] },
    { role: "VIEWER", permissions: ["project.view", "data.export"] },
  ],
};

function membersPage(items: Member[]) {
  return { project_id: 3, items, total: items.length, limit: 100, offset: 0 };
}

function routes(me: Me, overrides: Record<string, RouteHandler> = {}): Record<string, RouteHandler> {
  return {
    "GET /api/v1/me": () => jsonResponse(200, me),
    [`GET ${BASE}`]: () => jsonResponse(200, PROJECT),
    [`GET ${BASE}/scope-items`]: () =>
      jsonResponse(200, { project_id: 3, items: SCOPE_ITEMS, total: 2, limit: 1000, offset: 0 }),
    [`GET ${BASE}/calendar`]: () => jsonResponse(200, CALENDAR),
    [`GET ${BASE}/members`]: () => jsonResponse(200, membersPage([PM_MEMBER, PLANNER_MEMBER])),
    [`GET ${BASE}/role-permissions`]: () => jsonResponse(200, ROLE_PERMISSIONS),
    "GET /api/v1/users": () =>
      jsonResponse(200, { items: [PM_USER, PLANNER_USER, EXEC_USER], total: 3, limit: 1000, offset: 0 }),
    ...overrides,
  };
}

function renderAt(path = "/projects/3/settings") {
  return render(
    <MemoryRouter initialEntries={[path]} future={FUTURE}>
      <Routes>
        <Route path="/projects/:projectId/settings" element={<ProjectSettingsPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

async function region(name: string) {
  return screen.findByRole("region", { name });
}

function sent(calls: RecordedCall[], method: string, path: string) {
  return calls.filter((c) => c.method === method && c.path === path);
}

describe("UI-PRJ-002 プロジェクト設定", () => {
  beforeEach(() => {
    setAuthToken("t-pm");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    setAuthToken(null);
  });

  it("基本情報・スコープ・カレンダー・メンバー・権限を描画し、PM には保存の部品を出す", async () => {
    const { calls } = mockApi(routes(meWith(PM_PERMISSIONS)));

    const { container } = renderAt();

    const basic = await region("基本情報");
    expect(container.querySelector('[data-screen-id="UI-PRJ-002"]')).not.toBeNull();
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent("プロジェクト設定：P-001 基幹更改");
    expect(within(basic).getByLabelText("コード")).toHaveValue("P-001");
    expect(within(basic).getByLabelText("目的")).toHaveValue("老朽化した基幹系を更改する");
    expect(within(basic).getByLabelText("開始日")).toHaveValue("2026-04-01");
    expect(within(basic).getByLabelText("遅れ・前倒しの判定の許容幅（稼働日）")).toHaveValue(0.5);
    expect(within(basic).getByText("WBSの状態：作成中")).toBeInTheDocument();
    expect(within(basic).getByRole("button", { name: "基本情報を保存する" })).toBeEnabled();

    const scope = await region("スコープ");
    expect(await within(scope).findByLabelText("1 行目の名称")).toHaveValue("販売管理の更改");
    expect(within(scope).getByLabelText("2 行目の区分")).toHaveValue("OUT");
    expect(within(scope).getByRole("button", { name: "スコープを保存する" })).toBeInTheDocument();

    const calendar = await region("稼働カレンダー");
    expect(await within(calendar).findByLabelText("1 日の稼働時間")).toHaveValue(8);
    expect(within(calendar).getByLabelText("金")).toBeChecked();
    expect(within(calendar).getByLabelText("土")).not.toBeChecked();
    expect(within(calendar).getByRole("table", { name: "休日" })).toHaveTextContent("みどりの日");

    const members = await region("メンバー");
    const memberTable = await within(members).findByRole("table", { name: "メンバーの一覧" });
    expect(within(memberTable).getAllByRole("row")[2]).toHaveTextContent("計画担当者");
    expect(within(memberTable).getAllByRole("row")[2]).toHaveTextContent("開発部");

    const permissions = await region("権限");
    expect(await within(permissions).findByLabelText("閲覧者：data.export")).toBeChecked();
    expect(within(permissions).getByLabelText("閲覧者：wbs.edit")).not.toBeChecked();
    expect(within(permissions).getByLabelText("閲覧者：wbs.edit")).toBeEnabled();
    expect(within(permissions).getByRole("button", { name: "権限の表を保存する" })).toBeInTheDocument();

    expect(sent(calls, "GET", `${BASE}/scope-items`)[0]?.url).toBe(`${BASE}/scope-items?limit=1000&offset=0`);
    expect(sent(calls, "GET", `${BASE}/members`)[0]?.url).toBe(`${BASE}/members?limit=100&offset=0`);
  });

  it("project.view だけの人には値を見せるだけで、変える部品を出さない", async () => {
    const viewer = makeUser({ id: 7, login_id: "viewer", display_name: "閲覧 次郎", is_system_admin: false });
    const { calls } = mockApi(routes(meWith(["project.view"], viewer)));

    renderAt();

    const basic = await region("基本情報");
    expect(within(basic).getByLabelText("名称")).toBeDisabled();
    expect(within(basic).queryByRole("button", { name: "基本情報を保存する" })).not.toBeInTheDocument();
    const scope = await region("スコープ");
    expect(await within(scope).findByLabelText("1 行目の名称")).toBeDisabled();
    expect(within(scope).queryByRole("button", { name: "スコープを保存する" })).not.toBeInTheDocument();
    expect(within(scope).queryByRole("button", { name: "対象内の項目を足す" })).not.toBeInTheDocument();
    const calendar = await region("稼働カレンダー");
    expect(await within(calendar).findByLabelText("1 日の稼働時間")).toBeDisabled();
    expect(within(calendar).queryByRole("button", { name: "稼働カレンダーを保存する" })).not.toBeInTheDocument();
    const members = await region("メンバー");
    await within(members).findByRole("table", { name: "メンバーの一覧" });
    expect(within(members).queryByRole("button", { name: "メンバーを足す" })).not.toBeInTheDocument();
    expect(within(members).queryByRole("button", { name: /を外す$/ })).not.toBeInTheDocument();
    const permissions = await region("権限");
    expect(await within(permissions).findByLabelText("PM：project.manage")).toBeDisabled();
    expect(within(permissions).queryByRole("button", { name: "権限の表を保存する" })).not.toBeInTheDocument();
    // 変える部品を出さないので、ユーザーの一覧（API-CMN-003）も読まない
    expect(calls.some((c) => c.path === "/api/v1/users")).toBe(false);
  });

  it("基本情報と設定は変えた項目だけを送る（API-PRJ-004）", async () => {
    const { calls } = mockApi(
      routes(meWith(PM_PERMISSIONS), {
        [`PATCH ${BASE}`]: (call) => {
          const body = call.body as { name: string; settings: { delay_tolerance_days: number } };
          return jsonResponse(
            200,
            makeProject({
              ...PROJECT,
              name: body.name,
              settings: { ...PROJECT.settings, delay_tolerance_days: body.settings.delay_tolerance_days },
            }),
          );
        },
      }),
    );

    renderAt();
    const basic = await region("基本情報");
    fireEvent.change(within(basic).getByLabelText("名称"), { target: { value: "基幹更改（第2期）" } });
    fireEvent.change(within(basic).getByLabelText("目的"), { target: { value: "" } });
    fireEvent.change(within(basic).getByLabelText("遅れ・前倒しの判定の許容幅（稼働日）"), { target: { value: "1" } });
    fireEvent.click(within(basic).getByRole("button", { name: "基本情報を保存する" }));

    expect(await within(basic).findByRole("status")).toHaveTextContent("基本情報を保存しました");
    expect(sent(calls, "PATCH", BASE)[0]?.body).toEqual({
      name: "基幹更改（第2期）",
      purpose: null,
      settings: { delay_tolerance_days: 1 },
    });
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent("基幹更改（第2期）");
  });

  it("基本情報を何も変えずに保存すると、送らずにそう伝える", async () => {
    const { calls } = mockApi(routes(meWith(PM_PERMISSIONS)));

    renderAt();
    const basic = await region("基本情報");
    fireEvent.click(within(basic).getByRole("button", { name: "基本情報を保存する" }));

    expect(within(basic).getByRole("alert")).toHaveTextContent("変更した項目がありません");
    expect(sent(calls, "PATCH", BASE)).toHaveLength(0);
  });

  it("コードの重複（409）はサーバの文言を出す", async () => {
    mockApi(
      routes(meWith(PM_PERMISSIONS), {
        [`PATCH ${BASE}`]: () =>
          errorResponse(409, "DUPLICATE_PROJECT_CODE", "そのプロジェクトのコードは既に使われています"),
      }),
    );

    renderAt();
    const basic = await region("基本情報");
    fireEvent.change(within(basic).getByLabelText("コード"), { target: { value: "P-002" } });
    fireEvent.click(within(basic).getByRole("button", { name: "基本情報を保存する" }));

    expect(await within(basic).findByRole("alert")).toHaveTextContent("そのプロジェクトのコードは既に使われています");
    expect(within(basic).getByLabelText("コード")).toHaveValue("P-002");
  });

  it("スコープの項目を足し・並べ替え・消して、全件を置き換える（API-PRJ-006）", async () => {
    const { calls } = mockApi(
      routes(meWith(PM_PERMISSIONS), {
        [`PUT ${BASE}/scope-items`]: (call) => {
          const items = (call.body as { items: ScopeItemInput[] }).items;
          return jsonResponse(200, {
            project_id: 3,
            items: items.map((item, index) => ({
              id: item.id ?? 100 + index,
              kind: item.kind,
              title: item.title,
              description: item.description,
              sort_order: index + 1,
            })),
            total: items.length,
            limit: items.length,
            offset: 0,
          });
        },
      }),
    );

    renderAt();
    const scope = await region("スコープ");
    await within(scope).findByLabelText("1 行目の名称");
    fireEvent.click(within(scope).getByRole("button", { name: "対象外の項目を足す" }));
    fireEvent.change(within(scope).getByLabelText("3 行目の名称"), { target: { value: "データ移行" } });
    fireEvent.click(within(scope).getByRole("button", { name: "3 行目を上へ" }));
    fireEvent.click(within(scope).getByRole("button", { name: "3 行目を消す" }));
    fireEvent.click(within(scope).getByRole("button", { name: "スコープを保存する" }));

    expect(await within(scope).findByRole("status")).toHaveTextContent("スコープを保存しました");
    expect(sent(calls, "PUT", `${BASE}/scope-items`)[0]?.body).toEqual({
      items: [
        { id: 11, kind: "IN", title: "販売管理の更改", description: "受注から請求まで" },
        { kind: "OUT", title: "データ移行", description: null },
      ],
    });
    expect(within(scope).getAllByRole("row")).toHaveLength(3);
  });

  it("名称の無いスコープ項目は送らずに伝える", async () => {
    const { calls } = mockApi(routes(meWith(PM_PERMISSIONS)));

    renderAt();
    const scope = await region("スコープ");
    await within(scope).findByLabelText("1 行目の名称");
    fireEvent.click(within(scope).getByRole("button", { name: "対象内の項目を足す" }));
    fireEvent.click(within(scope).getByRole("button", { name: "スコープを保存する" }));

    expect(within(scope).getByRole("alert")).toHaveTextContent("名称の無い項目があります");
    expect(sent(calls, "PUT", `${BASE}/scope-items`)).toHaveLength(0);
  });

  it("スコープ項目が 1000 件を超えて全てを読めなければ、置き換えさせない", async () => {
    mockApi(
      routes(meWith(PM_PERMISSIONS), {
        [`GET ${BASE}/scope-items`]: () =>
          jsonResponse(200, { project_id: 3, items: SCOPE_ITEMS, total: 1001, limit: 1000, offset: 0 }),
      }),
    );

    renderAt();
    const scope = await region("スコープ");
    expect(await within(scope).findByRole("note")).toHaveTextContent("1000 件を超える");
    expect(within(scope).getByLabelText("1 行目の名称")).toBeDisabled();
    expect(within(scope).queryByRole("button", { name: "スコープを保存する" })).not.toBeInTheDocument();
  });

  it("稼働の曜日・稼働時間・休日を変えて保存する（API-PRJ-008）", async () => {
    const { calls } = mockApi(
      routes(meWith(PM_PERMISSIONS), {
        [`PUT ${BASE}/calendar`]: (call) => jsonResponse(200, { project_id: 3, ...(call.body as CalendarInput) }),
      }),
    );

    renderAt();
    const calendar = await region("稼働カレンダー");
    await within(calendar).findByLabelText("1 日の稼働時間");
    fireEvent.click(within(calendar).getByLabelText("土"));
    fireEvent.change(within(calendar).getByLabelText("1 日の稼働時間"), { target: { value: "7.5" } });
    fireEvent.change(within(calendar).getByLabelText("休日の日付"), { target: { value: "2026-04-29" } });
    fireEvent.change(within(calendar).getByLabelText("休日の名前"), { target: { value: "昭和の日" } });
    fireEvent.click(within(calendar).getByRole("button", { name: "休日を足す" }));
    fireEvent.click(within(calendar).getByRole("button", { name: "稼働カレンダーを保存する" }));

    expect(await within(calendar).findByRole("status")).toHaveTextContent("稼働カレンダーを保存しました");
    expect(sent(calls, "PUT", `${BASE}/calendar`)[0]?.body).toEqual({
      working_weekdays: "1111110",
      hours_per_day: 7.5,
      holidays: [
        { holiday_date: "2026-04-29", name: "昭和の日" },
        { holiday_date: "2026-05-04", name: "みどりの日" },
      ],
    });
  });

  it("既にある休日の日付は足さずに伝え、稼働日の無いカレンダー（422）はサーバの文言を出す", async () => {
    mockApi(
      routes(meWith(PM_PERMISSIONS), {
        [`PUT ${BASE}/calendar`]: () =>
          errorResponse(422, "INVALID_CALENDAR", "このカレンダーでは稼働日を数えられません"),
      }),
    );

    renderAt();
    const calendar = await region("稼働カレンダー");
    await within(calendar).findByLabelText("1 日の稼働時間");
    fireEvent.change(within(calendar).getByLabelText("休日の日付"), { target: { value: "2026-05-04" } });
    fireEvent.click(within(calendar).getByRole("button", { name: "休日を足す" }));
    expect(within(calendar).getByRole("alert")).toHaveTextContent("2026-05-04 は既に休日です");

    for (const day of ["月", "火", "水", "木", "金"]) {
      fireEvent.click(within(calendar).getByLabelText(day));
    }
    fireEvent.click(within(calendar).getByRole("button", { name: "稼働カレンダーを保存する" }));

    expect(await within(calendar).findByText("このカレンダーでは稼働日を数えられません")).toBeInTheDocument();
  });

  it("メンバーを足し（API-PRJ-010）、/me を読み直す", async () => {
    let members = [PM_MEMBER, PLANNER_MEMBER];
    const added = makeMember({
      user_id: 9,
      login_id: "exec",
      display_name: "実行 花子",
      roles: ["EXECUTOR", "CONTRACTOR"],
      organization: "委託先A",
      team: "移行班",
    });
    const { calls } = mockApi(
      routes(meWith(PM_PERMISSIONS), {
        [`GET ${BASE}/members`]: () => jsonResponse(200, membersPage(members)),
        [`PUT ${BASE}/members/9`]: () => {
          members = [...members, added];
          return jsonResponse(200, added);
        },
      }),
    );

    renderAt();
    const section = await region("メンバー");
    await within(section).findByRole("table", { name: "メンバーの一覧" });
    const addButton = within(section).getByRole("button", { name: "メンバーを足す" });
    await waitFor(() => expect(addButton).toBeEnabled());
    fireEvent.click(addButton);
    const form = within(section).getByRole("form", { name: "メンバーを足す" });
    // 今のメンバーは足す候補に出さない
    expect(within(form).getAllByRole("option").map((o) => o.textContent)).toEqual(["実行 花子（exec）"]);
    fireEvent.click(within(form).getByLabelText("委託先"));
    fireEvent.click(within(form).getByLabelText("実行担当者"));
    fireEvent.change(within(form).getByLabelText("所属"), { target: { value: "委託先A" } });
    fireEvent.change(within(form).getByLabelText("チーム"), { target: { value: "移行班" } });
    fireEvent.click(within(form).getByRole("button", { name: "足す" }));

    expect(await within(section).findByRole("status")).toHaveTextContent("「exec」をメンバーに足しました");
    expect(sent(calls, "PUT", `${BASE}/members/9`)[0]?.body).toEqual({
      roles: ["EXECUTOR", "CONTRACTOR"],
      organization: "委託先A",
      team: "移行班",
    });
    await waitFor(() => expect(within(section).getByRole("table")).toHaveTextContent("実行担当者・委託先"));
    expect(sent(calls, "GET", "/api/v1/me")).toHaveLength(2);
  });

  it("ロールを 1 つも選ばなければ送らずに伝える", async () => {
    const { calls } = mockApi(routes(meWith(PM_PERMISSIONS)));

    renderAt();
    const section = await region("メンバー");
    fireEvent.click(await within(section).findByRole("button", { name: "planner を変更する" }));
    const form = within(section).getByRole("form", { name: "メンバーの変更" });
    expect(within(form).getByLabelText("計画担当者")).toBeChecked();
    fireEvent.click(within(form).getByLabelText("計画担当者"));
    fireEvent.click(within(form).getByRole("button", { name: "保存する" }));

    expect(within(form).getByRole("alert")).toHaveTextContent("ロールを 1 つ以上選びます");
    expect(calls.filter((c) => c.method === "PUT")).toHaveLength(0);
  });

  it("メンバーは確かめてから外す（API-PRJ-011）", async () => {
    let members = [PM_MEMBER, PLANNER_MEMBER];
    const { calls } = mockApi(
      routes(meWith(PM_PERMISSIONS), {
        [`GET ${BASE}/members`]: () => jsonResponse(200, membersPage(members)),
        [`DELETE ${BASE}/members/2`]: () => {
          members = [PM_MEMBER];
          return jsonResponse(204, null);
        },
      }),
    );

    renderAt();
    const section = await region("メンバー");
    fireEvent.click(await within(section).findByRole("button", { name: "planner を外す" }));
    expect(sent(calls, "DELETE", `${BASE}/members/2`)).toHaveLength(0);
    const confirm = within(section).getByRole("group", { name: "メンバーを外すことの確かめ" });
    expect(confirm).toHaveTextContent("計画 太郎（planner）");
    fireEvent.click(within(confirm).getByRole("button", { name: "外す" }));

    expect(await within(section).findByRole("status")).toHaveTextContent("「planner」をメンバーから外しました");
    expect(sent(calls, "DELETE", `${BASE}/members/2`)).toHaveLength(1);
    await waitFor(() => expect(within(section).getByRole("table")).not.toHaveTextContent("planner"));
  });

  it("ロール×権限の表を全てのロールで置き換え（API-CMN-007）、/me を読み直す", async () => {
    const { calls } = mockApi(
      routes(meWith(PM_PERMISSIONS), {
        [`PUT ${BASE}/role-permissions`]: (call) =>
          jsonResponse(200, { project_id: 3, roles: (call.body as { roles: RoleGrants[] }).roles }),
      }),
    );

    renderAt();
    const section = await region("権限");
    fireEvent.click(await within(section).findByLabelText("閲覧者：data.export"));
    fireEvent.click(within(section).getByLabelText("計画担当者：estimate.edit"));
    fireEvent.click(within(section).getByRole("button", { name: "権限の表を保存する" }));

    expect(await within(section).findByRole("status")).toHaveTextContent("ロール×権限の表を保存しました");
    const body = sent(calls, "PUT", `${BASE}/role-permissions`)[0]?.body as { roles: RoleGrants[] };
    expect(body.roles.map((r) => r.role)).toEqual([
      "PM",
      "PLANNER",
      "EXECUTOR",
      "PROGRESS_MANAGER",
      "APPROVER",
      "CONTRACTOR",
      "VIEWER",
    ]);
    expect(body.roles[1]).toEqual({ role: "PLANNER", permissions: ["project.view", "wbs.edit", "estimate.edit"] });
    expect(body.roles[6]).toEqual({ role: "VIEWER", permissions: ["project.view"] });
    expect(sent(calls, "GET", "/api/v1/me")).toHaveLength(2);
  });

  it("プロジェクトを見られなければ（403）サーバの文言を出し、各節を出さない", async () => {
    mockApi(
      routes(meWith([]), {
        [`GET ${BASE}`]: () => errorResponse(403, "FORBIDDEN", "この操作の権限がありません"),
      }),
    );

    renderAt();

    expect(await screen.findByRole("alert")).toHaveTextContent("この操作の権限がありません");
    expect(screen.queryByRole("region", { name: "基本情報" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "プロジェクト一覧へ" })).toHaveAttribute("href", "/projects");
  });

  it("ルートの projectId が数でなければ、要求を送らずに見つからないと伝える", () => {
    const { calls } = mockApi({});

    renderAt("/projects/abc/settings");

    expect(screen.getByRole("alert")).toHaveTextContent("プロジェクトが見つかりません");
    expect(calls).toHaveLength(0);
  });
});

describe("UI-PRJ-002 の入力の組み立て", () => {
  it("parseProjectId は正の整数だけを受ける", () => {
    expect(parseProjectId("3")).toBe(3);
    expect(parseProjectId("0")).toBeNull();
    expect(parseProjectId("03")).toBeNull();
    expect(parseProjectId("-1")).toBeNull();
    expect(parseProjectId("1.5")).toBeNull();
    expect(parseProjectId(undefined)).toBeNull();
    expect(parseProjectId("99999999999999999999")).toBeNull();
  });

  it("collectProjectChanges：変えていなければ空、空欄にした任意の項目は null、設定は変えた項目だけ", () => {
    const project: Project = PROJECT;
    const form = formOf(project);
    expect(collectProjectChanges(project, form)).toEqual({ ok: true, changes: {} });
    expect(
      collectProjectChanges(project, {
        ...form,
        targetFinishDate: "",
        ownerUserId: 2,
        progressMethodDefault: "FIFTY_FIFTY",
        numbers: { ...form.numbers, variance_rate: "0.2", variance_hours: " 8 " },
      }),
    ).toEqual({
      ok: true,
      changes: {
        target_finish_date: null,
        owner_user_id: 2,
        settings: { progress_method_default: "FIFTY_FIFTY", variance_rate: 0.2 },
      },
    });
  });

  it("collectProjectChanges：設定の数が空・負・数でなければ送らない（境界の 0 は送れる）", () => {
    const form = formOf(PROJECT);
    expect(collectProjectChanges(PROJECT, { ...form, numbers: { ...form.numbers, delay_extract_days: "" } })).toEqual({
      ok: false,
      message: "大きく遅れた活動の抽出の閾値（稼働日）は 0 以上の数を入れます",
    });
    expect(collectProjectChanges(PROJECT, { ...form, numbers: { ...form.numbers, variance_hours: "-0.1" } }).ok).toBe(
      false,
    );
    expect(collectProjectChanges(PROJECT, { ...form, numbers: { ...form.numbers, variance_hours: "abc" } }).ok).toBe(
      false,
    );
    expect(collectProjectChanges(PROJECT, { ...form, numbers: { ...form.numbers, delay_tolerance_days: "0" } })).toEqual(
      { ok: true, changes: { settings: { delay_tolerance_days: 0 } } },
    );
  });

  it("toScopeInputs：既存の項目は id を保ち、足した項目は id を持たない。空欄の説明は null", () => {
    expect(
      toScopeInputs([
        { key: "item-11", id: 11, kind: "IN", title: "A", description: "説明" },
        { key: "new-1", kind: "OUT", title: "B", description: "  " },
      ]),
    ).toEqual([
      { id: 11, kind: "IN", title: "A", description: "説明" },
      { kind: "OUT", title: "B", description: null },
    ]);
  });

  it("working_weekdays は月曜から日曜の 7 文字で往復する", () => {
    expect(parseWeekdays("1111100")).toEqual([true, true, true, true, true, false, false]);
    expect(formatWeekdays(parseWeekdays("0101011"))).toBe("0101011");
    expect(formatWeekdays([false, false, false, false, false, false, false])).toBe("0000000");
  });

  it("addHoliday は日付の順に足し、同じ日付は拒む", () => {
    const holidays = [{ holiday_date: "2026-05-04", name: null }];
    expect(addHoliday(holidays, { holiday_date: "2026-01-01", name: "元日" })).toEqual([
      { holiday_date: "2026-01-01", name: "元日" },
      { holiday_date: "2026-05-04", name: null },
    ]);
    expect(addHoliday(holidays, { holiday_date: "2026-05-04", name: "重なり" })).toBeNull();
  });

  it("orderRoles は 3 章の順に並べる", () => {
    expect(orderRoles(["VIEWER", "PM", "CONTRACTOR"])).toEqual(["PM", "CONTRACTOR", "VIEWER"]);
  });

  it("toRoleGrants は全てのロールを 3 章の順、権限を 9.1 の順で送り、画面の一覧に無い権限も保つ", () => {
    const table = tableOf({
      project_id: 3,
      roles: [
        { role: "PM", permissions: ["wbs.edit", "project.view", "future.permission"] },
        { role: "VIEWER", permissions: [] },
      ],
    });
    const grants = toRoleGrants(table);
    expect(grants).toHaveLength(7);
    expect(grants[0]).toEqual({ role: "PM", permissions: ["project.view", "wbs.edit", "future.permission"] });
    expect(grants[1]).toEqual({ role: "PLANNER", permissions: [] });
  });
});
