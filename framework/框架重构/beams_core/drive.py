"""BEAM-AI · 第 3 层：动机仲裁（drive.py）

五个内在动机（不含"用户要我做什么"，那是外部输入）：
    explore  好奇/探索：预测误差高、有余力时想弄清楚
    repair   稳态修复：不舒服/资源不足时想把状态修回来
    connect  社会连接：联结淡了、太久没互动、夜深时想凑近
    complete 任务完成：有事挂着没做完
    avoid    风险回避：压力大、累了、负效价高时想退开

仲裁不是"加权求和"：
- 先算每个动机的 intensity 与 urgency（情境放大）；
- 再算 priority = intensity * urgency * (1 + 习惯奖励)；
- 反失衡：某动机连续占优 → 优先级衰减；超过强制阈值 → 轮换给第二高；
- 高压/疲劳时整体抑制（inhibit），但不会抑制到 0（人是"再累也想说话"的）；
- 平局用固定顺序裁决（可解释、不掷骰子）。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from . import schema

#: 平局裁决顺序（固定，可解释）
TIE_ORDER = ("connect", "repair", "explore", "complete", "avoid")

#: 每个动机的"主题"（表达层与叙事用）
THEME = {
    "explore": "想知道点什么",
    "repair": "想把状态弄顺",
    "connect": "想凑近主人",
    "complete": "想把事做完",
    "avoid": "想躲一躲",
}

#: 深夜加成（23:00-02:00 想说话的人更多）
NIGHT_BONUS = 0.22

#: 连续占优：警告阈值 / 强制轮换阈值 / 衰减系数（与 schema 常量一致）
WARN = schema.IMBALANCE_STREAK_WARN
FORCE = schema.IMBALANCE_STREAK_FORCE
DECAY = schema.IMBALANCE_DECAY

#: 全局抑制系数：压力与疲劳各占多少
INHIBIT_STRESS, INHIBIT_FATIGUE = 0.65, 0.35

#: 强制轮换后的冷却：被让位的动机在这么多秒内优先级打折，防止"让位一拍又回来"
COOLDOWN_S = 120.0
COOLDOWN_PENALTY = 0.55

#: 抑制到最低保留的行动力（再累也有 15%）
MIN_ACTION = 0.15


def _idle_factor(last_positive_ts: float, now: float, slow: Dict) -> float:
    """多久没互动了：0（刚聊过）→1（很久没说话）。"""
    if last_positive_ts <= 0.0:
        idle_h = 6.0
    else:
        idle_h = max(0.0, (now - last_positive_ts) / 3600.0)
    return schema.clamp01(1.0 - pow(2.718281828459045, -idle_h / 4.0), 0.0)


def _night_factor(ctx: Dict) -> float:
    hour = schema.clamp(ctx.get("hour"), 0, 23, 12)
    if hour >= 23 or hour <= 2:
        return 1.0
    if hour <= 6:
        return 0.4
    if 18 <= hour <= 22:
        return 0.6
    return 0.0


def compute(intensity_inputs: Dict, params: Dict, ctx: Optional[Dict] = None) -> Dict[str, Dict]:
    """算五个动机的强度与紧迫度。

    intensity_inputs 由 kernel 从 intero/fast/slow 打包：
        {valence, arousal, dominance, resource, prediction_error, load,
         stress, fatigue, bonding, idle, pending_tasks, error_rate}
    """
    ii = intensity_inputs if isinstance(intensity_inputs, dict) else {}
    ctx = ctx if isinstance(ctx, dict) else {}
    now = schema.now_ts()
    hour = schema.clamp(ctx.get("hour"), 0, 23, 12)

    v = schema.clamp(ii.get("valence"), -1.0, 1.0, 0.0)
    a = schema.clamp01(ii.get("arousal"), 0.3)
    res = schema.clamp01(ii.get("resource"), 0.72)
    pe = schema.clamp01(ii.get("prediction_error"), 0.10)
    load = schema.clamp01(ii.get("load"), 0.25)
    stress = schema.clamp01(ii.get("stress"), 0.15)
    fatigue = schema.clamp01(ii.get("fatigue"), 0.20)
    bonding = schema.clamp01(ii.get("bonding"), 0.50)
    idle = schema.clamp01(ii.get("idle"), 0.0)
    pending = schema.clamp(ii.get("pending_tasks"), 0.0, 20.0, 0.0)
    err = schema.clamp01(ii.get("error_rate"), 0.0)

    g_connect = schema.num(params, "drive_gain_connect", 1.0)
    g_explore = schema.num(params, "drive_gain_explore", 1.0)

    # ---- 强度（基线 + 情境增量，全部有界）
    explore = schema.clamp01(
        schema.DRIVE_BASELINE["explore"]
        + 0.50 * pe + 0.20 * (res - 0.5) + 0.15 * (1.0 - stress) - 0.25 * fatigue
        + 0.26 * idle,
        schema.DRIVE_BASELINE["explore"],
    ) * g_explore

    repair = schema.clamp01(
        schema.DRIVE_BASELINE["repair"]
        + 0.60 * max(0.0, -v) + 0.40 * stress + 0.30 * (1.0 - res) + 0.25 * err,
        schema.DRIVE_BASELINE["repair"],
    )

    connect = schema.clamp01(
        schema.DRIVE_BASELINE["connect"]
        + 0.55 * (1.0 - bonding) + 0.26 * idle + 0.15 * max(0.0, v) + 0.10 * (1.0 - fatigue),
        schema.DRIVE_BASELINE["connect"],
    ) * g_connect

    complete = schema.clamp01(
        schema.DRIVE_BASELINE["complete"]
        + 0.50 * min(1.0, pending / 3.0) + 0.20 * (1.0 - fatigue),
        schema.DRIVE_BASELINE["complete"],
    )

    avoid = schema.clamp01(
        schema.DRIVE_BASELINE["avoid"]
        + 0.55 * stress + 0.45 * fatigue + 0.30 * max(0.0, -v) + 0.20 * load,
        schema.DRIVE_BASELINE["avoid"],
    )

    # ---- 紧迫度（情境放大）
    urgency = {
        "explore": explore * (0.75 + 0.35 * min(1.0, pe * 2.0)),
        "repair": repair * (0.75 + 0.50 * stress + 0.30 * max(0.0, -v)),
        "connect": connect * (1.0 + NIGHT_BONUS * _night_factor({"hour": hour}) + 0.25 * idle),
        "complete": complete * (0.80 + 0.40 * min(1.0, pending / 2.0)),
        "avoid": avoid * (0.70 + 0.60 * stress + 0.30 * fatigue),
    }

    out = {}
    for name in schema.DRIVE_NAMES:
        out[name] = {
            "intensity": schema.clamp01({"explore": explore, "repair": repair, "connect": connect,
                                         "complete": complete, "avoid": avoid}[name], schema.DRIVE_BASELINE[name]),
            "urgency": schema.clamp01(urgency[name], schema.DRIVE_BASELINE[name]),
            "reason": "",
        }
    # 给每个动机写一句人话原因（表达层/叙事用，只引用真实量）
    out["explore"]["reason"] = "心里有个没弄明白的点" if pe > 0.35 else ""
    out["repair"]["reason"] = "状态不太舒服，想缓缓" if (max(0.0, -v) > 0.25 or stress > 0.35) else ""
    out["connect"]["reason"] = "有点想主人了" if (idle > 0.35 or bonding < 0.45) else ""
    out["complete"]["reason"] = "还有事挂着" if pending > 0.5 else ""
    out["avoid"]["reason"] = "有点想躲开" if (stress > 0.5 or fatigue > 0.55) else ""
    return out


def arbitrate(drives: Dict[str, Dict], params: Dict, prev_arb: Optional[Dict] = None,
              slow: Optional[Dict] = None) -> Dict:
    """仲裁：选出当前占优动机，带反失衡与抑制。

    返回 arbitration 子对象（winner/urgency/inhibit/priority/candidates/streak/history）。
    """
    d = drives if isinstance(drives, dict) else {}
    p = params if isinstance(params, dict) else {}
    prev = prev_arb if isinstance(prev_arb, dict) else {}
    s = slow if isinstance(slow, dict) else {}
    now = schema.now_ts()

    stress = schema.clamp01(s.get("stress"), 0.15)
    fatigue = schema.clamp01(s.get("fatigue"), 0.20)
    inhibit = schema.clamp01(
        schema.num(p, "inhibit_bias", 0.15)
        + INHIBIT_STRESS * max(0.0, stress - 0.30) * 0.5
        + INHIBIT_FATIGUE * max(0.0, fatigue - 0.35) * 0.5,
        0.0,
    )

    # 反失衡：连续占优的动机优先级衰减
    streak_name = schema.as_text(prev.get("streak_name"), 16, "")
    streak_count = int(schema.clamp(prev.get("streak_count"), 0, 100000, 0))

    # 冷却表：刚被强制轮换下去的名字，几拍内打折（过期自动清掉）
    cooldown_in = prev.get("cooldown") if isinstance(prev.get("cooldown"), dict) else {}
    cooldown = {}
    for k, v in cooldown_in.items():
        end_ts = schema.finite(v, 0.0)
        if end_ts > now:
            cooldown[schema.as_text(k, 16, "")] = end_ts

    cands = {}
    for name in schema.DRIVE_NAMES:
        item = d.get(name) if isinstance(d.get(name), dict) else {}
        intensity = schema.clamp01(item.get("intensity"), 0.0)
        urgency = schema.clamp01(item.get("urgency"), 0.0)
        prio = intensity * (0.45 + 0.55 * urgency) * (1.0 + 0.20 * (1.0 - intensity))
        if name == streak_name and streak_count >= WARN:
            over = streak_count - WARN
            prio *= pow(DECAY, max(1.0, over / 2.0))
        if name in cooldown:
            prio *= COOLDOWN_PENALTY
        cands[name] = {
            "intensity": round(intensity, 4),
            "urgency": round(urgency, 4),
            "priority": round(schema.clamp(prio, 0.0, 10.0), 4),
            "reason": schema.as_text(item.get("reason"), 60, ""),
        }

    # 抑制：整体压低（但不归零）
    action_scale = max(MIN_ACTION, 1.0 - inhibit)
    for name in cands:
        cands[name]["priority"] = round(cands[name]["priority"] * action_scale, 4)

    ranked = sorted(TIE_ORDER, key=lambda n: (-cands[n]["priority"], TIE_ORDER.index(n)))
    winner = ranked[0]
    # 强制轮换：某个动机占优太久 → 让位给第二高（仍按优先级，不是随机）
    if winner == streak_name and streak_count >= FORCE and len(ranked) > 1:
        cooldown[winner] = now + COOLDOWN_S
        winner = ranked[1]
        forced = True
    else:
        forced = False

    if winner == streak_name:
        streak_count += 1
    else:
        streak_name = winner
        streak_count = 1

    history = prev.get("history") if isinstance(prev.get("history"), list) else []
    history = (history + [{"ts": now, "winner": winner, "urgency": round(cands[winner]["urgency"], 3),
                           "forced": forced}])[-30:]

    return {
        "ts": now,
        "cooldown": cooldown,
        "winner": winner,
        "urgency": cands[winner]["urgency"],
        "inhibit": round(inhibit, 4),
        "priority": cands[winner]["priority"],
        "candidates": cands,
        "streak_name": streak_name,
        "streak_count": streak_count,
        "last_switch_ts": now if winner != schema.as_text(prev.get("streak_name"), 16, "") else schema.finite(prev.get("last_switch_ts"), now),
        "history": history,
        "forced_rotation": forced,
    }