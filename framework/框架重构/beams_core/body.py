"""BEAM-AI · 第 0 层：身体/环境接口（body.py）

AI 的"身体"不是血肉，而是：算力预算、内存、延迟、工具可用性、网络、连续运行时长、错误率。
本层只做**采样与归一化**，不做任何情绪解释。

原则：
- 采样失败一律降级为保守值，绝不抛异常打断内核；
- 所有量归一化到 0~1（延迟用对数压缩）；
- 不依赖 psutil：没有就退系统无关的保守估计（Windows 干净环境也能跑）。
"""
from __future__ import annotations

import math
import os
import sys
import time
from typing import Any, Dict, Optional

from . import schema

_PROC_START = time.time()

try:  # 可选依赖：有就用精确值，没有就降级
    import psutil  # type: ignore
    _HAS_PSUTIL = True
except Exception:  # pragma: no cover - 环境相关
    psutil = None  # type: ignore
    _HAS_PSUTIL = False


def _normalize_latency(ms: float) -> float:
    """延迟 30ms→0.05，300ms→0.5，3000ms→0.9（对数压缩，避免长尾一票否决）。"""
    v = max(1.0, schema.finite(ms, 80.0))
    return schema.clamp01(math.log10(v / 30.0) / 2.0 + 0.05)


def _safe_cpu_mem() -> tuple:
    if _HAS_PSUTIL:
        try:
            cpu = float(psutil.cpu_percent(interval=None)) / 100.0
            mem = float(psutil.virtual_memory().percent) / 100.0
            return schema.clamp01(cpu, 0.2), schema.clamp01(mem, 0.4)
        except Exception:
            pass
    return 0.2, 0.4  # 未知时给保守值，不假装健康也不假装过载


def _disk_free_ratio() -> float:
    """工作盘剩余比例（空间也是"身体"的一部分）。失败 → 0.5 中性。"""
    roots = []
    for cand in (os.environ.get("SystemDrive", "C:"), "G:"):
        if cand:
            roots.append(cand.rstrip("\\") + "\\")
    for root in roots:
        try:
            usage = os.statvfs(root)  # type: ignore[attr-defined]  # 仅 POSIX
            total = usage.f_blocks * usage.f_frsize
            free = usage.f_bavail * usage.f_frsize
            if total > 0:
                return schema.clamp01(free / total, 0.5)
        except Exception:
            continue
    if _HAS_PSUTIL:
        try:
            usage = psutil.disk_usage(os.getcwd())
            return schema.clamp01(usage.free / max(1, usage.total), 0.5)
        except Exception:
            pass
    return 0.5


def _default_tools() -> Dict[str, bool]:
    return {"net": True, "vision": True, "speak": False, "code": True}


def sample(context: Optional[Dict] = None) -> Dict:
    """采一拍身体状态。

    context 允许外部注入真实观测（比本地探测更可信）：
        latency_ms / errors_1h / tools / budget / cpu / mem
    缺省时用本地采样与保守估计。
    """
    ctx = context if isinstance(context, dict) else {}
    now = schema.now_ts()

    cpu, mem = _safe_cpu_mem()
    cpu = schema.clamp01(ctx.get("cpu", cpu), cpu)
    mem = schema.clamp01(ctx.get("mem", mem), mem)

    latency_ms = schema.clamp(ctx.get("latency_ms"), 1.0, 60000.0, 80.0)
    errors_1h = int(schema.clamp(ctx.get("errors_1h"), 0, 10000, 0))
    tools = ctx.get("tools") if isinstance(ctx.get("tools"), dict) else _default_tools()
    tools = {schema.as_text(k, 24, ""): bool(v) for k, v in tools.items()}

    uptime_s = max(0.0, now - _PROC_START)
    # 外部可覆盖磁盘余量（离线回放/单测要可复现），默认读真实磁盘
    disk = schema.clamp01(ctx.get("disk_free"), _disk_free_ratio())

    # 预算：算力/内存/延迟/磁盘 的合成"还能折腾多少"（0~1）
    load = schema.clamp01(0.45 * cpu + 0.30 * mem + 0.25 * _normalize_latency(latency_ms))
    budget = schema.clamp01(
        ctx.get("budget",
                0.92 - 0.35 * load - 0.12 * (1.0 - disk) - min(0.25, errors_1h * 0.02)),
        0.85,
    )

    return {
        "ts": now,
        "cpu": cpu,
        "mem": mem,
        "latency_ms": latency_ms,
        "tools": tools,
        "uptime_s": uptime_s,
        "errors_1h": errors_1h,
        "budget": budget,
        "load": load,
        "disk_free": disk,
    }


def body_vector(body: Dict) -> Dict[str, float]:
    """身体状态 → 低维连续向量（上行给第 1 层）。"""
    b = body if isinstance(body, dict) else {}
    tools = b.get("tools") if isinstance(b.get("tools"), dict) else {}
    tool_score = 1.0
    keys = [k for k in ("net", "vision", "speak", "code") if k in tools]
    if keys:
        tool_score = sum(1 for k in keys if tools[k]) / float(len(keys))
    return {
        "resource": schema.clamp01(b.get("budget"), 0.85),
        "load": schema.clamp01(b.get("load"), 0.30),
        "latency": schema.clamp01(_normalize_latency(schema.num(b, "latency_ms", 80.0)), 0.1),
        "error_rate": schema.clamp01(schema.num(b, "errors_1h", 0) / 20.0, 0.0),
        "tool_score": schema.clamp01(tool_score, 1.0),
    }