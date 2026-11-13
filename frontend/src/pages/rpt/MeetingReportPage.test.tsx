import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setAuthToken } from "../../api/client";
import { HEADER_HEIGHT, ROW_HEIGHT } from "../../components/gantt/scale";
import { makeMe, makeUser } from "../../test/fixtures";
import { errorResponse, jsonResponse, mockApi } from "../../test/mockApi";
import type { MeetingReport, ShareLink } from "../../api/rpt";
import {
  makeMeetingReport,
  makeReportDelay,
  makeReportKpi,
  makeReportMilestone,
  makeShareLink,
  makeShareLinkCreated,
  shareLinkList,
} from "../../test/rptFixtures";
import { makeBar, makeGantt, makeGanttRow, point } from "../../test/schFixtures";
import { formatDateTime } from "../../lib/format";
import {
  localDateTimeAfterDays,
  localDateTimeToIso,
  PRINT_LIST_LIMIT,
  PRINT_SHEET_HEIGHT_MM,
  shareLinkState,
} from "./labels";
import MeetingReportPage from "./MeetingReportPage";

const BASE = "/api/v1/projects/3";
const REPORT_URL = `${BASE}/status-reports/41/meeting-report`;

function meWith(permissions: string[]) {
  return makeMe({
    user: makeUser({ id: 5, is_system_admin: false }),
    system_permissions: [],
    projects: [{ project_id: 3, roles: ["PM"], permissions, view_level: "ALL" }],
  });
}

/**
 * 表示の範囲（11/12〜1/7）の前で終わる行・後で始まる行を混ぜた会議レポート。
 * 行 0：予定・実績とも 10/20 までで範囲の前（省く）、行 1：予定は 11/05 までだが未完了の実績が報告日まで延びる（残す）、
 * 行 2：資料作成（範囲の内。残す）、行 3：2 月の活動（範囲の後。省く）
 */
function reportWithRowsOutsideWindow(): MeetingReport {
  const base = makeMeetingReport();
  const vertex = (
    row_index: number,
    activity_id: number,
    date: string,
    fraction: number,
    judgement: "BEHIND" | "COMPLETED" | "NOT_STARTED",
  ) => ({
    row_index,
    activity_id,
    point: point(date, fraction),
    deviation: judgement === "BEHIND" ? -3 : 0,
    judgement,
    display_date: date,
  });
  const link = (from: [number, number], to: [number, number], start: string, end: string) => ({
    predecessor_id: from[1],
    successor_id: to[1],
    from_row: from[0],
    to_row: to[0],
    start: point(start, 1),
    end: point(end, 0),
  });
  return makeMeetingReport({
    gantt: makeGantt({
      ...base.gantt,
      rows: [
        makeGanttRow({
          row_index: 0,
          activity_id: 11,
          wbs_code: "1.1",
          name: "要件の整理",
          planned: makeBar("PLANNED", "2026-10-01", "2026-10-20"),
          actual: makeBar("ACTUAL", "2026-10-01", "2026-10-20"),
          assignees: ["実行担当A"],
          percent_complete: 100,
        }),
        makeGanttRow({
          row_index: 1,
          activity_id: 12,
          wbs_code: "1.1",
          name: "設計",
          planned: makeBar("PLANNED", "2026-10-21", "2026-11-05"),
          actual: makeBar("ACTUAL", "2026-10-21", "2026-12-10", { open_ended: true }),
          assignees: ["実行担当A"],
          percent_complete: 70,
        }),
        makeGanttRow({
          row_index: 2,
          activity_id: 21,
          planned: makeBar("PLANNED", "2026-12-01", "2026-12-21"),
          percent_complete: 25,
        }),
        makeGanttRow({
          row_index: 3,
          activity_id: 31,
          wbs_code: "2.1",
          name: "移行",
          planned: makeBar("PLANNED", "2027-02-01", "2027-02-20"),
        }),
      ],
      links: [link([0, 11], [1, 12], "2026-10-20", "2026-10-21"), link([1, 12], [2, 21], "2026-11-05", "2026-12-01")],
    }),
    lightning: {
      ...base.lightning,
      vertices: [
        vertex(0, 11, "2026-12-10", 1, "COMPLETED"),
        vertex(1, 12, "2026-11-30", 0.5, "BEHIND"),
        vertex(2, 21, "2026-12-06", 0.25, "BEHIND"),
        vertex(3, 31, "2026-12-10", 1, "NOT_STARTED"),
      ],
    },
  });
}

