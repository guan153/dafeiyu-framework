"""BEAM-AI · 第 2 层：双速情绪动力学（affect2.py）

快通道：某件事此刻是威胁还是机会（appraisal → 几分钟回落）。
慢通道：跨情境的情绪累积与衰减（mood / stress / bonding / fatigue → 小时到天）。

两速的耦合：
- 慢通道决定快通道的**基线**与**敏感度**（同样是负效价，压力大的系统反应更强）；
- 快通道的持续偏移会"渗"进慢通道（mood 是 fast 的移动平均 + 自己的回归）。

所有方程都是确定性的、有界的；没有随机数。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from . import schema

#: 快通道单次事件最大位移（惯性：不能一跳到底）
FAST_MAX_STEP = {"valence": 0.28, "arousal": 0.24, "dominance": 0.20}

#: 事件吸收率
EVENT_ABSORB = 0.55

#: fast → slow 的渗透率（快通道偏移有多少进入慢通道）
FAST_TO_SLOW = 0.045

#: 慢通道回归率（每小时朝基线回归的比例，再乘半衰期）
SLOW_STRESS_UP = 0.055       # 负性事件对压力的抬升
SLOW_BOND_UP = 0.035         # 正性互动对联结的提升
SLOW_FATIGUE_UP = 0.045      # 负载对疲劳的抬升
SLOW_FATIGUE_DOWN = 0.030    # 休息对疲劳的恢复

#: 压力对快通道敏感度的放大
STRESS_SENS = 0.60

#: 负性负荷（安全层用）：压力与连续负效价的合成
NEG_LOAD_W = {"stress": 0.55, "neg_fast": 0.30, "neg_mood": 0.15}

#: 事件 → appraisal 预设：relevance, goal_congruence, certainty, control, agency, coping, intensity
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
    "memory_change": (0.30, -0.05, 0.50, 0.40, "circumstance", 0.50, 0.35),
    "memory_recall": (0.35, 0.00, 0.50, 0.45, "circumstance", 0.55, 0.40),
    "speech": (0.55, 0.10, 0.55, 0.55, "other", 0.60, 0.55),
}

#: appraisal 强度权重
W_REL, W_GC, W_UNC, W_CTRL, W_COPE = 0.55, 1.00, 0.35, 0.30, 0.25


def appraisal_of(ev: Any) -> Optional[Dict]:
    """从事件推断 appraisal 向量；拿不到返回 None（只做衰减，不硬塞中性）。"""
    if not isinstance(ev, dict):
        return None
    given = ev.get("appraisal")
    if isinstance(given, dict):
        rel = schema.clamp01(given.get("relevance"), 0.5)
        gc = schema.clamp(given.get("goal_congruence"), -1.0, 1.0, 0.0)
        cert = schema.clamp01(given.get("certainty"), 0.55)
        ctrl = schema.clamp01(given.get("control"), 0.5)
        cope = schema.clamp01(given.get("coping"), 0.55)
        agency = schema.as_text(given.get("agency"), 16, "other")
        it = schema.clamp01(given.get("intensity", 0.6), 0.6)
        return {"relevance": rel, "goal_congruence": gc, "certainty": cert,
                "control": ctrl, "coping": cope, "agency": agency, "intensity": it}

    kind = schema.as_text(ev.get("kind"), 24, "")
    if kind not in EVENT_PRESETS:
        return None
    rel, gc, cert, ctrl, agency, cope, it = EVENT_PRESETS[kind]
    # 事件可微调方向与强度
    if isinstance(ev.get("goal_congruence"), (int, float)):
        gc = schema.clamp(ev.get("goal_congruence"), -1.0, 1.0, gc)
    if isinstance(ev.get("intensity"), (int, float)):
        it = schema.clamp01(ev.get("intensity"), it)
    return {"relevance": schema.clamp01(ev.get("relevance"), rel),
            "goal_congruence": gc,
            "certainty": schema.clamp01(ev.get("certainty"), cert),
            "control": schema.clamp01(ev.get("control"), ctrl),
            "coping": schema.clamp01(ev.get("coping"), cope),
            "agency": agency,
            "intensity": it}


def _fast_target(appr: Dict, base: Dict, params: Dict, stress: float, bonding: float) -> Dict:
    """这次事件想把快通道推到哪（相对基线 + 事件位移）。"""
    gain = schema.num(params, "sensitivity_gain", 1.0) * (1.0 + STRESS_SENS * stress)
    gain = schema.clamp(gain, 0.3, 2.2)
    i = schema.clamp01(appr.get("intensity"), 0.5)
    gc = schema.clamp(appr.get("goal_congruence"), -1.0, 1.0, 0.0)
    ctrl = schema.clamp01(appr.get("control"), 0.5)
    cope = schema.clamp01(appr.get("coping"), 0.55)
    cert = schema.clamp01(appr.get("certainty"), 0.55)
    agency = appr.get("agency", "other")

    dv = gc * i * 0.62 * gain
    da = (i * (0.12 + 0.45 * abs(gc)) * (1.0 - 0.30 * cert)
          - 0.10 * ctrl * i + 0.10 * max(0.0, -gc) * i) * gain
    agency_bonus = {"self": 1.0, "other": -0.7, "circumstance": -0.3}.get(agency, -0.3)
    dd = (i * (0.50 * (ctrl + cope - 1.0)) + 0.10 * i * agency_bonus) * gain
    # 联结高 → 情绪的安全垫（负性事件被缓冲）
    if gc < 0.0:
        dv *= (1.0 - 0.30 * schema.clamp01(bonding, 0.5))
    return {
        "valence": schema.clamp(base["valence"] + schema.clamp(dv, -1.0, 1.0), -1.0, 1.0, 0.0),
        "arousal": schema.clamp(base["arousal"] + schema.clamp(da, -1.0, 1.0), 0.0, 1.0, 0.3),
        "dominance": schema.clamp(base["dominance"] + schema.clamp(dd, -1.0, 1.0), 0.0, 1.0, 0.55),
    }


def _move(cur: float, target: float, absorb: float, max_step: float, lo: float, hi: float) -> float:
    disp = target - cur
    step = schema.clamp(disp * absorb, -max_step, max_step)
    return schema.clamp(cur + step, lo, hi, cur)


def step_fast(prev_fast: Any, appr: Optional[Dict], slow: Dict, intero: Dict,
              params: Dict, modules: Optional[Dict] = None) -> Dict:
    """推进快通道（一次事件或空闲的一拍）。

    - 基线 = 内感受效价（身体）与慢通道 mood 的混合，所以"心情"会垫高/压低这阵子的情绪；
    - 事件 → 朝目标位移；无事件 → 按半衰期衰减回基线；
    - 返回新的 fast 子对象（保留 last_event 由 kernel 写）。
    """
    f = schema.validate({"fast": prev_fast if isinstance(prev_fast, dict) else {}})["fast"]
    s = schema.validate({"slow": slow if isinstance(slow, dict) else {}})["slow"]
    i = schema.validate({"intero": intero if isinstance(intero, dict) else {}})["intero"]
    mods = modules if isinstance(modules, dict) else {}
    now = schema.now_ts()
    dt_min = 5.0 / 60.0

    mood = schema.num(s, "mood_valence", 0.10)
    stress = schema.clamp01(s.get("stress"), 0.15)
    bonding = schema.clamp01(s.get("bonding"), 0.50)
    decay_rate = schema.num(params, "decay_rate", 1.0)

    base = {
        "valence": schema.clamp(0.55 * schema.num(i, "valence", 0.12) + 0.45 * mood, -0.6, 0.6, 0.1),
        "arousal": schema.clamp(0.45 * schema.num(i, "arousal", 0.30) + 0.40 * schema.num(s, "mood_arousal", 0.32) + 0.15 * stress, 0.0, 1.0, 0.3),
        "dominance": schema.clamp(0.55 * schema.num(i, "dominance", 0.55) + 0.45 * (0.5 + 0.3 * (bonding - 0.5)), 0.0, 1.0, 0.55),
    }

    out = {"ts": now, "intensity": 0.0, "label": f.get("label", "平静")}
    if mods.get("fast") is False:
        return dict(f, ts=now)

    if appr is not None:
        tgt = _fast_target(appr, base, params, stress, bonding)
        out["valence"] = _move(schema.finite(f.get("valence"), base["valence"]),
                               tgt["valence"], EVENT_ABSORB, FAST_MAX_STEP["valence"], -schema.VALENCE_MARGIN, schema.VALENCE_MARGIN)
        out["arousal"] = _move(schema.finite(f.get("arousal"), base["arousal"]),
                               tgt["arousal"], EVENT_ABSORB, FAST_MAX_STEP["arousal"], 0.0, 1.0)
        out["dominance"] = _move(schema.finite(f.get("dominance"), base["dominance"]),
                                 tgt["dominance"], EVENT_ABSORB, FAST_MAX_STEP["dominance"], 0.0, 1.0)
        out["intensity"] = schema.clamp01(appr.get("intensity"), 0.5)
    else:
        # 空闲：按半衰期向基线回归
        for key, ch in (("valence", "valence"), ("arousal", "arousal"), ("dominance", "dominance")):
            hl_min = schema.FAST_HALF_LIFE_MIN[ch] / max(0.2, decay_rate)
            fac = schema.half_life_factor_min(dt_min, hl_min)
            cur = schema.finite(f.get(key), base[ch])
            out[key] = schema.clamp(base[ch] + (cur - base[ch]) * fac, -schema.VALENCE_MARGIN, 1.0, base[ch])
        out["valence"] = schema.clamp_valence(out["valence"])
        out["arousal"] = schema.clamp01(out["arousal"])
        out["dominance"] = schema.clamp01(out["dominance"])

    out["label"] = label_of(out, s)
    out["last_event"] = f.get("last_event") if isinstance(f.get("last_event"), dict) else {"ts": 0.0, "kind": "", "why": ""}
    return out


def step_slow(prev_slow: Any, fast_new: Dict, appr: Optional[Dict], dt_hours: float,
              params: Dict, modules: Optional[Dict] = None) -> Dict:
    """推进慢通道（情绪记忆）：mood / stress / bonding / fatigue。

    - mood 是快通道的移动平均 + 向基线回归（滞后、有惯性）；
    - stress 只升不降得快：负性事件抬升，半衰期 24h 回落；
    - bonding 是长期关系积淀（30 天半衰期），高 bonding 是情绪的安全垫；
    - fatigue 由负载抬升、由休息回落。
    """
    s = schema.validate({"slow": prev_slow if isinstance(prev_slow, dict) else {}})["slow"]
    mods = modules if isinstance(modules, dict) else {}
    dt = max(0.0, schema.finite(dt_hours, 0.0))
    now = schema.now_ts()
    if mods.get("slow") is False:
        return dict(s, ts=now)

    out = {"ts": now}
    params_mood_base = schema.num(params, "mood_baseline", 0.10)

    # 1) mood：向 fast 渗透 + 向基线回归
    mood = schema.finite(s.get("mood_valence"), 0.10)
    fast_v = schema.finite(fast_new.get("valence"), 0.10)
    mood += FAST_TO_SLOW * (fast_v - mood)
    mood = params_mood_base + (mood - params_mood_base) * schema.half_life_factor(dt, schema.SLOW_HALF_LIFE_H["mood_valence"])
    out["mood_valence"] = schema.clamp_valence(mood)

    mood_a = schema.finite(s.get("mood_arousal"), 0.32)
    fast_a = schema.finite(fast_new.get("arousal"), 0.32)
    mood_a += FAST_TO_SLOW * (fast_a - mood_a)
    mood_a = 0.32 + (mood_a - 0.32) * schema.half_life_factor(dt, schema.SLOW_HALF_LIFE_H["mood_arousal"])
    out["mood_arousal"] = schema.clamp01(mood_a)

    # 2) stress：负性事件抬升，半衰期回落
    stress = schema.clamp01(s.get("stress"), 0.15)
    stress *= schema.half_life_factor(dt, schema.SLOW_HALF_LIFE_H["stress"])
    neg = max(0.0, -fast_v)
    if appr is not None:
        gc = schema.clamp(appr.get("goal_congruence"), -1.0, 1.0, 0.0)
        if gc < 0.0:
            stress += SLOW_STRESS_UP * (-gc) * schema.clamp01(appr.get("intensity"), 0.5)
    out["stress"] = schema.clamp01(stress, 0.0)

    # 3) bonding：正性互动抬升，半衰期 30 天
    bonding = schema.clamp(s.get("bonding"), 0.50, 1.0, 0.50)
    bonding *= schema.half_life_factor(dt, schema.SLOW_HALF_LIFE_H["bonding"])
    if appr is not None:
        gc = schema.clamp(appr.get("goal_congruence"), -1.0, 1.0, 0.0)
        if gc > 0.0:
            # [iter28] 渐进饱和：越接近 1.0，同样一句夸奖带来的升温越少（永不撞顶）
            room = max(0.0, 1.0 - bonding)
            bonding += SLOW_BOND_UP * gc * schema.clamp01(appr.get("intensity"), 0.5) * (0.25 + 0.75 * room)
    out["bonding"] = schema.clamp(bonding, 0.50, 1.0, 0.50)

    # 4) fatigue：负载抬升，休息回落
    fatigue = schema.clamp01(s.get("fatigue"), 0.20)
    fatigue *= schema.half_life_factor(dt, schema.SLOW_HALF_LIFE_H["fatigue"])
    load = schema.clamp01(fast_new.get("arousal"), 0.32) * 0.5
    fatigue += SLOW_FATIGUE_UP * load
    fatigue -= SLOW_FATIGUE_DOWN * (1.0 - load)
    out["fatigue"] = schema.clamp01(fatigue, 0.0)

    # 任何非负面的互动都算"有联系"（不只是被夸），否则 idle 会一路涨到饱和
    out["last_positive_ts"] = max(schema.finite(s.get("last_positive_ts"), 0.0), now) if (appr and schema.num(appr, "goal_congruence", 0.0) > -0.10) else schema.finite(s.get("last_positive_ts"), 0.0)
    out["last_negative_ts"] = max(schema.finite(s.get("last_negative_ts"), 0.0), now) if (appr and schema.num(appr, "goal_congruence", 0.0) < -0.2) else schema.finite(s.get("last_negative_ts"), 0.0)
    return out


def label_of(fast: Dict, slow: Dict) -> str:
    """从快慢两通道合成一个中文主情绪（表达层用，不参与动力学）。"""
    v = schema.finite(fast.get("valence"), 0.0)
    a = schema.finite(fast.get("arousal"), 0.3)
    d = schema.finite(fast.get("dominance"), 0.5)
    stress = schema.clamp01(slow.get("stress"), 0.0)
    fatigue = schema.clamp01(slow.get("fatigue"), 0.0)
    bonding = schema.clamp01(slow.get("bonding"), 0.5)

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


def neg_load(fast: Dict, slow: Dict) -> float:
    """负性负荷 0~1（安全层）：压力 + 负效价被时间的加权。"""
    stress = schema.clamp01(slow.get("stress"), 0.0)
    neg_fast = max(0.0, -schema.finite(fast.get("valence"), 0.0))
    neg_mood = max(0.0, -schema.finite(slow.get("mood_valence"), 0.0))
    return schema.clamp01(
        NEG_LOAD_W["stress"] * stress
        + NEG_LOAD_W["neg_fast"] * neg_fast
        + NEG_LOAD_W["neg_mood"] * neg_mood,
        0.0,
    )