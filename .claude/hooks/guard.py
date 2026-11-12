#!/usr/bin/env python3
"""PreToolUse ガード Hook（指示書 §9.3）。

不可逆操作を Claude Code のツール呼び出しの段階で遮断する。
**jq に依存しない**（指示書 §17）。Python 3.11+ 標準ライブラリのみ。

登録（.claude/settings.json）:

    {"hooks": {"PreToolUse": [
      {"matcher": "Bash|Edit|Write|NotebookEdit",
       "hooks": [{"type": "command",
                  "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/guard.py\""}]}
    ]}}

**相対パスで登録してはならない。** hook はセッションの作業ディレクトリで実行されるため、
エージェントがサブディレクトリへ `cd` するとスクリプトが見つからず hook がエラーになり、
**全ツール呼び出しがブロックされて自力で復旧できなくなる**（実際に発生した）。
`kloop doctor` がこの誤りを検出する。

入力 : stdin の JSON（tool_name / tool_input / cwd / session_id …）
出力 : hookSpecificOutput.permissionDecision = allow | deny | ask

**拒否に例外は無い。** CR-031 は外部の判断者（ksd-controller）が deny を
事後に再承認する decision-cache 照合を持っていたが、**CR-039 で撤去した**
（同システムの廃止。宣言していたプロジェクトは全数走査で 0 件）。
拒否された操作は人が実行する（不可逆な公開は `kind = "push"` ノード。指示書 §6.8）。

**置き換えられない拒否がある**（CR-073。ksd-ls の CR-LS-KL-008・`docs/branch-rules.md` §6）。
`main` への直接のコミット・フックの迂回・クラウドの資源を変えるコマンドは `FIXED_BASH_RULES` と
`protected_branch_denial()` が止め、`guard-rules.toml` の `[bash].deny`（組み込みを置き換える）では外れない。
クラウドを変えるのは `effects = ["writes-cloud:<環境>"]` を宣言した `command` ノードだけで、
その上流には承認ゲートが要る（kloop が読み込み時に確かめる。`command` ノードに guard は効かない）。
"""

from __future__ import annotations

import json
import os
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

RULES_FILENAME = ".claude/guard-rules.toml"

