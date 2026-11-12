#!/usr/bin/env bash
# 着手前の基準を残すためだけに検証器を回す。**合否には使わない。**
#
# `ksdx plan graph` が生成するグラフの `verify-before` ノードから呼ばれる。
# 対象プロジェクトに無ければ `ksdx plan graph` がこの雛形を書き出す
# （kloop の `templates/scripts/kloop-commit.sh` と同じ扱い。**既にあれば書き換えない**）。
#
# ## なぜ `bash -c "... || true"` ではなくスクリプトなのか
#
# `[graph.command_policy]` を宣言すると、`-c` を含む argv は**グラフの読み込みで拒否される**
# （kloop 指示書 §6.2 / kloop CR-014。ENVIRONMENT_ERROR / 終了コード 7。
# `on_fail` は効かず、run は 1 ノードも実行しない。2026-08-16 に kloop 1.3.0 で実測）。
# argv からは `-c` に渡した文字列の中身を判定できないためである。
# このスクリプトを許可するなら、対象プロジェクトの kloop.toml へ次を書く:
#
#   [graph.command_policy]
#   allow = [["bash", "scripts/verify-baseline.sh"]]
#
# **許可することは、このスクリプトの中身を信頼すると決めることである。**
# kloop も ksd-x も、許可したスクリプトの中身は見ない。
#
# ## なぜ FAIL を 0 にするのか
#
# このグラフは**未実装 ID を実装するために在る**ので、着手前の検証は必ず FAIL する。
# 一方 kloop の `needs` は依存が **SUCCEEDED** であることを要求し、
# 満たされなければ下流を SKIPPED にする（kloop `graph.py`）。`on_fail = "continue"` を足しても直らない。
# 完了だけを待つ任意依存 `needs_optional`（kloop 1.19.0 から）で実装ループを走らせても、
# **途中のノードが 1 つでも FAILED なら run は `BLOCKED_NO_PROGRESS` で終わる**（kloop の `_schedule` の終わり）
# ——実装ループがすべて収束しても run は成功にならない（ksd-x の CR-I-037 と同じ判断）。
# **だから FAIL を基準として受け取る場所がどこかに要る。**
#
# ## 0・1 の他は返す（CR-I-039。2026-10-21 まではすべての終了コードを 0 にしていた）
#
# 0（PASS）と 1（FAIL）だけを基準として 0 にする。ほかの終了コード（kloop が無い 7・検証器が無い 127 など）は
# **検証器を起こせていない**——基準を取れていないので、そのまま返して止める（実装ループとレビューへ進ませない）。
# kloop の `scripts/verify-before.sh` と同じ形である（ksd-x の self-review は CR-I-041 から記録だけの verify ノードで、ラッパを使わない）。
# 検証器の契約は「0 = 合格、非 0 = 不合格」（kloop ADR-007 §5.2）なので、**FAIL を 1 の他で返す検証器**を
# `--verify-script` に渡したなら、下の `case` にその終了コードを足すこと（このファイルは導入先のもので、
# `ksdx plan graph` は既にあれば書き換えない）。kloop のランチャ（`kloop verify`）は PASS 0・FAIL 1 を返す。
#
# ## 基準の結果はノードディレクトリに残す（CR-I-040。2026-10-21 から）
#
# 基準は `.kloop/runs/<run>/nodes/verify-before/` に残る。`command.log` の `baseline=` の行（このスクリプトの出力）と、
# **kloop の検証器なら結果の `verify-result.json`**（突合の詳細 `traceability.json` はその隣）である。
# kloop の検証器（`kloop verify`。雛形の `scripts/verify.sh` が呼ぶ）は結果を `KLOOP_RESULT_PATH` へ書く
# （kloop 指示書 §5.2）ので、それをノードディレクトリ（`KLOOP_NODE_DIR`。kloop が command ノードへ必ず渡す）へ向ける。
# kloop の `scripts/verify-before.sh` と同じ形である（ksd-x の self-review は CR-I-041 から記録だけの verify ノードで、ラッパを使わない）。
# 2026-10-21 までは検証器の既定の置き場（`.kloop/verify-result.json`）に任せていたので、実装ループの事前検証が
# 同じ置き場を上書きし、基準は `command.log` の `baseline=PASS／FAIL` でしか残らなかった。
#
# **`KLOOP_RESULT_PATH` を見ない検証器**（結果の置き場の約束が kloop と違うもの）では、結果は今までどおり
# その検証器の置き場に残る。このラッパは移さず、`baseline_result=none` と出す（基準は `baseline=` の行だけになる）。
#
# **`KLOOP_NODE_DIR` が無ければ止める。**既定の置き場へ落とすと、基準がまた実装ループの検証に上書きされる。
# 手で試すなら `KLOOP_NODE_DIR=$(mktemp -d) bash scripts/verify-baseline.sh all`。
#
# **`set -e` を付けないこと。**付けると FAIL を 0 にする意図が壊れる。
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

# **この行は `ksdx plan graph` が `--verify-script` の値で書き換える。**
# 形（`verify_script="..."` の 1 行）を変えると書き換えに失敗し、生成が止まる。
verify_script="scripts/verify.sh"

node_dir="${KLOOP_NODE_DIR:-}"
if [ -z "$node_dir" ]; then
  echo "error=**KLOOP_NODE_DIR が無い。**これは kloop の command ノード（verify-before）から呼ばれる" >&2
  echo "  手で試すなら: KLOOP_NODE_DIR=\$(mktemp -d) bash ${BASH_SOURCE[0]} ${1:-all}" >&2
  exit 1
fi
mkdir -p "$node_dir" || exit 1
result="$node_dir/verify-result.json"
# 前の試行の結果を、この試行の基準と取り違えない（下の `baseline_result=` の判定のため）
rm -f "$result" || exit 1

KLOOP_RESULT_PATH="$result" bash "$verify_script" "${1:-all}"
rc=$?
case "$rc" in
  0) echo "baseline=PASS" ;;
  1) echo "baseline=FAIL（実装ループが直す。合否には使わない）" ;;
  *)
    echo "error=**検証器が exit $rc で終わった（PASS でも FAIL でもない）。**基準を取れていない" >&2
    exit "$rc"
    ;;
esac
if [ -f "$result" ]; then
  echo "baseline_result=$result"
else
  echo "baseline_result=none（検証器が KLOOP_RESULT_PATH へ書かなかった。結果はその検証器の置き場にあり、ここへは移していない）"
fi
exit 0
