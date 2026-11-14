// 画面写し（playwright.config.ts の説明を見る）。画面ごとに、ログイン済みの状態（sessionStorage）と API の応答（試験の
// データ src/test/*Fixtures.ts）を与えて開き、data-screen-id が出たら全体を撮る。定めていない要求・未捕捉の例外・
// console のエラーは画面ごとに書き出すだけで、合否にしない（判定は ADR-011 の B：[ui_runtime] の側）
import { execSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { test } from "@playwright/test";

import { SESSION_STORAGE_KEY } from "../src/auth/session";
import { makeChangeRequest } from "../src/test/chgFixtures";
import { makeAssignment, makeSummary, makeSummaryRow, makeTimeEntry, page as effPage } from "../src/test/effFixtures";
import { makeMe, makeMember, makeProject, makeUser } from "../src/test/fixtures";
import { makeMeetingReport } from "../src/test/rptFixtures";
import {
  activityList,
  makeActivity,
  makeBar,
  makeGantt,
  makeGanttRow,
  makeLightningLine,
  makeLightningRow,
  makeStatusReport,
} from "../src/test/schFixtures";
import { makeTree, makeValidation } from "../src/test/wbsFixtures";

const repoRoot = fileURLToPath(new URL("../..", import.meta.url));

function shotDir(): string {
  const sha = execSync("git rev-parse --short=12 HEAD", { cwd: repoRoot }).toString().trim();
  const dirty = execSync("git status --porcelain", { cwd: repoRoot }).toString().trim() !== "";
  const dir = `${repoRoot}.kloop/ui-shots/${sha}${dirty ? "-dirty" : ""}`;
  mkdirSync(dir, { recursive: true });
  return dir;
}

const DIR = shotDir();

const emptyPage = { items: [], total: 0, limit: 1000, offset: 0 };
const project = makeProject();
const session = { token: "token-shots", expiresAt: "2999-01-01T00:00:00Z", user: makeUser() };

/** どの画面でも読む応答（管理者・プロジェクト 3） */
const COMMON: Record<string, unknown> = {
  "GET /api/v1/me": makeMe(),
  "GET /api/v1/projects": { items: [project, makeProject({ id: 4, code: "PRJ-B", name: "基幹刷新" })], total: 2, limit: 100, offset: 0 },
  "GET /api/v1/projects/3": project,
  "GET /api/v1/projects/3/members": { project_id: 3, items: [makeMember()], total: 1, limit: 1000, offset: 0 },
  "GET /api/v1/users": { items: [makeUser()], total: 1, limit: 1000, offset: 0 },
};

interface Screen {
  id: string;
  path: string;
  login?: boolean;
  routes?: Record<string, unknown>;
}

const SCREENS: Screen[] = [
  { id: "UI-CMN-001", path: "/login", login: false },
  { id: "UI-PRJ-001", path: "/projects" },
  {
    id: "UI-PRJ-002",
    path: "/projects/3/settings",
    routes: {
      "GET /api/v1/projects/3/scope-items": { project_id: 3, ...emptyPage },
      "GET /api/v1/projects/3/calendar": { project_id: 3, working_weekdays: "1111100", hours_per_day: 8, holidays: [] },
      "GET /api/v1/projects/3/role-permissions": { project_id: 3, roles: [] },
    },
  },
  {
    id: "UI-WBS-001",
    path: "/projects/3/wbs",
    routes: {
      "GET /api/v1/projects/3/wbs": makeTree(),
      "GET /api/v1/projects/3/wbs/code-history": emptyPage,
    },
  },
  {
    id: "UI-WBS-003",
    path: "/projects/3/wbs-review",
    routes: {
      "GET /api/v1/projects/3/scope-items": emptyPage,
      "GET /api/v1/projects/3/wbs": makeTree(),
      "GET /api/v1/projects/3/wbs/validation": makeValidation(),
      "GET /api/v1/projects/3/scope-baselines": emptyPage,
      "GET /api/v1/projects/3/wbs/reviews": emptyPage,
    },
  },
  {
    id: "UI-SCH-001",
    path: "/projects/3/activities",
    routes: {
      "GET /api/v1/projects/3/activities": activityList([
        makeActivity(),
        makeActivity({ id: 22, wbs_code: "1.2", work_package_name: "基本設計" }),
      ]),
    },
  },
  {
    id: "UI-SCH-002",
    path: "/projects/3/gantt",
    routes: {
      "GET /api/v1/projects/3/gantt": makeGantt({
        rows: [
          makeGanttRow(),
          makeGanttRow({
            row_index: 1,
            activity_id: 22,
            wbs_code: "1.2",
            name: "基本設計",
            work_package_name: "基本設計",
            is_critical: true,
            total_float: 0,
            planned: makeBar("PLANNED", "2026-12-08", "2026-12-15"),
            actual: makeBar("ACTUAL", "2026-12-08", "2026-12-12"),
            percent_complete: 40,
          }),
        ],
      }),
      "GET /api/v1/projects/3/schedule-baselines": emptyPage,
      "GET /api/v1/projects/3/status-reports": emptyPage,
    },
  },
  {
    id: "UI-PRG-001",
    path: "/projects/3/status-reports/41",
    routes: {
      "GET /api/v1/projects/3/status-reports": { ...emptyPage, items: [makeStatusReport()], total: 1 },
      "GET /api/v1/projects/3/status-reports/41/lightning-line": makeLightningLine([makeLightningRow()]),
    },
  },
  {
    id: "UI-EFF-001",
    path: "/timesheet?week=2026-11-09",
    routes: {
      "GET /api/v1/projects/3/assignments": effPage([makeAssignment()]),
      "GET /api/v1/time-entries": effPage([makeTimeEntry()]),
    },
  },
  {
    id: "UI-EFF-002",
    path: "/projects/3/effort",
    routes: {
      "GET /api/v1/projects/3/status-reports": emptyPage,
      "GET /api/v1/projects/3/schedule-baselines": emptyPage,
      "GET /api/v1/projects/3/effort/summary": makeSummary({ rows: [makeSummaryRow()] }),
    },
  },
  {
    id: "UI-CHG-001",
    path: "/projects/3/change-requests",
    routes: { "GET /api/v1/projects/3/change-requests": { ...emptyPage, items: [makeChangeRequest()], total: 1 } },
  },
  {
    id: "UI-RPT-001",
    path: "/projects/3/status-reports/41/meeting",
    routes: {
      "GET /api/v1/projects/3/status-reports/41/meeting-report": makeMeetingReport(),
      "GET /api/v1/projects/3/share-links": { ...emptyPage, project_id: 3 },
    },
  },
];

const notes: string[] = [];

test.afterAll(() => {
  writeFileSync(`${DIR}/notes.md`, `# 画面写しの記録（合否ではない）\n\n${notes.join("\n")}\n`);
});

for (const screen of SCREENS) {
  test(`${screen.id} ${screen.path}`, async ({ page }) => {
    const unknown: string[] = [];
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
    page.on("console", (m) => {
      if (m.type() === "error") {
        errors.push(`console: ${m.text()}`);
      }
    });
    if (screen.login !== false) {
      await page.addInitScript(
        ([key, value]) => window.sessionStorage.setItem(key, value),
        [SESSION_STORAGE_KEY, JSON.stringify(session)] as const,
      );
    }
    await page.route("**/api/v1/**", async (route) => {
      const request = route.request();
      const key = `${request.method()} ${new URL(request.url()).pathname}`;
      const body = screen.routes?.[key] ?? COMMON[key];
      if (body === undefined) {
        unknown.push(key);
        await route.fulfill({
          status: 404,
          json: { error: { code: "NOT_FOUND", message: "画面写しで定めていない要求", details: [] } },
        });
        return;
      }
      await route.fulfill({ status: 200, json: body });
    });

    await page.goto(screen.path);
    await page.locator(`[data-screen-id="${screen.id}"]`).first().waitFor();
    await page.waitForLoadState("networkidle");
    await page.screenshot({ path: `${DIR}/${screen.id}.png`, fullPage: true });

    notes.push(
      `- ${screen.id} \`${screen.path}\`: 定めていない要求 ${unknown.length === 0 ? "なし" : unknown.join("・")}／` +
        `エラー ${errors.length === 0 ? "なし" : errors.join("・")}`,
    );
  });
}
