"""BEAM-AI · 状态契约与纯工具层（schema.py）

设计原则（对应 BEAM-AI 规格书）：
- 状态树按层组织：body / intero / fast / slow / drives / arbitration / meta / params / safety / trace
- 所有字段都有默认值，旧版本状态可平滑升级（缺失字段用默认值补齐）
- 所有数值有界（clamp / clamp01），禁止 NaN/Inf 流出
- 纯标准库、无 AstrBot 依赖，便于离线单测与影子模式

本层不产生任何"情绪概念"，只提供类型契约、边界与持久化。
"""
from __future__ import annotations

import json
import math
import os
import re
import tempfile
import time
from typing import Any, Dict, Iterable, List, Optional

STATE_VERSION = 1

# ---------------------------------------------------------------- 边界常量

EPS = 1e-9
VALENCE_RANGE = (-1.0, 1.0)
AROUSAL_RANGE = (0.0, 1.0)
DOMINANCE_RANGE = (0.0, 1.0)
UNIT_RANGE = (0.0, 1.0)

#: 效价安全边距：不贴 ±1，避免"锁死在边界推不动"
VALENCE_MARGIN = 0.92

#: 慢通道半衰期（小时）：心情 6h、压力 24h、联结 30 天、疲劳 8h
SLOW_HALF_LIFE_H = {
    "mood_valence": 6.0,
    "mood_arousal": 4.0,
    "stress": 24.0,
    "bonding": 30.0 * 24.0,
    "fatigue": 8.0,
}

#: 快通道半衰期（分钟）：情绪一阵一阵的，几分钟就回落
FAST_HALF_LIFE_MIN = {
    "valence": 4.0,
    "arousal": 3.0,
    "dominance": 6.0,
}

#: 内感受基线：出厂状态（安静、不累、也没多开心）
INTERO_BASELINE = {
    "valence": 0.12,
    "arousal": 0.30,
    "dominance": 0.55,
    "resource": 0.72,
    "prediction_error": 0.10,
    "load": 0.25,
    "action_cost": 0.25,
}

#: 快通道基线（情绪回落的目标）
FAST_BASELINE = {"valence": 0.10, "arousal": 0.32, "dominance": 0.55}

#: 慢通道基线
SLOW_BASELINE = {
    "mood_valence": 0.10,
    "mood_arousal": 0.32,
    "stress": 0.15,
    "bonding": 0.50,
    "fatigue": 0.20,
}

#: 五个动机的基线强度（没有输入时也不为 0：人是"有点想动"的）
DRIVE_NAMES = ("explore", "repair", "connect", "complete", "avoid")
DRIVE_BASELINE = {
    "explore": 0.28,
    "repair": 0.15,
    "connect": 0.30,
    "complete": 0.20,
    "avoid": 0.12,
}

#: 下行参数白名单：名称 -> (下限, 上限, 默认, 单次最大步长)
PARAMS_SPEC = {
    "sensitivity_gain": (0.5, 1.5, 1.0, 0.15),
    "decay_rate": (0.8, 1.25, 1.0, 0.10),
    "mood_baseline": (-0.30, 0.40, 0.10, 0.08),
    "drive_gain_connect": (0.6, 1.6, 1.0, 0.15),
    "drive_gain_explore": (0.6, 1.6, 1.0, 0.15),
    "inhibit_bias": (0.0, 0.5, 0.15, 0.05),
    "warmth_bias": (-0.2, 0.3, 0.0, 0.05),
    "brevity_bias": (-0.2, 0.3, 0.0, 0.05),
}

#: 人格包络：参数再调也不能越过的硬边界（安全层，不可被下行覆盖）
PERSONALITY_ENVELOPE = {
    "sensitivity_gain": (0.40, 1.80),
    "decay_rate": (0.60, 1.50),
    "mood_baseline": (-0.50, 0.60),
    "drive_gain_connect": (0.40, 2.00),
    "drive_gain_explore": (0.40, 2.00),
    "inhibit_bias": (0.0, 0.65),
    "warmth_bias": (-0.35, 0.45),
    "brevity_bias": (-0.35, 0.45),
}

#: 元认知与叙事
META_AUDIT_EVERY_ROUNDS = 12
NARRATIVE_MAX = 8

#: 安全层阈值
NEG_LOAD_ISOLATION = 0.68
IMBALANCE_STREAK_WARN = 6
IMBALANCE_STREAK_FORCE = 18
IMBALANCE_DECAY = 0.72

