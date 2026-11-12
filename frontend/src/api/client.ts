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

let unauthorizedHandler: (() => void) | null = null;

/** トークンを付けた要求が 401 になったとき（失効・期限切れ）に呼ぶ処理を登録する。null で外す */
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

/** 問い合わせの値。配列は同じ名前を繰り返す（`report_ids=1&report_ids=2`。サーバの list の Query の形） */
export type QueryValue = string | number | boolean | undefined | (string | number)[];

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  query?: Record<string, QueryValue>;
  signal?: AbortSignal;
}

function buildUrl(path: string, query: RequestOptions["query"]): string {
  const url = `${API_PREFIX}${path.startsWith("/") ? path : `/${path}`}`;
  if (!query) {
    return url;
  }
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (Array.isArray(value)) {
      for (const item of value) {
        params.append(key, String(item));
      }
    } else if (value !== undefined) {
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

interface SendOptions {
  method: NonNullable<RequestOptions["method"]>;
  query?: RequestOptions["query"];
  signal?: AbortSignal;
  accept: string;
  contentType?: string;
  body?: BodyInit;
}

/** 要求を送る。2xx 以外は本体（設計書 2.4 の形）を ApiError に直して投げる。2xx の応答の本体は読まずに返す */
async function send(path: string, options: SendOptions): Promise<Response> {
  const headers: Record<string, string> = { Accept: options.accept };
  if (options.contentType !== undefined) {
    headers["Content-Type"] = options.contentType;
  }
  const sentToken = authToken;
  if (sentToken) {
    headers.Authorization = `Bearer ${sentToken}`;
  }
  const response = await fetch(buildUrl(path, options.query), {
    method: options.method,
    headers,
    body: options.body,
    signal: options.signal,
  });
  if (response.ok) {
    return response;
  }
  const data = parseJson(await response.text());
  if (response.status === 401 && sentToken && sentToken === authToken) {
    unauthorizedHandler?.();
  }
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

function toJson<T>(response: Response, text: string): T {
  const data = parseJson(text);
  if (data === NOT_JSON) {
    throw new ApiError(response.status, {
      code: "INVALID_RESPONSE",
      message: "サーバの応答を読めません",
      details: [],
    });
  }
  return data as T;
}

/** /api/v1 の下を呼び、JSON を返す。2xx 以外は ApiError を投げる */
export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const response = await send(path, {
    method: options.method ?? "GET",
    query: options.query,
    signal: options.signal,
    accept: "application/json",
    contentType: options.body === undefined ? undefined : "application/json",
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  return toJson<T>(response, await response.text());
}

/** サーバが返したファイル。名前は Content-Disposition の filename（無ければ null） */
export interface DownloadedFile {
  blob: Blob;
  filename: string | null;
}

/** Content-Disposition（`attachment; filename="x.csv"`）からファイルの名前を取り出す。無ければ null */
export function filenameFromDisposition(header: string | null): string | null {
  if (header === null) {
    return null;
  }
  const match = /filename="([^"]+)"/.exec(header) ?? /filename=([^;\s]+)/.exec(header);
  return match?.[1] ?? null;
}

/** /api/v1 の下からファイルを受け取る（GET）。2xx 以外は ApiError を投げる */
export async function apiDownload(
  path: string,
  options: Pick<RequestOptions, "query" | "signal"> = {},
): Promise<DownloadedFile> {
  const response = await send(path, { method: "GET", query: options.query, signal: options.signal, accept: "*/*" });
  return {
    blob: await response.blob(),
    filename: filenameFromDisposition(response.headers.get("Content-Disposition")),
  };
}

/** /api/v1 の下へファイルそのものを本体として送り（POST）、JSON を返す。2xx 以外は ApiError を投げる */
export async function apiUpload<T>(
  path: string,
  file: Blob,
  options: Pick<RequestOptions, "query" | "signal"> & { contentType: string },
): Promise<T> {
  const response = await send(path, {
    method: "POST",
    query: options.query,
    signal: options.signal,
    accept: "application/json",
    contentType: options.contentType,
    body: file,
  });
  return toJson<T>(response, await response.text());
}
