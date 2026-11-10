"""設定（設計書 8 章 NFR-10）。設定は環境変数だけから読む。

- ``PMT_DATABASE_URL``：DB の接続先。無ければ開発用の SQLite のファイル（``backend/pmt-dev.db``）
- ``PMT_TOKEN_TTL_HOURS``：認証トークンの有効期間（時間）。既定 12（設計書 6.1）
- ``PMT_INITIAL_ADMIN_LOGIN``・``PMT_INITIAL_ADMIN_PASSWORD``：最初のシステム管理者。
  既定値を持たない（本番値を独自に決めない）。使う側が未指定なら fail fast する

``.env`` ファイルは読まない（秘密情報をファイルに置かせない）。
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE_URL = f"sqlite:///{BACKEND_DIR / 'pmt-dev.db'}"


class Settings(BaseSettings):
    """環境変数 ``PMT_*`` から読む設定。"""

    model_config = SettingsConfigDict(env_prefix="PMT_", env_file=None, extra="ignore")

    database_url: str = DEFAULT_DATABASE_URL
    token_ttl_hours: int = Field(default=12, gt=0)
    initial_admin_login: str | None = None
    initial_admin_password: str | None = None


def get_settings() -> Settings:
    """環境変数から設定を読み直して返す（キャッシュしない。試験で差し替えやすくするため）。"""
    return Settings()
