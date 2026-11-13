// 一覧の全件を読む（設計書 6.1：一覧は limit（既定 100、上限 1000）・offset を受け、total を返す）。
// 選択肢・一覧に要る資源（活動・報告日・承認待ち・メンバー）は先頭の 1000 件で切らず、total まで頁を読み足す
// （docs/decisions/ASSUMPTIONS.md A-49 の 2。活動は NFR-01 で 2,000 件まで）
import { ApiError } from "./client";
import type { Page } from "./cmn";

/** 一覧の limit の上限（6.1） */
export const PAGE_LIMIT_MAX = 1000;

/** 一覧の 1 頁を読む関数（各 API の list…(…, { limit, offset }) を包む） */
export type PageFetcher<T> = (query: { limit: number; offset: number }) => Promise<Pick<Page<T>, "items" | "total">>;

/** 読む間に一覧の件数（total）が変わったときの誤り（画面で作る。頁の境目で行がずれ、抜け・重なりが出うる） */
export const LIST_CHANGED_MESSAGE = "一覧を読む間に件数が変わりました。読み直してください";

/**
 * 一覧の全ての頁を limit 1000 で先頭から順に読み、items をつないで返す。
 * 止まる条件（無限に読まない）：読んだ件数が total に達した・空の頁が返った・total が最初の頁と変わった。
 * total が変わったときは、読んだ分を返さずに ApiError（409 `LIST_CHANGED`）を投げる（黙って抜けた一覧を返さない）
 */
export async function fetchAllPages<T>(fetchPage: PageFetcher<T>): Promise<T[]> {
  const items: T[] = [];
  let total: number | null = null;
  for (;;) {
    const page = await fetchPage({ limit: PAGE_LIMIT_MAX, offset: items.length });
    if (total !== null && page.total !== total) {
      throw new ApiError(409, { code: "LIST_CHANGED", message: LIST_CHANGED_MESSAGE, details: [] });
    }
    total = page.total;
    items.push(...page.items);
    if (page.items.length === 0 || items.length >= total) {
      return items;
    }
  }
}
