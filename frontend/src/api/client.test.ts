import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiFetch, setAuthToken } from "./client";

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

  it("成功の応答が JSON でなければ INVALID_RESPONSE にする", async () => {
    fetchMock.mockResolvedValue(new Response("<html></html>", { status: 200 }));

    const error = await apiFetch("/x").catch((e: unknown) => e);

    expect(error).toMatchObject({ status: 200, code: "INVALID_RESPONSE" });
  });
});
