# -*- coding: utf-8 -*-
"""她的生存率巡检。

五套东西里任何一套挂了，她就从你的世界消失；而 QQ 不重放消息。
这个脚本只做一件事：**发现谁不在了**，并写进时间线。

默认只报警、不重启（重启必须主人点头）。
需要自动拉起时加 --restart（会调 deploy/启动脚本）。

跑法：
    python tools/watchdog.py            # 巡检一次
    python tools/watchdog.py --json     # 机器可读
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from whale_core import timeline  # noqa: E402

#: 关键服务：名字 / 端口 / 挂了算不算致命
SERVICES = [
    ("AstrBot", 6185, True),
    ("AstrBot-ws", 6199, True),
    ("BEAM网关", 8920, True),
    ("SnowLuma", 5099, True),
    ("SnowLuma-web", 3000, False),
    ("laya分类", 8901, False),
    ("QwenEmo", 8914, False),
    ("ASR", 8913, False),
    ("TTS", 8912, False),
    ("声音事件", 8916, False),
]

#: 进程名片段（Windows）
#: 名字 → (匹配的命令行片段, 进程名)
PROCS = {
    "SnowLuma": ("SnowLuma", "node.exe"),
    "ComfyUI": ("ComfyUI\\main.py", "python.exe"),
    "BEAM网关": ("beam_gateway.py", "python.exe"),
}


def port_up(p: int, timeout: float = 0.6) -> bool:
    s = socket.socket()
    s.settimeout(timeout)
    try:
        return s.connect_ex(("127.0.0.1", p)) == 0
    finally:
        s.close()


def proc_running(marker: str, exe: str = "python.exe") -> bool:
    """按「进程名 + 命令行」找——只匹配指定 exe，避免把 powershell 自己算进去。"""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"@(Get-CimInstance Win32_Process | Where-Object {{ $_.Name -eq '{exe}' -and $_.CommandLine -like '*{marker}*' }}).Count"],
            capture_output=True, text=True, timeout=25,
        )
        return int((out.stdout or "0").strip() or 0) > 0
    except Exception:
        return False


def check() -> dict:
    down = []
    for name, port, fatal in SERVICES:
        if not port_up(port):
            down.append({"name": name, "port": port, "fatal": fatal})
    procs = {k: proc_running(m, exe) for k, (m, exe) in PROCS.items()}
    return {
        "at": datetime.now().strftime("%H:%M:%S"),
        "down": down,
        "fatal_down": [d for d in down if d["fatal"]],
        "procs": procs,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    r = check()
    if r["fatal_down"]:
        timeline.record("watchdog", "down", services=",".join(d["name"] for d in r["fatal_down"]))
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=1))
    else:
        print(f"[{r['at']}] 巡检")
        if not r["down"]:
            print("  全部在线 ✅")
        for d in r["down"]:
            print(f"  {'✖ 致命' if d['fatal'] else '· 可缺'} {d['name']}（{d['port']}）不在")
        for k, v in r["procs"].items():
            print(f"  进程 {k}: {'在' if v else '不在'}")
    return 1 if r["fatal_down"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