#: 設定ファイルが無い場合に使う既定ルール（指示書 §9.3）。`[bash].deny` が**置き換える**。
#: クラウドの CLI（旧 `terraform apply|destroy`・`kubectl delete|apply`）は CR-073 で `FIXED_BASH_RULES` へ移した
BUILTIN_BASH_RULES: tuple[tuple[str, str, str], ...] = (
    (
        r"\bgit\s+push\b",
        "git push は不可逆な公開操作である",
        'kind = "push" ノード（11 規則と [push].allowed_branches）を通すこと',
    ),
    (r"\bgit\s+reset\s+--hard\b", "作業内容を復元不能に破棄する", "git stash を使うこと"),
    (r"\bgit\s+clean\s+-[a-zA-Z]*f", "追跡外ファイルを復元不能に削除する", "削除対象を確認してから人が実行すること"),
    (r"\bgit\s+(filter-branch|filter-repo)\b", "履歴を書き換える", "人が実行すること"),
    (r"\bgit\s+push\b.*--force|--force.*\bgit\s+push\b", "履歴を上書きする", "人が実行すること"),
    (
        r"\bgh\s+(pr\s+create|release|repo\s+delete)\b",
        "外部へ公開する操作である",
        "人が実行すること（PR を作るノードは kloop に無い。公開は push ノードの 11 規則と全件の関門を通す）",
    ),
    (r"\bdocker\s+system\s+prune\b", "他の作業の成果物も削除しうる", "対象を限定して人が実行すること"),
    (r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f?\s+/(?!tmp)", "ルート直下を再帰削除しようとしている", "対象パスを見直すこと"),
    (r":\(\)\{.*\};:", "fork bomb", "—"),
    (r"\bcurl\b[^|]*\|\s*(ba)?sh\b", "検証されていないスクリプトを実行する", "内容を保存して確認してから実行すること"),
)

#: 1 つの単純なコマンドの中（`;`・`&`・`|`・改行で切れる）。規則が隣のコマンドの語を拾わないため
_ONE = r"[^;&|\n]"

#: クラウドの資源を変えるコマンド（CR-073。ksd-ls の Q-LS-33 の A・LS-D29）。**読むだけ（list・describe・get）は止めない。**
#: **kloop の `ksdloop.graph.CLOUD_WRITE_PATTERNS` と同じ文字列である**（`command` ノードの argv を読み込み時に同じ規則で見る。
#: 揃っていることは試験 `test_guard_hook.TestTheCloudRulesMatchTheGraphCheck` が見る）
CLOUD_WRITE_REASON = "クラウドの資源を変える（{what}）"
CLOUD_WRITE_ALTERNATIVE = (
    'effects = ["writes-cloud:<環境>"] を宣言した command ノードに書き、上流の承認ゲート（approval ノード）を通すこと'
    "（クラウドの構成変更は人の承認が要る唯一の操作。読むだけの list / describe / get は止めない）"
)
CLOUD_WRITE_RULES: tuple[tuple[str, str], ...] = (
    (
        # 手元だけを変える群（emulators・components・config・auth）は止めない（エミュレータの起動・SDK の更新・設定の切替え）
        r"\bgcloud\b(?!" + _ONE + r"*?\s(?:emulators|components|config|auth)\b)" + _ONE
        + r"*?\s(?:create|delete|update|patch|deploy|undelete|restore|import|resize|enable|disable"
        r"|submit|start|stop|restart|reset|resume|suspend|rollback|replace|promote|migrate|apply|cancel|rm|mv"
        r"|(?:add|remove|set)-[a-z-]+)(?=[\s;&|)]|$)",
        "gcloud の作成・削除・更新・デプロイ・IAM の変更",
    ),
    (
        r"\b(?:gcloud\b" + _ONE + r"*?\sstorage|gsutil\b" + _ONE + r"*?)\s(?:cp|rsync)\b" + _ONE
        + r"*\s[\"']?gs://[^\s;&|\"']*[\"']?\s*(?=[;&|\n)]|$)",
        "Cloud Storage への書込み（行き先が gs://）",
    ),
    (
        r"\bgsutil\b" + _ONE + r"*?\s(?:rm|mv|mb|rb|setmeta|rewrite|compose"
        r"|(?:acl|defacl|iam|lifecycle|versioning|cors|label|logging|web|notification|retention|ubla|pap|autoclass"
        r"|defstorageclass|requesterpays|rpo|kms|hmac|bucketpolicyonly)\s+(?:set|ch|create|delete|update|on|off|lock"
        r"|clear|temp|event-based|authorize|watchbucket|stopchannel))\b",
        "gsutil の削除・移動・設定の変更",
    ),
    (
        r"\bbq\b" + _ONE + r"*?\s(?:rm|mk|update|load|insert|cp|cancel|truncate|undelete|set-iam-policy"
        r"|add-iam-policy-binding|remove-iam-policy-binding)\b",
        "BigQuery の作成・削除・更新・読込み",
    ),
    (
        r"\b(?:bq\b" + _ONE + r"*?\squery|gcloud\b" + _ONE + r"*?\sexecute-sql)\b" + _ONE
        + r"*\b(?i:insert|update|delete|merge|create|drop|alter|truncate|grant|revoke)\b",
        "SQL の DML・DDL（bq query・spanner execute-sql）",
    ),
    (
        r"\b(?:terraform|tofu|terragrunt)\b(?:\s+-\S+)*\s+(?:apply|destroy|import|taint|untaint|force-unlock"
        r"|(?:run-all|run)\s+(?:apply|destroy)|state\s+(?:rm|mv|push|replace-provider)|workspace\s+delete)\b",
        "IaC の適用（terraform・tofu・terragrunt）",
    ),
    (
        r"\bpulumi\b" + _ONE + r"*?\s(?:up|update|destroy|import|refresh|cancel|stack\s+(?:rm|init|rename|import)"
        r"|state\s+(?:delete|unprotect|rename|move|edit|repair))\b",
        "IaC の適用（pulumi）",
    ),
    (
        r"\bkubectl\b(?:\s+(?:-[a-zA-Z]\s+\S+|--[\w-]+(?:[=\s]\S+)?|-\S+))*?\s+(?:apply|delete|create|patch|replace"
        r"|edit|scale|autoscale|set|label|annotate|taint|drain|cordon|uncordon|expose|run|cp"
        r"|rollout\s+(?:restart|undo|pause|resume))\b",
        "クラスタの状態の変更（kubectl）",
    ),
    (r"\bhelm\b" + _ONE + r"*?\s(?:install|upgrade|uninstall|delete|rollback)\b", "クラスタの状態の変更（helm）"),
    (
        r"\bgh\s+api\b" + _ONE + r"*?(?:\s-X\s*|\s--method[\s=]\s*)[\"']?(?i:post|patch|put|delete)\b",
        "外部のサービスの設定の変更（gh api の書込み）",
    ),
    (
        r"\bgh\s+api\b(?!" + _ONE + r"*?(?:\s-X\s*|\s--method[\s=]\s*)[\"']?(?i:get)\b)\s+(?!graphql\b)" + _ONE
        + r"*?\s(?:-[fF]|--field|--raw-field|--input)(?=[\s=]|$)",
        "外部のサービスの設定の変更（gh api は -f / -F / --input で POST になる）",
    ),
    (r"\bgh\s+api\b" + _ONE + r"*?\bgraphql\b" + _ONE + r"*\bmutation\b", "外部のサービスの設定の変更（GraphQL の mutation）"),
    (
        r"\bgh\s+(?:repo\s+(?:delete|edit|archive|unarchive|rename)|secret\s+(?:set|delete|remove)"
        r"|variable\s+(?:set|delete|remove)|workflow\s+(?:enable|disable))\b",
        "外部のサービスの設定の変更（リポジトリ・秘密・変数・ワークフロー）",
    ),
    (
        r"\bgcloud\b(?!" + _ONE + r"*?\s(?:emulators|components|config|auth)\b)" + _ONE
        + r"*?\s(?:add|execute|export|publish|snapshot|destroy|deprecate|move|upgrade|rolling-action"
        r"|(?:jobs|triggers|workflows)\s+run"
        r"|(?:attach|detach|create|delete|update|enable|disable|start|stop|reset|recreate|abandon)-[a-z-]+)(?=[\s;&|)]|$)",
        "gcloud の追加・実行・書き出し・公開・スナップショット・破棄・移動・更新",
    ),
    (
        r"\b(?:gcloud\b" + _ONE + r"*?\sstorage|gsutil\b" + _ONE + r"*?)\s(?:cp|rsync)\b" + _ONE
        + r"*?\s(?!-)[^\s;&|]+\s+[\"']?gs://",
        "Cloud Storage への書込み（2 つ目より後の引数が gs://）",
    ),
    (
        r"\bbq\b" + _ONE + r"*?\s(?:extract\b|query\b" + _ONE + r"*?\s--destination_table\b)",
        "BigQuery の書き出し・問い合わせの結果を表へ書く",
    ),
    (
        r"\bkubectl\b(?:\s+(?:-[a-zA-Z]\s+\S+|--[\w-]+(?:[=\s]\S+)?|-\S+))*?\s+(?:exec|debug|attach)\b",
        "コンテナの中でコマンドを走らせる（kubectl exec・debug・attach。中のコマンドは見分けられない）",
    ),
    (
        r"\baws\b(?:\s+--[\w-]+(?:[=\s][^\s;&|-][^\s;&|]*)?)*?\s+(?!configure\s|eks\s+update-kubeconfig\b)"
        r"(?:s3\s+(?:rm|mb|rb|mv|website)|[a-z0-9][a-z0-9-]*\s+(?:package|(?:create|delete|put|update|modify|run"
        r"|terminate|start|stop|reboot|restart|attach|detach|associate|disassociate|add|remove|register|deregister|tag"
        r"|untag|enable|disable|import|restore|copy|set|reset|revoke|authorize|cancel|allocate|release|replace|upload"
        r"|send|execute|batch-write|transact-write|rotate|purge|accept|reject|change|invoke|publish|apply|promote"
        r"|failover|resize|deploy)(?:-[a-z0-9-]+)?))(?=[\s;&|)]|$)",
        "AWS の作成・削除・更新・実行（aws）",
    ),
    (
        r"\baws\b(?:\s+--[\w-]+(?:[=\s][^\s;&|-][^\s;&|]*)?)*?\s+s3\s+(?:cp|sync)\b" + _ONE
        + r"*?\s(?!-)[^\s;&|]+\s+[\"']?s3://",
        "S3 への書込み（2 つ目より後の引数が s3://）",
    ),
    # Firebase CLI（ksd-ls の作業 RQ-KL-001。CR-124）。副コマンドは全体の選択肢（-P・--project など）の後の最初の語だけを見る
    # （`firebase help deploy` は止めない）。手元だけの群（emulators・setup・login・target・experiments・ext:sdk）は止めない。
    # 読むだけ（*:list・*:get・open・serve・use・init・help）は動詞の表に無い
    (
        r"\bfirebase(?:-tools)?(?:@[^\s;&|]+)?(?:\s+(?:(?:-P|--project|--account|--token|-c|--config)(?:\s+|=)[^\s;&|]+"
        r"|-[^\s;&|]*))*\s+(?!(?:emulators|setup|login|target|experiments|ext:sdk)(?=[:\s;&|)]|$))(?:[a-z][a-z0-9-]*:)*"
        r"(?:deploy|create|delete|destroy|set|unset|remove|update|push|prune|clone|disable|install|uninstall|configure"
        r"|import|rollback|distribute|addfirebase|restore|migrate|grant|grantaccess|setpolicy|upload|add|deprecate"
        r"|undeprecate|register)(?=[\s;&|)]|$)",
        "Firebase の作成・削除・更新・デプロイ・データの書込み（firebase）",
    ),
)

#: フックの迂回（CR-073。`docs/branch-rules.md` §6「迂回はエージェントの guard で止める」）
HOOK_BYPASS_ALTERNATIVE = "フックを通してコミットすること（メッセージにこの語を書くなら -F でファイルから渡す）"
HOOK_BYPASS_RULES: tuple[tuple[str, str, str], ...] = (
    (r"\bgit\b" + _ONE + r"*\s--no-verify\b", "git のフックを迂回する（--no-verify）", HOOK_BYPASS_ALTERNATIVE),
    (
        r"\bgit\b" + _ONE + r"*?\scommit\b" + _ONE + r"*?\s-[a-zA-Z]*n[a-zA-Z]*(?=[\s;&|]|$)",
        "git のフックを迂回する（git commit -n は --no-verify と同じ）",
        HOOK_BYPASS_ALTERNATIVE,
    ),
    (r"(?i:\bcore\.hookspath\b)", "git のフックの置き場を変える（core.hooksPath）", "フックの置き場は人が設定すること"),
    (
        r"\bKSDLS_ESCALATE\s*=",
        "main へのマージの例外を自分で開ける（KSDLS_ESCALATE）",
        "main へは ksdls escalate（薄い口は scripts/escalate.sh）で入れること",
    ),
)

#: **`[bash].deny` で置き換えられない拒否**（CR-073）。プロジェクトの規則（エージェントが書き換えうるファイル）の
#: 1 行で、外せないゲート（クラウドの構成変更の承認）と `main` の守りが外れないようにする
FIXED_BASH_RULES: tuple[tuple[str, str, str], ...] = HOOK_BYPASS_RULES + tuple(
    (pattern, CLOUD_WRITE_REASON.format(what=what), CLOUD_WRITE_ALTERNATIVE) for pattern, what in CLOUD_WRITE_RULES
)

#: 直接コミットさせないブランチ（`docs/branch-rules.md` §6。`main` へ入るのは全件の関門を通った `dev` のマージだけ）
PROTECTED_BRANCHES: tuple[str, ...] = ("main",)
#: 今のブランチへ履歴を足す git の操作。`merge`・`pull` は `--ff-only`（取り込むだけ）なら止めない
_GIT_COMMITTING = re.compile(
    r"\bgit\b(?P<opts>(?:\s+(?:-C\s+(?:\"[^\"]*\"|'[^']*'|[^\s;&|]+)|-c\s+[^\s;&|]+|--?[\w.-]+(?:=[^\s;&|]+)?))*)"
    r"\s+(?P<sub>commit|cherry-pick|revert|am|rebase|merge|pull)\b(?P<args>" + _ONE + r"*)"
)
_GIT_DASH_C = re.compile(r"-C\s+(\"[^\"]*\"|'[^']*'|[^\s;&|]+)")
_CD = re.compile(r"(?:^|[;&|(]|\s)cd\s+(\"[^\"]*\"|'[^']*'|[^\s;&|)]+)")
#: 同じコマンドの中で保護したブランチへ移ってから履歴を足す形（`git checkout main && git commit …`）。
#: `git checkout main -- <パス>`（ファイルを取り出すだけ）は移らないので止めない
_SWITCH_TO_PROTECTED = re.compile(
    r"\bgit\s+(?:checkout|switch)\s+(?:-[a-zA-Z-]+\s+)*(?:" + "|".join(map(re.escape, PROTECTED_BRANCHES)) + r")"
    r"(?![\w./-])(?!\s+--(?:\s|$))(?P<rest>.*)",
    re.DOTALL,
)

#: 常に書き込みを禁止するパス（参照プロジェクト。承認しても書き込まない）。
#: `/mnt/c/ksd-loop`・`/mnt/c/ksd-x` は ksd-ls へ取り込んだ上流で、2026-10-25 に退役した凍結した記録（ksd-ls 作業 L7-KL-001）。
#: ksd-x の試験の複製の置き場 `/mnt/c/ksd-x-runs` は作業場なので入れない（前方一致は `/` の区切りで見るので `/mnt/c/ksd-x` は当たらない）
BUILTIN_DENY_WRITE: tuple[str, ...] = (
    "/mnt/c/ksd-analyzer",
    "/mnt/c/ksd-analyzer-runs",
    "/mnt/c/devkdc",
    "/mnt/c/ksd-loop",
    "/mnt/c/ksd-x",
)

#: 読み取りを禁止するパス（秘密情報。指示書 §10.4）
BUILTIN_DENY_READ: tuple[str, ...] = (
    "~/.ssh",
    "~/.aws",
    "~/.claude/.credentials.json",
    # specarch の Neo4j のパスワード。Community には利用者ロールが無く、書込みの資格でもある（CR-043）
    "~/.config/specarch",
)

WRITE_TOOLS = {"Edit", "Write", "NotebookEdit", "MultiEdit"}
READ_TOOLS = {"Read"}

#: 実行器がその呼出しに渡す、置き換えられない拒否の置き場（ksd-ls 作業 L4-KL-002。CR-100）。`os.pathsep` 区切りの絶対パスで、
#: `*` は 1 階層に当たる。planner ノードの鍵と MCP の設定の置き場（`<state_dir>/mcp`・`<run>/nodes/*/mcp`）を、
#: どのノードのツールにも読ませない・書かせない。`guard-rules.toml` の `allow_*` では外れない。主の囲いは
#: `claude --settings` のサンドボックスの `denyRead`・`denyWrite` と `permissions.deny` で、これはその下の 1 層である
RUN_DENY_ENV = "KLOOP_GUARD_DENY_PATHS"


def project_dir(payload: dict[str, Any]) -> Path:
    """プロジェクトルート。**作業ディレクトリではない。**

    優先順:
      1. `CLAUDE_PROJECT_DIR`（Claude Code が hook へ渡す）
      2. payload の `cwd`
      3. プロセスの cwd

    ここを間違えると、サブディレクトリで実行されたときに `guard-rules.toml` が
    見つからず、**プロジェクト固有の拒否設定が黙って無効化される**。
    """
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env)
    return Path(str(payload.get("cwd") or os.getcwd()))


def load_rules(cwd: Path) -> dict[str, Any]:
    """`guard-rules.toml` を読む。**読めないときは黙らない。**

    ここで握り潰すと、プロジェクトが足した拒否（`[paths].deny_write` /
    `[bash].deny`）が**構文エラー 1 個で消える**。ガードは組み込みの既定だけで
    判定を続け、**保護が外れたことはどこにも現れない**——`allow` が返るだけである。
    ロックアウトを避けるため素通しはするが、**素通ししたことは必ず言う。**

    **読めない規則で落ちない**（5.8.10）。tomllib は UTF-8 として読むので、UTF-8 でない規則は
    `TOMLDecodeError` ではなく `UnicodeDecodeError` になる。表でない `paths`・`bash` は判定の途中で
    `AttributeError` になる。5.8.9 まではどちらも `main()` の素通しへ落ち、**組み込みの拒否まで外れていた**
    （独立レビューの実測）。**読めない理由を数え上げない**（5.8.12）：深い入れ子の配列で tomllib は
    `RecursionError` を上げ、5.8.11 までは同じく素通しした（実装者の実測）。どの例外でも組み込みの既定で判定する。
    **読めた値を当てる途中の例外**（深いドット付きキーの表を `str()` する `RecursionError`）は、`main()` が規則を読まずに
    組み込みの既定だけで判定し直す（5.8.16）。
    """
    path = cwd / RULES_FILENAME
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as fh:
            rules = tomllib.load(fh)
    except Exception as exc:  # noqa: BLE001 - 規則を読めない理由で素通しにしない
        print(
            f"kloop guard: {RULES_FILENAME} を読めないため組み込みの既定だけで判定した"
            f"（**このプロジェクトが足した拒否は効いていない**）: {exc}",
            file=sys.stderr,
        )
        return {}
    if "controller" in rules:
        # **撤去した宣言を黙って無視しない**（CR-039）。無視しても判定は deny のまま
        # ——安全な側へ倒れる——が、**書いた人は「拒否には例外がある」と思ったままになる。**
        # 判定は変えず、効いていないことだけを言う
        print(
            f"kloop guard: {RULES_FILENAME} の [controller] は CR-039 で撤去したため"
            "**読んでいない**（deny に例外は無い）。この宣言は削除すること",
            file=sys.stderr,
        )
    for key in ("paths", "bash"):
        if key in rules and not isinstance(rules[key], dict):
            print(
                f"kloop guard: {RULES_FILENAME} の {key} が表でないため読んでいない"
                f"（**このプロジェクトが {key} に足した拒否は効いていない**。組み込みの既定で判定した）",
                file=sys.stderr,
            )
            del rules[key]
    return rules


