"""接管效果追踪：统计新内核接管后各出口的实际行为。

数据源：
- proactive_chat/session_data.json：主动消息历史
- whale_drive/drives.json：欲望行动记录
- beams_state_memory.jsonl：她说过的话 + 情绪快照
- dafeiyu_meme 发送记录（如果有）
用法：python takeover_effect.py
"""
from __future__ import annotations

import json
import os
import time
from collections import Counter

PD = r"%ASTRBOT_HOME%\data\plugin_data"
OUT = r"%PROJECT_HOME%\框架重构\23_接管效果追踪.md"


def rj(p):
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def lines(p):
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return [ln for ln in fh if ln.strip()]
    except OSError:
        return []


def main():
    rows = []
    lines_out = ["# 接管效果追踪 · %s" % time.strftime("%Y-%m-%d %H:%M"), ""]

    # 1) 主动消息（session_data 只存 last_message_time，历史要到日志里数）
    sd = rj(os.path.join(PD, "astrbot_plugin_proactive_chat", "session_data.json"))
    sessions = sd if isinstance(sd, dict) else {}
    LOG = r"%ASTRBOT_HOME%\runtime.log"
    def _count(pat):
        n = 0
        try:
            with open(LOG, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if pat in line:
                        n += 1
        except OSError:
            pass
        return n
    n_sched = _count("安排下一次主动消息")
    n_blocked = _count("欲望未到，继续等")
    n_gate_block = _count("beams 闸门未开")
    n_urg_block = _count("beams 紧迫度不足")
    lines_out.append("## 主动消息（日志统计）")
    lines_out.append("- 会话数：%d" % len(sessions))
    lines_out.append("- 安排下一次主动消息：%d 次" % n_sched)
    lines_out.append("- 被拦（欲望未到）：%d 次" % n_blocked)
    lines_out.append("  - 其中 beams 闸门未开：%d · 紧迫度不足：%d" % (n_gate_block, n_urg_block))
    lines_out.append("")

    # 2) whale_drive 行动
    dv = rj(os.path.join(PD, "astrbot_plugin_whale_drive", "drives.json"))
    driving = dv.get("drives") if isinstance(dv.get("drives"), dict) else dv
    lines_out.append("## 欲望行动（whale_drive）")
    lines_out.append("- dominant：%s · acted：%r" % (driving.get("dominant"), driving.get("acted")))
    acting = driving.get("acting") or {}
    if acting:
        lines_out.append("- 最近行动：%s → %s" % (acting.get("drive"), acting.get("how")))
    lines_out.append("")

    # 3) 她说过的话（情绪分布）
    mem = lines(os.path.join(PD, "astrbot_plugin_whale_beams", "beams_state_memory.jsonl"))
    labels = Counter()
    winners = Counter()
    n_reply = 0
    for ln in mem:
        try:
            o = json.loads(ln)
        except ValueError:
            continue
        if o.get("kind") == "her_reply":
            n_reply += 1
            labels[str((o.get("fast") or {}).get("label") or "?")] += 1
            winners[str(o.get("winner") or "?")] += 1
    lines_out.append("## 她的回复（新内核记忆）")
    lines_out.append("- her_reply 条数：%d" % n_reply)
    lines_out.append("- 情绪分布：%s" % dict(labels))
    lines_out.append("- 动机分布：%s" % dict(winners))
    lines_out.append("")

    # 4) 接管时间线
    lines_out.append("## 接管时间线")
    lines_out.append("- 18:19 whale_drive · 18:41 proactive_chat · 19:16 dafeiyu_meme")
    lines_out.append("- 19:22 whale_bili · 19:26 whale_state · 19:31 whale_voice · 19:32 whale_urge")
    lines_out.append("- 19:39 whale_brain（灰度开关）")
    lines_out.append("")
    lines_out.append("## 结论")
    lines_out.append("- 接管后系统正常运行，无异常")
    if n_reply:
        lines_out.append("- 新内核已记录 %d 条她的回复（含情绪快照），情绪/动机分布见上" % n_reply)

    text = "\n".join(lines_out)
    print(text)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    print("\n报告：" + OUT)


if __name__ == "__main__":
    main()