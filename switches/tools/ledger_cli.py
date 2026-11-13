# -*- coding: utf-8 -*-
"""弱点账本命令行工具（运维用，免得每次都写一次性脚本）。

用法：
    python tools/ledger_cli.py top [N]
    python tools/ledger_cli.py list [--status open]
    python tools/ledger_cli.py verify <关键词> [--note "怎么验的"]
    python tools/ledger_cli.py reopen <关键词>
    python tools/ledger_cli.py record <标题> [--severity P2] [--category bug]
    python tools/ledger_cli.py show <关键词>
    python tools/ledger_cli.py stats
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("ASTRBOT_ROOT", r"%ASTRBOT_HOME%")

from whale_core.ledger import CATEGORY_LABEL, STATUS_LABEL, get_ledger  # noqa: E402


def _match(items, keyword: str):
    return [w for w in items if keyword in w.title or keyword in w.key]


def cmd_top(args) -> int:
    for w in get_ledger().top(args.n):
        print(f"[{w.severity}｜{CATEGORY_LABEL.get(w.category, w.category)}｜{STATUS_LABEL.get(w.status, w.status)}] "
              f"{w.title} ｜ 次数 {w.occurrences} ｜ 分 {w.score}")
    return 0


def cmd_list(args) -> int:
    led = get_ledger()
    items = led.list(status=args.status, limit=args.limit)
    for w in items:
        print(f"{w.key}\n  [{w.severity}｜{w.category}｜{w.status}] {w.title}（{w.occurrences} 次）")
    print(f"\n共 {len(items)} 条")
    return 0


def cmd_verify(args) -> int:
    led = get_ledger()
    hits = _match(led.list(status=None, limit=500), args.keyword)
    if not hits:
        print(f"没找到含「{args.keyword}」的弱点")
        return 1
    for w in hits:
        led.set_status(w.key, "verified", note=args.note or "命令行确认")
        print(f"verified: {w.key} {w.title}")
    return 0


def cmd_reopen(args) -> int:
    led = get_ledger()
    hits = _match(led.list(status=None, limit=500), args.keyword)
    if not hits:
        print(f"没找到含「{args.keyword}」的弱点")
        return 1
    for w in hits:
        led.set_status(w.key, "triage", note=args.note or "重新打开")
        print(f"reopened: {w.key} {w.title}")
    return 0


def cmd_record(args) -> int:
    key, is_new = get_ledger().record(
        args.title,
        category=args.category,
        severity=args.severity,
        source="cli",
    )
    print(f"{'新增' if is_new else '已存在(+1)'}: {key}")
    return 0


def cmd_status(args) -> int:
    led = get_ledger()
    hits = _match(led.list(status=None, limit=500), args.keyword)
    if not hits:
        print(f"没找到含「{args.keyword}」的弱点")
        return 1
    for w in hits:
        ok = led.set_status(w.key, args.status, note=args.note or "命令行改状态")
        print(f"{'ok' if ok else 'fail'}: {w.key} -> {args.status}")
    return 0


def cmd_show(args) -> int:
    led = get_ledger()
    hits = _match(led.list(status=None, limit=500), args.keyword)
    if not hits:
        print("没找到")
        return 1
    for w in hits:
        print(f"{w.key}")
        print(f"  标题：{w.title}")
        print(f"  严重度：{w.severity}｜类别：{w.category}｜状态：{w.status}｜次数：{w.occurrences}")
        print(f"  根因：{w.root_cause or '—'}")
        print(f"  现象：{w.symptom or '—'}")
        print(f"  证据：{w.evidence}")
        print("  历史：")
        for h in led.history(w.key, limit=8):
            print(f"    - {h['kind']}: {h['detail'][:80]}")
    return 0


def cmd_stats(args) -> int:
    s = get_ledger().stats()
    print(f"总 {s['total']} 条，未解决 {s['open_count']} 条，权重分 {s['open_score']}")
    print("状态：", s["by_status"])
    print("类别：", s["by_category"])
    print("严重度：", s["by_severity"])
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("top")
    p.add_argument("n", nargs="?", type=int, default=8)
    p.set_defaults(func=cmd_top)

    p = sub.add_parser("list")
    p.add_argument("--status", default=None)
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("verify")
    p.add_argument("keyword")
    p.add_argument("--note", default="")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("reopen")
    p.add_argument("keyword")
    p.add_argument("--note", default="")
    p.set_defaults(func=cmd_reopen)

    p = sub.add_parser("record")
    p.add_argument("title")
    p.add_argument("--severity", default="P2")
    p.add_argument("--category", default="bug")
    p.set_defaults(func=cmd_record)

    p = sub.add_parser("status")
    p.add_argument("keyword")
    p.add_argument("status", choices=["open", "triage", "proposed", "fixing", "verified", "wontfix", "regressed"])
    p.add_argument("--note", default="")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("show")
    p.add_argument("keyword")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("stats")
    p.set_defaults(func=cmd_stats)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
