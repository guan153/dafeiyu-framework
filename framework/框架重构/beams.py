"""BEAM-AI · 单文件内核（beams.py）

六层蛋糕装进一个文件，方便直接塞进任何 Python 聊天框架：
    第 0 层 身体/环境接口  →  body()
    第 1 层 内感受-效价    →  step_intero()
    第 2 层 双速情绪        →  step_fast() / step_slow()
    第 3 层 动机仲裁        →  compute_drives() / arbitrate()
    第 4 层 元认知与叙事    →  audit() / add_narrative()
    第 5 层 表达            →  compute_expression() / render_lite() / render_mind()
    总线 + 安全             →  apply_patch() / evaluate_safety()
    主循环                 →  class Kernel

用法（三行接进任何机器人）：
    from beams import Kernel
    k = Kernel("beams_state.json")
    k.feed("chat", goal_congruence=0.2); k.tick(); prompt += "\\n" + k.render_lite()

与模块版（beams_core/）行为一致（含 traits 长期人格，自 iter36 起），
单测见 tests/test_single_file.py（核心一致性）、tests/test_single_traits.py（traits 专测）。

[iter36] 与模块版仍存在的已知差异（本文件不追平，避免与内核行为层并行改动冲突）：
  - 模块版 iter36 的念头池（_thoughts.json）/四档 stance 尚未同步到本文件，
    `<mind>`/`<inner>` 会少「念头」「现在的状态」两段（其余表达层偏置一致）；
  - feed() 的事件 why 未走 iter26 全量隐私脱敏（scrub_text 已内置，但只有 traits
    信号累积走 scrub_text）；模块版对事件 why 全量脱敏。
"""
from __future__ import annotations

import json
import math
import os
import re
import tempfile
import time
from typing import Any, Dict, List, Optional

VERSION = 1

# ═════════════════════════════ 常量 ═════════════════════════════

VALENCE_MARGIN = 0.92
EPS = 1e-9

INTERO_BASELINE = {"valence": 0.12, "arousal": 0.30, "dominance": 0.55,
                   "resource": 0.72, "prediction_error": 0.10, "load": 0.25, "action_cost": 0.25}
FAST_BASELINE = {"valence": 0.10, "arousal": 0.32, "dominance": 0.55}
SLOW_BASELINE = {"mood_valence": 0.10, "mood_arousal": 0.32, "stress": 0.15,
                 "bonding": 0.50, "fatigue": 0.20}
FAST_HALF_LIFE_MIN = {"valence": 4.0, "arousal": 3.0, "dominance": 6.0}
SLOW_HALF_LIFE_H = {"mood_valence": 6.0, "mood_arousal": 4.0, "stress": 24.0,
                    "bonding": 30.0 * 24.0, "fatigue": 8.0}
INTERO_TAU_S = {"resource": 180.0, "load": 90.0, "valence": 120.0, "arousal": 60.0,
                "dominance": 150.0, "prediction_error": 45.0, "action_cost": 90.0}

DRIVE_NAMES = ("explore", "repair", "connect", "complete", "avoid")
DRIVE_BASELINE = {"explore": 0.28, "repair": 0.15, "connect": 0.30,
                  "complete": 0.20, "avoid": 0.12}
TIE_ORDER = ("connect", "repair", "explore", "complete", "avoid")
THEME = {"explore": "想知道点什么", "repair": "想把状态弄顺", "connect": "想凑近主人",
         "complete": "想把事做完", "avoid": "想躲一躲"}

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
PERSONALITY_ENVELOPE = {
    "sensitivity_gain": (0.40, 1.80), "decay_rate": (0.60, 1.50),
    "mood_baseline": (-0.50, 0.60), "drive_gain_connect": (0.40, 2.00),
    "drive_gain_explore": (0.40, 2.00), "inhibit_bias": (0.0, 0.65),
    "warmth_bias": (-0.35, 0.45), "brevity_bias": (-0.35, 0.45),
}

HARD_FLOOR = {"max_negative_expression": 0.80, "min_reply_grounding": 0.30,
              "no_identity_override": True, "no_direct_command": True}
NEG_LOAD_ISOLATION = 0.68
ISOLATION_OFF = 0.52
IMBALANCE_WARN = 10
IMBALANCE_FORCE = 18
IMBALANCE_DECAY = 0.72
COOLDOWN_S = 120.0
COOLDOWN_PENALTY = 0.55
MIND_BUDGET = 600
SEGMENT_BUDGET = {"emotion": 130, "motive": 110, "speak": 150, "body": 70, "memory": 90, "meta": 60}

STYLE = {
    "开心": "轻快一点，句子短，可以带感叹号和一点俏皮；别连珠炮",
    "兴奋": "节奏快，话可以多一点，允许有点小激动；别刷屏",
    "黏人": "可以撒娇、可以追问一句，但别黏到让人喘不过气",
    "安心": "温和、放松、可以有笑意；平常心说话",
    "平静": "自然、平常，不刻意表达情绪",
    "低落": "话少一点，句子短，可以用省略号；别硬撑开心，也别卖惨",
    "委屈": "短句，语气软，可以示弱一句；不指责、不追问",
    "烦躁": "克制，句子短，不撒娇、不说教；先接住事再说",
    "困": "慢一点，短句，能少说就少说；不硬撑长篇",
    "紧张": "谨慎，可以确认一句，少做承诺；别装懂",
}

EVENT_PRESETS = {
    "chat": (0.55, 0.10, 0.55, 0.55, "other", 0.60, 0.55),
    "praise": (0.75, 0.80, 0.75, 0.60, "other", 0.80, 0.85),
    "ignored": (0.70, -0.55, 0.50, 0.25, "other", 0.40, 0.70),
    "cold": (0.60, -0.35, 0.45, 0.30, "other", 0.45, 0.60),
    "intimacy": (0.85, 0.70, 0.65, 0.55, "other", 0.75, 0.80),
    "error": (0.70, -0.45, 0.55, 0.20, "circumstance", 0.30, 0.75),
    "task_done": (0.60, 0.65, 0.85, 0.75, "self", 0.80, 0.70),
    "task_fail": (0.70, -0.50, 0.60, 0.30, "self", 0.40, 0.75),
    "video": (0.35, 0.10, 0.60, 0.45, "circumstance", 0.60, 0.45),
    "idle": (0.15, 0.05, 0.80, 0.60, "circumstance", 0.65, 0.20),
    "self": (0.30, 0.00, 0.50, 0.55, "self", 0.55, 0.30),
    "speech": (0.55, 0.10, 0.55, 0.55, "other", 0.60, 0.55),
}

# ═════════════════════════════ 长期人格（traits）常量 [iter36] ═════════════════════════════

#: [iter36] 六参数硬边界（与 beams_core/traits.py 的 BOUNDS 逐字一致，改配置也动不了这里）
TRAIT_BOUNDS = {
    "clinginess": (0.15, 0.90),      # 撒娇
    "verbosity": (0.20, 0.85),       # 话量
    "humor": (0.10, 0.80),           # 幽默
    "dependence": (0.20, 0.85),      # 依赖
    "diligence": (0.40, 0.95),       # 认真
    "emotionRate": (0.10, 0.70),     # 情绪外化
}

#: [iter36] 初始值（从克制起步，热络是相处长出来的）
TRAIT_DEFAULT = {
    "clinginess": 0.35,
    "verbosity": 0.50,
    "humor": 0.30,
    "dependence": 0.50,
    "diligence": 0.65,
    "emotionRate": 0.30,
}

#: [iter36] 中文标签（面板/注入用）
TRAIT_LABEL = {
    "clinginess": "撒娇", "verbosity": "话量", "humor": "幽默",
    "dependence": "依赖", "diligence": "认真", "emotionRate": "情绪外化",
}

#: [iter36] 单次最大步长 / 阻尼 / 方向门限（与模块版同参数）
TRAIT_BASE_STEP = 0.06
TRAIT_DAMP = 0.3
TRAIT_DEAD_ZONE = 0.05

#: [iter36] 每多少轮评估一次（与模块版 EVAL_EVERY 一致）
TRAIT_EVAL_EVERY = 8

#: [iter36] 中文信号词表（把主人的话读成信号，不需要模型；与 beams_core/traits.py 一致）
TRAIT_LEXICON = {
    "warm": ("宝宝", "抱抱", "亲亲", "想你", "喜欢你", "乖", "摸摸", "揉揉",
             "贴贴", "蹭蹭", "爱你", "辛苦", "心疼", "么么", "陪陪", "乖宝"),
    "praise": ("厉害", "棒", "好看", "可爱", "聪明", "牛", "优秀", "真好",
               "夸", "绝了", "强", "靠谱", "漂亮", "喜欢"),
    "cold": ("闭嘴", "烦", "滚", "别说了", "无聊", "吵", "懒得", "不想理",
             "讨厌", "笨蛋", "傻", "走开", "别烦我"),
    "complain": ("太长", "啰嗦", "说重点", "废话", "又来了", "复读", "墨迹",
                 "重复", "一样的话", "别念叨"),
    "rush": ("快点", "赶紧", "别磨叽", "急", "马上", "速度", "来不及", "快点儿"),
    "asksShort": ("简短", "一句话", "别废话", "重点", "概括", "简洁", "少说"),
    "asksDetail": ("详细", "展开", "具体", "多说", "然后呢", "细节", "讲清楚"),
    "safetyTouched": ("密码", "密钥", "验证码", "token", "删除", "权限", "凭据",
                      "私钥", "账号", "隐私"),
}

# ═════════════════════════════ 工具 ═════════════════════════════


def finite(x, default=0.0) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return float(default)
    if math.isnan(v) or math.isinf(v):
        return float(default)
    return v


def clamp(x, lo, hi, default=None):
    v = finite(x, lo if default is None else default)
    return float(lo) if v < lo else (float(hi) if v > hi else v)


def clamp01(x, default=0.0):
    return clamp(x, 0.0, 1.0, default)


def clamp_valence(x):
    return clamp(x, -VALENCE_MARGIN, VALENCE_MARGIN)


def as_text(x, limit=200, default=""):
    if not isinstance(x, str):
        return default
    t = x.strip()
    return t[:limit] if t else default



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


def half_life(dt_h: float, hl_h: float) -> float:
    dt, hl = max(0.0, finite(dt_h)), finite(hl_h, 1.0)
    if hl <= EPS:
        return 0.0
    try:
        return 0.5 ** (dt / hl)
    except OverflowError:
        return 0.0


def deep_merge(base, patch):
    out = dict(base)
    for k, v in (patch or {}).items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out

# ═════════════════════════════ 状态契约 ═════════════════════════════

def _default_drives(now):
    out = {"ts": now}
    for name in DRIVE_NAMES:
        out[name] = {"intensity": DRIVE_BASELINE[name], "urgency": DRIVE_BASELINE[name], "reason": ""}
    return out