function renderAt(path = "/projects/3/status-reports/41/meeting") {
  return render(
    <MemoryRouter initialEntries={[path]}>
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
      "進捗の値：計算値（確定前）・表示の範囲 2026-11-12〜2027-01-07",
    );
    // 計画の値は表示した時点の最新計画（7.4。CR-013）
    expect(container.querySelector('[data-note="plan-source"]')).toHaveTextContent("計画は表示した時点の最新計画");

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

  it("上段は表示の範囲に予定・実績のバーが掛かる行だけを描き、省いた行の数を書き添える。頂点・矢印は描いた行にだけ当てる（7.4）", async () => {
    mockApi({
      "GET /api/v1/me": () => jsonResponse(200, meWith(["project.view"])),
      [`GET ${REPORT_URL}`]: () => jsonResponse(200, reportWithRowsOutsideWindow()),
    });
    const { container } = renderAt();
    const svg = await screen.findByRole("img", { name: "ガントチャート" });

    // 範囲の前で終わる行 0 と、範囲の後の行 3 は描かない。行 1 は未完了の実績のバーで残す
    const rows = Array.from(container.querySelectorAll('[data-layer="row-labels"] [data-row]'));
    expect(rows.map((r) => r.getAttribute("data-row"))).toEqual(["1", "2"]);
    expect(svg).toHaveAttribute("data-row-count", "2");
    expect(svg).toHaveAttribute("height", String(HEADER_HEIGHT + 2 * ROW_HEIGHT));
    expect(screen.getByTestId("gantt-viewport")).toHaveStyle({ height: `${HEADER_HEIGHT + 2 * ROW_HEIGHT}px` });
    expect(container.querySelector('[data-note="rows-outside-window"]')).toHaveTextContent(
      "表示範囲の外の 2 行は省いた",
    );
    // 今の計画に無い頂点の注記とは別（この報告日には無い）
    expect(screen.queryByText(/今の計画に無い活動の頂点/)).toBeNull();

    // イナズマ線は描いた行の頂点だけを、報告日線の上端から下端まで行の順に結ぶ
    const vertices = Array.from(container.querySelectorAll('[data-layer="lightning"] circle'));
    expect(vertices.map((v) => v.querySelector("title")?.textContent)).toEqual([
      "1.1 設計：遅れ（2026-11-30）",
      "1.1 資料作成：遅れ（2026-12-06）",
    ]);
    const polyline = container.querySelector('[data-layer="lightning"] polyline')!.getAttribute("points")!.split(" ");
    expect(polyline).toHaveLength(4);
    expect(polyline[3]).toBe(`${280 + 29 * 24},${HEADER_HEIGHT + 2 * ROW_HEIGHT}`);

    // 依存の矢印は両端の行を描くものだけ
    expect(container.querySelector('[data-link="12-21"]')).not.toBeNull();
    expect(container.querySelector('[data-link="11-12"]')).toBeNull();

    // 行 1 のラベルは、範囲の前で終わる予定のバーの右（左端で切れる）ではなく、報告日まで延びる実績のバーの右に置く
    const label = container.querySelector('[data-layer="bar-labels"] [data-activity-id="12"]');
    expect(label).toHaveAttribute("x", String(280 + 29 * 24 + 10));
    expect(label).toHaveTextContent("実行担当A 70%");
    expect(container.querySelector('[data-layer="bar-labels"] [data-activity-id="11"]')).toBeNull();
  });

  it("印刷は行の数にかかわらず A3 横の 1 枚：ガントは全ての行の高さへ伸ばさず残りの高さの枠へ縮め、下段の一覧は先頭だけを載せて省いた件数を書く", async () => {
    const delays = Array.from({ length: PRINT_LIST_LIMIT + 5 }, (_, i) =>
      makeReportDelay({ activity_id: 100 + i, name: `遅れの活動${i + 1}`, deviation: -20 + i }),
    );
    const milestones = Array.from({ length: PRINT_LIST_LIMIT }, (_, i) =>
      makeReportMilestone({ activity_id: 200 + i, name: `節目${i + 1}` }),
    );
    mockApi({
      "GET /api/v1/me": () => jsonResponse(200, meWith(["project.view"])),
      [`GET ${REPORT_URL}`]: () => jsonResponse(200, makeMeetingReport({ delays, milestones })),
    });
    const { container } = renderAt();
    const delayTable = await screen.findByRole("table", { name: "遅延一覧" });

    // 画面には全ての行を出す。印刷には先頭の PRINT_LIST_LIMIT 件だけを載せ、省いた件数を表の前に書く（印刷にだけ出す）
    expect(within(delayTable).getAllByRole("row").slice(1)).toHaveLength(PRINT_LIST_LIMIT + 5);
    const delayNote = container.querySelector(
      'section[aria-labelledby="report-delays-heading"] [data-note="print-limit"]',
    );
    expect(delayNote).toHaveTextContent(
      `印刷には先頭の ${PRINT_LIST_LIMIT} 件だけを載せ、残りの 5 件は省いた（全 ${PRINT_LIST_LIMIT + 5} 件）`,
    );
    expect(delayNote).toHaveClass("print-only");
    expect(delayNote?.compareDocumentPosition(delayTable)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    // 上限ちょうどなら省かない
    expect(
      container.querySelector('section[aria-labelledby="report-milestones-heading"] [data-note="print-limit"]'),
    ).toBeNull();
    // 範囲の外の行が無ければ、省いた行の注記も出さない
    expect(container.querySelector('[data-note="rows-outside-window"]')).toBeNull();

    // 印刷用の CSS を規則として読む
    const sheet = (container.querySelector("style") as HTMLStyleElement).sheet!;
    const pageRule = Array.from(sheet.cssRules).find((r) => r.cssText.startsWith("@page"));
    expect(pageRule?.cssText).toContain("size: A3 landscape");
    const media = Array.from(sheet.cssRules).find(
      (r): r is CSSMediaRule => r instanceof CSSMediaRule && r.media.mediaText === "print",
    )!;
    const rules = new Map(
      Array.from(media.cssRules).map((r) => [(r as CSSStyleRule).selectorText, (r as CSSStyleRule).style]),
    );
    const rule = (selector: string) => {
      const style = rules.get(selector);
      expect(style, selector).toBeDefined();
      return style!;
    };

    // 画面の区画を版面（A3 横・余白 10mm：高さ 277mm）に収まる高さの縦の並びにし、溢れた分で 2 枚目を作らない
    expect(PRINT_SHEET_HEIGHT_MM).toBeLessThanOrEqual(277);
    const page = rule(".meeting-report-page, .shared-view-page");
    expect(page.getPropertyValue("height")).toBe(`${PRINT_SHEET_HEIGHT_MM}mm`);
    expect(page.getPropertyValue("display")).toBe("flex");
    expect(page.getPropertyValue("flex-direction")).toBe("column");
    expect(page.getPropertyValue("overflow")).toBe("hidden");
    const sheetRule = rule(".meeting-report-page > .meeting-report-sheet, .shared-view-page > .meeting-report-sheet");
    expect(sheetRule.getPropertyValue("flex")).toBe("1 1 0");
    expect(rule(".meeting-report-sheet > .report-upper").getPropertyValue("flex")).toBe("1 1 0");

    // ガントは残りの高さの枠（行の数で決まる画面の高さを打ち消す）に、viewBox の縦横比を保って縮める
    const viewport = rule(".meeting-report-sheet > .report-upper > .gantt-viewport");
    expect(viewport.getPropertyValue("flex")).toBe("1 1 0");
    expect(viewport.getPropertyValue("min-height")).toBe("0");
    expect(viewport.getPropertyValue("height")).toBe("auto");
    expect(viewport.getPropertyPriority("height")).toBe("important");
    expect(viewport.getPropertyValue("overflow")).toBe("hidden");
    const svgRule = rule(".meeting-report-sheet .gantt-viewport svg");
    expect(svgRule.getPropertyValue("width")).toBe("100%");
    expect(svgRule.getPropertyValue("height")).toBe("100%");
    // 全ての行の高さへ伸ばす規則（幅に合わせた height: auto）を持たない
    for (const [selector, style] of rules) {
      if (selector.includes("svg")) {
        expect(style.getPropertyValue("height"), selector).not.toBe("auto");
      }
    }
    const svg = screen.getByRole("img", { name: "ガントチャート" });
    expect(svg).toHaveAttribute("viewBox", `0 0 ${svg.getAttribute("width")} ${svg.getAttribute("height")}`);
    expect(svg.getAttribute("preserveAspectRatio")).toBeNull();

    // 下段の一覧は 1 行を 1 段にし、先頭の PRINT_LIST_LIMIT 行の後を印刷しない
    expect(rule(".meeting-report-sheet .report-lower td").getPropertyValue("white-space")).toBe("nowrap");
    expect(
      rule(`.meeting-report-sheet .report-lower tbody tr:nth-child(n + ${PRINT_LIST_LIMIT + 1})`).getPropertyValue(
        "display",
      ),
    ).toBe("none");
    expect(rule(".meeting-report-sheet .print-only").getPropertyValue("display")).toBe("block");
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
    expect(screen.getByText(/報告日 2026-12-10（確定済み）/)).toHaveTextContent("進捗の値：確定時の写し");
    // 確定済みでも計画の値は写しでなく、表示した時点の最新計画（7.4。CR-013）
    expect(container.querySelector('[data-note="plan-source"]')).toHaveTextContent(
      "計画は表示した時点の最新計画（ガントのバー、マイルストーンの予定日・見込み日・ずれ）",
    );
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

describe("UI-RPT-001 共有リンク（API-RPT-002・003・005。RPT-03）", () => {
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
    // サーバの状態：作る・失効させるたびに一覧（API-RPT-005）も変わる
    let links: ShareLink[] = [];
    const { calls } = mockApi({
      "GET /api/v1/me": () => jsonResponse(200, meWith(["project.view", "share.manage"])),
      [`GET ${REPORT_URL}`]: () => jsonResponse(200, makeMeetingReport()),
      [`GET ${BASE}/share-links`]: () => jsonResponse(200, shareLinkList(links)),
      [`POST ${BASE}/share-links`]: () => {
        const { token: _token, ...link } = created;
        links = [link];
        return jsonResponse(201, created);
      },
      [`DELETE ${BASE}/share-links/71`]: () => {
        links = links.map((l) => ({ ...l, revoked_at: "2026-12-10T02:00:00Z" }));
        return jsonResponse(204, null);
      },
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
      [`GET ${BASE}/share-links`]: () => jsonResponse(200, shareLinkList([])),
      [`POST ${BASE}/share-links`]: () => errorResponse(400, "VALIDATION_ERROR", "入力に誤りがあります"),
    });
    renderAt();

    const form = await screen.findByRole("form", { name: "共有リンクを作る" });
    // 既定値（14 日後）を消して空にする
    fireEvent.change(within(form).getByLabelText("有効期限"), { target: { value: "" } });
    fireEvent.submit(form);
    expect(calls.some((c) => c.method === "POST")).toBe(false);
    expect(screen.getByRole("alert")).toHaveTextContent("有効期限を入力してください");

    fireEvent.change(within(form).getByLabelText("有効期限"), { target: { value: "2020-01-01T09:00" } });
    fireEvent.submit(form);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("入力に誤りがあります"));
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(1);
  });

  it("有効期限の入力の既定値は今から 14 日後（端末の時刻）で、そのまま作れる（CR-019）", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    try {
      vi.setSystemTime(new Date("2026-12-10T09:30:45"));
      const { calls } = mockApi({
        "GET /api/v1/me": () => jsonResponse(200, meWith(["project.view", "share.manage"])),
        [`GET ${REPORT_URL}`]: () => jsonResponse(200, makeMeetingReport()),
        [`GET ${BASE}/share-links`]: () => jsonResponse(200, shareLinkList([])),
        [`POST ${BASE}/share-links`]: () => jsonResponse(201, makeShareLinkCreated()),
      });
      renderAt();

      const form = await screen.findByRole("form", { name: "共有リンクを作る" });
      expect(within(form).getByLabelText("有効期限")).toHaveValue("2026-12-24T09:30");
      fireEvent.click(within(form).getByRole("button", { name: "共有リンクを作る" }));

      await screen.findByLabelText("共有リンクの URL（71）");
      const post = calls.find((c) => c.method === "POST");
      expect(post?.body).toEqual({ scope: "REPORT", expires_at: new Date("2026-12-24T09:30").toISOString() });
    } finally {
      vi.useRealTimers();
    }
  });

  it("共有リンクの一覧を範囲・期限・状態（有効・失効・期限切れ）・作った日時で出し、トークンは出さない（API-RPT-005）", async () => {
    const future = new Date(Date.now() + 7 * 24 * 3600 * 1000).toISOString();
    const items = [
      makeShareLink({ id: 73, scope: "GANTT", expires_at: future, created_at: "2026-11-12T01:00:00Z" }),
      makeShareLink({ id: 72, expires_at: future, revoked_at: "2026-11-11T02:00:00Z" }),
      makeShareLink({ id: 71, expires_at: "2026-01-01T00:00:00Z", created_at: "2025-12-01T00:00:00Z" }),
    ];
    // 一覧の応答にトークンが紛れても画面に出さない（サーバは返さない。4.2）
    const leaked = items.map((l) => ({ ...l, token: `tok-${l.id}` }));
    const { calls } = mockApi({
      "GET /api/v1/me": () => jsonResponse(200, meWith(["project.view", "share.manage"])),
      [`GET ${REPORT_URL}`]: () => jsonResponse(200, makeMeetingReport()),
      [`GET ${BASE}/share-links`]: () => jsonResponse(200, shareLinkList(leaked)),
    });
    const { container } = renderAt();

    const table = await screen.findByRole("table", { name: "共有リンクの一覧" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent("ガントチャート（最新の進捗）");
    expect(rows[0]).toHaveTextContent("有効");
    expect(rows[0]).toHaveTextContent(formatDateTime("2026-11-12T01:00:00Z"));
    expect(rows[0]).toHaveTextContent(formatDateTime(future));
    expect(within(rows[0]!).getByRole("button", { name: "失効させる…" })).toBeInTheDocument();
    expect(rows[1]).toHaveTextContent(`失効（${formatDateTime("2026-11-11T02:00:00Z")}）`);
    expect(within(rows[1]!).queryByRole("button")).toBeNull();
    expect(rows[2]).toHaveTextContent("期限切れ");
    expect(rows[2]).toHaveTextContent("定例会議レポート（最新の報告日）");
    expect(within(rows[2]!).queryByRole("button")).toBeNull();

    expect(container.textContent).not.toContain("tok-");
    expect(screen.queryByLabelText(/共有リンクの URL/)).toBeNull();
    expect(screen.queryByRole("table", { name: "この画面で作った共有リンク" })).toBeNull();
    expect(calls.find((c) => c.path === `${BASE}/share-links`)?.url).toBe(`${BASE}/share-links?limit=100&offset=0`);
  });

  it("一覧の有効なリンクを確認の段を経て失効させ、一覧を読み直す（API-RPT-003）", async () => {
    const future = new Date(Date.now() + 7 * 24 * 3600 * 1000).toISOString();
    let links = [makeShareLink({ id: 73, expires_at: future })];
    const { calls } = mockApi({
      "GET /api/v1/me": () => jsonResponse(200, meWith(["project.view", "share.manage"])),
      [`GET ${REPORT_URL}`]: () => jsonResponse(200, makeMeetingReport()),
      [`GET ${BASE}/share-links`]: () => jsonResponse(200, shareLinkList(links)),
      [`DELETE ${BASE}/share-links/73`]: () => {
        links = links.map((l) => ({ ...l, revoked_at: "2026-11-12T06:00:00Z" }));
        return jsonResponse(204, null);
      },
    });
    renderAt();

    const table = await screen.findByRole("table", { name: "共有リンクの一覧" });
    fireEvent.click(within(table).getByRole("button", { name: "失効させる…" }));
    fireEvent.click(within(table).getByRole("button", { name: "やめる" }));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);

    fireEvent.click(within(table).getByRole("button", { name: "失効させる…" }));
    expect(within(table).getByRole("group", { name: "失効の確認" })).toHaveTextContent("戻せません");
    fireEvent.click(within(table).getByRole("button", { name: "失効させる" }));

    await waitFor(() => expect(within(table).getAllByRole("row")[1]).toHaveTextContent("失効（"));
    expect(calls.filter((c) => c.method === "DELETE").map((c) => c.url)).toEqual([`${BASE}/share-links/73`]);
    expect(calls.filter((c) => c.method === "GET" && c.path === `${BASE}/share-links`)).toHaveLength(2);
    expect(within(table).queryByRole("button")).toBeNull();
  });

  it("共有リンクが無ければその旨を出す", async () => {
    mockApi({
      "GET /api/v1/me": () => jsonResponse(200, meWith(["project.view", "share.manage"])),
      [`GET ${REPORT_URL}`]: () => jsonResponse(200, makeMeetingReport()),
      [`GET ${BASE}/share-links`]: () => jsonResponse(200, shareLinkList([])),
    });
    renderAt();

    expect(await screen.findByText("共有リンクはまだありません")).toBeInTheDocument();
    expect(screen.queryByRole("table", { name: "共有リンクの一覧" })).toBeNull();
  });
});

describe("shareLinkState（共有リンクの一覧の状態）", () => {
  const now = Date.parse("2026-12-10T00:00:00Z");

  it("失効を有効期限より先に見て、有効期限の時刻ちょうどからは期限切れ", () => {
    expect(shareLinkState({ expires_at: "2026-12-10T00:00:01Z", revoked_at: null }, now)).toBe("ACTIVE");
    expect(shareLinkState({ expires_at: "2026-12-10T00:00:00Z", revoked_at: null }, now)).toBe("EXPIRED");
    expect(shareLinkState({ expires_at: "2026-12-01T00:00:00Z", revoked_at: "2026-11-30T00:00:00Z" }, now)).toBe(
      "REVOKED",
    );
    expect(shareLinkState({ expires_at: "2026-12-31T00:00:00Z", revoked_at: "2026-12-09T00:00:00Z" }, now)).toBe(
      "REVOKED",
    );
  });

  it("時差の無いサーバの時刻は UTC とみなす", () => {
    expect(shareLinkState({ expires_at: "2026-12-10T00:00:00", revoked_at: null }, now)).toBe("EXPIRED");
    expect(shareLinkState({ expires_at: "2026-12-10T00:00:01", revoked_at: null }, now)).toBe("ACTIVE");
  });
});

describe("localDateTimeAfterDays（有効期限の入力の既定値）", () => {
  it("端末の暦で日を足し、datetime-local の値（秒は切り捨て）にする", () => {
    expect(localDateTimeAfterDays(new Date("2026-12-10T09:30:45"), 14)).toBe("2026-12-24T09:30");
    expect(localDateTimeAfterDays(new Date("2026-12-25T23:59:59"), 14)).toBe("2027-01-08T23:59");
    expect(localDateTimeAfterDays(new Date("2028-02-20T00:05:00"), 14)).toBe("2028-03-05T00:05");
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
