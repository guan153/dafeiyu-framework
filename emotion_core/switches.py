# -*- coding: utf-8 -*-
"""第一版影子开关 + 只读快照读取器（2026-11-13 P0，lead 授权）。

契约：只读；读不到/坏 JSON → 默认值（fail-soft）；开关 5 秒 mtime 缓存。
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

log = logging.getLogger("whale_core.switches")

_DATA = Path(r"%ASTRBOT_HOME%\data")
_SWITCH_FILE = _DATA / "plugin_data" / "whale_core" / "shadow_switches.json"
_SNAPSHOT_FILE = _DATA / "plugin_data" / "whale_core" / "emotion_snapshot.json"

_CACHE = {"mtime": 0.0, "data": {}}
_DEFAULTS = {
    "emotion.snapshot": "shadow",
    "emotion.reader.state_bus": "legacy",
    "emotion.reader.brain": "legacy",
    "emotion.reader.face": "legacy",
    "emotion.reader.meme": "legacy",
    "emotion.reader.urge": "legacy",
    "emotion.reader.bili": "legacy",
    "emotion.reader.voice": "legacy",
    "voice.want_source": "legacy",
    "emotion.shadow_log": "on",
    "state.refresh_seconds": 60,
    "proactive.state_stale_guard": "off",
    "proactive.state_stale_sec": 900,
    "outlet.stable_ticks": 0,
    "proactive.outlet_check": "raw",
    "proactive.outlet_stable_ttl": 120,
    "proactive.outlet_allow_winner": "connect",
    "voice.reason_pool_outlet": "off",
    "proactive.ledger": "off",
    "proactive.daily_total": 6,
    "proactive.min_gap_sec": 1800,
    "proactive.per_source": {"proactive_chat": 3, "will": 2, "bili": 2, "paint": 2},
    "will.two_strikes_scope": "persist",
}


def _load() -> dict:
    try:
        mt = _SWITCH_FILE.stat().st_mtime
    except Exception:
        return dict(_DEFAULTS)
    if mt != _CACHE["mtime"]:
        try:
            raw = json.loads(_SWITCH_FILE.read_text(encoding="utf-8-sig"))
            data = raw if isinstance(raw, dict) else {}
        except Exception:
            data = {}
        merged = dict(_DEFAULTS)
        merged.update({k: v for k, v in data.items() if isinstance(k, str)})
        _CACHE.update(mtime=mt, data=merged)
    return _CACHE["data"]


def get(key: str, default=None):
    val = _load().get(key)
    if val is None:
        return _DEFAULTS.get(key, default)
    return val


def read_emotion_snapshot(max_age: float = 300.0):
    try:
        raw = json.loads(_SNAPSHOT_FILE.read_text(encoding="utf-8-sig"))
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None
    try:
        if int(raw.get("schema") or 0) < 2:
            return None
        if str(raw.get("source") or "") != "astrbot_plugin_whale_affect":
            return None
        if float(raw.get("ts") or 0.0) <= 0:
            return None
        if time.time() - float(raw["ts"]) > float(max_age):
            return None
        if not raw.get("mood"):
            return None
        return raw
    except Exception:
        return None


def shadow_log(tag: str, **fields) -> None:
    try:
        if str(get("emotion.shadow_log", "on")) != "on":
            return
        payload = {"tag": tag, "ts": round(time.time(), 3)}
        payload.update(fields)
        log.info("[emotion-shadow] %s", json.dumps(payload, ensure_ascii=False))
    except Exception:
        pass
