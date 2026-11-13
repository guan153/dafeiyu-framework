# -*- coding: utf-8 -*-
"""正常人词包读写。

源：%USERPROFILE%\\Desktop\\正常人沟通词包\\vocab.jsonl（14450 条）
- 妈妈（鲸鱼少女）：完整版，含荤词
- 女儿（大肥鱼）：剔除「网上荤词」那一类，其余照用

用法：
    pick(n=6, mood="难受")     → 抽几条贴题的
    stats()                    → 看看库里什么样
"""
from __future__ import annotations

import json
import random
import re
from pathlib import Path

SRC = Path(r"%USERPROFILE%\Desktop\正常人沟通词包\vocab.jsonl")

# 女儿不能看的：网上荤词（擦边/涩/文学露骨/事后）整类剔除
NSFW_CATEGORY = "网上荤词"
KID_SAFE_CATEGORIES = {
    "开场接话", "附和共鸣", "吐槽自嘲", "安慰安抚", "夸人", "拒绝推脱",
    "道歉认错", "催促确认", "转场收尾", "日常碎碎念", "时间概念",
    "办事逻辑", "情绪心态", "育儿照省",
}

# 情绪 → 用哪几类
MOOD_MAP = [
    (("难受", "委屈", "难过", "哭", "堵", "累", "烦", "崩"), ["安慰安抚", "情绪心态", "附和共鸣"]),
    (("开心", "好耶", "哈哈", "笑", "成"), ["附和共鸣", "夸人", "日常碎碎念"]),
    (("生气", "气死", "烦死", "滚", "凶"), ["吐槽自嘲", "情绪心态"]),
    (("困", "睡", "累"), ["日常碎碎念", "转场收尾", "时间概念"]),
    (("在吗", "在不在", "喂", "你好"), ["开场接话"]),
    (("吃饭", "饭", "饿", "吃"), ["日常碎碎念"]),
    (("为什么", "怎么", "咋", "啥", "?"), ["附和共鸣", "追问"]),
    (("谢谢", "谢", "好"), ["附和共鸣", "夸人"]),
]

_cache: dict[str, list[dict]] = {}


def _load() -> list[dict]:
    if "all" in _cache:
        return _cache["all"]
    rows = []
    if SRC.exists():
        with SRC.open("r", encoding="utf-8", errors="replace") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    d = json.loads(ln)
                except Exception:
                    continue
                if d.get("line"):
                    rows.append(d)
    _cache["all"] = rows
    return rows


def _by_cat(cat: str) -> list[dict]:
    key = "cat:" + cat
    if key not in _cache:
        _cache[key] = [d for d in _load() if d.get("category") == cat]
    return _cache[key]


def stats() -> dict:
    rows = _load()
    out: dict[str, int] = {}
    for d in rows:
        out[d.get("category", "?")] = out.get(d.get("category", "?"), 0) + 1
    return {"total": len(rows), "by_category": out}


def _pools(text: str, kid_safe: bool) -> list[str]:
    """按这句话的情绪/场景，挑几类语料。"""
    cats: list[str] = []
    for words, cs in MOOD_MAP:
        if any(w in text for w in words):
            cats.extend(cs)
    if not cats:
        cats = ["开场接话", "附和共鸣", "日常碎碎念"]
    if kid_safe:
        cats = [c for c in cats if c in KID_SAFE_CATEGORIES] or ["附和共鸣", "日常碎碎念"]
    return list(dict.fromkeys(cats))


def pick(text: str = "", n: int = 6, kid_safe: bool = False) -> list[str]:
    """抽几条贴题的真人说法。"""
    pools: list[dict] = []
    for cat in _pools(text, kid_safe):
        rows = _by_cat(cat)
        if kid_safe:
            rows = [d for d in rows if d.get("category") in KID_SAFE_CATEGORIES]
        pools.extend(rows)
    if not pools:
        return []
    # 太长的不要（一行话别太长）
    pools = [d for d in pools if 3 <= len(d.get("line", "")) <= 30]
    if not pools:
        return []
    picks = random.sample(pools, min(n, len(pools)))
    return [d["line"] for d in picks]


def block(text: str, n: int = 6, kid_safe: bool = False) -> str:
    """给 prompt 用的一小段。"""
    lines = pick(text, n, kid_safe)
    if not lines:
        return ""
    head = "真人平时是这么说的（学味道，别照抄原句）："
    return head + "\n" + "\n".join(f"- {x}" for x in lines)


def export_kid_safe(out_path: Path) -> int:
    """导出女儿用的干净版。"""
    rows = [d for d in _load() if d.get("category") in KID_SAFE_CATEGORIES]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for d in rows:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    return len(rows)


if __name__ == "__main__":
    s = stats()
    print(f"总条数 {s['total']}")
    for k, v in sorted(s["by_category"].items(), key=lambda x: -x[1]):
        flag = "  ← 女儿不用" if k == NSFW_CATEGORY else ""
        print(f"  {k}: {v}{flag}")
    kid = sum(v for k, v in s["by_category"].items() if k in KID_SAFE_CATEGORIES)
    print(f"\n女儿可用的：{kid} 条（剔掉 {s['total'] - kid} 条）")
    print("\n=== 妈妈版抽样（难受）===")
    for x in pick("我今天很难受", 5):
        print("  ", x)
    print("\n=== 女儿版抽样（难受）===")
    for x in pick("我今天很难受", 5, kid_safe=True):
        print("  ", x)
