#!/usr/bin/env bash
# レビュー後のコミット（graphs/review-pipeline.toml の commit ノードから呼ばれる。コミットの前の承認ゲートは CR-073 で外した）。
#
# **`bash -c "..."` ではなくスクリプトにしてある**（指示書 §6.2、CR-014）。
# `[graph.command_policy]` を宣言すると `-c` を含む argv は拒否される。
# argv からは `-c` に渡した文字列の中身を判定できないためである。
# このスクリプトを許可するなら kloop.toml へ次を書く:
#
#   [graph.command_policy]
#   allow = [["bash", "scripts/kloop-commit.sh"]]
#
# **許可することは、このスクリプトの中身を信頼すると決めることである。**
#
# **`git add -A` は使わない**（CR-127）。Claude Code の sandbox は、書き込みを禁じた実在しないパス（`.mcp.json` など）に
# 中からだけ見える 0 バイト・読み取り専用のファイル（置き物）を置く。追跡しているファイルの変更（`git add -u`）と、
# 追跡していないファイルのうち置き物（空で書けない通常のファイル）でないものを名前で足し、足さなかった名前を出す。
set -u

message="${KLOOP_COMMIT_MESSAGE:-fix: kloop によるループ修正}"

git add -u || exit $?

# 追跡していないファイル（無視されたものは入らない）を NUL 区切りで読む（空白・改行を含む名前でも壊れない）
files=()
count=0
while IFS= read -r -d '' f; do
  if [ -f "$f" ] && [ ! -L "$f" ] && [ ! -s "$f" ] && [ ! -w "$f" ]; then
    printf '足さなかった（空で読み取り専用。sandbox の置き物と見た）: %s\n' "$f"
    continue
  fi
  files+=("$f")
  count=$((count + 1))
done < <(git ls-files -o --exclude-standard -z)

if [ "$count" -gt 0 ]; then
  # 名前を glob にしない（--literal-pathspecs）。多ければ xargs が分けて渡す
  printf '%s\0' "${files[@]}" | xargs -0 git --literal-pathspecs add -- || exit $?
fi

if git diff --cached --quiet; then
  echo "コミットする差分が無い。何もしていない。"
  exit 0
fi
git commit -m "$message"
