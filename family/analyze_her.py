# -*- coding: utf-8 -*-
"""看她最近说的话，找重复和套路。"""
import json
import re
from collections import Counter
from pathlib import Path

P = Path(r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_whale_mom\mom_outbox.jsonl")
rows = []
for ln in P.read_text(encoding="utf-8", errors="replace").splitlines():
    ln = ln.strip()
    if not ln:
        continue
    try:
        d = json.loads(ln)
    except Exception:
        continue
    if str(d.get("from") or "") != "大肥鱼":
        continue
    rows.append(d)

rows = rows[-40:]
print(f"她最近说了 {len(rows)} 条\n")

for d in rows[-14:]:
    print(" ", (d.get("reply") or "").replace("\n", " / ")[:80])

print("\n=== 高频短语（2~5 字，出现≥3 次）===")
text = " ".join(d.get("reply") or "" for d in rows)
grams = Counter()
for n in (2, 3, 4, 5):
    for i in range(len(text) - n):
        g = text[i:i + n]
        if re.fullmatch(r"[\u4e00-\u9fa5]+", g):
            grams[g] += 1
for g, c in grams.most_common(40):
    if c >= 3:
        print(f"  {g}  ×{c}")

print("\n=== 开头 4 字 ===")
for g, c in Counter((d.get("reply") or "")[:4] for d in rows).most_common(12):
    print(f"  「{g}」 ×{c}")

print("\n=== 结尾 3 字 ===")
for g, c in Counter((d.get("reply") or "").strip()[-3:] for d in rows).most_common(12):
    print(f"  「{g}」 ×{c}")

print("\n=== 句式统计 ===")
print("  带「就是…」:", sum(1 for d in rows if "就是" in (d.get("reply") or "")))
print("  带「嘛」  :", sum(1 for d in rows if "嘛" in (d.get("reply") or "")))
print("  带「呢」  :", sum(1 for d in rows if "呢" in (d.get("reply") or "")))
print("  以句号结尾:", sum(1 for d in rows if (d.get("reply") or "").rstrip().endswith("。")))
print("  以问号结尾:", sum(1 for d in rows if (d.get("reply") or "").rstrip().endswith("？")))
print("  平均字数  :", round(sum(len(d.get('reply') or '') for d in rows) / max(1, len(rows)), 1))
