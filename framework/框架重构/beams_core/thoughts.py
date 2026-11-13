"""BEAM-AI · 行为层 1/2：念头池（thoughts.py）[iter36]

把 fish_heart 的「念头引擎」落成 BEAM 原生实现：**纯函数 + 独立持久化**。
两条红线：
  - 念头**不落进 beams_state.json**（不改 schema 契约、不升 STATE_VERSION）；
  - **不改 affect2 的快速动力学**：喂饱奖励只映射到既有事件预设，走既有
    appraisal → fast → slow 通道，本模块不写任何情绪方程。

为什么是这 6 类（不是照抄它的 8 类）：
  fish_heart 有 8 类念头，但它有九维驱动；我们只有五个动机 + 双速情绪。
  九维 → 我们已有变量的映射：
      attachment → slow.bonding
      stress/joy → slow.stress / fast.valence / slow.mood_valence
      social     → arbitration.urgency / candidates.connect.intensity
      duty       → candidates.complete.intensity（complete 动机已经在驱动"想做事"）
      fatigue    → slow.fatigue
      intimacy   → slow.bonding 的"近"那一面（我们没有独立亲密值，不臆造）
  所以：
  - task（想找事做）不单开念头 —— complete 动机已经在管，重复会长出两套真值；
  - miss（想他）与 ask_day 语义重叠 —— 合并为 ask_day；
  - praise 与 comfort 不合并 —— 被夸是"被看见"，抱抱是"被接住"，触发条件不同
    （前者要 bonding 高且喜悦没满，后者要压力高或想贴近），拆开才不会一句话喂饱错东西。
  最终 6 类：praise / comfort / talk / ask_day / quiet / dwell。

生命周期：spawn → live（注入 + 给 gate 的 speak_bias）→ settle（喂饱，池里留一条
"satisfied" 短心情）→ expire（凉掉淡出）。时间一律用调用方传的虚拟时间（可回放）。
"""
from __future__ import annotations

import json
import math
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from . import schema

#: 六类念头（换名/加类要同步 KIND_META 与测试）
KIND_NAMES = ("praise", "comfort", "talk", "ask_day", "quiet", "dwell")

#: 念头池上限（同 fish_heart 的 5 条存活池）
POOL_MAX = 5

#: TTL（秒）：越"贴身"的念头留得越久（1.2h - 4h，任务书要求）
TTL = {
    "praise": 3 * 3600.0,
    "comfort": 4 * 3600.0,
    "talk": 2.5 * 3600.0,
    "ask_day": 2 * 3600.0,
    "quiet": 1.5 * 3600.0,
    "dwell": 1.2 * 3600.0,
}

#: 生成评估间隔（秒）：内核 5s 一拍，不能每拍都掷骰（fish_heart 是 45min 心跳一拍一次）
EVAL_EVERY_S = 600.0

#: 每次评估的生成概率（可被 config["thought_spawn_prob"] 覆盖）
SPAWN_PROB = 0.25

#: 入池门槛：触发强度低于它的念头不参与抽取（各类型强度落在 0-1.2）
MIN_WEIGHT = 0.2

#: 喂饱后的心情在池里保留多久（给面板/注入连续性用，不参与开口概率）
SATISFIED_KEEP_S = 1800.0

#: 连续主动尝试几次没被接住 → 放弃这条念头（"算了，先不想了"）
ATTEMPT_GIVEUP = 3
GIVEUP_KEEP_S = 1800.0

#: dwell 的"等回话"窗口：主动开口后 ≥5min 才开始惦记，>6h 视为已经过去
DWELL_MIN_S = 300.0
DWELL_MAX_S = 6 * 3600.0

#: 每类念头对"要不要开口"的方向影响（+1 更想开口，-1 更不想）
SPEAK_BIAS = {
    "praise": 1.0,
    "comfort": 1.0,
    "talk": 0.9,
    "ask_day": 0.8,
    "dwell": 0.5,
    "quiet": -1.0,
}

