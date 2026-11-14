# ブランチの規則（この導入先）

`ksdls setup project` が置いた雛形（元は ksd-ls の `docs/branch-rules.md` §8・§8.1「導入先」）。この導入先に合わせて直してよい。
`ksdls setup project` は上書きしない（ksd-ls の雛形が変われば `docs/branch-rules.md.ksdls-new` を置くので、差分を見て取り込む）。

## 1. ブランチ

| ブランチ | 役割 | 入れるとき |
|---|---|---|
| `main` | 本番 | `ksdls escalate --root <導入先>` だけ（`dev` のコミットを `--no-ff` でマージする）。関門（§2）を通ったコミットに限る |
| `dev` | 開発の統合 | 作業のブランチを合流する |
| `wt/<task_id>` | 1 つの作業 | 作業ツリー `../<リポジトリ>.wt/<task_id>` で書く |

- `main` へ直接コミットしない（フックが拒む）。`dev` が無ければ `git branch dev main` で作る
- 作業ツリー：`git worktree add ../<リポジトリ>.wt/<task_id> -b wt/<task_id> dev`（リポジトリの外に置く）。終われば `dev` へ合流し、`git worktree remove ../<リポジトリ>.wt/<task_id>`
- `<task_id>` は作業の ID（kloop の計画のノードなら `<run>/<node_id>` の `/` を `-` に置き換える）
- ほかに長く残るブランチは作らない（調べるだけの一時のブランチは `tmp/<名前>`。`dev` へは作業にしてから `wt/` で入れる）

## 2. 関門（`ksdls test gate --verify`）

- `ksdls test gate --verify [<rev>] --root <導入先>`：測るコミット（既定 `HEAD`）を `../<リポジトリ>.wt/gate-<sha 12 桁>` に取り出し、そのコミットの `ksdls.toml` の `[gate] prepare` を順に流してから `kloop -C <取り出した木> verify all` を流す。**合否は kloop の判定**（`PASS` で終了コード 0 のときだけ合格）
- 通れば `<git の共通の置き場>/ksdls/full-gate/<sha>` に記録する（2 行目 `mode verify`）。`main` へのマージ（§3）はこの記録を要る
- 分母（流す検査）は `spec-ids.toml` の `[[checks]]`。変えるのは人
- **グラフ（ksd-ls の試験の層）に載せる**：git の記録はグラフに載らない。載せるのは資格のある人の端末か ksd-ls の取りまとめ役で、`ksdls test gate --record-from <sha> --root <導入先>`（記録の後から）か `ksdls test gate --verify --record --root <導入先>`（流すときに）。project の ID は根の `kloop.toml` の `[project] id`。エージェントは書かない（guard が止める）。`ksdls escalate` の報告の関門の欄に、載せたかが出る（止めない）
- 準備の宣言（導入先の根の `ksdls.toml`。コミットに入れる。無ければ準備なし）：

  ```toml
  [gate]
  prepare = [["npm", "ci"]]   # argv の配列の配列（シェルを通さない）。0 でなければ不合格
  timeout_seconds = 3600       # 準備と kloop verify を合わせた上限（既定 3600）
  ```

## 3. 本番へのエスカレーション（`ksdls escalate`）

- `ksdls escalate --root <導入先> [--dry-run] [<dev のコミット>]`：引数が無ければ `main..dev` の first-parent の上で関門の記録がある最新のコミットを選び、`KSDLS_ESCALATE=1` で `main` へ `--no-ff` でマージする（コミットの末尾に `KSDLS-Escalation:` の行）。報告は `.ksdls/escalations/` に残る。`--dry-run` は報告だけ
- 止まるのは、関門の記録が無い・`main` の先に無い・作業ツリーが汚れている・宣言したクラウドの置き場（`.ksdls/branches.toml` の `cloud_paths`）に触る（利用者の承認が要る）とき

## 3.2 リリースの自動化（`kloop release`）

