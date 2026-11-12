#!/usr/bin/env bash
# 検証器のランチャ（kloop 指示書 §5 / ADR-012）。
#
#   bash scripts/verify.sh [scope]
#
# **検証器の実装はここに無い。kloop 本体が持つ。**
# 配ると実体が 2 つになり、版ずれの検出と差分の当て込みに人手が要るためである
# （実際に、基盤側の修正が利用プロジェクトへ届かず、同じ欠陥が両方に別々に残った）。
#
# プロジェクトが書くのは `spec-ids.toml` だけである。このファイルは編集しない。
#
# 契約は変わらない（ADR-007 §5.2）:
#   - exit code 0 = 合格、非 0 = 不合格
#   - $KLOOP_RESULT_PATH（既定 .kloop/verify-result.json）へ結果を書く
#   - jq に依存しない。外部パッケージに依存しない
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

# KLOOP_BIN で実体を指定できる（CI や、PATH を汚したくない環境向け）
KLOOP="${KLOOP_BIN:-kloop}"

if ! command -v "$KLOOP" >/dev/null 2>&1 && [ ! -x "$KLOOP" ]; then
  cat >&2 <<'EOF'
kloop が見つからない。**検証器の実装は kloop 本体にある**（ADR-012）。

次のいずれかを行うこと:
  bash <kloop リポジトリ>/scripts/install.sh    PATH へ入れる（sudo 不要）
  KLOOP_BIN=<kloop リポジトリ>/bin/kloop bash scripts/verify.sh   実体を直接指定する

**この検証は実行していない。** 結果は「合格」でも「不合格」でもない。
EOF
  exit 7   # ENVIRONMENT_ERROR（kloop 指示書 §3.3）
fi

exec "$KLOOP" verify "$@"
