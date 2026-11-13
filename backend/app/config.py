"""設定（設計書 8 章 NFR-10）。設定は環境変数だけから読む。

- ``PMT_DATABASE_URL``：DB の接続先。無ければ開発用の SQLite のファイル（``backend/pmt-dev.db``）
- ``PMT_TOKEN_TTL_HOURS``：認証トークンの有効期間（時間）。既定 12（設計書 6.1）
- ``PMT_INITIAL_ADMIN_LOGIN``・``PMT_INITIAL_ADMIN_PASSWORD``：最初のシステム管理者。
  既定値を持たない（本番値を独自に決めない）。使う側が未指定なら fail fast する
- ``PMT_PASSWORD_MIN_LENGTH``：パスワードの最小の長さ。既定 12（NFR-04。最大は 1024 で設定にしない）
- ``PMT_LOGIN_MAX_FAILURES``：ログインを続けて誤るとロックする回数。既定 10（NFR-04）
- ``PMT_LOGIN_LOCK_MINUTES``：ロックの長さ（分）。既定 15（NFR-04）
- ``PMT_API_DOCS``：API の説明（``/docs``・``/redoc``・``/api/v1/openapi.json``）を出すか。既定 偽（設計書 6.1・CR-019）
- ``PMT_BUSINESS_TIMEZONE``：「今日」と日付で指定する期間の暦の時間帯（IANA の名前）。既定 ``Asia/Tokyo``（設計書 1.3・CR-019）。
  知らない名前なら起動しない（fail fast）
- ``PMT_IMPORT_MAX_BYTES``・``PMT_IMPORT_MAX_ROWS``：取込のファイルの上限。既定 10485760 バイト・50000 行（設計書 6.3・CR-019）
- ``PMT_SHARE_LINK_MAX_DAYS``：共有リンクの有効期限の上限（今からの日数）。既定 90（API-RPT-002・CR-019）
- ``PMT_STATIC_DIR``：画面のビルド（``frontend/dist``）を置いたディレクトリ。指定すればサーバが画面を配る。既定 無し（設計書 1.2・CR-020）

``.env`` ファイルは読まない（秘密情報をファイルに置かせない）。
"""

from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator
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
    # 最大の長さ（1024。app.services.users の PASSWORD_MAX_LENGTH）を超える最小は、どのパスワードも通らないので拒む
    password_min_length: int = Field(default=12, ge=1, le=1024)
    login_max_failures: int = Field(default=10, gt=0)
    login_lock_minutes: int = Field(default=15, gt=0)
    api_docs: bool = False
    business_timezone: str = "Asia/Tokyo"
    import_max_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    import_max_rows: int = Field(default=50_000, gt=0)
    share_link_max_days: int = Field(default=90, gt=0)
    static_dir: Path | None = None

    @field_validator("business_timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"PMT_BUSINESS_TIMEZONE の時間帯が見つかりません: {value!r}") from exc
        return value

    @property
    def business_zone(self) -> ZoneInfo:
        """業務の時間帯（「今日」の暦。設計書 1.3）。"""
        return ZoneInfo(self.business_timezone)


def get_settings() -> Settings:
    """環境変数から設定を読み直して返す（キャッシュしない。試験で差し替えやすくするため）。"""
    return Settings()
