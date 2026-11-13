"""BEAM-AI · 双向联动总线（bus.py）

所有层共享一条总线，但只传两类东西：
    上行：状态（含不确定度）——不传"必须执行"的指令；
    下行：有界调节参数——不直接覆盖低层安全基线，不发命令。

下行参数规则：
- 只认 PARAMS_SPEC 白名单里的名字；
- 单次步长有上限（一次最多调 0.15），防止一次审核把人格掰弯；
- 夹在人格包络 PERSONALITY_ENVELOPE 内（安全层，不可越）；
- 每次调节都留审计（谁、为什么、调了什么、前后值）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import schema


def up_packet(state: Dict) -> Dict:
    """上行包：给上层（元认知 / 宿主注入 / 调试）看的状态摘要。"""
    st = schema.validate(state) if isinstance(state, dict) else schema.default_state()
    f, s, i = st["fast"], st["slow"], st["intero"]
    arb = st["arbitration"]
    return {
        "ts": schema.finite(st.get("ts"), 0.0),
        "tick": int(schema.num(st, "tick_count", 0)),
        "intero": {
            "valence": schema.finite(i.get("valence"), 0.0),
            "arousal": schema.finite(i.get("arousal"), 0.0),
            "resource": schema.finite(i.get("resource"), 0.0),
            "load": schema.finite(i.get("load"), 0.0),
            "prediction_error": schema.finite(i.get("prediction_error"), 0.0),
        },
        "fast": {"valence": schema.finite(f.get("valence"), 0.0),
                 "arousal": schema.finite(f.get("arousal"), 0.0),
                 "label": schema.as_text(f.get("label"), 16, "平静")},
        "slow": {"mood_valence": schema.finite(s.get("mood_valence"), 0.0),
                 "stress": schema.finite(s.get("stress"), 0.0),
                 "bonding": schema.finite(s.get("bonding"), 0.0),
                 "fatigue": schema.finite(s.get("fatigue"), 0.0)},
        "arbitration": {"winner": schema.as_text(arb.get("winner"), 16, "connect"),
                        "urgency": schema.finite(arb.get("urgency"), 0.0),
                        "inhibit": schema.finite(arb.get("inhibit"), 0.0),
                        "streak": int(schema.num(arb, "streak_count", 0))},
        "safety": {"neg_load": schema.finite(st["safety"].get("neg_load"), 0.0),
                   "isolation": bool(st["safety"].get("isolation"))},
    }


def _envelope(name: str, value: float) -> float:
    lo, hi = schema.PERSONALITY_ENVELOPE.get(name, (-1e9, 1e9))
    return schema.clamp(value, lo, hi, value)


def apply_patch(state: Dict, patch: Dict, source: str = "", why: str = "") -> tuple:
    """应用下行调节参数。返回 (新状态, 审计条目列表)。

    patch 例：{"warmth_bias": +0.06, "decay_rate": -0.05}
    """
    st = schema.validate(state) if isinstance(state, dict) else schema.default_state()
    patch = patch if isinstance(patch, dict) else {}
    out = schema.copy_state(st)
    audits: List[Dict] = []
    src = schema.as_text(source, 32, "unknown")
    reason = schema.as_text(why, 80, "")

    for name, raw in patch.items():
        spec = schema.PARAMS_SPEC.get(name)
        if spec is None:
            audits.append({"ts": schema.now_ts(), "source": src, "param": schema.as_text(name, 32, ""),
                           "applied": 0.0, "rejected": "not_in_whitelist"})
            continue
        lo, hi, default, step = spec
        cur = schema.num(out["params"], name, default)
        want = schema.num({"v": raw}, "v", cur)
        delta = schema.clamp(want - cur, -step, step)
        val = _envelope(name, cur + delta)
        out["params"][name] = round(val, 4)
        audits.append({
            "ts": schema.now_ts(), "source": src, "param": name,
            "from": round(cur, 4), "to": round(val, 4),
            "applied": round(val - cur, 4), "reason": reason,
        })

    return out, audits


def audit_line(audit: Dict) -> str:
    return "%s %s %s: %.3f→%.3f (%s)" % (
        schema.as_text(audit.get("param"), 32, "?"),
        "接受" if audit.get("applied") else "拒绝",
        schema.as_text(audit.get("source"), 24, "?"),
        schema.num(audit, "from", 0.0), schema.num(audit, "to", 0.0),
        schema.as_text(audit.get("rejected") or audit.get("reason"), 40, ""),
    )

# ---------------------------------------------------------------- 事件总线

class EventBus:
    """极简事件总线：让模块之间"挂载监听"，而不是两两硬连。

    用法：
        bus = EventBus()
        bus.subscribe("mood_change", fn)
        bus.publish("mood_change", {"label": "低落"})
    """

    def __init__(self):
        self._subs = {}
        self._history = []

    def subscribe(self, topic: str, callback) -> None:
        if not callable(callback):
            return
        self._subs.setdefault(topic, []).append(callback)

    def unsubscribe(self, topic: str, callback) -> None:
        items = self._subs.get(topic) or []
        if callback in items:
            items.remove(callback)

    def publish(self, topic: str, payload: Dict) -> int:
        """发布事件；单个回调异常不影响其他订阅者。"""
        n = 0
        for cb in list(self._subs.get(topic) or []) + list(self._subs.get("*") or []):
            try:
                cb(payload)
                n += 1
            except Exception:
                continue
        self._history.append({"ts": payload.get("ts"), "topic": topic})
        self._history = self._history[-100:]
        return n

    def topics(self):
        return {k: len(v) for k, v in self._subs.items()}


def default_bus() -> EventBus:
    return EventBus()