def _rules_for_bash(rules: dict[str, Any]) -> list[tuple[str, str, str]]:
    """`[bash].deny`（組み込みを**置き換える**）。読めない宣言は読まずに言う（5.8.12）。

    5.8.11 までは、配列でない `deny`（数・真偽・日付）が `TypeError` で `main()` の素通しへ落ち、
    正規表現として読めないパターンだけを書くと**何も言わずに**すべての Bash を通した（独立レビューの実測）。
    """
    entries = (rules.get("bash") or {}).get("deny")
    if not entries:
        return list(BUILTIN_BASH_RULES)
    if not isinstance(entries, list):
        print(
            f"kloop guard: {RULES_FILENAME} の [bash].deny が配列でないため読んでいない"
            "（**このプロジェクトが足した Bash の拒否は効いていない**。組み込みの既定で判定した）",
            file=sys.stderr,
        )
        return list(BUILTIN_BASH_RULES)
    out: list[tuple[str, str, str]] = []
    invalid: list[str] = []
    ignored = 0
    not_text = 0
    for e in entries:
        if not (isinstance(e, dict) and e.get("pattern")):
            ignored += 1
            continue
        if not isinstance(e["pattern"], str):
            # **文字列にして読まない**（5.10.1。5 回目の独立レビューの低-7）。5.10.0 までは `str()` で読み、`['git x']` は
            # 文字クラスになって関係のない Bash（`cat a`）を黙って拒み、深い表は `RecursionError` で規則をすべて捨てた
            not_text += 1
            continue
        pattern = e["pattern"]
        try:
            re.compile(pattern)
        except Exception:  # noqa: BLE001 - re.error だけでなく、深い入れ子の RecursionError も読めないものとする
            invalid.append(pattern)
            continue
        out.append((pattern, str(e.get("reason", "")), str(e.get("alternative", ""))))
    fallback = "（**読めるパターンが無いので組み込みの既定で判定した**）" if not out else ""
    if invalid:
        print(
            f"kloop guard: {RULES_FILENAME} の [bash].deny に正規表現として読めないパターンがあり、読んでいない: "
            + ", ".join(invalid)
            + fallback,
            file=sys.stderr,
        )
        fallback = ""
    if ignored:
        # **黙って飛ばさない**（5.8.16。5 回目の独立レビューの低-7：`deny = ['origin']` は何も言わずに組み込みで判定した）
        print(
            f"kloop guard: {RULES_FILENAME} の [bash].deny に `pattern` の付いた表でない要素が {ignored} 個あり、読んでいない"
            + fallback,
            file=sys.stderr,
        )
        fallback = ""
    if not_text:
        print(
            f"kloop guard: {RULES_FILENAME} の [bash].deny に文字列でない `pattern` が {not_text} 個あり、読んでいない"
            + fallback,
            file=sys.stderr,
        )
    return out or list(BUILTIN_BASH_RULES)


def _listed(rules: dict[str, Any], key: str) -> list[Any]:
    """`[paths].<key>` の配列。配列でなければ読まずに言う（5.8.16。5 回目の独立レビューの低-7：`deny_write = "x"` は
    何も言わずに組み込みの既定だけで判定した）。"""
    paths = rules.get("paths") or {}
    if key not in paths:
        return []
    if not isinstance(paths[key], list):
        print(
            f"kloop guard: {RULES_FILENAME} の [paths].{key} が配列でないため読んでいない"
            f"（**このプロジェクトが {key} に書いた指定は効いていない**）",
            file=sys.stderr,
        )
        return []
    return paths[key]


def _paths_from(rules: dict[str, Any], key: str, builtin: tuple[str, ...]) -> list[str]:
    return [str(v) for v in _listed(rules, key)] + list(builtin)


def _exceptions(rules: dict[str, Any], key: str) -> list[str]:
    """明示的に許可するパス（deny より優先する）。

    組み込みの拒否パスの一部だけを開けたいときに使う。
    例: `/mnt/c/devkdc` は既定で書き込み禁止だが、利用者が明示的に承認した
    サブディレクトリだけを `allow_write` へ列挙して開ける。
    **拒否の既定を弱めるので、guard-rules.toml に理由を書くこと。**
    """
    return [str(v) for v in _listed(rules, key)]


def _expand(p: str, cwd: Path | None = None) -> str:
    """パスを絶対パスへ。相対パスは**プロジェクトの cwd** を基準に解決する。

    Hook プロセスの cwd ではなく、呼び出し元が渡す `cwd` を基準にすること。
    `guard-rules.toml` に `scripts/verify.sh` のような相対パスを書けるようにするため。

    **`abspath` では足りない**（2026-08-18、独立レビューが実測）。

    ```text
    //mnt/c/ksd-analyzer/x   posixpath は先頭の // を保存する → 前方一致が外れる
    <proj>/refs/x            refs -> /mnt/c/ksd-analyzer の symlink 1 本で全域が開く
    ```

    `realpath` は両方を潰す。**大文字小文字は塞いでいない**——
    `casefold` 比較を入れたところ、`allow_write` 側の一致も伸びて
    **以前 deny だった入力が allow になった**（最長一致で拒否が負ける）。
    `/mnt/c` が大文字小文字を区別しない事実は残っており、
    **`/mnt/c/KSD-analyzer/x` は今も通る。**
    """
    expanded = os.path.expanduser(p)
    root = str(cwd) if cwd is not None else os.getcwd()
    joined = os.path.join(root, expanded)
    try:
        return os.path.realpath(joined)
    except (OSError, ValueError):
        # **判定不能を素通しにしない。** `realpath` は `lstat` を呼ぶため、
        # `abspath`（純粋な文字列操作）には無かった例外経路が増える——
        # NUL バイトを含むパスで `ValueError` が上がり、`main()` の握り潰しに
        # 落ちて **allow になっていた**（実測。独立レビューが再現）。
        # 解決できないものは、正規化前の絶対パスで判定する（拒否側に倒す）。
        return os.path.abspath(joined)


def _match_len(target: str, base: str, cwd: Path | None = None) -> int:
    """target が base の配下なら base の長さ、そうでなければ -1。"""
    b = _expand(base, cwd)
    if target == b or target.startswith(b.rstrip("/") + "/"):
        return len(b)
    return -1


def _decide_path(target: str, allows: list[str], denies: list[str], cwd: Path | None) -> str | None:
    """許可・拒否の**最も具体的な（最長一致の）指定**を採用する。

    `allow_write = ["/mnt/c/devkdc/llu"]` と
    `deny_write = ["LLU..._統合設計書.md"]` が両方ある場合、
    後者の方が具体的なので拒否が勝つ。
    戻り値は一致した拒否指定（拒否する場合）、許可なら None。
    """
    best_allow = max((_match_len(target, a, cwd) for a in allows), default=-1)
    best_deny, matched = -1, None
    for d in denies:
        n = _match_len(target, d, cwd)
        if n > best_deny:
            best_deny, matched = n, d
    if best_deny >= 0 and best_deny > best_allow:
        return matched
    return None


def branch_of(start: Path) -> str | None:
    """`start` を含む作業ツリーの今のブランチ。分からなければ None（git を起動せず `HEAD` を読む）。

    作業ツリー（`git worktree`）では `.git` がファイルで、`gitdir: <置き場>` の先に `HEAD` がある。
    detached HEAD・git の外・読めないときは None で、**止めない**——`main` への直接のコミットの主の守りは
    git のフック（`pre-commit`）であり、guard はフックを迂回させないための補いである（`docs/branch-rules.md` §6）。
    """
    try:
        here = start.expanduser().resolve()
        for base in (here, *here.parents):
            dot_git = base / ".git"
            if dot_git.is_dir():
                head = dot_git / "HEAD"
            elif dot_git.is_file():
                text = dot_git.read_text(encoding="utf-8", errors="replace").strip()
                if not text.startswith("gitdir:"):
                    return None
                git_dir = Path(text[len("gitdir:"):].strip())
                head = (git_dir if git_dir.is_absolute() else base / git_dir) / "HEAD"
            else:
                continue
            ref = head.read_text(encoding="utf-8", errors="replace").strip()
            prefix = "ref: refs/heads/"
            return ref[len(prefix):] if ref.startswith(prefix) else None
    except (OSError, ValueError, RuntimeError):
        return None
    return None


def _unquote(word: str) -> str:
    return word[1:-1] if len(word) >= 2 and word[0] == word[-1] and word[0] in "\"'" else word


def protected_branch_denial(command: str, cwd: Path) -> str:
    """`main`（`PROTECTED_BRANCHES`）へ直接履歴を足す Bash なら理由、そうでなければ空文字列（CR-073）。

    見るのは `git commit`・`cherry-pick`・`revert`・`am`・`rebase` と、`--ff-only` の無い `merge`・`pull` で、
    そのときのブランチは payload の `cwd`（その前の `cd <パス>` と `git -C <パス>` を足したもの）の `HEAD` から読む。
    同じコマンドの中で `git checkout main` / `git switch main` してから履歴を足す形も止める。
    **見分けられないもの**：変数（`$WT`）を含むパス・スクリプトの中の git・別のシェルを介した `cd`。
    これらは止めず、git のフックに任せる。
    """
    names = "・".join(PROTECTED_BRANCHES)
    alternative = (
        f"dev か wt/<task_id> の作業ツリーでコミットすること（{names} へ入るのは全件の関門を通った dev の"
        " ksdls escalate のマージだけ。docs/branch-rules.md §6）"
    )

    def adds_history(match: re.Match[str]) -> bool:
        return match.group("sub") not in ("merge", "pull") or "--ff-only" not in match.group("args").split()

    switched = _SWITCH_TO_PROTECTED.search(command)
    if switched and any(adds_history(m) for m in _GIT_COMMITTING.finditer(switched.group("rest"))):
        return f"kloop guard: {names} へ移ってから履歴を足す（{names} へ直接コミットしない）。 代替: {alternative}"

    for match in _GIT_COMMITTING.finditer(command):
        if not adds_history(match):
            continue
        where = cwd
        for cd in _CD.finditer(command, 0, match.start()):
            where = where / os.path.expanduser(_unquote(cd.group(1)))
        dash_c = _GIT_DASH_C.findall(match.group("opts"))
        if dash_c:
            where = where / os.path.expanduser(_unquote(dash_c[-1]))
        if "$" in str(where) or "`" in str(where):
            continue  # 変数を含むパスは解決しない（推測で止めない。フックが守る）
        branch = branch_of(where)
        if branch in PROTECTED_BRANCHES:
            return (
                f"kloop guard: {branch} へ直接コミットしない（git {match.group('sub')}。今のブランチ: {branch}）。"
                f" 代替: {alternative}"
            )
    return ""


