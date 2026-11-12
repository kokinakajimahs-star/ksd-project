// 入力の長さの上限。サーバの検査（backend/app/schemas/projects.py。列に合わせた A-11）と同じ値にする。
// 画面の上限は入力の補助で、正の検査はサーバが行う
export const PROJECT_CODE_MAX = 50;
export const PROJECT_NAME_MAX = 200;
export const SCOPE_TITLE_MAX = 200;
export const HOLIDAY_NAME_MAX = 200;
export const MEMBER_ORGANIZATION_MAX = 200;
export const MEMBER_TEAM_MAX = 200;
/** 1 日の稼働時間（API-PRJ-008：0 より大きく 24 以下・小数 2 桁まで） */
export const HOURS_PER_DAY_MAX = 24;