# ---------------------------------------------------------------- 数值工具


def finite(x: Any, default: float = 0.0) -> float:
    """转有限 float；NaN/Inf/非数字一律给 default。"""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return float(default)
    if math.isnan(v) or math.isinf(v):
        return float(default)
    return v


def num(d: Any, key: str, default: float = 0.0) -> float:
    """从 dict（或对象）里安全取数值。"""
    if isinstance(d, dict):
        return finite(d.get(key), default)
    return finite(getattr(d, key, default), default)


def clamp(x: Any, lo: float, hi: float, default: Optional[float] = None) -> float:
    v = finite(x, lo if default is None else default)
    if v < lo:
        return float(lo)
    if v > hi:
        return float(hi)
    return v


def clamp01(x: Any, default: float = 0.0) -> float:
    return clamp(x, 0.0, 1.0, default)


def clamp_valence(x: Any) -> float:
    return clamp(x, -VALENCE_MARGIN, VALENCE_MARGIN)


def as_text(x: Any, limit: int = 200, default: str = "") -> str:
    if not isinstance(x, str):
        return default
    t = x.strip()
    if not t:
        return default
    return t[:limit]



# ── 隐私闸门（借鉴 dsh-grow：敏感信息不进情绪流水/记忆）────────────────────
_SCRUB_PATTERNS = (
    (re.compile(r"(?i)(password|passwd|pwd|密码|口令)\s*[:：=]\s*\S+"), "密码=***"),
    (re.compile(r"(?i)(验证码|verification[_ ]?code|otp)\s*[:：=]?\s*\d{4,8}"), "验证码=***"),
    (re.compile(r"(?i)(api[_-]?key|secret|access[_-]?key|token)\s*[:：=]\s*\S+"), r"\1=***"),
    (re.compile(r"(?i)sk-[A-Za-z0-9_\-]{10,}"), "sk-***"),
    (re.compile(r"ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"), "ghp_***"),
    (re.compile(r"AKIA[A-Z0-9]{16}"), "AKIA***"),
    (re.compile(r"eyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{10,}"), "jwt.***"),
    (re.compile(r"1[3-9]\d{9}"), "1**********"),
    (re.compile(r"\b\d{17}[\dXx]\b"), "身份证***"),
    (re.compile(r"\b\d{16,19}\b"), "卡号***"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "email***"),
)


def scrub_text(text: str, limit: int = 200) -> str:
    """去掉敏感信息后再落库（手机/证件/卡号/邮箱/密码/密钥/JWT）。"""
    t = as_text(text, limit, "")
    if not t:
        return ""
    for pat, rep in _SCRUB_PATTERNS:
        t = pat.sub(rep, t, count=1)
    return t[:limit]


def now_ts() -> float:
    return time.time()


def half_life_factor(dt_hours: float, half_life_hours: float) -> float:
    """经过 dt 小时后，相对偏移还剩多少（0~1）。"""
    dt = max(0.0, finite(dt_hours, 0.0))
    hl = finite(half_life_hours, 1.0)
    if hl <= EPS:
        return 0.0
    try:
        return 0.5 ** (dt / hl)
    except OverflowError:
        return 0.0


def half_life_factor_min(dt_minutes: float, half_life_minutes: float) -> float:
    return half_life_factor(finite(dt_minutes, 0.0) / 60.0, finite(half_life_minutes, 1.0) / 60.0)


def clamp_to_range(value: float, lo: float, hi: float) -> float:
    return clamp(value, lo, hi)


def price_memory_rank(items: List[Dict], key: str) -> List[Dict]:
    return sorted(items, key=lambda x: finite(x.get(key), 0.0), reverse=True)