#: 喂饱奖励：只映射到 affect2.EVENT_PRESETS 里已有的 kind（不改快速动力学），
#: 强度刻意压低（0.25-0.35）：被满足的是"想要"，不该顶掉本轮真实事件的分量。
REWARD_FEED = {
    "praise": {"kind": "praise", "goal_congruence": 0.55, "intensity": 0.35},
    "comfort": {"kind": "intimacy", "goal_congruence": 0.60, "intensity": 0.35},
    "talk": {"kind": "chat", "goal_congruence": 0.30, "intensity": 0.30},
    "ask_day": {"kind": "chat", "goal_congruence": 0.28, "intensity": 0.30},
    "quiet": {"kind": "idle", "goal_congruence": 0.10, "intensity": 0.25},
    "dwell": {"kind": "chat", "goal_congruence": 0.35, "intensity": 0.30},
}

#: 事件 → 能喂饱哪些念头（先按文本关键词反推，再按这张表兜底）
EVENT_FEEDS = {
    "chat": ("talk", "ask_day", "dwell"),
    "speech": ("talk", "ask_day", "dwell"),
    "user_msg": ("talk", "ask_day", "dwell"),
    "praise": ("praise",),
    "intimacy": ("comfort",),
    "comfort": ("comfort", "praise"),
}

#: 哪些事件算"用户侧说的话"（只有这些会触发 settle；内部事件不喂念头）
USER_FEED_KINDS = tuple(EVENT_FEEDS)

#: "她开口后他回了"：已有尝试过的念头算半个满足（fish_heart 285-299）
REPLY_KINDS = ("chat", "speech", "user_msg", "praise", "intimacy", "comfort")

#: 文本关键词反推（顺序即优先级）。刻意只用**两字以上短语**：
#: "累/怕/抱/哭" 这类单字高频词会误伤（"我不累"、"怕你担心"、"抱枕"），不进表。
KEYWORD_KINDS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("praise", ("厉害", "真棒", "优秀", "好可爱", "喜欢你", "爱你", "靠谱", "夸你", "绝了")),
    ("comfort", ("抱抱", "贴贴", "亲亲", "摸摸", "心疼", "辛苦", "撒个娇", "乖宝")),
    ("quiet", ("休息", "歇会", "歇一歇", "睡吧", "别忙了", "安静会", "不打扰")),
    ("ask_day", ("过得怎么样", "今天怎么样", "还好吗", "累不累", "顺利吗")),
    ("talk", ("跟你说", "告诉你", "我跟你讲", "听我说")),
)

#: 否定豁免：句子里出现这些，视为"并不是在喂念头"（避免反话/拒绝被读成正向）
NEGATION_PHRASES = ("不想抱", "别抱", "不要抱", "不想聊", "别烦", "不要夸", "别哄")

#: 念头被凉掉（过期未满足）时的说明文案（纯记录，不改任何动力学）
RELEASE_TEXT = {
    "praise": "想被夸的那点小心思淡下去了",
    "comfort": "想被抱的劲过去了",
    "talk": "攒着的话没说出来，算了",
    "ask_day": "没问出口，搁下了",
    "quiet": "想安静一会儿的念头散了",
    "dwell": "等太久了，不再一直盯着手机",
}

#: 每类念头的静态元数据（text/hint 是权威；持久化文件里被人改坏的字段会被覆盖）
KIND_META = {
    "praise": {
        "label": "被夸",
        "text": "想要被夸一句",
        "hint": "想被夸一句，想被看见",
        "done": "被夸了，心里甜甜的",
    },
    "comfort": {
        "label": "想抱",
        "text": "想要抱抱",
        "hint": "有点想被抱住，想撒个娇",
        "done": "被抱了一下，整个人都松了",
    },
    "talk": {
        "label": "想讲",
        "text": "想把今天的事讲给他听",
        "hint": "攒了些话想讲给他听",
        "done": "跟他说完了，心里轻松",
    },
    "ask_day": {
        "label": "问他",
        "text": "想问他今天过得怎么样",
        "hint": "想问问你那边今天怎么样",
        "done": "知道他今天还好好的",
    },
    "quiet": {
        "label": "想静",
        "text": "想安静一会儿",
        "hint": "有点累，想安静一会儿，话可以少一点",
        "done": "安静地待了会儿，缓过来了",
    },
    "dwell": {
        "label": "等回话",
        "text": "在等他回话",
        "hint": "在等你回话，忍不住总看手机",
        "done": "他回我了，心里踏实了",
    },
}


