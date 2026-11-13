"""BEAM-AI · 第 5 层：表达与行动（express.py）

这一层**没有独立决策权**：它只把内部仲裁结果忠实地映射成表达参数与注入文本，
并把行动结果回传（由 kernel 写回内感受层）。它不许绕过仲裁自己"想说什么就说什么"。

两件事：
1) compute(state) → 表达参数（话多话少/暖度/俏皮/克制/emoji 比例/负性强度）；
2) render_mind(state) → 注入给 LLM 的 <mind> 块（≤600 字，分段预算，按优先级裁剪）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import (drive as drive_mod, meta as meta_mod, safety as safety_mod, schema,
               stance as stance_mod, thoughts as thoughts_mod, traits as traits_mod)

#: 注入总预算（字）
MIND_BUDGET = 600

#: 分段预算：段名 -> 每段上限（字）
#: [iter36] 新增 thought(60) / stance(55)；总预算 MIND_BUDGET 不变，仍按优先级裁剪
SEGMENT_BUDGET = {"emotion": 130, "motive": 110, "thought": 60, "stance": 55,
                  "speak": 150, "body": 70, "memory": 90, "meta": 60}

#: [iter36] 精简注入的硬预算（测试闸门：render_lite <= LITE_BUDGET，线上 240）
LITE_BUDGET = 240

#: 情绪 → 说话风格（具体到句式特征，不是抽象倾向）
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


def _resolve_stance(state: Dict, stance: Optional[Dict] = None) -> Dict:
    """[iter36] 档位来源优先级：显式入参 > state 快照（兼容）> 现算（纯函数）。

    内核每拍算好 stance 会显式传进来；直接调 express 的老路径拿不到也没关系——
    stance.compute 是纯函数，现算结果一致。
    """
    if isinstance(stance, dict) and stance.get("level") in stance_mod.LEVELS:
        return stance
    snap = state.get("stance") if isinstance(state, dict) else None
    if isinstance(snap, dict) and snap.get("level") in stance_mod.LEVELS:
        return snap
    try:
        return stance_mod.compute(state)
    except Exception:
        return {"level": "calm", "level_cn": "平静", "score": 0.0, "reason": "",
                "tendency": stance_mod.TENDENCY["calm"]}


def compute(state: Dict, stance: Optional[Dict] = None) -> Dict:
    """内部状态 → 表达参数（全部有界）。

    [iter36] stance：四档行为状态（calm/wronged/afraid/shut），只做有界表达修正，
    不改仲裁结果（“说什么”仍归 drive/affect，“怎么说”才在这一层）。
    """
    st = schema.validate(state) if isinstance(state, dict) else schema.default_state()
    f, s, i = st["fast"], st["slow"], st["intero"]
    arb, p, sec = st["arbitration"], st["params"], st["safety"]

    v = schema.finite(f.get("valence"), 0.0)
    a = schema.finite(f.get("arousal"), 0.3)
    energy = schema.clamp01(1.0 - schema.clamp01(s.get("fatigue"), 0.2), 0.8)
    stress = schema.clamp01(s.get("stress"), 0.15)
    bonding = schema.clamp01(s.get("bonding"), 0.5)
    urgency = schema.clamp01(arb.get("urgency"), 0.3)
    inhibit = schema.clamp01(arb.get("inhibit"), 0.0)
    isolated = bool(sec.get("isolation"))

    # 话多话少：情绪越高越想说；累/压力大/被抑制时少说
    talkative = schema.clamp01(0.40 + 0.35 * max(0.0, v) + 0.25 * a + 0.10 * energy
                               - 0.25 * stress - 0.30 * inhibit, 0.5)
    # 暖度：联结高 + 正效价 - 隔离
    warmth = schema.clamp01(0.50 + 0.30 * (bonding - 0.5) * 2.0 + 0.25 * max(0.0, v)
                            - 0.20 * stress - 0.15 * inhibit, 0.5)
    if isolated:
        warmth = max(warmth, safety_mod.RECOVERY_EFFECT["express_warmth_floor"])
    # 俏皮：正效价 + 低压力（低落/烦躁时收起来）
    playfulness = schema.clamp01(0.30 + 0.40 * max(0.0, v) + 0.20 * (energy - 0.5)
                                 - 0.35 * max(0.0, -v) - 0.30 * stress, 0.3)
    # 简洁度：累/压力/长回复反馈
    brevity = schema.clamp01(0.45 + 0.30 * stress + 0.20 * (1.0 - energy) + 0.15 * inhibit,
                             0.45)
    # emoji 比例：俏皮高才多用
    emoji_rate = schema.clamp01(playfulness * 0.6 + max(0.0, v) * 0.2, 0.0)
    # 负性表达强度（给硬底线检查用）
    negative_intensity = schema.clamp01(max(0.0, -v) * 0.9 + stress * 0.3, 0.0)

    # 参数修正（来自元认知审核与下行总线，有界）
    warmth = schema.clamp01(warmth + schema.num(p, "warmth_bias", 0.0), warmth)
    brevity = schema.clamp01(brevity + schema.num(p, "brevity_bias", 0.0), brevity)
    inhibit = schema.clamp01(inhibit + schema.num(p, "inhibit_bias", 0.0) * 0.5, inhibit)

    # [iter27] 长期人格偏置（天~周级漂移，比情绪慢得多；有界叠加）
    try:
        tr = st.get("traits") if isinstance(st.get("traits"), dict) else {}
        def _t(key: str, dflt: float) -> float:
            v = tr.get(key)
            return schema.clamp01(v, dflt) if isinstance(v, (int, float)) else dflt
        cling = _t("clinginess", 0.35)
        hum = _t("humor", 0.30)
        verb = _t("verbosity", 0.50)
        er = _t("emotionRate", 0.30)
        warmth = schema.clamp01(warmth + (cling - 0.35) * 0.20, warmth)
        playfulness = schema.clamp01(playfulness + (hum - 0.30) * 0.20, playfulness)
        brevity = schema.clamp01(brevity + (0.50 - verb) * 0.25, brevity)
        talkative = schema.clamp01(talkative + (verb - 0.50) * 0.20, talkative)
        emoji_rate = schema.clamp01(emoji_rate + (er - 0.30) * 0.15, 0.0)
    except Exception:
        pass

    # [iter36] 四档 stance：负性档只压低暖度/话量、抬高简洁度（每项 ±0.15 有界）。
    #   刻意不改仲裁结果，也不改"话多话少"的来源（drive/affect 仍是唯一来源）。
    _stt = _resolve_stance(st, stance)
    _lvl = schema.as_text(_stt.get("level"), 8, "calm")
    for _name in ("warmth", "brevity", "talkative", "playfulness"):
        _delta = stance_mod.adjust(_lvl, _name)
        if not _delta:
            continue
        if _name == "warmth":
            warmth = schema.clamp01(warmth + _delta, warmth)
        elif _name == "brevity":
            brevity = schema.clamp01(brevity + _delta, brevity)
        elif _name == "talkative":
            talkative = schema.clamp01(talkative + _delta, talkative)
        else:
            playfulness = schema.clamp01(playfulness + _delta, playfulness)
    # 负性档不玩梗：emoji 跟随俏皮度（仍是有界修正，不是新机制）
    if _lvl != "calm":
        emoji_rate = schema.clamp01(min(emoji_rate, playfulness * 0.6 + max(0.0, v) * 0.2), 0.0)

    label = schema.as_text(f.get("label"), 16, "平静")
    return {
        "label": label,
        "style": STYLE.get(label, STYLE["平静"]),
        "talkative": round(talkative, 3),
        "warmth": round(warmth, 3),
        "playfulness": round(playfulness, 3),
        "brevity": round(brevity, 3),
        "emoji_rate": round(emoji_rate, 3),
        "inhibition": round(inhibit, 3),
        "negative_intensity": round(negative_intensity, 3),
        "energy": round(energy, 3),
        "bonding": round(bonding, 3),
        "urgency": round(urgency, 3),
        "drive": schema.as_text(arb.get("winner"), 16, "connect"),
        "grounded": True,
        "isolated": isolated,
        # [iter36] 四档行为状态（面板 / 注入 / 第二批模块消费；不进 state 契约）
        "stance": {
            "level": _lvl,
            "level_cn": schema.as_text(_stt.get("level_cn"), 8, "平静"),
            "score": round(schema.finite(_stt.get("score"), 0.0), 2),
            "reason": schema.as_text(_stt.get("reason"), 80, ""),
            "tendency": schema.as_text(_stt.get("tendency"), 80, ""),
        },
        "confidence": round(schema.clamp01(st["meta"]["self_model"].get("confidence"), 0.6), 3),
        "sentence_hint": ("1-2 句就好" if brevity >= 0.62 else
                          "几句话，别长篇" if brevity >= 0.5 else "可以多说两句，但别说教"),
    }


def _trim(text: str, limit: int) -> str:
    t = schema.as_text(text, limit + 20, "")
    return t if len(t) <= limit else t[: max(0, limit - 1)] + "…"


def render_mind(state: Dict, thoughts: Optional[List] = None,
                stance: Optional[Dict] = None) -> str:
    """渲染 <mind> 注入块。总长 ≤ MIND_BUDGET，按优先级裁剪。

    [iter36] 优先级：emotion > motive > thought > stance > speak > body > memory > meta
    thoughts/stance 由内核显式传入（念头池不进 state；stance 是内核内存对象）。
    """
    st = schema.validate(state) if isinstance(state, dict) else schema.default_state()
    f, s, i = st["fast"], st["slow"], st["intero"]
    arb = st["arbitration"]
    _stt = _resolve_stance(st, stance)
    exp = compute(st, stance=_stt)
    sec = st["safety"]
    sm = st["meta"]["self_model"]
    _pool = thoughts if isinstance(thoughts, list) else (
        st.get("thoughts") if isinstance(st.get("thoughts"), list) else [])

    drive = exp["drive"]
    theme = drive_mod.THEME.get(drive, "想动一动")
    # 第二动机
    cands = arb.get("candidates") if isinstance(arb.get("candidates"), dict) else {}
    ranked = sorted(cands.items(), key=lambda kv: -schema.num(kv[1], "priority", 0.0))
    second = ""
    if len(ranked) > 1:
        second_name = ranked[1][0]
        second = "%s（%.2f）" % (drive_mod.THEME.get(second_name, ""), schema.num(ranked[1][1], "urgency", 0.0))

    seg_emotion = (
        "此刻：%s（效价 %+.2f，唤醒 %.2f，把握 %.2f）。"
        "这阵子的底色：%s；压力 %.2f。"
        % (f.get("label", "平静"), schema.finite(f.get("valence"), 0.0),
           schema.finite(f.get("arousal"), 0.0), schema.finite(f.get("dominance"), 0.5),
           "偏暖" if schema.finite(s.get("mood_valence"), 0.0) > 0.15 else
           "偏凉" if schema.finite(s.get("mood_valence"), 0.0) < -0.1 else "平的",
           schema.finite(s.get("stress"), 0.0))
    )
    seg_motive = "此刻最想：%s（紧迫 %.2f）" % (theme, exp["urgency"])
    if second:
        seg_motive += "；其次是%s" % second
    if sec.get("isolation"):
        seg_motive += "。注意：你在恢复期，先照顾自己"

    # [iter36] 念头：只写"当前最惦记的那一条"（一句，≤60 字段预算）
    seg_thought = thoughts_mod.inject_line(_pool)
    # [iter36] stance：非平静档必注（跨档必注的当期实现：非 calm 每轮都写；
    #   同档节流留给第二批模块按会话记账，见报告"风险与回滚"）。
    _lvl = schema.as_text(_stt.get("level"), 8, "calm")
    seg_stance = ""
    if _lvl != "calm":
        seg_stance = "现在的状态：%s——%s。" % (
            schema.as_text(_stt.get("level_cn"), 8, "平静"),
            schema.as_text(_stt.get("tendency"), 80, ""))

    seg_speak = (
        "怎么说：%s；%s。话多话少 %.2f，暖度 %.2f，俏皮 %.2f；别说教、别复读、别编。"
        % (exp["style"], exp["sentence_hint"], exp["talkative"], exp["warmth"], exp["playfulness"])
    )
    # [iter27] 长期人格一句话（只提变化方向，不占太多预算）
    try:
        _tr = st.get("traits") if isinstance(st.get("traits"), dict) else {}
        if _tr:
            _line = traits_mod.summary(_tr)
            if _line:
                seg_speak += " 长期：%s。" % _line
    except Exception:
        pass

    seg_body = "身体：精力 %.2f，负载 %.2f，工具%s。刚才有没有不舒服：%s。" % (
        exp["energy"], schema.finite(i.get("load"), 0.0),
        "大体可用" if schema.num(i, "resource", 0.7) > 0.5 else "有点吃紧",
        "有" if schema.finite(f.get("valence"), 0.0) < -0.2 else "没有",
    )

    narrative = meta_mod.narrative_context(st["meta"].get("narrative"), 2)
    seg_memory = ("最近记得：%s。" % narrative) if narrative else ""

    seg_meta = "对自己的把握 %.2f；不确定就直说不知道。" % schema.clamp01(sm.get("confidence"), 0.6)

    segments: List[tuple] = [
        ("emotion", seg_emotion),
        ("motive", seg_motive),
        ("thought", seg_thought),
        ("stance", seg_stance),
        ("speak", seg_speak),
        ("body", seg_body),
        ("memory", seg_memory),
        ("meta", seg_meta),
    ]
    parts = []
    used = 0
    for name, text in segments:
        if not text:
            continue
        budget = SEGMENT_BUDGET.get(name, 80)
        piece = _trim(text, budget)
        if used + len(piece) + 1 > MIND_BUDGET:
            break
        parts.append(piece)
        used += len(piece) + 1
    return "<mind>\n" + "\n".join(parts) + "\n</mind>"



def render_short(state: Dict) -> str:
    """一行极简摘要（日志/面板用，不注入）。"""
    st = schema.validate(state) if isinstance(state, dict) else schema.default_state()
    exp = compute(st)
    return "[%s|%s|话%.2f|暖%.2f|驱%.2f]" % (
        exp["label"], exp["drive"], exp["talkative"], exp["warmth"], exp["urgency"])

def render_lite(state: Dict, thoughts: Optional[List] = None,
                stance: Optional[Dict] = None) -> str:
    """精简注入块（≤ LITE_BUDGET=240 字）：只写旧系统没有的「连续状态」。

    旧注入里已经有「此刻情绪 / 说话风格 / 身体能量 / 意图」，
    这里刻意不重复，只补三样新东西：跨小时的心情底色（慢通道）+ 恢复期标记
    + [iter36] 四档档位倾向与"心里惦记着的那一条"。这样既不稀释注意力，
    也不和旧意图层打架。
    """
    st = schema.validate(state) if isinstance(state, dict) else schema.default_state()
    s = st["slow"]
    mood = schema.finite(s.get("mood_valence"), 0.0)
    tone = "偏暖" if mood > 0.15 else ("偏凉" if mood < -0.10 else "平的")
    head = [
        "连续状态（跨小时的心情底色，不是这一轮的情绪）：这阵子%s；压力 %.2f；负性负荷 %.2f。"
        % (tone, schema.finite(s.get("stress"), 0.0),
           schema.finite(st["safety"].get("neg_load"), 0.0)),
    ]
    if st["safety"].get("isolation"):
        head.append("你现在在恢复期，先照顾自己，别硬撑。")
    # [iter36] 行为层：档位倾向 + 念头（都按优先级插在"底色只影响"之前）
    _stt = _resolve_stance(st, stance)
    _lvl = schema.as_text(_stt.get("level"), 8, "calm")
    behaviour: List[str] = []
    if _lvl != "calm":
        # 跨档必注：档位一变就写进注入（本函数无跨轮记忆，非 calm 每轮都注；
        # TODO(第二批)：按会话记账做"同档 20 分钟节流"，见 48b 报告）。
        behaviour.append("现在的状态：%s——%s。" % (
            schema.as_text(_stt.get("level_cn"), 8, "平静"),
            schema.as_text(_stt.get("tendency"), 80, "")))
    _pool = thoughts if isinstance(thoughts, list) else (
        st.get("thoughts") if isinstance(st.get("thoughts"), list) else [])
    _line = thoughts_mod.inject_line(_pool)
    if _line:
        behaviour.append(_line)
    tail = ["底色只影响说话的温度；此刻说什么，以这一轮的为准。"]
    # 路由 → 说话长度（gate 的 quick/deep 真正影响生成）
    route = schema.as_text(st["gate"].get("route"), 12, "normal")
    if route == "quick":
        tail.append("这轮回应一句就够，别铺开。")
    elif route == "deep":
        tail.append("这轮可以认真展开说，但也别说教。")
    lines = head + behaviour + tail
    # 硬预算：超了先丢行为层（它是增量信息，不能挤掉核心底色），再逐条裁
    while len("<inner>\n" + "\n".join(lines) + "\n</inner>") > LITE_BUDGET and behaviour:
        behaviour.pop()
        lines = head + behaviour + tail
    while len("<inner>\n" + "\n".join(lines) + "\n</inner>") > LITE_BUDGET and len(lines) > 1:
        lines.pop()
    return "<inner>\n" + "\n".join(lines) + "\n</inner>"