# -*- coding: utf-8 -*-
"""情绪单一事实源（EmotionHub）。

要治的病：`affect_state=黏人 / beams_snapshot=平静 / beams_live=紧张 / whale_state=安心`
——模型一轮收到 4 个互相矛盾的心情，表现当然会飘。

规则：
- **单写者**：只有 `claim()` 成功的插件能 `update()`；其他插件只能 `observe()`（上报观察，不写事实）。
- **带版本**：每次更新 version+1，读方可以判断新旧，避免旧快照覆盖新状态。
- **会过期**：超过 `max_age` 没更新就标 stale，注入时降级为「不确定」，而不是拿几天前的心情冒充现在。
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from . import paths
from .bus import get_bus

log = logging.getLogger("whale_core.emotion")

MOODS = ("开心", "黏人", "吃醋", "委屈", "困", "兴奋", "胡闹", "好奇", "安心", "认真", "平静", "低落")
NEEDS = ("bond", "existence", "mattering", "energy")


@dataclass
class EmotionState:
    mood: str = "平静"
    intensity: float = 0.4
    valence: float = 0.0      # -1 难受 … +1 舒服
    arousal: float = 0.4      # 0 蔫 … 1 亢奋
    needs: dict[str, float] = field(default_factory=lambda: {k: 60.0 for k in NEEDS})
    confidence: float = 0.5
    reason: str = ""
    source: str = ""
    version: int = 0
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EmotionHub:
    def __init__(self, state_path: Path | str | None = None, owner: str = "astrbot_plugin_whale_affect") -> None:
        self.path = Path(state_path or paths.emotion_file())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._owner: str | None = None
        self._default_owner = owner
        self._state = self._load()
        self._observations: list[dict[str, Any]] = []

    # ── 所有权 ──────────────────────────────────────────────
    @property
    def owner(self) -> str | None:
        return self._owner

    def claim(self, plugin: str) -> tuple[bool, str]:
        """申请成为写者。同一时间只允许一个。"""
        if self._owner is None:
            self._owner = plugin
            log.info("情绪写者已登记：%s", plugin)
            return True, "claimed"
        if self._owner == plugin:
            return True, "already_owner"
        return False, f"owner_is:{self._owner}"

    def release(self, plugin: str) -> bool:
        if self._owner == plugin:
            self._owner = None
            return True
        return False

    # ── 写 ──────────────────────────────────────────────────
    def update(self, plugin: str, patch: dict[str, Any], *, reason: str = "") -> dict[str, Any] | None:
        """只有写者能改事实。非写者返回 None（不抛异常，调用方自行降级）。"""
        if self._owner is None or plugin != self._owner:
            log.warning("拒绝非写者更新情绪：%s（当前写者 %s）", plugin, self._owner)
            return None

        state = self._state
        mood = patch.get("mood", state.mood)
        if mood not in MOODS:
            mood = state.mood
        state.mood = mood
        state.intensity = _clamp(patch.get("intensity", state.intensity), 0.0, 1.0)
        state.valence = _clamp(patch.get("valence", state.valence), -1.0, 1.0)
        state.arousal = _clamp(patch.get("arousal", state.arousal), 0.0, 1.0)
        state.confidence = _clamp(patch.get("confidence", state.confidence), 0.0, 1.0)
        if isinstance(patch.get("needs"), dict):
            for k, v in patch["needs"].items():
                if k in NEEDS:
                    state.needs[k] = _clamp(v, 0.0, 100.0)
        state.reason = str(patch.get("reason") or reason or "")[:200]
        state.source = plugin
        state.version += 1
        state.updated_at = time.time()

        self._save(state)
        get_bus().publish("emotion.changed", state.to_dict(), source=plugin)
        return state.to_dict()

    def observe(self, plugin: str, note: str, *, mood_hint: str = "") -> None:
        """非写者上报观察（例如 bili 发现主人情绪低落），写给写者参考，不直接改事实。"""
        item = {"plugin": plugin, "note": note[:300], "mood_hint": mood_hint, "ts": time.time()}
        self._observations.append(item)
        del self._observations[:-50]
        get_bus().publish("emotion.observation", item, source=plugin)

    def observations(self, limit: int = 10) -> list[dict[str, Any]]:
        return self._observations[-limit:]

    # ── 读 ──────────────────────────────────────────────────
    def snapshot(self, max_age: float = 900.0) -> dict[str, Any]:
        state = self._state.to_dict()
        age = time.time() - (state.get("updated_at") or 0)
        state["age_sec"] = round(age, 1)
        state["stale"] = bool(age > max_age)
        state["owner"] = self._owner
        return state

    def render_block(self, max_age: float = 900.0) -> str:
        """注入用的一小段；全项目只此一处产出心情事实，避免多份互相打脸。"""
        st = self.snapshot(max_age=max_age)
        if st["stale"]:
            return ""
        mood, intensity = st["mood"], st["intensity"]
        needs = st.get("needs") or {}
        need_txt = " ".join(f"{k}={int(needs.get(k, 0))}" for k in NEEDS if k in needs)
        return (
            f"<emotion_state>\n"
            f"此刻心情：{mood}（强度 {intensity:.2f}，置信 {st['confidence']:.2f}）\n"
            f"感受轴：愉悦 {st['valence']:+.2f}／兴奋 {st['arousal']:.2f}\n"
            f"内在需求：{need_txt}\n"
            f"</emotion_state>"
        )

    # ── 持久化 ──────────────────────────────────────────────
    def _load(self) -> EmotionState:
        if not self.path.exists():
            return EmotionState(updated_at=0.0)
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            known = {f for f in EmotionState.__dataclass_fields__}  # type: ignore[attr-defined]
            return EmotionState(**{k: v for k, v in raw.items() if k in known})
        except Exception as exc:  # noqa: BLE001
            log.warning("情绪快照读取失败，重置：%s", exc)
            return EmotionState(updated_at=0.0)

    def _save(self, state: EmotionState) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)  # 原子替换，避免半截文件

    @property
    def default_owner(self) -> str:
        return self._default_owner


def _clamp(value: Any, lo: float, hi: float, default: float = 0.0) -> float:
    try:
        out = float(value)
    except Exception:
        return default
    if out != out:  # NaN
        return default
    return max(lo, min(hi, out))


_HUB: EmotionHub | None = None


def get_emotion_hub() -> EmotionHub:
    global _HUB
    if _HUB is None:
        _HUB = EmotionHub()
    return _HUB