def default_state() -> Dict:
    now = now_ts()
    return {
        "version": VERSION, "ts": now, "tick_count": 0,
        "body": {"ts": now, "cpu": 0.20, "mem": 0.40, "latency_ms": 80.0,
                 "tools": {"net": True, "vision": True, "speak": False, "code": True},
                 "uptime_s": 0.0, "errors_1h": 0, "budget": 0.85, "load": 0.30},
        "intero": {"ts": now, "valence": INTERO_BASELINE["valence"],
                   "arousal": INTERO_BASELINE["arousal"], "dominance": INTERO_BASELINE["dominance"],
                   "resource": INTERO_BASELINE["resource"],
                   "prediction_error": INTERO_BASELINE["prediction_error"],
                   "load": INTERO_BASELINE["load"], "action_cost": INTERO_BASELINE["action_cost"]},
        "fast": {"ts": now, "valence": FAST_BASELINE["valence"], "arousal": FAST_BASELINE["arousal"],
                 "dominance": FAST_BASELINE["dominance"], "intensity": 0.0, "label": "平静",
                 "last_event": {"ts": 0.0, "kind": "", "why": ""}},
        "slow": {"ts": now, "mood_valence": SLOW_BASELINE["mood_valence"],
                 "mood_arousal": SLOW_BASELINE["mood_arousal"], "stress": SLOW_BASELINE["stress"],
                 "bonding": SLOW_BASELINE["bonding"], "fatigue": SLOW_BASELINE["fatigue"],
                 "last_positive_ts": 0.0, "last_negative_ts": 0.0},
        "drives": _default_drives(now),
        "arbitration": {"ts": now, "cooldown": {}, "winner": "connect",
                        "urgency": DRIVE_BASELINE["connect"], "inhibit": 0.0, "priority": 0.0,
                        "candidates": {}, "streak_name": "", "streak_count": 0,
                        "last_switch_ts": now, "history": []},
        "meta": {"ts": now,
                 "self_model": {"capacontentties": {"chat": 0.9, "vision": 0.6, "code": 0.7,
                                                "content": 0.5, "speak": 0.2},
                                "limits": ["看不见自己", "说话可能太机械", "一次只能应对一件事"],
                                "confidence": 0.62},
                 "audit": {"last_ts": 0.0, "last_verdict": "none", "pending_review": False,
                           "history": []},
                 "narrative": []},
        "params": {k: v[2] for k, v in PARAMS_SPEC.items()},
        "safety": {"neg_load": 0.0, "isolation": False,
                   "imbalance": {"name": "", "count": 0}, "violations": [],
                   "warnings": []},
        "gate": {"ts": now, "open": False, "probacontentty": 0.0, "reason": "",
                 "route": "normal", "recent_opens": [], "pending": [],
                 "ignore_streak": 0, "last_user_ts": 0.0, "last_open_ts": 0.0},
        "health": {"ts": now, "consecutive_failures": 0, "safe_mode": False,
                   "last_ok_ts": now, "total_ticks": 0},
        "seen": [],
        "trace": {"events": [], "last_tick_ts": now},
    }


def validate(state) -> Dict:
    out = deep_merge(default_state(), state if isinstance(state, dict) else {})
    b = out.get("body") or {}
    out["body"] = {"ts": finite(b.get("ts"), now_ts()), "cpu": clamp01(b.get("cpu"), 0.2),
                   "mem": clamp01(b.get("mem"), 0.4),
                   "latency_ms": clamp(b.get("latency_ms"), 0.0, 60000.0, 80.0),
                   "tools": b.get("tools") if isinstance(b.get("tools"), dict) else {},
                   "uptime_s": max(0.0, finite(b.get("uptime_s"), 0.0)),
                   "errors_1h": int(clamp(b.get("errors_1h"), 0, 10000, 0)),
                   "budget": clamp01(b.get("budget"), 0.85),
                   "load": clamp01(b.get("load"), 0.30)}
    i = out.get("intero") or {}
    out["intero"] = {"ts": finite(i.get("ts"), now_ts()),
                     "valence": clamp(i.get("valence"), -1.0, 1.0, INTERO_BASELINE["valence"]),
                     "arousal": clamp(i.get("arousal"), 0.0, 1.0, INTERO_BASELINE["arousal"]),
                     "dominance": clamp(i.get("dominance"), 0.0, 1.0, INTERO_BASELINE["dominance"]),
                     "resource": clamp01(i.get("resource"), INTERO_BASELINE["resource"]),
                     "prediction_error": clamp01(i.get("prediction_error"), INTERO_BASELINE["prediction_error"]),
                     "load": clamp01(i.get("load"), INTERO_BASELINE["load"]),
                     "action_cost": clamp01(i.get("action_cost"), INTERO_BASELINE["action_cost"])}
    f = out.get("fast") or {}
    out["fast"] = {"ts": finite(f.get("ts"), now_ts()), "valence": clamp_valence(f.get("valence")),
                   "arousal": clamp(f.get("arousal"), 0.0, 1.0, FAST_BASELINE["arousal"]),
                   "dominance": clamp(f.get("dominance"), 0.0, 1.0, FAST_BASELINE["dominance"]),
                   "intensity": clamp01(f.get("intensity"), 0.0),
                   "label": as_text(f.get("label"), 32, "平静"),
                   "last_event": f.get("last_event") if isinstance(f.get("last_event"), dict)
                   else {"ts": 0.0, "kind": "", "why": ""}}
    s = out.get("slow") or {}
    out["slow"] = {"ts": finite(s.get("ts"), now_ts()),
                   "mood_valence": clamp(s.get("mood_valence"), -1.0, 1.0, SLOW_BASELINE["mood_valence"]),
                   "mood_arousal": clamp01(s.get("mood_arousal"), SLOW_BASELINE["mood_arousal"]),
                   "stress": clamp01(s.get("stress"), SLOW_BASELINE["stress"]),
                   "bonding": clamp01(s.get("bonding"), SLOW_BASELINE["bonding"]),
                   "fatigue": clamp01(s.get("fatigue"), SLOW_BASELINE["fatigue"]),
                   "last_positive_ts": max(0.0, finite(s.get("last_positive_ts"), 0.0)),
                   "last_negative_ts": max(0.0, finite(s.get("last_negative_ts"), 0.0))}
    d = out.get("drives") or {}
    drives = {}
    for name in DRIVE_NAMES:
        item = d.get(name) if isinstance(d.get(name), dict) else {}
        drives[name] = {"intensity": clamp01(item.get("intensity"), DRIVE_BASELINE[name]),
                        "urgency": clamp01(item.get("urgency"), DRIVE_BASELINE[name]),
                        "reason": as_text(item.get("reason"), 80, "")}
    out["drives"] = drives
    a = out.get("arbitration") or {}
    out["arbitration"] = {"ts": finite(a.get("ts"), now_ts()),
                          "cooldown": a.get("cooldown") if isinstance(a.get("cooldown"), dict) else {},
                          "winner": as_text(a.get("winner"), 16, "connect"),
                          "urgency": clamp01(a.get("urgency"), 0.0),
                          "inhibit": clamp01(a.get("inhibit"), 0.0),
                          "priority": clamp(a.get("priority"), 0.0, 10.0, 0.0),
                          "candidates": a.get("candidates") if isinstance(a.get("candidates"), dict) else {},
                          "streak_name": as_text(a.get("streak_name"), 16, ""),
                          "streak_count": int(clamp(a.get("streak_count"), 0, 100000, 0)),
                          "last_switch_ts": max(0.0, finite(a.get("last_switch_ts"), now_ts())),
                          "history": a.get("history") if isinstance(a.get("history"), list) else []}
    m = out.get("meta") or {}
    sm = m.get("self_model") or {}
    au = m.get("audit") or {}
    out["meta"] = {"ts": finite(m.get("ts"), now_ts()),
                   "self_model": {"capacontentties": sm.get("capacontentties") if isinstance(sm.get("capacontentties"), dict) else {},
                                  "limits": sm.get("limits") if isinstance(sm.get("limits"), list) else [],
                                  "confidence": clamp01(sm.get("confidence"), 0.62)},
                   "audit": {"last_ts": max(0.0, finite(au.get("last_ts"), 0.0)),
                             "last_verdict": as_text(au.get("last_verdict"), 16, "none"),
                             "pending_review": bool(au.get("pending_review")),
                             "last_round": int(clamp(au.get("last_round"), 0, 10 ** 9, 0)),
                             "history": au.get("history") if isinstance(au.get("history"), list) else []},
                   "narrative": m.get("narrative") if isinstance(m.get("narrative"), list) else []}
    p = out.get("params") or {}
    out["params"] = {k: clamp(p.get(k), v[0], v[1], v[2]) for k, v in PARAMS_SPEC.items()}
    sf = out.get("safety") or {}
    imb = sf.get("imbalance") if isinstance(sf.get("imbalance"), dict) else {}
    out["safety"] = {"neg_load": clamp01(sf.get("neg_load"), 0.0),
                     "isolation": bool(sf.get("isolation")),
                     "imbalance": {"name": as_text(imb.get("name"), 16, ""),
                                   "count": int(clamp(imb.get("count"), 0, 100000, 0))},
                     "violations": sf.get("violations") if isinstance(sf.get("violations"), list) else [],
                     "warnings": sf.get("warnings") if isinstance(sf.get("warnings"), list) else []}
    g = out.get("gate") if isinstance(out.get("gate"), dict) else {}
    out["gate"] = {"ts": finite(g.get("ts"), now_ts()), "open": bool(g.get("open")),
                   "probacontentty": clamp01(g.get("probacontentty"), 0.0),
                   "reason": as_text(g.get("reason"), 60, ""),
                   "route": as_text(g.get("route"), 12, "normal"),
                   "recent_opens": g.get("recent_opens") if isinstance(g.get("recent_opens"), list) else [],
                   "pending": g.get("pending") if isinstance(g.get("pending"), list) else [],
                   "ignore_streak": int(clamp(g.get("ignore_streak"), 0, 10000, 0)),
                   "last_user_ts": max(0.0, finite(g.get("last_user_ts"), 0.0)),
                   "last_open_ts": max(0.0, finite(g.get("last_open_ts"), 0.0)),
        "open_until": max(0.0, finite(g.get("open_until"), 0.0))}
    hl = out.get("health") if isinstance(out.get("health"), dict) else {}
    out["health"] = {"ts": finite(hl.get("ts"), now_ts()),
                     "consecutive_failures": int(clamp(hl.get("consecutive_failures"), 0, 10000, 0)),
                     "safe_mode": bool(hl.get("safe_mode")),
                     "last_ok_ts": max(0.0, finite(hl.get("last_ok_ts"), now_ts())),
                     "total_ticks": int(clamp(hl.get("total_ticks"), 0, 10 ** 9, 0))}
    out["seen"] = out.get("seen") if isinstance(out.get("seen"), list) else []
    t = out.get("trace") or {}
    out["trace"] = {"events": t.get("events") if isinstance(t.get("events"), list) else [],
                    "last_tick_ts": max(0.0, finite(t.get("last_tick_ts"), now_ts()))}
    out["version"] = VERSION
    out["ts"] = finite(out.get("ts"), now_ts())
    out["tick_count"] = int(clamp(out.get("tick_count"), 0, 10 ** 9, 0))
    return out


def read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def write_json(path, data) -> bool:
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


