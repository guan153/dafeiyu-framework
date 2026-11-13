# -*- coding: utf-8 -*-
"""早报：把昨晚攒的 face_turns.jsonl + stats.json 汇成一页结论。

跑法：
    python tools/face_report.py
    python tools/face_report.py --json
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TURNS = ROOT / "docs" / "face_turns.jsonl"
STATS = Path(r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_whale_face\stats.json")


def load_turns() -> list[dict]:
    if not TURNS.exists():
        return []
    out = []
    for line in TURNS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    turns = load_turns()
    stats = {}
    if STATS.exists():
        try:
            stats = json.loads(STATS.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            stats = {}

    injects = [t for t in turns if t.get("kind") == "inject"]
    hits = [t for t in turns if t.get("kind") == "hit"]
    prompts = [t for t in turns if t.get("kind") == "prompt"]

    moods = collections.Counter(t.get("mood") for t in injects)
    scenes = collections.Counter(t.get("scene") for t in injects)
    lens = [t.get("len", 0) for t in injects if isinstance(t.get("len"), int)]
    hit_true = sum(1 for t in hits if t.get("hit"))
    last_prompt = stats.get("last_prompt") or {}

    summary = {
        "turns_injected": len(injects),
        "hits": len(hits),
        "hit_rate": round(hit_true / len(hits), 3) if hits else None,
        "moods": dict(moods),
        "scenes": dict(scenes),
        "len_avg": round(sum(lens) / len(lens)) if lens else None,
        "len_max": max(lens) if lens else None,
        "budget": 240,
        "last_prompt": last_prompt,
        "stats": {k: stats.get(k) for k in ("injected", "skipped", "hit", "miss", "hit_rate", "whitelisted")},
    }

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=1))
        return 0

    print("=" * 60)
    print("脸层 · 昨晚成绩单")
    print("=" * 60)
    if not injects:
        print("没有注入记录。可能：主人没发消息，或脸层没被调用（先跑 face_health.py 看白名单）。")
        return 1
    print(f"注入轮数：{len(injects)}")
    print(f"注入长度：平均 {summary['len_avg']} 字 / 最长 {summary['len_max']} 字（预算 {summary['budget']}）")
    print(f"心情分布：{dict(moods)}")
    print(f"场景分布：{dict(scenes)}")
    if hits:
        print(f"触达：{hit_true}/{len(hits)} = {summary['hit_rate']:.0%}")
        if summary["hit_rate"] is not None and summary["hit_rate"] < 0.3:
            print("  ↳ 偏低：口癖/颜文字写进去了但没被用，下一步该改措辞或降预算")
    else:
        print("触达：无记录（回复可能没有触发比对）")
    if last_prompt:
        srcs = [k for k, v in (last_prompt.get("sources") or {}).items() if v]
        print(f"最近一轮 prompt：{last_prompt.get('parts')} 块 / {last_prompt.get('chars')} 字 · 来源 {', '.join(srcs) or '无'}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
