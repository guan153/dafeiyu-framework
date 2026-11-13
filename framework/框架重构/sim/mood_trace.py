"""情绪轨迹报告（sim/mood_trace.py）

把影子期观测画成 ASCII 轨迹 + 统计，回答"她这段时间过得怎么样"。
输出：%PROJECT_HOME%\框架重构\08_情绪轨迹.md
"""
from __future__ import annotations

import json
import os
import time

SRC = r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_whale_beams\beams_obs.jsonl"
STATE = r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_whale_beams\beams_state.json"
OUT = r"%PROJECT_HOME%\框架重构\08_情绪轨迹.md"
BLOCKS = "▁▂▃▄▅▆▇█"


def sparkline(values):
    if not values:
        return ""
    lo, hi = min(values), max(values)
    if hi - lo < 1e-9:
        return BLOCKS[3] * len(values)
    out = []
    for v in values:
        idx = int((v - lo) / (hi - lo) * (len(BLOCKS) - 1))
        out.append(BLOCKS[max(0, min(len(BLOCKS) - 1, idx))])
    return "".join(out)


def load_rows(path):
    rows = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return rows


def main():
    rows = load_rows(SRC)
    state = {}
    try:
        with open(STATE, "r", encoding="utf-8") as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        pass

    lines = ["# 情绪轨迹（影子期）", ""]
    if not rows:
        lines.append("暂无观测数据")
    else:
        times = [time.strftime("%H:%M", time.localtime(float(r.get("ts") or 0))) for r in rows]
        mood = [float(r.get("mood") or 0.0) for r in rows]
        stress = [float(r.get("stress") or 0.0) for r in rows]
        neg = [float(r.get("neg_load") or 0.0) for r in rows]
        talk = [float(r.get("talkative") or 0.0) for r in rows]
        winners = [str(r.get("drive") or "?") for r in rows]
        lines.append("- 采样：%d 条（%s → %s）" % (len(rows), times[0], times[-1]))
        lines.append("")
        lines.append("## 轨迹（每 60 秒一点）")
        lines.append("")
        lines.append("| 量 | 轨迹 |")
        lines.append("|---|---|")
        lines.append("| 心情底色 mood | `%s` |" % sparkline(mood))
        lines.append("| 压力 stress | `%s` |" % sparkline(stress))
        lines.append("| 负性负荷 | `%s` |" % sparkline(neg))
        lines.append("| 话多 talkative | `%s` |" % sparkline(talk))
        lines.append("")
        lines.append("## 动机分布")
        lines.append("")
        dist = {}
        for w in winners:
            dist[w] = dist.get(w, 0) + 1
        total = len(winners) or 1
        for name, n in sorted(dist.items(), key=lambda kv: -kv[1]):
            bar = "█" * max(1, int(24 * n / total))
            lines.append("- %-8s %5.1f%% %s" % (name, 100.0 * n / total, bar))
        lines.append("")
        lines.append("## 当前状态")
        lines.append("")
        lines.append("- tick：%s；标签：%s" % (state.get("tick_count"), (state.get("fast") or {}).get("label")))
        slow = state.get("slow") or {}
        lines.append("- 心情 %+.3f / 压力 %.3f / 联结 %.3f / 疲劳 %.3f" % (
            float(slow.get("mood_valence") or 0), float(slow.get("stress") or 0),
            float(slow.get("bonding") or 0), float(slow.get("fatigue") or 0)))
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines[:40]))
    print("\n已写入：" + OUT)


if __name__ == "__main__":
    main()