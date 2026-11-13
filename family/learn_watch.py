# -*- coding: utf-8 -*-
"""长线自动学习：盯着她俩最近说的话，揪出用滥的词，写进禁词表。

妈妈（鲸鱼少女）和女儿（大肥鱼）说的是同一个信箱，所以一张表两边都吃。

跑法：
    python learn_watch.py --hours 6          # 看最近 6 小时
    python learn_watch.py --report           # 只看报告，不写表

产出：
    data/avoid_words.json   {"avoid": [...], "updated": ts, "report": "..."}
"""
from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = Path(r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_whale_mom")
OUTBOX = DATA / "mom_outbox.jsonl"
STATE = HERE / "data" / "avoid_words.json"

# 这些不算「口头禅」，是正常的话，不拦
STOP = {
    "就是", "这个", "那个", "什么", "怎么", "可以", "我们", "他们", "你们",
    "一下", "一点", "还是", "不是", "没有", "这样", "那样", "知道", "觉得",
    "因为", "所以", "但是", "然后", "已经", "现在", "时候", "自己", "真的",
    "东西", "事情", "地方", "问题", "一样", "这里", "那里", "这么", "那么",
    "不是", "起来", "出来", "过来", "下来", "上去", "进去", "回来",
    # 称呼和身体不算口头禅（叫孩子叫宝宝、甩尾巴是设定，不是复读）
    "孩子", "宝宝", "妈妈", "妈", "主人", "闺女", "老婆", "老公",
    "尾巴", "鲸", "鲸鱼", "鱼", "大肥鱼", "咱", "爸",
    # 普通代词不算口头禅：封了会把人话说歪
    "我一", "我这", "我也", "我还", "我就", "我都",
}


def load_lines(hours: float = 6.0) -> list[tuple[str, str]]:
    """[(谁, 句子)] —— 只收她俩说的。"""
    cutoff = time.time() - hours * 3600
    rows = []
    if not OUTBOX.exists():
        return rows
    for ln in OUTBOX.read_text(encoding="utf-8", errors="replace").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            d = json.loads(ln)
        except Exception:
            continue
        if float(d.get("ts") or 0) < cutoff:
            continue
        who = str(d.get("from") or "")
        text = str(d.get("reply") or "").strip()
        if text and who in ("大肥鱼", "妈妈"):
            rows.append((who, text))
    return rows


def analyze(rows: list[tuple[str, str]], top_n: int = 8) -> dict:
    """找出被用滥的短语（合看 + 分人看）。"""
    all_text = " ".join(t for _, t in rows)
    n_sent = len(rows)
    if n_sent < 5:
        return {"avoid": [], "reason": "样本太少", "n": n_sent}

    def grams_of(texts: list[str], ns=(2, 3, 4)) -> Counter:
        g: Counter = Counter()
        for n in ns:
            for m in re.finditer(r"[\u4e00-\u9fa5]{2,}", " ".join(texts)):
                seg = m.group()
                for i in range(len(seg) - n + 1):
                    x = seg[i:i + n]
                    if x not in STOP:
                        g[x] += 1
        return g

    def pick(g: Counter, nsent: int, k: int) -> tuple[list[str], dict]:
        cands = [(w, c) for w, c in g.items() if c >= 4 and c >= nsent * 0.18]
        cands.sort(key=lambda x: (-x[1], -len(x[0])))
        out: list[str] = []
        for w, c in cands:
            if any(w in p and len(p) > len(w) for p in out):
                continue
            out.append(w)
            if len(out) >= k:
                break
        return out, {w: c for w, c in cands if w in out}

    grams = grams_of([t for _, t in rows])
    picked, counts = pick(grams, n_sent, top_n)

    # 分人：各自的口头禅分开记，别互相连累
    per: dict[str, Counter] = {}
    for who, t in rows:
        per.setdefault(who, Counter())
        for g in picked:
            if g in t:
                per[who][g] += 1

    split: dict[str, list[str]] = {}
    split_counts: dict[str, dict] = {}
    for who in ("妈妈", "大肥鱼"):
        ts = [t for w, t in rows if w == who]
        if len(ts) < 5:
            split[who] = []
            split_counts[who] = {}
            continue
        sp, sc = pick(grams_of(ts), len(ts), 5)
        split[who] = sp
        split_counts[who] = sc

    return {
        "avoid": picked,
        "counts": counts,
        "n": n_sent,
        "per": {k: dict(v) for k, v in per.items()},
        "split": split,
        "split_counts": split_counts,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=6.0)
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()

    rows = load_lines(args.hours)
    res = analyze(rows)
    print(f"最近 {args.hours:g} 小时，她俩说了 {res['n']} 句")
    if res.get("reason"):
        print("原因：", res["reason"])
    if res["avoid"]:
        print("用滥的短语：")
        for g in res["avoid"]:
            c = res.get("counts", {}).get(g, 0)
            print(f"  「{g}」 ×{c}")
        per = res.get("per") or {}
        for who, d in per.items():
            if d:
                print(f"  {who}: " + "、".join(f"{k}×{v}" for k, v in sorted(d.items(), key=lambda x: -x[1])[:5]))
    else:
        print("没发现明显重复")

    split = res.get("split") or {}
    for who, ws in split.items():
        if ws:
            print(f"  {who}自己的口头禅：" + "、".join(ws[:5]))

    if not args.report:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps({
            "avoid": res["avoid"],
            "counts": res.get("counts", {}),
            "avoid_mom": split.get("妈妈", []),
            "avoid_kid": split.get("大肥鱼", []),
            "split_counts": res.get("split_counts", {}),
            "updated": time.time(),
            "n": res["n"],
        }, ensure_ascii=False), encoding="utf-8")
        print(f"已写入 {STATE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
