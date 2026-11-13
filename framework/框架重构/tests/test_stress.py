"""压力测试：3000 拍 + 随机事件，检查数值稳定/有界/性能。

用法：python test_stress.py
"""
import os
import random
import sys
import tempfile
import time

sys.path.insert(0, r"%PROJECT_HOME%\框架重构")
from beams_core import schema  # noqa: E402
from beams_core.kernel import BeamsKernel  # noqa: E402

BODY = {"cpu": 0.2, "mem": 0.4, "latency_ms": 100.0, "errors_1h": 0, "disk_free": 0.5,
        "tools": {"net": True, "vision": True, "tts": True, "code": True}}
KINDS = ["chat", "praise", "cold", "ignored", "error", "task_done", "task_fail",
         "intimacy", "video", "idle", "self", "speech", "memory_recall"]


def main():
    random.seed(42)
    tmp = tempfile.mkdtemp(prefix="stress_")
    k = BeamsKernel(os.path.join(tmp, "s.json"), autosave=False)
    t = 1_800_000_000.0
    n = 3000
    t0 = time.time()
    problems = []
    for i in range(n):
        if random.random() < 0.35:
            k.feed(random.choice(KINDS), goal_congruence=random.uniform(-1, 1),
                   intensity=random.uniform(0.2, 1.0), why="stress")
        obs = k.tick(body_ctx=BODY, ctx={"hour": (i // 120) % 24, "pending_tasks": random.randint(0, 3)},
                     now=t + i * 5.0, dt_s=5.0)
        if not (-1.0 <= obs["mood"] <= 1.0):
            problems.append("mood 越界 @%d: %s" % (i, obs["mood"]))
        if not (0.0 <= obs["stress"] <= 1.0):
            problems.append("stress 越界 @%d: %s" % (i, obs["stress"]))
        if not (0.0 <= obs["neg_load"] <= 1.0):
            problems.append("neg_load 越界 @%d: %s" % (i, obs["neg_load"]))
        if obs["drive"] not in schema.DRIVE_NAMES:
            problems.append("winner 非法 @%d: %s" % (i, obs["drive"]))
        if len(problems) > 5:
            break
    elapsed = time.time() - t0
    import json as _json
    state_kb = len(_json.dumps(k.state, ensure_ascii=False)) / 1024.0
    print("压测：%d 拍（35%% 概率随机事件）" % n)
    print("耗时：%.2fs（%.2fs/千拍）" % (elapsed, elapsed / n * 1000))
    print("状态大小：%.1f KB（内存形态）" % state_kb)
    print("问题数：%d" % len(problems))
    for p in problems[:5]:
        print("  -", p)
    if not problems:
        print("\nOK：3000 拍无越界、无 NaN、无非法 winner（长期稳定性通过）")
    else:
        print("\nFAIL：存在越界问题")


if __name__ == "__main__":
    main()