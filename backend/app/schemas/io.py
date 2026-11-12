"""表形式の入出力の API（設計書 6.2 の API-INT-002、6.3）の応答。出力（API-INT-001）はファイルそのものを返す。"""

from __future__ import annotations

from pydantic import BaseModel


class ImportIssueOut(BaseModel):
    """取込の誤り 1 件（6.3 の ``{行, 列, コード, メッセージ}``）。``row`` はファイルの行（見出しが 1）、``column`` は列の見出し。"""

    row: int
    column: str | None
    code: str
    message: str


class ImportChangeOut(BaseModel):
    """取り込む（取り込んだ）1 行。``action`` は CREATE／UPDATE、``changed`` は更新で変わる列の見出し。

    ``key`` は照合キー：wbs は WBS ID（追加を書いた後は振った WBS ID）、activities は活動ID（追加は書いた後に振った ID。
    ``dry_run`` では空）、time-entries は ``ログインID/活動ID/作業日``。
    """

    row: int
    action: str
    key: str
    changed: list[str]


class ImportResultOut(BaseModel):
    """取込の結果。``ok`` が偽なら ``issues`` に全ての誤りがあり、何も書いていない（``dry_run`` のときだけこの形で返す）。"""

    target: str
    format: str
    dry_run: bool
    ok: bool
    written: bool
    created: int
    updated: int
    unchanged: int
    changes: list[ImportChangeOut]
    issues: list[ImportIssueOut]
