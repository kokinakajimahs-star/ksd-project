import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setAuthToken } from "../../api/client";
import { makeAuditLog, memberPage } from "../../test/chgFixtures";
import { makeMember } from "../../test/fixtures";
import { errorResponse, jsonResponse, mockApi, type RecordedCall, type RouteHandler } from "../../test/mockApi";
import AuditLogsPage, { auditChanges, toAuditQuery } from "./AuditLogsPage";

const FUTURE = { v7_startTransition: true, v7_relativeSplatPath: true };
const BASE = "/api/v1/projects/3";

const UPDATE = makeAuditLog();
const CREATE = makeAuditLog({
  id: 902,
  action: "CREATE",
  entity_type: "corrective_actions",
  entity_id: 81,
  wbs_code: "1.2",
  before: null,
  after: { cause: "仕様の確定が遅れた", status: "NOT_STARTED" },
});
const DELETE = makeAuditLog({
  id: 903,
  action: "DELETE",
  user_id: 99,
  entity_type: "dependencies",
  entity_id: 7,
  wbs_code: null,
  before: { predecessor_id: 21, successor_id: 22 },
  after: null,
});

function routes(overrides: Record<string, RouteHandler> = {}): Record<string, RouteHandler> {
  return {
    [`GET ${BASE}/members`]: () =>
      jsonResponse(200, memberPage([makeMember({ user_id: 2, display_name: "計画 太郎" })])),
    [`GET ${BASE}/audit-logs`]: () => jsonResponse(200, { items: [UPDATE, CREATE, DELETE], total: 3, limit: 100, offset: 0 }),
    ...overrides,
  };
}

