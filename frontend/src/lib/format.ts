// 表示の書式（設計書 8 章 NFR-09：日付は YYYY-MM-DD）

function pad2(value: number): string {
  return String(value).padStart(2, "0");
}

// 時差の指定（Z・+09:00 など）が付いているか
const HAS_TIMEZONE = /(Z|[+-]\d{2}:?\d{2})$/i;

/**
 * サーバの日時（ISO 8601）を時刻（エポックからのミリ秒）にする。
 * サーバは UTC で記録する（backend/app/services/clock.py）。時差の指定が無い値は UTC とみなす。読めない値は NaN
 */
export function serverTime(value: string): number {
  return new Date(HAS_TIMEZONE.test(value) ? value : `${value}Z`).getTime();
}

/**
 * サーバの日時（ISO 8601）を、利用者の端末の時刻で `YYYY-MM-DD HH:mm` にする（時差の扱いは serverTime と同じ）。
 * 読めない値はそのまま返す。
 */
export function formatDateTime(value: string): string {
  const time = serverTime(value);
  if (Number.isNaN(time)) {
    return value;
  }
  const date = new Date(time);
  return (
    `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())} ` +
    `${pad2(date.getHours())}:${pad2(date.getMinutes())}`
  );
}