#: **ksdls の書く口を止める**（作業 L4-KL-003。CR-101。`FIXED_BASH_RULES` と同じく `[bash].deny` では外れない）。
#: グラフ DB の記録・計画・台帳を書くのは kloop の実行器と人（自分の端末）だけで、エージェントの Bash からは書かせない
#: （書き先の承認の置き場 `~/.config/ksdls` は設定の deny と sandbox が守る）。読むだけの口は次の表の語と選択肢だけを通し、
#: ほかの口・知らない選択肢・略した選択肢（argparse は略を受ける：`--app` は `--apply`）は止める。
#: 値は（選択肢 → "flag"（値を取らない）か "value"（値を 1 つ取る）、位置の引数の数の（最小, 最大））
KSDLS_READ_COMMANDS: dict[tuple[str, ...], tuple[dict[str, str], tuple[int, int]]] = {
    (): ({"--version": "flag"}, (0, 0)),
    ("setup",): ({}, (0, 0)),
    ("setup", "env"): ({"--check": "flag"}, (0, 0)),
    # 導入先の下見（何も書かない。作成・衝突・同じと数を出す。ksd-ls の RQ-LS-001）。--dry-run のある形だけを通し、無い形・
    # --lead・--mcp・旗に値・導入先の数が 1 でない形は書く口として止める（_KSDLS_READ_ONLY_WITH。CR-130）
    ("setup", "project"): ({"--dry-run": "flag", "--diff": "flag"}, (1, 1)),
    ("lint",): ({}, (0, 0)),
    ("lint", "context"): ({"--root": "value", "--write-index": "flag"}, (0, 0)),
    # 文脈パックを読むだけ（グラフ DB に書かない。MCP の context_for_change と同じ。L8-SA-005）。--out は手元の
    # ディレクトリに context.json・context.md・context-full.md を書くだけ（--change・--worktree は git を読むだけ）
    ("context",): (
        {
            "--task": "value",
            "--change": "value",
            "--worktree": "flag",
            "--files": "value",
            "--for": "value",
            "--stage": "value",
            "--max-chars": "value",
            "--json": "flag",
            "--out": "value",
            "--root": "value",
            "--workdir": "value",
            "--project-id": "value",
        },
        (0, 0),
    ),
    ("graph",): ({}, (0, 0)),
    ("graph", "doctor"): ({"--root": "value"}, (0, 0)),
    ("graph", "schema"): ({"--root": "value"}, (0, 0)),
    ("graph", "roles"): ({"--root": "value"}, (0, 0)),
    ("graph", "query"): (
        {
            "--reader": "value",
            "--params": "value",
            "--requests": "value",
            "--max-rows": "value",
            "--json": "flag",
            "--root": "value",
        },
        (0, 1),
    ),
    ("graph", "namespaces"): ({"--root": "value", "--json": "flag"}, (0, 0)),
    # 最後のバックアップの一覧・予定・版の保持を読むだけ（--record・--by は meta_backup に書くので止める。L8-LS-008）
    ("graph", "backups"): ({"--root": "value", "--json": "flag"}, (0, 0)),
    ("loop",): ({}, (0, 0)),
    ("loop", "reconcile"): (
        {
            "--root": "value",
            "--run": "value",
            "--json": "flag",
            "--out": "value",
            "--state-dir": "value",
            "--project-id": "value",
        },
        (0, 0),
    ),
    ("loop", "claims"): (
        {
            "--stale": "flag",
            "--project-id": "value",
            "--run": "value",
            "--limit": "value",
            "--json": "flag",
            "--root": "value",
        },
        (0, 0),
    ),
    ("loop", "state"): ({"--project-id": "value", "--limit": "value", "--json": "flag", "--root": "value"}, (1, 1)),
    ("loop", "plan"): ({"--project-id": "value", "--limit": "value", "--json": "flag", "--root": "value"}, (1, 1)),
    ("task",): ({}, (0, 0)),
    ("task", "reconcile"): (
        {"--root": "value", "--project-id": "value", "--json": "flag", "--out": "value"},
        (0, 0),
    ),
    # --check だけを通す（書き出しを作らずに確かめる）。--check の無い形・--write-ledgers のある形は下の表で止める
    ("task", "export"): (
        {
            "--root": "value",
            "--project-id": "value",
            "--json": "flag",
            "--out": "value",
            "--check": "flag",
            "--write-ledgers": "flag",
        },
        (0, 0),
    ),
    ("task", "show"): ({"--root": "value", "--project-id": "value", "--json": "flag"}, (1, 1)),
    # 作業票の本文から AG の版にある鍵を出すだけ（L6-X-002）。--write（code_keys を書く）と書く口の選択肢（--dry-run・
    # --actor・--op-id・--reason）は下の表で止める。版の写しは ~/.cache/ksdls/mcp/ に置く
    ("task", "scope"): (
        {
            "--root": "value",
            "--project-id": "value",
            "--json": "flag",
            "--ag-version": "value",
            "--ag-workdir": "value",
        },
        (1, 1),
    ),
    # 閉じていない作業票どうしの影響の重なりを知らせるだけ（L6-X-002。止めない）。版の写しは ~/.cache/ksdls/mcp/ に置く
    ("task", "overlaps"): (
        {
            "--root": "value",
            "--project-id": "value",
            "--json": "flag",
            "--ag-version": "value",
            "--ag-workdir": "value",
            "--depth": "value",
        },
        (0, 1),
    ),
    ("denom",): ({}, (0, 0)),
    ("denom", "show"): (
        {"--version": "value", "--kind": "value", "--root": "value", "--project-id": "value", "--json": "flag"},
        (0, 0),
    ),
    ("denom", "diff"): (
        {
            "--since-last-escalation": "flag",
            "--kind": "value",
            "--root": "value",
            "--project-id": "value",
            "--json": "flag",
        },
        (0, 2),
    ),
    ("test",): ({}, (0, 0)),
    # 作り直しの口（L10）：下に口を持つ群。cases check・verify だけが読む口で、cases record は下の書く口の表で止める（3 語目まで見る）
    ("port",): ({}, (0, 0)),
    ("port", "cases"): ({}, (0, 0)),
    # 仕様書の索引の要求ごとに golden の例があるかを見るだけ（グラフ DB に書かない。ksd-ls の L10-LS-002）。
    # 先に golden を記録する --record・結果を書く --result は表に入れない（ksd-ls の L10-LS-005）
    ("port", "cases", "check"): ({"-C": "value", "--json": "flag"}, (0, 0)),
    # 新しい実装を golden の全例で流して元の実装の記録と比べるだけ（グラフ DB に書かない。結果は根の .ksdls/port/ に書く。
    # 置き場を変える --result は表に入れない：ループのノードの担当は既定の置き場でだけ確かめる。ksd-ls の L10-LS-002）。
    # --scope は port plan が書いた群の例だけを流す読む選択肢（ksd-ls の L10-LS-006）。port plan（書く口）は表に無いので止める
    ("port", "verify"): ({"-C": "value", "--json": "flag", "--scope": "value"}, (0, 0)),
    # 仕様書を決定論で確かめるだけ（グラフ DB に書かない。--facts の目録は読むだけ。無ければ specarch を一時の置き場で
    # mock・memory で流す）。索引を書き直す --write・結果を書く --result は表に入れない（ksd-ls の L10-LS-004）。
    # 見直しの判定を読む --verdict は読む。主張の一覧を書く --claims は表に入れない（ksd-ls の L10-LS-005）
    ("port", "spec"): ({}, (0, 0)),
    ("port", "spec", "check"): ({"-C": "value", "--json": "flag", "--facts": "value", "--verdict": "value", "--prior-verdict": "value"}, (0, 0)),
    # 記録（.ksdls/port/ の確かめの結果・golden・事実の目録・run.json）を読んで報告するだけ（LLM を呼ばない・specarch を
    # 流さない・何も書かない。ksd-ls の L10-LS-006）
    ("port", "report"): ({"-C": "value", "--json": "flag"}, (0, 0)),
    ("port", "status"): ({"-C": "value", "--json": "flag"}, (0, 0)),
    # 新しい実装の要求の印を kloop の spec verify で数えるだけ（合否に使わない・グラフ DB に書かない。結果は根の
    # .ksdls/port/ に、kloop の docs/spec-ids.json は置き場に書く。port verify と同じ読む口。ksd-ls の L10-LS-006）
    ("port", "trace"): ({"-C": "value", "--json": "flag"}, (0, 0)),
    # 計画を作るだけ（グラフ DB に書かない。読むのは --coverage-graph の被覆と --patterns-graph の承認済みの試験パターン
    # だけ。ksd-ls の L8-SA-007）。--out・--result-json・--coverage はファイル、--run は選んだ試験と lint を流す。--order は
    # 流す順（name・risk）、--fail-fast は最初の失敗で止める（ksd-ls の L8-SA-010）
    ("test", "plan"): (
        {
            "--base": "value",
            "--head": "value",
            "--depth": "value",
            "--by-file": "flag",
            "--json": "flag",
            "--out": "value",
            "--run": "flag",
            "--result-json": "value",
            "--cache": "value",
            "--no-cache": "flag",
            "--root": "value",
            "--coverage": "value",
            "--coverage-graph": "flag",
            "--patterns-graph": "flag",
            "--order": "value",
            "--fail-fast": "flag",
        },
        (0, 0),
    ),
    # 変わった宣言に変異を当て、どの試験が殺したかを出すだけ（グラフ DB に書かない・読まない）。複製は根の中の
    # .kloop/mutation/per-test/ で、終わりに消す。--out はファイル（L5-KL-002。kloop の CR-106）
    ("test", "mutate"): (
        {
            "--changed": "flag",
            "--code-key": "value",
            "--base": "value",
            "--head": "value",
            "--limit": "value",
            "--seed": "value",
            "--tests": "value",
            "--timeout": "value",
            "--plan": "value",
            "--out": "value",
            "--json": "flag",
            "--root": "value",
            "--cache": "value",
            "--no-cache": "flag",
        },
        (0, 0),
    ),
    # 冗長な試験の候補と消す前後の検査を出すだけ（グラフ DB に書かない。読むのは --coverage-graph・--mutation-graph の
    # 被覆と殺した変異だけ）。--coverage・--mutations・--out はファイル（L5-SA-006。§12.12 の R-2）
    ("test", "redundancy"): (
        {
            "--coverage": "value",
            "--coverage-graph": "flag",
            "--mutations": "value",
            "--mutation-graph": "flag",
            "--groups": "value",
            "--check-removal": "value",
            "--out": "value",
            "--json": "flag",
            "--root": "value",
        },
        (0, 0),
    ),
    # 変わったファイルから走らせる候補の試験を選ぶだけ（グラフ DB に書かない。試験を流さない。MCP の tests_for_change と
    # 同じ。L6-SA-002）。--files は 1 つの値（カンマ区切り）を繰り返して渡す。版の写しは ~/.cache/ksdls/mcp/ に置く
    ("test", "for-change"): (
        {
            "--files": "value",
            "--depth": "value",
            "--include-ambiguous": "flag",
            "--version": "value",
            "--root": "value",
            "--workdir": "value",
            "--project-id": "value",
            "--json": "flag",
        },
        (0, 0),
    ),
    # 試験ごとの関門の結果と選択の見落としを読むだけ（MCP の test_history と同じ。L6-SA-002）。--pattern は試験パターンの
    # ID（カンマ区切り・繰り返し。実装の試験に絞る。ksd-ls の L8-SA-008）
    ("test", "history"): (
        {
            "--test": "value",
            "--pattern": "value",
            "--limit": "value",
            "--root": "value",
            "--project-id": "value",
            "--json": "flag",
        },
        (0, 0),
    ),
    # 試験パターンごとの実装（IMPLEMENTED_BY）と最後の結果を読むだけ（MCP の pattern_coverage と同じ。グラフ DB に書かない。
    # 版の写しは ~/.cache/ksdls/mcp/ に置く。ksd-ls の L8-SA-008）
    ("test", "pattern-coverage"): (
        {
            "--module": "value",
            "--requirement": "value",
            "--node": "value",
            "--state": "value",
            "--limit": "value",
            "--root": "value",
            "--workdir": "value",
            "--project-id": "value",
            "--json": "flag",
        },
        (0, 0),
    ),
    # 試験パターンの人の口（L8-SA-004）。下に口を持つ群：show・export・check だけが読む口で、import・review・approve・
    # retire・bind は下の書く口の表で止める（3 語目まで見る）
    ("test", "patterns"): ({}, (0, 0)),
    # 試験パターンの一覧か 1 つと出来事を読むだけ
    ("test", "patterns", "show"): (
        {"--status": "value", "--limit": "value", "--json": "flag", "--root": "value", "--project-id": "value"},
        (0, 1),
    ),
    # 承認済みの分母の人が読む写しを書き出すだけ（グラフ DB に書かない。--out はファイル。既定は根の .ksdls/denominators/）
    ("test", "patterns", "export"): (
        {"--format": "value", "--out": "value", "--root": "value", "--project-id": "value"},
        (0, 0),
    ),
    # 承認済みの試験パターンに今の木の実装（試験の宣言）があるかを確かめるだけ（グラフ DB に書かない。読むのは承認済みの
    # 集合だけ。木の AG は memory。ksd-ls の L8-SA-007）
    ("test", "patterns", "check"): (
        {"--root": "value", "--cache": "value", "--no-cache": "flag", "--json": "flag", "--project-id": "value"},
        (0, 0),
    ),
}
#: 止める書く口（理由に出す名前。表に無い口・選択肢も止める。`task` の口は読むだけの口の表に無ければ止める）
KSDLS_WRITE_COMMANDS: dict[tuple[str, ...], str] = {
    ("setup", "env"): "--check の無い setup env は venv・依存・git のフックを整える",
    ("setup", "project"): "導入先に雛形・skill を置く（--dry-run は通す）",
    ("graph", "schema"): "--apply は DDL を流す",
    ("graph", "roles"): "--apply はロールと権限を流す",
    ("graph", "backup"): "名前空間を書き出す",
    ("graph", "backups"): "--record は最後のバックアップの記録を meta_backup に書く",
    ("graph", "restore"): "書き出しを表へ戻し、ジャーナルを送り直す",
    ("loop", "import"): "ファイルの記録をグラフ DB へ取り込む（--dry-run も止める）",
    ("loop", "plan", "apply"): "計画の版を人として書く（--dry-run も止める）",
    ("task", "import"): "3 モジュールの台帳をグラフ DB へ取り込む（--dry-run も止める）",
    ("task", "export"): "--check の無い task export はグラフから台帳の書き出しを作る（--write-ledgers は台帳の表を書き換える）",
    ("task", "switch"): "台帳の表を書き出しに置き換え、グラフに切替の時刻を書く（人と取りまとめ役だけ。--dry-run も止める）",
    ("task", "new"): "タスクの項目を足す（--dry-run も止める）",
    ("task", "set"): "タスクの項目の欄・優先度・担当・状態を書く（--dry-run も止める）",
    ("task", "set-refs"): "タスクの項目の参照の配列（code_keys・trace_ids など）を丸ごと置き換える（--dry-run も止める）",
    ("task", "link"): "タスクの関係を足す（--dry-run も止める）",
    ("task", "unlink"): "タスクの関係を外す（--dry-run も止める）",
    ("task", "close"): "タスクを閉じる（--dry-run も止める）",
    ("task", "accept"): "要求ドラフトを採る・退ける（--dry-run も止める）",
    ("task", "reopen"): "閉じたタスクを開き直す（--dry-run も止める）",
    ("task", "bind"): "plan_task の層をまたぐ辺と TASK_CONCERNS_CODE を結び直す（--dry-run も止める）",
    ("task", "scope"): "作業票の code_keys を書く（--write。--dry-run も止める）",
    ("task",): "タスクの台帳を書く",
    ("denom", "update"): "分母の新しい版を書く（--dry-run も止める）",
    ("denom",): "分母を書く",
    ("test", "plan"): "試験の計画を試験層に記録する（--record）",
    ("test", "mutate"): "変異の run（変異と殺した試験）を試験層に記録する（--record）",
    ("test", "gc"): "作り直せる試験層の行（計画・被覆・変異の run）を消す（--dry-run も止める）",
    ("test", "ag-write"): "ksd-ls 自身の AG（コード層の版）を Spanner に書く（取りまとめ役がエスカレーションのときに流す）",
    ("test", "ag-gc"): "ksd-ls の AG の古い版を run ごと消す（取りまとめ役がエスカレーションのときに流す。--dry-run も止める）",
    ("test", "coverage"): "4 つの群の全件を被覆つきで流し、--record で被覆の run を試験層に書く（--out だけでも止める）",
    ("test", "gate"): "全件の関門を流し、合格の記録と --record で見落としを試験層に書く（--no-git-record も止める）",
    ("test", "record"): "試験ごとの集計（fail_rate・flaky_score・runs）を試験層に書く（--dry-run も止める）",
    ("test", "patterns", "import"): "試験パターンの草案を proposed で試験層に取り込む（--dry-run も止める）",
    ("test", "patterns", "review"): "試験パターンのレビューの記録を試験層に書く（--dry-run も止める）",
    ("test", "patterns", "approve"): "試験パターンを採り、分母（承認済みの集合）の版を書く（--dry-run も止める）",
    ("test", "patterns", "retire"): "試験パターンを外し、分母の版を書く（--dry-run も止める）",
    ("test", "patterns", "bind"): "試験と試験パターンの結び付き（IMPLEMENTED_BY）を試験層に書く（--record の無い形も止める）",
    ("test",): "試験層を書く",
    ("escalate",): "全件の通った dev のコミットを main へマージする（本番へのエスカレーション。--dry-run も止める）",
    ("gate-bg", "tick"): "裏の全件の関門を 1 回決めて流す（systemd のユーザーのタイマーが起こす）",
    ("gate-bg", "status"): "裏の全件の関門の状態を読む（取りまとめ役の口。ループのノードからは呼ばない）",
    ("gate-bg", "pause"): "裏の全件の関門を止める印を置く",
    ("gate-bg", "resume"): "裏の全件の関門を止める印を外す",
    ("gate-bg", "run-now"): "全件の関門を今すぐ流す",
    ("gate-bg", "install"): "systemd のユーザーの単位を入れる（--dry-run も止める）",
    ("gate-bg",): "裏の全件の関門（L8-LS-003）",
    ("design", "search"): "統合設計書の索引を作り直して手元の xerj で引く（担当の口。ループのノードからは呼ばない。L9-LS-009）",
    ("design", "status"): "統合設計書の索引の状態を読む（担当と取りまとめ役の口。ループのノードからは呼ばない）",
    ("design", "serve"): "統合設計書の索引のサーバを起こす・止める（取りまとめ役の口）",
    ("design", "prune"): "消えた作業ツリーの索引とサーバのデータセットを消す（取りまとめ役の口）",
    ("design",): "統合設計書だけを xerj の索引で引く口（L9-LS-009）",
    ("port", "cases", "record"): "元の実装を流して golden を書き直す（期待値は元の実装からだけ取る。ループのノードからは呼ばない。L10-LS-002）",
    ("port", "init"): "port.toml と新しい実装の置き場を言語の雛形から作る（git init・kloop の雛形。人の口。L10-LS-003）",
    ("port", "facts"): "元の実装の事実の目録と sha256 の目録を書く（specarch を mock・memory で流す。L10-LS-004）",
    ("port", "spec", "plan"): "作り直しの根に仕様書を起こす kloop のグラフ・タスク・プロンプト・guard の規則を書く（人の口。L10-LS-005）",
    ("port", "plan"): "群の順と群ごとの例（.ksdls/port/plan.json）と、置き場の spec-ids.toml・trace.toml・検証器のラッパ・新しい実装を作る kloop のグラフ・kloop.toml のタスクを書く（人の口。L10-LS-006）",
    ("port", "run"): "作り直しの段を順に流し kloop を起こす（kloop.toml の mock_command も書き換える。人の口。ループのノードからは呼ばない。L10-LS-006）",
    ("port",): "仕様書の無いソースからの作り直しの口（表に無い口は止める。L10）",
    ("mcp", "serve"): "資格を持つ ksdls-plan のサーバを起こす（起こすのは kloop の実行器だけ）",
    ("mcp", "connect"): "鍵で計画を書く中継（planner ノードの claude が MCP の設定で起こす。Bash からは呼ばない）",
    ("mcp",): "計画を書く MCP のサーバと中継（CR-100）",
}
#: 書く口のうち、旗を付けた形だけを読むだけの口として通すもの（口 → 旗）。旗が無い・旗に値を付けた・位置の引数の数が
#: 合わない形は、読む形と見分けずに書く口として止める（--help は通す。CR-101・CR-130）
_KSDLS_READ_ONLY_WITH: dict[tuple[str, ...], str] = {
    ("setup", "env"): "--check",
    ("setup", "project"): "--dry-run",
    ("task", "export"): "--check",
}
KSDLS_ALTERNATIVE = (
    "書く口は人が自分の端末で流すこと（計画を変えるのは planner ノードか人の ksdls loop plan apply）。"
    "読むだけの口（ksdls graph doctor・graph schema と graph roles（--apply なし）・graph query・loop reconcile・"
    "loop claims・loop state <run_id>・loop plan <run_id>・task reconcile・task export --check・task show <項目>・"
    "denom show・denom diff・graph backups・test plan と test mutate（--record なし）・test for-change・test history・"
    "test patterns show・test patterns export・test patterns check・lint context・"
    "context・"
    "setup env --check・setup project <導入先> --dry-run [--diff]・--version）は、"
    "変数・$(…)・入れ子のシェル・python -c を使わずに直接呼べば通る"
)
_KSDLS = "ksdls"
#: 単純なコマンドを分ける記号（shlex の punctuation_chars）。改行もコマンドの区切り
_SHELL_PUNCT = "();<>|&\n"
_RESERVED = frozenset(
    {"if", "then", "else", "elif", "fi", "do", "done", "while", "until", "for", "in", "case", "esac", "select",
     "function", "!", "{", "}", "[[", "]]", "coproc"}
)  # fmt: skip
_PYTHON = re.compile(r"^(?:python|pypy)(?:\d+(?:\.\d+)*)?$")
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "mksh", "fish"})
#: 後ろのコマンドを起動する前置き（値を取る選択肢・飛ばす位置の引数の数）。知らない選択肢は値を取らないとみなす
#: （取るのに取らないとみなすと、値を頭のコマンドとして見る → 知らないコマンドで止まる。逆に倒すと頭を飛ばしうる）
_WRAPPERS: dict[str, tuple[frozenset[str], int]] = {
    "env": (frozenset({"-u", "--unset", "-C", "--chdir"}), 0),
    "command": (frozenset(), 0),
    "exec": (frozenset({"-a"}), 0),
    "builtin": (frozenset(), 0),
    "nohup": (frozenset(), 0),
    "nice": (frozenset({"-n", "--adjustment"}), 0),
    "ionice": (frozenset({"-c", "-n", "-p", "--class", "--classdata"}), 0),
    "timeout": (frozenset({"-s", "--signal", "-k", "--kill-after"}), 1),
    "time": (frozenset({"-f", "--format", "-o", "--output"}), 0),
    "stdbuf": (frozenset({"-i", "-o", "-e", "--input", "--output", "--error"}), 0),
    "setsid": (frozenset(), 0),
    "sudo": (frozenset({"-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U", "--user", "--group"}), 0),
    "doas": (frozenset({"-u", "-C"}), 0),
    "flock": (frozenset({"-w", "--timeout", "-E", "--conflict-exit-code"}), 1),
    "watch": (frozenset({"-n", "--interval"}), 0),
    "unbuffer": (frozenset(), 0),
    "xargs": (
        frozenset({"-I", "-n", "-P", "-L", "-d", "-E", "-s", "-a", "--max-args", "--max-procs", "--delimiter",
                   "--arg-file", "--replace", "--max-lines", "--eof", "--max-chars", "--process-slot-var"}),
        0,
    ),  # fmt: skip
}
#: `<名前> run …` で後ろのコマンドを起動するもの（uvx・pipx run は後ろがパッケージのコマンド）
_RUNNERS = frozenset({"uv", "pipx", "poetry", "pdm", "hatch", "rye", "conda", "pixi"})
#: 引数をコマンドとして起動しない（ksdls の語はデータ：ファイルの名前・探す文字列・書き出す文）
_INERT = frozenset(
    {"cat", "tac", "head", "tail", "less", "more", "wc", "ls", "tree", "stat", "file", "du", "df", "realpath",
     "readlink", "dirname", "basename", "echo", "printf", "true", "false", "test", "[", "diff", "cmp", "comm", "sort",
     "uniq", "cut", "tr", "paste", "join", "column", "nl", "fold", "fmt", "od", "xxd", "hexdump", "strings", "grep",
     "egrep", "fgrep", "rg", "ag", "ack", "jq", "yq", "cd", "pushd", "popd", "pwd", "mkdir", "rmdir", "touch", "cp",
     "mv", "rm", "ln", "chmod", "tee", "md5sum", "sha1sum", "sha256sum", "sha512sum", "base64", "export", "unset",
     "declare", "typeset", "local", "readonly", "set", "shift", "which", "whereis", "type", "hash", "sleep", "date",
     "wait", "read", "exit", "return", "ruff", "mypy", "black", "isort", "pyright", "pylint", "flake8", "pytest",
     "zip", "unzip", "gzip", "gunzip", "zcat"}
)  # fmt: skip
#: 引数の文を自分で解く（sed の e・awk の system）。ksdls の語が空白や記号を含まなければ（ファイルの名前・パターン）データとして読む
_SCRIPTABLE = frozenset({"sed", "awk", "gawk", "mawk", "nawk"})
_SCRIPT_CHARS = frozenset(" \t\n!;|&`$()")
#: git の、ほかのコマンドを起動しうる語（rebase --exec・bisect run・submodule foreach・-c alias.x=!…・config）。これらが無く、
#: ksdls の語が `!` を含まなければ（コミットの文・探す文字列・パス）データとして読む
_GIT_EXEC = frozenset({"-c", "--config-env", "config", "--exec", "-x", "run", "foreach", "--extcmd", "difftool",
                       "mergetool", "--upload-pack", "--receive-pack"})  # fmt: skip
