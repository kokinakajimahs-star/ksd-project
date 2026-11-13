import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import { setAuthToken } from "./api/client";
import { makeMe, makeProject, makeUser, storeSession } from "./test/fixtures";
import { jsonResponse, mockApi } from "./test/mockApi";
import { makeDelays } from "./test/prgFixtures";
import { makeMeetingReport, makeSharedView } from "./test/rptFixtures";
import { activityList, makeGantt, makeLightningLine } from "./test/schFixtures";
import { makeDictionary, makeMatrix, makeTrace, makeTree, makeValidation } from "./test/wbsFixtures";

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

describe("App（ルートの枠）", () => {
  it("ホームを描画する", () => {
    renderAt("/");
    expect(screen.getByRole("heading", { level: 1, name: "PMT" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "ホーム" })).toBeInTheDocument();
  });

  it("知らないルートでは見つからないことを示す", () => {
    renderAt("/no-such-page");
    expect(screen.getByRole("alert")).toHaveTextContent("ページが見つかりません");
    expect(screen.getByRole("link", { name: "ホームへ戻る" })).toHaveAttribute("href", "/");
  });
});

describe("App（プロジェクト基盤のルート。7.1）", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    setAuthToken(null);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    window.sessionStorage.clear();
    setAuthToken(null);
  });

  it("/projects は UI-PRJ-001 を描画し、ヘッダからも行ける", async () => {
    storeSession();
    mockApi({
      "GET /api/v1/me": () =>
        jsonResponse(200, makeMe({ system_permissions: [], user: makeUser({ is_system_admin: false }) })),
      "GET /api/v1/projects": () => jsonResponse(200, { items: [makeProject()], total: 1, limit: 100, offset: 0 }),
    });

    const { container } = renderAt("/projects");

    expect(await screen.findByRole("table", { name: "プロジェクトの一覧" })).toBeInTheDocument();
    expect(container.querySelector('[data-screen-id="UI-PRJ-001"]')).not.toBeNull();
    expect(screen.getByRole("link", { name: "プロジェクト" })).toHaveAttribute("href", "/projects");
  });

  it("/projects/:projectId/settings はログインが要る（ログインしていなければ UI-CMN-001 へ送る）", () => {
    const { calls } = mockApi({});

    const { container } = renderAt("/projects/3/settings");

    expect(container.querySelector('[data-screen-id="UI-CMN-001"]')).not.toBeNull();
    expect(container.querySelector('[data-screen-id="UI-PRJ-002"]')).toBeNull();
    expect(calls).toHaveLength(0);
  });

  it("/projects/:projectId/settings は UI-PRJ-002 を描画する", async () => {
    storeSession();
    mockApi({
      "GET /api/v1/me": () => jsonResponse(200, makeMe()),
      "GET /api/v1/projects/3": () => jsonResponse(200, makeProject()),
      "GET /api/v1/projects/3/scope-items": () =>
        jsonResponse(200, { project_id: 3, items: [], total: 0, limit: 1000, offset: 0 }),
      "GET /api/v1/projects/3/calendar": () =>
        jsonResponse(200, { project_id: 3, working_weekdays: "1111100", hours_per_day: 8, holidays: [] }),
      "GET /api/v1/projects/3/members": () =>
        jsonResponse(200, { project_id: 3, items: [], total: 0, limit: 100, offset: 0 }),
      "GET /api/v1/projects/3/role-permissions": () => jsonResponse(200, { project_id: 3, roles: [] }),
      "GET /api/v1/users": () => jsonResponse(200, { items: [], total: 0, limit: 1000, offset: 0 }),
    });

    const { container } = renderAt("/projects/3/settings");

    expect(await screen.findByRole("region", { name: "基本情報" })).toBeInTheDocument();
    expect(container.querySelector('[data-screen-id="UI-PRJ-002"]')).not.toBeNull();
  });
});