function renderAt(path = "/projects/3/audit-logs") {
  return render(
    <MemoryRouter initialEntries={[path]} future={FUTURE}>
      <Routes>
        <Route path="/projects/:projectId/audit-logs" element={<AuditLogsPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

function sent(calls: RecordedCall[], method: string, path: string) {
  return calls.filter((c) => c.method === method && c.path === path);
}

describe("UI-CHG-003 変更履歴", () => {
  beforeEach(() => {
    setAuthToken("t-pm");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    setAuthToken(null);
  });

  it("変更者・日時・操作・対象・WBS ID・変更前後の値を出す（CHG-09）", async () => {
    mockApi(routes());
    const { container } = renderAt();

    expect(container.querySelector('[data-screen-id="UI-CHG-003"]')).not.toBeNull();
    const table = await screen.findByRole("table", { name: "変更履歴" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(3);

    expect(await within(rows[0]!).findByText("計画 太郎")).toBeInTheDocument();
    expect(rows[0]).toHaveTextContent("更新");
    expect(rows[0]).toHaveTextContent("activities");
    expect(rows[0]).toHaveTextContent("21");
    expect(rows[0]).toHaveTextContent("1.1");
    expect(rows[0]).toHaveTextContent("duration_days: 5 → 8");

    expect(rows[1]).toHaveTextContent("追加");
    expect(rows[1]).toHaveTextContent("cause: 仕様の確定が遅れた");
    expect(rows[1]).toHaveTextContent("status: NOT_STARTED");

    expect(rows[2]).toHaveTextContent("削除");
    expect(rows[2]).toHaveTextContent("#99");
    expect(rows[2]).toHaveTextContent("predecessor_id: 21");
  });

  it("履歴を変更・削除する部品を置かない（4.2・NFR-05）", async () => {
    const { calls } = mockApi(routes());
    renderAt();
    const table = await screen.findByRole("table", { name: "変更履歴" });

    expect(within(table).queryAllByRole("button")).toHaveLength(0);
    expect(screen.queryByRole("button", { name: /削除|編集|変更する/ })).toBeNull();
    expect(calls.every((c) => c.method === "GET")).toBe(true);
  });

  it("期間・変更者・対象・WBS ID で検索する（API-CHG-010）", async () => {
    const { calls } = mockApi(routes());
    renderAt();
    const form = screen.getByRole("form", { name: "変更履歴を検索する" });
    await within(form).findByRole("option", { name: "計画 太郎" });

    fireEvent.change(within(form).getByLabelText("期間（から）"), { target: { value: "2026-11-01" } });
    fireEvent.change(within(form).getByLabelText("期間（まで）"), { target: { value: "2026-11-10" } });
    fireEvent.change(within(form).getByLabelText("変更者"), { target: { value: "2" } });
    fireEvent.change(within(form).getByLabelText("対象の種類（表の名前）"), { target: { value: "activities" } });
    fireEvent.change(within(form).getByLabelText("対象の ID"), { target: { value: "21" } });
    fireEvent.change(within(form).getByLabelText("WBS ID（記録した時点の ID）"), { target: { value: "1.1" } });
    fireEvent.click(within(form).getByRole("button", { name: "検索する" }));

    await waitFor(() => {
      const last = sent(calls, "GET", `${BASE}/audit-logs`).at(-1)!;
      const query = new URLSearchParams(last.url.split("?")[1]);
      expect(query.get("date_from")).toBe("2026-11-01");
      expect(query.get("date_to")).toBe("2026-11-10");
      expect(query.get("user_id")).toBe("2");
      expect(query.get("entity_type")).toBe("activities");
      expect(query.get("entity_id")).toBe("21");
      expect(query.get("wbs_code")).toBe("1.1");
      expect(query.get("offset")).toBe("0");
    });
  });

  it("期間が逆・対象の ID が整数でなければ、検索せずに理由を出す", async () => {
    const { calls } = mockApi(routes());
    renderAt();
    await screen.findByRole("table", { name: "変更履歴" });
    const before = sent(calls, "GET", `${BASE}/audit-logs`).length;
    const form = screen.getByRole("form", { name: "変更履歴を検索する" });

    fireEvent.change(within(form).getByLabelText("期間（から）"), { target: { value: "2026-11-10" } });
    fireEvent.change(within(form).getByLabelText("期間（まで）"), { target: { value: "2026-11-01" } });
    fireEvent.click(within(form).getByRole("button", { name: "検索する" }));
    expect(within(form).getByRole("alert")).toHaveTextContent("期間の始まりは終わり以前の日付にしてください");
    expect(sent(calls, "GET", `${BASE}/audit-logs`)).toHaveLength(before);
  });

  it("audit.view が無い（403）ときはサーバの文言を出す", async () => {
    mockApi(routes({ [`GET ${BASE}/audit-logs`]: () => errorResponse(403, "FORBIDDEN", "変更履歴を読む権限がありません") }));
    renderAt();
    expect(await screen.findByRole("alert")).toHaveTextContent("変更履歴を読む権限がありません");
    expect(screen.queryByRole("table", { name: "変更履歴" })).toBeNull();
  });

  it("プロジェクトの ID が読めなければ、API を呼ばずに知らせる", () => {
    const { calls } = mockApi({});
    renderAt("/projects/x/audit-logs");
    expect(screen.getByRole("alert")).toHaveTextContent("プロジェクトが見つかりません");
    expect(calls).toHaveLength(0);
  });

  it("項目ごとの前後：追加は後だけ、削除は前だけ、更新は両方（前に無い項目は後の順で足す）", () => {
    expect(auditChanges({ before: { a: 1 }, after: { a: 2, b: "x" } })).toEqual([
      { field: "a", before: 1, after: 2 },
      { field: "b", before: undefined, after: "x" },
    ]);
    expect(auditChanges({ before: null, after: { a: 1 } })).toEqual([{ field: "a", before: undefined, after: 1 }]);
    expect(auditChanges({ before: { a: 1 }, after: null })).toEqual([{ field: "a", before: 1, after: undefined }]);
  });

  it("検索の条件：空は付けない。対象の ID は整数だけ", () => {
    const empty = { dateFrom: "", dateTo: "", userId: "", entityType: " ", entityId: "", wbsCode: "" };
    expect(toAuditQuery(empty)).toEqual({ query: {} });
    expect(toAuditQuery({ ...empty, entityId: "1.5" })).toEqual({ message: "対象の ID は整数で入力してください" });
    expect(toAuditQuery({ ...empty, dateFrom: "2026-11-01", dateTo: "2026-11-01" })).toEqual({
      query: { date_from: "2026-11-01", date_to: "2026-11-01" },
    });
  });
});
