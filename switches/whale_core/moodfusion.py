# -*- coding: utf-8 -*-
"""心情合成（mood composition）——分层，不投票。

上一版是「三个分数量加权投票」，被否了：三个源量纲根本不同
（affect 是 0.47 的强度、soul 是 0–100 的十维、laya 是 8 类概率），
硬加权只是看起来有依据。

这一版按**时间尺度分层**，像潮汐的三层：

    底色 layer   soul 10 维          小时级慢变，决定「她这段日子是什么心情」
    温度 layer   EmotionHub 强度     分钟级，决定「此刻这份心情有多烫」
    扰动 layer   laya 单句判读       秒级，只决定「这一句怎么回」，**不改心情**

核心不变式：**扰动永远不能改心情标签。**
你说「我好累」，laya 判「疲惫」——那是你的状态；她该变软、话变短，但不是她也变成疲惫。

对外只暴露一个合成结果 :class:`Composed`，谁要用谁读，不许再各算各的。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "MOODS",
    "LAYA_MAP",
    "BASE_ALPHA",
    "BASE_SWITCH_MARGIN",
    "TEMP_WEIGHT",
    "Composed",
    "blend_base",
    "base_mood",
    "compose",
    "read_hub",
    "read_soul",
    "read_base_state",
    "write_base_state",
    "ask_laya",
    "write_state",
    "read_state",
]

MOODS = ("开心", "黏人", "吃醋", "委屈", "困", "兴奋", "胡闹", "好奇", "安心", "认真", "平静", "低落")

# laya 的 8 类 → 我们的 12 类（只用于「怎么回应」，不参与心情）
LAYA_MAP = {
    "兴奋": "兴奋",
    "吃醋": "吃醋",
    "平静": "平静",
    "开心": "开心",
    "撒娇": "黏人",
    "生气": "吃醋",
    "疲惫": "困",
    "难过": "低落",
}

BASE_ALPHA = 0.08          # 底色每拍只吸收 8%：一次极端读数翻不动它（小时级）
BASE_SWITCH_MARGIN = 0.03  # 新底色要领先旧底色才换
TEMP_WEIGHT = 0.40         # 温度对强度的贡献


@dataclass
class Composed:
    mood: str                      # 唯一心情标签（来自底色）
    intensity: float               # 底色 × 温度调制后的强度
    base_mood: str = "平静"
    base_intensity: float = 0.4
    temp_intensity: float = 0.4
    expression: dict = field(default_factory=dict)   # 扰动给出的「怎么回应」
    notes: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "mood": self.mood,
            "intensity": round(self.intensity, 3),
            "base": {"mood": self.base_mood, "intensity": round(self.base_intensity, 3)},
            "temp": round(self.temp_intensity, 3),
            "expression": self.expression,
            "notes": self.notes,
        }


def _clamp(x, lo=0.0, hi=1.0, default=0.0) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    if v != v:
        return default
    return max(lo, min(hi, v))


# ── 底色层：慢 ─────────────────────────────────────────────
def blend_base(prev: dict[str, float] | None, soul: dict[str, float], alpha: float = BASE_ALPHA) -> dict[str, float]:
    """把这一拍的 soul 十维揉进底色。alpha 越小越慢。"""
    cur = {k: _clamp(_clamp(v, 0, 100, 0) / 100.0) for k, v in (soul or {}).items() if k in MOODS}
    if not prev:
        return dict(cur)
    out = {k: float(v) for k, v in prev.items() if k in MOODS}
    for k, v in cur.items():
        out[k] = (1 - alpha) * out.get(k, v) + alpha * v
    return out


def base_mood(base: dict[str, float], prev_label: str | None = None) -> tuple[str, float]:
    """底色里的主情绪 + 强度。迟滞只防抖，不做跨源仲裁。"""
    if not base:
        return "平静", 0.4
    winner = max(base, key=base.get)
    if prev_label and prev_label in base and prev_label != winner:
        if base[winner] - base[prev_label] < BASE_SWITCH_MARGIN:
            winner = prev_label
    return winner, _clamp(base[winner], 0.05, 1.0, 0.4)


# ── 扰动层：只决定怎么回应 ────────────────────────────────
def _expression(perturb: dict | None) -> dict:
    """laya 的单句判读 → 表达参数。绝不产出 mood。"""
    if not isinstance(perturb, dict):
        return {"soften": False, "brevity": False, "touch_ok": True,
                "brightness": 0.0, "scene_hint": None, "source": "无"}
    label = str(perturb.get("label") or "")
    conf = _clamp(perturb.get("confidence"), 0, 1, 0.0)
    if not label or conf < 0.45:
        return {"soften": False, "brevity": False, "touch_ok": True,
                "brightness": 0.0, "scene_hint": None, "source": f"{label or '无'} {conf:.2f}（置信不足）"}

    expr = {"soften": False, "brevity": False, "touch_ok": True,
            "brightness": 0.0, "scene_hint": None, "source": f"{label} {conf:.2f}"}
    if label in ("疲惫", "难过"):
        expr.update(soften=True, brevity=True, scene_hint="comfort", brightness=-0.4)
    elif label == "生气":
        expr.update(soften=True, scene_hint="comfort", brightness=-0.2)
    elif label == "撒娇":
        expr.update(touch_ok=True, brightness=0.3)
    elif label in ("开心", "兴奋"):
        expr.update(brightness=0.4)
    elif label == "平静":
        expr.update(brightness=0.0)
    return expr


# ── 合成 ───────────────────────────────────────────────────
def compose(
    base: dict[str, float],
    temp: dict | None = None,
    perturb: dict | None = None,
    *,
    prev_label: str | None = None,
) -> Composed:
    """合成唯一心情。心情只由底色决定；温度调强度；扰动只调表达。"""
    temp = temp or {}
    b_mood, b_int = base_mood(base, prev_label)
    t_int = _clamp(temp.get("intensity"), 0.0, 1.0, b_int)

    # 温度调制：底色为主，温度最多把它拉动 40%
    intensity = _clamp(b_int * (1 - TEMP_WEIGHT) + t_int * TEMP_WEIGHT, 0.05, 1.0, b_int)

    expr = _expression(perturb)
    return Composed(
        mood=b_mood,
        intensity=intensity,
        base_mood=b_mood,
        base_intensity=b_int,
        temp_intensity=t_int,
        expression=expr,
        notes={
            "base_top": {k: round(v, 2) for k, v in sorted(base.items(), key=lambda kv: -kv[1])[:3]},
            "temp_mood": temp.get("mood") if isinstance(temp, dict) else None,
        },
    )


# ── IO ─────────────────────────────────────────────────────
def read_hub(path: str | Path) -> dict:
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return {"mood": d.get("mood"), "intensity": d.get("intensity")}
    except Exception:
        return {}


def read_soul(path: str | Path) -> dict:
    try:
        import sqlite3

        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            row = con.execute("select mood from state where id=1").fetchone()
        finally:
            con.close()
        return json.loads(row[0]) if row and row[0] else {}
    except Exception:
        return {}


def read_base_state(path: str | Path) -> dict:
    """底色是随时间累积的，必须持久化，否则每次重启都从零开始。"""
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return {"values": d.get("values") or {}, "label": d.get("label") or None, "ts": d.get("ts") or 0}
    except Exception:
        return {"values": {}, "label": None, "ts": 0}


def write_base_state(path: str | Path, values: dict, label: str) -> None:
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"values": values, "label": label, "ts": int(time.time())},
                                ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


def ask_laya(text: str, url: str = "http://127.0.0.1:8901/classify", timeout: float = 3.0) -> dict | None:
    if not text:
        return None
    try:
        import urllib.request

        req = urllib.request.Request(
            url, data=json.dumps({"text": text[:300]}).encode(), headers={"Content-Type": "application/json"}
        )
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore"))
    except Exception:
        return None


def write_state(path: str | Path, composed: Composed) -> None:
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = composed.to_dict()
        payload["ts"] = int(time.time())
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


def read_state(path: str | Path, max_age: float = 300.0) -> dict | None:
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        if time.time() - float(d.get("ts") or 0) > max_age:
            return None
        return d
    except Exception:
        return None
