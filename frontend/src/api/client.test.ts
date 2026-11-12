import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  apiDownload,
  apiFetch,
  apiUpload,
  filenameFromDisposition,
  setAuthToken,
  setUnauthorizedHandler,
} from "./client";
import { readBlobText } from "../test/download";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("apiFetch", () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
    setAuthToken(null);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("/api/v1 の下を呼び、トークンがあれば Bearer を付ける", async () => {
    fetchMock.mockResolvedValue(jsonResponse(200, { status: "ok" }));
    setAuthToken("t-123");

    const result = await apiFetch<{ status: string }>("/health", { query: { limit: 10, offset: undefined } });

    expect(result).toEqual({ status: "ok" });
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("/api/v1/health?limit=10");
    expect((init?.headers as Record<string, string>).Authorization).toBe("Bearer t-123");
    expect(init?.method).toBe("GET");
  });

  it("問い合わせの配列は同じ名前を繰り返す（空の配列は送らない）", async () => {
    fetchMock.mockResolvedValue(jsonResponse(200, { lines: [] }));

    await apiFetch("/projects/3/lightning-lines", { query: { report_ids: [4, 7], other: [] } });

    const [url] = fetchMock.mock.calls[0]!;
    expect(url).toBe("/api/v1/projects/3/lightning-lines?report_ids=4&report_ids=7");
  });

  it("本体を JSON で送る", async () => {
    fetchMock.mockResolvedValue(jsonResponse(201, { id: 1 }));

    await apiFetch("/projects", { method: "POST", body: { name: "A" } });

    const [, init] = fetchMock.mock.calls[0]!;
    expect(init?.body).toBe(JSON.stringify({ name: "A" }));
    expect((init?.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
    expect((init?.headers as Record<string, string>).Authorization).toBeUndefined();
  });

  it("エラーの形（設計書 2.4）を ApiError に直す", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(409, { error: { code: "INVALID_STATE", message: "報告日は確定済みです", details: [] } }),
    );

    const error = await apiFetch("/x").catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 409, code: "INVALID_STATE", message: "報告日は確定済みです" });
  });

  it("形の違うエラーは HTTP_ERROR にする", async () => {
    fetchMock.mockResolvedValue(new Response("Bad Gateway", { status: 502 }));

    const error = await apiFetch("/x").catch((e: unknown) => e);

    expect(error).toMatchObject({ status: 502, code: "HTTP_ERROR" });
  });

  it("トークンを付けた要求が 401 なら、登録した処理を呼ぶ（付けていなければ呼ばない）", async () => {
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    try {
      fetchMock.mockImplementation(async () =>
        jsonResponse(401, { error: { code: "UNAUTHENTICATED", message: "ログインしてください", details: [] } }),
      );

      // ログインの失敗（トークンなし）は失効ではない
      await apiFetch("/auth/login", { method: "POST", body: {} }).catch(() => undefined);
      expect(onUnauthorized).not.toHaveBeenCalled();

      setAuthToken("t-expired");
      const error = await apiFetch("/me").catch((e: unknown) => e);
      expect(error).toMatchObject({ status: 401 });
      expect(onUnauthorized).toHaveBeenCalledTimes(1);
    } finally {
      setUnauthorizedHandler(null);
    }
  });

  it("成功の応答が JSON でなければ INVALID_RESPONSE にする", async () => {
    fetchMock.mockResolvedValue(new Response("<html></html>", { status: 200 }));

    const error = await apiFetch("/x").catch((e: unknown) => e);

    expect(error).toMatchObject({ status: 200, code: "INVALID_RESPONSE" });
  });
});

describe("apiDownload・apiUpload（ファイルの受け取りと送り）", () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
    setAuthToken(null);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    setAuthToken(null);
  });

  it("ファイルを受け取り、Content-Disposition の名前を返す", async () => {
    fetchMock.mockResolvedValue(
      new Response("WBS ID\n1\n", {
        status: 200,
        headers: { "Content-Type": "text/csv", "Content-Disposition": 'attachment; filename="project-3-wbs.csv"' },
      }),
    );
    setAuthToken("t-1");

    const file = await apiDownload("/projects/3/export", { query: { target: "wbs", format: "csv" } });

    expect(file.filename).toBe("project-3-wbs.csv");
    expect(await readBlobText(file.blob)).toBe("WBS ID\n1\n");
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("/api/v1/projects/3/export?target=wbs&format=csv");
    expect(init?.method).toBe("GET");
    expect((init?.headers as Record<string, string>).Authorization).toBe("Bearer t-1");
  });

  it("受け取りのエラーも ApiError に直す", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(403, { error: { code: "FORBIDDEN", message: "この操作の権限がありません", details: [] } }),
    );

    const error = await apiDownload("/projects/3/export").catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 403, code: "FORBIDDEN" });
  });

  it("ファイルそのものを本体にして送り、JSON を返す", async () => {
    fetchMock.mockResolvedValue(jsonResponse(200, { ok: true }));
    const file = new Blob(["a,b\n"], { type: "text/csv" });

    const result = await apiUpload<{ ok: boolean }>("/projects/3/import", file, {
      query: { target: "wbs", dry_run: true },
      contentType: "text/csv",
    });

    expect(result).toEqual({ ok: true });
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("/api/v1/projects/3/import?target=wbs&dry_run=true");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe(file);
    expect((init?.headers as Record<string, string>)["Content-Type"]).toBe("text/csv");
  });

  it("送りのエラーは details を持った ApiError にする", async () => {
    const details = [{ row: 2, column: "WBS ID", code: "REQUIRED", message: "WBS ID が空" }];
    fetchMock.mockResolvedValue(jsonResponse(422, { error: { code: "IMPORT_INVALID", message: "何も書きませんでした", details } }));

    const error = await apiUpload("/projects/3/import", new Blob(["x"]), { contentType: "text/csv" }).catch((e: unknown) => e);

    expect(error).toMatchObject({ status: 422, code: "IMPORT_INVALID", details });
  });

  it("Content-Disposition から名前を取り出す（無ければ null）", () => {
    expect(filenameFromDisposition('attachment; filename="a b.csv"')).toBe("a b.csv");
    expect(filenameFromDisposition("attachment; filename=x.xlsx")).toBe("x.xlsx");
    expect(filenameFromDisposition("attachment")).toBeNull();
    expect(filenameFromDisposition(null)).toBeNull();
  });
});
