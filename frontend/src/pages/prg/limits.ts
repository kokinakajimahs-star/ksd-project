// 進捗の画面の上限。サーバの検査（backend/app/schemas/progress.py）と同じ値にする。
// 画面の上限は入力の補助で、正の検査はサーバが行う

/** 遅延理由（DELAY_REASON_MAX） */
export const DELAY_REASON_MAX = 4000;
/** 残工数（時間。HOURS_MAX） */
export const HOURS_MAX = 1_000_000;
/** 算定項目の名前（TITLE_MAX）・重み（WEIGHT_MAX）・1 つの活動の項目の数（ITEMS_MAX） */
export const ITEM_TITLE_MAX = 200;
export const ITEM_WEIGHT_MAX = 1_000_000;
export const ITEMS_MAX = 1000;
