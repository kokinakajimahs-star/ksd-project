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
