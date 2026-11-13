import { describe, expect, it, vi } from "vitest";

import { ApiError } from "./client";
import { fetchAllPages, LIST_CHANGED_MESSAGE, PAGE_LIMIT_MAX } from "./pages";

/** 0 から n−1 までの数を、limit・offset の頁に切って返す一覧（total は固定） */
function numbers(n: number) {
  const all = Array.from({ length: n }, (_, i) => i);
  return vi.fn(async ({ limit, offset }: { limit: number; offset: number }) => ({
    items: all.slice(offset, offset + limit),
    total: n,
  }));
}

describe("一覧の全件を読む（6.1・A-49 の 2）", () => {
  it("limit 1000 で total に達するまで offset を進めて読み足す（2 頁にまたがる）", async () => {
    const fetchPage = numbers(1500);

    const items = await fetchAllPages(fetchPage);

    expect(items).toHaveLength(1500);
    expect(items[0]).toBe(0);
    expect(items[1499]).toBe(1499);
    expect(fetchPage.mock.calls.map(([q]) => q)).toEqual([
      { limit: PAGE_LIMIT_MAX, offset: 0 },
      { limit: PAGE_LIMIT_MAX, offset: 1000 },
    ]);
  });

  it("total が 1000 ちょうど・1 頁で足りるときは 1 回だけ読む", async () => {
    const exact = numbers(1000);
    expect(await fetchAllPages(exact)).toHaveLength(1000);
    expect(exact).toHaveBeenCalledTimes(1);

    const small = numbers(3);
    expect(await fetchAllPages(small)).toEqual([0, 1, 2]);
    expect(small).toHaveBeenCalledTimes(1);
  });

  it("空の一覧は 1 回読んで空を返す", async () => {
    const empty = numbers(0);
    expect(await fetchAllPages(empty)).toEqual([]);
    expect(empty).toHaveBeenCalledTimes(1);
  });

  it("total に届く前に空の頁が返れば、そこで止まる（無限に読まない）", async () => {
    const fetchPage = vi.fn(async ({ offset }: { limit: number; offset: number }) => ({
      items: offset === 0 ? ["a", "b"] : [],
      total: 5,
    }));

    expect(await fetchAllPages(fetchPage)).toEqual(["a", "b"]);
    expect(fetchPage).toHaveBeenCalledTimes(2);
  });

  it("読む間に total が変われば、読んだ分を返さずに 409 LIST_CHANGED を投げる", async () => {
    let total = 1200;
    const fetchPage = vi.fn(async ({ limit, offset }: { limit: number; offset: number }) => {
      const page = { items: Array.from({ length: Math.min(limit, total - offset) }, (_, i) => offset + i), total };
      total -= 1; // 1 頁目を読んだ後に 1 件消えた
      return page;
    });

    const error = await fetchAllPages(fetchPage).catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 409, code: "LIST_CHANGED", message: LIST_CHANGED_MESSAGE });
    expect(fetchPage).toHaveBeenCalledTimes(2);
  });

  it("頁の読み込みの誤りはそのまま投げる", async () => {
    const failure = new ApiError(403, { code: "FORBIDDEN", message: "権限がありません", details: [] });
    await expect(fetchAllPages(() => Promise.reject(failure))).rejects.toBe(failure);
  });
});
