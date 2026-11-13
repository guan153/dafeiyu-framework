# -*- coding: utf-8 -*-
"""一次性补丁：能力调度器加「draw 闲置自动切回 speak」。

问题：画兴触发 `switch("draw")` 会停掉 TTS（显存互斥），但切过去之后**再也没有切回来**。
2026-11-07 17:46 就是如此：她的语音哑了一个多小时，直到人工发现。

修法：auto-guard 每 30 秒检查，如果
- 当前是 draw，
- ComfyUI 在线且队列为空，
- 距离切到 draw 已超过 DRAW_IDLE_RESTORE_SEC（默认 600 秒），
就自动 `switch("speak")` 把语音恢复。

幂等：已含 `DRAW_IDLE_RESTORE_SEC` 就跳过。
"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

TARGET = Path(r"%PROJECT_HOME%\audition\gateway\capability_manager.py")
MARK = "DRAW_IDLE_RESTORE_SEC"

OLD_CONST = "_SWITCH_LOCK = None  # 延迟创建：切换串行化"
NEW_CONST = '''_SWITCH_LOCK = None  # 延迟创建：切换串行化
# [2026-11-07 自动恢复] draw 闲置多久后自动切回 speak。
# 背景：切 draw 会停 TTS（显存互斥），而原来切过去就不回来了，语音会一直哑。
DRAW_IDLE_RESTORE_SEC = float(__import__("os").environ.get("DRAW_IDLE_RESTORE_SEC", "600"))
_LAST_SWITCH_TS = 0.0
'''

OLD_LOAD = '''    v = json.load(fh).get("current")'''
NEW_LOAD = '''    v = json.load(fh).get("current")'''

OLD_SWITCH_SIG = "def switch(cap):"
NEW_SWITCH_SIG = "def switch(cap):\n    global _LAST_SWITCH_TS"

OLD_SWITCH_TS = '''    _STATE["current"] = cap
    _save_state()
    log("switch -> %s" % cap)'''
NEW_SWITCH_TS = '''    _STATE["current"] = cap
    _save_state()
    _LAST_SWITCH_TS = time.time()
    log("switch -> %s" % cap)'''

OLD_LOAD_TS = '''    v = json.load(fh).get("current")'''
NEW_LOAD_TS = '''    data = json.load(fh)
    v = data.get("current")
    if v == "draw":
        globals()["_LAST_SWITCH_TS"] = float(data.get("ts") or 0.0)'''

OLD_GUARD = '''            if _STATE.get("current") != "draw":
                continue
            if port_up(8188):
                continue
            log("auto-guard: draw 激活但 8188 不在，重启 ComfyUI")
            start_service("draw")
'''
NEW_GUARD = '''            if _STATE.get("current") != "draw":
                continue
            if not port_up(8188):
                log("auto-guard: draw 激活但 8188 不在，重启 ComfyUI")
                start_service("draw")
                continue
            # [2026-11-07] draw 闲置自动切回 speak，别再让她的语音哑着
            idle = time.time() - float(_LAST_SWITCH_TS or 0)
            if _LAST_SWITCH_TS and idle > DRAW_IDLE_RESTORE_SEC and _comfy_queue_empty():
                log("auto-guard: draw 已闲置 %d 秒且队列空闲，自动切回 speak" % int(idle))
                try:
                    switch("speak")
                except Exception as exc:  # noqa: BLE001
                    log("auto-guard: 自动切回 speak 失败：%s" % exc)
'''

OLD_HELPER = '''def vram():'''
NEW_HELPER = '''def _comfy_queue_empty():
    """ComfyUI 队列是否空闲（拿不到就按「不空闲」处理，宁可不切）。"""
    try:
        with urllib.request.urlopen("http://127.0.0.1:8188/queue", timeout=4) as r:
            data = json.loads(r.read().decode("utf-8"))
        running = data.get("queue_running") or []
        pending = data.get("queue_pending") or []
        return not running and not pending
    except Exception:
        return False


def vram():'''


def main() -> int:
    raw = TARGET.read_text(encoding="utf-8")
    if MARK in raw:
        print("已经打过补丁，跳过")
        return 0

    text = raw
    for name, old, new in (
        ("const", OLD_CONST, NEW_CONST),
        ("switch_global", OLD_SWITCH_SIG, NEW_SWITCH_SIG),
        ("switch_ts", OLD_SWITCH_TS, NEW_SWITCH_TS),
        ("load_ts", OLD_LOAD_TS, NEW_LOAD_TS),
        ("helper", OLD_HELPER, NEW_HELPER),
        ("guard", OLD_GUARD, NEW_GUARD),
    ):
        if old not in text:
            raise SystemExit(f"锚点没找到：{name}")
        text = text.replace(old, new, 1)

    backup = TARGET.with_suffix(f".bak-idle-{time.strftime('%Y%m%d-%H%M%S')}.py")
    shutil.copy2(TARGET, backup)
    TARGET.write_text(text, encoding="utf-8")
    print(f"已备份：{backup.name}")
    print("已应用：const / switch_global / helper / guard")
    return 0


if __name__ == "__main__":
    sys.exit(main())