#: ksdls の引数に現れたら、シェルが語を増やしうる（変数・置き換え・glob・波括弧の展開）。表で見分けられないので止める
_KSDLS_EXPANDS = re.compile(r"[$`*?\[\n]|\{[^{}\s]*(?:,|\.\.)[^{}\s]*\}")


def _ksdls_denied(what: str) -> str:
    return f"kloop guard: ksdls の書く口（{what}）はエージェントの Bash から呼ばない（CR-101）。 代替: {KSDLS_ALTERNATIVE}"


def _ksdls_unknown(why: str) -> str:
    return (
        f"kloop guard: ksdls を書く口で呼ぶかを見分けられない形（{why}）なので止めた（fail closed。CR-101）。"
        f" 代替: {KSDLS_ALTERNATIVE}"
    )


def _ksdls_write(key: tuple[str, ...]) -> str:
    what = KSDLS_WRITE_COMMANDS.get(key) or KSDLS_WRITE_COMMANDS.get(key[:1])
    name = " ".join(("ksdls", *key))
    return _ksdls_denied(f"{name}：{what}" if what else f"{name}：読むだけの口の表に無い")


def ksdls_args_denial(args: list[str]) -> str:
    """ksdls の引数（コマンドの名前の後ろ）が書く口・見分けられない形なら理由、読むだけの口なら空文字列（CR-101）。"""
    for word in args:
        if _KSDLS_EXPANDS.search(word):
            return _ksdls_unknown(f"引数 {word!r} をシェルが展開しうる（$・`・glob・波括弧）")
    if tuple(args[:3]) == ("loop", "plan", "apply"):
        return _ksdls_write(("loop", "plan", "apply"))
    key: tuple[str, ...] = ()
    if args and not args[0].startswith("-"):
        key = (args[0],)
        if len(args) > 1 and not args[1].startswith("-") and key in KSDLS_READ_COMMANDS:
            key = (args[0], args[1])
            # 下に口を持つ群（test patterns）は 3 語目まで見る（どちらの表にも無い語は位置の引数のまま）
            third = (*key, args[2]) if len(args) > 2 and not args[2].startswith("-") else None
            if key in KSDLS_READ_COMMANDS and third and (third in KSDLS_READ_COMMANDS or third in KSDLS_WRITE_COMMANDS):
                key = third
    if key not in KSDLS_READ_COMMANDS:
        return _ksdls_write(key)
    options, (least, most) = KSDLS_READ_COMMANDS[key]
    gate = _KSDLS_READ_ONLY_WITH.get(key)
    rest = args[len(key):]
    positionals: list[str] = []
    seen: set[str] = set()
    helped = only_positional = False
    i = 0
    while i < len(rest):
        word = rest[i]
        i += 1
        if not only_positional and word == "--":
            only_positional = True
            continue
        if not only_positional and word.startswith("-") and word != "-":
            name, eq, _value = word.partition("=")
            if name in ("-h", "--help") and not eq:
                helped = True
                continue
            kind = options.get(name)
            if kind is None:
                if name == "--apply" or key in KSDLS_WRITE_COMMANDS:
                    return _ksdls_write(key)
                return _ksdls_unknown(f"ksdls {' '.join(key)} の知らない選択肢 {name}（略した形も止める）")
            if kind == "flag" and eq:
                if name == gate:
                    return _ksdls_write(key)  # `--dry-run=x` は読む形ではない（CR-130）
                return _ksdls_unknown(f"値を取らない選択肢 {name} に値")
            if kind == "value" and not eq:
                if i >= len(rest):
                    return _ksdls_unknown(f"選択肢 {name} の値が無い")
                i += 1
            seen.add(name)
            continue
        positionals.append(word)
    if gate and not helped and (gate not in seen or not least <= len(positionals) <= most):
        return _ksdls_write(key)
    if key == ("task", "export") and "--write-ledgers" in seen and not helped:
        return _ksdls_write(key)
    if key == ("loop", "plan") and "apply" in positionals:
        return _ksdls_write(("loop", "plan", "apply"))
    if not helped and not least <= len(positionals) <= most:
        return _ksdls_unknown(f"ksdls {' '.join(key)} の位置の引数の数（{len(positionals)}）")
    return ""


