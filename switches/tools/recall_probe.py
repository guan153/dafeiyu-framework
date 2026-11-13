# -*- coding: utf-8 -*-
"""统一召回探针（影子模式，不注入）。

用法：
    python tools/recall_probe.py 唱歌
    python tools/recall_probe.py 喜欢 --limit 5 --budget 600
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("ASTRBOT_ROOT", r"%ASTRBOT_HOME%")

from whale_core import memory_recall  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("query")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--budget", type=int, default=600)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    hits = memory_recall.recall(args.query, limit=args.limit)
    if args.json:
        print(json.dumps([h.to_dict() for h in hits], ensure_ascii=False, indent=2))
        return 0

    print(f"召回「{args.query}」：{len(hits)} 条")
    for hit in hits:
        also = hit.meta.get("also_in") or []
        print(f"  [{hit.source} {hit.score}] {hit.text[:70]}" + (f"（也在 {also}）" if also else ""))
    block = memory_recall.render(hits, budget=args.budget)
    print("\n渲染块：")
    print(block or "（空）")
    print(f"\n各层规模：{memory_recall.stats()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
