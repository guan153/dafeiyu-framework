# -*- coding: utf-8 -*-
"""语音/主动消息闸门（voice_gate）只读适配。

背景：`whale_voice` 早就在写一个统一闸门 `voice_gate.json`，
`proactive_chat` 读它决定「这一轮到底允不允许主动开口」。
如果 v3 的出站队列另造一套策略，就会变成**两套闸门**，比没有更糟。

所以这里的原则是：
- Read-only: never write gates or consume tokens.
- Autonomous callers opt into fail-closed handling of missing, invalid or stale gates.
- Valid shadow gates remain observational; explicit outlet permissions share one contract.
- token 已在 consequence.pending 里 → 视为重复，否决。

传输仍各归各：proactive_chat 有收据与超时不重发，继续用它自己的；
我们的队列走 `context.send_message`。统一的是「能不能发」，不是「怎么发」。
"""

from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path
from typing import Any

from . import paths

VOICE_PLUGIN_NAMES = (
    "astrbot_plugin_whale_voice",
    "astrbot_plugin_voice",
    "astrbot_plugin_proactive_voice",
)


def gate_dir() -> Path | None:
    env = os.environ.get("VOICE_DATA_DIR")
    if env and Path(env).is_dir():
        return Path(env)
    base = paths.plugin_data_dir()
    for name in VOICE_PLUGIN_NAMES:
        candidate = base / name
        if candidate.is_dir():
            return candidate
    return None


def _read_json(path: Path) -> tuple[bool, Any]:
    try:
        return True, json.loads(path.read_bytes().decode("utf-8-sig"))
    except Exception:  # noqa: BLE001
        return False, None


def consequence_paths(data_dir: Path) -> list[Path]:
    found = [data_dir / n for n in ("consequence.json", "voice_consequence.json") if (data_dir / n).exists()]
    return found or [data_dir / "consequence.json"]


def owner_session(data_dir: Path | None) -> str:
    """读 voice 插件配置里的 owner_session：只有绑定会话才受闸门约束。"""
    if not data_dir:
        return ""
    cfg = data_dir.parent.parent / "config" / "astrbot_plugin_whale_voice_config.json"
    ok, data = _read_json(cfg)
    if ok and isinstance(data, dict):
        return str(data.get("owner_session") or "")
    return ""


def evaluate(
    *,
    session_id: str = "",
    outlet: str = "proactive",
    now: float | None = None,
    data_dir: Path | None = None,
    fail_closed: bool = False,
) -> dict[str, Any]:
    """Evaluate the shared gate without reserving or consuming a token.

    Args:
        session_id: Exact outgoing session; unrelated sessions retain isolation.
        outlet: Explicit outlet permission takes precedence over a general deny.
        now: Optional clock for deterministic tests.
        data_dir: Optional gate directory for isolated tests.
        fail_closed: Reject missing, invalid or stale gates for proactive sends.

    Returns:
        Content-free decision code and the parsed gate, when available.
    """
    resolved = data_dir if data_dir is not None else gate_dir()
    if resolved is None:
        return {"allow": not fail_closed, "code": "gate_missing", "gate": None}
    resolved = Path(resolved)
    bound = owner_session(resolved)
    if session_id and bound and session_id != bound:
        return {"allow": True, "code": "not_owner_session", "gate": None}

    ok, gate = _read_json(resolved / "voice_gate.json")
    if not ok or not isinstance(gate, dict):
        code = (
            "gate_corrupt"
            if (resolved / "voice_gate.json").exists()
            else "gate_missing"
        )
        return {"allow": not fail_closed, "code": code, "gate": None}
    try:
        now_f = time.time() if now is None else float(now)
        expires = float(gate.get("expires"))
    except (TypeError, ValueError, OverflowError):
        return {"allow": not fail_closed, "code": "gate_corrupt", "gate": gate}
    if not math.isfinite(expires) or not math.isfinite(now_f):
        return {"allow": not fail_closed, "code": "gate_corrupt", "gate": gate}
    if now_f >= expires:
        return {"allow": not fail_closed, "code": "gate_stale", "gate": gate}
    decision = gate.get("decision")
    if decision not in ("allow", "deny"):
        return {"allow": not fail_closed, "code": "gate_corrupt", "gate": gate}
    if gate.get("enforce") is not True:
        code = "shadow_allow" if decision == "allow" else "shadow_would_deny"
        return {"allow": True, "code": code, "gate": gate}

    outlets = gate.get("outlets")
    permitted = outlets.get(outlet) if isinstance(outlets, dict) else None
    if permitted is False:
        return {"allow": False, "code": f"outlet_deny:{outlet}", "gate": gate}
    # Outlet reason pools may bypass low desire, never quiet hours or user limits.
    if decision == "deny" and (
        permitted is not True or gate.get("code") not in ("low_want", "no_reason")
    ):
        return {"allow": False, "code": "deny", "gate": gate}
    token = gate.get("token")
    if decision == "allow" and (not isinstance(token, str) or not token.strip()):
        return {"allow": not fail_closed, "code": "token_missing", "gate": gate}
    if isinstance(token, str) and token.strip():
        for path in consequence_paths(resolved):
            if not path.exists():
                continue
            ok_c, cons = _read_json(path)
            if (
                not ok_c
                or not isinstance(cons, dict)
                or not isinstance(cons.get("pending", []), list)
            ):
                if fail_closed:
                    return {"allow": False, "code": "consequence_corrupt", "gate": gate}
                continue
            for item in cons.get("pending", []):
                if isinstance(item, dict) and item.get("token") == token:
                    return {"allow": False, "code": "duplicate_token", "gate": gate}
    return {
        "allow": True,
        "code": "outlet_allow" if permitted is True else "enforced_allow",
        "gate": gate,
    }


def describe() -> dict[str, Any]:
    """给体检/命令看的状态描述。"""
    resolved = gate_dir()
    result: dict[str, Any] = {
        "dir": str(resolved) if resolved else "",
        "exists": bool(resolved and (resolved / "voice_gate.json").exists()),
        "owner_session": owner_session(resolved),
        "decision": evaluate(),
    }
    if result["exists"]:
        ok, gate = _read_json(resolved / "voice_gate.json")
        if ok and isinstance(gate, dict):
            result["gate"] = {
                "decision": gate.get("decision"),
                "enforce": gate.get("enforce"),
                "expires_in": round(float(gate.get("expires") or 0) - time.time(), 1),
                "code": gate.get("code"),
            }
    return result
