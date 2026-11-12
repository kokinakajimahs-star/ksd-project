import { render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setAuthToken } from "../../api/client";
import { errorResponse, jsonResponse, mockApi } from "../../test/mockApi";
import { makeDelay, makeDelays, makeSlip } from "../../test/prgFixtures";
import DelaysPage, { slipText } from "./DelaysPage";

const BASE = "/api/v1/projects/3";

// 稲妻チャート.txt §6 の例：報告日 12/10。資料作成（δ＝−4.75）と、もう 1 件の遅れ（δ＝−2。閾値ちょうど）
const DELAYS = makeDelays([
  makeDelay(),
  makeDelay({
    activity_id: 26,
    wbs_code: "1.4",
    name: "移行準備",
    deviation: -2,
    percent_complete: 50,
    delay_reason: null,
    forecast_finish: "2026-12-15",
    expected_finish: 15,
    expected_finish_date: "2026-12-15",
    is_critical: false,
    transitive_successor_ids: [],
    successor_slips: [],
    milestone_slips: [],
    project_finish: { before_date: "2026-12-21", after_date: "2026-12-21", slip_days: 0 },
  }),
]);

function renderAt(path = "/projects/3/status-reports/41/delays") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/projects/:projectId/status-reports/:reportId/delays" element={<DelaysPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("UI-PRG-002 遅延分析", () => {
  beforeEach(() => {
    setAuthToken("t-pm");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    setAuthToken(null);
  });

  it("遅延活動をサーバの順（遅れの大きい順）に、遅延理由・完了見込み・クリティカルか・影響するマイルストーンと並べる（PRG-19）", async () => {
    const { calls } = mockApi({ [`GET ${BASE}/status-reports/41/delays`]: () => jsonResponse(200, DELAYS) });
    const { container } = renderAt();

    const table = await screen.findByRole("table", { name: "遅延活動" });
    expect(container.querySelector('[data-screen-id="UI-PRG-002"]')).not.toBeNull();
    expect(calls.map((c) => c.url)).toEqual([`${BASE}/status-reports/41/delays`]);
    expect(screen.getByText(/報告日 2026-12-10（入力中）/)).toHaveTextContent("−2 稼働日以下");

    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((r) => r.getAttribute("data-activity-id"))).toEqual(["21", "26"]);
    expect(rows[0]).toHaveTextContent("資料作成");
    expect(rows[0]).toHaveTextContent("-4.75");
    expect(rows[0]).toHaveTextContent("レビュー待ち");
    expect(rows[0]).toHaveTextContent("2026-12-26");
    expect(rows[0]).toHaveTextContent("はい");
    expect(rows[0]).toHaveTextContent("公開（+5 稼働日）");
    expect(rows[1]).toHaveTextContent("-2");
    expect(rows[1]).toHaveTextContent("未入力");
    expect(rows[1]).toHaveTextContent("2026-12-15");
    expect(rows[1]).toHaveTextContent("いいえ");
  });

  it("活動ごとの影響（後続・マイルストーン・プロジェクトの終了）と、全体の影響を出す（5.7 の 3・4）", async () => {
    mockApi({ [`GET ${BASE}/status-reports/41/delays`]: () => jsonResponse(200, DELAYS) });
    renderAt();

    const impact = await screen.findByRole("region", { name: "資料作成 の影響" });
    expect(impact).toHaveTextContent("2026-12-21 → 2026-12-26（+5 稼働日）");
    expect(impact).toHaveTextContent("クリティカルパス上");
    expect(impact).toHaveTextContent("後続の活動 1 件");
    const successors = within(impact).getByRole("table", { name: "資料作成 で終わりがずれる後続の活動" });
    expect(within(successors).getAllByRole("row")).toHaveLength(2);
    expect(within(impact).getByRole("table", { name: "資料作成 で影響するマイルストーン" })).toHaveTextContent("◆ 公開");
    expect(impact).toHaveTextContent("プロジェクトの終了：2026-12-21 → 2026-12-26（+5 稼働日）");

    const other = screen.getByRole("region", { name: "移行準備 の影響" });
    expect(other).toHaveTextContent("クリティカルパス外");
    expect(other).toHaveTextContent("移行準備 で影響するマイルストーン：影響なし");
    expect(other).toHaveTextContent("プロジェクトの終了：2026-12-21 → 2026-12-21（ずれなし）");

    const combined = screen.getByRole("region", { name: "全体の影響（全ての見込みを同時に当てた結果）" });
    expect(within(combined).getByRole("table", { name: "全体で影響するマイルストーン" })).toHaveTextContent(
      "◆ 公開2026-12-212026-12-26+5 稼働日",
    );
    expect(combined).toHaveTextContent("プロジェクトの終了：2026-12-21 → 2026-12-26（+5 稼働日）");
  });

  it("進捗入力・是正措置へ進める（7.2）", async () => {
    mockApi({ [`GET ${BASE}/status-reports/41/delays`]: () => jsonResponse(200, DELAYS) });
    renderAt();
    await screen.findByRole("table", { name: "遅延活動" });

    expect(screen.getByRole("link", { name: "進捗入力へ" })).toHaveAttribute("href", "/projects/3/status-reports/41");
    expect(screen.getByRole("link", { name: "是正措置" })).toHaveAttribute("href", "/projects/3/corrective-actions");
  });

  it("遅延が無ければその旨を出す", async () => {
    mockApi({
      [`GET ${BASE}/status-reports/41/delays`]: () =>
        jsonResponse(200, makeDelays([], { combined_milestone_slips: [], combined_project_finish: null })),
    });
    renderAt();

    expect(await screen.findByText("遅延している活動はありません")).toBeInTheDocument();
    expect(screen.getByText("全体で影響するマイルストーン：影響なし")).toBeInTheDocument();
    expect(screen.queryByRole("table", { name: "遅延活動" })).toBeNull();
  });

  it("委託先（自分の割当だけ）にはその旨を出し、プロジェクトの終了のずれを出さない", async () => {
    mockApi({
      [`GET ${BASE}/status-reports/41/delays`]: () =>
        jsonResponse(
          200,
          makeDelays([makeDelay({ project_finish: null })], {
            restricted: true,
            combined_project_finish: null,
            combined_milestone_slips: [makeSlip()],
          }),
        ),
    });
    renderAt();

    await screen.findByRole("table", { name: "遅延活動" });
    expect(screen.getByText("自分の割当の活動だけを表示している")).toBeInTheDocument();
    expect(screen.queryByText(/プロジェクトの終了/)).toBeNull();
  });

  it("サーバの誤り（無い報告日・権限が無い）は文言を出す", async () => {
    mockApi({
      [`GET ${BASE}/status-reports/41/delays`]: () => errorResponse(403, "FORBIDDEN", "この操作の権限がありません"),
    });
    renderAt();

    expect(await screen.findByRole("alert")).toHaveTextContent("この操作の権限がありません");
    expect(screen.queryByRole("table", { name: "遅延活動" })).toBeNull();
  });

  it("ID が正しくなければ API を呼ばずに見つからないことを示す", () => {
    const { calls } = mockApi({});
    const { container } = renderAt("/projects/x/status-reports/41/delays");
    expect(container.querySelector('[data-screen-id="UI-PRG-002"]')).not.toBeNull();
    expect(screen.getByRole("alert")).toHaveTextContent("プロジェクトが見つかりません");
    expect(calls).toHaveLength(0);
  });

  it("終わりのずれの表示", () => {
    expect(slipText(0)).toBe("ずれなし");
    expect(slipText(3)).toBe("+3 稼働日");
    expect(slipText(-1)).toBe("-1 稼働日");
  });
});