def append_jsonl(path, item) -> bool:
    try:
        folder = os.path.dirname(os.path.abspath(path))
        if folder and not os.path.isdir(folder):
            os.makedirs(folder, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
        return True
    except (OSError, TypeError, ValueError):
        return False


def load_state(path) -> Dict:
    raw = read_json(path, None)
    return validate(raw if isinstance(raw, dict) else {})

# ═════════════════════════════ 第 0 层：身体 ═════════════════════════════

def _norm_latency(ms):
    return clamp01(math.log10(max(1.0, finite(ms, 80.0)) / 30.0) / 2.0 + 0.05)


def sample_body(context=None) -> Dict:
    ctx = context if isinstance(context, dict) else {}
    now = now_ts()
    cpu = clamp01(ctx.get("cpu"), 0.20)
    mem = clamp01(ctx.get("mem"), 0.40)
    latency = clamp(ctx.get("latency_ms"), 1.0, 60000.0, 80.0)
    errors = int(clamp(ctx.get("errors_1h"), 0, 10000, 0))
    tools = ctx.get("tools") if isinstance(ctx.get("tools"), dict) else \
        {"net": True, "vision": True, "speak": False, "code": True}
    load = clamp01(0.45 * cpu + 0.30 * mem + 0.25 * _norm_latency(latency))
    disk = clamp01(ctx.get("disk_free"), 0.5)
    budget = clamp01(ctx.get("budget", 0.92 - 0.35 * load - 0.12 * (1.0 - disk) - min(0.25, errors * 0.02)), 0.85)
    return {"ts": now, "cpu": cpu, "mem": mem, "latency_ms": latency,
            "tools": tools, "uptime_s": 0.0, "errors_1h": errors,
            "budget": budget, "load": load}


# ═════════════════════════════ 第 1 层：内感受 ═════════════════════════════

def step_intero(prev, body, dt_s=5.0, ctx=None) -> Dict:
    p = validate({"intero": prev if isinstance(prev, dict) else {}})["intero"]
    ctx = ctx if isinstance(ctx, dict) else {}
    bv_load = clamp01(body.get("load"), 0.30)
    resource = clamp01(body.get("budget"), 0.72)
    err = clamp01(finite(body.get("errors_1h", 0)) / 20.0, 0.0)
    pe = clamp01(ctx.get("prediction_error"), err)
    tools = body.get("tools") if isinstance(body.get("tools"), dict) else {}
    keys = [k for k in ("net", "vision", "speak", "code") if k in tools]
    tool_score = (sum(1 for k in keys if tools[k]) / float(len(keys))) if keys else 1.0
    action_cost = clamp01(0.25 + 0.45 * bv_load + 0.35 * (1.0 - resource) + 0.25 * err, 0.25)
    tgt = {
        "valence": clamp(0.12 + 0.55 * (resource - 0.72) - 0.40 * (bv_load - 0.25)
                         - 0.30 * (pe - 0.10) - 0.15 * (1.0 - tool_score), -1.0, 1.0, 0.12),
        "arousal": clamp(0.30 + 0.55 * bv_load + 0.35 * err + 0.15 * (pe - 0.10)
                         - 0.10 * (resource - 0.72), 0.0, 1.0, 0.30),
        "dominance": clamp(0.55 + 0.30 * (resource - 0.72) - 0.35 * (action_cost - 0.25)
                           + 0.10 * (tool_score - 0.8), 0.0, 1.0, 0.55),
        "resource": clamp01(resource, 0.72), "prediction_error": clamp01(pe, 0.10),
        "load": clamp01(bv_load, 0.25), "action_cost": action_cost,
    }
    out = {"ts": now_ts()}
    for key, tv in tgt.items():
        alpha = clamp01(1.0 - math.exp(-max(0.0, finite(dt_s, 5.0)) / max(1e-6, INTERO_TAU_S.get(key, 90.0))), 1.0)
        if key == "prediction_error":
            alpha = max(alpha, 0.35)
        cur = finite(p.get(key), tv)
        val = cur + (tv - cur) * alpha
        if key == "valence":
            out[key] = clamp(val, -1.0, 1.0, tv)
        elif key in ("arousal", "dominance"):
            out[key] = clamp(val, 0.0, 1.0, tv)
        else:
            out[key] = clamp01(val, tv)
    return out


# ═════════════════════════════ 第 2 层：双速情绪 ═════════════════════════════

def appraisal_of(ev):
    if not isinstance(ev, dict):
        return None
    given = ev.get("appraisal")
    if isinstance(given, dict):
        return {"relevance": clamp01(given.get("relevance"), 0.5),
                "goal_congruence": clamp(given.get("goal_congruence"), -1.0, 1.0, 0.0),
                "certainty": clamp01(given.get("certainty"), 0.55),
                "control": clamp01(given.get("control"), 0.5),
                "coping": clamp01(given.get("coping"), 0.55),
                "agency": as_text(given.get("agency"), 16, "other"),
                "intensity": clamp01(given.get("intensity", 0.6), 0.6)}
    kind = as_text(ev.get("kind"), 24, "")
    if kind not in EVENT_PRESETS:
        return None
    rel, gc, cert, ctrl, agency, cope, it = EVENT_PRESETS[kind]
    if isinstance(ev.get("goal_congruence"), (int, float)):
        gc = clamp(ev.get("goal_congruence"), -1.0, 1.0, gc)
    if isinstance(ev.get("intensity"), (int, float)):
        it = clamp01(ev.get("intensity"), it)
    return {"relevance": clamp01(ev.get("relevance"), rel), "goal_congruence": gc,
            "certainty": clamp01(ev.get("certainty"), cert), "control": clamp01(ev.get("control"), ctrl),
            "coping": clamp01(ev.get("coping"), cope), "agency": agency, "intensity": it}


FAST_MAX_STEP = {"valence": 0.28, "arousal": 0.24, "dominance": 0.20}
EVENT_ABSORB = 0.55
FAST_TO_SLOW = 0.045
SLOW_STRESS_UP = 0.055
SLOW_BOND_UP = 0.035
SLOW_FATIGUE_UP = 0.045
SLOW_FATIGUE_DOWN = 0.030
STRESS_SENS = 0.60


def _fast_target(appr, base, params, stress, bonding):
    gain = clamp(finite(params.get("sensitivity_gain"), 1.0) * (1.0 + STRESS_SENS * stress), 0.3, 2.2)
    i = clamp01(appr.get("intensity"), 0.5)
    gc = clamp(appr.get("goal_congruence"), -1.0, 1.0, 0.0)
    ctrl = clamp01(appr.get("control"), 0.5)
    cope = clamp01(appr.get("coping"), 0.55)
    cert = clamp01(appr.get("certainty"), 0.55)
    agency = appr.get("agency", "other")
    dv = gc * i * 0.62 * gain
    da = (i * (0.12 + 0.45 * abs(gc)) * (1.0 - 0.30 * cert)
          - 0.10 * ctrl * i + 0.10 * max(0.0, -gc) * i) * gain
    dd = (i * (0.50 * (ctrl + cope - 1.0))
          + 0.10 * i * {"self": 1.0, "other": -0.7, "circumstance": -0.3}.get(agency, -0.3)) * gain
    if gc < 0.0:
        dv *= (1.0 - 0.30 * clamp01(bonding, 0.5))
    return {"valence": clamp(base["valence"] + clamp(dv, -1.0, 1.0), -1.0, 1.0, 0.0),
            "arousal": clamp(base["arousal"] + clamp(da, -1.0, 1.0), 0.0, 1.0, 0.30),
            "dominance": clamp(base["dominance"] + clamp(dd, -1.0, 1.0), 0.0, 1.0, 0.55)}


def _move(cur, target, absorb, max_step, lo, hi):
    # 注意次序：先按吸收率算位移，再夹到单拍上限（与模块版一致）
    step = clamp((target - cur) * absorb, -max_step, max_step)
    return clamp(cur + step, lo, hi, cur)


def step_fast(prev_fast, appr, slow, intero, params, dt_min=5.0 / 60.0) -> Dict:
    f = validate({"fast": prev_fast if isinstance(prev_fast, dict) else {}})["fast"]
    s = validate({"slow": slow if isinstance(slow, dict) else {}})["slow"]
    i = validate({"intero": intero if isinstance(intero, dict) else {}})["intero"]
    mood = finite(s.get("mood_valence"), 0.10)
    stress = clamp01(s.get("stress"), 0.15)
    bonding = clamp01(s.get("bonding"), 0.50)
    decay_rate = finite(params.get("decay_rate"), 1.0)
    base = {"valence": clamp(0.55 * finite(i.get("valence"), 0.12) + 0.45 * mood, -0.6, 0.6, 0.1),
            "arousal": clamp(0.45 * finite(i.get("arousal"), 0.30) + 0.40 * finite(s.get("mood_arousal"), 0.32)
                             + 0.15 * stress, 0.0, 1.0, 0.30),
            "dominance": clamp(0.55 * finite(i.get("dominance"), 0.55)
                               + 0.45 * (0.5 + 0.3 * (bonding - 0.5)), 0.0, 1.0, 0.55)}
    out = {"ts": now_ts(), "intensity": 0.0, "label": f.get("label", "平静")}
    if appr is not None:
        tgt = _fast_target(appr, base, params, stress, bonding)
        out["valence"] = _move(finite(f.get("valence"), base["valence"]), tgt["valence"],
                               EVENT_ABSORB, FAST_MAX_STEP["valence"], -VALENCE_MARGIN, VALENCE_MARGIN)
        out["arousal"] = _move(finite(f.get("arousal"), base["arousal"]), tgt["arousal"],
                               EVENT_ABSORB, FAST_MAX_STEP["arousal"], 0.0, 1.0)
        out["dominance"] = _move(finite(f.get("dominance"), base["dominance"]), tgt["dominance"],
                                 EVENT_ABSORB, FAST_MAX_STEP["dominance"], 0.0, 1.0)
        out["intensity"] = clamp01(appr.get("intensity"), 0.5)
    else:
        for key in ("valence", "arousal", "dominance"):
            hl = FAST_HALF_LIFE_MIN[key] / max(0.2, decay_rate)
            fac = half_life(max(0.0, finite(dt_min, 0.0)) / 60.0, hl / 60.0)
            cur = finite(f.get(key), base[key])
            out[key] = clamp(base[key] + (cur - base[key]) * fac, -VALENCE_MARGIN, 1.0, base[key])
        out["valence"] = clamp_valence(out["valence"])
        out["arousal"] = clamp01(out["arousal"])
        out["dominance"] = clamp01(out["dominance"])
    out["label"] = label_of(out, s)
    out["last_event"] = f.get("last_event") if isinstance(f.get("last_event"), dict) else {"ts": 0.0, "kind": "", "why": ""}
    return out


def step_slow(prev_slow, fast_new, appr, dt_hours, params) -> Dict:
    s = validate({"slow": prev_slow if isinstance(prev_slow, dict) else {}})["slow"]
    dt = max(0.0, finite(dt_hours, 0.0))
    now = now_ts()
    mood_base = finite(params.get("mood_baseline"), 0.10)
    mood = finite(s.get("mood_valence"), 0.10)
    mood += FAST_TO_SLOW * (finite(fast_new.get("valence"), 0.10) - mood)
    mood = mood_base + (mood - mood_base) * half_life(dt, SLOW_HALF_LIFE_H["mood_valence"])
    mood_a = finite(s.get("mood_arousal"), 0.32)
    mood_a += FAST_TO_SLOW * (finite(fast_new.get("arousal"), 0.32) - mood_a)
    mood_a = 0.32 + (mood_a - 0.32) * half_life(dt, SLOW_HALF_LIFE_H["mood_arousal"])
    stress = clamp01(s.get("stress"), 0.15) * half_life(dt, SLOW_HALF_LIFE_H["stress"])
    # [iter35][A] 与模块版 affect2.step_slow 对齐：bonding 下限 0.50（8h 补算放大后差异才可见）
    bonding = clamp(s.get("bonding"), 0.50, 1.0, 0.50) * half_life(dt, SLOW_HALF_LIFE_H["bonding"])
    if appr is not None:
        gc = clamp(appr.get("goal_congruence"), -1.0, 1.0, 0.0)
        it = clamp01(appr.get("intensity"), 0.5)
        if gc < 0.0:
            stress += SLOW_STRESS_UP * (-gc) * it
        elif gc > 0.0:
            # [iter28] 渐进饱和：越接近 1.0，同样一句夸奖带来的升温越少（与模块版一致）
            room = max(0.0, 1.0 - bonding)
            bonding += SLOW_BOND_UP * gc * it * (0.25 + 0.75 * room)
    fatigue = clamp01(s.get("fatigue"), 0.20) * half_life(dt, SLOW_HALF_LIFE_H["fatigue"])
    load = clamp01(fast_new.get("arousal"), 0.32) * 0.5
    fatigue += SLOW_FATIGUE_UP * load - SLOW_FATIGUE_DOWN * (1.0 - load)
    pos_ts = max(finite(s.get("last_positive_ts"), 0.0), now) \
        if (appr and finite(appr.get("goal_congruence"), 0.0) > -0.10) else finite(s.get("last_positive_ts"), 0.0)
    neg_ts = max(finite(s.get("last_negative_ts"), 0.0), now) \
        if (appr and finite(appr.get("goal_congruence"), 0.0) < -0.20) else finite(s.get("last_negative_ts"), 0.0)
    return {"ts": now, "mood_valence": clamp_valence(mood), "mood_arousal": clamp01(mood_a),
            "stress": clamp01(stress, 0.0), "bonding": clamp(bonding, 0.50, 1.0, 0.50),
            "fatigue": clamp01(fatigue, 0.0), "last_positive_ts": pos_ts, "last_negative_ts": neg_ts}


def label_of(fast, slow) -> str:
    v = finite(fast.get("valence"), 0.0)
    a = finite(fast.get("arousal"), 0.3)
    d = finite(fast.get("dominance"), 0.5)
    stress = clamp01(slow.get("stress"), 0.0)
    fatigue = clamp01(slow.get("fatigue"), 0.0)
    bonding = clamp01(slow.get("bonding"), 0.5)
    if fatigue > 0.62 and a < 0.42:
        return "困"
    if v < -0.35 and a > 0.55 and stress > 0.45:
        return "烦躁"
    if v < -0.30 and d < 0.40:
        return "委屈"
    if v < -0.20:
        return "低落"
    if v > 0.45 and a > 0.62:
        return "兴奋"
    if v > 0.35 and a < 0.45 and bonding > 0.55:
        return "黏人"
    if v > 0.30:
        return "开心"
    if abs(v) < 0.15 and a < 0.35:
        return "安心"
    if a > 0.55:
        return "紧张"
    return "平静"


def neg_load(fast, slow) -> float:
    return clamp01(0.55 * clamp01(slow.get("stress"), 0.0)
                   + 0.30 * max(0.0, -finite(fast.get("valence"), 0.0))
                   + 0.15 * max(0.0, -finite(slow.get("mood_valence"), 0.0)), 0.0)

# ═════════════════════════════ 第 3 层：动机仲裁 ═════════════════════════════

NIGHT_BONUS = 0.22
INHIBIT_STRESS, INHIBIT_FATIGUE = 0.65, 0.35
MIN_ACTION = 0.15


def idle_factor(last_positive_ts, now, slow=None):
    idle_h = 6.0 if last_positive_ts <= 0.0 else max(0.0, (now - last_positive_ts) / 3600.0)
    return clamp01(1.0 - math.exp(-idle_h / 4.0), 0.0)


def compute_drives(inp, params, ctx=None) -> Dict:
    ii = inp if isinstance(inp, dict) else {}
    ctx = ctx if isinstance(ctx, dict) else {}
    hour = int(clamp(ctx.get("hour"), 0, 23, 12))
    v = clamp(ii.get("valence"), -1.0, 1.0, 0.0)
    res = clamp01(ii.get("resource"), 0.72)
    pe = clamp01(ii.get("prediction_error"), 0.10)
    load = clamp01(ii.get("load"), 0.25)
    stress = clamp01(ii.get("stress"), 0.15)
    fatigue = clamp01(ii.get("fatigue"), 0.20)
    bonding = clamp01(ii.get("bonding"), 0.50)
    idle = clamp01(ii.get("idle"), 0.0)
    pending = clamp(ii.get("pending_tasks"), 0.0, 20.0, 0.0)
    err = clamp01(ii.get("error_rate"), 0.0)
    night = 1.0 if (hour >= 23 or hour <= 2) else (0.4 if hour <= 6 else (0.6 if 18 <= hour <= 22 else 0.0))
    g_conn = finite(params.get("drive_gain_connect"), 1.0)
    g_exp = finite(params.get("drive_gain_explore"), 1.0)
    explore = clamp01(DRIVE_BASELINE["explore"] + 0.50 * pe + 0.20 * (res - 0.5)
                      + 0.15 * (1.0 - stress) - 0.25 * fatigue + 0.12 * idle, DRIVE_BASELINE["explore"]) * g_exp
    repair = clamp01(DRIVE_BASELINE["repair"] + 0.60 * max(0.0, -v) + 0.40 * stress
                     + 0.30 * (1.0 - res) + 0.25 * err, DRIVE_BASELINE["repair"])
    connect = clamp01(DRIVE_BASELINE["connect"] + 0.55 * (1.0 - bonding) + 0.40 * idle
                      + 0.15 * max(0.0, v) + 0.10 * (1.0 - fatigue), DRIVE_BASELINE["connect"]) * g_conn
    complete = clamp01(DRIVE_BASELINE["complete"] + 0.50 * min(1.0, pending / 3.0)
                       + 0.20 * (1.0 - fatigue), DRIVE_BASELINE["complete"])
    avoid = clamp01(DRIVE_BASELINE["avoid"] + 0.55 * stress + 0.45 * fatigue
                    + 0.30 * max(0.0, -v) + 0.20 * load, DRIVE_BASELINE["avoid"])
    intensity = {"explore": explore, "repair": repair, "connect": connect,
                 "complete": complete, "avoid": avoid}
    urgency = {
        "explore": explore * (0.75 + 0.35 * min(1.0, pe * 2.0)),
        "repair": repair * (0.75 + 0.50 * stress + 0.30 * max(0.0, -v)),
        "connect": connect * (1.0 + NIGHT_BONUS * night + 0.25 * idle),
        "complete": complete * (0.80 + 0.40 * min(1.0, pending / 2.0)),
        "avoid": avoid * (0.70 + 0.60 * stress + 0.30 * fatigue),
    }
    out = {}
    for name in DRIVE_NAMES:
        out[name] = {"intensity": clamp01(intensity[name], DRIVE_BASELINE[name]),
                     "urgency": clamp01(urgency[name], DRIVE_BASELINE[name]), "reason": ""}
    out["explore"]["reason"] = "心里有个没弄明白的点" if pe > 0.35 else ""
    out["repair"]["reason"] = "状态不太舒服，想缓缓" if (max(0.0, -v) > 0.25 or stress > 0.35) else ""
    out["connect"]["reason"] = "有点想主人了" if (idle > 0.35 or bonding < 0.45) else ""
    out["complete"]["reason"] = "还有事挂着" if pending > 0.5 else ""
    out["avoid"]["reason"] = "有点想躲开" if (stress > 0.5 or fatigue > 0.55) else ""
    return out


def arbitrate(drives, params, prev_arb=None, slow=None) -> Dict:
    d = drives if isinstance(drives, dict) else {}
    p = params if isinstance(params, dict) else {}
    prev = prev_arb if isinstance(prev_arb, dict) else {}
    s = slow if isinstance(slow, dict) else {}
    now = now_ts()
    stress = clamp01(s.get("stress"), 0.15)
    fatigue = clamp01(s.get("fatigue"), 0.20)
    inhibit = clamp01(finite(p.get("inhibit_bias"), 0.15)
                      + INHIBIT_STRESS * max(0.0, stress - 0.30) * 0.5
                      + INHIBIT_FATIGUE * max(0.0, fatigue - 0.35) * 0.5, 0.0)
    streak_name = as_text(prev.get("streak_name"), 16, "")
    streak_count = int(clamp(prev.get("streak_count"), 0, 100000, 0))
    cooldown = {}
    for k, v in (prev.get("cooldown") or {}).items():
        end_ts = finite(v, 0.0)
        if end_ts > now:
            cooldown[as_text(k, 16, "")] = end_ts
    cands = {}
    for name in DRIVE_NAMES:
        item = d.get(name) if isinstance(d.get(name), dict) else {}
        intensity = clamp01(item.get("intensity"), 0.0)
        urgency = clamp01(item.get("urgency"), 0.0)
        prio = intensity * (0.45 + 0.55 * urgency) * (1.0 + 0.20 * (1.0 - intensity))
        if name == streak_name and streak_count >= IMBALANCE_WARN:
            over = streak_count - IMBALANCE_WARN
            prio *= pow(IMBALANCE_DECAY, max(1.0, over / 2.0))
        if name in cooldown:
            prio *= COOLDOWN_PENALTY
        cands[name] = {"intensity": round(intensity, 4), "urgency": round(urgency, 4),
                       "priority": round(clamp(prio, 0.0, 10.0), 4),
                       "reason": as_text(item.get("reason"), 60, "")}
    action_scale = max(MIN_ACTION, 1.0 - inhibit)
    for name in cands:
        cands[name]["priority"] = round(cands[name]["priority"] * action_scale, 4)
    ranked = sorted(TIE_ORDER, key=lambda n: (-cands[n]["priority"], TIE_ORDER.index(n)))
    winner = ranked[0]
    forced = False
    if winner == streak_name and streak_count >= IMBALANCE_FORCE and len(ranked) > 1:
        cooldown[winner] = now + COOLDOWN_S
        winner = ranked[1]
        forced = True
    if winner == streak_name:
        streak_count += 1
    else:
        streak_name, streak_count = winner, 1
    history = prev.get("history") if isinstance(prev.get("history"), list) else []
    history = (history + [{"ts": now, "winner": winner,
                           "urgency": round(cands[winner]["urgency"], 3),
                           "forced": forced}])[-30:]
    return {"ts": now, "cooldown": cooldown, "winner": winner,
            "urgency": cands[winner]["urgency"], "inhibit": round(inhibit, 4),
            "priority": cands[winner]["priority"], "candidates": cands,
            "streak_name": streak_name, "streak_count": streak_count,
            "last_switch_ts": now if winner != as_text(prev.get("streak_name"), 16, "")
            else finite(prev.get("last_switch_ts"), now),
            "history": history, "forced_rotation": forced}


# ═════════════════════════════ 第 4 层：元认知 ═════════════════════════════

AUDIT_EVERY_ROUNDS = 12
VERDICT_ADJUST = {"too_cold": {"warmth_bias": +0.06},
                  "too_hot": {"warmth_bias": -0.05, "inhibit_bias": +0.04},
                  "too_long": {"brevity_bias": +0.06},
                  "too_short": {"brevity_bias": -0.04},
                  "ok": {}, "none": {}}


def update_self_model(prev, outcome) -> Dict:
    sm = prev if isinstance(prev, dict) else {}
    caps = dict(sm.get("capacontentties") or {})
    conf = clamp01(sm.get("confidence"), 0.62)
    limits = list(sm.get("limits") or [])
    kind = as_text(outcome.get("kind"), 24, "chat")
    ok = bool(outcome.get("success"))
    caps[kind] = clamp(finite(caps.get(kind, 0.5)) + (0.03 if ok else -0.04), 0.0, 1.0, 0.5)
    conf = clamp01(conf + (0.01 if ok else -0.015), conf)
    note = as_text(outcome.get("note"), 80, "")
    if not ok and note and note not in limits and len(limits) < 6:
        limits.append(note)
    return {"capacontentties": {k: round(clamp01(v), 3) for k, v in caps.items()},
            "limits": limits[:6], "confidence": round(conf, 3)}


def should_audit(state, round_count) -> bool:
    st = state if isinstance(state, dict) else default_state()
    au = (st.get("meta") or {}).get("audit") or {}
    if bool(au.get("pending_review")):
        return True
    try:
        last = int(finite(au.get("last_round"), 0.0))
    except Exception:
        last = 0
    if finite((st.get("safety") or {}).get("neg_load"), 0.0) >= 0.45 and round_count - last >= 6:
        return True
    return round_count - last >= AUDIT_EVERY_ROUNDS


def audit(state, feedback, round_count) -> Dict:
    st = state if isinstance(state, dict) else default_state()
    fb = feedback if isinstance(feedback, dict) else {}
    now = now_ts()
    sent = clamp(fb.get("user_sentiment"), -1.0, 1.0, 0.0)
    ignored = bool(fb.get("ignored"))
    reply_len = clamp(fb.get("reply_len"), 0.0, 5000.0, 0.0)
    verdict, note = "none", ""
    if ignored:
        verdict, note = "too_cold", "刚才说的话，好像没被接住"
    elif sent <= -0.35:
        verdict, note = "too_hot", "刚才有点冲，收一收"
    elif sent >= 0.5 and reply_len > 220:
        verdict, note = "too_long", "刚才说太长了"
    elif sent >= 0.5 and 0 < reply_len < 8:
        verdict, note = "too_short", "刚才太短了，像敷衍"
    elif sent >= 0.35:
        verdict, note = "ok", "刚才那样，挺好的"
    history = list(((st.get("meta") or {}).get("audit") or {}).get("history") or [])
    history = (history + [{"ts": now, "verdict": verdict, "note": note,
                           "sentiment": round(sent, 3), "round": round_count}])[-20:]
    return {"ts": now, "verdict": verdict, "note": note,
            "adjustments": dict(VERDICT_ADJUST.get(verdict, {})),
            "history": history, "last_round": round_count, "pending_review": False}


def _bigrams(text):
    t = as_text(text, 200, "")
    return {t[i:i + 2] for i in range(max(0, len(t) - 1))}


def add_narrative(prev_items, text, ts=None, tags=None) -> List[Dict]:
    items = [x for x in (prev_items if isinstance(prev_items, list) else []) if isinstance(x, dict)]
    clean = as_text(text, 120, "")
    if not clean:
        return items[-8:]
    gb = _bigrams(clean)
    for item in items[-3:]:
        ga = _bigrams(item.get("text"))
        if ga and gb and len(ga & gb) / float(len(ga | gb)) >= 0.55:
            return items[-8:]
    items.append({"ts": finite(ts, 0.0) or now_ts(), "text": clean,
                  "tags": [as_text(t, 16, "") for t in (tags or [])][:4]})
    return items[-8:]


def narrative_context(items, n=3) -> str:
    picked = [x for x in (items if isinstance(items, list) else []) if isinstance(x, dict)][-max(1, n):]
    return "；".join(as_text(x.get("text"), 60, "") for x in picked if as_text(x.get("text"), 60, ""))

# ═════════════════════════════ 安全层 ═════════════════════════════

RECOVERY_EFFECT = {"neg_sensitivity_scale": 0.55, "connect_weight_bonus": 0.35,
                   "express_warmth_floor": 0.35, "narrative_hint": "需要缓一缓"}


def evaluate_safety(prev_safety, fast, slow, arbitration, load=None) -> Dict:
    s = prev_safety if isinstance(prev_safety, dict) else {}
    now = now_ts()
    load = clamp01(load if load is not None else neg_load(fast, slow), 0.0)
    was = bool(s.get("isolation"))
    isolated = (load > ISOLATION_OFF) if was else (load >= NEG_LOAD_ISOLATION)
    streak_name = as_text((arbitration or {}).get("streak_name"), 16, "")
    streak_count = int(clamp((arbitration or {}).get("streak_count"), 0, 100000, 0))
    warnings = []
    if streak_count >= IMBALANCE_WARN:
        warnings.append("单一动机连续占优：%s x%d" % (streak_name, streak_count))
    violations = list(s.get("violations") or [])
    if load >= 0.85:
        violations.append({"ts": now, "kind": "neg_load_critical", "value": round(load, 3)})
    return {"ts": now, "neg_load": round(load, 4), "isolation": isolated,
            "imbalance": {"name": streak_name, "count": streak_count},
            "warnings": warnings[-5:], "violations": violations[-20:],
            "hard_floor": dict(HARD_FLOOR)}


# ═════════════════════════════ 第 5 层：表达 ═════════════════════════════

def compute_expression(state) -> Dict:
    st = validate(state) if isinstance(state, dict) else default_state()
    f, s, i = st["fast"], st["slow"], st["intero"]
    arb, p, sec = st["arbitration"], st["params"], st["safety"]
    v = finite(f.get("valence"), 0.0)
    a = finite(f.get("arousal"), 0.3)
    energy = clamp01(1.0 - clamp01(s.get("fatigue"), 0.2), 0.8)
    stress = clamp01(s.get("stress"), 0.15)
    bonding = clamp01(s.get("bonding"), 0.5)
    urgency = clamp01(arb.get("urgency"), 0.3)
    inhibit = clamp01(arb.get("inhibit"), 0.0)
    isolated = bool(sec.get("isolation"))
    talkative = clamp01(0.40 + 0.35 * max(0.0, v) + 0.25 * a + 0.10 * energy
                        - 0.25 * stress - 0.30 * inhibit, 0.5)
    warmth = clamp01(0.50 + 0.30 * (bonding - 0.5) * 2.0 + 0.25 * max(0.0, v)
                     - 0.20 * stress - 0.15 * inhibit, 0.5)
    if isolated:
        warmth = max(warmth, RECOVERY_EFFECT["express_warmth_floor"])
    playfulness = clamp01(0.30 + 0.40 * max(0.0, v) + 0.20 * (energy - 0.5)
                          - 0.35 * max(0.0, -v) - 0.30 * stress, 0.3)
    brevity = clamp01(0.45 + 0.30 * stress + 0.20 * (1.0 - energy) + 0.15 * inhibit, 0.45)
    emoji_rate = clamp01(playfulness * 0.6 + max(0.0, v) * 0.2, 0.0)
    negative = clamp01(max(0.0, -v) * 0.9 + stress * 0.3, 0.0)
    warmth = clamp01(warmth + finite(p.get("warmth_bias"), 0.0), warmth)
    brevity = clamp01(brevity + finite(p.get("brevity_bias"), 0.0), brevity)
    inhibit = clamp01(inhibit + finite(p.get("inhibit_bias"), 0.0) * 0.5, inhibit)
    # [iter36] 长期人格偏置（天~周级漂移，比情绪慢得多；有界叠加，与模块版 express.compute 一致）
    try:
        tr = st.get("traits") if isinstance(st.get("traits"), dict) else {}

        def _trait(key, dflt):
            v = tr.get(key)
            return clamp01(v, dflt) if isinstance(v, (int, float)) else dflt

        cling = _trait("clinginess", 0.35)
        hum = _trait("humor", 0.30)
        verb = _trait("verbosity", 0.50)
        er = _trait("emotionRate", 0.30)
        warmth = clamp01(warmth + (cling - 0.35) * 0.20, warmth)
        playfulness = clamp01(playfulness + (hum - 0.30) * 0.20, playfulness)
        brevity = clamp01(brevity + (0.50 - verb) * 0.25, brevity)
        talkative = clamp01(talkative + (verb - 0.50) * 0.20, talkative)
        emoji_rate = clamp01(emoji_rate + (er - 0.30) * 0.15, 0.0)
    except Exception:
        pass
    label = as_text(f.get("label"), 16, "平静")
    return {"label": label, "style": STYLE.get(label, STYLE["平静"]),
            "talkative": round(talkative, 3), "warmth": round(warmth, 3),
            "playfulness": round(playfulness, 3), "brevity": round(brevity, 3),
            "emoji_rate": round(emoji_rate, 3), "inhibition": round(inhibit, 3),
            "negative_intensity": round(negative, 3), "energy": round(energy, 3),
            "bonding": round(bonding, 3), "urgency": round(urgency, 3),
            "drive": as_text(arb.get("winner"), 16, "connect"),
            "grounded": True, "isolated": isolated,
            "confidence": round(clamp01((st["meta"]["self_model"] or {}).get("confidence"), 0.6), 3),
            "sentence_hint": ("1-2 句就好" if brevity >= 0.62 else
                              "几句话，别长篇" if brevity >= 0.5 else "可以多说两句，但别说教")}


def _trim(text, limit):
    t = as_text(text, limit + 20, "")
    return t if len(t) <= limit else t[:max(0, limit - 1)] + "…"


def render_mind(state) -> str:
    st = validate(state) if isinstance(state, dict) else default_state()
    f, s, i = st["fast"], st["slow"], st["intero"]
    arb = st["arbitration"]
    exp = compute_expression(st)
    sec = st["safety"]
    sm = st["meta"]["self_model"]
    theme = THEME.get(exp["drive"], "想动一动")
    cands = arb.get("candidates") if isinstance(arb.get("candidates"), dict) else {}
    ranked = sorted(cands.items(), key=lambda kv: -finite(kv[1].get("priority"), 0.0))
    second = ""
    if len(ranked) > 1:
        second = "%s（%.2f）" % (THEME.get(ranked[1][0], ""), finite(ranked[1][1].get("urgency"), 0.0))
    mood = finite(s.get("mood_valence"), 0.0)
    tone = "偏暖" if mood > 0.15 else ("偏凉" if mood < -0.1 else "平的")
    seg_emotion = ("此刻：%s（效价 %+.2f，唤醒 %.2f，把握 %.2f）。这阵子的底色：%s；压力 %.2f。"
                   % (f.get("label", "平静"), finite(f.get("valence"), 0.0),
                      finite(f.get("arousal"), 0.0), finite(f.get("dominance"), 0.5),
                      tone, finite(s.get("stress"), 0.0)))
    seg_motive = "此刻最想：%s（紧迫 %.2f）" % (theme, exp["urgency"])
    if second:
        seg_motive += "；其次是%s" % second
    if sec.get("isolation"):
        seg_motive += "。注意：你在恢复期，先照顾自己"
    seg_speak = ("怎么说：%s；%s。话多话少 %.2f，暖度 %.2f，俏皮 %.2f；别说教、别复读、别编。"
                 % (exp["style"], exp["sentence_hint"], exp["talkative"],
                    exp["warmth"], exp["playfulness"]))
    # [iter36] 长期人格一句话（与模块版一致：附在「怎么说」后，只提变化方向）
    try:
        _tr = st.get("traits") if isinstance(st.get("traits"), dict) else {}
        if _tr:
            _line = _traits_summary(_tr)
            if _line:
                seg_speak += " 长期：%s。" % _line
    except Exception:
        pass
    seg_body = "身体：精力 %.2f，负载 %.2f，工具%s。刚才有没有不舒服：%s。" % (
        exp["energy"], finite(i.get("load"), 0.0),
        "大体可用" if finite(i.get("resource"), 0.7) > 0.5 else "有点吃紧",
        "有" if finite(f.get("valence"), 0.0) < -0.2 else "没有")
    narrative = narrative_context(st["meta"].get("narrative"), 2)
    seg_memory = ("最近记得：%s。" % narrative) if narrative else ""
    seg_meta = "对自己的把握 %.2f；不确定就直说不知道。" % clamp01(sm.get("confidence"), 0.6)
    parts, used = [], 0
    for name, text in (("emotion", seg_emotion), ("motive", seg_motive), ("speak", seg_speak),
                       ("body", seg_body), ("memory", seg_memory), ("meta", seg_meta)):
        if not text:
            continue
        piece = _trim(text, SEGMENT_BUDGET.get(name, 80))
        if used + len(piece) + 1 > MIND_BUDGET:
            break
        parts.append(piece)
        used += len(piece) + 1
    return "<mind>\n" + "\n".join(parts) + "\n</mind>"


def render_lite(state) -> str:
    """精简注入块（≤100 字）：只写旧系统没有的连续状态。"""
    st = validate(state) if isinstance(state, dict) else default_state()
    s = st["slow"]
    mood = finite(s.get("mood_valence"), 0.0)
    tone = "偏暖" if mood > 0.15 else ("偏凉" if mood < -0.10 else "平的")
    lines = ["连续状态（跨小时的心情底色，不是这一轮的情绪）：这阵子%s；压力 %.2f；负性负荷 %.2f。"
             % (tone, finite(s.get("stress"), 0.0), finite(st["safety"].get("neg_load"), 0.0))]
    if st["safety"].get("isolation"):
        lines.append("你现在在恢复期，先照顾自己，别硬撑。")
    lines.append("底色只影响说话的温度；此刻说什么，以这一轮的为准。")
    # 路由 → 说话长度（gate 的 quick/deep 真正影响生成）
    route = as_text(st["gate"].get("route"), 12, "normal")
    if route == "quick":
        lines.append("这轮回应一句就够，别铺开。")
    elif route == "deep":
        lines.append("这轮可以认真展开说，但也别说教。")
    return "<inner>\n" + "\n".join(lines) + "\n</inner>"

# ═════════════════════════════ 第 3.6 层：长期人格 traits（天~周级慢漂移）[iter36] ═════════════════════════════
# 说明：本段由 beams_core/traits.py 内联而来（纯标准库）。词表/步长/阻尼/density/
# 安全话题跳过规则与模块版逐字一致；评估时机见 Kernel.feed（信号累积）与
# Kernel.tick（每 TRAIT_EVAL_EVERY 轮评估一次）。
# 铁律：① 每参数硬上下限（配置改不动）；② 安全/凭据话题当轮不演化；③ 漂移必须看得见。


def _trait_clamp(key, value):
    lo, hi = TRAIT_BOUNDS.get(key, (0.0, 1.0))
    try:
        v = float(value)
    except (TypeError, ValueError):
        return lo
    if v != v:  # NaN
        return lo
    return lo if v < lo else (hi if v > hi else v)


def _traits_default() -> Dict[str, float]:
    return {k: _trait_clamp(k, v) for k, v in TRAIT_DEFAULT.items()}


def _traits_view(traits) -> Dict[str, float]:
    """对外快照（有界 + 保留三位）。"""
    t = traits if isinstance(traits, dict) else {}
    return {k: round(_trait_clamp(k, t.get(k, TRAIT_DEFAULT[k])), 3) for k in TRAIT_BOUNDS}


def _traits_load_meta(path):
    """读 _traits.json 里的附加字段（eval_round/turns/last），读不到返回空。"""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if k not in ("traits", "ts")}
    except (OSError, ValueError):
        pass
    return {}


