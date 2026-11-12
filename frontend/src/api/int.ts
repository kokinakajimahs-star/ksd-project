// 表形式の入出力の API の型と呼び出し（設計書 6.2 の API-INT-001・002、6.3）。
// 形はサーバの schemas（backend/app/schemas/io.py）と backend/app/api/io.py に合わせる
import { apiDownload, apiUpload, type DownloadedFile } from "./client";

/** 入出力の対象（6.2 の target） */
export type IoTarget = "wbs" | "activities" | "time-entries";
/** ファイルの形式（6.2 の format） */
export type IoFormat = "csv" | "xlsx";

export const IO_TARGETS: readonly IoTarget[] = ["wbs", "activities", "time-entries"];
export const IO_FORMATS: readonly IoFormat[] = ["csv", "xlsx"];

/** 取込で送る本体の型（サーバの API-INT-002 の説明の型。サーバは application/octet-stream も受ける） */
export const IO_MEDIA_TYPES: Readonly<Record<IoFormat, string>> = {
  csv: "text/csv",
  xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
};

/** 取込の誤り 1 件（6.3 の {行, 列, コード, メッセージ}）。row はファイルの行（見出しが 1）、column は列の見出し */
export interface ImportIssue {
  row: number;
  column: string | null;
  code: string;
  message: string;
}

/** 取り込む（取り込んだ）1 行。changed は更新で変わる列の見出し */
export interface ImportChange {
  row: number;
  action: "CREATE" | "UPDATE";
  key: string;
  changed: string[];
}

/** 取込の結果（ImportResultOut）。ok が偽なら issues に全ての誤りがあり、何も書いていない */
export interface ImportResult {
  target: IoTarget;
  format: IoFormat;
  dry_run: boolean;
  ok: boolean;
  written: boolean;
  created: number;
  updated: number;
  unchanged: number;
  changes: ImportChange[];
  issues: ImportIssue[];
}

/** API-INT-001 出力する（権限は data.export） */
export function exportData(projectId: number, target: IoTarget, format: IoFormat): Promise<DownloadedFile> {
  return apiDownload(`/projects/${projectId}/export`, { query: { target, format } });
}

/**
 * API-INT-002 取り込む（権限は data.import）。dry_run は検査だけ（誤りがあっても 200 で ok が偽）。
 * dry_run でなく誤りがあれば 422 IMPORT_INVALID（details が誤りの配列）で、何も書かない
 */
export function importData(
  projectId: number,
  target: IoTarget,
  format: IoFormat,
  file: Blob,
  dryRun: boolean,
): Promise<ImportResult> {
  return apiUpload<ImportResult>(`/projects/${projectId}/import`, file, {
    query: { target, format, dry_run: dryRun },
    contentType: IO_MEDIA_TYPES[format],
  });
}

function isImportIssue(value: unknown): value is ImportIssue {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const v = value as Record<string, unknown>;
  return (
    typeof v.row === "number" &&
    (v.column === null || typeof v.column === "string") &&
    typeof v.code === "string" &&
    typeof v.message === "string"
  );
}

/** 422 IMPORT_INVALID の details から取込の誤りを取り出す（形の違うものは除く） */
export function importIssuesFrom(details: unknown[]): ImportIssue[] {
  return details.filter(isImportIssue);
}
