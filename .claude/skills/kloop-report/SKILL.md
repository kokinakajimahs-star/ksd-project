---
name: kloop-report
description: 過去の kloop run（ループ／グラフ）の結果を読み、収束の推移・未達の項目・コスト・見ていない範囲を要約して報告する。「前回の run はどうだった」「収束したのか」「なぜ止まったのか」「kloop の結果をまとめて」のようなときに使う。LLM のクォータを消費しない。Summarize a past kloop run without consuming quota.
argument-hint: "[run_id]（省略時は最新）"
allowed-tools: Bash, Read, Glob, Grep
---

# kloop run を要約する

**このスキルは LLM を消費しない。** 既にある成果物を読むだけである。回し直さない。

## 手順

```bash
KLOOP="${KLOOP_BIN:-kloop}"   # PATH の kloop（ksd-ls の venv の入口）。KLOOP_BIN で実体を指定できる

$KLOOP status --last 5                 # 新しい順に一覧
$KLOOP report --run-id <run_id>        # 収束レポート（report.md も生成される）
$KLOOP metrics --last 20               # run 間の集計（成功率・収束反復数・コスト）
```

深掘りが要るときだけ、次を読む。

```text
.kloop/runs/<run_id>/run.json                       … 終了状態・設定スナップショット
.kloop/runs/<run_id>/metrics.jsonl                  … 1 行 1 反復（またはノード）
.kloop/runs/<run_id>/attempts.md                    … 試した手段の履歴
.kloop/runs/<run_id>/iterations/iter-000N/verify.log … 検証器の生ログ
.kloop/runs/<run_id>/iterations/iter-000N/prompt.md  … その反復に渡したプロンプト
.kloop/runs/<run_id>/nodes/<node>/                   … グラフのノード成果物
```

## 読み方

1. **終了状態を最初に見る。** `SUCCESS` 以外は「失敗」ではなく、それぞれ別の意味を持つ
2. **収束の推移を見る。** 右肩下がりで上限に当たったのか、横ばいだったのかで対処が違う
3. **`progress_source` を見る。** `worktree_fingerprint` なら、進捗は「ファイルが変わったか」
   でしか見ていない。品質の改善を意味しない
4. **`backend` を見る。** `mock` なら実機の Claude は呼ばれていない
5. **`degraded` を見る。** 検証器が `verify-result.json` を返していないなら、合否は exit code だけ
6. **graph run では、`loop` ノードの縮退も見る。** graph run 自身は検証結果を持たない。
   3 と 5 の事実は `nodes/<id>/loop/run.json`（`progress_source` / `final_verify.degraded`）にある。
   kloop 5.1.0 以降の `report.md` はこれをノード名付きで「このレポートが見ていないもの」へ出す
   （CR-042）。**古い run は `kloop report --run-id <id>` を打ち直せば出る**

## 報告するときの言い回し

| 書かない | 書く |
|---|---|
| 「修正が完了した」 | 「検証器は PASS した。検証していない範囲は下記のとおり」 |
| 「問題なし」 | 「この run が検証していないもの」を必ず添える |
| 「N 件のバグを直した」 | 「failures が N 件減った。個々の妥当性は人が判定していない」 |
| 「失敗した」 | 終了状態を名前で言う（`BLOCKED_NO_PROGRESS` と `BUDGET_EXCEEDED` は別物） |
| 「◯◯ 円かかった」 | 「推定コストは N USD。クライアント側の推定値であり課金の正ではない」 |

- レポートの「このレポートが見ていないもの」の節を**要約から落とさない**
- 上限で省略した件数、代理指標に落ちた判定、スキップされたノードを黙って落とさない