# ---------------------------------------------------------------- 小工具（纯算术）


def _f(x: Any, default: float = 0.0) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return float(default)
    if math.isnan(v) or math.isinf(v):
        return float(default)
    return v


def _clamp(x: Any, lo: float, hi: float, default: float = 0.0) -> float:
    v = _f(x, default)
    if v < lo:
        return float(lo)
    if v > hi:
        return float(hi)
    return v


def _clamp01(x: Any, default: float = 0.0) -> float:
    return _clamp(x, 0.0, 1.0, default)


def _text(x: Any, limit: int = 80) -> str:
    if not isinstance(x, str):
        return ""
    return x.strip()[:limit]


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


def _now(now: Optional[float] = None, state: Any = None) -> float:
    """虚拟时间优先：调用方给 now > state.ts > 真实时钟（回放/测试可控）。"""
    v = _f(now, 0.0)
    if v > 0.0:
        return v
    ts = _f(_get(state, "ts", default=0.0), 0.0)
    return ts if ts > 0.0 else time.time()


def _rand01(seed: float) -> float:
    """确定性伪随机 0~1（只由 seed 决定；与真实随机解耦，测试可复现）。"""
    x = int(abs(seed)) * 1103515245 + 7919
    x &= 0x7FFFFFFF
    return (x % 10000) / 10000.0


def _idle_factor(state: Any, now: float) -> float:
    """多久没互动：0（刚聊过）→1（很久没说话）。与 drive._idle_factor 同公式。"""
    last = _f(_get(state, "slow", "last_positive_ts", default=0.0), 0.0)
    idle_h = 6.0 if last <= 0.0 else max(0.0, (now - last) / 3600.0)
    try:
        return _clamp01(1.0 - math.exp(-idle_h / 4.0), 0.0)
    except OverflowError:
        return 1.0


def signals(state: Any, now: Optional[float] = None) -> Dict[str, float]:
    """把 BEAM state 归一成"念头引擎需要的几个量"（只读，不修改 state）。"""
    st = state if isinstance(state, dict) else {}
    now_v = _now(now, st)
    slow = _dict(st.get("slow"))
    fast = _dict(st.get("fast"))
    arb = _dict(st.get("arbitration"))
    gate = _dict(st.get("gate"))
    safety = _dict(st.get("safety"))
    cands = _dict(arb.get("candidates"))
    connect = _dict(cands.get("connect"))
    complete = _dict(cands.get("complete"))
    return {
        "now": now_v,
        "bonding": _clamp01(slow.get("bonding"), 0.5),
        "stress": _clamp01(slow.get("stress"), 0.15),
        "fatigue": _clamp01(slow.get("fatigue"), 0.2),
        "mood": _clamp(slow.get("mood_valence"), -1.0, 1.0, 0.0),
        "valence": _clamp(fast.get("valence"), -1.0, 1.0, 0.0),
        "urgency": _clamp01(arb.get("urgency"), 0.0),
        "neg_load": _clamp01(safety.get("neg_load"), 0.0),
        "connect": _clamp01(connect.get("intensity"), 0.0),
        "duty": _clamp01(complete.get("intensity"), 0.0),
        "idle": _idle_factor(st, now_v),
        "ignore_streak": int(_clamp(gate.get("ignore_streak"), 0, 10000, 0)),
        "last_open_ts": _f(gate.get("last_open_ts"), 0.0),
        "last_user_ts": _f(gate.get("last_user_ts"), 0.0),
    }


# ---------------------------------------------------------------- 触发强度