def deep_merge(base: Dict, patch: Dict) -> Dict:
    """把 patch 深合并进 base 的副本（只用于补齐默认字段）。"""
    out = dict(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------- 默认状态


def _default_drives(now: float) -> Dict:
    out = {"ts": now}
    for name in DRIVE_NAMES:
        out[name] = {
            "intensity": DRIVE_BASELINE[name],
            "urgency": DRIVE_BASELINE[name],
            "reason": "",
        }
    return out


def default_state() -> Dict:
    now = now_ts()
    return {
        "version": STATE_VERSION,
        "ts": now,
        "tick_count": 0,
        "body": {
            "ts": now,
            "cpu": 0.20,
            "mem": 0.40,
            "latency_ms": 80.0,
            "tools": {"net": True, "vision": True, "tts": False, "code": True},
            "uptime_s": 0.0,
            "errors_1h": 0,
            "budget": 0.85,
        },
        "intero": {
            "ts": now,
            "valence": INTERO_BASELINE["valence"],
            "arousal": INTERO_BASELINE["arousal"],
            "dominance": INTERO_BASELINE["dominance"],
            "resource": INTERO_BASELINE["resource"],
            "prediction_error": INTERO_BASELINE["prediction_error"],
            "load": INTERO_BASELINE["load"],
            "action_cost": INTERO_BASELINE["action_cost"],
        },
        "fast": {
            "ts": now,
            "valence": FAST_BASELINE["valence"],
            "arousal": FAST_BASELINE["arousal"],
            "dominance": FAST_BASELINE["dominance"],
            "intensity": 0.0,
            "label": "平静",
            "last_event": {"ts": 0.0, "kind": "", "why": ""},
        },
        "slow": {
            "ts": now,
            "mood_valence": SLOW_BASELINE["mood_valence"],
            "mood_arousal": SLOW_BASELINE["mood_arousal"],
            "stress": SLOW_BASELINE["stress"],
            "bonding": SLOW_BASELINE["bonding"],
            "fatigue": SLOW_BASELINE["fatigue"],
            "last_positive_ts": 0.0,
            "last_negative_ts": 0.0,
        },
        "drives": _default_drives(now),
        "arbitration": {
            "ts": now,
            "cooldown": {},
            "winner": "connect",
            "urgency": DRIVE_BASELINE["connect"],
            "inhibit": 0.0,
            "priority": 0.0,
            "candidates": {},
            "streak_name": "",
            "streak_count": 0,
            "last_switch_ts": now,
            "history": [],
        },
        "meta": {
            "ts": now,
            "self_model": {
                "capabilities": {"chat": 0.9, "vision": 0.6, "code": 0.7, "bili": 0.5, "tts": 0.2},
                "limits": ["看不见自己", "说话可能太机械", "一次只能应对一件事"],
                "confidence": 0.62,
            },
            "audit": {
                "last_ts": 0.0,
                "last_verdict": "none",
                "pending_review": False,
                "history": [],
            },
            "narrative": [],
        },
        "params": {k: v[2] for k, v in PARAMS_SPEC.items()},
        "safety": {
            "neg_load": 0.0,
            "isolation": False,
            "imbalance": {"name": "", "count": 0},
            "violations": [],
        },
        "gate": {"ts": now, "open": False, "probability": 0.0, "reason": "",
                 "route": "normal", "recent_opens": [], "pending": [],
                 "ignore_streak": 0, "last_user_ts": 0.0, "last_open_ts": 0.0,
                 "contracts": {}},
        "health": {"ts": now, "consecutive_failures": 0, "safe_mode": False,
                   "last_ok_ts": now, "total_ticks": 0},
        "seen": [],
        "trace": {"events": [], "last_tick_ts": now},
    }


# ---------------------------------------------------------------- 校验 / 升级


def validate(state: Any) -> Dict:
    """深补默认值 + 逐字段钳制。任何输入都返回合法状态树。"""
    base = default_state()
    if not isinstance(state, dict):
        return base
    out = deep_merge(base, state)

    # 数值钳制（逐层）
    b = out.get("body") if isinstance(out.get("body"), dict) else {}
    out["body"] = {
        "ts": finite(b.get("ts"), now_ts()),
        "cpu": clamp01(b.get("cpu"), 0.2),
        "mem": clamp01(b.get("mem"), 0.4),
        "latency_ms": clamp(b.get("latency_ms"), 0.0, 60000.0, 80.0),
        "tools": b.get("tools") if isinstance(b.get("tools"), dict) else {},
        "uptime_s": max(0.0, finite(b.get("uptime_s"), 0.0)),
        "errors_1h": int(clamp(b.get("errors_1h"), 0, 10000, 0)),
        "budget": clamp01(b.get("budget"), 0.85),
    }

    i = out.get("intero") if isinstance(out.get("intero"), dict) else {}
    out["intero"] = {
        "ts": finite(i.get("ts"), now_ts()),
        "valence": clamp(i.get("valence"), -1.0, 1.0, INTERO_BASELINE["valence"]),
        "arousal": clamp(i.get("arousal"), 0.0, 1.0, INTERO_BASELINE["arousal"]),
        "dominance": clamp(i.get("dominance"), 0.0, 1.0, INTERO_BASELINE["dominance"]),
        "resource": clamp(i.get("resource"), 0.0, 1.0, INTERO_BASELINE["resource"]),
        "prediction_error": clamp(i.get("prediction_error"), 0.0, 1.0, INTERO_BASELINE["prediction_error"]),
        "load": clamp(i.get("load"), 0.0, 1.0, INTERO_BASELINE["load"]),
        "action_cost": clamp(i.get("action_cost"), 0.0, 1.0, INTERO_BASELINE["action_cost"]),
    }

    f = out.get("fast") if isinstance(out.get("fast"), dict) else {}
    out["fast"] = {
        "ts": finite(f.get("ts"), now_ts()),
        "valence": clamp_valence(f.get("valence")),
        "arousal": clamp(f.get("arousal"), 0.0, 1.0, FAST_BASELINE["arousal"]),
        "dominance": clamp(f.get("dominance"), 0.0, 1.0, FAST_BASELINE["dominance"]),
        "intensity": clamp01(f.get("intensity"), 0.0),
        "label": as_text(f.get("label"), 32, "平静"),
        "last_event": f.get("last_event") if isinstance(f.get("last_event"), dict) else {"ts": 0.0, "kind": "", "why": ""},
    }

    s = out.get("slow") if isinstance(out.get("slow"), dict) else {}
    out["slow"] = {
        "ts": finite(s.get("ts"), now_ts()),
        "mood_valence": clamp(s.get("mood_valence"), -1.0, 1.0, SLOW_BASELINE["mood_valence"]),
        "mood_arousal": clamp(s.get("mood_arousal"), 0.0, 1.0, SLOW_BASELINE["mood_arousal"]),
        "stress": clamp01(s.get("stress"), SLOW_BASELINE["stress"]),
        "bonding": clamp01(s.get("bonding"), SLOW_BASELINE["bonding"]),
        "fatigue": clamp01(s.get("fatigue"), SLOW_BASELINE["fatigue"]),
        "last_positive_ts": max(0.0, finite(s.get("last_positive_ts"), 0.0)),
        "last_negative_ts": max(0.0, finite(s.get("last_negative_ts"), 0.0)),
    }

    d = out.get("drives") if isinstance(out.get("drives"), dict) else {}
    drives = {}
    for name in DRIVE_NAMES:
        item = d.get(name) if isinstance(d.get(name), dict) else {}
        drives[name] = {
            "intensity": clamp01(item.get("intensity"), DRIVE_BASELINE[name]),
            "urgency": clamp01(item.get("urgency"), DRIVE_BASELINE[name]),
            "reason": as_text(item.get("reason"), 80, ""),
        }
    out["drives"] = drives

    a = out.get("arbitration") if isinstance(out.get("arbitration"), dict) else {}
    out["arbitration"] = {
        "ts": finite(a.get("ts"), now_ts()),
        "cooldown": a.get("cooldown") if isinstance(a.get("cooldown"), dict) else {},
        "winner": as_text(a.get("winner"), 16, "connect"),
        "urgency": clamp01(a.get("urgency"), 0.0),
        "inhibit": clamp01(a.get("inhibit"), 0.0),
        "priority": clamp(a.get("priority"), 0.0, 10.0, 0.0),
        "candidates": a.get("candidates") if isinstance(a.get("candidates"), dict) else {},
        "streak_name": as_text(a.get("streak_name"), 16, ""),
        "streak_count": int(clamp(a.get("streak_count"), 0, 100000, 0)),
        "last_switch_ts": max(0.0, finite(a.get("last_switch_ts"), now_ts())),
        "history": a.get("history") if isinstance(a.get("history"), list) else [],
    }

    m = out.get("meta") if isinstance(out.get("meta"), dict) else {}
    sm = m.get("self_model") if isinstance(m.get("self_model"), dict) else {}
    au = m.get("audit") if isinstance(m.get("audit"), dict) else {}
    out["meta"] = {
        "ts": finite(m.get("ts"), now_ts()),
        "self_model": {
            "capabilities": sm.get("capabilities") if isinstance(sm.get("capabilities"), dict) else {},
            "limits": sm.get("limits") if isinstance(sm.get("limits"), list) else [],
            "confidence": clamp01(sm.get("confidence"), 0.62),
        },
        "audit": {
            "last_ts": max(0.0, finite(au.get("last_ts"), 0.0)),
            "last_verdict": as_text(au.get("last_verdict"), 16, "none"),
            "pending_review": bool(au.get("pending_review")),
            "last_round": int(clamp(au.get("last_round"), 0, 10 ** 9, 0)),
            "history": au.get("history") if isinstance(au.get("history"), list) else [],
        },
        "narrative": m.get("narrative") if isinstance(m.get("narrative"), list) else [],
    }

    p = out.get("params") if isinstance(out.get("params"), dict) else {}
    out["params"] = {
        k: clamp(p.get(k), v[0], v[1], v[2]) for k, v in PARAMS_SPEC.items()
    }

    sf = out.get("safety") if isinstance(out.get("safety"), dict) else {}
    imb = sf.get("imbalance") if isinstance(sf.get("imbalance"), dict) else {}
    out["safety"] = {
        "neg_load": clamp01(sf.get("neg_load"), 0.0),
        "isolation": bool(sf.get("isolation")),
        "imbalance": {
            "name": as_text(imb.get("name"), 16, ""),
            "count": int(clamp(imb.get("count"), 0, 100000, 0)),
        },
        "violations": sf.get("violations") if isinstance(sf.get("violations"), list) else [],
    }

    g = out.get("gate") if isinstance(out.get("gate"), dict) else {}
    out["gate"] = {
        "ts": finite(g.get("ts"), now_ts()),
        "open": bool(g.get("open")),
        "probability": clamp01(g.get("probability"), 0.0),
        "reason": as_text(g.get("reason"), 60, ""),
        "route": as_text(g.get("route"), 12, "normal"),
        "recent_opens": g.get("recent_opens") if isinstance(g.get("recent_opens"), list) else [],
        "pending": g.get("pending") if isinstance(g.get("pending"), list) else [],
        "ignore_streak": int(clamp(g.get("ignore_streak"), 0, 10000, 0)),
        "last_user_ts": max(0.0, finite(g.get("last_user_ts"), 0.0)),
        "last_open_ts": max(0.0, finite(g.get("last_open_ts"), 0.0)),
        "open_until": max(0.0, finite(g.get("open_until"), 0.0)),
        "contracts": g.get("contracts") if isinstance(g.get("contracts"), dict) else {},
    }
    hl = out.get("health") if isinstance(out.get("health"), dict) else {}
    out["health"] = {
        "ts": finite(hl.get("ts"), now_ts()),
        "consecutive_failures": int(clamp(hl.get("consecutive_failures"), 0, 10000, 0)),
        "safe_mode": bool(hl.get("safe_mode")),
        "last_ok_ts": max(0.0, finite(hl.get("last_ok_ts"), now_ts())),
        "total_ticks": int(clamp(hl.get("total_ticks"), 0, 10 ** 9, 0)),
    }
    out["seen"] = out.get("seen") if isinstance(out.get("seen"), list) else []

    t = out.get("trace") if isinstance(out.get("trace"), dict) else {}
    out["trace"] = {
        "events": t.get("events") if isinstance(t.get("events"), list) else [],
        "last_tick_ts": max(0.0, finite(t.get("last_tick_ts"), now_ts())),
    }

    out["version"] = int(clamp(out.get("version"), 1, 999, STATE_VERSION))
    out["ts"] = finite(out.get("ts"), now_ts())
    out["tick_count"] = int(clamp(out.get("tick_count"), 0, 10 ** 9, 0))
    return out


def copy_state(state: Dict) -> Dict:
    return validate(state)


# ---------------------------------------------------------------- 持久化


def read_json(path: str, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def write_json(path: str, data: Any) -> bool:
    """准原子写：同目录临时文件 + os.replace。失败返回 False，绝不抛出。"""
    try:
        folder = os.path.dirname(os.path.abspath(path))
        if folder and not os.path.isdir(folder):
            os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".beams_", suffix=".tmp", dir=folder or None)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
            return True
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except (OSError, ValueError, TypeError):
        return False


def append_jsonl(path: str, item: Dict) -> bool:
    try:
        folder = os.path.dirname(os.path.abspath(path))
        if folder and not os.path.isdir(folder):
            os.makedirs(folder, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
        return True
    except (OSError, TypeError, ValueError):
        return False


def load_state(path: str) -> Dict:
    """读状态文件；不存在/损坏 → 出厂状态。"""
    raw = read_json(path, None)
    if not isinstance(raw, dict):
        return default_state()
    return validate(raw)


def save_state(path: str, state: Dict) -> bool:
    return write_json(path, validate(state))


def file_age_hours(path: str) -> float:
    try:
        return max(0.0, (time.time() - os.path.getmtime(path)) / 3600.0)
    except OSError:
        return 1e9