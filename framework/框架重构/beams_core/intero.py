"""BEAM-AI · 第 1 层：内感受-稳态-效价（intero.py）

把身体状态压成持续更新的低维向量：
    [valence, arousal, dominance, resource, prediction_error, load, action_cost]

关键（BEAM 原则）：
- **不随任务切换归零**：状态持久化在 beams_state 里，只有 tick 会推进；没有"对话开始=重置"。
- 内感受的效价是"身体舒不舒服"，不是"情不情绪"——情绪快通道在第 2 层。
- 所有输出有界，时间步长非法时原样返回。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from . import body as body_mod, schema

#: 向目标靠拢的时间常数（秒）：资源/负载慢，效价/唤醒略快
TAU_S = {
    "resource": 180.0,
    "load": 90.0,
    "valence": 120.0,
    "arousal": 60.0,
    "dominance": 150.0,
    "prediction_error": 45.0,
    "action_cost": 90.0,
}

#: 预测误差平滑系数（新观测占比）
PE_ALPHA = 0.35


def _targets(body: Dict, ctx: Optional[Dict] = None) -> Dict[str, float]:
    """由身体向量 + 外部信号算出内感受"目标值"。"""
    bv = body_mod.body_vector(body)
    ctx = ctx if isinstance(ctx, dict) else {}

    resource = bv["resource"]
    load = bv["load"]
    tool_score = bv["tool_score"]
    err = bv["error_rate"]

    # 外部可注入更精确的预测误差（例如"用户反应与预期不符"），缺省用错误率
    pe_in = schema.clamp01(ctx.get("prediction_error"), err)
    action_cost = schema.clamp01(
        0.25 + 0.45 * load + 0.35 * (1.0 - resource) + 0.25 * err,
        0.25,
    )

    # 内感受效价：资源足→舒服；负载高/误差大→难受。系数刻意温和（身体不是情绪放大器）
    valence = schema.clamp(
        0.12
        + 0.55 * (resource - 0.72)
        - 0.40 * (load - 0.25)
        - 0.30 * (pe_in - 0.10)
        - 0.15 * (1.0 - tool_score),
        -1.0, 1.0, 0.12,
    )
    arousal = schema.clamp(
        0.30 + 0.55 * load + 0.35 * err + 0.15 * (pe_in - 0.10) - 0.10 * (resource - 0.72),
        0.0, 1.0, 0.30,
    )
    dominance = schema.clamp(
        0.55 + 0.30 * (resource - 0.72) - 0.35 * (action_cost - 0.25) + 0.10 * (tool_score - 0.8),
        0.0, 1.0, 0.55,
    )
    return {
        "valence": valence,
        "arousal": arousal,
        "dominance": dominance,
        "resource": schema.clamp01(resource, 0.72),
        "prediction_error": schema.clamp01(pe_in, 0.10),
        "load": schema.clamp01(load, 0.25),
        "action_cost": action_cost,
    }


def _alpha(dt_s: float, tau: float) -> float:
    dt = max(0.0, schema.finite(dt_s, 0.0))
    t = max(1e-6, schema.finite(tau, 60.0))
    return schema.clamp01(1.0 - pow(2.718281828459045, -dt / t), 1.0)


def update(prev_intero: Any, body: Dict, dt_s: float = 5.0,
           ctx: Optional[Dict] = None) -> Dict:
    """推进一拍内感受。返回新 intero 子对象（含 ts）。

    用指数滑动（一阶滞后）而不是瞬跳：身体状态是"持续的"，不是"这一拍的快照"。
    """
    prev = schema.validate({"intero": prev_intero if isinstance(prev_intero, dict) else {}})["intero"]
    tgt = _targets(body, ctx)
    now = schema.now_ts()
    out = {"ts": now}
    for key in ("resource", "load", "valence", "arousal", "dominance",
                "prediction_error", "action_cost"):
        alpha = _alpha(dt_s, TAU_S.get(key, 90.0))
        # 预测误差是"观测"，跟得更快一点
        if key == "prediction_error":
            alpha = max(alpha, PE_ALPHA)
        cur = schema.finite(prev.get(key), tgt[key])
        val = cur + (tgt[key] - cur) * alpha
        if key in ("valence",):
            out[key] = schema.clamp(val, -1.0, 1.0, tgt[key])
        elif key == "arousal" or key == "dominance":
            out[key] = schema.clamp(val, 0.0, 1.0, tgt[key])
        else:
            out[key] = schema.clamp01(val, tgt[key])
    return out


def up_vector(intero: Dict) -> Dict[str, float]:
    """上行信号：内感受向量 + 不确定度（保留信息，不压成单个标签）。"""
    i = intero if isinstance(intero, dict) else {}
    resource = schema.clamp01(i.get("resource"), 0.72)
    pe = schema.clamp01(i.get("prediction_error"), 0.10)
    load = schema.clamp01(i.get("load"), 0.25)
    return {
        "valence": schema.clamp(i.get("valence"), -1.0, 1.0, 0.12),
        "arousal": schema.clamp(i.get("arousal"), 0.0, 1.0, 0.30),
        "dominance": schema.clamp(i.get("dominance"), 0.0, 1.0, 0.55),
        "resource": resource,
        "prediction_error": pe,
        "load": load,
        "action_cost": schema.clamp01(i.get("action_cost"), 0.25),
        # 不确定度：资源/预测误差越极端，越不确定（上行保留不确定性）
        "uncertainty": {
            "resource": schema.clamp01(abs(resource - 0.5) * 1.2, 0.2),
            "prediction_error": schema.clamp01(pe, 0.1),
        },
    }