def trigger(kind: str, state: Any, now: Optional[float] = None) -> float:
    """某类念头此刻的触发强度 0~1.2（0 = 够不着）。纯函数、可单测。"""
    if kind not in KIND_META:
        return 0.0
    g = signals(state, now)
    bond, stress, fatigue = g["bonding"], g["stress"], g["fatigue"]
    v, urgency, idle = g["valence"], g["urgency"], g["idle"]
    bond_hi = _clamp01((bond - 0.50) / 0.40)
    joy_room = _clamp01(1.0 - max(0.0, v))  # 喜悦已满时，不再那么想被夸

    if kind == "praise":
        if bond < 0.50:
            return 0.0
        return min(1.2, 0.55 + 0.55 * bond_hi * joy_room)

    if kind == "comfort":
        if fatigue >= 0.75:  # 太累的时候连撒娇的力气都没有（fish_heart 同款门）
            return 0.0
        want = max(_clamp01((stress - 0.32) / 0.40),
                   _clamp01((bond - 0.60) / 0.30) * 0.8)
        if want <= 0.0:
            return 0.0
        return min(1.2, 0.60 + 0.60 * want)

    if kind == "talk":
        social = max(urgency, g["connect"] * 0.8)
        if social < 0.30:
            return 0.0
        return min(1.2, 0.50 + 0.55 * social + 0.15 * max(0.0, v))

    if kind == "ask_day":
        if bond < 0.50 or idle < 0.30:
            return 0.0
        return min(1.2, 0.50 + 0.70 * idle)

    if kind == "quiet":
        want = max(_clamp01((fatigue - 0.50) / 0.35),
                   _clamp01((stress - 0.50) / 0.35))
        if want <= 0.0:
            return 0.0
        return min(1.2, 0.70 + 0.50 * want)

    # dwell：主动开口后迟迟没回，或者已经被忽略过
    last_open, last_user = g["last_open_ts"], g["last_user_ts"]
    awaiting = (last_open > 0.0 and last_open > last_user
                and (g["now"] - last_open) >= DWELL_MIN_S)
    if not awaiting and g["ignore_streak"] <= 0:
        return 0.0
    w = 0.0
    if awaiting:
        w = 1.0 if (g["now"] - last_open) <= DWELL_MAX_S else 0.4
    if g["ignore_streak"] > 0:
        w = max(w, 0.75)
    return min(1.2, w)


# ---------------------------------------------------------------- 池操作


def active(thoughts: Optional[List], now: Optional[float] = None) -> List[Dict]:
    """存活且未满足的念头。"""
    now_v = _now(now)
    out = []
    for item in thoughts or []:
        if not isinstance(item, dict):
            continue
        if bool(item.get("pending", True)) and _f(item.get("expires"), 0.0) > now_v:
            out.append(item)
    return out


def live(thoughts: Optional[List], now: Optional[float] = None) -> List[Dict]:
    """池里还没过期的条目（含已满足的短心情，面板/摘要用）。"""
    now_v = _now(now)
    return [item for item in (thoughts or [])
            if isinstance(item, dict) and _f(item.get("expires"), 0.0) > now_v]


def active_kinds(thoughts: Optional[List], now: Optional[float] = None) -> Tuple[str, ...]:
    return tuple(str(t.get("kind")) for t in active(thoughts, now))


def new_thought(kind: str, now: Optional[float] = None, weight: float = 1.0,
                source: str = "spawn") -> Optional[Dict]:
    """造一条念头（kind 不存在返回 None）。"""
    meta = KIND_META.get(_text(kind, 24))
    if not meta:
        return None
    now_v = _now(now)
    kind_s = _text(kind, 24)
    return {
        "kind": kind_s,
        "text": meta["text"],
        "hint": meta["hint"],
        "weight": round(_clamp(weight, 0.0, 1.2, 1.0), 3),
        "created": now_v,
        "expires": now_v + _f(TTL.get(kind_s), 3 * 3600.0),
        "pending": True,
        "attempts": 0,
        "mood": "",
        "source": _text(source, 12),
    }