def _mentions(word: str) -> bool:
    return _KSDLS in word


def _is_assignment(word: str) -> bool:
    name, eq, _value = word.partition("=")
    return bool(eq) and name.isidentifier()


def _ksdls_tokens(command: str) -> list[str]:
    import shlex

    lexer = shlex.shlex(command, posix=True, punctuation_chars=_SHELL_PUNCT)
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    return list(lexer)


def _simple_commands(tokens: list[str]) -> list[list[str]]:
    """記号で単純なコマンドに分ける。リダイレクト（`>`・`2>&1`・`<<EOF`）の行き先は語から外す.

    標準入力へ流す `<`・`<<<` の行き先が ksdls を含むなら、そのコマンドの語に残す（`bash <<< 'ksdls …'` を見る）。
    ヒアドキュメントの中身は改行で分けた次の行からのコマンドとして見る（データの行でも ksdls を含めば止まる）。
    """
    out: list[list[str]] = []
    words: list[str] = []
    redirect = ""
    for token in tokens:
        if redirect:
            if "<" in redirect and "<<" != redirect and _mentions(token):
                words.append(token)
            redirect = ""
            continue
        if token and all(ch in _SHELL_PUNCT for ch in token):
            if ("<" in token or ">" in token) and "(" not in token and ")" not in token:
                if words and words[-1].isdigit():
                    words.pop()  # 2> の 2
                redirect = token
                continue
            out.append(words)
            words = []
            continue
        words.append(token)
    out.append(words)
    return [w for w in out if w]


def _unwrap(words: list[str], with_value: frozenset[str], positionals: int) -> list[str] | None:
    """前置き（env・timeout・xargs など）の後ろのコマンド。見分けられなければ None."""
    i = 1
    while i < len(words):
        word = words[i]
        if word == "--":
            i += 1
            break
        if words[0] == "env" and _is_assignment(word):
            i += 1
            continue
        if word.startswith("-") and len(word) > 1:
            if words[0] == "env" and word.split("=", 1)[0] in ("-S", "--split-string"):
                return None
            if word in with_value:
                if i + 1 < len(words) and _mentions(words[i + 1]):
                    return None
                i += 2
                continue
            i += 1
            continue
        break
    for _ in range(positionals):
        if i >= len(words) or _mentions(words[i]):
            return None
        i += 1
    return words[i:]