describe("App（WBS のルート。7.1）", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    setAuthToken(null);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    window.sessionStorage.clear();
    setAuthToken(null);
  });

  const viewer = () =>
    makeMe({
      user: makeUser({ is_system_admin: false }),
      system_permissions: [],
      projects: [{ project_id: 3, roles: ["VIEWER"], permissions: ["project.view"], view_level: "ALL" }],
    });
  const emptyPage = { items: [], total: 0, limit: 1000, offset: 0 };

  it.each(["/projects/3/wbs", "/projects/3/wbs/101", "/projects/3/wbs-review", "/projects/3/responsibility"])(
    "%s はログインが要る（ログインしていなければ UI-CMN-001 へ送る）",
    (path) => {
      const { calls } = mockApi({});
      const { container } = renderAt(path);
      expect(container.querySelector('[data-screen-id="UI-CMN-001"]')).not.toBeNull();
      expect(calls).toHaveLength(0);
    },
  );

  it("/projects/:projectId/wbs は UI-WBS-001 を描画する", async () => {
    storeSession();
    mockApi({
      "GET /api/v1/me": () => jsonResponse(200, viewer()),
      "GET /api/v1/projects/3/wbs": () => jsonResponse(200, makeTree()),
      "GET /api/v1/projects/3/members": () => jsonResponse(200, emptyPage),
      "GET /api/v1/projects/3/wbs/code-history": () => jsonResponse(200, emptyPage),
    });
    const { container } = renderAt("/projects/3/wbs");
    expect(await screen.findByRole("table", { name: "WBS のアウトライン" })).toBeInTheDocument();
    expect(container.querySelector('[data-screen-id="UI-WBS-001"]')).not.toBeNull();
  });

  it("/projects/:projectId/wbs/:elementId は UI-WBS-002 を描画する", async () => {
    storeSession();
    mockApi({
      "GET /api/v1/me": () => jsonResponse(200, viewer()),
      "GET /api/v1/projects/3/members": () => jsonResponse(200, emptyPage),
      "GET /api/v1/projects/3/wbs/elements/101/dictionary": () => jsonResponse(200, makeDictionary()),
      "GET /api/v1/projects/3/estimates/ELEMENT/101/history": () =>
        jsonResponse(200, { target_type: "ELEMENT", target_id: 101, target_exists: true, current_hours: 40, ...emptyPage }),
      "GET /api/v1/projects/3/wbs-codes/1.1/trace": () => jsonResponse(200, makeTrace()),
    });
    const { container } = renderAt("/projects/3/wbs/101");
    expect(await screen.findByLabelText("WBS辞書")).toBeInTheDocument();
    expect(container.querySelector('[data-screen-id="UI-WBS-002"]')).not.toBeNull();
    expect(container.querySelector('[data-screen-id="UI-WBS-001"]')).toBeNull();
  });

  it("/projects/:projectId/wbs-review は UI-WBS-003 を描画する", async () => {
    storeSession();
    mockApi({
      "GET /api/v1/me": () => jsonResponse(200, viewer()),
      "GET /api/v1/projects/3/members": () => jsonResponse(200, emptyPage),
      "GET /api/v1/projects/3/scope-items": () => jsonResponse(200, emptyPage),
      "GET /api/v1/projects/3/wbs": () => jsonResponse(200, makeTree()),
      "GET /api/v1/projects/3/wbs/validation": () => jsonResponse(200, makeValidation()),
      "GET /api/v1/projects/3/scope-baselines": () => jsonResponse(200, emptyPage),
      "GET /api/v1/projects/3/wbs/reviews": () => jsonResponse(200, emptyPage),
    });
    const { container } = renderAt("/projects/3/wbs-review");
    expect(await screen.findByRole("table", { name: "100% ルールの検査の結果" })).toBeInTheDocument();
    expect(container.querySelector('[data-screen-id="UI-WBS-003"]')).not.toBeNull();
  });

  it("/projects/:projectId/responsibility は UI-WBS-004 を描画する", async () => {
    storeSession();
    mockApi({
      "GET /api/v1/projects/3/wbs/responsibility-matrix": () => jsonResponse(200, makeMatrix()),
    });
    const { container } = renderAt("/projects/3/responsibility");
    expect(await screen.findByRole("table", { name: "責任分担表" })).toBeInTheDocument();
    expect(container.querySelector('[data-screen-id="UI-WBS-004"]')).not.toBeNull();
  });
});

