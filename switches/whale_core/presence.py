# -*- coding: utf-8 -*-
"""她知道自己"刚才不在"。

问题：AstrBot / SnowLuma 一重启或一崩，她就换个进程从零开始，
而在她看来**世界从没断过**。你在这期间说的话，她一句都不知道，
却还是照常接话——这是最像"假人"的一刻。

做法：每次加载时看一眼上次心跳，算出空档；
空档超过阈值就记一条 gap 事件，让脸层在下一轮告诉她"你刚断了一会儿"。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

DATA = Path(r"%ASTRBOT_HOME%\data")
HB = DATA / "plugin_data" / "whale_core" / "presence.json"

__all__ = ["note_boot", "touch", "last_gap", "render_hint"]


def _read() -> dict:
    try:
        return json.loads(HB.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write(d: dict) -> None:
    try:
        HB.parent.mkdir(parents=True, exist_ok=True)
        tmp = HB.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        tmp.replace(HB)
    except Exception:
        pass


def touch(ts: float | None = None) -> None:
    """心跳：她还活着。"""
    d = _read()
    d["last_seen"] = ts or time.time()
    _write(d)


def note_boot(threshold_min: float = 20.0) -> float:
    """加载时调用：返回这次断档多少分钟（0 = 首次或没断）。"""
    now = time.time()
    d = _read()
    last = float(d.get("last_seen") or 0)
    gap_min = (now - last) / 60.0 if last else 0.0
    d["last_seen"] = now
    d["boot_at"] = now
    d["last_gap_min"] = round(gap_min, 1)
    _write(d)
    if gap_min >= threshold_min:
        try:
            import sys

            if str(DATA) not in sys.path:
                sys.path.insert(0, str(DATA))
            from whale_core import timeline

            timeline.record("presence", "gap", gap_min=round(gap_min, 1))
        except Exception:
            pass
    return gap_min


def last_gap() -> float:
    return float(_read().get("last_gap_min") or 0.0)


def render_hint(gap_min: float) -> str:
    """给脸层用的一句话。别让她编造断档期间发生的事。"""
    if gap_min < 20:
        return ""
    m = int(gap_min)
    human = f"{m} 分钟" if m < 90 else f"{m // 60} 小时 {m % 60} 分钟"
    return (f"（刚才你不在，约 {human}。这期间主人说的话你没听到——不要装作知道；"
            f"如果他提到别的事，可以自然地问一句。）")
