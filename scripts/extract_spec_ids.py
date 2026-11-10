#!/usr/bin/env python3
"""設計書から ID 体系を抽出して `docs/spec-ids.json` を生成する。

抽出の規則は `spec-ids.toml` の `[[kinds]]` が定義する。**このスクリプトは
プロジェクト固有の知識を持たない。** 変更するのは spec-ids.toml 側である。

    python3 scripts/extract_spec_ids.py

**この出力は設計書から毎回生成する。** 手で編集しない（ドリフトを防ぐため）。
依存は Python 3.11+ 標準ライブラリのみ。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "spec-ids.toml"


def load_config(path: Path = CONFIG) -> dict[str, Any]:
    if not path.is_file():
        raise SystemExit(f"{path} がありません。`kloop bootstrap` で生成してください。")
    with path.open("rb") as fh:
        return tomllib.load(fh)


def section_of(text: str, pattern: str) -> str:
    """見出し正規表現から次の同レベル見出しまでを切り出す。"""
    m = re.search(pattern, text, re.MULTILINE)
    if not m:
        return ""
    rest = text[m.end() :]
    level = m.group(0).strip().split(" ")[0]  # "#" / "##" …
    nxt = re.search(rf"^{re.escape(level)} ", rest, re.MULTILINE)
    return rest[: nxt.start()] if nxt else rest


def _group_of(kind: dict[str, Any], ident: str) -> str:
    expr = kind.get("group_from")
    if not expr:
        return ""
    m = re.search(expr, ident)
    if not m:
        return ""
    try:
        return m.group("group") or ""
    except IndexError:
        return ""


def extract_kind(text: str, kind: dict[str, Any]) -> list[dict[str, Any]]:
    scope = section_of(text, kind["section"]) if kind.get("section") else text
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    if kind.get("row"):
        row_re = re.compile(kind["row"], re.MULTILINE)
        sig_re = re.compile(kind["signature"]) if kind.get("signature") else None
        for m in row_re.finditer(scope):
            data = {k: (v.strip() if isinstance(v, str) else v) for k, v in m.groupdict().items() if v}
            ident = data.get("id", "")
            if not ident or ident in seen:
                continue
            if sig_re and data.get("sigcell"):
                sigs = [s.group("sig").strip() for s in sig_re.finditer(data["sigcell"])]
                if not sigs:
                    continue  # 署名の無い行は対象外
                endpoints = []
                for sig in sigs:
                    head, _, path = sig.partition(" ")
                    endpoints.append(
                        {
                            "methods": [x for x in head.split("/") if x.isupper()] or ["GET"],
                            "path": path.strip() or sig,
                        }
                    )
                data["endpoints"] = endpoints
                data["path"] = endpoints[0]["path"]
                data["methods"] = endpoints[0]["methods"]
                data.pop("sigcell", None)
            seen.add(ident)
            data["group"] = _group_of(kind, ident)
            rows.append(data)
    else:
        pat = re.compile(kind["pattern"], re.MULTILINE)
        for m in pat.finditer(scope):
            for value in m.groupdict().values():
                if not value or value in seen:
                    continue
                seen.add(value)
                rows.append({"id": value, "group": _group_of(kind, value)})

    return sorted(rows, key=lambda r: r["id"])


#: グループ別タスクへ分割する条件（下記 should_split 参照）
MIN_ITEMS_TO_SPLIT = 8
MIN_ITEMS_PER_GROUP = 2.5


def should_split(items: list[dict[str, Any]], kind_cfg: dict[str, Any]) -> bool:
    """グループ別のタスクへ分けるべきか。

    分けすぎると 1 タスクあたりの作業が小さくなり、反復のたびに
    プロセス起動と検証のコストだけが積み上がる。逆に大きすぎると収束しない。

    `split_by_group` を spec-ids.toml に書けば明示的に上書きできる。
    """
    explicit = kind_cfg.get("split_by_group")
    if explicit is not None:
        return bool(explicit)
    groups = {i.get("group") or "" for i in items}
    groups.discard("")
    if len(groups) < 2 or len(items) < MIN_ITEMS_TO_SPLIT:
        return False
    return len(items) / len(groups) >= MIN_ITEMS_PER_GROUP


def groups_of(items: list[dict[str, Any]]) -> list[str]:
    return sorted({i["group"] for i in items if i.get("group")})


def build(config: dict[str, Any], root: Path) -> dict[str, Any]:
    doc = root / config["spec"]["document"]
    if not doc.is_file():
        raise SystemExit(f"設計書が見つかりません: {doc}")
    text = doc.read_text(encoding="utf-8")

    kinds: dict[str, Any] = {}
    for kind in config.get("kinds", []):
        kinds[kind["key"]] = {
            "label": kind.get("label", kind["key"]),
            "chapter": kind.get("chapter", ""),
            "baseline": int(kind.get("baseline", 0)),
            "items": extract_kind(text, kind),
        }

    payload: dict[str, Any] = {
        "generated_from": doc.name,
        "document_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
        "kinds": kinds,
    }
    payload["counts"] = {k: len(v["items"]) for k, v in kinds.items()}
    payload["counts"]["total"] = sum(payload["counts"].values())
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="設計書から ID 体系を抽出する")
    parser.add_argument("--config", default=str(CONFIG))
    parser.add_argument("--out", default="docs/spec-ids.json")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    config = load_config(Path(args.config))
    payload = build(config, ROOT)

    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if not args.quiet:
        parts = [
            f"{v['label']} {len(v['items'])}" for v in payload["kinds"].values()
        ]
        print(f"抽出: {' / '.join(parts)}（合計 {payload['counts']['total']}）→ {out}")
        for key, kind in payload["kinds"].items():
            base = kind["baseline"]
            got = len(kind["items"])
            if base and got < base:
                print(f"  **警告** {key}: {got} 件（基準 {base} 件を下回る）", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
