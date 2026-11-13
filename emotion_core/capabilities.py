# -*- coding: utf-8 -*-
"""能力注册表：她「会什么、不会什么、谁能替她做」的一张表。

用途有三：
1. 自省评分里的 `coverage` 维度（目标能力清单 vs 已实现能力）；
2. 冲突治理：同一能力多个提供者时，指定唯一 preferred，其余标 standby；
3. 对话诚实：不会的能力直接告诉她「这个我还不会」，而不是硬编一个像样的谎。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Any, Callable

# 目标能力清单：这是「理想中的她」该会的事。
# 缺哪项，就是一条能力缺口（gap），进弱点账本。
TARGET_CAPABILITIES: dict[str, str] = {
    "chat": "私聊对话",
    "memory_long": "长期记忆",
    "memory_recall": "记忆召回",
    "emotion": "情绪状态",
    "proactive": "主动消息",
    "web_search": "联网查资料",
    "self_reflect": "自省与自我改进",
}


@dataclass
class Capability:
    name: str
    label: str
    providers: list[str] = field(default_factory=list)
    preferred: str = ""
    status: str = "unknown"      # ok / degraded / down / missing / standby
    health: str = ""
    last_check: float = 0.0
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CapabilityRegistry:
    def __init__(self) -> None:
        self._caps: dict[str, Capability] = {}

    # ── 注册 ────────────────────────────────────────────────
    def declare(self, name: str, *, label: str = "", status: str = "ok") -> Capability:
        cap = self._caps.get(name)
        if cap is None:
            cap = Capability(name=name, label=label or TARGET_CAPABILITIES.get(name, name), status=status)
            self._caps[name] = cap
        return cap

    def provide(self, name: str, provider: str, *, health: str = "", preferred: bool = False) -> Capability:
        cap = self.declare(name)
        if provider not in cap.providers:
            cap.providers.append(provider)
        if preferred or not cap.preferred:
            cap.preferred = provider
        cap.status = "ok" if cap.status in ("unknown", "missing") else cap.status
        if health:
            cap.health = health
        cap.last_check = time.time()
        return cap

    def set_status(self, name: str, status: str, health: str = "") -> None:
        cap = self.declare(name)
        cap.status = status
        if health:
            cap.health = health
        cap.last_check = time.time()

    # ── 查询 ────────────────────────────────────────────────
    def get(self, name: str) -> Capability | None:
        return self._caps.get(name)

    def has(self, name: str) -> bool:
        cap = self._caps.get(name)
        return bool(cap and cap.providers and cap.status in ("ok", "degraded", "standby"))

    def conflicts(self, *, include_resolved: bool = False) -> list[dict[str, Any]]:
        """同一能力多个提供者的冲突。

        默认只报「没指定首选」的：有 preferred 说明已经仲裁过，多实现是备份不是冲突。
        """
        out = []
        for cap in self._caps.values():
            if len(cap.providers) > 1:
                resolved = bool(cap.preferred and cap.preferred in cap.providers)
                if resolved and not include_resolved:
                    continue
                out.append({
                    "capability": cap.name,
                    "providers": list(cap.providers),
                    "preferred": cap.preferred,
                    "resolved": resolved,
                })
        return out

    def gaps(self) -> list[dict[str, str]]:
        """目标清单里有、但没人提供的能力。"""
        gaps = []
        for name, label in TARGET_CAPABILITIES.items():
            cap = self._caps.get(name)
            if not cap or not cap.providers or cap.status == "missing":
                gaps.append({"capability": name, "label": label})
        return gaps

    def report(self) -> dict[str, Any]:
        total = len(TARGET_CAPABILITIES)
        covered = sum(1 for name in TARGET_CAPABILITIES if self.has(name))
        return {
            "coverage": round(covered / total, 3) if total else 0.0,
            "covered": covered,
            "total": total,
            "capabilities": {name: cap.to_dict() for name, cap in self._caps.items()},
            "gaps": self.gaps(),
            "conflicts": self.conflicts(),
        }


_REGISTRY: CapabilityRegistry | None = None


def get_registry() -> CapabilityRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = CapabilityRegistry()
    return _REGISTRY
