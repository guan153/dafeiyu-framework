# -*- coding: utf-8 -*-
"""并发锁自测：同时起两个 agent_bridge，看第二个是否排队。"""
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(r"%PROJECT_HOME%\母女聊天室")
BRIDGE = HERE / "agent_bridge.py"
PY = sys.executable

tasks = [
    "读 %PROJECT_HOME%\\母女聊天室\\data\\tasks.json，数一下 status=done 的条数，只回数字。",
    "读 %PROJECT_HOME%\\母女聊天室\\data\\train_log.jsonl 最后一行，只回时间字段。",
]

procs = []
t0 = time.time()
for t in tasks:
    p = subprocess.Popen(
        [PY, str(BRIDGE), "--text", t, "--json"],
        cwd=str(HERE), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace")
    procs.append(p)

for i, p in enumerate(procs, 1):
    out, _ = p.communicate()
    print(f"[{i}] 完成于 {time.time() - t0:.1f}s: {out.strip()[:220]}")

print(f"wall {time.time() - t0:.1f}s")
print("lock exists:", (Path(r"%USERPROFILE%\.dsh\bridge\agent.lock")).exists())
