# -*- coding: utf-8 -*-
"""路径唯一出处。

现状：607 处写死 `%QBOT_HOME%\\qbot` / `%PROJECT_HOME%`，换机器就全断。
这个模块把根目录收成两个变量，**新代码只许从这里取**；老代码逐步迁移。

环境变量优先（迁移机器时只改这两个）：
    WHALE_ROOT      她的工程根，默认 %PROJECT_HOME%
    ASTRBOT_DATA    AstrBot 的 data 目录，默认 %ASTRBOT_HOME%\\data

用法：
    from whale_core.paths import ROOT, DATA, PD, snowluma_log, plugin_data
    p = plugin_data("astrbot_plugin_whale_will")      # → DATA/plugin_data/...
    log = snowluma_log("2026-11-09")
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

DEFAULT_ROOT = Path(r"%PROJECT_HOME%")
DEFAULT_DATA = Path(r"%ASTRBOT_HOME%\data")


def _resolve(env: str, default: Path) -> Path:
    raw = os.environ.get(env, "").strip()
    return Path(raw) if raw else default


def _check(p: Path, name: str) -> Path:
    if not p.exists():
        raise FileNotFoundError(f"{name} 不存在：{p}（可用环境变量覆盖）")
    return p


#: 她的工程根（工作区）
ROOT = _resolve("WHALE_ROOT", DEFAULT_ROOT)
#: AstrBot 数据根
DATA = _resolve("ASTRBOT_DATA", DEFAULT_DATA)
#: 插件数据根
PD = DATA / "plugin_data"
#: 工作区里的插件源目录（deploy 从这里同步）
SRC_PLUGINS = ROOT / "qqbot_v3"
#: 她的日志（SnowLuma 桥）
SNOW_LOGS = Path(os.environ.get("SNOWLUMA_LOGS", r"%QBOT_HOME%\qbot\SnowLuma\logs"))
#: 备份输出
BACKUPS = Path(os.environ.get("WHALE_BACKUPS", r"%PROJECT_HOME%\backups"))


def plugin_data(name: str) -> Path:
    return PD / name


def snowluma_log(day: str | None = None) -> Path:
    d = day or date.today().isoformat()
    return SNOW_LOGS / f"snowluma-{d}.log"


def ensure_dirs() -> list[str]:
    """返回缺失的目录（不创建，先报告）。"""
    missing = []
    for p, name in ((ROOT, "ROOT"), (DATA, "DATA"), (PD, "PD"), (SNOW_LOGS, "SNOW_LOGS")):
        if not p.exists():
            missing.append(f"{name}={p}")
    return missing


__all__ = [
    "ROOT", "DATA", "PD", "SRC_PLUGINS", "SNOW_LOGS", "BACKUPS",
    "plugin_data", "snowluma_log", "ensure_dirs",
]