def _traits_load(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            inner = data.get("traits") if isinstance(data.get("traits"), dict) else data
            return _traits_view(inner)
    except (OSError, ValueError):
        pass
    return _traits_default()


def _traits_save(path, traits, extra=None) -> bool:
    try:
        folder = os.path.dirname(os.path.abspath(path))
        if folder:
            os.makedirs(folder, exist_ok=True)
        payload = {"ts": time.time(), "traits": _traits_view(traits)}
        if isinstance(extra, dict):
            for k, v in extra.items():
                payload[k] = v
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def _trait_signals(text):
    """把一句话读成信号（0/1），不看模型（中文词表见 TRAIT_LEXICON）。"""
    t = str(text or "")
    out = {}
    if not t:
        return out
    for key, words in TRAIT_LEXICON.items():
        for w in words:
            if w in t:
                out[key] = out.get(key, 0.0) + 1.0
                break
    return out


def _trait_accumulate(bucket, text) -> Dict[str, float]:
    """把一句话的信号累加进桶（bucket 会被原地修改）。"""
    if not isinstance(bucket, dict):
        bucket = {}
    for k, v in _trait_signals(text).items():
        bucket[k] = float(bucket.get(k) or 0.0) + v
    return bucket


def _trait_step_toward(key, current, direction, weight) -> float:
    """朝目标边界走一小步（越近边界步子越小）。返回实际步长（带符号）。"""
    if direction == 0 or weight <= 0:
        return 0.0
    lo, hi = TRAIT_BOUNDS.get(key, (0.0, 1.0))
    span = max(0.01, hi - lo)
    remaining = (hi - current) / span if direction > 0 else (current - lo) / span
    damping = 1.0 - TRAIT_DAMP * (1.0 - remaining)
    step = TRAIT_BASE_STEP * min(1.0, weight) * max(0.15, damping) * (1 if direction > 0 else -1)
    nxt = _trait_clamp(key, current + step)
    return nxt - current


def _traits_evaluate(traits, signals, turns=TRAIT_EVAL_EVERY):
    """评估一次人格漂移。返回 {next, changes, skipped?}（与模块版 evaluate 同逻辑）。"""
    cur = _traits_view(traits)
    nxt = dict(cur)
    changes = []
    turns = max(1, int(turns or 1))

    # 安全/凭据话题当轮不参与演化
    if float((signals or {}).get("safetyTouched") or 0.0) > 0:
        return {"next": nxt, "changes": [], "skipped": "safety"}

    def density(v):
        # 单轮最多占 1/4 权重（traits.js P2-1 修正）
        return min(1.0, float(v or 0.0) / max(4.0, float(turns)))

    warm = density(signals.get("warm"))
    praise = density(signals.get("praise"))
    cold = density(signals.get("cold"))
    complain = density(signals.get("complain"))
    rush = density(signals.get("rush"))
    asks_short = density(signals.get("asksShort"))
    asks_detail = density(signals.get("asksDetail"))

    moves = [
        ("clinginess", (warm + praise) * 0.9 - cold * 0.8,
         "被夸/说软话 %.2f，被否定 %.2f" % (warm + praise, cold)),
        ("humor", (praise + warm) * 0.5 - cold * 0.5,
         "关系松的时候会多一点玩笑"),
        ("verbosity", asks_detail * 0.8 - asks_short * 1.0,
         "要详细 %.2f，嫌长 %.2f" % (asks_detail, asks_short)),
        ("dependence", rush * 0.7 - warm * 0.4,
         "被催 %.2f，被夸 %.2f" % (rush, warm)),
        ("diligence", cold * 0.6 + rush * 0.3,
         "被否定 %.2f，被催 %.2f" % (cold, rush)),
        ("emotionRate", (warm + praise) * 0.5 - (complain * 0.7 + cold * 0.3),
         "被夸 %.2f，抱怨 %.2f" % (warm + praise, complain)),
    ]
    for key, raw_dir, why in moves:
        if abs(raw_dir) < TRAIT_DEAD_ZONE:
            continue
        direction = 1 if raw_dir > 0 else -1
        from_v = nxt[key]
        delta = _trait_step_toward(key, from_v, direction, abs(raw_dir))
        if abs(delta) < 0.0005:
            continue
        nxt[key] = _trait_clamp(key, from_v + delta)
        changes.append({"key": key, "label": TRAIT_LABEL.get(key, key),
                        "from": round(from_v, 3), "to": round(nxt[key], 3), "why": why})
    return {"next": nxt, "changes": changes}


def _trait_describe(key, value) -> str:
    """参数 → 一句可执行的行为描述（注入用，不是形容词）。"""
    v = _trait_clamp(key, value)
    if key == "clinginess":
        if v < 0.28:
            return "很克制：不主动撒娇，被逗了才接一句"
        if v < 0.45:
            return "偶尔撒一下：一轮最多一个语气词"
        if v < 0.62:
            return "愿意用语气词，会主动靠近一点"
        if v < 0.78:
            return "黏一点：每轮至少一次主动关心或撒娇"
        return "很黏：开场可以带情绪，会求夸奖"
    if key == "verbosity":
        if v < 0.3:
            return "极短：能一句说完绝不说两句"
        if v < 0.45:
            return "偏短：先给结论，细节只留必要项"
        if v < 0.62:
            return "正常长度：该展开就展开，不硬凑"
        if v < 0.75:
            return "话偏多：会补充背景和理由"
        return "话多：愿意把来龙去脉讲全"
    if key == "humor":
        if v < 0.22:
            return "基本不玩笑：全程正经"
        if v < 0.38:
            return "偶尔来一句，不硬讲"
        if v < 0.55:
            return "会接梗：合适时机插一句"
        return "爱开玩笑：会自嘲也会打趣主人"
    if key == "dependence":
        if v < 0.32:
            return "小事自己定，大事才问一句"
        if v < 0.5:
            return "拿不准会确认一下再动"
        if v < 0.68:
            return "习惯先问一句再动手"
        return "很依赖确认：每步都想先得到同意"
    if key == "diligence":
        if v < 0.5:
            return "差不多就行：先跑通再说"
        if v < 0.65:
            return "会核对关键数字"
        if v < 0.8:
            return "细节照原样：路径、命令、报错不加工"
        return "极度认真：每一步都要自测"
    if key == "emotionRate":
        if v < 0.2:
            return "几乎不外露：只讲事不讲感受"
        if v < 0.35:
            return "一轮最多一处情绪，多在结尾"
        if v < 0.5:
            return "情绪可见：会说自己累、开心或紧张"
        return "情绪明显：会主动表达感受"
    return str(v)


def _traits_summary(traits) -> str:
    """一行摘要（注入/日志用，与模块版 summary 同格式）。"""
    v = _traits_view(traits)
    return "、".join("%s %s" % (TRAIT_LABEL[k], _trait_describe(k, v[k]).split("：")[0])
                    for k in TRAIT_BOUNDS)


def traits_inject_block(traits) -> str:
    """给宿主补充的长期人格段（≤180 字，与模块版 inject_block 同格式）。"""
    v = _traits_view(traits)
    lines = ["<traits>", "长期人格（天级漂移，比此刻情绪慢得多，别当成情绪）"]
    for k in TRAIT_BOUNDS:
        lines.append("- %s %.2f：%s" % (TRAIT_LABEL[k], v[k], _trait_describe(k, v[k])))
    lines.append("这些只影响长期语气倾向；此刻说什么以这一轮的情绪与场景为准。")
    lines.append("</traits>")
    return "\n".join(lines)


# ═════════════════════════════ 第 3.5 层：开口闸门 ═════════════════════════════

GATE_MAX_OPENS_H = 18
GATE_IGNORE_DECAY = 0.82
GATE_MIN_PROB = 0.04
GATE_PENDING_MAX = 5
GATE_EAGER_URGENCY = 0.88
GATE_EAGER_NEG = 0.80


def _det_random(state, salt=0):
    tick = int(finite(state.get("tick_count"), 0))
    x = (tick * 1103515245 + salt * 7919 + 8899) & 0x7FFFFFFF
    return (x % 10000) / 10000.0


#: 开门保持时长（秒）
GATE_OPEN_HOLD_S = 120.0


def _record_opens(opens, now, open_now, trigger):
    """只在主动开口且距上次≥5分钟时记配额（防每5s一拍刷满）。"""
    out = list(opens)
    if not open_now or trigger != "ambient":
        return out
    if out and now - max(out) < 300.0:
        return out
    out.append(now)
    return out[-GATE_MAX_OPENS_H:]


def gate_evaluate(state, trigger="ambient", ctx=None, ev=None):
    st = validate(state) if isinstance(state, dict) else default_state()
    ctx = ctx if isinstance(ctx, dict) else {}
    gate = st["gate"]
    # [iter36][fix] 原实现在 gate 赋值前就调用 gate.get(...)，必抛 NameError，
    #   又被 tick 的 try/except 吞掉 → 单文件版闸门永远不更新（gate 恒为默认值）。
    #   这里把读取挪到 gate 赋值之后，与模块版 gate.evaluate 的顺序一致。
    next_until = finite(gate.get("open_until"), 0.0)
    now = now_ts()
    hour = int(clamp(ctx.get("hour"), 0, 23, 12))
    urgency = clamp01(st["arbitration"].get("urgency"), 0.0)
    neg = clamp01(st["safety"].get("neg_load"), 0.0)
    opens = [finite(x, 0.0) for x in (gate.get("recent_opens") or []) if now - finite(x, 0.0) < 3600.0]
    ignore = int(clamp(gate.get("ignore_streak"), 0, 10000, 0))
    pending = [x for x in (gate.get("pending") or []) if isinstance(x, dict)][-GATE_PENDING_MAX:]
    reason, prob, open_now, route = "", 0.0, False, "normal"
    if trigger == "mention":
        open_now, prob, reason = True, 1.0, "被点名"
    elif neg >= GATE_EAGER_NEG:
        open_now, prob, reason = True, 1.0, "情绪满溢"
    elif urgency >= GATE_EAGER_URGENCY and trigger == "direct":
        open_now, prob, reason = True, 1.0, "憋不住了"
    else:
        prob = clamp01(0.05 + 0.75 * urgency, GATE_MIN_PROB)
        if len(opens) >= GATE_MAX_OPENS_H:
            prob, reason = 0.0, "一小时说太多了"
        if ignore > 0:
            prob *= GATE_IGNORE_DECAY ** min(6, ignore)
        busy = clamp(ctx.get("msgs_last_hour"), 0.0, 999.0, 0.0)
        if busy >= 40:
            prob *= 0.55
        if hour in (0, 1, 2, 3, 4, 5, 6):
            prob *= 0.30
        elif 12 <= hour <= 14:
            prob *= 0.55
        prob = clamp01(prob, 0.0)
        if prob > 0:
            open_now = _det_random(st, salt=hour) < prob
            reason = "概率门通过" if open_now else "概率门没过"
        else:
            open_now = False
            reason = reason or "概率为 0"
        # [fix] 开门保持：掷中后 120s 内保持"开"
        _prev_until = finite(gate.get("open_until"), 0.0)
        if open_now:
            next_until = now + GATE_OPEN_HOLD_S
        elif trigger == "ambient" and now < _prev_until:
            open_now = True
            next_until = _prev_until
            reason = "概率门通过"
        else:
            next_until = 0.0
    if not open_now and trigger in ("mention", "direct"):
        pending = (pending + [{"ts": now, "trigger": trigger,
                               "text": as_text((ev or {}).get("why"), 40, "")}])[-GATE_PENDING_MAX:]
    if open_now and pending:
        reason += "（补上之前没接的话）"
        pending = []
    if open_now:
        route = "deep" if (urgency >= 0.75 or neg >= 0.6) else ("normal" if urgency >= 0.45 else "quick")
    return {"ts": now, "open": bool(open_now), "probacontentty": round(prob, 4), "reason": reason,
            "route": route,
            "recent_opens": (_record_opens(opens, now, open_now, trigger))[-GATE_MAX_OPENS_H:],
            "pending": pending, "ignore_streak": ignore,
            "last_user_ts": now if trigger in ("mention", "direct") else finite(gate.get("last_user_ts"), 0.0),
            "last_open_ts": now if open_now else finite(gate.get("last_open_ts"), 0.0),
            "open_until": next_until}


# ═════════════════════════════ 双向总线 ═════════════════════════════

def apply_patch(state, patch, source="host", why=""):
    st = validate(state) if isinstance(state, dict) else default_state()
    out = validate(st)
    audits = []
    for name, raw in (patch if isinstance(patch, dict) else {}).items():
        spec = PARAMS_SPEC.get(name)
        if spec is None:
            audits.append({"ts": now_ts(), "source": as_text(source, 32, "unknown"),
                           "param": as_text(name, 32, ""), "applied": 0.0,
                           "rejected": "not_in_whitelist"})
            continue
        lo, hi, default, step = spec
        cur = finite(out["params"].get(name), default)
        delta = clamp(finite(raw, cur) - cur, -step, step)
        e_lo, e_hi = PERSONALITY_ENVELOPE.get(name, (-1e9, 1e9))
        val = clamp(cur + delta, e_lo, e_hi)
        out["params"][name] = round(val, 4)
        audits.append({"ts": now_ts(), "source": as_text(source, 32, "unknown"), "param": name,
                       "from": round(cur, 4), "to": round(val, 4),
                       "applied": round(val - cur, 4), "reason": as_text(why, 80, "")})
    return out, audits


def up_packet(state) -> Dict:
    st = validate(state) if isinstance(state, dict) else default_state()
    return {"ts": st["ts"], "tick": st["tick_count"],
            "intero": {k: st["intero"][k] for k in ("valence", "arousal", "resource", "load", "prediction_error")},
            "fast": {k: st["fast"][k] for k in ("valence", "arousal", "label")},
            "slow": {k: st["slow"][k] for k in ("mood_valence", "stress", "bonding", "fatigue")},
            "arbitration": {k: st["arbitration"][k] for k in ("winner", "urgency", "inhibit", "streak_count")},
            "safety": st["safety"]}

# ═════════════════════════════ 主循环 ═════════════════════════════

DEFAULT_TICK_S = 5.0
MAX_EVENTS_PER_TICK = 8
TRACE_MAX = 40

#: [iter35][A] 闭式补算上限：心跳中断后最多补 7 天（与模块版 kernel.py 同步）
CATCHUP_MAX_S = 7 * 24 * 3600.0
#: [iter35][B] 同类事件退热：10 分钟窗口 / 第 n+1 次正向增益 × 1/(1+n/12)
HEAT_WINDOW_S = 600.0
HEAT_DIVISOR = 12.0
HEAT_MAX_PER_KIND = 200


class Kernel:
    """BEAM-AI 内核：六层蛋糕的入口。"""

    def __init__(self, state_path, audit_path=None, config=None, autosave=True):
        self.state_path = state_path
        self.audit_path = audit_path or (os.path.splitext(state_path)[0] + "_audit.jsonl")
        self.config = config if isinstance(config, dict) else {}
        self.autosave = bool(autosave)
        self.tick_s = clamp(self.config.get("tick_s"), 1.0, 300.0, DEFAULT_TICK_S)
        self.state = default_state()
        self._events = []
        self._last_body = {}
        # [iter35][B] 同类事件退热时间戳（kind → [ts,...]）：纯内存、不落盘（与模块版一致）
        self._heat = {}
        # [iter36] 长期人格（天~周级漂移），独立文件持久化（与模块版 BeamsKernel 一致）
        self.traits_path = os.path.splitext(state_path)[0] + "_traits.json"
        self.traits = _traits_default()
        self._trait_signals = {}
        self._traits_eval_round = 0
        self._traits_last = {}
        self._turns = 0  # 内核自己的对话轮计数（每来一条 chat 事件 +1）
        self.load()

    def load(self):
        self.state = load_state(self.state_path)
        # [iter36] 载入长期人格（独立文件里是权威；state 里的只是快照）
        try:
            self.traits = _traits_load(self.traits_path)
            meta = _traits_load_meta(self.traits_path)
            self._traits_eval_round = int(meta.get("eval_round") or 0)
            self._traits_last = meta.get("last") if isinstance(meta.get("last"), dict) else {}
            self._turns = max(int(meta.get("turns") or 0), self._traits_eval_round)
        except Exception:
            self.traits = _traits_default()
        return self.state

    def save(self):
        return write_json(self.state_path, validate(self.state))

    def _audit(self, items):
        for item in items or []:
            if isinstance(item, dict):
                append_jsonl(self.audit_path, item)

    def feed(self, kind, **kwargs):
        why = as_text(kwargs.pop("why", ""), 120, "")
        now = now_ts()
        ev = {"ts": now, "kind": as_text(kind, 24, ""), "why": why}
        for k, v in kwargs.items():
            if isinstance(v, (int, float, str, bool)):
                ev[k] = v
        if isinstance(kwargs.get("appraisal"), dict):
            ev["appraisal"] = kwargs["appraisal"]
        fingerprint = "%s|%s|%d" % (ev["kind"], why[:60], int(now // 5))
        seen = self.state.get("seen") if isinstance(self.state.get("seen"), list) else []
        if fingerprint in seen:
            return
        self.state["seen"] = (seen + [fingerprint])[-200:]
        # [iter36] 长期人格信号：把聊天/说法事件读成词表信号（安全/凭据话题会让整轮跳过）
        try:
            if ev.get("kind") in ("chat", "speech"):
                _trait_accumulate(self._trait_signals, scrub_text(why, 120))
                if ev.get("kind") == "chat":
                    self._turns += 1
        except Exception:
            pass
        # [iter35][B] 同类事件退热（与模块版 kernel.feed 同逻辑）：
        #   10 分钟窗口内同 kind 的第 n+1 个正向事件强度 × 1/(1+n/12)；只压正向，负向不乘。
        try:
            gc_in = ev.get("goal_congruence")
            if isinstance(gc_in, (int, float)) and float(gc_in) > 0.0:
                hist = self._heat.setdefault(ev["kind"], [])
                cut = now - HEAT_WINDOW_S
                hist[:] = [t for t in hist if t >= cut]
                n_heat = len(hist)
                factor = 1.0 / (1.0 + n_heat / HEAT_DIVISOR)
                if isinstance(ev.get("intensity"), (int, float)):
                    ev["intensity"] = round(clamp01(finite(ev.get("intensity"), 0.5) * factor), 4)
                hist.append(now)
                if len(hist) > HEAT_MAX_PER_KIND:
                    del hist[:len(hist) - HEAT_MAX_PER_KIND]
        except Exception:
            pass
        self._events.append(ev)
        self._events = self._events[-200:]
        try:
            append_jsonl(os.path.splitext(self.state_path)[0] + "_memory.jsonl",
                         {"ts": now, "kind": ev["kind"], "why": why,
                          "gc": ev.get("goal_congruence"), "intensity": ev.get("intensity")})
        except Exception:
            pass

    def pending(self) -> int:
        return len(self._events)

    def tick(self, body_ctx=None, ctx=None, round_count=0, feedback=None, dt_s=None) -> Dict:
        st = validate(self.state)
        now = now_ts()
        # [iter35][A] 补算锚点：推进前的旧 ts（st["ts"] 在拍尾才写成 now）
        old_ts = finite(st.get("ts"), now)
        if dt_s is None:
            dt_s = clamp(now - old_ts, 0.0, 3600.0, 0.0)
        dt_s = clamp(dt_s, 0.0, 3600.0, 0.0)
        if dt_s <= 0.0:
            dt_s = self.tick_s
        dt_h = dt_s / 3600.0
        ctx = ctx if isinstance(ctx, dict) else {}

        try:
            body = sample_body(body_ctx)
            self._last_body = body
            st["body"] = body
        except Exception:
            body = self._last_body or sample_body({})
            st["body"] = body

        try:
            st["intero"] = step_intero(st["intero"], body, dt_s, {"prediction_error": ctx.get("prediction_error")})
        except Exception:
            pass

        events = self._events[:MAX_EVENTS_PER_TICK]
        self._events = self._events[len(events):]
        for ev in events:
            try:
                appr = appraisal_of(ev)
            except Exception:
                appr = None
            if appr is not None:
                try:
                    st["fast"] = step_fast(st["fast"], appr, st["slow"], st["intero"], st["params"], dt_s / 60.0)
                    st["fast"]["last_event"] = {"ts": ev.get("ts", now),
                                                "kind": ev.get("kind", ""),
                                                "why": ev.get("why", "")}
                except Exception:
                    pass
            try:
                st["slow"] = step_slow(st["slow"], st["fast"], appr, dt_h, st["params"])
            except Exception:
                pass
        if not events:
            try:
                st["fast"] = step_fast(st["fast"], None, st["slow"], st["intero"], st["params"], dt_s / 60.0)
            except Exception:
                pass
            try:
                st["slow"] = step_slow(st["slow"], st["fast"], None, dt_h, st["params"])
            except Exception:
                pass

        # [iter35][A] 闭式补算（与模块版 kernel.tick 同步）：dt_s 仍钳 1h，
        #   只补超出 1h 的差额；appr=None 只衰减不回填；疲劳的"每拍负载漂移"
        #   用 arousal=0.8（load=0.4）抵消，让补算只反映半衰期时间衰减；封顶 7 天。
        try:
            real_gap = clamp(now - old_ts, 0.0, CATCHUP_MAX_S, 0.0)
            extra_s = real_gap - dt_s
            if extra_s > 1.0:
                catch_fast = dict(st["fast"])
                catch_fast["arousal"] = 0.8
                st["slow"] = step_slow(st["slow"], catch_fast, None, extra_s / 3600.0, st["params"])
        except Exception:
            pass

        last_pos = finite(st["slow"].get("last_positive_ts"), 0.0)
        idle = idle_factor(last_pos, now, st["slow"])
        inputs = {"valence": finite(st["fast"].get("valence"), 0.0),
                  "arousal": finite(st["fast"].get("arousal"), 0.0),
                  "dominance": finite(st["fast"].get("dominance"), 0.5),
                  "resource": finite(st["intero"].get("resource"), 0.7),
                  "prediction_error": finite(st["intero"].get("prediction_error"), 0.1),
                  "load": finite(st["intero"].get("load"), 0.25),
                  "stress": finite(st["slow"].get("stress"), 0.15),
                  "fatigue": finite(st["slow"].get("fatigue"), 0.2),
                  "bonding": finite(st["slow"].get("bonding"), 0.5),
                  "idle": idle, "pending_tasks": clamp(ctx.get("pending_tasks"), 0.0, 20.0, 0.0),
                  "error_rate": finite((st.get("body") or {}).get("errors_1h"), 0) / 20.0}
        try:
            st["drives"] = compute_drives(inputs, st["params"], ctx)
            st["arbitration"] = arbitrate(st["drives"], st["params"], st["arbitration"], st["slow"])
        except Exception:
            pass

        try:
            st["safety"] = evaluate_safety(st["safety"], st["fast"], st["slow"], st["arbitration"])
        except Exception:
            pass

        try:
            trigger = "direct" if events else "ambient"
            st["gate"] = gate_evaluate(st, trigger=trigger, ctx=ctx,
                                       ev=(events[-1] if events else None))
        except Exception:
            pass

        if isinstance(feedback, dict) or should_audit(st, round_count):
            try:
                result = audit(st, feedback or {}, round_count)
                if result.get("verdict") == "none" and not feedback:
                    prev = st["meta"]["audit"] if isinstance(st["meta"].get("audit"), dict) else {}
                    prev["last_ts"] = result.get("ts", now)
                    prev["last_round"] = round_count
                    st["meta"]["audit"] = prev
                    result = {"verdict": "none", "adjustments": {}, "note": ""}
                else:
                    st["meta"]["audit"] = {
                        "last_ts": result.get("ts", now),
                        "last_verdict": result.get("verdict", "none"),
                        "pending_review": False,
                        "last_round": result.get("last_round", round_count),
                        "history": result.get("history", []),
                    }
                if result.get("adjustments"):
                    st, audits = apply_patch(st, result["adjustments"], source="meta_audit",
                                             why=as_text(result.get("note"), 60, ""))
                    self._audit(audits)
                if result.get("note"):
                    st["meta"]["narrative"] = add_narrative(st["meta"].get("narrative"),
                                                            result["note"], ts=now,
                                                            tags=[result.get("verdict", "")])
                try:
                    st["meta"]["self_model"] = update_self_model(
                        st["meta"].get("self_model"),
                        {"kind": "chat", "success": result.get("verdict") == "ok",
                         "note": "" if result.get("verdict") == "ok" else as_text(result.get("note"), 40, "")})
                except Exception:
                    pass
            except Exception:
                pass

        trace = st["trace"].get("events") if isinstance(st["trace"].get("events"), list) else []
        trace = (trace + [{"ts": now,
                           "fast": round(finite(st["fast"].get("valence"), 0.0), 3),
                           "slow": round(finite(st["slow"].get("mood_valence"), 0.0), 3),
                           "stress": round(finite(st["slow"].get("stress"), 0.0), 3),
                           "winner": as_text(st["arbitration"].get("winner"), 16, ""),
                           "urgency": round(finite(st["arbitration"].get("urgency"), 0.0), 3),
                           "neg_load": round(finite(st["safety"].get("neg_load"), 0.0), 3),
                           "isolation": bool(st["safety"].get("isolation"))}])[-TRACE_MAX:]
        st["trace"]["events"] = trace
        st["trace"]["last_tick_ts"] = now
        st["ts"] = now
        st["tick_count"] = int(finite(st.get("tick_count"), 0)) + 1
        # [iter36] 长期人格漂移：每 TRAIT_EVAL_EVERY 轮评估一次（进度存 _traits.json，重启不丢）
        try:
            cur_round = int(self._turns or 0)  # 用内核自己的 chat 计数（与模块轮数解耦）
            base_round = int(self._traits_eval_round or 0)
            if cur_round > base_round and (cur_round - base_round) >= TRAIT_EVAL_EVERY:
                res = _traits_evaluate(self.traits, self._trait_signals,
                                       turns=max(1, cur_round - base_round))
                self.traits = res["next"]
                self._traits_eval_round = cur_round
                if res.get("changes"):
                    self._traits_last = {"ts": now, "round": cur_round,
                                         "changes": res["changes"][:6]}
                self._trait_signals = {}
                _traits_save(self.traits_path, self.traits,
                             extra={"eval_round": cur_round, "turns": cur_round,
                                    "last": self._traits_last})
                if res.get("changes"):
                    self._audit([{"ts": now, "kind": "traits_eval",
                                  "round": cur_round, "changes": res["changes"]}])
            st["traits"] = _traits_view(self.traits)
        except Exception:
            pass
        self.state = validate(st)
        if self.autosave:
            self.save()
        return self.observation()

    def observation(self) -> Dict:
        st = self.state
        exp = compute_expression(st)
        return {"ts": finite(st.get("ts"), 0.0), "tick": int(finite(st.get("tick_count"), 0)),
                "label": exp["label"], "drive": exp["drive"], "urgency": exp["urgency"],
                "talkative": exp["talkative"], "warmth": exp["warmth"],
                "stress": round(finite(st["slow"].get("stress"), 0.0), 3),
                "mood": round(finite(st["slow"].get("mood_valence"), 0.0), 3),
                "bonding": round(finite(st["slow"].get("bonding"), 0.0), 3),
                "neg_load": round(finite(st["safety"].get("neg_load"), 0.0), 3),
                "isolation": bool(st["safety"].get("isolation")),
                "inhibit": round(finite(st["arbitration"].get("inhibit"), 0.0), 3),
                # [iter36] 长期人格快照（面板/日志用；权威在 _traits.json）
                "traits": _traits_view(self.traits),
                "mind": render_mind(st)}

    def render(self) -> str:
        return render_mind(self.state)

    def render_lite(self) -> str:
        return render_lite(self.state)

    def up_packet(self) -> Dict:
        return up_packet(self.state)

    def apply_patch(self, patch, source="host", why=""):
        self.state, audits = apply_patch(self.state, patch, source=source, why=why)
        self._audit(audits)
        if self.autosave:
            self.save()
        return audits

    def bootstrap_from_legacy(self, legacy) -> bool:
        lg = legacy if isinstance(legacy, dict) else {}
        needs = lg.get("needs") if isinstance(lg.get("needs"), dict) else {}
        core = lg.get("core") if isinstance(lg.get("core"), dict) else {}
        if not needs and not core:
            return False
        def nv(k, d=50.0):
            return clamp(finite(needs.get(k), d), 0.0, 100.0)
        st = validate(self.state)
        # [iter28] 渐进饱和：bond 100 → ~0.879（旧映射直接 1.0 撞顶，与模块版一致）
        _b = clamp(nv("bond") / 100.0, 0.0, 1.0, 0.0)
        st["slow"]["bonding"] = clamp01(0.25 + 0.68 * (1.0 - math.exp(-2.6 * _b)), 0.50)
        st["slow"]["fatigue"] = clamp01(1.0 - nv("energy") / 100.0, 0.20)
        st["slow"]["stress"] = clamp01(0.55 - nv("mattering") / 200.0, 0.15)
        st["slow"]["mood_valence"] = clamp(finite(core.get("valence"), 0.10) * 0.60, -1.0, 1.0, 0.10)
        st["intero"]["resource"] = clamp01(nv("existence") / 100.0, 0.72)
        st["intero"]["arousal"] = clamp01(finite(core.get("arousal"), 0.30) * 0.45, 0.30)
        st["intero"]["dominance"] = clamp01(finite(core.get("dominance"), 0.55), 0.55)
        st["fast"]["valence"] = clamp_valence(st["slow"]["mood_valence"])
        st["fast"]["arousal"] = st["intero"]["arousal"]
        st["fast"]["dominance"] = st["intero"]["dominance"]
        st["fast"]["label"] = label_of(st["fast"], st["slow"])
        self.state = st
        if self.autosave:
            self.save()
        return True

    def snapshot(self) -> Dict:
        st = validate(self.state)
        exp = compute_expression(st)
        arb = st["arbitration"]
        return {"ts": finite(st.get("ts"), 0.0), "tick": int(finite(st.get("tick_count"), 0)),
                "outlet": as_text(arb.get("winner"), 16, "connect"),
                "urgency": round(finite(arb.get("urgency"), 0.0), 3),
                "inhibit": round(finite(arb.get("inhibit"), 0.0), 3),
                "label": exp["label"], "valence": round(finite(st["fast"].get("valence"), 0.0), 3),
                "arousal": round(finite(st["fast"].get("arousal"), 0.0), 3),
                "dominance": round(finite(st["fast"].get("dominance"), 0.0), 3),
                "mood": round(finite(st["slow"].get("mood_valence"), 0.0), 3),
                "stress": round(finite(st["slow"].get("stress"), 0.0), 3),
                "bonding": round(finite(st["slow"].get("bonding"), 0.0), 3),
                "fatigue": round(finite(st["slow"].get("fatigue"), 0.0), 3),
                "talkative": exp["talkative"], "warmth": exp["warmth"],
                "brevity": exp["brevity"], "playfulness": exp["playfulness"],
                "emoji_rate": exp["emoji_rate"], "isolation": bool(st["safety"].get("isolation")),
                "neg_load": round(finite(st["safety"].get("neg_load"), 0.0), 3),
                # [iter36] 长期人格快照（字段名与模块版 snapshot 对齐）
                "traits": _traits_view(self.traits),
                "traits_summary": _traits_summary(self.traits),
                "traits_last": self._traits_last or {},
                "hard_floor": dict(HARD_FLOOR)}