def spawn(state: Any, thoughts: Optional[List], now: Optional[float] = None,
          roll: float = 1.0, prob: Optional[float] = None) -> Optional[Dict]:
    """评估一次生成：先掷 spawn 门，再按触发强度加权抽一类。

    roll 由内核用确定性种子算（可回放）；测试直接传 0.0/1.0 即可。
    不修改 thoughts，返回新念头（或 None），由调用方 append。
    """
    now_v = _now(now, state)
    pool = [t for t in (thoughts or []) if isinstance(t, dict)]
    if len(active(pool, now_v)) >= POOL_MAX:
        return None
    p = SPAWN_PROB if prob is None else _clamp01(prob)
    if _f(roll, 1.0) >= p:
        return None
    taken = set(active_kinds(pool, now_v))
    cands: List[Tuple[str, float]] = []
    for kind in KIND_NAMES:
        if kind in taken:  # 同类未满足的念头只留一条（fish_heart exclude 同款）
            continue
        w = trigger(kind, state, now_v)
        if w >= MIN_WEIGHT:
            cands.append((kind, w))
    if not cands:
        return None
    total = sum(w for _k, w in cands)
    pick = _rand01(int(now_v) + 17) * total
    acc = 0.0
    chosen = cands[-1]
    for kind, w in cands:
        acc += w
        if pick <= acc:
            chosen = (kind, w)
            break
    return new_thought(chosen[0], now_v, weight=min(1.2, chosen[1]))


def note_attempt(thoughts: Optional[List], now: Optional[float] = None) -> str:
    """主动开口记一次尝试；连续 3 次没人应 → 放弃该念头。返回被放弃的 kind（或 ""）。"""
    now_v = _now(now)
    pool = active(thoughts, now_v)
    if not pool:
        return ""
    target = max(pool, key=lambda t: _f(t.get("weight"), 1.0))
    target["attempts"] = int(_clamp(target.get("attempts"), 0, 99, 0)) + 1
    if int(target["attempts"]) >= ATTEMPT_GIVEUP:
        target["pending"] = False
        target["mood"] = "算了，先不想了"
        target["expires"] = now_v + GIVEUP_KEEP_S
        return str(target.get("kind") or "")
    return ""


def match_kind(text: Any) -> Optional[str]:
    """从用户一句话反推"喂饱了哪条念头"（先查否定豁免，再按优先级匹配）。"""
    t = _text(text, 120)
    if not t:
        return None
    for neg in NEGATION_PHRASES:
        if neg in t:
            return None
    for kind, kws in KEYWORD_KINDS:
        if any(k in t for k in kws):
            return kind
    return None


def _satisfy(thought: Dict, now: float, mood: str) -> None:
    thought["pending"] = False
    thought["mood"] = _text(mood, 40) or "心里踏实了"
    thought["settled_ts"] = now
    thought["expires"] = now + SATISFIED_KEEP_S


def settle(thoughts: Optional[List], event_kind: str, text: str = "",
           now: Optional[float] = None, state: Any = None) -> Optional[Dict]:
    """一条用户侧事件最多喂饱一个念头（就地更新池，返回奖励载荷或 None）。

    匹配顺序（同 fish_heart 的"文本优先、事件兜底、半满足"）：
      1) 用户原话关键词 → 对应念头；
      2) 事件类型 → 能喂的念头里挑 weight 最高的；
      3) 她开口后他回了（之前尝试过的念头）→ 算半个满足，奖励 ×0.5。
    """
    kind = _text(event_kind, 24)
    if kind not in EVENT_FEEDS:
        return None
    now_v = _now(now, state)
    cands = active(thoughts, now_v)
    if not cands:
        return None
    chosen: Optional[Dict] = None
    soft = False
    by_text = match_kind(text)
    if by_text:
        chosen = next((t for t in cands if t.get("kind") == by_text), None)
    if chosen is None:
        feedable = EVENT_FEEDS.get(kind, ())  # 事件 → 能喂的念头 kind 集合
        hits = [t for t in cands if str(t.get("kind")) in feedable]
        if hits:
            chosen = max(hits, key=lambda t: _f(t.get("weight"), 1.0))
    if chosen is None and kind in REPLY_KINDS:
        tried = [t for t in cands if int(_f(t.get("attempts"), 0.0)) >= 1]
        if tried:
            chosen = max(tried, key=lambda t: _f(t.get("weight"), 1.0))
            soft = True
    if chosen is None:
        return None
    kind_s = str(chosen.get("kind") or "")
    meta = KIND_META.get(kind_s) or {}
    _satisfy(chosen, now_v, meta.get("done", ""))
    reward = dict(REWARD_FEED.get(kind_s) or {})
    if soft and reward:
        reward["intensity"] = round(_clamp(reward.get("intensity"), 0.0, 1.0, 0.3) * 0.5, 3)
    return {
        "kind": kind_s,
        "text": meta.get("text", ""),
        "mood": meta.get("done", ""),
        "reward": reward,
        "soft": soft,
    }


