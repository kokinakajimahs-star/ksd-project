// スケジュールの画面の上限。サーバの検査（backend/app/schemas/schedule.py・effort.py）と同じ値にする。
// 画面の上限は入力の補助で、正の検査はサーバが行う

/** 一覧の limit の上限（設計書 6.1） */
export const LIST_LIMIT_MAX = 1000;
/** タスクリストの 1 頁の件数（一括編集の単位。API-SCH-004 の上限 1000 件以内） */
export const ACTIVITIES_PAGE_SIZE = 200;
/** 活動の名称・基準の版の名前（NAME_MAX） */
export const ACTIVITY_NAME_MAX = 200;
export const BASELINE_NAME_MAX = 200;
/** 期間（稼働日。DURATION_MAX） */
export const DURATION_MAX = 100000;
