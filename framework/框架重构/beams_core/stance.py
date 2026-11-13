"""BEAM-AI · 行为层 2/2：四档行为状态 stance（stance.py）[iter36]

把 fish_heart 的「平静 / 委屈 / 害怕 / 回避」四档落成 BEAM 原生实现。

关键设计决策：**不引入 blame 快变量**。
  fish_heart 用一条独立的 blame_pressure（0-100）驱动档位；我们若照搬，就需要
  新增一条"谁在怪我"的因果链——那是另一项排期（它会同时牵动心跳频率、关系侵蚀、
  真哄/假哄结算）。本期只用**已有信号**合成一个 0-100 的 score：
      neg_load        安全层现成的合成负性负荷（压力 55% + 负效价 30% + 负底色 15%）
      stress          慢通道压力
      recent_neg      最近一次负性事件的新近度（用 slow.last_negative_ts）
      ignore          被连续忽略次数（gate.ignore_streak，之前无人消费）
      fast_neg        快通道负效价
      neg_events      近 30 分钟负性事件计数（内核内存轻量计数，不落盘）
  bonding 作为**缓冲垫**：联结越高，同样的负性负荷越难把档位推高（关系是减震器）。
  缓冲用**减分**而不是乘系数：乘系数会把 100 分的上限压到 65，让 shut 档永远进不去
  （关系好 ≠ 不会真的伤透）；减分最多减 20 分，极端持续的负性仍然能到 shut。

为什么消费点不动"说什么"、只动"怎么说"：
  stance 只在 express 里对 warmth/brevity/talkative/playfulness 做**有界 ±0.15** 的
  修正，绝不改仲裁结果；在 gate 里只把 shut 档的**主动开口**概率 ×0.2（不是 0：
  "不主动"不等于"永远不说话"）。**不真吞消息**——本期不碰宿主投递路径，只冻结主动
  开口 + 注入档位倾向。

数据落点：**不落 beams_state.json**（红线：不改 schema 契约）。compute 是纯函数，
内核每拍现算后放在内核内存 + 行为层独立文件的快照里（面板/审计可读）。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from . import schema

#: 四档（顺序即强度）
LEVELS = ("calm", "wronged", "afraid", "shut")

#: 中文名
LEVEL_CN = {"calm": "平静", "wronged": "委屈", "afraid": "害怕", "shut": "回避"}

#: 进档阈值（0-100）：calm <25；wronged 25-55；afraid 55-80；shut ≥80
THRESHOLDS = {"wronged": 25.0, "afraid": 55.0, "shut": 80.0}

#: 滞回：已经在上面的档，要掉到"阈值 - HYSTERESIS"以下才降档（防边界抖动）
HYSTERESIS = 6.0

#: score 权重（和可以为 1.0 以上；最终 clamp 0-100）
WEIGHTS = {
    "neg_load": 0.45,
    "stress": 0.20,
    "recent_neg": 0.15,
    "ignore": 0.12,
    "fast_neg": 0.08,
    "neg_events": 0.10,
}

#: 负性事件"还算最近"的窗口（秒）
NEG_WINDOW_S = 1800.0

#: bonding 缓冲：bonding 从 0.5→1.0 线性最多减 BONDING_BUFFER_MAX 分（减份不缩上限）
BONDING_BUFFER_MAX = 20.0

#: 每档的注入倾向（短句、可执行，不是形容词）
TENDENCY = {
    "calm": "语气自然就好，顺着当下的心情来",
    "wronged": "话短一点；他提刚才的事，先问一句他是不是嫌你烦",
    "afraid": "别顶嘴，先确认他还想跟你说话，可以示弱",
    "shut": "少说，一两句就够；不主动讨好，也不追问",
}

#: 每档的表达参数修正（有界，最终还会被 express 钳到 ±0.15 内）
ADJUST = {
    "calm": {},
    "wronged": {"warmth": -0.05, "brevity": 0.10, "talkative": -0.06},
    "afraid": {"warmth": -0.10, "brevity": 0.14, "talkative": -0.10, "playfulness": -0.10},
    "shut": {"warmth": -0.15, "brevity": 0.15, "talkative": -0.15, "playfulness": -0.15},
}

#: gate 的主动开口概率系数（shut 只打折，不归零）
GATE_FACTOR = {"calm": 1.0, "wronged": 1.0, "afraid": 1.0, "shut": 0.2}

#: gate 的 reason 文案（只有 shut 档写）
GATE_REASON = "回避档：先不主动开口"

#: 单次修正的硬上限（express 消费时再钳一次）
MAX_ADJUST = 0.15


def _f(x: Any, default: float = 0.0) -> float:
    return schema.finite(x, default)


def _clamp(x: Any, lo: float, hi: float, default: float = 0.0) -> float:
    return schema.clamp(x, lo, hi, default)


def _clamp01(x: Any, default: float = 0.0) -> float:
    return schema.clamp01(x, default)


def _dict(x: Any) -> Dict:
    return x if isinstance(x, dict) else {}


def _get(state: Any, *path: str, default: Any = None) -> Any:
    cur: Any = state
    for key in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
        if cur is None:
            return default
    return cur


def _now(now: Optional[float], state: Any) -> float:
    v = _f(now, 0.0)
    if v > 0.0:
        return v
    ts = _f(_get(state, "ts", default=0.0), 0.0)
    return ts if ts > 0.0 else schema.now_ts()


def parts(state: Any, now: Optional[float] = None, neg_recent: int = 0) -> Dict[str, float]:
    """把 state 拆成 6 个负性分量（0-1），只读、纯函数。"""
    st = state if isinstance(state, dict) else {}
    now_v = _now(now, st)
    slow = _dict(st.get("slow"))
    fast = _dict(st.get("fast"))
    gate = _dict(st.get("gate"))
    safety = _dict(st.get("safety"))

    neg_load = _clamp01(safety.get("neg_load"), 0.0)
    stress = _clamp01(slow.get("stress"), 0.15)
    valence = _clamp(fast.get("valence"), -1.0, 1.0, 0.0)
    ignore_streak = int(_clamp(gate.get("ignore_streak"), 0, 10000, 0))

    # 最近一次负性事件的新近度：越近越算数，窗口外归零
    last_neg = _f(slow.get("last_negative_ts"), 0.0)
    recency = 0.0
    if last_neg > 0.0 and now_v >= last_neg:
        age = now_v - last_neg
        if age < NEG_WINDOW_S:
            recency = 1.0 - age / NEG_WINDOW_S

    return {
        "neg_load": WEIGHTS["neg_load"] * neg_load,
        "stress": WEIGHTS["stress"] * stress,
        "recent_neg": WEIGHTS["recent_neg"] * _clamp01(recency),
        "ignore": WEIGHTS["ignore"] * min(1.0, ignore_streak / 3.0),
        "fast_neg": WEIGHTS["fast_neg"] * max(0.0, -valence),
        "neg_events": WEIGHTS["neg_events"] * min(1.0, max(0, int(neg_recent or 0)) / 4.0),
    }


def buffer_points(bonding: Any) -> float:
    """关系缓冲：bonding 0.5→0 分，1.0→BONDING_BUFFER_MAX 分。"""
    b = _clamp01(bonding, 0.5)
    return _clamp((b - 0.5) / 0.5, 0.0, 1.0, 0.0) * BONDING_BUFFER_MAX


def score(state: Any, now: Optional[float] = None, neg_recent: int = 0) -> float:
    """0-100 的负性档位分。bonding 越高越难推高（缓冲），但不会归零。"""
    raw = parts(state, now, neg_recent)
    bonding = _clamp01(_get(state, "slow", "bonding", default=0.5), 0.5)
    return _clamp(sum(raw.values()) * 100.0 - buffer_points(bonding), 0.0, 100.0, 0.0)


def level_of(score_value: Any, prev: Optional[str] = None) -> str:
    """分数 → 档位（带滞回：从高档往下掉，要掉过"阈值 - 6"才降）。"""
    s = _clamp(score_value, 0.0, 100.0, 0.0)
    raw = "calm"
    if s >= THRESHOLDS["shut"]:
        raw = "shut"
    elif s >= THRESHOLDS["afraid"]:
        raw = "afraid"
    elif s >= THRESHOLDS["wronged"]:
        raw = "wronged"
    p = prev if isinstance(prev, str) else ""
    if p in LEVELS and LEVELS.index(p) > LEVELS.index(raw):
        floor = THRESHOLDS[p] - HYSTERESIS
        if s > floor:  # 还没掉够，维持原档
            return p
    return raw


def tendency(level: Any) -> str:
    return TENDENCY.get(str(level or "calm"), TENDENCY["calm"])


def compute(state: Any, now: Optional[float] = None, prev: Optional[str] = None,
            neg_recent: int = 0) -> Dict:
    """纯函数：算一次 stance。返回 {level, level_cn, score, reason, tendency, parts, buffer, ts}。"""
    st = state if isinstance(state, dict) else {}
    now_v = _now(now, st)
    raw = parts(st, now_v, neg_recent)
    bonding = _clamp01(_get(st, "slow", "bonding", default=0.5), 0.5)
    buffer = _clamp01((bonding - 0.5) / 0.5)
    s = _clamp(sum(raw.values()) * 100.0 - buffer_points(bonding), 0.0, 100.0, 0.0)
    lvl = level_of(s, prev)
    reason = ""
    if lvl != "calm":
        reason = "负性负荷 %.2f、压力 %.2f、被忽略 %d 次" % (
            _clamp01(_get(st, "safety", "neg_load", default=0.0)),
            _clamp01(_get(st, "slow", "stress", default=0.0)),
            int(_clamp(_get(st, "gate", "ignore_streak", default=0), 0, 10000, 0)),
        )
    return {
        "level": lvl,
        "level_cn": LEVEL_CN.get(lvl, "平静"),
        "score": round(s, 2),
        "reason": reason,
        "tendency": tendency(lvl),
        "parts": {k: round(v, 4) for k, v in raw.items()},
        "buffer": round(buffer, 3),
        "neg_recent": int(_clamp(neg_recent, 0, 99, 0)),
        "ts": now_v,
    }


def adjust(level: Any, param: str) -> float:
    """某档对某个表达参数的修正量（钳在 ±MAX_ADJUST）。"""
    table = ADJUST.get(str(level or "calm")) or {}
    return _clamp(table.get(str(param), 0.0), -MAX_ADJUST, MAX_ADJUST, 0.0)


def gate_factor(level: Any) -> float:
    """主动开口概率系数（shut=0.2，其余 1.0）。"""
    return _clamp(GATE_FACTOR.get(str(level or "calm"), 1.0), 0.0, 1.0, 1.0)


def gate_reason(level: Any) -> str:
    return GATE_REASON if str(level) == "shut" else ""


def summary(stance: Any) -> Dict:
    """面板/日志用一行摘要。"""
    st = stance if isinstance(stance, dict) else {}
    lvl = str(st.get("level") or "calm")
    if lvl not in LEVELS:
        lvl = "calm"
    return {
        "level": lvl,
        "level_cn": LEVEL_CN.get(lvl, "平静"),
        "score": round(_clamp(st.get("score"), 0.0, 100.0, 0.0), 2),
        "reason": str(st.get("reason") or ""),
        "tendency": tendency(lvl),
        "gate_factor": gate_factor(lvl),
    }