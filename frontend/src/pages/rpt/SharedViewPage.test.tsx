import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, setAuthToken } from "../../api/client";
import { jsonResponse, mockApi } from "../../test/mockApi";
import { makeMeetingReport, makeSharedView } from "../../test/rptFixtures";
import { makeGantt, makeGanttRow } from "../../test/schFixtures";
import SharedViewPage, { isShareUnavailable } from "./SharedViewPage";

const SHARED_URL = "/api/v1/shared/tok-abc_123";

function renderAt(path = "/shared/tok-abc_123") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/shared/:token" element={<SharedViewPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

/** 無い・失効・期限切れのどれでもサーバが返す同じ 404（API-RPT-004） */
function notFound() {
  return jsonResponse(404, {
    error: { code: "NOT_FOUND", message: "共有リンクが見つからないか、失効・期限切れです", details: [] },
  });
}

describe("UI-RPT-002 共有ビュー（ログイン不要）", () => {
  beforeEach(() => {
    // ログインしていない関係者が開く
    setAuthToken(null);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    setAuthToken(null);
  });

  it("REPORT のリンクは、ログインせずに最新の報告日の定例会議レポートを読むだけで見せる（RPT-03・7.4）", async () => {
    const { calls } = mockApi({ [`GET ${SHARED_URL}`]: () => jsonResponse(200, makeSharedView()) });
    const { container } = renderAt();

    expect(await screen.findByRole("table", { name: "遅延一覧" })).toBeInTheDocument();
    expect(container.querySelector('[data-screen-id="UI-RPT-002"]')).not.toBeNull();
    // 認証の要らない唯一の API（6.1）。トークンはパスで送り、Authorization は付けない
    expect(calls.map((c) => c.url)).toEqual([SHARED_URL]);
    expect(calls[0]?.headers.Authorization).toBeUndefined();
    expect(screen.getByText(/PJ-3 基幹刷新・定例会議レポート（最新の報告日）・有効期限/)).toHaveTextContent(
      "（読むだけ）",
    );
    expect(screen.getByRole("table", { name: "マイルストーンの状況" })).toBeInTheDocument();
    // 共有の会議レポートにも、計画は表示した時点の最新計画と書く（7.4。CR-013）
    expect(container.querySelector('[data-note="plan-source"]')).toHaveTextContent("計画は表示した時点の最新計画");
    expect(container.querySelector('[data-kpi="overall_progress"]')).toHaveTextContent("37.5%");
    expect(screen.getByRole("img", { name: "ガントチャート" })).toHaveAttribute("width", String(280 + 57 * 24));
    // 読むだけ：書き込みの部品（共有リンクの作成など）は置かない
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByRole("form")).toBeNull();
  });

  it("GANTT のリンクは最新の進捗のガントだけを見せる", async () => {
    const gantt = makeGantt({ rows: [makeGanttRow({ name: "資料作成" })] });
    mockApi({
      [`GET ${SHARED_URL}`]: () =>
        jsonResponse(200, makeSharedView({ scope: "GANTT", gantt, meeting_report: null })),
    });
    const { container } = renderAt();

    expect(await screen.findByRole("heading", { name: "ガントチャート" })).toBeInTheDocument();
    expect(screen.getByText(/ガントチャート（最新の進捗）/)).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "ガントチャート" })).toBeInTheDocument();
    expect(container.querySelector('[data-layer="row-labels"]')).toHaveTextContent("1.1 資料作成");
    expect(screen.queryByRole("table", { name: "遅延一覧" })).toBeNull();
  });

  it("担当者は所属（組織／チーム）で示す。サーバの返す所属をバーの横に出し、その旨を書き添える（CR-019）", async () => {
    const row = makeGanttRow({ name: "資料作成", assignees: ["開発部／基盤チーム", "協力会社"], percent_complete: 40 });
    mockApi({
      [`GET ${SHARED_URL}`]: () =>
        jsonResponse(200, makeSharedView({ scope: "GANTT", gantt: makeGantt({ rows: [row] }), meeting_report: null })),
    });
    const { container } = renderAt();

    await screen.findByRole("heading", { name: "ガントチャート" });
    expect(container.querySelector('[data-note="assignee-affiliation"]')).toHaveTextContent(
      "担当は所属（組織／チーム）で示す",
    );
    expect(container.querySelector('[data-layer="bar-labels"]')).toHaveTextContent("開発部／基盤チーム、協力会社 40%");
  });

  it("REPORT のリンクで報告日がまだ無ければ、その旨を出す", async () => {
    mockApi({ [`GET ${SHARED_URL}`]: () => jsonResponse(200, makeSharedView({ meeting_report: null })) });
    renderAt();

    expect(await screen.findByText("報告日がまだ無いため、定例会議レポートはありません")).toBeInTheDocument();
  });

  it("閲覧できないリンクは、理由を見分けずにサーバの文言と 1 つの案内を出す", async () => {
    mockApi({ [`GET ${SHARED_URL}`]: () => notFound() });
    renderAt();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("共有リンクが見つからないか、失効・期限切れです");
    expect(alert).toHaveTextContent("リンクが正しいか、まだ有効かを、共有した人に確かめてください");
    expect(screen.queryByRole("img", { name: "ガントチャート" })).toBeNull();
  });

  it("共有の会議レポートでも、委託先の扱い（KPI なし）はサーバの値のまま出す", async () => {
    mockApi({
      [`GET ${SHARED_URL}`]: () =>
        jsonResponse(200, makeSharedView({ meeting_report: makeMeetingReport({ kpi: null, restricted: true }) })),
    });
    renderAt();

    expect(await screen.findByText("主要 KPI は表示できません")).toBeInTheDocument();
  });
});

describe("isShareUnavailable（共有リンクで閲覧できないか）", () => {
  it("API の 404 だけを閲覧できないとし、それ以外（サーバの誤り・通信の誤り）は違う", () => {
    const body = (code: string) => ({ code, message: "x", details: [] });
    expect(isShareUnavailable(new ApiError(404, body("NOT_FOUND")))).toBe(true);
    expect(isShareUnavailable(new ApiError(500, body("INTERNAL_ERROR")))).toBe(false);
    expect(isShareUnavailable(new Error("network"))).toBe(false);
  });
});
