"""BEAM-AI · 第 3.5 层：开口闸门（gate.py）

动机说"想说话"，不等于"现在就开口"。这一层照着照片 C 段的六道闸做工程化：

    C1 被点名（@/引用/叫名字）      → 强制开口（最高优先）
    C2 梯度触发（情绪/紧迫超阈值）   → 直接开口
    C3 静默搭话（长时间没人说话）    → 低概率主动找话
    C4 开口闸门（概率/频率/识趣）    → 概率门 + 每小时上限 + 被忽略降权
    C5 门禁（深夜/午睡/忙碌）        → 概率打折，不是绝对禁止
    C6 未响应队列（pending）        → 当时没说，之后补一句

设计约束：
- 全部确定性（用 tick 与时间做种子，不用真随机）→ 可回放、可审计；
- 只决定"开不开口"，不决定"说什么"（说什么归 L5 表达）；
- 输出 route（quick/normal/deep）给宿主做 LLM 路由，轻话不占重模型。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import schema, stance as stance_mod, thoughts as thoughts_mod

#: 每小时最多主动开口次数（不含被点名）
MAX_OPENS_PER_HOUR = 18

#: 被连着忽略几次后，概率按这个系数衰减
IGNORE_DECAY = 0.82

#: 识趣：主人上一小时消息数越多（在忙），概率打折越狠
BUSY_MSGS = 40
BUSY_FACTOR = 0.55

#: 门禁概率（不是禁止，是打折）
NIGHT_FACTOR = 0.30      # 0-6 点
LUNCH_FACTOR = 0.55      # 12-14 点
DEEP_NIGHT_HOURS = (0, 1, 2, 3, 4, 5, 6)

#: 概率下限（只要不撞频率上限，永远留一点可能）
MIN_PROB = 0.04

#: 未响应队列最多存几条
PENDING_MAX = 5

#: 情绪/紧迫超过这些值就直接开口（梯度触发）
EAGER_URGENCY = 0.88
EAGER_NEG_LOAD = 0.80


def _det_random(state: Dict, salt: int = 0) -> float:
    """确定性伪随机 0~1：只由 tick 与 salt 决定（可回放、与真实时间解耦）。"""
    tick = int(schema.num(state, "tick_count", 0))
    x = (tick * 1103515245 + salt * 7919 + 8899) & 0x7FFFFFFF
    return (x % 10000) / 10000.0


def _hour(ctx: Dict) -> int:
    return int(schema.clamp((ctx or {}).get("hour"), 0, 23, 12))


def _recent_opens(gate: Dict, now: float) -> List[float]:
    opens = gate.get("recent_opens") if isinstance(gate.get("recent_opens"), list) else []
    return [schema.finite(x, 0.0) for x in opens if now - schema.finite(x, 0.0) < 3600.0]


#: 两次"主动开口"记账的最小间隔（秒）：防止每 5s 一拍把配额刷满
OPEN_DEBOUNCE_S = 300.0
#: 开门保持时长（秒）：随机掷中后保持一会儿，避免"瞬时随机"让下游（主动消息）读到 False
OPEN_HOLD_S = 120.0


def _record_opens(opens: List[float], now: float, open_now: bool, trigger: str) -> List[float]:
    """只在真正"主动开口"且距上次 ≥5 分钟时记一笔配额。"""
    out = list(opens)
    if not open_now or trigger != "ambient":
        return out
    if out and now - max(out) < OPEN_DEBOUNCE_S:
        return out
    out.append(now)
    return out[-MAX_OPENS_PER_HOUR:]


def classify_trigger(ev: Optional[Dict], ctx: Optional[Dict]) -> str:
    """识别这次输入属于哪种触发。"""
    ev = ev if isinstance(ev, dict) else {}
    ctx = ctx if isinstance(ctx, dict) else {}
    if ev.get("mention") or ctx.get("mention"):
        return "mention"
    if ev.get("direct"):
        return "direct"
    if ev.get("kind") in ("speech", "chat"):
        return "direct"
    return "ambient"


def evaluate(state: Dict, trigger: str = "ambient", ctx: Optional[Dict] = None,
             ev: Optional[Dict] = None, stance: Optional[Dict] = None,
             thoughts: Optional[List] = None) -> Dict:
    """算这一拍要不要开口。返回新的 gate 子对象。

    [iter36] 新增两个行为层入参（都可省略，省略时从 state 兼容读取 / 现算）：
      - stance：四档行为状态，shut 档主动开口概率 ×0.2（不是 0）；
      - thoughts：念头池，speak_bias 给 ambient 分支的概率做 ±0.15 方向修正。
    两者都不改六道闸的顺序与含义，只调概率/理由文案。
    """
    st = schema.validate(state) if isinstance(state, dict) else schema.default_state()
    ctx = ctx if isinstance(ctx, dict) else {}
    gate = st["gate"]
    # 时间锚点用 state["ts"]（回放时是虚拟时间），没有才退真实时钟
    now = schema.finite(st.get("ts"), 0.0) or schema.now_ts()
    hour = _hour(ctx)
    arb = st["arbitration"]
    urgency = schema.clamp01(arb.get("urgency"), 0.0)
    safety = st["safety"]

    opens = _recent_opens(gate, now)
    ignore_streak = int(clamp_int := schema.clamp(gate.get("ignore_streak"), 0, 10000, 0))
    pending = [x for x in (gate.get("pending") or []) if isinstance(x, dict)][-PENDING_MAX:]

    reason = ""
    probability = 0.0
    open_now = False
    route = "normal"
    next_until = schema.finite(gate.get("open_until"), 0.0)

    # C1 被点名：必开
    if trigger == "mention":
        open_now = True
        probability = 1.0
        reason = "被点名"
    # 深度负面/满溢：梯度触发（安抚优先）
    elif schema.finite(safety.get("neg_load"), 0.0) >= EAGER_NEG_LOAD:
        open_now = True
        probability = 1.0
        reason = "情绪满溢，需要说"
    elif urgency >= EAGER_URGENCY and trigger == "direct":
        open_now = True
        probability = 1.0
        reason = "憋不住了"
    else:
        # C4 基础概率：紧迫度映射
        probability = schema.clamp01(0.05 + 0.75 * urgency, MIN_PROB)
        # [iter36] 念头方向：+0.15*speak_bias（只在 ambient 生效；quiet 的 -1 会把它压到 0）
        try:
            _pool = thoughts if isinstance(thoughts, list) else (
                st.get("thoughts") if isinstance(st.get("thoughts"), list) else [])
            _bias = thoughts_mod.speak_bias(_pool, now) if _pool else 0.0
            if _bias:
                probability = schema.clamp01(probability + 0.15 * _bias)
        except Exception:
            pass
        # 频率
        if len(opens) >= MAX_OPENS_PER_HOUR:
            probability = 0.0
            reason = "一小时说太多了"
        # 识趣：被忽略越多越收敛；主人在忙打折
        if ignore_streak > 0:
            probability *= IGNORE_DECAY ** min(6, ignore_streak)
        busy = schema.clamp(ctx.get("msgs_last_hour"), 0.0, 999.0, 0.0)
        if busy >= BUSY_MSGS:
            probability *= BUSY_FACTOR
        # C5 门禁
        if hour in DEEP_NIGHT_HOURS:
            probability *= NIGHT_FACTOR
        elif 12 <= hour <= 14:
            probability *= LUNCH_FACTOR
        # [iter36] 四档：shut 档主动开口 ×0.2（只冻结"主动"，不冻结回话；不真吞消息）
        try:
            _stt = stance if isinstance(stance, dict) else (
                st.get("stance") if isinstance(st.get("stance"), dict) else stance_mod.compute(st))
            _factor = stance_mod.gate_factor(_stt.get("level"))
            if _factor < 1.0:
                probability *= _factor
                _why = stance_mod.gate_reason(_stt.get("level"))
                reason = (reason + "；" if reason else "") + _why
        except Exception:
            pass
        probability = schema.clamp01(probability, 0.0)
        if probability > 0.0:
            open_now = _det_random(st, salt=hour) < probability
        # [fix] 开门保持：掷中后 120s 内保持"开"，防止下游在随机低谷读到 False
        _prev_until = schema.finite(gate.get("open_until"), 0.0)
        if open_now:
            next_until = now + OPEN_HOLD_S
        elif trigger == "ambient" and now < _prev_until:
            open_now = True
            next_until = _prev_until
            # [iter36] 保住行为层写下的 reason（如"回避档"），别被 hold 覆盖
            reason = reason or "概率门通过"
        else:
            open_now = False
            next_until = 0.0
            reason = reason or "概率为 0"

    # C6 未响应队列：没开口但现在被点名/被搭话 → 补一句
    if not open_now and trigger in ("mention", "direct"):
        pending.append({"ts": now, "trigger": trigger,
                        "text": schema.as_text((ev or {}).get("why"), 40, "")})
        pending = pending[-PENDING_MAX:]
    if open_now and pending:
        reason = reason + "（补上之前没接的话）"
        pending = []

    # LLM 路由：轻话不占重模型
    if open_now:
        if urgency >= 0.75 or schema.finite(safety.get("neg_load"), 0.0) >= 0.6:
            route = "deep"
        elif urgency >= 0.45:
            route = "normal"
        else:
            route = "quick"

    return {
        "ts": now,
        "open": bool(open_now),
        "probability": round(probability, 4),
        "reason": reason,
        "route": route,
        # 只有"主动开口"且与上次间隔 ≥300s 才记配额：
        # - 对话回复不占主动配额（否则聊天一多就误拦）
        # - ambient 每 5s 评估一次，不加去抖会 90 秒打满
        "recent_opens": _record_opens(opens, now, open_now, trigger),
        "pending": pending,
        "ignore_streak": ignore_streak,
        "last_user_ts": now if trigger in ("mention", "direct") else schema.finite(gate.get("last_user_ts"), 0.0),
        "last_open_ts": now if open_now else schema.finite(gate.get("last_open_ts"), 0.0),
        "open_until": next_until,
    }


def mark_ignored(state: Dict) -> Dict:
    """宿主调用：这次开口没被接住（用户没回/已读不回）→ 记一次忽略。"""
    st = schema.validate(state) if isinstance(state, dict) else schema.default_state()
    gate = dict(st["gate"])
    gate["ignore_streak"] = int(schema.clamp(schema.num(gate, "ignore_streak", 0) + 1, 0, 10000, 0))
    st["gate"] = gate
    return st


def mark_replied(state: Dict) -> Dict:
    """宿主调用：这次开口被接住了 → 忽略计数清零。"""
    st = schema.validate(state) if isinstance(state, dict) else schema.default_state()
    gate = dict(st["gate"])
    gate["ignore_streak"] = 0
    st["gate"] = gate
    return st

#: 契约表：能力 -> 是否默认开放（可在 state 里被 gate.contracts 覆盖）
CONTRACTS = {
    "speak": True,       # 开口
    "proactive": True,   # 主动消息
    "tts": True,         # 语音输出
    "draw": True,        # 生图
    "bili": True,        # 陪看
    "deep_model": True,  # 重度模型
    "computer": True,    # 电脑操作
}


def contract_check(state: Dict, capability: str) -> Dict:
    """契约/权限检查：某种能力现在允不允许用。

    规则（比"全开"多一层约束）：
    - deep_model 需要 紧迫/负性负荷 够高（轻话别占重模型）
    - proactive 需要闸门开着
    - tts 需要 显存/身体 预算够（GPU 余量少时降级文字）
    """
    st = schema.validate(state) if isinstance(state, dict) else schema.default_state()
    cap = schema.as_text(capability, 24, "")
    gate = st["gate"]
    arb = st["arbitration"]
    body = st["body"]
    if cap not in CONTRACTS:
        return {"allowed": False, "reason": "未知能力"}
    base = bool(CONTRACTS[cap])
    if cap == "deep_model":
        urgency = schema.clamp01(arb.get("urgency"), 0.0)
        neg = schema.finite(st["safety"].get("neg_load"), 0.0)
        allowed = base and (urgency >= 0.45 or neg >= 0.5)
        return {"allowed": allowed, "reason": "" if allowed else "轻话不占重模型"}
    if cap == "proactive":
        allowed = base and bool(gate.get("open"))
        return {"allowed": allowed, "reason": "" if allowed else "闸门未开"}
    if cap == "tts":
        budget = schema.finite(body.get("budget"), 0.85)
        allowed = base and budget >= 0.25
        return {"allowed": allowed, "reason": "" if allowed else "资源吃紧，降级文字"}
    return {"allowed": base, "reason": ""}
