import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setAuthToken } from "../../api/client";
import { readBlobText, stubDownloads } from "../../test/download";
import { errorResponse, jsonResponse, mockApi, type RecordedCall } from "../../test/mockApi";
import { makeMatrix } from "../../test/wbsFixtures";
import ResponsibilityPage, { cellText } from "./ResponsibilityPage";

const FUTURE = { v7_startTransition: true, v7_relativeSplatPath: true };
const BASE = "/api/v1/projects/3";
const CSV = "﻿WBS ID,名称,責任者のログインID,担当チーム,割当工数の合計（人時）,planner\r\n1.1,業務整理,planner,,24.00,責任者・担当 16.00\r\n";

function renderAt(path = "/projects/3/responsibility") {
  return render(
    <MemoryRouter initialEntries={[path]} future={FUTURE}>
      <Routes>
        <Route path="/projects/:projectId/responsibility" element={<ResponsibilityPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

function sent(calls: RecordedCall[], method: string, path: string) {
  return calls.filter((c) => c.method === method && c.path === path);
}

describe("UI-WBS-004 責任分担表", () => {
  let downloads: ReturnType<typeof stubDownloads> | null = null;

  beforeEach(() => {
    setAuthToken("t-pm");
  });

  afterEach(() => {
    downloads?.restore();
    downloads = null;
    vi.unstubAllGlobals();
    setAuthToken(null);
  });

  it("WP × メンバーの表に責任者・担当・割当工数を出し、責任者の無い WP を一覧にする（WBS-10）", async () => {
    const { calls } = mockApi({ [`GET ${BASE}/wbs/responsibility-matrix`]: () => jsonResponse(200, makeMatrix()) });
    const { container } = renderAt();

    expect(container.querySelector('[data-screen-id="UI-WBS-004"]')).not.toBeNull();
    const table = await screen.findByRole("table", { name: "責任分担表" });
    const headers = within(table).getAllByRole("columnheader").map((h) => h.textContent);
    expect(headers).toEqual(["WBS ID", "ワークパッケージ", "責任者", "担当チーム", "割当工数の合計", "計画 太郎", "元 次郎（メンバー外）"]);

    const rows = within(table).getAllByRole("row").slice(1);
    const first = within(rows[0]!).getAllByRole("cell").map((c) => c.textContent);
    expect(first).toEqual(["1.1", "業務整理", "計画 太郎", "—", "24 人時", "責任者・担当 16 人時", "担当 8 人時"]);
    const second = within(rows[1]!).getAllByRole("cell").map((c) => c.textContent);
    expect(second).toEqual(["1.2", "画面設計", "未設定", "設計チーム", "0 人時", "", ""]);
    expect(within(rows[0]!).getByRole("link", { name: "1.1" })).toHaveAttribute("href", "/projects/3/wbs/101");

    const without = screen.getByRole("list", { name: "責任者の無いワークパッケージ" });
    expect(without).toHaveTextContent("1.2 画面設計（担当チーム：設計チーム）");
    expect(sent(calls, "GET", `${BASE}/wbs/responsibility-matrix`)[0]!.url).toBe(`${BASE}/wbs/responsibility-matrix?format=json`);
  });

  it("CSV で保存すると format=csv で受け取ったファイルをサーバの名前で保存する（WBS-10）", async () => {
    downloads = stubDownloads();
    const { calls } = mockApi({
      [`GET ${BASE}/wbs/responsibility-matrix`]: (call) =>
        call.url.endsWith("format=csv")
          ? new Response(CSV, {
              status: 200,
              headers: {
                "Content-Type": "text/csv; charset=utf-8",
                "Content-Disposition": 'attachment; filename="responsibility-matrix-3.csv"',
              },
            })
          : jsonResponse(200, makeMatrix()),
    });
    renderAt();
    await screen.findByRole("table", { name: "責任分担表" });

    fireEvent.click(screen.getByRole("button", { name: "CSV で保存する" }));
    await waitFor(() => expect(downloads!.saved).toHaveLength(1));
    expect(downloads!.saved[0]!.filename).toBe("responsibility-matrix-3.csv");
    expect(downloads!.saved[0]!.revoked).toBe(true);
    expect(await readBlobText(downloads!.saved[0]!.blob)).toContain("責任者・担当 16.00");
    expect(calls.map((c) => c.url)).toContain(`${BASE}/wbs/responsibility-matrix?format=csv`);
  });

  it("委託先には見える WP だけを出したことを示し、行が無ければその旨を出す", async () => {
    mockApi({
      [`GET ${BASE}/wbs/responsibility-matrix`]: () =>
        jsonResponse(200, makeMatrix({ restricted: true, rows: [], without_responsible: [] })),
    });
    renderAt();
    expect(await screen.findByText("ワークパッケージはまだありません")).toBeInTheDocument();
    expect(screen.getByText(/自分の割当の経路のワークパッケージだけ/)).toBeInTheDocument();
    expect(screen.getByText("責任者の無いワークパッケージはありません")).toBeInTheDocument();
  });

  it("読めなければサーバの文言を出す", async () => {
    mockApi({ [`GET ${BASE}/wbs/responsibility-matrix`]: () => errorResponse(403, "FORBIDDEN", "権限がありません") });
    renderAt();
    expect(await screen.findByRole("alert")).toHaveTextContent("権限がありません");
  });
});

describe("cellText", () => {
  it("責任者・担当・割当工数をマスの言い方にする", () => {
    expect(cellText({ user_id: 1, is_responsible: true, is_assigned: false, planned_hours: 0 })).toBe("責任者");
    expect(cellText({ user_id: 1, is_responsible: false, is_assigned: true, planned_hours: 7.5 })).toBe("担当 7.5 人時");
    expect(cellText({ user_id: 1, is_responsible: true, is_assigned: true, planned_hours: 0 })).toBe("責任者・担当 0 人時");
    expect(cellText({ user_id: 1, is_responsible: false, is_assigned: false, planned_hours: 0 })).toBe("");
  });
});
