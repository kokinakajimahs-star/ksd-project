"""業務上のエラー（設計書 2.4・6.1）。

サービス層・API 層が投げ、``app.main`` の例外ハンドラが
``{"error": {"code", "message", "details"}}`` の形の応答に直す。

**文言（日本語）を指定したいエラーは、全てこの ``AppError`` で投げる。** FastAPI・Starlette の ``HTTPException`` は
フレームワークの部品（ルーティング・認証）が投げるものとして扱い、文言は状態コードで決まる既定の日本語になる
（``detail`` は ``details`` に入る）。
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """HTTP の状態コードとエラーのコードを持つ業務上のエラー。

    例：``AppError(409, "INVALID_STATE", "報告日は確定済みです")``・
    ``AppError(422, "SCHEDULE_CYCLE", "依存が循環しています", details=[...])``
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: list[Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = list(details) if details else []
