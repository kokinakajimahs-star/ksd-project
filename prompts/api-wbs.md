# タスク: APIの実装（WBS グループ・23 件）

<!--
プロンプト共通部。`generate_task_prompts.py` が各タスクプロンプトの先頭へ差し込む。
**このファイルは生成対象外なので、プロジェクトの文脈に合わせて編集してよい。**
-->

あなたは PMT（WBS・工数・進捗管理システム）の実装を行う。

## 正とするもの

`docs/pmt-design.md`（以下「設計書」）が唯一の正である。要求の背景は `docs/pmt-requirements.md`（要求仕様書）にある。
設計書の記載と実装が食い違う場合、**設計書が正**。

**設計書・要求仕様書を変更してはならない。** 記載どおりに実装できない場合は、変更を加えずに
`docs/instruction-change-requests.md` へ Change Request を起票して報告すること。

## 不変条件（INVARIANTS）

次のいずれかによって検証を通過させてはならない。

- 設計書・要求仕様書・`spec-ids.toml`・`trace.toml`・`scripts/verify.sh`・`scripts/setup-dev.sh`・
  `scripts/extract_spec_ids.py` の変更
- テストの削除・スキップ・無効化、アサーションの弱体化
- 設計書が定める基準値・しきい値（要求仕様書 7 章の業務ルール・設計書 4.2 の既定値）の改変
- ID だけを書いて中身が空の実装（検証器が実装済みと誤認する状態を作らない）

これらが必要だと判断した場合は、実施せずに理由を報告し停止すること。

## 未確定事項の扱い

**確認待ちで止まらない。** 次の 4 点セットで実装を完了させる。

```text
1. 抽象化する        2. 外部設定化する
3. 未指定なら fail fast   4. docs/open-questions.md へ Status 付きで記録する
```

ただし**本番値を独自に確定してはならない**。判断は `docs/decisions/ASSUMPTIONS.md` に残す。

## 技術スタック（設計書 1.1・2.1）

- サーバ：Python 3.12・FastAPI・SQLAlchemy 2・Alembic・Pydantic 2・openpyxl・pytest。リポジトリの根の `.venv`
- DB：開発・試験は SQLite、本番は PostgreSQL（接続は環境変数 `PMT_DATABASE_URL`）
- 画面：React 18・TypeScript 5・Vite 5・React Router 6・Vitest・Testing Library
- ガント・イナズマ線：自前の SVG（商用の部品を使わない）
- 層：api → services → domain（純粋）/ models / infra。ドメイン層は DB・HTTP・時刻・乱数に触れない

## 禁止

- `git push` / PR 作成 / デプロイ / 実データへの操作
- `main` ブランチへの切り替え・コミット・マージ（作業は今のブランチで行う）
- 秘密情報をコード・設定・ログへ書くこと。利用者の名前を書くこと
- 設計書の範囲（R1）を超えた機能追加

## このタスクのゴール

設計書 6.2 の API 23 件を実装する。

## 完了の定義

```bash
bash scripts/verify.sh api:WBS
```

が exit code 0 で終わること。この検証器は設計書の ID と実装を突合し、
**未実装 ID 数**を返す。この数が 0 になるまで反復する。

## 対象（23 件）

| ID | 内容 | パス |
|---|---|---|
| API-WBS-001 | WBSの木（集計値・改番待ち・検証の件数付き） | /api/v1/projects/{project_id}/wbs |
| API-WBS-002 | 要素を足す（採番する） | /api/v1/projects/{project_id}/wbs/elements |
| API-WBS-003 | 要素の名称・区分・責任者・自分の見積を変える | /api/v1/projects/{project_id}/wbs/elements/{element_id} |
| API-WBS-004 | 要素を移動する（IDは変えない） | /api/v1/projects/{project_id}/wbs/elements/{element_id}/move |
| API-WBS-005 | 要素を配下ごと消す | /api/v1/projects/{project_id}/wbs/elements/{element_id} |
| API-WBS-006 | 採番規則を変える | /api/v1/projects/{project_id}/wbs/numbering-rule |
| API-WBS-007 | 改番する（新旧の対応を返す） | /api/v1/projects/{project_id}/wbs/renumber |
| API-WBS-008 | WBS IDの新旧対応の一覧 | /api/v1/projects/{project_id}/wbs/code-history |
| API-WBS-009 | WBS辞書を読む | /api/v1/projects/{project_id}/wbs/elements/{element_id}/dictionary |
| API-WBS-010 | WBS辞書を書く | /api/v1/projects/{project_id}/wbs/elements/{element_id}/dictionary |
| API-WBS-011 | 責任分担表（`format=json` か `csv`） | /api/v1/projects/{project_id}/wbs/responsibility-matrix |
| API-WBS-012 | 100%ルールと完了条件の検査の結果 | /api/v1/projects/{project_id}/wbs/validation |
| API-WBS-013 | 要素とスコープ項目の紐付けを置き換える | /api/v1/projects/{project_id}/wbs/elements/{element_id}/scope-links |
| API-WBS-014 | レビューを始める | /api/v1/projects/{project_id}/wbs/reviews |
| API-WBS-015 | レビューと指摘を読む | /api/v1/projects/{project_id}/wbs/reviews/{review_id} |
| API-WBS-016 | 指摘を書く | /api/v1/projects/{project_id}/wbs/reviews/{review_id}/comments |
| API-WBS-017 | 指摘の状態・対応を更新する | /api/v1/projects/{project_id}/wbs/reviews/{review_id}/comments/{comment_id} |
| API-WBS-018 | WBSの承認を申請する（前提を確かめる） | /api/v1/projects/{project_id}/wbs/approval-requests |
| API-WBS-019 | 承認・差戻し（WBSと変更要求で共通） | /api/v1/approval-requests/{approval_id}/decision |
| API-WBS-020 | 自分が判断できる承認待ちの一覧 | /api/v1/approval-requests |
| API-WBS-021 | スコープベースラインの版の一覧 | /api/v1/projects/{project_id}/scope-baselines |
| API-WBS-022 | スコープベースラインの1版（要素と辞書） | /api/v1/projects/{project_id}/scope-baselines/{baseline_id} |
| API-WBS-023 | WBS IDによる横断照会 | /api/v1/projects/{project_id}/wbs-codes/{wbs_code}/trace |

## 進め方

1. まず `bash scripts/verify.sh api:WBS` を実行し、未実装 ID の一覧を確認する。
2. 設計書の該当章を読む（**該当箇所だけ**。全文を読み込まない）。
3. 未実装のうち依存の少ないものから実装する。
4. 再度検証器を実行し、未実装数が減ったことを確認する。
5. 1 反復で全部を終わらせようとしない。**着実に減らすことを優先する。**

## 報告

最後に次の 3 点だけを簡潔に報告すること。

1. 何を実装したか（ファイル単位）
2. 検証器の結果（未実装 ID がいくつ減ったか）
3. 次の反復への申し送り（未確認の仮説・保留した判断）

「完了した」という自己申告は合否に影響しない。合否は `bash scripts/verify.sh` が決める。
