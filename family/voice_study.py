# -*- coding: utf-8 -*-
"""把「正常人沟通词包」当语料读一遍，量出真人说话的规律。

只做统计，不抽句子去背。产出 data/voice_profile.json。

用法：
    python voice_study.py            # 打印画像
    python voice_study.py --lines 40 # 顺带打几行样本给我自己看
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

SRC = Path(r"%USERPROFILE%\Desktop\正常人沟通词包\vocab.jsonl")
OUT = Path(r"%PROJECT_HOME%\母女聊天室\data\voice_profile.json")

NSFW = "网上荤词"
KID_SAFE = {
    "开场接话", "附和共鸣", "吐槽自嘲", "安慰安抚", "夸人", "拒绝推脱",
    "道歉认错", "催促确认", "转场收尾", "日常碎碎念", "时间概念",
    "办事逻辑", "情绪心态", "育儿照省",
}

MOOD_WORDS = "呀呢吧嘛啊哦诶嘿哈啦咯咧哇哟嗷唉"
PARTICLES = set(MOOD_WORDS)
EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF]")
PUNCT_END = "。！？!?…~～.，,、；;：:"
WEAK = {"的", "了", "是", "我", "你", "他", "她", "它", "不", "在", "有", "个", "这", "那",
        "就", "都", "也", "还", "又", "很", "太", "要", "会", "能", "说", "过", "来", "去"}


def load() -> list[dict]:
    rows = []
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
    return rows


def strip_end(s: str) -> str:
    return s.rstrip(PUNCT_END + " \t")


def pct(n: int, d: int) -> float:
    return round(n / d, 3) if d else 0.0


def analyze(rows: list[dict], name: str) -> dict:
    lines = [d["line"].strip() for d in rows if d.get("line")]
    n = len(lines)
    lens = [len(x) for x in lines]
    lens_sorted = sorted(lens)

    def q(p: float) -> int:
        return lens_sorted[min(n - 1, int(n * p))] if n else 0

    ends = Counter()
    for x in lines:
        y = x.rstrip()
        if not y:
            continue
        ch = y[-1]
        if ch == "。":
            ends["句号"] += 1
        elif ch in "！!":
            ends["感叹"] += 1
        elif ch in "？?":
            ends["问号"] += 1
        elif ch in "~～":
            ends["波浪"] += 1
        elif ch in "…":
            ends["省略"] += 1
        elif ch in PUNCT_END:
            ends["其他标点"] += 1
        else:
            ends["不加标点"] += 1

    particle = sum(1 for x in lines if any(c in PARTICLES for c in x))
    question = sum(1 for x in lines if ("？" in x or "?" in x or re.search(
        r"吗|呢$|咋|啥|怎么|为什么|哪儿|哪里|谁|多少|几[点时]|是不是|要不要|好不好", x)))
    emoji = sum(1 for x in lines if EMOJI.search(x))
    comma = sum(x.count("，") for x in lines)
    clause = [x.count("，") + 1 for x in lines if x]

    heads = Counter()
    tails = Counter()
    for x in lines:
        t = strip_end(x)
        if len(t) >= 2:
            h = t[:2]
            if h not in WEAK:
                heads[h] += 1
        if len(t) >= 3:
            tails[t[-3:]] += 1

    colloquial = Counter()
    for w in ("就", "都", "也", "还", "才", "又", "再", "反正", "其实", "不过",
              "但是", "然后", "所以", "呗", "得了", "算了", "咋", "啥", "咋回事",
              "行", "成", "整", "弄", "搞", "搁", "贼", "挺", "有点儿", "一下子"):
        c = sum(1 for x in lines if w in x)
        if c:
            colloquial[w] = c

    first_person = sum(1 for x in lines if re.search(r"我|咱", x))
    second_person = sum(1 for x in lines if re.search(r"你|您", x))

    return {
        "name": name,
        "n": n,
        "len": {
            "mean": round(sum(lens) / n, 1) if n else 0,
            "median": q(0.5), "p25": q(0.25), "p75": q(0.75), "p90": q(0.9),
            "max": max(lens) if lens else 0,
            "le15": pct(sum(1 for x in lens if x <= 15), n),
            "le25": pct(sum(1 for x in lens if x <= 25), n),
            "ge30": pct(sum(1 for x in lens if x >= 30), n),
        },
        "end": {k: pct(v, n) for k, v in ends.most_common()},
        "particle_rate": pct(particle, n),
        "question_rate": pct(question, n),
        "emoji_rate": pct(emoji, n),
        "comma_per_line": round(comma / n, 2) if n else 0,
        "clause_ge2": pct(sum(1 for c in clause if c >= 2), n),
        "first_person_rate": pct(first_person, n),
        "second_person_rate": pct(second_person, n),
        "heads": heads.most_common(15),
        "tails": tails.most_common(15),
        "colloquial": colloquial.most_common(12),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lines", type=int, default=0)
    args = ap.parse_args()

    rows = load()
    kid = [d for d in rows if d["category"] in KID_SAFE]
    spicy = [d for d in rows if d["category"] == NSFW]

    prof = {
        "source": str(SRC),
        "total": len(rows),
        "kid_safe": analyze(kid, "女儿可读"),
        "all": analyze(rows, "全量"),
        "spicy": analyze(spicy, "荤词（只给妈妈）"),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(prof, ensure_ascii=False, indent=1), encoding="utf-8")

    for key in ("kid_safe", "all", "spicy"):
        a = prof[key]
        print(f"=== {a['name']} {a['n']} 条 ===")
        print("  长度", a["len"])
        print("  结尾", a["end"])
        print(f"  语气词 {a['particle_rate']:.0%}｜问句 {a['question_rate']:.0%}｜"
              f"emoji {a['emoji_rate']:.0%}｜两分句以上 {a['clause_ge2']:.0%}")
        print("  常用口语", a["colloquial"][:8])
        print("  开头", a["heads"][:8])
        print("  结尾", a["tails"][:8])
        print()

    if args.lines:
        print("=== 我随便看几行（女儿可读）===")
        step = max(1, len(kid) // args.lines)
        for d in kid[::step][: args.lines]:
            print(f"  [{d['category']}/{d['vibe']}] {d['line']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
