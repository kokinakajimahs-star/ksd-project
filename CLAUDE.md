# ksd-project — PMT（WBS・工数・進捗管理システム）

WBS → 見積 → ガント → イナズマ線 → 工数（EAC）→ 変更管理 を1つのデータで扱うプロジェクト管理ツールを自社開発する。
開発は ksd-ls（kloop・ksdx）の有界ループで進める。

## 正とするもの

| 文書 | 役割 |
|---|---|
| `docs/pmt-requirements.md` | 要求仕様書。R1 は 5 章の 64 件（要求層。`trace.toml` が数える） |
| `docs/pmt-design.md` | 設計書。**実装の唯一の正**。設計ID 193 件（`spec-ids.toml` が数える）と要求→設計の対応表（11 章） |
| `WBS.md` ほか根の Markdown・txt | 要求仕様書の入力資料（出所。書き換えない） |

実装と設計書が食い違う場合、設計書が正。**設計書・要求仕様書を変更してはならない。** 記載どおりに実装できない場合は、
変更を加えずに `docs/instruction-change-requests.md` へ Change Request を起票して報告する。
両文書・`spec-ids.toml`・`trace.toml`・`scripts/verify.sh`・`scripts/setup-dev.sh` は kloop の保護パスである。

## 検証コマンド（source of truth）

```bash
bash scripts/setup-dev.sh          # 開発環境（.venv と frontend/node_modules）。何度流しても同じ結果になる
bash scripts/verify.sh all         # 全体（これが唯一の正。kloop verify）
bash scripts/verify.sh <scope>     # スコープ限定：scaffold / db / rule / api / ui / api:PRJ のような 種別:群
kloop doctor                       # 環境診断
ksdx trace verify                  # 要求→設計の写像（要求 64 / GAP 0 / ORPHAN 0）
```

個別の検査（`spec-ids.toml` の `[[checks]]` と同じもの）:

- サーバの試験: `.venv/bin/python -m pytest -q backend/tests`
- DB の移行: `PMT_DATABASE_URL=sqlite:///$TMPDIR/x.db .venv/bin/python -m alembic -c backend/alembic.ini upgrade head`
- 画面の型検査: `npm --prefix frontend run typecheck`
- 画面の試験: `npm --prefix frontend test`

変更したコードに関係する検査が全て通るまで、その変更は完了していない。

## 実装規約（検証器が機械的に確認する。設計書 2.3・13.3）

| 種別 | 実装に現れるべき形 | 置き場 |
|---|---|---|
| テーブル `DB-…` | `__tablename__ = "<4.1 のテーブル名>"`（クラスの直前の行に `# DB-…`） | `backend/app/models/` |
| ルール `RULE-…` | 関数・クラスの直前の行頭コメント `# RULE-… 名称` ＋ 試験に同じ ID | `backend/app/domain/<5.1 の実装先>`・`backend/tests/domain/` |
| API `API-…` | ルート関数の docstring の先頭 `"""API-… 名称"""` | `backend/app/api/` |
| 画面 `UI-…` | 最上位の要素に `data-screen-id="UI-…"` ＋ `frontend/src/App.tsx` に 7.1 のルート | `frontend/src/pages/` |

- ID を書くのは**宣言**のときだけ。参照するだけのコメントで ID を行頭に書かない。
- **ID が現れていることと、実装が設計どおりであることは別である**（設計書 13.6）。ID だけ書いて中身が空の実装をしない。
- ドメイン層（`backend/app/domain/`）は純粋：DB・HTTP・`date.today()`・乱数に触れない。今日の日付は引数で受ける。

## ブランチ

`docs/branch-rules.md` に従う。作業は `dev`（または `wt/<task_id>`）。**`main` へ直接コミットしない。**
`main` へは関門（`ksdls test gate --verify`）を通した後に `ksdls escalate` で入れる。流すのは次の節の `kloop release` で、
エージェントは関門・escalate・`git push` を自分の Bash で打たない。
リモートは GitHub Enterprise の非公開リポジトリ `origin`（URL は `git remote -v` で見る。利用者の名前を含むので文書・コミットに写さない）。

## 本番へ出す（ksd-ls の導入先）

- 本番へ出すとき（関門 → escalate → `dev` と `main` の push。デプロイとクラウドの構成変更は含まない）は `kloop release request` で頼む。流すのは sandbox の外の受け手で、手順は kloop の中で決まっている
- 人に関門・escalate・push のコマンドを打たせない（エージェントの `kloop release run`・`install`・`uninstall` は guard が止める）
- 受け手が入っていなければ（`kloop release status`）、人に `kloop release install` を 1 回頼む
- 結果は `kloop release status` と `.kloop/release/<run_id>/report.md` で見る。止まったら理由を直してから頼み直す
- 関門が流れている間（15〜20 分）は、主の作業ツリーで編集・コミットをしない（escalate は作業ツリーが汚れていると止まる）。
  デプロイ（`deploy/deploy.sh`）は今までどおり利用者が流す

## エンジニアリング制約

- 最小の一貫した変更を選ぶ。設計書の範囲（R1）を超えた機能を足さない。
- 依存を足すときは版を固定する（`backend/requirements.lock`・`frontend/package-lock.json`）。足したら `bash scripts/setup-dev.sh`。
- 新しい抽象を導入する前に、既存のパターンを調べる。

## 未確定事項の扱い

**確認待ちで止まらない。** 抽象化する・外部設定化する・未指定なら fail fast・`docs/open-questions.md` へ Status 付きで記録する。
判断は `docs/decisions/ASSUMPTIONS.md` に残す。本番値を独自に確定しない。

## ループ方針

検証が失敗したとき:

1. `bash scripts/verify.sh <scope>` の出力（未実装 ID と失敗した検査）を全文読む
2. 設計書の該当章**だけ**を読む（4.1・5 章の該当節・6.2・7.1）
3. 依存の少ないものから、最小の一貫した変更を行う
4. 最も狭い検査を流し、次にスコープの検証を流す

2 回連続で測定可能な改善が無い場合、同じ方針を繰り返さない。阻害要因を診断し、異なる方針を選ぶ。
上限内で収束しない場合は、停止して 現在の状態 / 満たせていない基準 / 試した方針 / 根拠 / 人に求める判断 を報告する。

## 検証器の弱体化の禁止

次のいずれかによって検証を通過させてはならない。必要だと判断したら、実施せずに理由を報告して停止する。

- 設計書・要求仕様書・`spec-ids.toml`・`trace.toml`・`scripts/verify.sh` の変更
- テストの削除・スキップ・無効化、アサーションの弱体化
- 型検査・lint の設定を緩めること、しきい値の引き下げ
- 本番の振る舞いをスタブ・ハードコードで置き換えること

## 安全

- 秘密情報（`~/.config/specarch/`・`~/.config/ksdls/`・`~/.config/gcloud/` の中身、`.env`）を読まない・表示しない・渡さない
- 利用者の名前を文書・コード・コミットに書かない（「利用者」と書く）
- `git push --force`・フックの迂回をしない。本番インフラ・クラウドの資源を変えない

## 報告の言い回し

| 書かない | 書く |
|---|---|
| 「実装が完了した」 | 「未実装 ID が N 件から M 件になった。検証していない範囲は別途記載した」 |
| 「問題なし」 | 「この検証が見ていないもの」を必ず添える |
| 「失敗した」 | 終了状態を名前で言う（`BLOCKED_NO_PROGRESS` と `BUDGET_EXCEEDED` は別物） |

未実行のチェック（`skipped_checks`）を黙って落とさない。
