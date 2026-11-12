import { describe, expect, it } from "vitest";

import { ApiError } from "./client";
import { NETWORK_ERROR_MESSAGE, toErrorView } from "./errors";

describe("toErrorView", () => {
  it("サーバの文言と、details の message を重ねずに順に並べる", () => {
    const error = new ApiError(400, {
      code: "VALIDATION_ERROR",
      message: "入力に誤りがあります",
      details: [
        { loc: ["body", "holidays", 1, "holiday_date"], message: "同じ日付の休日が 2 回書かれています" },
        { loc: ["body", "holidays", 2, "holiday_date"], message: "同じ日付の休日が 2 回書かれています" },
        { loc: ["body", "owner_user_id"], message: "責任者が見つからないか、無効な利用者です" },
      ],
    });
    expect(toErrorView(error)).toEqual({
      message: "入力に誤りがあります",
      details: ["同じ日付の休日が 2 回書かれています", "責任者が見つからないか、無効な利用者です"],
    });
  });

  it("message を持たない details（project_id など）は出さない", () => {
    const error = new ApiError(404, {
      code: "NOT_FOUND",
      message: "プロジェクトが見つかりません",
      details: [{ project_id: 3 }, "文字列", null, { message: "" }],
    });
    expect(toErrorView(error)).toEqual({ message: "プロジェクトが見つかりません", details: [] });
  });

  it("ApiError でなければ接続の失敗とみなす", () => {
    expect(toErrorView(new TypeError("Failed to fetch"))).toEqual({ message: NETWORK_ERROR_MESSAGE, details: [] });
  });
});