def auto_complete(thoughts: Optional[List], state: Any,
                  now: Optional[float] = None) -> List[Dict]:
    """有些念头不是"他做了某事"才满足的，而是状态自己缓过来（fish_heart 363-370）。"""
    now_v = _now(now, state)
    g = signals(state, now_v)
    out: List[Dict] = []
    for thought in active(thoughts, now_v):
        kind = str(thought.get("kind") or "")
        mood = ""
        if kind == "quiet" and g["fatigue"] <= 0.40 and g["stress"] <= 0.35:
            mood = "安静了会儿，缓过来了"
        elif kind == "dwell" and g["last_user_ts"] > _f(thought.get("created"), 0.0):
            mood = "他回我了，心里踏实了"
        if mood:
            _satisfy(thought, now_v, mood)
            out.append({"kind": kind, "mood": mood})
    return out


def expire(thoughts: Optional[List], now: Optional[float] = None
           ) -> Tuple[List[Dict], List[Dict]]:
    """过期处理：返回 (保留的, 凉掉的未满足念头)。已满足的到期直接删（不返回）。"""
    now_v = _now(now)
    kept: List[Dict] = []
    faded: List[Dict] = []
    for item in thoughts or []:
        if not isinstance(item, dict):
            continue
        if _f(item.get("expires"), 0.0) > now_v:
            kept.append(item)
        elif bool(item.get("pending", True)):
            faded.append(item)
        # 已满足且到期 → 静默删掉
    return kept, faded


def release_effect(thought: Any) -> Dict:
    """凉掉释放：只返回"该记一笔什么"，不改任何情绪动力学（我们的红线）。"""
    kind = _text(_dict(thought).get("kind"), 24)
    return {"kind": kind, "text": RELEASE_TEXT.get(kind, "有个念头慢慢淡了")}


# ---------------------------------------------------------------- 对外消费 / 摘要


def pending_kind(thoughts: Optional[List], now: Optional[float] = None) -> str:
    pool = active(thoughts, now)
    if not pool:
        return ""
    best = max(pool, key=lambda t: _f(t.get("weight"), 1.0))
    return str(best.get("kind") or "")


def speak_bias(thoughts: Optional[List], now: Optional[float] = None) -> float:
    """当前念头让 gate 更想开口还是更不想（-1~1；没念头 = 0）。"""
    kind = pending_kind(thoughts, now)
    if not kind:
        return 0.0
    return _clamp(SPEAK_BIAS.get(kind, 1.0), -1.0, 1.0, 0.0)


def pending_hint(thoughts: Optional[List], now: Optional[float] = None) -> str:
    pool = active(thoughts, now)
    if not pool:
        return ""
    best = max(pool, key=lambda t: _f(t.get("weight"), 1.0))
    kind = str(best.get("kind") or "")
    meta = KIND_META.get(kind) or {}
    return str(meta.get("hint") or best.get("hint") or "")


def inject_line(thoughts: Optional[List], now: Optional[float] = None,
                limit: int = 40) -> str:
    """给注入用的一行（≤limit 字）："心里惦记着：……"。没有念头返回 ""。"""
    hint = pending_hint(thoughts, now)
    if not hint:
        return ""
    line = "心里惦记着：%s。" % hint
    if len(line) > limit:
        line = line[: max(0, limit - 1)] + "…"
    return line