def _python_denial(args: list[str], appended: bool) -> str:
    i = 0
    while i < len(args):
        word = args[i]
        if word == "-":
            return _ksdls_unknown("python が標準入力のコードを流す")
        if word in ("--check-hash-based-pycs",):
            i += 2
            continue
        if word.startswith("--"):
            i += 1
            continue
        if word.startswith("-") and len(word) > 1:
            for at, ch in enumerate(word[1:], start=1):
                tail = word[at + 1:]
                if ch in "XW":
                    if not tail:
                        i += 1
                    break
                if ch in "cm":
                    value = tail or (args[i + 1] if i + 1 < len(args) else "")
                    after = args[i + 1:] if tail else args[i + 2:]
                    if ch == "c":
                        if appended or _mentions(value):
                            return _ksdls_unknown("python -c のコードが ksdls を含む")
                        return ""
                    if value == "ksdls.cli":
                        return _ksdls_unknown("後から引数を足す形（xargs・find -exec）") if appended else ksdls_args_denial(after)
                    if value == _KSDLS or value.startswith(_KSDLS + "."):
                        return _ksdls_unknown(f"python -m {value}")
                    return ""
            i += 1
            continue
        if _mentions(word):
            return _ksdls_unknown(f"python が ksdls のファイル {word!r} を流す")
        return ""
    return "" if appended else _ksdls_unknown("python が標準入力のコードを流す")


def _shell_denial(args: list[str], depth: int, appended: bool) -> str:
    i = 0
    while i < len(args):
        word = args[i]
        if word in ("-o", "+o", "-O", "+O", "--rcfile", "--init-file"):
            i += 2
            continue
        if word.startswith("-") and not word.startswith("--") and "c" in word[1:]:
            if appended or i + 1 >= len(args):
                return _ksdls_unknown("シェルの -c の文が見えない")
            return _command_denial(args[i + 1], depth + 1)
        if word.startswith(("-", "+")):
            i += 1
            continue
        if _mentions(word) or appended:
            return _ksdls_unknown(f"シェルのスクリプト {word!r}")
        return ""
    return _ksdls_unknown("シェルが標準入力の文を流す")


def _simple_denial(words: list[str], depth: int, appended: bool = False) -> str:
    """1 つの単純なコマンド。`appended` は xargs・find -exec の中（後から引数が足される）."""
    i = 0
    while i < len(words) and (words[i] in _RESERVED or _is_assignment(words[i])):
        i += 1
    words = words[i:]
    if not words:
        return ""
    for word in words:
        if _mentions(word) and any(mark in word for mark in ("`", "$(", "<(", ">(")):
            return _ksdls_unknown("$(…)・`…` の中")
    head, args = words[0], words[1:]
    if "$" in head or "`" in head:
        return _ksdls_unknown(f"コマンドの名前 {head!r} が変数")
    name = os.path.basename(head)
    mentions = any(_mentions(w) for w in words)
    if name == _KSDLS:
        return _ksdls_unknown("後から引数を足す形（xargs・find -exec）") if appended else ksdls_args_denial(args)
    if _PYTHON.match(name):
        return _python_denial(args, appended)
    if name in _SHELLS:
        return _shell_denial(args, depth, appended)
    if name == "eval":
        return _ksdls_unknown("eval") if appended else _command_denial(" ".join(args), depth + 1)
    if name in _WRAPPERS or (name in _RUNNERS and args[:1] == ["run"]) or name == "uvx":
        with_value, skip = _WRAPPERS.get(name, (frozenset(), 0))
        inner = _unwrap(words if name not in _RUNNERS else words[1:], with_value, skip)
        if inner is None:
            return _ksdls_unknown(f"{name} の後ろのコマンド")
        if not inner:
            return _ksdls_unknown(f"{name} が後から足す引数を起動する") if appended else ""
        return _simple_denial(inner, depth, appended or name == "xargs")
    if name == "find":
        for at, word in enumerate(args):
            if word in ("-exec", "-execdir", "-ok", "-okdir"):
                inner = []
                for item in args[at + 1:]:
                    if item in (";", "+"):
                        break
                    inner.append(item)
                found = _simple_denial(inner, depth, appended=True)
                if found:
                    return found
        return ""
    if name in _INERT:
        return ""
    if name in _SCRIPTABLE or name == "git":
        if not mentions:
            return "" if not appended else _ksdls_unknown(f"{name} が後から足す引数を解く")
        if name == "git":
            if any(w in _GIT_EXEC or w.split("=", 1)[0] in _GIT_EXEC for w in args):
                return _ksdls_unknown("git がほかのコマンドを起動しうる語（--exec・run・foreach・-c など）")
            if any(_mentions(w) and "!" in w for w in args):
                return _ksdls_unknown("git の alias の ! が ksdls を含む")
            return ""
        if any(_mentions(w) and _SCRIPT_CHARS.intersection(w) for w in args):
            return _ksdls_unknown(f"{name} の文が ksdls を含む")
        return ""
    if not mentions and not appended:
        return ""
    return _ksdls_unknown(f"{name} が ksdls を起動するかを見分けられない")


def _command_denial(command: str, depth: int = 0) -> str:
    if depth > 4:
        return _ksdls_unknown("入れ子のシェルが深い")
    text = command.replace("\\\n", "")
    if _KSDLS not in re.sub(r"[\"'\\]", "", text):
        return ""
    try:
        tokens = _ksdls_tokens(text)
    except ValueError as exc:
        return _ksdls_unknown(f"コマンドを語に分けられない：{exc}")
    for words in _simple_commands(tokens):
        found = _simple_denial(words, depth)
        if found:
            return found
    return ""


def ksdls_write_denial(command: str) -> str:
    """ksdls の書く口・見分けられない形なら理由、そうでなければ空文字列（作業 L4-KL-003。CR-101）.

    `ksdls` という語を含まない Bash は見ない（引用符と \\ を除いて探す）。含めば単純なコマンドに分け、ksdls を起動する形
    （`ksdls`・絶対パスの `…/ksdls`・`python -m ksdls.cli`・前置きの `env`・`timeout`・`uv run` など・`sh -c`・`eval`）は
    `ksdls_args_denial` の表で見る。ksdls の語を引数に持つだけのコマンド（grep・cat・ruff・pytest など）は通す。
    それ以外（変数のコマンドの名前・`$(…)`・標準入力から流すシェル・知らないコマンド）は止める。
    **見えないもの**：書いたスクリプトの中・名前を組み立てる形（`k=ks; ${k}dls`）。sandbox の denyRead・denyWrite で
    接続先と承認の置き場（`~/.config/ksdls`）を読ませず書かせないことが下支えする。
    判定の途中の想定外の例外は止める側に倒す（guard の素通しへ落とさない）。
    """
    try:
        return _command_denial(command)
    except Exception as exc:  # noqa: BLE001 - 見分けられないものは止める（fail closed）
        return _ksdls_unknown(f"判定の途中の例外 {type(exc).__name__}")


#: クラウドの CLI の名前（`cloud_command_texts` が頭の語を読み替える先。U-LS-24。ksd-ls の作業 L4-LS-006。CR-102）
CLOUD_CLIS: tuple[str, ...] = (
    "gcloud", "gsutil", "bq", "kubectl", "helm", "terraform", "tofu", "terragrunt", "pulumi", "aws", "gh", "firebase",
    "firebase-tools",
)  # fmt: skip
#: `-c` の文を中のコマンドとして読むシェル
_CLOUD_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "mksh", "ash", "fish"})
#: 後ろの語をコマンドとして起動する前置き（値を取る選択肢・飛ばす位置の引数の数）。知らない選択肢は値を取らないとみなす
_CLOUD_WRAPPERS: dict[str, tuple[frozenset[str], int]] = {
    "env": (frozenset({"-u", "--unset", "-C", "--chdir"}), 0),
    "command": (frozenset(), 0),
    "exec": (frozenset({"-a"}), 0),
    "builtin": (frozenset(), 0),
    "nohup": (frozenset(), 0),
    "nice": (frozenset({"-n", "--adjustment"}), 0),
    "ionice": (frozenset({"-c", "-n", "-p", "--class", "--classdata"}), 0),
    "timeout": (frozenset({"-s", "--signal", "-k", "--kill-after"}), 1),
    "time": (frozenset({"-f", "--format", "-o", "--output"}), 0),
    "stdbuf": (frozenset({"-i", "-o", "-e", "--input", "--output", "--error"}), 0),
    "setsid": (frozenset(), 0),
    "sudo": (frozenset({"-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U", "--user", "--group"}), 0),
    "doas": (frozenset({"-u", "-C"}), 0),
    "watch": (frozenset({"-n", "--interval"}), 0),
    "npx": (frozenset({"-p", "--package", "-c", "--call", "-w", "--workspace"}), 0),
    "xargs": (
        frozenset({"-I", "-n", "-P", "-L", "-d", "-E", "-s", "-a", "--max-args", "--max-procs", "--delimiter",
                   "--arg-file", "--replace", "--max-lines", "--eof", "--max-chars"}),
        0,
    ),  # fmt: skip
}
_CLOUD_RESERVED = frozenset({"if", "then", "else", "elif", "do", "while", "until", "!", "{", "}", "time", "coproc"})
_CLOUD_PUNCT = "();<>|&\n"


def cloud_command_texts(command: str, depth: int = 0) -> list[str]:
    """クラウドの規則を当て直す、コマンドの読み替え（U-LS-24。ksd-ls の作業 L4-LS-006。CR-102）。無ければ空.

    コマンドを単純なコマンドに分け、代入と前置き（`env`・`sudo`・`timeout`・`xargs` など）を外し、頭の語がクラウドの CLI
    （絶対パス・引用符で囲んだ名前を含む）なら `<CLI の名前> <引数>`（引数は語に分けてから繋ぎ直す。空白を含む語は引用符で
    囲む）にする。シェルの `-c` の文と `eval` の文は中を同じく見る。`python -m awscli` は `aws`、`python …/gcloud.py` は
    `gcloud` と読む。**頭の語が変数**（`$X`・`${X:-…}`・`$(…)`・`` `…` ``）なら、どの CLI でもありうるとして CLI の名前ごとの
    形を返す（見分けられない形は止める側に倒す）。語に分けられなければ、引用符を外した文を返す。
    **guard（`templates/claude/hooks/guard.py`）と kloop（`ksdloop.graph`）で同じ文字列である**（試験が見る）。
    """
    import shlex

    if depth > 4:
        return []
    text = re.sub(r"\$\([^()]*\)|`[^`]*`", "$__subst__", command.replace("\\\n", " "))
    try:
        lexer = shlex.shlex(text, posix=True, punctuation_chars=_CLOUD_PUNCT)
        lexer.whitespace = " \t\r"
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return [re.sub(r"[\"']", "", text)]
    out: list[str] = []
    words: list[str] = []
    redirect = False
    for token in [*tokens, ";"]:
        if redirect:
            redirect = False
            continue
        if token and all(ch in _CLOUD_PUNCT for ch in token):
            if ("<" in token or ">" in token) and "(" not in token and ")" not in token:
                if words and words[-1].isdigit():
                    words.pop()  # 2> の 2
                redirect = True  # リダイレクトの行き先は語から外す
                continue
            out += _cloud_texts_of(words, depth)
            words = []
            continue
        words.append(token)
    return out