describe("App（スケジュールのルート。7.1）", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    setAuthToken(null);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    window.sessionStorage.clear();
    setAuthToken(null);
  });

  const viewer = () =>
    makeMe({
      user: makeUser({ is_system_admin: false }),
      system_permissions: [],
      projects: [{ project_id: 3, roles: ["VIEWER"], permissions: ["project.view"], view_level: "ALL" }],
    });
  const emptyPage = { items: [], total: 0, limit: 1000, offset: 0 };

  it.each(["/projects/3/activities", "/projects/3/gantt", "/projects/3/baselines"])(
    "%s はログインが要る（ログインしていなければ UI-CMN-001 へ送る）",
    (path) => {
      const { calls } = mockApi({});
      const { container } = renderAt(path);
      expect(container.querySelector('[data-screen-id="UI-CMN-001"]')).not.toBeNull();
      expect(calls).toHaveLength(0);
    },
  );

  it("/projects/:projectId/activities は UI-SCH-001 を描画する", async () => {
    storeSession();
    mockApi({
      "GET /api/v1/me": () => jsonResponse(200, viewer()),
      "GET /api/v1/projects/3/activities": () => jsonResponse(200, activityList([])),
    });
    const { container } = renderAt("/projects/3/activities");
    expect(await screen.findByText("活動はまだありません")).toBeInTheDocument();
    expect(container.querySelector('[data-screen-id="UI-SCH-001"]')).not.toBeNull();
  });

  it("/projects/:projectId/gantt は UI-SCH-002 を描画する", async () => {
    storeSession();
    mockApi({
      "GET /api/v1/me": () => jsonResponse(200, viewer()),
      "GET /api/v1/projects/3/gantt": () => jsonResponse(200, makeGantt()),
      "GET /api/v1/projects/3/schedule-baselines": () => jsonResponse(200, emptyPage),
      "GET /api/v1/projects/3/status-reports": () => jsonResponse(200, emptyPage),
    });
    const { container } = renderAt("/projects/3/gantt");
    expect(await screen.findByRole("img", { name: "ガントチャート" })).toBeInTheDocument();
    expect(container.querySelector('[data-screen-id="UI-SCH-002"]')).not.toBeNull();
  });

  it("/projects/:projectId/baselines は UI-SCH-003 を描画する", async () => {
    storeSession();
    mockApi({
      "GET /api/v1/me": () => jsonResponse(200, viewer()),
      "GET /api/v1/projects/3/schedule-baselines": () => jsonResponse(200, emptyPage),
    });
    const { container } = renderAt("/projects/3/baselines");
    expect(await screen.findByText("保存した版はまだありません")).toBeInTheDocument();
    expect(container.querySelector('[data-screen-id="UI-SCH-003"]')).not.toBeNull();
  });
});

describe("App（進捗のルート。7.1）", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    setAuthToken(null);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    window.sessionStorage.clear();
    setAuthToken(null);
  });

  const viewer = () =>
    makeMe({
      user: makeUser({ is_system_admin: false }),
      system_permissions: [],
      projects: [{ project_id: 3, roles: ["VIEWER"], permissions: ["project.view"], view_level: "ALL" }],
    });

  it.each(["/projects/3/status-reports/41", "/projects/3/status-reports/41/delays"])(
    "%s はログインが要る（ログインしていなければ UI-CMN-001 へ送る）",
    (path) => {
      const { calls } = mockApi({});
      const { container } = renderAt(path);
      expect(container.querySelector('[data-screen-id="UI-CMN-001"]')).not.toBeNull();
      expect(calls).toHaveLength(0);
    },
  );

  it("/projects/:projectId/status-reports/:reportId は UI-PRG-001 を描画する", async () => {
    storeSession();
    mockApi({
      "GET /api/v1/me": () => jsonResponse(200, viewer()),
      "GET /api/v1/projects/3/status-reports": () => jsonResponse(200, { items: [], total: 0, limit: 1000, offset: 0 }),
      "GET /api/v1/projects/3/status-reports/41/lightning-line": () => jsonResponse(200, makeLightningLine([])),
    });
    const { container } = renderAt("/projects/3/status-reports/41");
    expect(await screen.findByText("活動がありません")).toBeInTheDocument();
    expect(container.querySelector('[data-screen-id="UI-PRG-001"]')).not.toBeNull();
  });

  it("/projects/:projectId/status-reports/:reportId/delays は UI-PRG-002 を描画する", async () => {
    storeSession();
    mockApi({
      "GET /api/v1/projects/3/status-reports/41/delays": () =>
        jsonResponse(200, makeDelays([], { combined_milestone_slips: [], combined_project_finish: null })),
    });
    const { container } = renderAt("/projects/3/status-reports/41/delays");
    expect(await screen.findByText("遅延している活動はありません")).toBeInTheDocument();
    expect(container.querySelector('[data-screen-id="UI-PRG-002"]')).not.toBeNull();
  });
});

