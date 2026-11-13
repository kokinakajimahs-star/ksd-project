import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setAuthToken } from "../../api/client";
import type { ImportResult } from "../../api/int";
import { readBlobText, stubDownloads } from "../../test/download";
import { makeMe, makeUser } from "../../test/fixtures";
import { errorResponse, jsonResponse, mockApi, type RecordedCall, type RouteHandler } from "../../test/mockApi";
import ImportExportPage, { describeImportResult, exportTargets } from "./ImportExportPage";
import { formatFromFilename } from "./labels";

const BASE = "/api/v1/projects/3";
const XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

function meWith(permissions: string[]) {
  return makeMe({
    user: makeUser({ id: 7, is_system_admin: false }),
    system_permissions: [],
    projects: [{ project_id: 3, roles: ["PLANNER"], permissions: ["project.view", ...permissions], view_level: "ALL" }],
  });
}

// 9.2 の PLANNER：data.export・data.import と effort.view（実績工数の出力に要る。CR-015）を持つ
const PLANNER = meWith(["data.export", "data.import", "effort.view"]);

function makeResult(overrides: Partial<ImportResult> = {}): ImportResult {
  return {
    target: "wbs",
    format: "csv",
    dry_run: true,
    ok: true,
    written: false,
    created: 1,
    updated: 1,
    unchanged: 2,
    changes: [
      { row: 3, action: "UPDATE", key: "1.1", changed: ["名称", "自分の見積（人時）"] },
      { row: 5, action: "CREATE", key: "1.3", changed: [] },
    ],
    issues: [],
    ...overrides,
  };
}

const ISSUES = [
  { row: 2, column: "WBS ID", code: "REQUIRED", message: "WBS ID が空" },
  { row: 4, column: null, code: "COLUMNS", message: "列の数が見出しと合わない" },
];

function routes(overrides: Record<string, RouteHandler> = {}): Record<string, RouteHandler> {
  return {
    "GET /api/v1/me": () => jsonResponse(200, PLANNER),
    ...overrides,
  };
}

function renderAt(path = "/projects/3/import-export") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/projects/:projectId/import-export" element={<ImportExportPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

function sent(calls: RecordedCall[], method: string, path: string) {
  return calls.filter((c) => c.method === method && c.path === path);
}

function queryOf(call: RecordedCall): Record<string, string> {
  return Object.fromEntries(new URL(call.url, "http://localhost").searchParams.entries());
}

async function importForm() {
  return within(await screen.findByRole("form", { name: "取り込む" }));
}

function chooseFile(form: ReturnType<typeof within>, file: File) {
  fireEvent.change(form.getByLabelText("ファイル"), { target: { files: [file] } });
}

