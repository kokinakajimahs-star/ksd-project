# タスク: 構成づくり（scaffold）— 試験・型検査・DB の移行が走る状態にする

あなたは PMT（WBS・工数・進捗管理システム）の実装を始める。最初に `CLAUDE.md` と設計書 `docs/pmt-design.md` の
1 章（方式設計）・2 章（実装の置き場と規約）・6.1（API の共通の規約）・8 章（非機能設計）・10 章を読むこと。

## このタスクのゴール

設計書 2.1 のディレクトリ構成と 2.2 の開発環境を作り、`spec-ids.toml` の `[[checks]]` の 4 つ
（サーバの試験・DB の移行・画面の型検査・画面の試験）が**実際に走って通る**状態にする。

**設計ID（DB-・RULE-・API-・UI-）は 1 件も実装しない。** それは後続のスコープごとの実装ループの仕事である。
ID を宣言の形（行頭のコメント・docstring の先頭・`data-screen-id`）で書かないこと。

## 作るもの

### サーバ（`backend/`）

- `pyproject.toml`：パッケージ名 `pmt-backend`、Python 3.12、`[tool.pytest.ini_options]` に `pythonpath = ["."]`・`testpaths = ["tests"]`
- `requirements.lock`：版を `==` で固定する。少なくとも fastapi・uvicorn・sqlalchemy（2 系）・alembic・pydantic（2 系）・
  pydantic-settings・openpyxl・httpx・pytest。**推移的な依存も含めて固定する**（`.venv/bin/python -m pip freeze` の結果でよい）
- `app/main.py`：FastAPI のアプリ（`create_app()` と `app`）。`/api/v1` の接頭辞でルータを登録する仕組み（群ごとのルータは後続が足す）。
  エラー応答の形は設計書 2.4・6.1（`{"error": {"code", "message", "details"}}`）にする共通の例外ハンドラ。死活の `GET /api/v1/health`（ID を持たない）
- `app/config.py`：設定（環境変数 `PMT_DATABASE_URL`・`PMT_TOKEN_TTL_HOURS`・`PMT_INITIAL_ADMIN_LOGIN`・`PMT_INITIAL_ADMIN_PASSWORD`。設計書 8 章）。
  `PMT_DATABASE_URL` が無いときの既定は開発用の SQLite のファイル（`backend/pmt-dev.db`。`.gitignore` 済み）
- `app/infra/db.py`：SQLAlchemy 2 の `DeclarativeBase`（`Base`）・エンジン・セッションの生成・FastAPI の依存（セッション）。SQLite では外部キーを有効にする
- `app/infra/audit_listener.py`・`app/infra/security.py`：中身は後続が書く。モジュールの説明だけを置く（ID を書かない）
- `app/api/`・`app/schemas/`・`app/services/`・`app/domain/`・`app/models/`：パッケージ（`__init__.py`）。
  `app/models/__init__.py` は後続のモデルを import して `Base.metadata` に載せる場所であることを説明に書く
- `alembic.ini`・`alembic/env.py`・`alembic/script.py.mako`・`alembic/versions/`：`script_location = %(here)s/alembic`。
  接続先は `PMT_DATABASE_URL`（無ければ config の既定）。`target_metadata = Base.metadata`（`app.models` を import する）。
  **リポジトリの根から** `.venv/bin/python -m alembic -c backend/alembic.ini upgrade head` が通ること（移行が 0 本でも通る）
- `tests/conftest.py`：一時ファイルの SQLite でアプリと TestClient を作る fixture。`tests/test_health.py`：死活の試験（試験が 0 件だと pytest が失敗する）

### 画面（`frontend/`）

- `package.json`：scripts に `dev`・`build`（`tsc -b && vite build`）・`typecheck`（`tsc --noEmit -p tsconfig.json`）・`test`（`vitest run`）。
  依存は react 18・react-dom・react-router-dom 6、開発依存は typescript 5・vite 5・@vitejs/plugin-react・vitest・@testing-library/react・
  @testing-library/jest-dom・jsdom・@types/react・@types/react-dom。**版は `^` を付けず固定する**
- `package-lock.json`：`bash scripts/setup-dev.sh` が作る（無ければ npm install で作られる）。コミットする
- `tsconfig.json`（strict）・`vite.config.ts`（React・`/api` を `http://127.0.0.1:8000` へ渡す proxy・Vitest の jsdom）・`index.html`
- `src/main.tsx`・`src/App.tsx`：React Router の枠だけ（ルートは後続の画面のループが 7.1 のとおり足す）。
  `src/api/client.ts`：`/api/v1` を呼ぶ薄い関数（Bearer トークンを付ける）。`src/App.test.tsx`：枠が描画される試験

### 開発環境

- `scripts/setup-dev.sh` は既にある（保護パス。**編集しない**）。依存を書いたらこれを流して `.venv` と `frontend/node_modules` を作る
- 依存のインストールにはネットワークが要る。npm の置き場は `scripts/setup-dev.sh` が `.cache/npm` にする

## 完了の定義

```bash
bash scripts/setup-dev.sh
bash scripts/verify.sh scaffold
```

`scaffold` のスコープでは、`spec-ids.toml` の `[[extra_tasks]] scaffold` の `requires`（作るべきファイル）と、4 つの検査
（`backend-tests`・`db-migrations`・`frontend-typecheck`・`frontend-test`）と要求→設計の写像（`trace`）が見られる。exit 0 になること。

## 進め方

1. まず `bash scripts/verify.sh scaffold` を流し、何が足りないかを確かめる。
2. サーバ → `bash scripts/setup-dev.sh` → サーバの検査、画面 → `bash scripts/setup-dev.sh` → 画面の検査、の順に作る。
3. 最後に `bash scripts/verify.sh scaffold` を流す。

## 禁止

- 設計ID の宣言を書くこと（後続のループの分母を先に減らしてしまう）
- `spec-ids.toml`・`trace.toml`・`scripts/verify.sh`・`scripts/setup-dev.sh`・設計書・要求仕様書の変更
- 検査を通すための空の試験以外の細工（`--passWithNoTests` で試験 0 件を通す、など）
- `git push`・`main` への切り替え・フックの迂回。秘密情報・利用者の名前を書くこと

## 報告

作ったもの・流した検査と結果・固定した依存の版・自分で決めたこと（`docs/decisions/ASSUMPTIONS.md` に足す）・確かめていないこと。
