# -*- coding: utf-8 -*-
"""生图路由：本地画不动时，自动把元衡云端顶上去。

要治的场景（很具体）：
- ComfyUI 端口还活着（ping 通），但显存被 TTS/RVC 占了，提交就 OOM/超时；
- 或 ComfyUI 队列忙、提交被拒；
- 结果就是「本机画笔不可用」→ 她这次画不成。

正确做法不是直接失败，而是**降级到元衡**（付费但能出图），并且：
- 元衡必须真的就绪（有脚本、有 key）且今天还有额度；
- 兜底次数要有上限，别把额度一次烧光；
- 每次兜底都要留下证据（谁失败、为什么、改走了谁）。

本模块只做纯决策，不发起任何调用，便于单测。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Decision:
    use_meta: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"use_meta": self.use_meta, "reason": self.reason}


def can_fallback(
    *,
    local_ok: bool,
    local_error: str = "",
    meta_ready: bool,
    meta_remaining: int,
    fallback_enabled: bool = True,
    fallback_used_today: int = 0,
    fallback_daily_cap: int = 3,
) -> Decision:
    """本地失败时要不要用元衡兜底。

    local_ok    本地画笔当前是否可用（失败后可传 False，或说明错误）
    local_error 失败原因（仅用于记录/可读性）
    meta_ready  元衡后端是否就绪（脚本+key）
    meta_remaining 元衡今天剩余额度
    """
    if not fallback_enabled:
        return Decision(False, "fallback_disabled")
    if not meta_ready:
        return Decision(False, "meta_not_ready")
    if meta_remaining <= 0:
        return Decision(False, "meta_quota_exhausted")
    if fallback_used_today >= fallback_daily_cap:
        return Decision(False, "fallback_daily_cap")
    if local_ok and not local_error:
        return Decision(False, "local_fine")
    why = local_error or "local_unavailable"
    return Decision(True, f"fallback:{why}")


def should_retry_cloud_after_local_failure(
    *,
    engine: str,
    meta_ready: bool,
    meta_remaining: int,
    fallback_enabled: bool = True,
) -> bool:
    """已经选了 local 之后失败了，要不要立刻改走元衡。"""
    if engine != "local":
        return False
    return can_fallback(
        local_ok=False,
        local_error="local_failed",
        meta_ready=meta_ready,
        meta_remaining=meta_remaining,
        fallback_enabled=fallback_enabled,
    ).use_meta
