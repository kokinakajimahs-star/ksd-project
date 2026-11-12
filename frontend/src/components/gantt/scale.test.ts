import { describe, expect, it } from "vitest";

import {
  addDays,
  chartRange,
  countedAs,
  DAY_WIDTH,
  daysBetween,
  judgementColor,
  lightningPolyline,
  pastLineDash,
  PAST_LINE_DASHES,
  rangeWidth,
  ticks,
  visibleRows,
  xOfDate,
  xOfPoint,
} from "./scale";

describe("ガントの座標（設計書 7.3：横軸は暦日）", () => {
  it("暦日の差・足し算（月・年をまたぐ）", () => {
    expect(daysBetween("2026-12-01", "2026-12-10")).toBe(9);
    expect(daysBetween("2026-12-10", "2026-12-01")).toBe(-9);
    expect(daysBetween("2026-12-31", "2027-01-01")).toBe(1);
    expect(addDays("2026-02-28", 1)).toBe("2026-03-01");
    expect(addDays("2026-03-01", -1)).toBe("2026-02-28");
    expect(() => daysBetween("2026/12/01", "2026-12-02")).toThrow();
  });

  it("稲妻チャート.txt §6 の例：原点 12/01・報告日 12/10 の終業は x＝10 日分、P＝5.25（12/06 の 1/4）は 5.25 日分", () => {
    const w = DAY_WIDTH.day;
    // 全ての曜日を稼働日にした例（5.6 の試験の基準）。暦日上の座標はサーバが返す
    expect(xOfPoint("2026-12-01", { date: "2026-12-10", fraction: 1 }, w)).toBe(10 * w);
    expect(xOfPoint("2026-12-01", { date: "2026-12-06", fraction: 0.25 }, w)).toBe(5.25 * w);
    expect(xOfPoint("2026-12-01", { date: "2026-12-13", fraction: 0.6 }, w)).toBeCloseTo(12.6 * w);
    expect(xOfDate("2026-12-01", "2026-12-01", w)).toBe(0);
  });

  it("表示の単位は 1 日あたりの横幅だけを変える", () => {
    const range = { start: "2026-04-01", end: "2026-04-30" };
    expect(rangeWidth(range, DAY_WIDTH.day)).toBe(30 * DAY_WIDTH.day);
    expect(rangeWidth(range, DAY_WIDTH.week)).toBe(30 * DAY_WIDTH.week);
    expect(DAY_WIDTH.day).toBeGreaterThan(DAY_WIDTH.week);
    expect(DAY_WIDTH.week).toBeGreaterThan(DAY_WIDTH.month);
  });

  it("描く範囲は、サーバの範囲を報告日線などの日付で広げる。活動が無ければ null", () => {
    expect(chartRange({ range_start: "2026-04-01", range_end: "2026-04-30" }, [])).toEqual({
      start: "2026-04-01",
      end: "2026-04-30",
    });
    expect(chartRange({ range_start: "2026-04-01", range_end: "2026-04-30" }, ["2026-05-08", "2026-03-31"])).toEqual(
      { start: "2026-03-31", end: "2026-05-08" },
    );
    expect(chartRange({ range_start: null, range_end: null }, ["2026-05-08"])).toBeNull();
  });
});

describe("縦方向の仮想化（7.3・NFR-02）", () => {
  it("見えている行と前後の余分だけを描く", () => {
    expect(visibleRows(0, 280, 2000, 28, 5)).toEqual({ first: 0, last: 15 });
    expect(visibleRows(28 * 100, 280, 2000, 28, 5)).toEqual({ first: 95, last: 115 });
  });

  it("末尾で行の数を超えない。行が無ければ空", () => {
    expect(visibleRows(28 * 1995, 280, 2000, 28, 5)).toEqual({ first: 1990, last: 1999 });
    expect(visibleRows(0, 280, 0, 28)).toEqual({ first: 0, last: -1 });
    expect(visibleRows(-50, 280, 3, 28, 0)).toEqual({ first: 0, last: 2 });
  });
});

describe("判定の色（7.3 の ⑦）", () => {
  it("遅れ＝赤、前倒し＝青、予定どおり＝緑、未算定＝灰。完了・未着手（予定内）は予定どおりの色", () => {
    expect(judgementColor("BEHIND")).toBe("#d32f2f");
    expect(judgementColor("AHEAD")).toBe("#1976d2");
    expect(judgementColor("ON_SCHEDULE")).toBe("#388e3c");
    expect(judgementColor("NOT_COMPUTED")).toBe("#9e9e9e");
    expect(judgementColor("COMPLETED")).toBe("#388e3c");
    expect(judgementColor("NOT_STARTED")).toBe("#388e3c");
    expect(countedAs("COMPLETED")).toBe("ON_SCHEDULE");
    expect(countedAs("BEHIND")).toBe("BEHIND");
  });

  it("過去の線は報告日ごとに線の種類を変え、種類が尽きたら繰り返す", () => {
    expect(pastLineDash(0)).not.toBe(pastLineDash(1));
    expect(pastLineDash(PAST_LINE_DASHES.length)).toBe(pastLineDash(0));
  });
});

describe("目盛り", () => {
  const range = { start: "2026-03-30", end: "2026-04-14" };

  it("日：毎日（最初の日と月の初日は 月/日）", () => {
    const result = ticks(range, "day");
    expect(result).toHaveLength(16);
    expect(result[0]).toEqual({ date: "2026-03-30", label: "3/30" });
    expect(result[1]).toEqual({ date: "2026-03-31", label: "31" });
    expect(result[2]).toEqual({ date: "2026-04-01", label: "4/1" });
  });

  it("週：月曜（と最初の日）", () => {
    expect(ticks({ start: "2026-04-01", end: "2026-04-14" }, "week").map((t) => t.date)).toEqual([
      "2026-04-01",
      "2026-04-06",
      "2026-04-13",
    ]);
  });

  it("月：1 日（と最初の日）", () => {
    expect(ticks(range, "month")).toEqual([
      { date: "2026-03-30", label: "2026-03" },
      { date: "2026-04-01", label: "2026-04" },
    ]);
  });
});

describe("イナズマ線の折れ線（PRG-12）", () => {
  it("報告日線の上端から行の順に頂点を結び、下端で終える", () => {
    const line = lightningPolyline(
      [
        { row: 1, x: 300 },
        { row: 0, x: 126 },
      ],
      240,
      2,
      28,
      32,
    );
    expect(line).toBe("240,32 126,46 300,74 240,88");
  });
});