describe("App（変更管理のルート。7.1）", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    setAuthToken(null);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    window.sessionStorage.clear();
    setAuthToken(null);
  });

  const viewer = () =>
    makeMe({
      user: makeUser({ is_system_admin: false }),
      system_permissions: [],
      projects: [{ project_id: 3, roles: ["VIEWER"], permissions: ["project.view"], view_level: "ALL" }],
    });
  const emptyPage = { items: [], total: 0, limit: 100, offset: 0 };
  const members = () => jsonResponse(200, { project_id: 3, items: [], total: 0, limit: 1000, offset: 0 });

  it.each(["/projects/3/change-requests", "/projects/3/corrective-actions", "/projects/3/audit-logs"])(
    "%s はログインが要る（ログインしていなければ UI-CMN-001 へ送る）",
    (path) => {
      const { calls } = mockApi({});
      const { container } = renderAt(path);
      expect(container.querySelector('[data-screen-id="UI-CMN-001"]')).not.toBeNull();
      expect(calls).toHaveLength(0);
    },
  );

  it("/projects/:projectId/change-requests は UI-CHG-001 を描画する", async () => {
    storeSession();
    mockApi({
      "GET /api/v1/me": () => jsonResponse(200, viewer()),
      "GET /api/v1/projects/3/members": members,
      "GET /api/v1/projects/3/change-requests": () => jsonResponse(200, emptyPage),
    });
    const { container } = renderAt("/projects/3/change-requests");
    expect(await screen.findByText("変更要求はありません")).toBeInTheDocument();
    expect(container.querySelector('[data-screen-id="UI-CHG-001"]')).not.toBeNull();
  });

  it("/projects/:projectId/corrective-actions は UI-CHG-002 を描画する", async () => {
    storeSession();
    mockApi({
      "GET /api/v1/me": () => jsonResponse(200, viewer()),
      "GET /api/v1/projects/3/members": members,
      "GET /api/v1/projects/3/corrective-actions": () => jsonResponse(200, emptyPage),
    });
    const { container } = renderAt("/projects/3/corrective-actions");
    expect(await screen.findByText("是正措置はありません")).toBeInTheDocument();
    expect(container.querySelector('[data-screen-id="UI-CHG-002"]')).not.toBeNull();
  });

  it("/projects/:projectId/audit-logs は UI-CHG-003 を描画する", async () => {
    storeSession();
    mockApi({
      "GET /api/v1/projects/3/members": members,
      "GET /api/v1/projects/3/audit-logs": () => jsonResponse(200, emptyPage),
    });
    const { container } = renderAt("/projects/3/audit-logs");
    expect(await screen.findByText("該当する変更履歴はありません")).toBeInTheDocument();
    expect(container.querySelector('[data-screen-id="UI-CHG-003"]')).not.toBeNull();
  });
});

describe("App（工数のルート。7.1）", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    setAuthToken(null);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    window.sessionStorage.clear();
    setAuthToken(null);
  });

  const emptyPage = { items: [], total: 0, limit: 1000, offset: 0 };
  const doer = () =>
    makeMe({
      user: makeUser({ id: 7, is_system_admin: false }),
      system_permissions: [],
      projects: [{ project_id: 3, roles: ["MEMBER"], permissions: ["project.view", "effort.enter_own"], view_level: "ALL" }],
    });

  it.each(["/timesheet", "/projects/3/effort"])(
    "%s はログインが要る（ログインしていなければ UI-CMN-001 へ送る）",
    (path) => {
      const { calls } = mockApi({});
      const { container } = renderAt(path);
      expect(container.querySelector('[data-screen-id="UI-CMN-001"]')).not.toBeNull();
      expect(calls).toHaveLength(0);
    },
  );

  it("/timesheet は UI-EFF-001 を描画し、ヘッダからも行ける（7.2：ログイン → タイムシート）", async () => {
    storeSession();
    mockApi({
      "GET /api/v1/me": () => jsonResponse(200, doer()),
      "GET /api/v1/projects": () => jsonResponse(200, { ...emptyPage, items: [makeProject()], total: 1 }),
      "GET /api/v1/projects/3/assignments": () => jsonResponse(200, emptyPage),
      "GET /api/v1/time-entries": () => jsonResponse(200, emptyPage),
    });
    const { container } = renderAt("/timesheet?week=2026-11-09");

    expect(await screen.findByText("入力できる活動がありません（自分の割当の活動がありません）")).toBeInTheDocument();
    expect(container.querySelector('[data-screen-id="UI-EFF-001"]')).not.toBeNull();
    expect(screen.getByRole("link", { name: "タイムシート" })).toHaveAttribute("href", "/timesheet");
  });

  it("/projects/:projectId/effort は UI-EFF-002 を描画する", async () => {
    storeSession();
    const values = { planned: 0, baseline: null, actual: 0, etc: 0, eac: 0 };
    mockApi({
      "GET /api/v1/me": () => jsonResponse(200, doer()),
      "GET /api/v1/projects/3/status-reports": () => jsonResponse(200, emptyPage),
      "GET /api/v1/projects/3/schedule-baselines": () => jsonResponse(200, emptyPage),
      "GET /api/v1/projects/3/effort/summary": () =>
        jsonResponse(200, { axis: "wbs", report: null, report_specified: false, baseline: null, total: values, rows: [] }),
      "GET /api/v1/projects/3/effort/variance": () =>
        jsonResponse(409, { error: { code: "INVALID_STATE", message: "スケジュールベースラインがありません", details: [] } }),
    });
    const { container } = renderAt("/projects/3/effort");

    expect(await screen.findByRole("table", { name: "工数の集計（WBS）" })).toBeInTheDocument();
    expect(container.querySelector('[data-screen-id="UI-EFF-002"]')).not.toBeNull();
  });
});

