import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setAuthToken } from "../../api/client";
import type { Me } from "../../api/cmn";
import type { LightningLine } from "../../api/prg";
import { makeMe, makeUser } from "../../test/fixtures";
import { errorResponse, jsonResponse, mockApi, type RecordedCall, type RouteHandler } from "../../test/mockApi";
import {
  makeFinalizeResult,
  makeProgressEntry,
  makeProgressItems,
  makeProgressMethodResult,
} from "../../test/prgFixtures";
import { makeLightningLine, makeLightningRow, makeStatusReport, point } from "../../test/schFixtures";
import { buildEntryInput, buildItemsInput, type EntryForm } from "./EntryEditor";
import StatusReportPage, { remainingText } from "./StatusReportPage";

const FUTURE = { v7_startTransition: true, v7_relativeSplatPath: true };
const BASE = "/api/v1/projects/3";

function meWith(permissions: string[]): Me {
  return makeMe({
    user: makeUser({ id: 5, is_system_admin: false }),
    system_permissions: [],
    projects: [{ project_id: 3, roles: ["PM"], permissions, view_level: "ALL" }],
  });
}

// 稲妻チャート.txt §6 の例（設計書 5.6 の試験の基準）：報告日 12/10（L＝10）
function lineFor(reportId: number, status: "OPEN" | "FINALIZED" = "OPEN"): LightningLine {
  return makeLightningLine(
    [
      makeLightningRow({ remaining_hours: 30, delay_reason: "レビュー待ち" }),
      makeLightningRow({
        row_index: 1,
        activity_id: 22,
        wbs_code: "1.2",
        name: "関係者調整",
        progress_method: "DELIVERABLE_RATIO",
        percent_complete: null,
        actual_start: null,
        point: point("2026-12-10", 1, 10),
        deviation: 0,
        judgement: "NOT_COMPUTED",
        counted_as: "NOT_COMPUTED",
        display_date: "2026-12-10",
      }),
      makeLightningRow({
        row_index: 2,
        activity_id: 24,
        wbs_code: "1.3",
        name: "環境整備",
        progress_method: "ZERO_HUNDRED",
        percent_complete: 100,
        actual_finish: "2026-12-08",
        point: point("2026-12-10", 1, 10),
        deviation: 0,
        judgement: "COMPLETED",
        counted_as: "ON_SCHEDULE",
        display_date: "2026-12-10",
      }),
    ],
    {
      report: makeStatusReport({
        id: reportId,
        status,
        snapshot_id: status === "FINALIZED" ? 7 : null,
        finalized_at: status === "FINALIZED" ? "2026-12-10T10:00:00Z" : null,
      }),
      source: status === "FINALIZED" ? "SNAPSHOT" : "CALCULATED",
    },
  );
}

const REPORTS = [
  makeStatusReport({ id: 40, report_date: "2026-12-03", status: "FINALIZED", snapshot_id: 7 }),
  makeStatusReport({ id: 41, report_date: "2026-12-10", status: "OPEN" }),
];

function routes(me: Me, overrides: Record<string, RouteHandler> = {}): Record<string, RouteHandler> {
  return {
    "GET /api/v1/me": () => jsonResponse(200, me),
    [`GET ${BASE}/status-reports`]: () => jsonResponse(200, { items: REPORTS, total: 2, limit: 1000, offset: 0 }),
    [`GET ${BASE}/status-reports/41/lightning-line`]: () => jsonResponse(200, lineFor(41)),
    [`GET ${BASE}/status-reports/40/lightning-line`]: () => jsonResponse(200, lineFor(40, "FINALIZED")),
    ...overrides,
  };
}