describe("UI-INT-001 入出力（Excel・CSV）", () => {
  let downloads: ReturnType<typeof stubDownloads>;

  beforeEach(() => {
    setAuthToken("t-planner");
    downloads = stubDownloads();
  });

  afterEach(() => {
    downloads.restore();
    vi.unstubAllGlobals();
    setAuthToken(null);
  });

  it("出力と取込の部品と、6.3 の列の定義を出す", async () => {
    mockApi(routes());
    const { container } = renderAt();

    expect(container.querySelector('[data-screen-id="UI-INT-001"]')).not.toBeNull();
    expect(await screen.findByRole("form", { name: "出力する" })).toBeInTheDocument();
    expect(screen.getByRole("form", { name: "取り込む" })).toBeInTheDocument();
    expect(screen.getAllByText(/WBS ID, 親のWBS ID, 区分, 名称, 責任者のログインID/)).toHaveLength(2);

    const exportForm = within(screen.getByRole("form", { name: "出力する" }));
    fireEvent.change(exportForm.getByLabelText("対象"), { target: { value: "time-entries" } });
    expect(screen.getByText(/ログインID, 活動ID, 作業日, 工数（人時）, 備考/)).toBeInTheDocument();
    expect(screen.getByText(/ログインID＋活動ID＋作業日/)).toBeInTheDocument();
  });

  it("権限が出力だけなら、取込の部品を出さない（7.2）", async () => {
    mockApi(routes({ "GET /api/v1/me": () => jsonResponse(200, meWith(["data.export"])) }));
    renderAt();

    expect(await screen.findByRole("form", { name: "出力する" })).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "取り込む" })).toBeNull();
  });

  it("effort.view が無ければ、出力で実績工数を選ばせない（CR-015）。取込の対象は今どおり", async () => {
    // 9.2 の VIEWER 相当：data.export を持ち effort.view を持たない
    mockApi(routes({ "GET /api/v1/me": () => jsonResponse(200, meWith(["data.export", "data.import"])) }));
    renderAt();
    const exportForm = within(await screen.findByRole("form", { name: "出力する" }));

    const exportOptions = within(exportForm.getByLabelText("対象")).getAllByRole("option");
    expect(exportOptions.map((o) => o.getAttribute("value"))).toEqual(["wbs", "activities"]);
    expect(screen.getByText("実績工数の出力には、工数を読む権限（effort.view）が要ります")).toBeInTheDocument();
    const importForm = within(screen.getByRole("form", { name: "取り込む" }));
    const importOptions = within(importForm.getByLabelText("対象")).getAllByRole("option");
    expect(importOptions.map((o) => o.getAttribute("value"))).toEqual(["wbs", "activities", "time-entries"]);
  });

  it("effort.view があれば、出力で実績工数を選べる", async () => {
    mockApi(routes());
    renderAt();
    const exportForm = within(await screen.findByRole("form", { name: "出力する" }));

    const options = within(exportForm.getByLabelText("対象")).getAllByRole("option");
    expect(options.map((o) => o.getAttribute("value"))).toEqual(["wbs", "activities", "time-entries"]);
    expect(screen.queryByText("実績工数の出力には、工数を読む権限（effort.view）が要ります")).toBeNull();
    expect(exportTargets(false)).toEqual(["wbs", "activities"]);
    expect(exportTargets(true)).toEqual(["wbs", "activities", "time-entries"]);
  });

  it("入出力の権限が無ければ、部品を出さずにその旨を示す", async () => {
    mockApi(routes({ "GET /api/v1/me": () => jsonResponse(200, meWith([])) }));
    renderAt();

    expect(await screen.findByText("このプロジェクトで入出力を行う権限がありません")).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "出力する" })).toBeNull();
    expect(screen.queryByRole("form", { name: "取り込む" })).toBeNull();
  });

  it("プロジェクトの ID が数でなければ、見つからないことを示す", () => {
    const { calls } = mockApi(routes());
    renderAt("/projects/abc/import-export");

    expect(screen.getByRole("alert")).toHaveTextContent("プロジェクトが見つかりません");
    expect(calls).toHaveLength(0);
  });

  it("選んだ対象と形式で出力し（API-INT-001）、サーバの付けた名前で保存させる", async () => {
    const { calls } = mockApi(
      routes({
        [`GET ${BASE}/export`]: () =>
          new Response("活動ID,WPのWBS ID\n21,1.1\n", {
            status: 200,
            headers: { "Content-Type": XLSX, "Content-Disposition": 'attachment; filename="project-3-activities.xlsx"' },
          }),
      }),
    );
    renderAt();
    const form = within(await screen.findByRole("form", { name: "出力する" }));

    fireEvent.change(form.getByLabelText("対象"), { target: { value: "activities" } });
    fireEvent.change(form.getByLabelText("形式"), { target: { value: "xlsx" } });
    fireEvent.click(form.getByRole("button", { name: "出力する" }));

    expect(await screen.findByText("project-3-activities.xlsx を出力しました")).toBeInTheDocument();
    const exported = sent(calls, "GET", `${BASE}/export`);
    expect(exported).toHaveLength(1);
    expect(queryOf(exported[0]!)).toEqual({ target: "activities", format: "xlsx" });
    expect(exported[0]!.headers.Authorization).toBe("Bearer t-planner");
    expect(downloads.saved).toHaveLength(1);
    expect(downloads.saved[0]!.filename).toBe("project-3-activities.xlsx");
    expect(await readBlobText(downloads.saved[0]!.blob)).toBe("活動ID,WPのWBS ID\n21,1.1\n");
  });

  it("出力が拒まれたら（403）、サーバの文言を出し、何も保存させない", async () => {
    mockApi(routes({ [`GET ${BASE}/export`]: () => errorResponse(403, "FORBIDDEN", "この操作の権限がありません") }));
    renderAt();
    const form = within(await screen.findByRole("form", { name: "出力する" }));

    fireEvent.click(form.getByRole("button", { name: "出力する" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("この操作の権限がありません");
    expect(downloads.saved).toHaveLength(0);
  });

  it("「検査する」は dry_run で送り（API-INT-002）、書く行の一覧を出す。ファイルの拡張子から形式を選ぶ", async () => {
    const { calls } = mockApi(
      routes({ [`POST ${BASE}/import`]: () => jsonResponse(200, makeResult({ format: "xlsx" })) }),
    );
    renderAt();
    const form = await importForm();
    const file = new File(["xlsx-bytes"], "WBS.XLSX", { type: XLSX });

    chooseFile(form, file);
    expect(form.getByLabelText("形式")).toHaveValue("xlsx");
    fireEvent.click(form.getByRole("button", { name: "検査する" }));

    expect(
      await screen.findByText("検査を通りました。取り込むと 追加 1 件・更新 1 件・変更なし 2 件 になります（まだ書いていません）"),
    ).toBeInTheDocument();
    const posted = sent(calls, "POST", `${BASE}/import`);
    expect(posted).toHaveLength(1);
    expect(queryOf(posted[0]!)).toEqual({ target: "wbs", format: "xlsx", dry_run: "true" });
    expect(posted[0]!.headers["Content-Type"]).toBe(XLSX);
    expect(posted[0]!.rawBody).toBe(file);

    const table = screen.getByRole("table", { name: "取り込む行" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("3更新1.1名称, 自分の見積（人時）");
    expect(rows[1]).toHaveTextContent("5追加1.3—");
  });

  it("検査で誤りがあれば、行・列・コード・メッセージの表を出す（何も書かない）", async () => {
    mockApi(
      routes({
        [`POST ${BASE}/import`]: () =>
          jsonResponse(200, makeResult({ ok: false, created: 0, updated: 0, unchanged: 0, changes: [], issues: ISSUES })),
      }),
    );
    renderAt();
    const form = await importForm();

    chooseFile(form, new File(["x"], "wbs.csv", { type: "text/csv" }));
    fireEvent.click(form.getByRole("button", { name: "検査する" }));

    expect(await screen.findByText("検査で 2 件の誤りが見つかりました。何も書いていません")).toBeInTheDocument();
    const rows = within(screen.getByRole("table", { name: "取込の誤り" })).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("2WBS IDREQUIREDWBS ID が空");
    expect(rows[1]).toHaveTextContent("4—COLUMNS列の数が見出しと合わない");
    expect(screen.queryByRole("table", { name: "取り込む行" })).toBeNull();
  });

  it("「取り込む」は dry_run でなく送り、書いた件数を出す", async () => {
    const { calls } = mockApi(
      routes({
        [`POST ${BASE}/import`]: () =>
          jsonResponse(200, makeResult({ target: "time-entries", dry_run: false, written: true, created: 2, updated: 0, unchanged: 5, changes: [] })),
      }),
    );
    renderAt();
    const form = await importForm();

    fireEvent.change(form.getByLabelText("対象"), { target: { value: "time-entries" } });
    chooseFile(form, new File(["x"], "time.csv", { type: "text/csv" }));
    fireEvent.click(form.getByRole("button", { name: "取り込む" }));

    expect(await screen.findByText("取り込みました（追加 2 件・更新 0 件・変更なし 5 件）")).toBeInTheDocument();
    const posted = sent(calls, "POST", `${BASE}/import`);
    expect(queryOf(posted[0]!)).toEqual({ target: "time-entries", format: "csv", dry_run: "false" });
    expect(posted[0]!.headers["Content-Type"]).toBe("text/csv");
  });

  it("取込が 422 IMPORT_INVALID なら、サーバの文言と誤りの表を出す", async () => {
    mockApi(
      routes({
        [`POST ${BASE}/import`]: () =>
          jsonResponse(422, {
            error: { code: "IMPORT_INVALID", message: "取込の検査に誤りがあるため、何も書きませんでした", details: ISSUES },
          }),
      }),
    );
    renderAt();
    const form = await importForm();

    chooseFile(form, new File(["x"], "wbs.csv", { type: "text/csv" }));
    fireEvent.click(form.getByRole("button", { name: "取り込む" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("取込の検査に誤りがあるため、何も書きませんでした");
    expect(within(alert).queryByRole("listitem")).toBeNull();
    const rows = within(screen.getByRole("table", { name: "取込の誤り" })).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);
  });

  it("承認待ちで WBS を変えられなければ（409）、サーバの文言を出す", async () => {
    mockApi(routes({ [`POST ${BASE}/import`]: () => errorResponse(409, "INVALID_STATE", "承認待ちの間は WBS を変更できません") }));
    renderAt();
    const form = await importForm();

    chooseFile(form, new File(["x"], "wbs.csv", { type: "text/csv" }));
    fireEvent.click(form.getByRole("button", { name: "取り込む" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("承認待ちの間は WBS を変更できません");
    expect(screen.queryByRole("table", { name: "取込の誤り" })).toBeNull();
  });

  it("ファイルを選ばずに取り込もうとすると、送らずに理由を出す", async () => {
    const { calls } = mockApi(routes());
    renderAt();
    const form = await importForm();

    fireEvent.click(form.getByRole("button", { name: "検査する" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("取り込むファイルを選んでください");
    expect(sent(calls, "POST", `${BASE}/import`)).toHaveLength(0);
  });

  it("対象を変えたら、前の結果を消す", async () => {
    mockApi(routes({ [`POST ${BASE}/import`]: () => jsonResponse(200, makeResult()) }));
    renderAt();
    const form = await importForm();

    chooseFile(form, new File(["x"], "wbs.csv", { type: "text/csv" }));
    fireEvent.click(form.getByRole("button", { name: "検査する" }));
    expect(await screen.findByRole("table", { name: "取り込む行" })).toBeInTheDocument();

    fireEvent.change(form.getByLabelText("対象"), { target: { value: "activities" } });

    await waitFor(() => expect(screen.queryByRole("table", { name: "取り込む行" })).toBeNull());
    expect(screen.queryByRole("status")).toBeNull();
  });
});

describe("入出力の小さな計算", () => {
  it("取込の結果を 1 行の文にする（誤り・検査・書いた）", () => {
    expect(describeImportResult(makeResult({ ok: false, issues: ISSUES }))).toBe(
      "検査で 2 件の誤りが見つかりました。何も書いていません",
    );
    expect(describeImportResult(makeResult())).toBe(
      "検査を通りました。取り込むと 追加 1 件・更新 1 件・変更なし 2 件 になります（まだ書いていません）",
    );
    expect(describeImportResult(makeResult({ dry_run: false, written: true }))).toBe(
      "取り込みました（追加 1 件・更新 1 件・変更なし 2 件）",
    );
  });

  it("拡張子から形式を推す（分からなければ null）", () => {
    expect(formatFromFilename("a.csv")).toBe("csv");
    expect(formatFromFilename("A.XLSX")).toBe("xlsx");
    expect(formatFromFilename("a.xls")).toBeNull();
    expect(formatFromFilename("csv")).toBeNull();
  });
});
