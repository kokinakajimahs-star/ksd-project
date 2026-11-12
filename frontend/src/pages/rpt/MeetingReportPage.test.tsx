import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setAuthToken } from "../../api/client";
import { makeMe, makeUser } from "../../test/fixtures";
import { errorResponse, jsonResponse, mockApi } from "../../test/mockApi";
import {
  makeMeetingReport,
  makeReportKpi,
  makeReportMilestone,
  makeShareLinkCreated,
} from "../../test/rptFixtures";
import { localDateTimeToIso } from "./labels";
import MeetingReportPage from "./MeetingReportPage";

const FUTURE = { v7_startTransition: true, v7_relativeSplatPath: true };
const BASE = "/api/v1/projects/3";
const REPORT_URL = `${BASE}/status-reports/41/meeting-report`;

function meWith(permissions: string[]) {
  return makeMe({
    user: makeUser({ id: 5, is_system_admin: false }),
    system_permissions: [],
    projects: [{ project_id: 3, roles: ["PM"], permissions, view_level: "ALL" }],
  });
}

function renderAt(path = "/projects/3/status-reports/41/meeting") {
  return render(
    <MemoryRouter initialEntries={[path]} future={FUTURE}>
      <Routes>
        <Route path="/projects/:projectId/status-reports/:reportId/meeting" element={<MeetingReportPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("UI-RPT-001 定例会議レポート", () => {
  beforeEach(() => {
    setAuthToken("t-pm");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    setAuthToken(null);
  });

  it("上段にガント＋イナズマ線、下段に遅延一覧・マイルストーン・主要KPIを並べる（7.4・RPT-01）", async () => {
    const { calls } = mockApi({
      "GET /api/v1/me": () => jsonResponse(200, meWith(["project.view"])),
      [`GET ${REPORT_URL}`]: () => jsonResponse(200, makeMeetingReport()),
    });
    const { container } = renderAt();

    const delays = await screen.findByRole("table", { name: "遅延一覧" });
    expect(container.querySelector('[data-screen-id="UI-RPT-001"]')).not.toBeNull();
    expect(calls.map((c) => c.url).sort()).toEqual(["/api/v1/me", REPORT_URL]);
    expect(screen.getByText(/報告日 2026-12-10（入力中）/)).toHaveTextContent(
      "値：計算値（確定前）・表示の範囲 2026-11-12〜2027-01-07",
    );

    // 下段左：遅延一覧（サーバの順＝遅れの大きい順・遅延理由・完了見込み）
    const delayRows = within(delays).getAllByRole("row").slice(1);
    expect(delayRows).toHaveLength(1);
    expect(delayRows[0]).toHaveAttribute("data-activity-id", "21");
    expect(delayRows[0]).toHaveTextContent("資料作成");
    expect(delayRows[0]).toHaveTextContent("-4.75");
    expect(delayRows[0]).toHaveTextContent("25%");
    expect(delayRows[0]).toHaveTextContent("レビュー待ち");
    expect(delayRows[0]).toHaveTextContent("2026-12-21");
    expect(delayRows[0]).toHaveTextContent("2026-12-26（入力）");
    expect(delayRows[0]).toHaveTextContent("はい");

    // 下段中：マイルストーンの状況（予定日・見込み日・ずれ）
    const milestones = screen.getByRole("table", { name: "マイルストーンの状況" });
    const milestoneRows = within(milestones).getAllByRole("row").slice(1);
    expect(milestoneRows[0]).toHaveTextContent("◆ 1.3 公開");
    expect(milestoneRows[0]).toHaveTextContent("2026-12-21");
    expect(milestoneRows[0]).toHaveTextContent("2026-12-28");
    expect(milestoneRows[0]).toHaveTextContent("+5 稼働日");

    // 下段右：主要KPI
    expect(screen.getByRole("heading", { name: "主要KPI" })).toBeInTheDocument();
    expect(container.querySelector('[data-kpi="overall_progress"]')).toHaveTextContent("37.5%");
    expect(container.querySelector('[data-kpi="behind_count"]')).toHaveTextContent("1 件");
    expect(container.querySelector('[data-kpi="eac_variance_hours"]')).toHaveTextContent(
      "+10 人時（EAC 130 人時 ／ 基準 120 人時）",
    );
    expect(container.querySelector('[data-kpi="min_critical_float"]')).toHaveTextContent("0 稼働日");
    expect(container.querySelector('[data-kpi="overdue_action_count"]')).toHaveTextContent("2 件");

    // 印刷用の CSS（A3 横）
    const style = container.querySelector("style");
    expect(style?.textContent).toContain("@page { size: A3 landscape;");
  });

  it("上段は報告日の前後 4 週だけを暦日で描き、報告日線とイナズマ線の頂点を判定の色で描く（7.3・7.4）", async () => {
    mockApi({
      "GET /api/v1/me": () => jsonResponse(200, meWith(["project.view"])),
      [`GET ${REPORT_URL}`]: () => jsonResponse(200, makeMeetingReport()),
    });
    const { container } = renderAt();
    const svg = await screen.findByRole("img", { name: "ガントチャート" });

    // 2026-11-12〜2027-01-07 の 57 日 × 24px（日）＋ 見出しの列 280px。ガントの範囲（10/1〜3/31）では描かない
    expect(svg).toHaveAttribute("width", String(280 + 57 * 24));
    // 紙の 1 枚に載せるため、全ての行を描く（2 行）
    expect(container.querySelectorAll('[data-layer="row-labels"] [data-row]')).toHaveLength(2);
    // 範囲の外へ出るバーは切り取る
    expect(container.querySelector('[data-layer="planned"]')).toHaveAttribute("clip-path", "url(#gantt-plot-clip)");

    // 報告日線：12/10 の終業（11/12 から 28 日＋1 日分）
    const reportLine = container.querySelector('[data-layer="report-line"] line');
    expect(reportLine).toHaveAttribute("x1", String(280 + 29 * 24));
    expect(reportLine?.querySelector("title")).toHaveTextContent("報告日 2026-12-10");

    // 稲妻チャート.txt §6 の例：資料作成の進捗ポイントは 12/6 の 1/4（11/12 から 24.25 日）。遅れは赤
    const vertices = container.querySelectorAll('[data-layer="lightning"] circle');
    expect(vertices).toHaveLength(2);
    expect(vertices[0]).toHaveAttribute("cx", String(280 + 24.25 * 24));
    expect(vertices[0]).toHaveAttribute("data-judgement", "BEHIND");
    expect(vertices[0]).toHaveAttribute("fill", "#d32f2f");
    expect(vertices[0]?.querySelector("title")).toHaveTextContent("1.1 資料作成：遅れ（2026-12-06）");
    // 未着手（予定内）は予定どおりの色
    expect(vertices[1]).toHaveAttribute("data-judgement", "NOT_STARTED");
    expect(vertices[1]).toHaveAttribute("fill", "#388e3c");

    // 表示の単位は 1 日あたりの横幅だけを変える
    fireEvent.change(screen.getByLabelText("表示の単位"), { target: { value: "week" } });
    expect(screen.getByRole("img", { name: "ガントチャート" })).toHaveAttribute("width", String(280 + 57 * 6));
  });

  it("印刷するボタンでブラウザの印刷を呼ぶ", async () => {
    mockApi({
      "GET /api/v1/me": () => jsonResponse(200, meWith(["project.view"])),
      [`GET ${REPORT_URL}`]: () => jsonResponse(200, makeMeetingReport()),
    });
    const print = vi.spyOn(window, "print").mockImplementation(() => undefined);
    renderAt();
    await screen.findByRole("table", { name: "遅延一覧" });

    fireEvent.click(screen.getByRole("button", { name: "印刷する" }));
    expect(print).toHaveBeenCalledTimes(1);
  });

  it("確定済みは写しの値と出し、求められない KPI・空の一覧はその旨を出す", async () => {
    mockApi({
      "GET /api/v1/me": () => jsonResponse(200, meWith(["project.view"])),
      [`GET ${REPORT_URL}`]: () =>
        jsonResponse(
          200,
          makeMeetingReport({
            status: "FINALIZED",
            source: "SNAPSHOT",
            delays: [],
            milestones: [],
            kpi: makeReportKpi({
              overall_progress: null,
              behind_count: 0,
              eac_hours: 100,
              baseline_hours: null,
              eac_variance_hours: null,
              min_critical_float: null,
              overdue_action_count: 0,
            }),
          }),
        ),
    });
    const { container } = renderAt();

    expect(await screen.findByText("遅延している活動はありません")).toBeInTheDocument();
    expect(screen.getByText(/報告日 2026-12-10（確定済み）/)).toHaveTextContent("値：確定時の写し");
    expect(screen.getByText("マイルストーンはありません")).toBeInTheDocument();
    expect(container.querySelector('[data-kpi="overall_progress"]')).toHaveTextContent("算定できない");
    expect(container.querySelector('[data-kpi="eac_variance_hours"]')).toHaveTextContent(
      "基準が無いため求められない（EAC 100 人時）",
    );
    expect(container.querySelector('[data-kpi="min_critical_float"]')).toHaveTextContent("クリティカルな活動が無い");
    expect(container.querySelector('[data-kpi="behind_count"]')).toHaveTextContent("0 件");
  });

  it("委託先だけで読む人には KPI を出さず、自分の割当だけを表示している旨を出す。今の計画に無い頂点の数も出す", async () => {
    mockApi({
      "GET /api/v1/me": () => jsonResponse(200, meWith(["project.view"])),
      [`GET ${REPORT_URL}`]: () =>
        jsonResponse(
          200,
          makeMeetingReport({
            restricted: true,
            kpi: null,
            milestones: [makeReportMilestone({ slip_days: 0, forecast_date: "2026-12-21" })],
            lightning: { ...makeMeetingReport().lightning, missing_activity_ids: [30, null] },
          }),
        ),
    });
    const { container } = renderAt();

    expect(await screen.findByText("主要 KPI は表示できません")).toBeInTheDocument();
    expect(container.querySelector("[data-kpi]")).toBeNull();
    expect(screen.getByText(/自分の割当の活動だけを表示している/)).toBeInTheDocument();
    expect(screen.getByText("今の計画に無い活動の頂点 2 件は描いていない")).toBeInTheDocument();
    const milestones = screen.getByRole("table", { name: "マイルストーンの状況" });
    expect(within(milestones).getAllByRole("row")[1]).toHaveTextContent("ずれなし");
  });

  it("サーバのエラー（確定前で日程の無い活動がある 409 など）はその文言を出す", async () => {
    mockApi({
      "GET /api/v1/me": () => jsonResponse(200, meWith(["project.view"])),
      [`GET ${REPORT_URL}`]: () => errorResponse(409, "INVALID_STATE", "日程が計算されていない活動があります"),
    });
    renderAt();

    expect(await screen.findByRole("alert")).toHaveTextContent("日程が計算されていない活動があります");
    expect(screen.queryByRole("table", { name: "遅延一覧" })).toBeNull();
    expect(screen.getByRole("button", { name: "印刷する" })).toBeDisabled();
  });

  it("ID の形が正しくなければ API を呼ばない", () => {
    const { calls } = mockApi({});
    renderAt("/projects/3/status-reports/abc/meeting");

    expect(screen.getByRole("alert")).toHaveTextContent("報告日が見つかりません");
    expect(calls).toHaveLength(0);
  });

  it("行き先（進捗入力・遅延分析・ガント）へのリンクを置く（7.2）", async () => {
    mockApi({
      "GET /api/v1/me": () => jsonResponse(200, meWith(["project.view"])),
      [`GET ${REPORT_URL}`]: () => jsonResponse(200, makeMeetingReport()),
    });
    renderAt();
    await screen.findByRole("table", { name: "遅延一覧" });

    expect(screen.getByRole("link", { name: "進捗入力へ" })).toHaveAttribute("href", "/projects/3/status-reports/41");
    expect(screen.getByRole("link", { name: "遅延分析" })).toHaveAttribute(
      "href",
      "/projects/3/status-reports/41/delays",
    );
    expect(screen.getByRole("link", { name: "ガントチャート" })).toHaveAttribute("href", "/projects/3/gantt");
  });
});

describe("UI-RPT-001 共有リンク（API-RPT-002・003。RPT-03）", () => {
  beforeEach(() => {
    setAuthToken("t-pm");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    setAuthToken(null);
  });

  it("share.manage の無い人には共有リンクの部品を出さない（7.2）", async () => {
    mockApi({
      "GET /api/v1/me": () => jsonResponse(200, meWith(["project.view"])),
      [`GET ${REPORT_URL}`]: () => jsonResponse(200, makeMeetingReport()),
    });
    renderAt();
    await screen.findByRole("table", { name: "遅延一覧" });

    expect(screen.queryByRole("form", { name: "共有リンクを作る" })).toBeNull();
  });

  it("範囲と有効期限で共有リンクを作り、作ったときだけ URL を見せる。確認の段を経て失効させる", async () => {
    const created = makeShareLinkCreated({ scope: "GANTT" });
    const { calls } = mockApi({
      "GET /api/v1/me": () => jsonResponse(200, meWith(["project.view", "share.manage"])),
      [`GET ${REPORT_URL}`]: () => jsonResponse(200, makeMeetingReport()),
      [`POST ${BASE}/share-links`]: () => jsonResponse(201, created),
      [`DELETE ${BASE}/share-links/71`]: () => jsonResponse(204, null),
    });
    renderAt();

    const form = await screen.findByRole("form", { name: "共有リンクを作る" });
    fireEvent.change(within(form).getByLabelText("見せる範囲"), { target: { value: "GANTT" } });
    fireEvent.change(within(form).getByLabelText("有効期限"), { target: { value: "2026-12-31T18:00" } });
    fireEvent.click(within(form).getByRole("button", { name: "共有リンクを作る" }));

    const url = await screen.findByLabelText("共有リンクの URL（71）");
    const post = calls.find((c) => c.method === "POST");
    // 有効期限は時差つきの時刻（端末の時刻を UTC にしたもの）で送る
    expect(post?.body).toEqual({ scope: "GANTT", expires_at: new Date("2026-12-31T18:00").toISOString() });
    expect((post?.body as { expires_at: string }).expires_at).toMatch(/Z$/);
    expect(url).toHaveValue(`${window.location.origin}/shared/tok-abc_123`);
    const table = screen.getByRole("table", { name: "この画面で作った共有リンク" });
    expect(within(table).getAllByRole("row")[1]).toHaveTextContent("ガントチャート（最新の進捗）");

    // 失効は戻せないので、確認の段を 1 つ挟む
    fireEvent.click(within(table).getByRole("button", { name: "失効させる…" }));
    fireEvent.click(within(table).getByRole("button", { name: "やめる" }));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);

    fireEvent.click(within(table).getByRole("button", { name: "失効させる…" }));
    fireEvent.click(within(table).getByRole("button", { name: "失効させる" }));

    expect(await within(table).findByText("失効済み")).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "DELETE").map((c) => c.url)).toEqual([`${BASE}/share-links/71`]);
    expect(screen.queryByLabelText("共有リンクの URL（71）")).toBeNull();
  });

  it("有効期限が空なら送らない。サーバの拒否（今以前の期限など）は文言を出す", async () => {
    const { calls } = mockApi({
      "GET /api/v1/me": () => jsonResponse(200, meWith(["project.view", "share.manage"])),
      [`GET ${REPORT_URL}`]: () => jsonResponse(200, makeMeetingReport()),
      [`POST ${BASE}/share-links`]: () => errorResponse(400, "VALIDATION_ERROR", "入力に誤りがあります"),
    });
    renderAt();

    const form = await screen.findByRole("form", { name: "共有リンクを作る" });
    fireEvent.submit(form);
    expect(calls.some((c) => c.method === "POST")).toBe(false);
    expect(screen.getByRole("alert")).toHaveTextContent("有効期限を入力してください");

    fireEvent.change(within(form).getByLabelText("有効期限"), { target: { value: "2020-01-01T09:00" } });
    fireEvent.submit(form);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("入力に誤りがあります"));
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(1);
  });
});

describe("localDateTimeToIso（有効期限の入力を時差つきの時刻にする）", () => {
  it("datetime-local の値を UTC の ISO 8601 にし、空・形の違う値は null", () => {
    expect(localDateTimeToIso("2026-12-31T18:00")).toBe(new Date("2026-12-31T18:00").toISOString());
    expect(localDateTimeToIso("2026-12-31T18:00:30")).toBe(new Date("2026-12-31T18:00:30").toISOString());
    expect(localDateTimeToIso("")).toBeNull();
    expect(localDateTimeToIso("2026-12-31")).toBeNull();
    expect(localDateTimeToIso("2026-13-45T99:99")).toBeNull();
  });
});
