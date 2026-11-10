# ASSUMPTIONS（環境構築時に行った判断）

設計書に明記が無かったため自ら決めた事項を記録する。
設計内容の変更ではなく、実装・検証を機械化するための補完である。

## 書式

```markdown
## A-xx: <選択の要約>

- **選択:**
- **理由:**
- **影響範囲:**
- **覆す場合:** （何を変える必要があるか）
- **限界:** （この判断で担保できないこと）
```

---

## A-01: 進捗指標は「未実装 ID 数」

- **選択:** 設計書の ID のうち実装に現れていないものの数を kloop の進捗指標にする
- **理由:** 設計書の規模が 1 セッションで終わらないため反復が前提になる。
  反復を機械的に回すには進捗を人手を介さず測る必要がある。
- **影響範囲:** `scripts/check_traceability.py`、`kloop.toml` の `progress_metrics`
- **限界:** **ID の存在は実装の正しさを意味しない。** 独立レビューで補う。

## A-02: 設計ID は 4 種別（db・rule・api・ui）× 群で数える

- **選択:** 設計書 4.1・5.1・6.2・7.1 の表の 1 行を 1 件とし（187 件）、`<種別>-<群>-<3桁>` の ID の群でスコープを割る（37 スコープ）
- **理由:** `ksdx plan` は ID 文字列の群（`group_from`）でしかスコープを割れない。1 スコープ 1〜23 件にして、1 本のループで収束できる大きさにする
- **影響範囲:** `spec-ids.toml`・`trace.toml`・`graphs/implement.toml`・`kloop.toml` の `[tasks.implement-*]`
- **覆す場合:** 設計書の ID と 11 章の対応表の列、`spec-ids.toml` の `[[kinds]]` を同時に変え、`ksdx plan` で生成し直す
- **限界:** ID の存在は実装の正しさを意味しない。設計書 8 章（非機能）・9 章（権限の表の全組合せ）には ID が無く、検証器は見ていない

## A-03: 証拠の形（evidence）

- **選択:** db は `regex`（`__tablename__ = "<テーブル名>"`。ID ではなく実体）、rule は `declaration_and_test`、api は `declaration`、ui は `declaration` ＋ `route_key`
- **理由:** 既定の `id` は参照コメントだけで実装済みになる。テーブルは名前が実体そのもの、ルールは試験が無いと式の正しさを何も確かめないため
- **影響範囲:** `spec-ids.toml`
- **限界:** API の宣言は試験を求めていない（API の試験は `backend-tests` の検査で流れるが、ID ごとの試験の有無は数えない）

## A-04: 実装の順（群の順）

- **選択:** `graphs/group-needs.toml` で テーブル → ルール → API → 画面 と、群をまたぐ依存（外部キー・計算の部品・認証）を宣言した。構成づくり（scaffold）はグラフの外で先に `kloop run scaffold` で回す
- **理由:** `ksdx plan graph` は `[[extra_tasks]]` をノードにしない（グラフの中で scaffold を先に置けない）。待つスコープが収束しないと下流は SKIPPED になるので、辺は必要なものだけにした
- **影響範囲:** `graphs/implement.toml`（生成物）
- **覆す場合:** `graphs/group-needs.toml` を直し、`ksdx plan graph … --group-needs graphs/group-needs.toml --out graphs/implement.toml --force` で生成し直す
- **限界:** 書くノードは 1 本ずつ走る（`[graph.worktrees]` を使っていない。作業ツリーを分けると `.venv`・`node_modules` が無く検査が落ちるため）

## A-05: グラフの実装ループはコミットしない

- **選択:** 実装グラフ（`graphs/implement.toml`）はコミットせず、最後の `review` の `findings.md` を読んでから作業者が `dev` へコミットする
- **理由:** `ksdx plan graph` の生成物の形（コミットの前の承認ゲートを置かない。CR-I-034）。`main` への反映は関門とエスカレーションで行う
- **限界:** 途中のスコープの成果は run が終わるまで未コミットで作業ツリーに積まれる

## A-06: 実機のループはサンドボックスの外で起動する

- **選択:** `[state] graph = "ksdls-loop"`（ksd-ls のグラフ層へ記録）。実機の `kloop run`・`kloop graph` は利用者の端末（サンドボックスの外）で起動する
- **理由:** Claude Code のサンドボックスの中からは、グラフ DB の接続先と Claude の資格が読めない（2026-11-10 実測：グラフの doctor が TargetError、`kloop doctor` が「認証・課金先が見つからない」）
- **限界:** サンドボックスの中で確かめたのは dry-run・mock の配線と検証器だけである


## A-07: 構成づくり（scaffold）で決めたこと

- **選択:**
  - サーバの依存は 2026-11-10 時点の版で固定した（fastapi 0.162.2・SQLAlchemy 2.0.57・alembic 1.20.1・pydantic 2.14.3・pytest 8.4.2 ほか。`backend/requirements.lock`）。設計書 1.1 の下限を満たす
  - PostgreSQL（本番）のドライバは psycopg 3（`psycopg[binary]` 3.3.8）。`PMT_DATABASE_URL` は `postgresql://…` と書けば `postgresql+psycopg://…` として扱う（`app.infra.db.normalize_database_url`）
  - 画面の依存は設計書 1.1 の主版（React 18・React Router 6・Vite 5・Vitest 2・TypeScript 5）の中の最新で固定した（react-router-dom 6.30.6・vite 5.4.21・vitest 2.1.9。`frontend/package.json` に `^` を付けない。`package-lock.json` をコミットする）。主版の中では直らない勧告は CR-001 に起票した
  - フレームワークの `HTTPException` の文言は状態コードで決まる日本語にし、`detail` は `details` に入れる。文言を指定するエラーは全て `AppError` で投げる。想定外の例外も 500 `INTERNAL_ERROR` の形で返す（中身は応答に出さない）
  - SQLite の移行は外部キーの強制を外して流し、終わったら `PRAGMA foreign_key_check` で違反が無いことを確かめる（表の作り直しの DROP が子の行を連鎖で消すため。`create_migration_engine`）
  - `app.main` の `app` は最初に参照されたときに作る（import しただけで環境変数を読まない。設計書 2.2 の `uvicorn app.main:app` はそのまま使える）
  - API 層はセッションの依存を `app.services` から取る（1.3：API はサービスとスキーマにだけ依存する）
  - `build` は `tsc --noEmit -p tsconfig.json && vite build` にした（`prompts/scaffold.md` の `tsc -b` ではない。tsconfig を 1 本にして `noEmit` で型だけ見るため）
  - 業務上のエラーは `backend/app/errors.py` の `AppError`（状態コード・コード・文言・details）。サービス層と API 層の両方が使うので、どちらの層にも置かない
  - 入力の誤り（FastAPI の検証エラー）は 400 `VALIDATION_ERROR` に直す（設計書 6.1。FastAPI の既定の 422 は業務ルールの違反に空けておく）
  - 設定は `pydantic-settings` で環境変数 `PMT_*` だけから読み、`.env` は読まない。最初の管理者のログイン名・パスワードは既定値を持たない
  - 死活の口 `GET /api/v1/health`（設計ID を持たない。DB へ `SELECT 1` を流す）
- **理由:** 設計書 2.1・2.2・2.4・6.1・8 章。後続のスコープが同じ形で足せるようにする
- **限界:** starlette 1.10 が「httpx を TestClient に使うのは非推奨（httpx2 へ）」と警告を出す。今は動く。上げるときは `backend/requirements.lock` を作り直して試験を流す
