"""BEAM-AI · 第 4 层：元认知与身份连续性（meta.py）

三件事（BEAM 规格）：
1) 自我模型：我能做什么、不能做什么、此刻有多大把握；
2) 元认知审核：这个冲动/这次表达，是不是过了？——它不压制情绪，而是给情绪换解释框架；
3) 叙事连续性：能解释"我为什么这么做"，并且不每次都从零开始（也不复读）。

红线：`audit` 只根据**真实存在的反馈**下结论；没有反馈就说"没有反馈"，不编。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import schema

#: 审核触发：每 N 个对话轮一次
AUDIT_EVERY_ROUNDS = schema.META_AUDIT_EVERY_ROUNDS

#: 审核结论 → 参数调整（有界，且走总线白名单）
VERDICT_ADJUST = {
    "too_cold": {"warmth_bias": +0.06},
    "too_hot": {"warmth_bias": -0.05, "inhibit_bias": +0.04},
    "too_long": {"brevity_bias": +0.06},
    "too_short": {"brevity_bias": -0.04},
    "ok": {},
    "none": {},
}

#: 叙事去重阈值（2-gram Jaccard 相似度）
DEDUP_SIM = 0.55


def _bigrams(text: str) -> set:
    t = schema.as_text(text, 200, "")
    return {t[i:i + 2] for i in range(max(0, len(t) - 1))}


def _similar(a: str, b: str) -> float:
    ga, gb = _bigrams(a), _bigrams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / float(len(ga | gb))


# ---------------------------------------------------------------- 自我模型


def update_self_model(prev: Any, outcome: Dict) -> Dict:
    """按行为结果更新自我模型（成功涨置信，失败降置信；有上下界）。"""
    sm = prev if isinstance(prev, dict) else {}
    caps = sm.get("capacontentties") if isinstance(sm.get("capacontentties"), dict) else {}
    conf = schema.clamp01(sm.get("confidence"), 0.62)
    limits = sm.get("limits") if isinstance(sm.get("limits"), list) else []

    kind = schema.as_text(outcome.get("kind"), 24, "chat")
    ok = bool(outcome.get("success"))
    delta = 0.03 if ok else -0.04
    caps[kind] = schema.clamp(schema.num(caps, kind, 0.5) + delta, 0.0, 1.0, 0.5)
    conf = schema.clamp01(conf + (0.01 if ok else -0.015), conf)

    note = schema.as_text(outcome.get("note"), 80, "")
    if not ok and note and note not in limits and len(limits) < 6:
        limits.append(note)

    return {"capacontentties": {k: round(schema.clamp01(v), 3) for k, v in caps.items()},
            "limits": limits[:6],
            "confidence": round(conf, 3)}


# ---------------------------------------------------------------- 审核


def should_audit(state: Dict, round_count: int) -> bool:
    """什么时候该回头看一眼：到轮数了 / 有未处理反馈 / 负性负荷高（但会限流）。"""
    st = schema.validate(state) if isinstance(state, dict) else schema.default_state()
    au = st["meta"]["audit"]
    if bool(au.get("pending_review")):
        return True
    try:
        last = int(schema.num(au, "last_round", 0.0))
    except Exception:
        last = 0
    # 负性负荷高：至少隔 6 轮再查一次，避免每拍都"例行检查"把真实结论冲掉
    if schema.num(st["safety"], "neg_load", 0.0) >= 0.45 and round_count - last >= 6:
        return True
    return round_count - last >= AUDIT_EVERY_ROUNDS


def audit(prev_state: Dict, feedback: Dict, round_count: int) -> Dict:
    """元认知审核：看刚才那次表达/行为，是否过冷/过热/过长/过短。

    feedback 由宿主提供（用户反应/回复长度/是否被忽略），没有就给 "none"。
    返回 {verdict, note, adjustments, ts}；adjustments 会经总线写入参数（有界）。
    """
    st = schema.validate(prev_state) if isinstance(prev_state, dict) else schema.default_state()
    fb = feedback if isinstance(feedback, dict) else {}
    now = schema.now_ts()

    verdict = "none"
    note = ""
    # 只根据真实反馈判断
    sent = schema.clamp(fb.get("user_sentiment"), -1.0, 1.0, 0.0)
    ignored = bool(fb.get("ignored"))
    reply_len = schema.clamp(fb.get("reply_len"), 0.0, 5000.0, 0.0)

    if ignored:
        verdict = "too_cold"
        note = "刚才说的话，好像没被接住"
    elif sent <= -0.35:
        verdict = "too_hot"
        note = "刚才有点冲，收一收"
    elif sent >= 0.5 and reply_len > 220:
        verdict = "too_long"
        note = "刚才说太长了"
    elif sent >= 0.5 and 0 < reply_len < 8:
        verdict = "too_short"
        note = "刚才太短了，像敷衍"
    elif sent >= 0.35:
        verdict = "ok"
        note = "刚才那样，挺好的"

    adjustments = dict(VERDICT_ADJUST.get(verdict, {}))
    history = st["meta"]["audit"].get("history") if isinstance(st["meta"]["audit"].get("history"), list) else []
    history = (history + [{"ts": now, "verdict": verdict, "note": note,
                           "sentiment": round(sent, 3), "round": round_count}])[-20:]

    return {
        "ts": now,
        "verdict": verdict,
        "note": note,
        "adjustments": adjustments,
        "history": history,
        "last_round": round_count,
        "pending_review": False,
    }


# ---------------------------------------------------------------- 叙事连续性


def add_narrative(prev_narrative: Any, text: str, ts: Optional[float] = None,
                  tags: Optional[List[str]] = None) -> List[Dict]:
    """追加一条叙事（去重 + 上限 8 条，旧的不删只压缩：保留最近 8 条）。"""
    items = prev_narrative if isinstance(prev_narrative, list) else []
    clean = schema.as_text(text, 120, "")
    if not clean:
        return items[-schema.NARRATIVE_MAX:]
    items = [x for x in items if isinstance(x, dict)]
    for item in items[-3:]:
        if _similar(clean, schema.as_text(item.get("text"), 120, "")) >= DEDUP_SIM:
            return items[-schema.NARRATIVE_MAX:]
    items.append({
        "ts": schema.finite(ts, 0.0) or schema.now_ts(),
        "text": clean,
        "tags": [schema.as_text(t, 16, "") for t in (tags or [])][:4],
    })
    return items[-schema.NARRATIVE_MAX:]


def narrative_context(narrative: Any, n: int = 3) -> str:
    """取最近几条叙事拼成一句背景（给注入用，不复读）。"""
    items = [x for x in (narrative if isinstance(narrative, list) else []) if isinstance(x, dict)]
    picked = items[-max(1, n):]
    if not picked:
        return ""
    return "；".join(schema.as_text(x.get("text"), 60, "") for x in picked if schema.as_text(x.get("text"), 60, ""))