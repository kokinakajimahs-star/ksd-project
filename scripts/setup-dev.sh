#!/usr/bin/env bash
# 開発環境を用意する（設計書 2.2）。何度流しても同じ結果になる。
#
#   bash scripts/setup-dev.sh
#
# - リポジトリの根に .venv を作り、backend/requirements.lock を入れる（無ければ入れない）
# - frontend/package-lock.json があれば npm ci で frontend/node_modules を作る
# - npm の置き場は既定で .cache/npm（~/.npm はサンドボックスの中で書けない）
#
# **保護パスである。** 検証を通すために編集しない。ksdls.toml の [gate] prepare もこれを呼ぶ。
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
ROOT="$(pwd)"
PY="${PMT_PYTHON:-python3}"

# ---------------------------------------------------------------- サーバ
if [ ! -x .venv/bin/python ]; then
  echo "==> .venv を作る（$PY）"
  "$PY" -m venv .venv
fi
if [ -f backend/requirements.lock ]; then
  echo "==> backend/requirements.lock を入れる"
  .venv/bin/python -m pip install --disable-pip-version-check -q -r backend/requirements.lock
else
  echo "==> backend/requirements.lock が無い（サーバの依存は入れていない）"
fi

# ---------------------------------------------------------------- 画面
export npm_config_cache="${npm_config_cache:-$ROOT/.cache/npm}"
export npm_config_update_notifier=false
export npm_config_fund=false
export npm_config_audit=false
if [ -f frontend/package-lock.json ]; then
  echo "==> frontend の依存を入れる（npm ci）"
  npm --prefix frontend ci --no-progress
elif [ -f frontend/package.json ]; then
  echo "==> frontend/package-lock.json が無い。npm install で作る（版を固定するため、できたロックをコミットすること）"
  npm --prefix frontend install --no-progress
else
  echo "==> frontend/package.json が無い（画面の依存は入れていない）"
fi

echo "==> 完了"
