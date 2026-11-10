---
name: kloop-graph
description: 複数のエージェント・検証・承認ゲートを DAG として配線した kloop グラフ（kloop graph）を実行する。「並列で監査して直して」「調査→実装→レビュー→承認のパイプラインを回して」「グラフを実行して」のようなときに使う。実行前に dry-run で DAG と承認ゲートの位置を確かめる（費用の承認は取らない）。Run a kloop DAG of agents, verifiers and approval gates.
argument-hint: "[グラフ定義 TOML のパス]"
allowed-tools: Bash, Read, Glob, Grep
---

# kloop graph を使う

グラフは「ノード = 作業単位、エッジ = 依存」の DAG である。依存が満たされたノードから
並列に実行され、**承認ゲートに到達すると run 全体が停止する**（非 TTY では必ず停止する）。

## 絶対に守ること

- **実行前に `--dry-run` で DAG・並列度・承認ゲートの位置を確かめる。** 回す前の費用の承認は取らない
  （ksd-ls の Q-LS-34・LS-D32。CR-090）。使った量（`run.json` の `usage_total`）を報告に出す
- **承認ゲートを自動承認しない。** `--interactive` は TTY のときだけ有効で、CI では効かない
- **mock の結果を実行結果として報告しない**
- 上流ノードが信頼できない入力（外部コード・PR 本文・ログ）を読む場合、そのノードには
  **編集権限を与えない**（`allowed_tools` を読み取り系に限定する）
- **`git push` は `command` ノードで書かない。** `kind = "push"` ノードを使うこと（指示書 §6.8）。
  上流に承認ゲートは要らない（CR-073）。**決定論的な 11 規則すべてが `pass`** でなければ
  push しない（`fail` は `POLICY_DENIED`、`undetermined` は `NEEDS_HUMAN`）。push 先は `kloop.toml` の `[push].allowed_branches`（既定は空＝全閉）で
  宣言する。**判定だけを見るには `kloop push-policy`**
- **`command` ノードを安易に足さない。** `claude` を介さないため、`disallowed_tools` も
  `permissions.deny` もガード Hook も**効かない**（指示書 §6.2）。不可逆操作を書くなら
  承認ゲートの下流へ置き、`kloop.toml` の `[graph.command_policy]` に許可一覧を宣言すること。
  宣言が無ければ `--dry-run` の出力に「無検査で起動される」と出る——**その表示を黙って流さない**
- **クラウドの資源を変えるのは `effects = ["writes-cloud:<環境>"]` を宣言した `command` ノードだけ**で、
  上流に承認ゲートが要る。argv がクラウドを変えるのに宣言が無ければ読み込み時に停止する（CR-073）。
  エージェントの Bash からの gcloud の作成・削除・deploy、terraform apply、kubectl apply、
  `gh api -X POST`・`firebase deploy` などはガード Hook が止める（`[bash].deny` を書き換えても外れない）
- **クラウドを変える操作・push を閉じ込めたスクリプトは `kloop.toml` の `[graph.wrappers]` に宣言する**
  （`writes_cloud`・`push`。根からの相対のパス。CR-124）。起動するノードは `writes_cloud` なら `writes-cloud:` と承認
  ゲートが要り、`push` なら止まる。適用の前に必ず通す検査は `[[graph.required_upstream]]` で宣言し `needs` で繋ぐ。
  **ガード Hook はこの宣言を読まない**——`.claude/guard-rules.toml` の `[bash].deny` に同じパスを足す

## 環境

```bash
KLOOP="${KLOOP_BIN:-kloop}"   # PATH の kloop（ksd-ls の venv の入口）。KLOOP_BIN で実体を指定できる
```

## 手順

