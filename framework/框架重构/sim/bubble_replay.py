# -*- coding: utf-8 -*-
"""切条回放：拿她真实发过的消息跑 _bubble_split，验证切条效果（不需要新对话）

用法：python bubble_replay.py [--days 2] [--n 30]
输出：33_切条回放-YYYYMMDD.md
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = r"%PROJECT_HOME%\框架重构"
DB = r"%QBOT_HOME%\qbot\SnowLuma\data\100000001\messages.db"
BOT_ID = "100000001"
OUT = os.path.join(ROOT, "33_切条回放-%s.md" % time.strftime("%Y%m%d"))

# —— 与 astrbot_plugin_whale_brain/main.py 的 _bubble_split 保持一致 ——
_BUBBLE_MAX = 3
_BUBBLE_SOFT = 24
_BUBBLE_MIN = 2


def _bubble_split(text, soft=_BUBBLE_SOFT, max_n=_BUBBLE_MAX):
    t = (text or "").strip()
    if not t:
        return []
    if len(t) <= 12:
        return [t]
    if "```" in t or "http://" in t or "https://" in t:
        return [t]
    if re.search(r"(?m)^\s*(\d+[.、]|[-*])\s", t):
        return [t]
    paras = [p.strip() for p in re.split(r"\n+", t) if p.strip()]
    sents = []
    for para in paras:
        raw = [s.strip() for s in re.findall(r"[^。！？!?…~]+[。！？!?…~]*", para) if s.strip()]
        for s in raw:
            if len(s) > soft:
                # [iter16] 超长句没有句末标点：按软标点（，、；）再切（汇总 §249）
                sents.extend([p for p in re.split(r"(?<=[，、；])", s) if p.strip()])
            else:
                sents.append(s)
    if not sents:
        return [t]
    bubbles, cur = [], ""
    for s in sents:
        if cur and len(cur) + len(s) > soft:
            bubbles.append(cur)
            cur = s
        else:
            cur += s
    if cur:
        bubbles.append(cur)
    if len(bubbles) > max_n:
        bubbles = bubbles[: max_n - 1] + ["".join(bubbles[max_n - 1:])]
    return [b for b in bubbles if b.strip()]


def load_messages(days=2):
    t0 = time.time() - days * 86400
    con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    rows = []
    try:
        cur = con.cursor()
        for (data, ts) in cur.execute("SELECT data, timestamp FROM messages ORDER BY timestamp"):
            try:
                rec = json.loads(data)
            except ValueError:
                continue
            if str(rec.get("user_id")) != BOT_ID:
                continue
            text = "".join(
                str((seg.get("data") or {}).get("text") or "")
                for seg in (rec.get("message") or [])
                if isinstance(seg, dict) and seg.get("type") == "text"
            ).strip()
            if text and float(ts or 0) >= t0:
                rows.append((float(ts or 0), text))
    finally:
        con.close()
    return rows


def main():
    days = 2
    limit = 30
    for i, a in enumerate(sys.argv):
        if a == "--days" and i + 1 < len(sys.argv):
            days = int(sys.argv[i + 1])
        if a == "--n" and i + 1 < len(sys.argv):
            limit = int(sys.argv[i + 1])
    msgs = load_messages(days)[-limit:]
    lines = ["# 切条回放 · %s" % time.strftime("%Y-%m-%d %H:%M"), ""]
    lines.append("拿她最近 %d 天真实发过的 %d 条消息，套用 brain 的 `_bubble_split` 看会切成几条。" % (days, len(msgs)))
    lines.append("（规则：≤12 字不拆；代码块/URL/列表不拆；按句末标点切、合并到 ~24 字；最多 3 条）")
    lines.append("")
    if not msgs:
        lines.append("⚠️ 没有样本")
    else:
        rows = []
        for ts, t in msgs:
            b = _bubble_split(t)
            rows.append((ts, t, b))
        multi = [r for r in rows if len(r[2]) > 1]
        lines.append("## 统计")
        lines.append("- 样本：%d 条；会被切成多条的：%d 条（%.0f%%）" % (
            len(rows), len(multi), len(multi) / len(rows) * 100))
        before = sum(len(t) for _ts, t, _b in rows)
        after_max = sum(max(len(x) for x in b) for _ts, _t, b in rows)
        lines.append("- 原文总字数：%d；切后最长气泡合计：%d（说明气泡被压短）" % (before, after_max))
        avg_before = before / len(rows)
        avg_bubble = sum(sum(len(x) for x in b) / len(b) for _ts, _t, b in rows) / len(rows)
        lines.append("- 平均单条：%.1f 字 → 平均气泡：%.1f 字" % (avg_before, avg_bubble))
        lines.append("")
        lines.append("## 样例（前 12 条多条的）")
        for ts, t, b in multi[:12]:
            lines.append("- [%s] **%d 条**" % (time.strftime("%m-%d %H:%M", time.localtime(ts)), len(b)))
            lines.append("  - 原文：%s" % t[:80].replace("\n", " / "))
            for x in b:
                lines.append("  - → %s" % x[:60].replace("\n", " / "))
        lines.append("")
        lines.append("## 判定")
        ok = sum(1 for _ts, _t, b in rows if len(b) <= 3)
        lines.append("- 切条后 ≤3 条：%d/%d %s" % (ok, len(rows), "✅" if ok == len(rows) else "❌"))
        lines.append("- 单条长度是否 ≤40 字：%s" % (
            "✅" if all(max(len(x) for x in b) <= 40 for _ts, _t, b in rows) else "⚠️ 有超标"))
    text = "\n".join(lines)
    print(text)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    print("\n报告：" + OUT)


if __name__ == "__main__":
    main()