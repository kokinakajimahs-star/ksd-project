// API の呼び出し（設計書 6.1）。接頭辞 /api/v1、認証は Bearer トークン、エラーは {"error": {...}} の形

export const API_PREFIX = "/api/v1";

export interface ApiErrorBody {
  code: string;
  message: string;
  details: unknown[];
}

/** サーバが返したエラー（設計書 2.4 の形）。HTTP の状態コードを持つ */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: unknown[];

  constructor(status: number, body: ApiErrorBody) {
    super(body.message);
    this.name = "ApiError";
    this.status = status;
    this.code = body.code;
    this.details = body.details;
  }
}

let authToken: string | null = null;

/** ログイン（API-CMN-001）で得たトークンを覚える。null で忘れる */
export function setAuthToken(token: string | null): void {
  authToken = token;
}

export function getAuthToken(): string | null {
  return authToken;
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  query?: Record<string, string | number | boolean | undefined>;
  signal?: AbortSignal;
}

function buildUrl(path: string, query: RequestOptions["query"]): string {
  const url = `${API_PREFIX}${path.startsWith("/") ? path : `/${path}`}`;
  if (!query) {
    return url;
  }
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined) {
      params.set(key, String(value));
    }
  }
  const text = params.toString();
  return text ? `${url}?${text}` : url;
}

function isApiErrorBody(value: unknown): value is { error: ApiErrorBody } {
  if (typeof value !== "object" || value === null || !("error" in value)) {
    return false;
  }
  const error = (value as { error: unknown }).error;
  return typeof error === "object" && error !== null && "code" in error && "message" in error;
}

const NOT_JSON = Symbol("not-json");

function parseJson(text: string): unknown {
  if (!text) {
    return null;
  }
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return NOT_JSON;
  }
}

/** /api/v1 の下を呼び、JSON を返す。2xx 以外は ApiError を投げる */
export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  if (authToken) {
    headers.Authorization = `Bearer ${authToken}`;
  }
  const response = await fetch(buildUrl(path, options.query), {
    method: options.method ?? "GET",
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    signal: options.signal,
  });
  const text = await response.text();
  const data = parseJson(text);
  if (!response.ok) {
    if (isApiErrorBody(data)) {
      throw new ApiError(response.status, {
        code: data.error.code,
        message: data.error.message,
        details: Array.isArray(data.error.details) ? data.error.details : [],
      });
    }
    throw new ApiError(response.status, {
      code: "HTTP_ERROR",
      message: `要求を処理できません（${response.status}）`,
      details: [],
    });
  }
  if (data === NOT_JSON) {
    throw new ApiError(response.status, {
      code: "INVALID_RESPONSE",
      message: "サーバの応答を読めません",
      details: [],
    });
  }
  return data as T;
}