def _cloud_texts_of(words: list[str], depth: int) -> list[str]:
    import shlex

    def assignment(word: str) -> bool:
        name, eq, _value = word.partition("=")
        return bool(eq) and name.isidentifier()

    while words and (words[0] in _CLOUD_RESERVED or assignment(words[0])):
        words = words[1:]
    for _ in range(8):
        wrapper = _CLOUD_WRAPPERS.get(os.path.basename(words[0])) if words else None
        if wrapper is None:
            break
        with_value, positionals = wrapper
        i = 1
        while i < len(words):
            word = words[i]
            if word == "--":
                i += 1
                break
            if assignment(word):
                i += 1
            elif word.startswith("-") and len(word) > 1:
                i += 2 if word in with_value else 1
            else:
                break
        words = words[i + positionals:]
    if not words:
        return []
    head, args = words[0], words[1:]
    name = os.path.basename(head)
    if "$" in name or "`" in name:
        return [shlex.join([cli, *args]) for cli in CLOUD_CLIS]
    if name in _CLOUD_SHELLS:
        for at, word in enumerate(args):
            if word.startswith("-") and not word.startswith("--") and "c" in word[1:]:
                return cloud_command_texts(args[at + 1], depth + 1) if at + 1 < len(args) else []
        return []
    if name == "eval":
        return cloud_command_texts(" ".join(args), depth + 1)
    if re.fullmatch(r"(?:python|pypy)[\d.]*", name):
        for at, word in enumerate(args):
            if word == "-m":
                module = args[at + 1] if at + 1 < len(args) else ""
                return [shlex.join(["aws", *args[at + 2:]])] if module == "awscli" else []
            if not word.startswith("-"):
                script = os.path.basename(word).removesuffix(".py")
                return [shlex.join([script, *args[at + 1:]])] if script in CLOUD_CLIS else []
        return []
    stem = re.sub(r"\.(?:py|exe|cmd|bat)$", "", name)
    return [shlex.join([stem, *args])] if stem in CLOUD_CLIS else []


def fixed_bash_denial(command: str, cwd: Path) -> str:
    """置き換えられない拒否（`FIXED_BASH_RULES`・`protected_branch_denial()`）。当たらなければ空文字列（CR-073）。"""
    for pattern, reason, alternative in FIXED_BASH_RULES:
        if re.search(pattern, command):
            return f"kloop guard: {reason}（パターン: {pattern}）。 代替: {alternative}"
    ksdls = ksdls_write_denial(command)
    if ksdls:
        return ksdls
    try:
        texts = cloud_command_texts(command)
    except Exception as exc:  # noqa: BLE001 - 見分けられないものは止める（fail closed。ksdls_write_denial と同じ）
        return (
            f"kloop guard: クラウドの資源を変えるかを見分ける途中の例外 {type(exc).__name__}（止める側に倒した）。"
            f" 代替: {CLOUD_WRITE_ALTERNATIVE}"
        )
    for text in texts:
        for pattern, what in CLOUD_WRITE_RULES:
            if re.search(pattern, text):
                return (
                    f"kloop guard: {CLOUD_WRITE_REASON.format(what=what)}（読み替え: {text[:160]}・パターン: {pattern}）。"
                    f" 代替: {CLOUD_WRITE_ALTERNATIVE}"
                )
    return protected_branch_denial(command, cwd)


def run_denied_paths() -> list[str]:
    """`KLOOP_GUARD_DENY_PATHS` の置き場（絶対パスだけ。無ければ空）。"""
    raw = os.environ.get(RUN_DENY_ENV, "")
    return [p.rstrip("/") for p in raw.split(os.pathsep) if p.startswith("/") and p.rstrip("/")]


def _run_deny_regex(pattern: str, *, segment: str) -> str:
    return "".join(segment if part == "*" else re.escape(part) for part in re.split(r"(\*)", pattern))


def run_denial(target: str) -> str:
    """target（絶対パス）が `KLOOP_GUARD_DENY_PATHS` の置き場かその配下なら、当たった置き場。そうでなければ空文字列。"""
    for pattern in run_denied_paths():
        if re.fullmatch(_run_deny_regex(pattern, segment="[^/]*") + "(?:/.*)?", target):
            return pattern
    return ""


def run_denial_in_command(command: str) -> str:
    """Bash のコマンドが `KLOOP_GUARD_DENY_PATHS` の置き場を名指していれば、その置き場。そうでなければ空文字列。"""
    for pattern in run_denied_paths():
        if re.search(_run_deny_regex(pattern, segment="[^/\\s'\"]*"), command):
            return pattern
    return ""


def decide(payload: dict[str, Any], cwd: Path, rules: dict[str, Any] | None = None) -> tuple[str, str]:
    """(decision, reason) を返す。decision は allow / deny / ask。`rules` を渡さなければ `guard-rules.toml` を読む。"""
    if rules is None:
        rules = load_rules(cwd)
    tool = str(payload.get("tool_name", ""))
    raw_input = payload.get("tool_input")
    # 想定外の型（配列・文字列など）で落ちないこと。落ちると hook エラーになり、
    # **全ツール呼び出しがブロックされて復旧できなくなる**
    tool_input: dict[str, Any] = raw_input if isinstance(raw_input, dict) else {}

    if tool == "Bash":
        command = str(tool_input.get("command", ""))
        # **置き換えられない拒否を先に見る**（CR-073）。ブランチはプロジェクトの根ではなく、実際の作業ディレクトリで見る
        fixed = fixed_bash_denial(command, Path(str(payload.get("cwd") or cwd)))
        if fixed:
            return "deny", fixed
        fenced = run_denial_in_command(command)
        if fenced:
            return "deny", f"kloop guard: `{fenced}` は planner ノードの鍵と MCP の設定の置き場で、ノードからは触れない（CR-100）。"
        for pattern, reason, alternative in _rules_for_bash(rules):
            try:
                if re.search(pattern, command):
                    alt = f" 代替: {alternative}" if alternative else ""
                    return "deny", f"kloop guard: {reason}（パターン: {pattern}）。{alt}"
            except re.error:
                continue
        return "allow", ""

    if tool in WRITE_TOOLS or tool in READ_TOOLS or tool in ("Grep", "Glob"):
        # 実行器が渡した置き場（CR-100）は `allow_*` で外れない。Grep・Glob は `path` を見る（hook の matcher に入れたときだけ届く）
        given = str(tool_input.get("file_path") or tool_input.get("notebook_path") or tool_input.get("path") or "")
        fenced = run_denial(_expand(given, cwd)) if given else ""
        if fenced:
            return "deny", f"kloop guard: `{fenced}` は planner ノードの鍵と MCP の設定の置き場で、ノードからは触れない（CR-100）。"

    if tool in WRITE_TOOLS:
        target = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
        if target:
            denied = _decide_path(
                _expand(target, cwd),
                _exceptions(rules, "allow_write"),
                _paths_from(rules, "deny_write", BUILTIN_DENY_WRITE),
                cwd,
            )
            if denied:
                return (
                    "deny",
                    f"kloop guard: `{denied}` は書き込み禁止である（参照専用／保護対象）。"
                    "必要な内容はカレントプロジェクトへコピーして扱うこと。"
                    "利用者が明示的に承認した範囲だけを開ける場合は "
                    "`.claude/guard-rules.toml` の `[paths].allow_write` へ理由付きで追記すること"
                    "（**より具体的なパスの指定が優先される**）。",
                )
        return "allow", ""

    if tool in READ_TOOLS:
        target = str(tool_input.get("file_path", ""))
        if target:
            denied = _decide_path(
                _expand(target, cwd),
                _exceptions(rules, "allow_read"),
                _paths_from(rules, "deny_read", BUILTIN_DENY_READ),
                cwd,
            )
            if denied:
                return "deny", f"kloop guard: `{denied}` は秘密情報のため読み取り禁止である。"
        return "allow", ""

    return "allow", ""


def main() -> int:
    """**このプロセスは常に exit 0 で終わる。**

    PreToolUse hook が非ゼロで終わると Claude Code はツール呼び出しを止める。
    ガードが自分のバグで落ちると、Bash も Read も使えなくなり**自力で復旧できない**
    （実際に発生した）。したがって、

    - 拒否は stdout の JSON で伝える（exit code では伝えない）
    - 規則を当てる途中の例外は、規則を読まずに**組み込みの既定だけでもう一度判定する**（5.8.16）
    - それでも落ちる予期しない例外は stderr へ出したうえで**素通しする**

    素通しは保護が外れることを意味するが、ガードは多層防御の 1 層であり、
    `permissions.deny` と sandbox が下支えする（ADR-006）。
    「黙って保護が外れる」より「復旧不能で全作業が止まる」方が損害が大きい、という判断である。
    stderr はセッションに表示されるので、外れたことは人が気づける。
    """
    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001 - 深い入れ子の JSON は ValueError でなく RecursionError（5.10.0 までは exit 1 だった）
        # 判断できない入力で作業を止めない。ただし何も許可を追加しない。
        return 0
    if not isinstance(payload, dict):
        return 0

    try:
        cwd = project_dir(payload)
        try:
            decision, reason = decide(payload, cwd)
        except Exception as exc:  # noqa: BLE001 - 読んだ規則の値で落ちても、組み込みの拒否まで外さない
            # **読めた規則の値で落ちる道がある**（5.8.16。5 回目の独立レビューの中-1）。tomllib はドット付きキー
            # （`pattern.a.a.…`）を再帰せずに読むので、1 万段の表も `load_rules` を通り、値を `str()` で文字列にする
            # ところで `RecursionError` になる。5.8.15 までは、ここから下の素通しへ落ちて全規則が外れた（実測）
            print(
                f"kloop guard: {RULES_FILENAME} の規則を当てる途中で例外が発生したため、規則を読まずに組み込みの既定だけで判定した"
                f"（**このプロジェクトが足した拒否は効いていない**）: {exc!r}",
                file=sys.stderr,
            )
            decision, reason = decide(payload, cwd, rules={})
    except Exception as exc:  # noqa: BLE001 - ここで落ちるとロックアウトになる
        print(
            f"kloop guard: 判定中に例外が発生したため素通しした（保護が効いていない）: {exc!r}",
            file=sys.stderr,
        )
        return 0

    if decision == "allow":
        if reason:
            # 通常の allow は常に `""` である。理由が付く経路は CR-039 で撤去したが、
            # **付いていたら黙って捨てない**（将来の経路のための出口を残す）
            print(reason, file=sys.stderr)
        return 0

    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": decision,
                    "permissionDecisionReason": reason,
                }
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
