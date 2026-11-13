// 入力の長さの上限。サーバの検査（backend/app/schemas/users.py。users の列に合わせた A-08）と同じ値にする。
// 画面の上限は入力の補助で、正の検査はサーバが行う
export const LOGIN_ID_MAX = 100;
export const DISPLAY_NAME_MAX = 200;
export const EMAIL_MAX = 254;
export const ORGANIZATION_MAX = 200;
export const PASSWORD_MAX = 1024;
// パスワードの最小の長さ（NFR-04）。サーバの設定 PMT_PASSWORD_MIN_LENGTH の既定（backend/app/config.py）と同じ値。
// 運用で設定を変えたときは画面の注記だけがずれる（正の検査はサーバが行い、違反は 400 の理由で示す）ので、入力の制限には使わない
export const PASSWORD_MIN = 12;
