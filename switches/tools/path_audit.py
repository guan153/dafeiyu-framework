# -*- coding: utf-8 -*-
"""路径可迁移审计：她有多少地方写死了绝对路径？

现在换台机器、换个盘符，她就找不到自己的记忆和模型。
这个脚本把写死的路径全数出来，按文件排，供后面统一收进配置。

跑法：python tools/path_audit.py [--top 20]
"""

from __future__ import annotations

import argparse
import re
from collections import Counter
from pathlib import Path

ROOTS = [
    Path(r"%ASTRBOT_HOME%\data\plugins"),
    Path(r"%PROJECT_HOME%\qqbot_v3"),
    Path(r"%PROJECT_HOME%\audition"),
]
PAT = re.compile(r"[EG]:\\\\?[\w\u4e00-\u9fff][^\"'\n]{2,80}")
SKIP = (".bak", "test_", "__pycache__")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=20)
    args = ap.parse_args()
    counter: Counter[str] = Counter()
    per_file: Counter[str] = Counter()
    examples: dict[str, str] = {}
    for root in ROOTS:
        if not root.exists():
            continue
        for f in root.rglob("*.py"):
            if any(s in f.name for s in SKIP) or "__pycache__" in str(f):
                continue
            try:
                t = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            hits = PAT.findall(t)
            if not hits:
                continue
            per_file[str(f)] = len(hits)
            for h in hits:
                norm = h.split("\\")[0:3]
                counter["\\".join(norm)] += 1
                examples.setdefault("\\".join(norm), h[:70])
    total = sum(counter.values())
    print(f"写死的绝对路径共 {total} 处，分布在 {len(per_file)} 个文件。\n")
    print("按前缀：")
    for prefix, n in counter.most_common(args.top):
        print(f"  {n:>4}  {prefix}   例：{examples.get(prefix,'')}")
    print("\n按文件：")
    for path, n in per_file.most_common(args.top):
        print(f"  {n:>4}  {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
