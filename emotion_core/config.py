# -*- coding: utf-8 -*-
"""统一配置：默认值写死在代码里，覆盖值放 core_config.json。

好处：换插件配置不会因为 AstrBot 的配置文件读写而漂移；
坏处：要记住两处。所以这里提供 `explain()` 告诉你每个值从哪来。
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

from . import paths

DEFAULTS: dict[str, Any] = {
    "core": {
        "enabled": True,
        "start_outbound": True,
        "log_level": "INFO",
    },
    "outbound": {
        "min_interval_sec": 90,
        "per_source_interval": 300,
        "daily_cap": 40,
        "quiet_hours": [0, 7],
        "dedupe_window_sec": 1800,
        "max_attempts": 3,
    },
    "introspect": {
        "enabled": True,
        "interval_min": 15,
        "log_window_min": 30,
        "inject_enable": True,
        "inject_budget": 320,
        "inject_top": 4,
        "auto_propose": True,
        "propose_min_score": 18.0,
        "auto_apply_low_risk": False,
        "llm_explain": False,
        "owner_umo": "",
    },
    "probes": {
        "astrbot.webui": {"kind": "tcp", "target": ["127.0.0.1", 6185], "critical": True},
        "astrbot.ws": {"kind": "tcp", "target": ["127.0.0.1", 6199], "critical": True},
        "onebot.http": {"kind": "tcp", "target": ["127.0.0.1", 3000], "critical": True},
        "beam.gateway": {"kind": "http", "target": "http://127.0.0.1:8920/health", "critical": True},
    },
    "inject": {
        # Limit only known temporary plugin blocks, never user or system content.
        "cap_chars": 3000,
        "enforce_budget": True,
        "keep_turns": 200,
    },
    "memory_inject": {
        # 统一召回网关：向量粗召回 + LLM 重排。
        # 依据 docs/重排实验.md：纯向量 0.65 精度 43.5%，加重排后 80.0%（25 条判定）。
        # 重排失败时回退到 score>=0.70（极严），宁可不想起，不可想起错的。
        "enable": True,
        "mode": "vector_rerank",
        "coarse_score": 0.50,
        "min_score": 0.65,
        "rerank_timeout": 6.0,
        "limit": 2,
        "budget": 300,
        "min_matched": 1,
        "index_refresh_hours": 6,
    },
}

_CACHE: tuple[float, dict[str, Any]] | None = None


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load(force: bool = False) -> dict[str, Any]:
    """读取配置（60 秒缓存，改完不用重启也能见效）。"""
    global _CACHE
    path = paths.config_json()
    mtime = path.stat().st_mtime if path.exists() else 0.0
    if not force and _CACHE and _CACHE[0] == mtime:
        return copy.deepcopy(_CACHE[1])

    override: dict[str, Any] = {}
    if path.exists():
        try:
            override = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 —— 配置坏了也要能起得来
            override = {}
    merged = _deep_merge(DEFAULTS, override if isinstance(override, dict) else {})
    _CACHE = (mtime, merged)
    return copy.deepcopy(merged)


def section(name: str) -> dict[str, Any]:
    return load().get(name, {}) or {}


def save(data: dict[str, Any]) -> Path:
    """写回覆盖配置（只写差异，保持文件干净）。"""
    path = paths.config_json()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path


def set_value(dotted_key: str, value: Any) -> Path:
    """`outbound.daily_cap` = 30 这种写法，顺手帮我改配置。"""
    current = load(force=True)
    node = current
    parts = dotted_key.split(".")
    for part in parts[:-1]:
        if not isinstance(node.get(part), dict):
            node[part] = {}
        node = node[part]
    node[parts[-1]] = value

    # 只保存与默认值不同的部分
    diff: dict[str, Any] = {}

    def walk(default: Any, actual: Any, target: dict[str, Any]) -> None:
        if isinstance(default, dict) and isinstance(actual, dict):
            for k, v in actual.items():
                if isinstance(default.get(k), dict) and isinstance(v, dict):
                    child: dict[str, Any] = {}
                    walk(default[k], v, child)
                    if child:
                        target[k] = child
                elif default.get(k) != v:
                    target[k] = v
        return

    walk(DEFAULTS, current, diff)
    return save(diff)


def explain() -> dict[str, Any]:
    path = paths.config_json()
    return {
        "path": str(path),
        "exists": path.exists(),
        "defaults": sorted(DEFAULTS.keys()),
        "effective": load(force=True),
    }
