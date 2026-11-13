"""单文件版 vs 模块版行为一致性测试。

同一串事件 + 同样拍数，两版推进出来的状态应该几乎完全一致。
"""
import os
import sys
import tempfile

sys.path.insert(0, r"%PROJECT_HOME%\框架重构")
sys.path.insert(0, r"%ASTRBOT_HOME%\data\plugins")

import beams  # noqa: E402
from beams_core.kernel import BeamsKernel  # noqa: E402

BODY = {"cpu": 0.2, "mem": 0.4, "latency_ms": 100.0, "errors_1h": 0,
        "disk_free": 0.5, "tools": {"net": True, "vision": True, "tts": False, "code": True}}
CTX = {"hour": 12, "pending_tasks": 0.0}

SCRIPT = [
    [("chat", {"goal_congruence": 0.2, "intensity": 0.6})],
    [],
    [("praise", {"goal_congruence": 0.8, "intensity": 0.9})],
    [],
    [("cold", {"goal_congruence": -0.5, "intensity": 0.8})],
    [],
    [("task_fail", {"goal_congruence": -0.6, "intensity": 0.9})],
    [],
    [("error", {"goal_congruence": -0.4, "intensity": 0.7})],
    [],
]


def run(kernel, events_per_tick, total_ticks=10):
    for i in range(total_ticks):
        for kind, kw in events_per_tick[i % len(events_per_tick)]:
            kernel.feed(kind, **kw)
        kernel.tick(body_ctx=BODY, ctx=CTX, dt_s=5.0)


def states_close(a, b, path="", tol=1e-6):
    diffs = []
    for key in ("valence", "arousal", "dominance"):
        da = float(a.get(key) or 0.0)
        db = float(b.get(key) or 0.0)
        if abs(da - db) > tol:
            diffs.append("%s.%s: %.6f vs %.6f" % (path, key, da, db))
    return diffs


def main():
    tmp = tempfile.mkdtemp(prefix="single_vs_mod_")
    mod = BeamsKernel(os.path.join(tmp, "mod.json"), autosave=False)
    single = beams.Kernel(os.path.join(tmp, "single.json"), autosave=False)
    run(mod, SCRIPT)
    run(single, SCRIPT)

    diffs = []
    diffs += states_close(mod.state["fast"], single.state["fast"], "fast", 1e-4)
    diffs += states_close(mod.state["slow"], single.state["slow"], "slow", 1e-4)
    for key in ("mood_valence", "stress", "bonding", "fatigue"):
        if abs(float(mod.state["slow"][key]) - float(single.state["slow"][key])) > 1e-4:
            diffs.append("slow.%s: %.6f vs %.6f" % (key, mod.state["slow"][key], single.state["slow"][key]))
    if mod.state["arbitration"]["winner"] != single.state["arbitration"]["winner"]:
        diffs.append("winner: %s vs %s" % (mod.state["arbitration"]["winner"], single.state["arbitration"]["winner"]))

    mod_obs = mod.observation()
    single_obs = single.observation()
    if mod_obs["label"] != single_obs["label"]:
        diffs.append("label: %s vs %s" % (mod_obs["label"], single_obs["label"]))
    mind_len = len(single.render())
    lite_len = len(single.render_lite())
    if not (mind_len <= 700 and lite_len <= 240):
        diffs.append("render 超预算: mind=%d lite=%d" % (mind_len, lite_len))

    print("模块版: label=%s drive=%s mood=%.4f stress=%.4f" % (
        mod_obs["label"], mod_obs["drive"], mod.state["slow"]["mood_valence"], mod.state["slow"]["stress"]))
    print("单文件: label=%s drive=%s mood=%.4f stress=%.4f" % (
        single_obs["label"], single_obs["drive"], single.state["slow"]["mood_valence"], single.state["slow"]["stress"]))
    print("mind=%d 字, lite=%d 字" % (mind_len, lite_len))
    if diffs:
        print("\n差异：")
        for d in diffs:
            print("  -", d)
        raise SystemExit(1)
    print("\nOK：两版行为一致（容差 1e-4）")


if __name__ == "__main__":
    main()