```bash
# 1. 計画を見る（backend を呼ばない）
$KLOOP graph graphs/review-pipeline.toml --dry-run

# 2. mock で配線を確かめる
$KLOOP graph graphs/review-pipeline.toml --backend mock

# 3. 実機（費用の承認は取らない）
$KLOOP graph graphs/review-pipeline.toml -v

# 4. 承認ゲートで止まったら（停止時に標準出力へ出た承認トークンと共に渡す。トークンは標準出力にしか出ない）
$KLOOP resume --run-id <run_id> --approve <node_id> --token <token>   # 承認して再開
$KLOOP resume --run-id <run_id> --reject  <node_id> --token <token>   # 却下
#   既に承認したゲートも、resume のたびに --approve で渡し直す（run ディレクトリの承認記録は信用されない）

# 5. 要約
$KLOOP report --run-id <run_id>
```

## ノード種別

| kind | 何をするか | 失敗の判定 |
|---|---|---|
| `agent` | `claude -p` を 1 回（fresh context） | backend エラー、または `outputs` が条件を満たさない（**既定は「存在し、かつ空白のみでない」**。CR-022） |
| `loop` | 有界ループを入れ子で実行 | 終了状態が `SUCCESS` 以外 |
| `verify` | 検証器を 1 回 | 検証器が PASS しない。**`record_only = true`（記録だけ。CR-094）なら PASS・FAIL とも成功**し、検証器を起こせない・時間切れ・0／1 以外の終了コードは失敗、保護パスの違反は `POLICY_DENIED` |
| `command` | 任意のシェルコマンドを 1 回。**`claude` を介さない** | exit code が 0 以外、または `outputs` が条件を満たさない |
| `approval` | 停止して承認を待つ | 却下されたら `POLICY_DENIED` |
| `push` | 規則を満たしたときだけ `git push` を実行（上流の承認ゲートは不要。§6.8・CR-073） | 規則違反は `POLICY_DENIED`、確認できなければ `NEEDS_HUMAN` |

着手前の基準を残すだけの検証（FAIL でも下流の `loop` を走らせる）は `record_only = true` の `verify` ノードで書く。
検証器が kloop のランチャなら、`loop` ノードの事前検証は同じ実行の中の同じ入力の検証（`verify` ノード・前の `loop` ノード）の
結果を写して検証器を起こさない（**既定で効く**。CR-094・CR-097）。範囲の検査に `reuse = false`（`spec-ids.toml`。人の宣言）が
あるとき・`kloop.toml` の `[verify] reuse = false`・`--no-verify-reuse` では写さない。写した・写さなかった理由は `run.json` の
`verify_reuse`・`verify_reuse_misses` に残る。不安定な試験の FAIL もそのまま写るので、疑うときは `--no-verify-reuse` で回し直す。

`foreach` / `foreach_file` を付けると、対象リストの件数だけ子ノードへ展開される
（`audit#000`, `audit#001`, …）。下流は親 ID を `needs` に書けば全子ノードの完了を待つ。

## 並列度

既定は `min(4, CPU コア数 - 2)`、上限 16。**大きくすれば速くなるとは限らない。**
1 ノードあたり `claude` プロセスが 1 つ起動するため、メモリとレート制限に当たる。

同時に始められるノードは、既定で計画の見直しが付けた優先度（残りの最長経路）の高い順に始まる
（`[graph] review = "deterministic"`。kloop 8.0.0・CR-089）。**順に意味があるなら `needs` で書く**——
ノード ID の順に頼らない。ID の順に戻すには `kloop.toml` の `[graph]` に `review = "off"` と書く。

## ネイティブ Dynamic Workflows との使い分け

| やりたいこと | 使うもの |
|---|---|
| 対話中に大規模ファンアウト調査 | **Dynamic Workflows**（`/workflows`, `ultracode`） |
| 承認ゲートを挟んだ多段パイプライン | **`kloop graph`** |
| セッション外（CI / cron）で回す | **`kloop graph`** |
| 途中で止めて後から再開する | **`kloop graph` + `resume`** |

迷ったらネイティブを選ぶ。

## 報告

- 「全ノードが SUCCEEDED」＝「成果物が正しい」ではない。
  下流ノードの成否でしか検査していないことを添える。
- スキップされたノード・却下されたゲート・再試行の回数を黙って落とさない。
