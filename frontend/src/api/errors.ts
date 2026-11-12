// 画面に出すエラーの文言。サーバの文言（設計書 2.4 の message。日本語）をそのまま出す
import { ApiError } from "./client";

export const NETWORK_ERROR_MESSAGE = "サーバに接続できません。時間をおいてやり直してください";

/** 例外を利用者に見せる 1 行にする。ApiError はサーバの文言、それ以外は接続の失敗とみなす */
export function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    return error.message;
  }
  return NETWORK_ERROR_MESSAGE;
}

/** 画面に出すエラー：1 行の文言と、項目ごとの理由（400 の details の message） */
export interface ErrorView {
  message: string;
  details: string[];
}

/**
 * 例外を文言と項目ごとの理由にする。理由は details（設計書 2.4）のうち message を持つものを、重ねずに順に並べる。
 * 「入力に誤りがあります」だけでは、どの項目を直せばよいか分からないため
 */
export function toErrorView(error: unknown): ErrorView {
  const details: string[] = [];
  if (error instanceof ApiError) {
    for (const detail of error.details) {
      if (typeof detail === "object" && detail !== null && "message" in detail) {
        const message = (detail as { message: unknown }).message;
        if (typeof message === "string" && message !== "" && !details.includes(message)) {
          details.push(message);
        }
      }
    }
  }
  return { message: describeError(error), details };
}