function renderAt(path = "/projects/3/status-reports/41") {
  return render(
    <MemoryRouter initialEntries={[path]} future={FUTURE}>
      <Routes>
        <Route path="/projects/:projectId/status-reports/:reportId" element={<StatusReportPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

function sent(calls: RecordedCall[], method: string, path: string) {
  return calls.filter((c) => c.method === method && c.path === path);
}

async function table() {
  return screen.findByRole("table", { name: "活動の進捗" });
}

describe("UI-PRG-001 進捗入力（報告日）", () => {
  beforeEach(() => {
    setAuthToken("t-pm");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    setAuthToken(null);
  });

  it("報告日の活動の進捗を一覧にする。未算定には入力を促し、残工数は自動か入力かを区別する（PRG-02・EFF-06）", async () => {
    mockApi(routes(meWith(["project.view"])));
    const { container } = renderAt();
    const t = await table();

    expect(container.querySelector('[data-screen-id="UI-PRG-001"]')).not.toBeNull();
    const rows = within(t).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent("資料作成");
    expect(rows[0]).toHaveTextContent("25%");
    expect(rows[0]).toHaveTextContent("30 時間（入力）");
    expect(rows[0]).toHaveTextContent("レビュー待ち");
    expect(rows[0]).toHaveTextContent("-4.75");
    expect(rows[0]).toHaveTextContent("遅れ");
    expect(rows[1]).toHaveTextContent("未算定（入力が要る）");
    expect(rows[1]).toHaveTextContent("成果物完了割合");
    expect(rows[1]).toHaveTextContent("自動");
    expect(rows[2]).toHaveTextContent("完了");
    expect(screen.getByText(/状態：入力中/)).toHaveTextContent("許容幅 0.5 日");
    expect(screen.getByRole("link", { name: "遅延分析" })).toHaveAttribute(
      "href",
      "/projects/3/status-reports/41/delays",
    );
    // 7.2：進捗入力 → 会議レポート（UI-RPT-001）
    expect(screen.getByRole("link", { name: "定例会議レポート" })).toHaveAttribute(
      "href",
      "/projects/3/status-reports/41/meeting",
    );
  });

  it("project.view だけの人には入力・作成・確定の部品を出さない", async () => {
    mockApi(routes(meWith(["project.view"])));
    renderAt();
    await table();

    expect(screen.queryByRole("button", { name: "資料作成 を入力する" })).toBeNull();
    expect(screen.queryByRole("form", { name: "報告日を作る" })).toBeNull();
    expect(screen.queryByRole("button", { name: "報告日を確定する" })).toBeNull();
  });

  it("進捗率・実績日付・残工数・遅延理由・完了見込み日を保存し、行を読み直す（API-PRG-003。PRG-02・03）", async () => {
    const { calls } = mockApi(
      routes(meWith(["project.view", "progress.enter_all"]), {
        [`PUT ${BASE}/status-reports/41/progress/21`]: () =>
          jsonResponse(200, makeProgressEntry({ percent_complete: 40, remaining_hours: 12, remaining_hours_source: "ENTERED" })),
      }),
    );
    renderAt();
    await table();

    fireEvent.click(await screen.findByRole("button", { name: "資料作成 を入力する" }));
    const form = screen.getByRole("form", { name: "進捗の入力" });
    expect(within(form).getByLabelText("進捗率（%）")).toHaveValue(25);
    fireEvent.change(within(form).getByLabelText("進捗率（%）"), { target: { value: "40" } });
    fireEvent.change(within(form).getByLabelText("残工数（時間）"), { target: { value: "12" } });
    fireEvent.change(within(form).getByLabelText("遅延理由"), { target: { value: "仕様の確認待ち" } });
    fireEvent.change(within(form).getByLabelText("完了見込み日"), { target: { value: "2026-12-24" } });
    fireEvent.click(within(form).getByRole("button", { name: "進捗を保存する" }));

    expect(await screen.findByRole("status")).toHaveTextContent("進捗率 40%・残工数 12 時間（入力）");
    const put = sent(calls, "PUT", `${BASE}/status-reports/41/progress/21`);
    expect(put).toHaveLength(1);
    expect(put[0]!.body).toEqual({
      percent_complete: 40,
      actual_start: "2026-12-01",
      actual_finish: null,
      remaining_hours: 12,
      delay_reason: "仕様の確認待ち",
      forecast_finish: "2026-12-24",
    });
    await waitFor(() => expect(sent(calls, "GET", `${BASE}/status-reports/41/lightning-line`)).toHaveLength(2));
  });

  it("実績開始日より前の実績終了日は送らずに理由を出す（PRG-02）", async () => {
    const { calls } = mockApi(routes(meWith(["project.view", "progress.enter_all"])));
    renderAt();
    await table();

    fireEvent.click(await screen.findByRole("button", { name: "資料作成 を入力する" }));
    const form = screen.getByRole("form", { name: "進捗の入力" });
    fireEvent.change(within(form).getByLabelText("実績終了日"), { target: { value: "2026-11-30" } });
    fireEvent.click(within(form).getByRole("button", { name: "進捗を保存する" }));

    expect(await within(form).findByRole("alert")).toHaveTextContent("実績開始日より前の実績終了日は入力できません");
    expect(sent(calls, "PUT", `${BASE}/status-reports/41/progress/21`)).toHaveLength(0);
  });

  it("直接入力でない活動は進捗率を入れられず、進捗率を空で送る（BR-06）", async () => {
    const { calls } = mockApi(
      routes(meWith(["project.view", "progress.enter_own"]), {
        [`PUT ${BASE}/status-reports/41/progress/24`]: () =>
          jsonResponse(200, makeProgressEntry({ activity_id: 24, progress_method: "ZERO_HUNDRED", percent_complete: 100 })),
      }),
    );
    renderAt();
    await table();
    expect(screen.getByText("書けるのは自分の割当の活動の進捗だけ")).toBeInTheDocument();

    fireEvent.click(await screen.findByRole("button", { name: "環境整備 を入力する" }));
    const form = screen.getByRole("form", { name: "進捗の入力" });
    expect(within(form).getByLabelText("進捗率（%）")).toBeDisabled();
    fireEvent.click(within(form).getByRole("button", { name: "進捗を保存する" }));

    await screen.findByRole("status");
    const body = sent(calls, "PUT", `${BASE}/status-reports/41/progress/24`)[0]!.body as Record<string, unknown>;
    expect(body.percent_complete).toBeNull();
    expect(body.actual_finish).toBe("2026-12-08");
  });

  it("自分の割当でない活動への 403 はサーバの文言を出す", async () => {
    mockApi(
      routes(meWith(["project.view", "progress.enter_own"]), {
        [`PUT ${BASE}/status-reports/41/progress/21`]: () =>
          errorResponse(403, "FORBIDDEN", "自分の割当の活動ではありません"),
      }),
    );
    renderAt();
    await table();

    fireEvent.click(await screen.findByRole("button", { name: "資料作成 を入力する" }));
    fireEvent.click(screen.getByRole("button", { name: "進捗を保存する" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("自分の割当の活動ではありません");
  });

  it("schedule.edit を持つ人は活動の算定方式を変えられる（API-PRG-005。PRG-05）", async () => {
    const { calls } = mockApi(
      routes(meWith(["project.view", "schedule.edit"]), {
        [`PUT ${BASE}/activities/21/progress-method`]: () => jsonResponse(200, makeProgressMethodResult()),
      }),
    );
    renderAt();
    await table();

    fireEvent.click(await screen.findByRole("button", { name: "資料作成 を入力する" }));
    // 進捗を書く権限が無ければ進捗の入力欄は出さない
    expect(screen.queryByRole("form", { name: "進捗の入力" })).toBeNull();
    const form = screen.getByRole("form", { name: "算定方式" });
    const submit = within(form).getByRole("button", { name: "算定方式を変える" });
    expect(submit).toBeDisabled();
    fireEvent.change(within(form).getByLabelText("進捗率の算定方式"), { target: { value: "FIFTY_FIFTY" } });
    fireEvent.click(submit);

    expect(await screen.findByRole("status")).toHaveTextContent("算定方式を 50/50法 にした");
    expect(sent(calls, "PUT", `${BASE}/activities/21/progress-method`)[0]!.body).toEqual({
      progress_method: "FIFTY_FIFTY",
    });
  });

  it("算定方式をプロジェクトの既定に戻すときは null を送る", async () => {
    const { calls } = mockApi(
      routes(meWith(["project.view", "schedule.edit"]), {
        [`PUT ${BASE}/activities/21/progress-method`]: () =>
          jsonResponse(200, makeProgressMethodResult({ progress_method: null, effective_method: "DIRECT" })),
      }),
    );
    renderAt();
    await table();

    fireEvent.click(await screen.findByRole("button", { name: "資料作成 を入力する" }));
    fireEvent.change(screen.getByLabelText("進捗率の算定方式"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "算定方式を変える" }));

    expect(await screen.findByRole("status")).toHaveTextContent("直接入力（プロジェクトの既定）");
    expect(sent(calls, "PUT", `${BASE}/activities/21/progress-method`)[0]!.body).toEqual({ progress_method: null });
  });

  it("項目を数える方式の活動は算定項目を置き換え、2 回目からは保存済みの項目の id を送る（API-PRG-004）", async () => {
    let saved = 0;
    const { calls } = mockApi(
      routes(meWith(["project.view", "progress.enter_all"]), {
        [`PUT ${BASE}/activities/22/progress-items`]: () => {
          saved += 1;
          return jsonResponse(
            200,
            makeProgressItems({
              percent_complete: 100,
              items: [
                {
                  id: 90,
                  item_type: "DELIVERABLE",
                  title: "設計書",
                  weight: 1,
                  done: true,
                  approved: false,
                  done_at: "2026-12-09T00:00:00Z",
                },
              ],
            }),
          );
        },
      }),
    );
    renderAt();
    await table();

    fireEvent.click(await screen.findByRole("button", { name: "関係者調整 を入力する" }));
    const form = screen.getByRole("form", { name: "算定項目" });
    expect(within(form).getByText(/並べなかった既存の項目は消える/)).toBeInTheDocument();
    fireEvent.click(within(form).getByRole("button", { name: "項目を足す" }));
    expect(within(form).getByLabelText("1 行目の種類")).toHaveValue("DELIVERABLE");
    fireEvent.change(within(form).getByLabelText("1 行目の名前"), { target: { value: "設計書" } });
    fireEvent.click(within(form).getByLabelText("1 行目の完了"));
    fireEvent.click(within(form).getByRole("button", { name: "算定項目を保存する" }));

    expect(await screen.findByRole("status")).toHaveTextContent("算定項目を 1 件で保存した。進捗率 100%");
    expect(sent(calls, "PUT", `${BASE}/activities/22/progress-items`)[0]!.body).toEqual({
      items: [{ item_type: "DELIVERABLE", title: "設計書", weight: 1, done: true, approved: false }],
    });

    // 保存の応答の項目（id 付き）が残り、もう一度保存すると id を載せて送る
    await waitFor(() => expect(sent(calls, "GET", `${BASE}/status-reports/41/lightning-line`)).toHaveLength(2));
    const again = screen.getByRole("form", { name: "算定項目" });
    expect(within(again).getByLabelText("1 行目の名前")).toHaveValue("設計書");
    fireEvent.click(within(again).getByRole("button", { name: "算定項目を保存する" }));
    await waitFor(() => expect(saved).toBe(2));
    expect(sent(calls, "PUT", `${BASE}/activities/22/progress-items`)[1]!.body).toEqual({
      items: [{ id: 90, item_type: "DELIVERABLE", title: "設計書", weight: 1, done: true, approved: false }],
    });
  });

  it("status_report.manage を持つ人は報告日を作り、作った報告日へ移る（API-PRG-001。PRG-01）", async () => {
    const created = makeStatusReport({ id: 42, report_date: "2026-12-17" });
    const { calls } = mockApi(
      routes(meWith(["project.view", "status_report.manage"]), {
        [`POST ${BASE}/status-reports`]: () => jsonResponse(201, created),
        [`GET ${BASE}/status-reports/42/lightning-line`]: () => jsonResponse(200, lineFor(42)),
      }),
    );
    renderAt();
    await table();

    const form = screen.getByRole("form", { name: "報告日を作る" });
    fireEvent.change(within(form).getByLabelText("新しい報告日"), { target: { value: "2026-12-17" } });
    fireEvent.click(within(form).getByRole("button", { name: "報告日を作る" }));

    expect(await screen.findByRole("status")).toHaveTextContent("報告日 2026-12-17 を作った");
    expect(sent(calls, "POST", `${BASE}/status-reports`)[0]!.body).toEqual({ report_date: "2026-12-17" });
    await waitFor(() => expect(sent(calls, "GET", `${BASE}/status-reports/42/lightning-line`)).toHaveLength(1));
    expect(sent(calls, "GET", `${BASE}/status-reports`)).toHaveLength(2);
  });

  it("同じ日付の報告日は 409 の文言を出す", async () => {
    mockApi(
      routes(meWith(["project.view", "status_report.manage"]), {
        [`POST ${BASE}/status-reports`]: () =>
          errorResponse(409, "DUPLICATE_STATUS_REPORT", "同じ日付の報告日が既にあります"),
      }),
    );
    renderAt();
    await table();

    fireEvent.change(screen.getByLabelText("新しい報告日"), { target: { value: "2026-12-10" } });
    fireEvent.click(screen.getByRole("button", { name: "報告日を作る" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("同じ日付の報告日が既にあります");
  });

  it("報告日を確かめてから確定し、スナップショットを作る（API-PRG-007。PRG-17）", async () => {
    let finalized = false;
    const { calls } = mockApi(
      routes(meWith(["project.view", "status_report.manage", "progress.enter_all"]), {
        [`POST ${BASE}/status-reports/41/finalize`]: () => {
          finalized = true;
          return jsonResponse(200, makeFinalizeResult());
        },
        [`GET ${BASE}/status-reports/41/lightning-line`]: () =>
          jsonResponse(200, lineFor(41, finalized ? "FINALIZED" : "OPEN")),
      }),
    );
    renderAt();
    await table();

    fireEvent.click(screen.getByRole("button", { name: "報告日を確定する" }));
    const confirm = screen.getByRole("group", { name: "確定の確認" });
    expect(confirm).toHaveTextContent("戻せない");
    expect(sent(calls, "POST", `${BASE}/status-reports/41/finalize`)).toHaveLength(0);
    fireEvent.click(within(confirm).getByRole("button", { name: "確定する" }));

    expect(await screen.findByRole("status")).toHaveTextContent("スナップショット（活動 4 件）を作った");
    expect(sent(calls, "POST", `${BASE}/status-reports/41/finalize`)).toHaveLength(1);
    // 確定の後は入力の部品を出さない（BR-13）
    expect(await screen.findByText(/確定済みの報告日は入力できない/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "資料作成 を入力する" })).toBeNull();
    expect(screen.queryByRole("button", { name: "報告日を確定する" })).toBeNull();
  });

  it("確定済みの報告日は、書く権限があっても入力の部品を出さない", async () => {
    mockApi(routes(meWith(["project.view", "status_report.manage", "progress.enter_all", "schedule.edit"])));
    renderAt("/projects/3/status-reports/40");
    await table();

    expect(screen.getByText(/状態：確定済み/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "資料作成 を入力する" })).toBeNull();
    expect(screen.queryByRole("button", { name: "報告日を確定する" })).toBeNull();
  });

  it("報告日を選び直すと、その報告日の行を読む", async () => {
    const { calls } = mockApi(routes(meWith(["project.view"])));
    renderAt();
    await table();

    const select = screen.getByLabelText("報告日");
    // 新しい順に並べる
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "2026-12-10（入力中）",
      "2026-12-03（確定済み）",
    ]);
    fireEvent.change(select, { target: { value: "40" } });

    await waitFor(() => expect(sent(calls, "GET", `${BASE}/status-reports/40/lightning-line`)).toHaveLength(1));
    expect(await screen.findByText(/状態：確定済み/)).toBeInTheDocument();
  });

  it("無い報告日は 404 の文言を出す", async () => {
    mockApi(
      routes(meWith(["project.view"]), {
        [`GET ${BASE}/status-reports/99/lightning-line`]: () =>
          errorResponse(404, "NOT_FOUND", "報告日が見つかりません"),
      }),
    );
    renderAt("/projects/3/status-reports/99");

    expect(await screen.findByRole("alert")).toHaveTextContent("報告日が見つかりません");
    expect(screen.queryByRole("table", { name: "活動の進捗" })).toBeNull();
  });

  it("ID が正しくなければ API を呼ばずに見つからないことを示す", () => {
    const { calls } = mockApi({});
    const { container } = renderAt("/projects/3/status-reports/abc");
    expect(container.querySelector('[data-screen-id="UI-PRG-001"]')).not.toBeNull();
    expect(screen.getByRole("alert")).toHaveTextContent("報告日が見つかりません");
    expect(calls).toHaveLength(0);
  });
});

describe("進捗の入力欄の値の組み立て", () => {
  const base: EntryForm = {
    percent: "25",
    actualStart: "2026-12-01",
    actualFinish: "",
    remaining: "",
    delayReason: "  ",
    forecastFinish: "",
  };

  it("空欄は null（残工数の空は自動）、空白だけの遅延理由も null", () => {
    expect(buildEntryInput(base, true)).toEqual({
      input: {
        percent_complete: 25,
        actual_start: "2026-12-01",
        actual_finish: null,
        remaining_hours: null,
        delay_reason: null,
        forecast_finish: null,
      },
      errors: [],
    });
  });

  it("進捗率は 0〜100（境界を含む）。範囲の外は理由を返す", () => {
    expect(buildEntryInput({ ...base, percent: "0" }, true).input?.percent_complete).toBe(0);
    expect(buildEntryInput({ ...base, percent: "100" }, true).input?.percent_complete).toBe(100);
    expect(buildEntryInput({ ...base, percent: "100.5" }, true).errors).toEqual([
      "進捗率は 0〜100 の数で入力してください",
    ]);
    expect(buildEntryInput({ ...base, percent: "-1" }, true).input).toBeNull();
  });

  it("直接入力でなければ進捗率を送らない", () => {
    expect(buildEntryInput({ ...base, percent: "300" }, false).input?.percent_complete).toBeNull();
  });

  it("実績終了日は実績開始日と同じ日まで。前の日は理由を返す", () => {
    expect(buildEntryInput({ ...base, actualFinish: "2026-12-01" }, true).errors).toEqual([]);
    expect(buildEntryInput({ ...base, actualFinish: "2026-11-30" }, true).errors).toEqual([
      "実績開始日より前の実績終了日は入力できません",
    ]);
  });

  it("残工数は 0 以上", () => {
    expect(buildEntryInput({ ...base, remaining: "0" }, true).input?.remaining_hours).toBe(0);
    expect(buildEntryInput({ ...base, remaining: "-0.5" }, true).input).toBeNull();
  });

  it("算定項目は名前が要り、重みは 0 より大きい", () => {
    const draft = { key: 1, id: null, itemType: "CHECK" as const, title: " 試験 ", weight: "2", done: false, approved: true };
    expect(buildItemsInput([draft])).toEqual({
      items: [{ item_type: "CHECK", title: "試験", weight: 2, done: false, approved: true }],
      errors: [],
    });
    expect(buildItemsInput([{ ...draft, title: "", weight: "0" }]).errors).toEqual([
      "1 行目：名前を入力してください",
      "1 行目：重みは 0 より大きい数で入力してください",
    ]);
  });

  it("残工数の表示は自動か入力かを区別する（EFF-06）", () => {
    expect(remainingText(null)).toBe("自動");
    expect(remainingText(8)).toBe("8 時間（入力）");
  });
});