describe("App（入出力のルート。7.1）", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    setAuthToken(null);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    window.sessionStorage.clear();
    setAuthToken(null);
  });

  it("/projects/:projectId/import-export はログインが要る（ログインしていなければ UI-CMN-001 へ送る）", () => {
    const { calls } = mockApi({});
    const { container } = renderAt("/projects/3/import-export");
    expect(container.querySelector('[data-screen-id="UI-CMN-001"]')).not.toBeNull();
    expect(calls).toHaveLength(0);
  });

  it("/projects/:projectId/import-export は UI-INT-001 を描画する", async () => {
    storeSession();
    mockApi({
      "GET /api/v1/me": () =>
        jsonResponse(
          200,
          makeMe({
            user: makeUser({ id: 7, is_system_admin: false }),
            system_permissions: [],
            projects: [
              { project_id: 3, roles: ["PLANNER"], permissions: ["project.view", "data.export"], view_level: "ALL" },
            ],
          }),
        ),
    });
    const { container } = renderAt("/projects/3/import-export");

    expect(await screen.findByRole("form", { name: "出力する" })).toBeInTheDocument();
    expect(container.querySelector('[data-screen-id="UI-INT-001"]')).not.toBeNull();
  });
});

describe("App（レポート・共有のルート。7.1）", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    setAuthToken(null);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    window.sessionStorage.clear();
    setAuthToken(null);
  });

  it("/projects/:projectId/status-reports/:reportId/meeting はログインが要る（ログインしていなければ UI-CMN-001 へ送る）", () => {
    const { calls } = mockApi({});
    const { container } = renderAt("/projects/3/status-reports/41/meeting");
    expect(container.querySelector('[data-screen-id="UI-CMN-001"]')).not.toBeNull();
    expect(calls).toHaveLength(0);
  });

  it("/projects/:projectId/status-reports/:reportId/meeting は UI-RPT-001 を描画する", async () => {
    storeSession();
    mockApi({
      "GET /api/v1/me": () =>
        jsonResponse(
          200,
          makeMe({
            user: makeUser({ is_system_admin: false }),
            system_permissions: [],
            projects: [{ project_id: 3, roles: ["VIEWER"], permissions: ["project.view"], view_level: "ALL" }],
          }),
        ),
      "GET /api/v1/projects/3/status-reports/41/meeting-report": () => jsonResponse(200, makeMeetingReport()),
    });
    const { container } = renderAt("/projects/3/status-reports/41/meeting");

    expect(await screen.findByRole("table", { name: "遅延一覧" })).toBeInTheDocument();
    expect(container.querySelector('[data-screen-id="UI-RPT-001"]')).not.toBeNull();
  });

  it("/shared/:token はログインせずに UI-RPT-002 を描画する（ログインの画面へ送らない）", async () => {
    const { calls } = mockApi({ "GET /api/v1/shared/tok-abc_123": () => jsonResponse(200, makeSharedView()) });
    const { container } = renderAt("/shared/tok-abc_123");

    expect(await screen.findByRole("table", { name: "遅延一覧" })).toBeInTheDocument();
    expect(container.querySelector('[data-screen-id="UI-RPT-002"]')).not.toBeNull();
    expect(container.querySelector('[data-screen-id="UI-CMN-001"]')).toBeNull();
    expect(calls.map((c) => c.url)).toEqual(["/api/v1/shared/tok-abc_123"]);
  });
});
