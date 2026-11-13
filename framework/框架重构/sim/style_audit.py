# -*- coding: utf-8 -*-
"""风格抽检：对「她今天真正发出去的消息」做 01 §I/§N/§V 验收

用法：python style_audit.py [--days 1]
输出：31_风格抽检-YYYYMMDD.md
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
OUT = os.path.join(ROOT, "31_风格抽检-%s.md" % time.strftime("%Y%m%d"))

SLOP = (
    "作为一个AI", "作为人工智能", "作为一个语言模型", "我无法体验情感",
    "感谢你的提问", "这是一个很好的问题", "首先", "其次", "综上所述", "总而言之",
    "希望对你有帮助", "如有其他问题", "请随时告诉我", "我完全理解你的感受",
    "你的感受是完全正常的", "一切都会好起来的", "你并不孤单", "赋能", "抓手",
    "闭环落地", "颗粒度", "不可或缺", "至关重要", "深度剖析", "全面梳理",
    "Certainly", "Absolutely", "I'd be happy to", "Great question", "I hope this helps",
)


def load_messages(days=1):
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
            if text:
                rows.append((float(ts or 0), text))
    finally:
        con.close()
    return [r for r in rows if r[0] >= t0]


def main():
    msgs = load_messages(1)
    lines = ["# 风格抽检 · %s" % time.strftime("%Y-%m-%d %H:%M"), ""]
    lines.append("样本：最近 24 小时机器人发出的 %d 条文本消息" % len(msgs))
    lines.append("")
    if not msgs:
        lines.append("⚠️ 没有样本（她还没发过消息，或 SnowLuma 库路径不对）")
    else:
        lens = [len(t) for _ts, t in msgs]
        avg = sum(lens) / len(lens)
        short10 = sum(1 for x in lens if x <= 10) / len(lens) * 100
        short30 = sum(1 for x in lens if x <= 30) / len(lens) * 100
        lines.append("## 长度分布（01 §N：短碎、1-2 句）")
        lines.append("- 平均 %.1f 字；≤10 字 %.0f%%；≤30 字 %.0f%%" % (avg, short10, short30))
        lines.append("")
        lines.append("## 禁表命中（01 §I）")
        hits = []
        for ts, t in msgs:
            for w in SLOP:
                if w in t:
                    hits.append((w, t[:40]))
        if hits:
            lines.append("- ❌ 命中 %d 处：" % len(hits))
            for w, t in hits[:10]:
                lines.append("  - 「%s」← %s" % (w, t))
        else:
            lines.append("- ✅ 0 命中")
        lines.append("")
        lines.append("## 工具调用/JSON 泄漏（事故可见）")
        leaks = []
        for ts, t in msgs:
            if ("soul_post" in t or "<tool_call>" in t or "<tool_use" in t
                    or t.startswith(("{", "[{")) or '"content"' in t[:80] or t.strip() in ('"}', "}")):
                leaks.append((ts, t[:60]))
        if leaks:
            lines.append("- ❌ 发现 %d 条疑似泄漏：" % len(leaks))
            for ts, t in leaks[:8]:
                lines.append("  - [%s] %s" % (time.strftime("%H:%M", time.localtime(ts)), t))
        else:
            lines.append("- ✅ 0 条")
        lines.append("")

        lines.append("## 气泡连发（01 §N：一件事一条、2-3 条连发）")
        msgs_sorted = sorted(msgs)
        bursts = 0
        i = 0
        while i < len(msgs_sorted):
            j = i
            while j + 1 < len(msgs_sorted) and msgs_sorted[j + 1][0] - msgs_sorted[j][0] <= 12:
                j += 1
            n = j - i + 1
            if n >= 2:
                bursts += 1
            i = j + 1
        lines.append("- 连发组（12 秒内多条）：%d 组 / 共 %d 条" % (bursts, len(msgs)))
        lines.append("")
        lines.append("## 示例（最近 6 条）")
        for ts, t in sorted(msgs)[-6:]:
            lines.append("- [%s] %s" % (time.strftime("%H:%M", time.localtime(ts)), t[:60]))
        lines.append("")
        lines.append("## 验收四连（01 §I6）")
        lines.append("- 删客服收尾仍完整？→ 见上禁表命中")
        lines.append("- 开场是否像客服？→ 见示例首句")
        lines.append("- 列表能否并成 1-2 气泡？→ 见长度分布")
        lines.append("- 被问身份是否短认？→ 需人工抽看")
    text = "\n".join(lines)
    print(text)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    print("\n报告：" + OUT)


if __name__ == "__main__":
    main()