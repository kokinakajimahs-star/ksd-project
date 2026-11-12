import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

import { formatDateTime } from "./format";

describe("formatDateTime（NFR-09 の YYYY-MM-DD）", () => {
  // 端末の時刻帯を固定する（Node は TZ の書き換えをその場で反映する）
  beforeAll(() => {
    vi.stubEnv("TZ", "Asia/Tokyo");
  });

  afterAll(() => {
    vi.unstubAllEnvs();
  });

  it("UTC の日時を端末の時刻（ここでは日本時間）で YYYY-MM-DD HH:mm にする", () => {
    expect(formatDateTime("2026-11-10T01:30:00Z")).toBe("2026-11-10 10:30");
    expect(formatDateTime("2026-11-10T15:05:09.123+00:00")).toBe("2026-11-11 00:05");
  });

  it("時差の指定が無い値は UTC とみなす", () => {
    expect(formatDateTime("2026-11-10T01:30:00")).toBe("2026-11-10 10:30");
  });

  it("時差の指定があればそれに従う", () => {
    expect(formatDateTime("2026-11-10T10:30:00+09:00")).toBe("2026-11-10 10:30");
  });

  it("読めない値はそのまま返す", () => {
    expect(formatDateTime("not-a-date")).toBe("not-a-date");
  });
});
