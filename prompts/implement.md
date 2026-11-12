# タスク: 設計書の設計IDを、スコープ（種別×群）ごとに実装する

あなたは PMT（WBS・工数・進捗管理システム）の実装を行う。

## 正とするもの

- `docs/pmt-design.md`（設計書）が**唯一の正**である。要求の背景は `docs/pmt-requirements.md`（要求仕様書）。
- 実装と設計書が食い違う場合、設計書が正。**設計書・要求仕様書を変更してはならない。**
  記載どおりに実装できない・設計書の中で矛盾している場合は、変更を加えずに `docs/instruction-change-requests.md` へ
  Change Request を起票し、その旨を報告する。
- プロジェクトの規約は `CLAUDE.md` にある。最初に読むこと。

## このタスクの範囲

このループの検証器は `bash scripts/verify.sh <scope>`（`<scope>` は `db:PRJ`・`rule:SCH`・`api:WBS`・`ui:SCH` のような
「種別:群」）である。**検証器の出力（feedback）に並ぶ未実装の設計IDが、このタスクの範囲である。**
出力が無い最初の反復では、まず自分で `bash scripts/verify.sh <scope>` を流して範囲を確かめる
（スコープの名前は `kloop.toml` の該当タスクの `verify` に書いてある）。

範囲の外の設計IDを実装しない。ただし、範囲の ID を動かすのに要る最小の土台（ルータの登録・サービス・スキーマ・
試験の fixture）は作ってよい。

## 種別ごとの「実装した」の意味（設計書 2.3・13.3）

| 種別 | 読む章 | 作るもの | 検証器が見る形 |
|---|---|---|---|
| `db` | 4.1・4.2・3 | `backend/app/models/<群>.py` に SQLAlchemy 2 のモデル（4.1 の列・一意制約・外部キー）。Alembic の移行を `backend/alembic/versions/` に足す | `__tablename__ = "<テーブル名>"`。クラスの直前の行に `# DB-…` |
| `rule` | 5.1 と 5 章の該当節（5.2〜5.10）・要求仕様書 7 章の BR | `backend/app/domain/<5.1 の実装先>` に純粋な関数・データクラス。`backend/tests/domain/test_<ファイル>.py` に境界値の試験 | 関数・クラスの直前の行頭 `# RULE-… 名称`。試験の docstring に同じ ID |
| `api` | 6.1・6.2・6.3・9 | `backend/app/api/<群>.py` のルート（6.2 のメソッドとパスのとおり）・`services/`・`schemas/`。`backend/tests/api/test_<群>.py` に正常系と、403・409・422 のうち該当するもの | docstring の先頭 `"""API-… 名称"""` |
| `ui` | 7.1・7.2・7.3・7.4 | `frontend/src/pages/<群>/` の画面。`frontend/src/App.tsx` にルート。`*.test.tsx` に描画と主要な操作の試験 | 最上位の要素に `data-screen-id="UI-…"`。7.1 のルートの文字列 |

- **ID が現れていることと、実装が設計どおりであることは別である。** 検証器は前者しか見ない。
  後者は独立レビューが見る。ID だけ書いて中身が空・`pass`・`TODO` の実装をしない。
- ID を書くのは宣言のときだけ。参照するだけのコメントで ID を行頭に書かない（`# RULE-PRG-002 を参照` は書かない）。
- 計算ルールは設計書 5 章の式・表をそのまま試験にする。5.6 の試験の基準（稲妻チャート.txt §6 の例）・5.2 の WBS ID の例は必ず試験に入れる。
- ドメイン層は純粋に保つ（`date.today()`・乱数・DB・HTTP に触れない。今日の日付は引数）。
- API の権限は 9 章の表と RULE-AUTH-001 を通す（サーバ側で必ず判定する）。書き込みは変更履歴に残る（infra のイベント）。

## 進め方

1. `CLAUDE.md` を読み、`bash scripts/verify.sh <scope>` を流して未実装の ID と失敗した検査を確かめる。
2. 設計書の該当章**だけ**を読む（全文を読み込まない）。依存の向き（設計書 1.3）を確かめる。
3. 既にある実装（同じ種別の別の群）のパターンに合わせる。無ければ設計書 2.1 の置き場に作る。
4. 依存の少ない ID から、1 件ずつ「実装 → 試験 → 最も狭い検査」を回す：
   `.venv/bin/python -m pytest -q backend/tests/domain/test_x.py` / `npm --prefix frontend test -- <ファイル>`
5. スコープの検証 `bash scripts/verify.sh <scope>` を流し、未実装の数が減ったことを確かめる。
6. 依存を足したら版を固定し（`backend/requirements.lock`・`frontend/package-lock.json`）、`bash scripts/setup-dev.sh` を流す。

1 回の反復で全件を終える必要は無い。**進んだ分が検証器で測れる状態**（試験が通る・型が通る）で反復を終えること。
壊れた途中の状態で終えると、次の反復は直すことから始まる。

## 計算資源の使い方

このループは利用者の仮想 PC（WSL2）の上で、別の開発セッションと同時に動く。2026-11-11 に、試験の同時実行が重なった最中に
VM ごと落ちた（`docs/decisions/ASSUMPTIONS.md` の A-24）。次を守る。

- 試験・検証は**一度に 1 本、前景で**流す。`bash scripts/verify.sh …` を裏（`&`・`nohup`・出力をファイルへ回して `sleep` で待つ）で
  流しながら、別の試験を流さない。全体の試験は 2 分を超えるので、Bash の `timeout` を延ばして（最大 600000 ms）前景で流す
- 開発中は最も狭い検査（試験ファイル 1 本・`-k`）を流す。スコープの検証 `bash scripts/verify.sh <scope>` は反復の終わりに 1 回でよい
  （反復の後に kloop が同じ検証器を流す）
- 並列化を足さない（`pytest -n`・pytest-xdist の導入・vitest の `--maxWorkers` を上げる・`frontend/vite.config.ts` の `maxWorkers` を上げる）
- 常駐するものを起動しない（`vite`・`npm run dev`・`uvicorn --reload`・`run` の無い `vitest`・watch のたぐい）

## 完了の定義

- `bash scripts/verify.sh <scope>` が exit 0（範囲の未実装 0 件・試験・型検査・DB の移行・要求→設計の写像が通る）
- 試験を削除・skip・弱体化していない。型検査・lint の設定を緩めていない
- 新しい依存は版を固定している

## 未確定事項

確認待ちで止まらない。抽象化・外部設定化・未指定なら fail fast・`docs/open-questions.md` へ記録、の 4 点で進める。
自分で決めたことは `docs/decisions/ASSUMPTIONS.md` に 1 項目足す。本番値を独自に確定しない。

## 禁止

- 設計書・要求仕様書・`spec-ids.toml`・`trace.toml`・`scripts/verify.sh`・`scripts/setup-dev.sh`・`graphs/group-needs.toml` の変更
- 試験の削除・skip・アサーションの緩和、検証を通すためのハードコード・スタブ
- `git push`・PR 作成・デプロイ・`main` への切り替えやマージ・フックの迂回
- 秘密情報・利用者の名前を書くこと

## 報告

反復の最後に、次を短く書く：

- 実装した設計ID と、まだ残っている設計ID
- 流した検査とその結果（流していない検査も書く）
- 自分で決めたこと（ASSUMPTIONS.md の項目）・起票した Change Request
- **この反復で確かめていないこと**（検証器が見ない、設計どおりかどうかの点）