def summary(source: Any, now: Optional[float] = None) -> Dict:
    """面板/日志用摘要。source 可以是池（list）也可以是 state（dict，兼容老调用）。"""
    if isinstance(source, list):
        pool: List[Dict] = source
        now_v = _now(now)
    else:
        pool = source.get("thoughts") if isinstance(source, dict) else []
        pool = pool if isinstance(pool, list) else []
        now_v = _now(now, source)
    pend = sorted(active(pool, now_v), key=lambda t: -_f(t.get("weight"), 1.0))
    sat = [t for t in live(pool, now_v) if not bool(t.get("pending", True))]
    return {
        "count": len(pend),
        "satisfied": len(sat),
        "bias": round(speak_bias(pool, now_v), 3),
        "hint": pending_hint(pool, now_v),
        "line": inject_line(pool, now_v),
        "pending": [{
            "kind": str(t.get("kind") or ""),
            "label": (KIND_META.get(str(t.get("kind"))) or {}).get("label", ""),
            "text": str(t.get("text") or ""),
            "weight": round(_f(t.get("weight"), 1.0), 3),
            "age_min": round(max(0.0, now_v - _f(t.get("created"), now_v)) / 60.0, 1),
            "left_min": round(max(0.0, _f(t.get("expires"), now_v) - now_v) / 60.0, 1),
            "attempts": int(_clamp(t.get("attempts"), 0, 99, 0)),
        } for t in pend[:POOL_MAX]],
        "satisfied_items": [{
            "kind": str(t.get("kind") or ""),
            "mood": str(t.get("mood") or ""),
        } for t in sat[:3]],
    }


# ---------------------------------------------------------------- 持久化（独立文件）


def _sanitize(item: Any, now: float) -> Optional[Dict]:
    """把磁盘上的一条念头收敛成合法值（kind 权威来自 KIND_META，坏字段丢弃）。"""
    if not isinstance(item, dict):
        return None
    kind = _text(item.get("kind"), 24)
    meta = KIND_META.get(kind)
    if not meta:
        return None
    ttl = _f(TTL.get(kind), 3 * 3600.0)
    created = _f(item.get("created"), 0.0)
    expires = _f(item.get("expires"), 0.0)
    if created <= 0.0:
        # 缺 created：能反推就反推（这样"过期条目"仍会被判过期），反推不了才当刚生成
        created = (expires - ttl) if expires > 0.0 else now
    if expires <= 0.0 or expires <= created:
        expires = created + ttl
    pending = bool(item.get("pending", True))
    return {
        "kind": kind,
        "text": meta["text"],
        "hint": meta["hint"],
        "weight": round(_clamp(item.get("weight"), 0.0, 1.2, 1.0), 3),
        "created": created,
        "expires": expires,
        "pending": pending,
        "attempts": int(_clamp(item.get("attempts"), 0, 99, 0)),
        "mood": _text(item.get("mood"), 40) if not pending else "",
        "source": _text(item.get("source"), 12) or "spawn",
    }


def load(path: str, now: Optional[float] = None) -> Tuple[List[Dict], Dict]:
    """读独立念头文件。返回 (池, meta)。文件不存在/损坏 → ([], {})，绝不抛。"""
    now_v = _now(now)
    raw: Any = None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return [], {}
    if not isinstance(raw, dict):
        return [], {}
    pool: List[Dict] = []
    for item in raw.get("thoughts") if isinstance(raw.get("thoughts"), list) else []:
        one = _sanitize(item, now_v)
        if one is not None and _f(one.get("expires"), 0.0) > now_v:
            pool.append(one)
        if len(pool) >= POOL_MAX:
            break
    meta_in = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
    meta = {
        "last_eval": _f(meta_in.get("last_eval"), 0.0),
        "comfort": _dict(meta_in.get("comfort")),
        "stance": _dict(meta_in.get("stance")),
    }
    return pool, meta


def save(path: str, thoughts: Optional[List], meta: Optional[Dict] = None) -> bool:
    """准原子写（tmp + os.replace）。失败返回 False，绝不抛。"""
    try:
        folder = os.path.dirname(os.path.abspath(path))
        if folder and not os.path.isdir(folder):
            os.makedirs(folder, exist_ok=True)
        now_v = time.time()
        items = [one for one in (_sanitize(t, now_v) for t in (thoughts or []))
                 if one is not None][:POOL_MAX]
        payload: Dict[str, Any] = {"ts": now_v, "thoughts": items}
        if isinstance(meta, dict):
            payload["meta"] = meta
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        return True
    except (OSError, ValueError, TypeError):
        return False