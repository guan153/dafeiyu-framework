"""复读率检测：量化"她有没有一直在说同几句话"。

指标：
1. 高频短语（出现 ≥2 次的 4 字片段）
2. 两两相似度（2-gram Jaccard）均值/最大值
3. 开头重复率（前 4 字相同）
用法：python repetition_check.py [--n 30]
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter

MEM = r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_whale_beams\beams_state_memory.jsonl"


def load_replies(n=30):
    out = []
    try:
        with open(MEM, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                if o.get("kind") == "her_reply" and o.get("text"):
                    out.append(str(o["text"]))
    except OSError:
        pass
    return out[-n:]


def bigrams(text):
    t = "".join(str(text).split())
    return {t[i:i + 2] for i in range(max(0, len(t) - 1))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30)
    args = ap.parse_args()
    replies = load_replies(args.n)
    if len(replies) < 2:
        print("样本不足（<2 条 her_reply）")
        return
    print("样本：%d 条最近的回复" % len(replies))

    # 1) 高频 4-gram
    grams = Counter()
    for r in replies:
        t = "".join(str(r).split())
        for i in range(max(0, len(t) - 3)):
            grams[t[i:i + 4]] += 1
    hot = [(g, c) for g, c in grams.most_common(10) if c >= 2]
    print("\n高频 4 字片段（出现≥2次）：")
    if hot:
        for g, c in hot:
            print("  %-6s x%d" % (g, c))
    else:
        print("  无（很好，说明没有明显复读短语）")

    # 2) 两两相似度
    sims = []
    bs = [bigrams(r) for r in replies]
    for i in range(len(bs)):
        for j in range(i + 1, len(bs)):
            if not bs[i] or not bs[j]:
                continue
            sims.append(len(bs[i] & bs[j]) / max(1, len(bs[i] | bs[j])))
    if sims:
        print("\n两两相似度：均值 %.3f / 最大 %.3f / >0.4 的比例 %.0f%%" % (
            sum(sims) / len(sims), max(sims), 100.0 * sum(1 for s in sims if s > 0.4) / len(sims)))

    # 3) 开头重复
    heads = Counter("".join(str(r).split())[:4] for r in replies)
    dup_head = sum(1 for c in heads.values() if c >= 2)
    print("开头 4 字重复的种类：%d 种" % dup_head)
    print("\n结论：高频片段少 + 相似度低 = 复读改善。")


if __name__ == "__main__":
    main()