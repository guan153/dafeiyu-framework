"""BEAM-AI 影子期 watchdog（sim/watch_shadow.py）

S0 阶段每 10 分钟跑一次，回答三个问题：
1) 内核还活着吗（最后 tick 距今多少秒）？
2) 状态数值健康吗（有没有 NaN/越界）？
3) 和旧内核对照得怎么样（标签一致性/效价方向一致性）？

只读，不改任何线上状态。用法：
    python watch_shadow.py            # 追加一条观测到 04_影子观测.md
    python watch_shadow.py --report   # 只打印不写文件
"""
from __future__ import annotations

import json
import os
import sys
import time

DATA = r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_whale_beams"
OUT = r"%PROJECT_HOME%\框架重构\04_影子观测.md"
STATE = os.path.join(DATA, "beams_state.json")
OBS = os.path.join(DATA, "beams_obs.jsonl")
CMP = os.path.join(DATA, "beams_compare.jsonl")


def _read_jsonl(path, limit=200):
    rows = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        continue
    except OSError:
        pass
    return rows[-limit:]


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def main():
    report_only = "--report" in sys.argv
    now = time.time()
    st = _read_json(STATE)
    obs = _read_jsonl(OBS)
    cmp_rows = _read_jsonl(CMP)

    alive_gap = now - float(st.get("ts") or 0.0)
    tick_count = int(st.get("tick_count") or 0)
    fast = st.get("fast") or {}
    slow = st.get("slow") or {}
    arb = st.get("arbitration") or {}
    safety = st.get("safety") or {}

    # 数值健康
    problems = []
    for name, lo, hi, val in (
        ("fast.valence", -1.0, 1.0, fast.get("valence")),
        ("fast.arousal", 0.0, 1.0, fast.get("arousal")),
        ("slow.mood_valence", -1.0, 1.0, slow.get("mood_valence")),
        ("slow.stress", 0.0, 1.0, slow.get("stress")),
        ("safety.neg_load", 0.0, 1.0, safety.get("neg_load")),
    ):
        try:
            v = float(val)
        except (TypeError, ValueError):
            problems.append("%s 非数字" % name)
            continue
        if v != v:
            problems.append("%s NaN" % name)
        elif not (lo <= v <= hi):
            problems.append("%s 越界 %.3f" % (name, v))

    # 对照一致性（最近 20 条）：新旧标签/效价方向是否同向
    same_dir = 0
    total = 0
    for row in cmp_rows[-20:]:
        legacy = (row.get("legacy") or {}).get("valence")
        beams = (row.get("beams") or {}).get("fast_v")
        try:
            lv, bv = float(legacy), float(beams)
        except (TypeError, ValueError):
            continue
        total += 1
        if (lv >= 0 and bv >= 0) or (lv < 0 and bv < 0):
            same_dir += 1
    agree = (same_dir / total) if total else 0.0

    lines = []
    lines.append("## %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    lines.append("")
    lines.append("- tick_count：%d（最后心跳 %.1f 秒前 → %s）" % (
        tick_count, alive_gap, "存活" if alive_gap < 30 else "**失联**"))
    lines.append("- 此刻：%s / 最想：%s（%.2f）/ 心情 %+.2f 压力 %.2f 负性负荷 %.2f%s" % (
        fast.get("label"), arb.get("winner"), float(arb.get("urgency") or 0.0),
        float(slow.get("mood_valence") or 0.0), float(slow.get("stress") or 0.0),
        float(safety.get("neg_load") or 0.0),
        "（恢复期）" if safety.get("isolation") else ""))
    lines.append("- 与旧内核效价同向率（最近 %d 条）：%.0f%%" % (total, agree * 100))
    lines.append("- 数值健康：%s" % ("通过" if not problems else "；".join(problems)))
    lines.append("- 观测流水条数：%d；对照表条数：%d" % (len(obs), len(cmp_rows)))
    lines.append("")

    text = "\n".join(lines)
    print(text)
    if report_only:
        return
    try:
        with open(OUT, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")
    except OSError as exc:
        print("写报告失败：%s" % exc)


if __name__ == "__main__":
    main()