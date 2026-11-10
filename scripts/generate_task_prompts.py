#!/usr/bin/env python3
"""`docs/spec-ids.json` からタスクプロンプトを生成する。

プロンプトへ実装対象の ID を**明示的に列挙する**ことで、
エージェントが設計書を読み直して対象を数え上げる手間と取りこぼしを減らす。

    python3 scripts/generate_task_prompts.py

設計書が変わったら再生成する。**生成物（prompts/*.md）は手で編集しない。**
プロジェクト固有の指示は `prompts/_preamble.md` へ書く（これは生成対象外）。

依存は Python 3.11+ 標準ライブラリのみ。
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "spec-ids.toml"
IDS = ROOT / "docs" / "spec-ids.json"
OUT = ROOT / "prompts"
PREAMBLE = OUT / "_preamble.md"

OUTPUT_CONTRACT = """\
## 報告

最後に次の 3 点だけを簡潔に報告すること。

1. 何を実装したか（ファイル単位）
2. 検証器の結果（未実装 ID がいくつ減ったか）
3. 次の反復への申し送り（未確認の仮説・保留した判断）

「完了した」という自己申告は合否に影響しない。合否は `bash scripts/verify.sh` が決める。
"""


def _table(rows: list[dict[str, Any]], cols: list[tuple[str, str]]) -> str:
    head = "| " + " | ".join(c[0] for c in cols) + " |"
    sep = "|" + "|".join(["---"] * len(cols)) + "|"
    body = [
        "| " + " | ".join(str(r.get(c[1], "")).replace("|", "\\|") for c in cols) + " |"
        for r in rows
    ]
    return "\n".join([head, sep, *body])


def _columns(items: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """items に実際に入っている属性から表の列を決める。"""
    cols: list[tuple[str, str]] = [("ID", "id")]
    for key, label in (
        ("name", "内容"),
        ("route", "ルーティング"),
        ("path", "パス"),
        ("criteria", "判定基準"),
        ("actor", "利用者"),
    ):
        if any(i.get(key) for i in items):
            cols.append((label, key))
    return cols[:4]


def render(
    *,
    key: str,
    label: str,
    chapter: str,
    scope: str,
    items: list[dict[str, Any]],
    preamble: str,
    extra: str = "",
) -> str:
    group = scope.partition(":")[2]
    title = f"{label}の実装" + (f"（{group} グループ・{len(items)} 件）" if group else f"（{len(items)} 件）")
    ui_spec = (ROOT / "docs" / "ui").is_dir() and key in ("ui", "screen")

    parts = [f"# タスク: {title}", "", preamble.strip(), ""]
    parts += [
        "## このタスクのゴール",
        "",
        f"設計書 {chapter} の {label} {len(items)} 件を実装する。",
        "",
        "## 完了の定義",
        "",
        "```bash",
        f"bash scripts/verify.sh {scope}",
        "```",
        "",
        "が exit code 0 で終わること。この検証器は設計書の ID と実装を突合し、",
        "**未実装 ID 数**を返す。この数が 0 になるまで反復する。",
        "",
    ]

    if ui_spec:
        parts += [
            "## 画面仕様",
            "",
            "**実装前に必ず対象画面の仕様を読むこと。**",
            "",
            "```text",
            "docs/ui/_common-layout.md    全画面共通のヘッダー・サイドナビ（1 度だけ実装する）",
            "docs/ui/<画面ID>.md          画面ごとの概要・入力規則・一覧テーブル列・画面要素",
            "```",
            "",
            "UI 設計資料から自動抽出したものである。図形テキストのみの抽出なので、",
            "**色・罫線・余白などの視覚的な仕様は含まれない。**",
            "抽出物にはサンプル値が混じる。**サンプル値を定数として実装しないこと。**",
            "",
        ]

    rows = list(items)
    if ui_spec:
        for r in rows:
            r = r.setdefault("spec", f"`docs/ui/{r['id']}.md`")
    cols = _columns(rows)
    if ui_spec:
        cols = cols[:3] + [("画面仕様", "spec")]

    parts += [f"## 対象（{len(items)} 件）", "", _table(rows, cols), ""]
    if extra:
        parts += [extra, ""]
    parts += [
        "## 進め方",
        "",
        f"1. まず `bash scripts/verify.sh {scope}` を実行し、未実装 ID の一覧を確認する。",
        "2. 設計書の該当章を読む（**該当箇所だけ**。全文を読み込まない）。",
        "3. 未実装のうち依存の少ないものから実装する。",
        "4. 再度検証器を実行し、未実装数が減ったことを確認する。",
        "5. 1 反復で全部を終わらせようとしない。**着実に減らすことを優先する。**",
        "",
        OUTPUT_CONTRACT,
    ]
    return "\n".join(parts).rstrip() + "\n"


def render_all(kinds: dict[str, Any], preamble: str) -> str:
    lines = [
        "# タスク: 全体の収束",
        "",
        preamble.strip(),
        "",
        "## このタスクのゴール",
        "",
        "未実装 ID を 0 にする。",
        "",
        "## 完了の定義",
        "",
        "```bash",
        "bash scripts/verify.sh all",
        "```",
        "",
        "## 注意",
        "",
        "このタスクは**全スコープを一度に見る**ため、未実装が多い段階で回すと収束しない。",
        "個別タスクを先に収束させてから使うこと。",
        "",
        "## 内訳",
        "",
        "| 種別 | 章 | 件数 |",
        "|---|---|---|",
    ]
    for key, kind in kinds.items():
        lines.append(f"| {kind['label']} | {kind['chapter']} | {len(kind['items'])} |")
    lines += [
        "",
        "## 収束しない場合",
        "",
        "反復回数を増やす前に、次を確認すること（kloop 指示書 §2.1）。",
        "",
        "```text",
        "1. 検証器の出力が根本原因を示せているか",
        "2. タスクの分解が大きすぎないか（個別タスクへ戻る）",
        "3. プロンプトが誤った前提を与えていないか",
        "```",
        "",
        OUTPUT_CONTRACT,
    ]
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ID 一覧からタスクプロンプトを生成する")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    if not IDS.is_file():
        print(f"{IDS} がありません。先に extract_spec_ids.py を実行してください。", file=sys.stderr)
        return 7
    with CONFIG.open("rb") as fh:
        config = tomllib.load(fh)
    ids = json.loads(IDS.read_text(encoding="utf-8"))
    preamble = PREAMBLE.read_text(encoding="utf-8") if PREAMBLE.is_file() else ""

    OUT.mkdir(parents=True, exist_ok=True)
    by_key = {k["key"]: k for k in config.get("kinds", [])}
    written: list[str] = []

    # 分割の判断は extract_spec_ids.should_split() に集約する
    # （kloop bootstrap のタスク生成と同じ関数を使うため、両者がずれない）
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from extract_spec_ids import groups_of, should_split  # noqa: E402

    for key, kind in ids["kinds"].items():
        cfg = by_key.get(key, {})
        items = kind["items"]
        if should_split(items, cfg):
            for group in groups_of(items):
                subset = [i for i in items if i.get("group") == group]
                name = f"{key}-{group.lower()}"
                (OUT / f"{name}.md").write_text(
                    render(
                        key=key, label=kind["label"], chapter=kind["chapter"],
                        scope=f"{key}:{group}", items=subset, preamble=preamble,
                        extra=cfg.get("prompt_extra", ""),
                    ),
                    encoding="utf-8",
                )
                written.append(name)
        else:
            (OUT / f"{key}.md").write_text(
                render(
                    key=key, label=kind["label"], chapter=kind["chapter"],
                    scope=key, items=items, preamble=preamble,
                    extra=cfg.get("prompt_extra", ""),
                ),
                encoding="utf-8",
            )
            written.append(key)

    (OUT / "all.md").write_text(render_all(ids["kinds"], preamble), encoding="utf-8")
    written.append("all")

    if not args.quiet:
        print(f"{len(written)} 件のプロンプトを生成した → {OUT}")
        for name in written:
            print(f"  prompts/{name}.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
