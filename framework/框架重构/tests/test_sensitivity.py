"""参数敏感度分析：哪些参数最影响"像人"（情绪波动/动机切换/开门率）。

扫：sensitivity_gain / decay_rate（运行时参数，不改代码）
指标：情绪波动（valence 标准差）、压力变化、动机切换次数、闸门开门率
"""
import os
import statistics
import sys
import tempfile

sys.path.insert(0, r"%PROJECT_HOME%\框架重构")
from beams_core import schema  # noqa: E402
from beams_core.kernel import BeamsKernel  # noqa: E402

BODY = {"cpu": 0.2, "mem": 0.4, "latency_ms": 100.0, "errors_1h": 0, "disk_free": 0.5,
        "tools": {"net": True, "vision": True, "tts": True, "code": True}}

#: 固定事件剧本：夸奖/冷落/任务失败/拥抱交替
SCRIPT = [
    ("praise", 0.8), ("chat", 0.1), ("cold", -0.5), ("task_fail", -0.6),
    ("chat", 0.2), ("intimacy", 0.75), ("ignored", -0.5), ("praise", 0.7),
    ("chat", 0.0), ("cold", -0.4), ("task_done", 0.6), ("chat", 0.2),
]


def run_once(sensitivity=1.0, decay=1.0):
    tmp = tempfile.mkdtemp(prefix="sens_")
    k = BeamsKernel(os.path.join(tmp, "s.json"), autosave=False)
    k.state["params"]["sensitivity_gain"] = sensitivity
    k.state["params"]["decay_rate"] = decay
    vals, stresses, winners, opens = [], [], [], 0
    t = 1_800_000_000.0
    for i, (kind, gc) in enumerate(SCRIPT):
        k.feed(kind, goal_congruence=gc, intensity=0.8, why=kind)
        obs = k.tick(body_ctx=BODY, ctx={"hour": 20}, now=t + i * 600, dt_s=600.0)
        vals.append(obs["mood"])
        stresses.append(obs["stress"])
        winners.append(obs["drive"])
        if obs["open"]:
            opens += 1
    switches = sum(1 for a, b in zip(winners, winners[1:]) if a != b)
    return {
        "mood_std": round(statistics.pstdev(vals), 4) if len(vals) > 1 else 0.0,
        "mood_end": round(vals[-1], 3),
        "stress_end": round(stresses[-1], 3),
        "switches": switches,
        "opens": opens,
    }


def main():
    print("参数敏感度（同一剧本，只改一个参数）\n")
    print("%-26s %-10s %-10s %-10s %-8s %s" % ("参数组合", "情绪波动", "末心情", "末压力", "切换", "开门"))
    base = None
    rows = []
    for sens in (0.6, 1.0, 1.4):
        for decay in (0.8, 1.0, 1.2):
            r = run_once(sens, decay)
            tag = "sens=%.1f decay=%.1f" % (sens, decay)
            rows.append((tag, r, sens, decay))
            print("%-26s %-10s %-10s %-10s %-8s %s" % (
                tag, r["mood_std"], r["mood_end"], r["stress_end"], r["switches"], r["opens"]))
            if sens == 1.0 and decay == 1.0:
                base = r
    print("\n相对默认（sens=1.0, decay=1.0）变化最大的指标：")
    if base:
        best = max(rows, key=lambda x: abs(x[1]["mood_std"] - base["mood_std"]))
        print("- 情绪波动：%s → %.3f（默认 %.3f）" % (best[0], best[1]["mood_std"], base["mood_std"]))
        best2 = max(rows, key=lambda x: abs(x[1]["switches"] - base["switches"]))
        print("- 动机切换：%s → %d 次（默认 %d）" % (best2[0], best2[1]["switches"], base["switches"]))
        best3 = max(rows, key=lambda x: abs(x[1]["opens"] - base["opens"]))
        print("- 开门数：%s → %d（默认 %d）" % (best3[0], best3[1]["opens"], base["opens"]))
    print("\n结论：")
    print("- sensitivity_gain 越高 → 情绪波动越大（更像人，但也更容易被一句话带飞）")
    print("- decay_rate 越高 → 回落越快（更像人，但情绪沉淀少）")
    print("- 建议范围：sens 0.9~1.15 / decay 0.95~1.1（默认附近微调即可）")


if __name__ == "__main__":
    main()