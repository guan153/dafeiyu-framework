# -*- coding: utf-8 -*-
"""生图 / 语音 两个独立开关（一键开关，带状态文件）。

为什么要两个：8G 显存只够一个 GPU 服务。
- 生图（ComfyUI 8188）占显存时，TTS（8912）必须让位；
- 反过来也一样。

所以这里不是两个互不相干的开关，而是**两个意图**：
    image on  → 切到 draw（ComfyUI 起来，TTS 停）
    image off → 切到 speak（TTS 起来，ComfyUI 停）；若语音也关着 → free
    voice on  → 切到 speak（TTS 起来，ComfyUI 停）
    voice off → 切到 draw（如果生图开着）或 free

状态写到 `audition/gateway/toggles.json`，供 capability_manager 的自动守护读取，
避免「关了又自己起来」。

用法：
    python toggles.py image on|off|status
    python toggles.py voice on|off|status
    python toggles.py status
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

GATEWAY = Path(r"%PROJECT_HOME%\audition\gateway")
STATE_FILE = GATEWAY / "toggles.json"
CAP_URL = "http://127.0.0.1:8921"
COMFY_PORT = 8188
TTS_PORT = 8912


# ── 状态 ────────────────────────────────────────────────────
def read_state() -> dict:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8-sig"))
        return {
            "image": bool(data.get("image", False)),
            "voice": bool(data.get("voice", True)),
            "updated": float(data.get("updated") or 0),
        }
    except Exception:  # noqa: BLE001
        return {"image": False, "voice": True, "updated": 0.0}


def write_state(*, image: bool, voice: bool) -> None:
    payload = {"image": bool(image), "voice": bool(voice), "updated": time.time()}
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, STATE_FILE)


# ── 能力调度器 ──────────────────────────────────────────────
def cap_status(timeout: float = 5.0) -> dict:
    try:
        with urllib.request.urlopen(f"{CAP_URL}/status", timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def switch(cap: str, timeout: float = 200.0) -> dict:
    body = json.dumps({"cap": cap}).encode("utf-8")
    req = urllib.request.Request(
        f"{CAP_URL}/switch", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def port_up(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex((host, port)) == 0
    finally:
        s.close()


def kill_port(port: int) -> int:
    """兜底：直接杀掉监听该端口的进程（调度器不听话时用）。"""
    killed = 0
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=20).stdout
        pids = set()
        for line in out.splitlines():
            if f":{port}" in line and "LISTENING" in line:
                parts = line.split()
                if parts and parts[-1].isdigit():
                    pids.add(parts[-1])
        for pid in pids:
            subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True, timeout=20)
            killed += 1
    except Exception:  # noqa: BLE001
        pass
    return killed


def wait_ready(port: int, seconds: float = 120.0) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if port_up(port):
            return True
        time.sleep(1)
    return False


# ── 应用意图 ────────────────────────────────────────────────
def apply(image: bool, voice: bool) -> dict:
    """按两个意图切 GPU 状态。互斥时以「开着的那个」为准。

    注意顺序：**先落盘意图，再切服务**。
    否则守护进程可能在切换途中读到旧状态，把开关又切回去（实战踩过）。
    """
    if image and voice:
        voice = False  # 显存只够一个，开生图就必然停语音
    target = "draw" if image else ("speak" if voice else "free")
    write_state(image=image, voice=voice)
    result = switch(target)
    if target == "draw":
        wait_ready(COMFY_PORT, 120)
    elif target == "speak":
        wait_ready(TTS_PORT, 120)
    # 切换失败就把意图回滚成实际状态，别让文件骗人
    if not result.get("ok", True) and not port_up(COMFY_PORT if image else TTS_PORT):
        write_state(image=not image if image else image, voice=voice)
    return {"target": target, "result": result}


def snapshot() -> str:
    st = read_state()
    cap = cap_status()
    services = cap.get("services") or {}
    draw_up = bool((services.get("draw") or {}).get("up")) or port_up(COMFY_PORT)
    tts_up = bool((services.get("tts") or {}).get("up")) or port_up(TTS_PORT)
    state = {
        "生图": "开" if st["image"] else "关",
        "语音/唱歌": "开" if st["voice"] else "关",
        "GPU 当前": cap.get("current") or "?",
        "ComfyUI(8188)": "UP" if draw_up else "DOWN",
        "TTS(8912)": "UP" if tts_up else "DOWN",
    }
    line = "｜".join(f"{k}={v}" for k, v in state.items())
    if (st["image"] and not draw_up) or (st["voice"] and not tts_up):
        line += "｜⚠ 状态与实际不一致，可运行 sync"
    return line


def sync_state() -> str:
    """按实际端口校准状态文件（防漂移）。"""
    cap = cap_status()
    services = cap.get("services") or {}
    image = bool((services.get("draw") or {}).get("up")) or port_up(COMFY_PORT)
    voice = bool((services.get("tts") or {}).get("up")) or port_up(TTS_PORT)
    write_state(image=image, voice=voice)
    return snapshot()


def main(argv: list[str]) -> int:
    if not argv:
        print("用法：toggles.py image|voice|status [on|off]")
        return 2
    cmd = argv[0].lower()
    action = argv[1].lower() if len(argv) > 1 else "status"

    if cmd == "status":
        print(snapshot())
        return 0

    if cmd == "sync":
        print(sync_state())
        return 0

    st = read_state()
    if cmd in ("image", "voice") and action in ("on", "off"):
        if not cap_status():
            if cmd == "voice" and action == "off":
                write_state(image=False, voice=False)
                print("已记录：生图/语音全关（能力调度器没开，不会有服务自己起）。")
                return 0
            print("能力调度器（8921）没开，先运行「QQ机器人-开-聊天only.bat」，再切这个开关。")
            return 1
    if cmd == "image":
        if action == "on":
            apply(image=True, voice=False)
        elif action == "off":
            # 关生图 = 把语音还回来（她的常态是能说话）
            apply(image=False, voice=True)
        else:
            print(snapshot())
            return 0
    elif cmd == "voice":
        if action == "on":
            apply(image=False, voice=True)
        elif action == "off":
            # 关语音 = 显存全放掉（生图也不会被顺手拉起来）
            apply(image=False, voice=False)
        else:
            print(snapshot())
            return 0
    else:
        print(f"未知命令：{cmd}")
        return 2

    # 关闭时兜底：如果调度器没停掉，直接杀端口
    if cmd == "image" and action == "off" and not read_state()["image"]:
        if port_up(COMFY_PORT):
            kill_port(COMFY_PORT)
    if cmd == "voice" and action == "off" and not read_state()["voice"]:
        if port_up(TTS_PORT):
            kill_port(TTS_PORT)

    print(snapshot())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
