// 試験用：fetch を差し替え、"GET /api/v1/me" のような「メソッド パス」ごとに応答を返す。
// 定めていない要求は例外にする（試験が気づかずに通らないように）
import { vi } from "vitest";

export interface RecordedCall {
  method: string;
  /** 問い合わせ（?以降）を含む URL */
  url: string;
  /** 問い合わせを除いたパス */
  path: string;
  headers: Record<string, string>;
  /** JSON の本体を読んだ値（文字列の本体だけ） */
  body: unknown;
  /** 本体そのもの（ファイルを送る要求の Blob など） */
  rawBody: BodyInit | null | undefined;
}

export type RouteHandler = (call: RecordedCall) => Response | Promise<Response>;

export function jsonResponse(status: number, body: unknown): Response {
  if (status === 204) {
    return new Response(null, { status });
  }
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

export function errorResponse(status: number, code: string, message: string): Response {
  return jsonResponse(status, { error: { code, message, details: [] } });
}

/**
 * 一覧（6.1）の応答：all を要求の limit（既定 100）・offset で切り、total に all の件数を入れて返す。
 * 1000 件を超える一覧を頁を読み足して読む試験に使う。extra は一覧の他の欄（project_id・dependencies など）
 */
export function pagedResponse(all: unknown[], call: RecordedCall, extra: Record<string, unknown> = {}): Response {
  const query = new URLSearchParams(call.url.split("?")[1] ?? "");
  const limit = Number(query.get("limit") ?? "100");
  const offset = Number(query.get("offset") ?? "0");
  return jsonResponse(200, { ...extra, items: all.slice(offset, offset + limit), total: all.length, limit, offset });
}

/** 応答の表を受け、fetch を差し替える。呼ばれた要求は calls に順に残る */
export function mockApi(routes: Record<string, RouteHandler>) {
  const calls: RecordedCall[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    const method = init?.method ?? "GET";
    const path = url.split("?")[0] ?? url;
    const body = typeof init?.body === "string" ? (JSON.parse(init.body) as unknown) : undefined;
    const headers = (init?.headers ?? {}) as Record<string, string>;
    const call: RecordedCall = { method, url, path, headers, body, rawBody: init?.body };
    calls.push(call);
    const handler = routes[`${method} ${path}`];
    if (!handler) {
      throw new Error(`試験で定めていない要求: ${method} ${url}`);
    }
    return handler(call);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, fetchMock };
}