- §2 の関門・グラフへの記録・§3 のエスカレーション・`dev` と `main` の push は、kloop の実行器が決まった順で流す：前提 → 関門 → 試験の層への記録 → 台帳の取り込み直し（この 2 つは `[state] graph` の宣言があるとき）→ escalate → push `dev` → push `main`。`main` の push に人の確認は置かない。止まるのは、関門の不合格・§3 の止め（`cloud_paths` は利用者の承認が要る）・push の規則・push 先が受け手を入れた時と違うとき
- **エージェント**は `kloop release request [--note <文>]` で頼むだけ（依頼を 1 つ置く。何も流さない）。人に関門・escalate・push のコマンドを打たせない。結果は `kloop release status` と `.kloop/release/<run_id>/report.md`
- **人**が 1 回だけ受け手（systemd のユーザーの単位）を入れる：`kloop -C <導入先> release install --dry-run` で中身を見てから `kloop -C <導入先> release install`。受け手は依頼が置かれると sandbox の外で `kloop release run` を流す。手で流すなら人の端末で `kloop release run`。外すのは `kloop release uninstall`
- 受け手は、リポジトリに触る子（関門の検査・準備・git のフック・push）を bubblewrap の覆いの中で流し、sandbox と同じ資格の置き場（`~/.ssh`・`~/.config/ksdls`・`~/.config/gcloud` など）を隠す。Linux だけで、`bwrap` が無ければ止まる。push は HTTPS と git の資格の置き場を使う（ssh のリモートは覆いの中で鍵を読めない）
- `dev` と `main` は `[push].allowed_branches` に依らず、push の 11 規則で測る（`allowed_branches` に `main` を足さない）。`main` は escalate のマージ（`KSDLS-Escalation:` の行のあるコミット）が先頭のときだけ出す
- エージェントは `kloop release run|install|uninstall` を打てない（guard が止める）
- **台帳の取り込み直し**：受け手は記録の後に `ksdls task import --root <導入先> --refresh --rev <関門の sha>` を覆いの外で流し、関門で測ったコミットの台帳（未決・仮定・変更要求・`trace*.toml`）をグラフへ揃える（台帳の正本はファイルのまま）。落ちても release は止まらず、報告に注意が出る
- **push の判定の台帳**（`kloop-state/push-policy-log/`）は版管理下に置く記録で、release の push の後に書かれる。release はコミットしないので、報告と `kloop release status` の知らせに従って `dev` へコミットする（次の release で `main` へ入る。`.gitignore` しない）

## 3.1 最初の push とリモートの登録（sandbox の外で人が 1 回）

- 空のリモートへ導入した直後は、**リモートの登録と、`main`・`dev` の最初の push を、sandbox の外の端末で人が 1 回だけ行う**。Claude Code の sandbox は `.git/config` を守り（中からの `git remote add` は落ちる）、guard は生の `git push` を止め、kloop の push ノードは新しいリモートのブランチを作らない（kloop の CR-132）
- 手順：`git remote add origin <URL>` → `kloop push-policy --branch main`（`fast_forward_required` 以外の内容の規則が通るのを見る。判定は undetermined か fail で、pass にはならない）→ `git push -u origin main`（空のリポジトリでは最初に push したブランチが既定のブランチになる）→ 同じく `dev`
- それからは `dev` と `main` を §3.2 の `kloop release` が出す（`dev` だけなら kloop の push ノードでもよい。`[push].allowed_branches` に `dev`）。`main` は §3 のエスカレーションで入れ、`pre-push`（§4）はエスカレーションのマージだけを `main` へ送る。`main` を push ノードで出すなら、そのマージのコミットを出す形にする
- 詳しくは kloop の `docs/adoption-guide.md`「最初の push とリモートの登録」

## 4. フック（`.githooks/`）

- 効かせるのは利用者：`git config core.hooksPath .githooks`（`ksdls setup project` は git の設定を変えない）。フックはコミットに入れる（作業ツリーにも効く）
- `pre-commit`：`main` への直接のコミットを拒む
- `pre-merge-commit`：`main` へのマージは `KSDLS_ESCALATE=1` と関門の記録（§2）があるときだけ
- `pre-push`：`main` へはエスカレーションのマージだけを送る。早送りでない更新とブランチの削除を拒む。送るコミットの数を出す
- フックは手元の守りで、迂回できる。**`--no-verify` を使わない**。リモートのブランチの保護は外部のサービスの設定なので、利用者が決めて設定する

## 5. コミットの印

コミットのメッセージの末尾に `KSDLS-Task: <task_id>` を付ける（kloop がコミットするときは `KSDLS-Run: <run_id>` も付く）。
