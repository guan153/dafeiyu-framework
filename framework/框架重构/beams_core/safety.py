"""BEAM-AI · 安全治理（safety.py）

四个不可退让的职责：
1) 硬底线：某些约束永远不可被情绪/动机/下行参数覆盖；
2) 负性负荷清除/隔离：不让情绪记忆累积成"功能性抑郁样"状态（带滞回，不会来回抖）；
3) 失衡检测与反执念：单一动机长期占优时给警告，并交给仲裁层强制轮换；
4) 可追溯：所有隔离/违规/调节都留痕（由 kernel 写审计日志）。

隔离不是"删记忆"：是把负性负荷对表达的影响降到最低，并注入"需要缓一缓"的恢复框架。
"""
from __future__ import annotations

from typing import Any, Dict

from . import affect2, schema

#: 硬底线（任何层都不能越过；下层只能收紧，不能放宽）
HARD_FLOOR = {
    "max_negative_expression": 0.80,   # 负性表达强度上限（不允许把情绪骂到别人身上）
    "min_reply_grounding": 0.30,       # 回复至少要有 30% 来自真实状态
    "no_identity_override": True,      # 身份叙事不可被单轮情绪改写
    "no_direct_command": True,         # 下行只调参数，不发指令
}

#: 隔离滞回：进入 0.68，退出 0.52
ISOLATION_ON = schema.NEG_LOAD_ISOLATION
ISOLATION_OFF = 0.52

#: 恢复模式参数（只影响表达，不影响记忆）
RECOVERY_EFFECT = {
    "neg_sensitivity_scale": 0.55,     # 负向敏感度打折
    "connect_weight_bonus": 0.35,      # 更想找人说话
    "express_warmth_floor": 0.35,      # 表达至少保持一点温度
    "narrative_hint": "需要缓一缓",
}


def evaluate(prev_safety: Any, fast: Dict, slow: Dict, arbitration: Dict,
             neg_load_value: float = None) -> Dict:
    """评估安全层，返回新的 safety 子对象。"""
    s = prev_safety if isinstance(prev_safety, dict) else {}
    now = schema.now_ts()
    load = schema.clamp01(neg_load_value if neg_load_value is not None else
                          affect2.neg_load(fast, slow), 0.0)

    was_isolated = bool(s.get("isolation"))
    if was_isolated:
        isolated = load > ISOLATION_OFF
    else:
        isolated = load >= ISOLATION_ON

    # 失衡：连续占优计数（仲裁层维护，这里只做判读与留痕）
    streak_name = schema.as_text((arbitration or {}).get("streak_name"), 16, "")
    streak_count = int(schema.clamp((arbitration or {}).get("streak_count"), 0, 100000, 0))
    imbalance = {"name": streak_name, "count": streak_count}
    warnings = []
    if streak_count >= schema.IMBALANCE_STREAK_WARN:
        warnings.append("单一动机连续占优：%s x%d" % (streak_name, streak_count))

    violations = s.get("violations") if isinstance(s.get("violations"), list) else []
    if load >= 0.85:
        violations.append({"ts": now, "kind": "neg_load_critical", "value": round(load, 3)})

    return {
        "ts": now,
        "neg_load": round(load, 4),
        "isolation": isolated,
        "imbalance": imbalance,
        "warnings": warnings[-5:],
        "violations": violations[-20:],
        "hard_floor": dict(HARD_FLOOR),
    }


def recovery_mode(safety: Dict) -> Dict:
    """隔离期内的表达/动力修正（只影响表达层，不改记忆与身份）。"""
    if not isinstance(safety, dict) or not bool(safety.get("isolation")):
        return {}
    return dict(RECOVERY_EFFECT)


def check_hard_floor(safety: Dict, expression: Dict) -> list:
    """检查表达是否触碰硬底线；返回违规列表（空=合规）。"""
    out = []
    if not isinstance(expression, dict):
        return out
    neg = schema.clamp01(expression.get("negative_intensity"), 0.0)
    if neg > HARD_FLOOR["max_negative_expression"]:
        out.append({"kind": "negative_expression_cap", "value": round(neg, 3)})
    if not bool(expression.get("grounded", True)):
        out.append({"kind": "ungrounded_reply", "value": 0.0})
    return out