# -*- coding: utf-8 -*-
"""目录与路径约定：整个 v3 只在这里认路。

以前各插件各写死 `%ASTRBOT_HOME%\\data`，换机器/换盘就集体失灵；
现在统一走 ASTRBOT_ROOT，环境变量优先，缺失才回退默认值。
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_ASTRBOT_ROOT = Path(r"%ASTRBOT_HOME%")


def astrbot_root() -> Path:
    """AstrBot 根目录（可用 ASTRBOT_ROOT 覆盖）。"""
    raw = os.environ.get("ASTRBOT_ROOT")
    if raw:
        return Path(raw).expanduser().resolve()
    return DEFAULT_ASTRBOT_ROOT


def data_dir() -> Path:
    """AstrBot 数据根目录。"""
    return astrbot_root() / "data"


def plugin_data_dir(plugin_name: str | None = None) -> Path:
    """插件数据目录；不传则返回父目录。"""
    base = data_dir() / "plugin_data"
    return base / plugin_name if plugin_name else base


def core_dir() -> Path:
    """内核自有数据目录（账本、队列、快照都在这）。"""
    path = plugin_data_dir("whale_core")
    path.mkdir(parents=True, exist_ok=True)
    return path


def ledger_db() -> Path:
    return core_dir() / "weakness.sqlite3"


def outbound_db() -> Path:
    return core_dir() / "outbound.sqlite3"


def emotion_file() -> Path:
    return core_dir() / "emotion_state.json"


def runtime_log() -> Path:
    """AstrBot 运行日志（跑在根目录下，日志名固定）。"""
    return astrbot_root() / "runtime.log"


def config_json() -> Path:
    """内核配置（与 AstrBot 插件配置分开，便于 AstrBot 不在时也能读）。"""
    return core_dir() / "core_config.json"


def version_info() -> dict:
    from . import __version__

    return {
        "version": __version__,
        "astrbot_root": str(astrbot_root()),
        "data_dir": str(data_dir()),
        "core_dir": str(core_dir()),
    }
