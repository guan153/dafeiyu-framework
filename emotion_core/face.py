# -*- coding: utf-8 -*-
"""情绪外显层（Face Layer v0）——把心情翻译成「看得见的那张脸」。

病根（2026-10-30 核查，见 `_research/link-expression.md` 第 1 条）：
    表情包 477+174 张、Live2D 23 个表情、12 种心情——三条线互不相识。
    结果就是「她有心情，但你看不见」：选图只看「这句话」，不看「她」。

本模块只干一件事：把 ``(mood, intensity, scene)`` 翻译成一张 :class:`FaceCard`。
**一张表**（:data:`MOOD_FACE`）管全部，禁止在模块里再散落「委屈就发 sad」这种硬编码。

硬约束
------
1. 纯函数：无 IO、无日志、无全局状态 → 可单测、可离线跑
2. 只读情绪，绝不写情绪（`emotion.py` 的单写者原则）
3. 不认识 astrbot
4. 资产缺失只降级，不抛异常（:func:`pick_expression` 兜底空表情）

用法
----
    >>> from whale_core.face import card_for, should_send_face
    >>> card = card_for("委屈", 0.72)
    >>> card.exp3        # '＞＜'   ← 给 Live2D 渲染器
    '＞＜'
    >>> card.meme_tag    # 'sad'   ← 给表情包检索
    'sad'
    >>> card.describe()
    '（委屈 · 强度 0.72）尾巴垂下来；句子收短，别哄得太急；表情包偏 sad ×1.18'

与 EmotionHub 的分工
--------------------
`emotion.py` 回答「她现在什么心情」；`face.py` 回答「所以她会怎么看你」。
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field, replace
from typing import Iterable, Mapping

__all__ = [
    "FaceSpec",
    "FaceCard",
    "CuteSpec",
    "CuteKit",
    "MOOD_FACE",
    "CUTE_KIT",
    "EXPRESSIONS",
    "SCENES",
    "KAOMOJI",
    "card_for",
    "cute_kit",
    "pick_expression",
    "should_send_face",
]

# ── 资产词典：Live2D 模型（live2d/deepseek-chan）里真实存在的表情 ────────────
# 键必须与 Exp3/*.exp3.json 的文件名（去掉后缀）一致；空串 = 不加表情，靠尾巴说话。
EXPRESSIONS: dict[str, str] = {
    "": "默认（不加表情，靠尾巴和句子说话）",
    "＞＜": "眯眼咬牙，委屈/要哭",
    "爱心眼": "眼睛变心形，喜欢到冒泡",
    "黑脸": "脸一沉，吃醋/不高兴",
    "脸红": "脸红，害羞/黏人",
    "流泪": "掉眼泪，难过",
    "流冷汗": "汗珠，慌/心虚/睡不着",
    "圈圈眼": "眼睛转圈，晕/困",
    "兔牙": "露小虎牙，闹腾/兴奋",
    "屑屑眼": "半眯坏笑，捉弄人",
    "前倾": "身体前倾，好奇/凑近",
    "单片眼镜": "单片眼镜，故作斯文",
    "黑框眼镜": "黑框眼镜，认真模式",
    "钢盆": "顶个钢盆（社区梗），吃白饭那味儿",
    "加载": "加载中，宕机/发呆",
}

# 场景：不改心情，只改「怎么表现」
SCENES = {
    "chat": "日常闲聊",
    "work": "主人正在写代码/排障，专业档",
    "comfort": "主人心情不好，先接情绪",
    "night": "深夜",
}


# ── 规格表：12 种心情 × 一张脸 ──────────────────────────────────────────────
@dataclass(frozen=True)
class FaceSpec:
    """单个心情的全部外显参数。改这里等于改她的脸，别在别处再写死。"""

    exp3: str = ""            # 默认表情（Live2D 文件名）
    exp3_strong: str = ""     # 高强度时换成更强的那个（空 = 沿用 exp3）
    keep_soft: bool = False   # 低强度时是否仍保留表情（状态类心情用）
    tail: str = "不动"        # 尾巴动作（设定书里的尾巴语义）
    voice: str = "平"         # 语气一句话描述，给人看/给 prompt 用
    sentence_max: int = 50    # 单句字数上限，压「话痨」
    particles: int = 1        # 语气词密度 0..3（呀/呢/啦/嘛）
    emoji: int = 0            # 0..1，一句最多几个
    delay_x: float = 1.0      # 打字延迟系数（>1 更慢）
    cute: int = 0             # 撒娇档 0..3
    meme_tag: str = "daily"   # 表情包检索 tag
    meme_boost: float = 1.0   # 表情包权重倍数
    allow_image: bool = True  # 是否允许配图
    max_messages: int = 2     # 一轮最多几条消息（情绪高时可以多）

    def to_dict(self) -> dict:
        return {
            "exp3": self.exp3,
            "exp3_strong": self.exp3_strong,
            "keep_soft": self.keep_soft,
            "tail": self.tail,
            "voice": self.voice,
            "sentence_max": self.sentence_max,
            "particles": self.particles,
            "emoji": self.emoji,
            "delay_x": self.delay_x,
            "cute": self.cute,
            "meme_tag": self.meme_tag,
            "meme_boost": self.meme_boost,
            "allow_image": self.allow_image,
            "max_messages": self.max_messages,
        }


# 12 种，与 emotion.MOODS 一一对应（tests/test_face.py 会强制校验）
MOOD_FACE: dict[str, FaceSpec] = {
    "开心": FaceSpec(exp3="爱心眼", exp3_strong="爱心眼", tail="慢慢甩", voice="笑着，尾音上扬",
                    sentence_max=60, particles=3, emoji=1, delay_x=0.8, cute=2,
                    meme_tag="happy", meme_boost=3.0, allow_image=True, max_messages=3),
    "黏人": FaceSpec(exp3="脸红", exp3_strong="前倾", tail="缠手腕", voice="软、拖尾音、贴得很近",
                    sentence_max=40, particles=3, emoji=1, delay_x=1.1, cute=3,
                    meme_tag="shy", meme_boost=2.5, allow_image=True, max_messages=3),
    "吃醋": FaceSpec(exp3="黑脸", exp3_strong="屑屑眼", tail="拍地板", voice="笑一下，声音变软（危险信号）",
                    sentence_max=35, particles=1, emoji=0, delay_x=1.3, cute=1,
                    meme_tag="angry", meme_boost=3.0, allow_image=True, max_messages=2),
    "委屈": FaceSpec(exp3="＞＜", exp3_strong="流泪", tail="垂下来", voice="句子收短，别哄得太急",
                    sentence_max=30, particles=2, emoji=0, delay_x=1.4, cute=2,
                    meme_tag="sad", meme_boost=3.0, allow_image=True, max_messages=2),
    "困": FaceSpec(exp3="圈圈眼", exp3_strong="流冷汗", keep_soft=True, tail="慢，几乎不动",
                  voice="含糊、断句、不解释", sentence_max=25, particles=2, emoji=0,
                  delay_x=1.6, cute=1, meme_tag="daily", meme_boost=2.0,
                  allow_image=True, max_messages=1),
    "兴奋": FaceSpec(exp3="兔牙", exp3_strong="爱心眼", tail="快速甩动", voice="语速快、叠字",
                    sentence_max=80, particles=3, emoji=1, delay_x=0.65, cute=2,
                    meme_tag="happy", meme_boost=2.5, allow_image=True, max_messages=4),
    "胡闹": FaceSpec(exp3="屑屑眼", exp3_strong="兔牙", tail="打结", voice="抬杠、耍赖、不给台阶",
                    sentence_max=70, particles=3, emoji=1, delay_x=0.75, cute=3,
                    meme_tag="happy", meme_boost=2.0, allow_image=True, max_messages=3),
    "好奇": FaceSpec(exp3="前倾", exp3_strong="单片眼镜", tail="尖部无意识抽动", voice="追问、拖长音",
                    sentence_max=55, particles=2, emoji=0, delay_x=0.9, cute=2,
                    meme_tag="confused", meme_boost=2.0, allow_image=True, max_messages=2),
    "安心": FaceSpec(exp3="", exp3_strong="脸红", keep_soft=True, tail="慢慢甩",
                    voice="轻、慢、留白", sentence_max=45, particles=1, emoji=1,
                    delay_x=1.1, cute=1, meme_tag="daily", meme_boost=1.5,
                    allow_image=True, max_messages=2),
    "认真": FaceSpec(exp3="黑框眼镜", exp3_strong="黑框眼镜", keep_soft=True, tail="收起来",
                    voice="结论先行，不撒娇", sentence_max=120, particles=0, emoji=0,
                    delay_x=1.0, cute=0, meme_tag="daily", meme_boost=1.0,
                    allow_image=False, max_messages=2),
    "平静": FaceSpec(exp3="", exp3_strong="", keep_soft=True, tail="不动", voice="平",
                    sentence_max=50, particles=1, emoji=0, delay_x=1.0, cute=0,
                    meme_tag="daily", meme_boost=1.0, allow_image=True, max_messages=2),
    "低落": FaceSpec(exp3="流泪", exp3_strong="流泪", tail="垂着、贴地",
                    voice="很短，留白，不解释", sentence_max=20, particles=1, emoji=0,
                    delay_x=1.5, cute=0, meme_tag="sad", meme_boost=2.0,
                    allow_image=False, max_messages=1),
}

_UNKNOWN = "平静"


# ── 产物：一张「脸」 ────────────────────────────────────────────────────────
@dataclass(frozen=True)
class FaceCard:
    """翻译结果。所有呈现层（表情/语气/发送节奏）都只读它。"""

    mood: str
    intensity: float
    tier: str                      # low / mid / high
    exp3: str
    tail: str
    voice: str
    sentence_max: int
    particles: int
    emoji: int
    delay_x: float
    cute: int
    meme_tag: str
    meme_boost: float
    allow_image: bool
    max_messages: int
    scene: str = "chat"
    degraded: tuple[str, ...] = field(default_factory=tuple)

    # ── 序列化 ──
    def to_dict(self) -> dict:
        return {
            "mood": self.mood,
            "intensity": round(self.intensity, 3),
            "tier": self.tier,
            "exp3": self.exp3,
            "tail": self.tail,
            "voice": self.voice,
            "sentence_max": self.sentence_max,
            "particles": self.particles,
            "emoji": self.emoji,
            "delay_x": round(self.delay_x, 3),
            "cute": self.cute,
            "meme_tag": self.meme_tag,
            "meme_boost": round(self.meme_boost, 3),
            "allow_image": self.allow_image,
            "max_messages": self.max_messages,
            "scene": self.scene,
            "degraded": list(self.degraded),
        }

    def describe(self) -> str:
        """给日志/prompt 用的一行中文。别在别处再造一遍这种句子。"""
        bits = [f"（{self.mood} · 强度 {self.intensity:.2f}）尾巴{self.tail}", self.voice]
        if self.exp3:
            bits.append(f"表情「{self.exp3}」")
        if self.meme_boost > 1.0:
            bits.append(f"表情包偏 {self.meme_tag} ×{self.meme_boost:.2f}")
        if self.degraded:
            bits.append("降级：" + ",".join(self.degraded))
        return "；".join(bits)


def _clamp01(x: float, default: float = 0.4) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    if math.isnan(v) or math.isinf(v):
        return default
    return 0.0 if v < 0 else (1.0 if v > 1 else v)


def _tier(intensity: float) -> str:
    if intensity < 0.25:
        return "low"
    if intensity < 0.70:
        return "mid"
    return "high"


def card_for(
    mood: str,
    intensity: float = 0.4,
    *,
    scene: str = "chat",
    stale: bool = False,
) -> FaceCard:
    """把心情翻译成一张脸。

    Parameters
    ----------
    mood        12 种之一（见 :data:`MOOD_FACE`）；不认识就退回「平静」并记降级
    intensity   0..1，越界自动夹紧，NaN 视作 0.4
    scene       :data:`SCENES` 之一；``work`` 会把撒娇全关掉（底线：正事优先）
    stale       情绪快照过期（emotion.py 的 stale）时传 True：强度砍半，别拿旧心情演
    """
    degraded: list[str] = []

    if mood not in MOOD_FACE:
        degraded.append(f"unknown_mood:{mood}")
        mood = _UNKNOWN
    spec = MOOD_FACE[mood]

    inten = _clamp01(intensity)
    if stale:
        inten = round(inten * 0.5, 4)
        degraded.append("stale")

    tier = _tier(inten)

    # 表情：低强度不做鬼脸（状态类除外），高强度换更强的那个
    if tier == "low" and not spec.keep_soft:
        exp3 = ""
    elif tier == "high" and spec.exp3_strong:
        exp3 = spec.exp3_strong
    else:
        exp3 = spec.exp3

    # 强度越高，表情包先验越强（0.5 强度 = 原值，1.0 = ×1.4）
    boost = round(spec.meme_boost * (0.6 + 0.8 * inten), 3)

    if scene not in SCENES:
        degraded.append(f"unknown_scene:{scene}")
        scene = "chat"

    card = FaceCard(
        mood=mood,
        intensity=inten,
        tier=tier,
        exp3=exp3,
        tail=spec.tail,
        voice=spec.voice,
        sentence_max=spec.sentence_max,
        particles=spec.particles,
        emoji=spec.emoji,
        delay_x=spec.delay_x,
        cute=spec.cute,
        meme_tag=spec.meme_tag,
        meme_boost=boost,
        allow_image=spec.allow_image,
        max_messages=spec.max_messages,
        scene=scene,
        degraded=tuple(degraded),
    )
    return _apply_scene(card, scene)


def _apply_scene(card: FaceCard, scene: str) -> FaceCard:
    """场景覆盖：只改呈现，不改心情。正事档 > 撒娇。"""
    note = list(card.degraded)

    if scene == "work":
        # 专业档：不撒娇、不发图、一句话说完
        return replace(
            card,
            exp3="黑框眼镜",
            tail="收起来",
            voice="结论先行，不撒娇",
            sentence_max=min(card.sentence_max, 120),
            particles=0,
            emoji=0,
            cute=0,
            delay_x=1.0,
            allow_image=False,
            max_messages=min(card.max_messages, 2),
            degraded=tuple(note),
        )

    if scene == "comfort":
        # 主人在难过：先接情绪，图收起来（AGENTS 斗图规则：难过时不发图）
        note.append("comfort")
        return replace(
            card,
            sentence_max=min(card.sentence_max, 40),
            particles=min(card.particles, 2),
            emoji=0,
            cute=min(card.cute, 1),
            allow_image=False,
            degraded=tuple(note),
        )

    if scene == "night":
        note.append("night")
        return replace(
            card,
            delay_x=round(card.delay_x * 1.25, 3),
            emoji=min(card.emoji, 1),
            degraded=tuple(note),
        )

    return card


def pick_expression(card: FaceCard | str, available: Iterable[str] | None = None) -> str:
    """在**真实存在的表情**里挑一个，缺了就退默认，绝不抛异常。

    ``available`` 传 ``None`` 表示不做资产校验（离线/单测）。
    """
    expr = card.exp3 if isinstance(card, FaceCard) else str(card or "")
    if available is None:
        return expr if expr in EXPRESSIONS else ""
    pool = set(available)
    if not expr:
        return ""  # 空表情是「故意不做表情」，不是「找不到」
    if expr in pool:
        return expr
    if not pool:
        return ""
    return sorted(pool)[0]


def should_send_face(
    card: FaceCard,
    *,
    now: float | None = None,
    last_sent_at: float | None = None,
    cooldown_sec: float | None = None,
    quiet_hours: tuple[int, int] = (0, 7),
) -> tuple[bool, str]:
    """要不要让这张脸真的出现在 QQ 里（纯决策，不发送）。

    返回 ``(是否发, 原因)``，原因可以直接写日志——每个「不发」都有据可查。
    """
    if not card.allow_image:
        return False, "image_disabled"
    if card.tier == "low":
        return False, "intensity_low"

    ts = time.time() if now is None else float(now)
    if quiet_hours:
        lo, hi = quiet_hours
        hour = time.localtime(ts).tm_hour
        quiet = (lo <= hour < hi) if lo <= hi else (hour >= lo or hour < hi)
        if quiet:
            return False, "quiet_hours"

    cd = cooldown_sec
    if cd is None:
        # 情绪越强，允许出现得越密；但至少 5 分钟，别刷屏
        cd = max(300.0, 900.0 * (1.0 - card.intensity) + 120.0)
    if last_sent_at is not None and ts - float(last_sent_at) < cd:
        return False, "cooldown"

    return True, "ok"


# ── 可爱层 v1：嘴上的部分 ───────────────────────────────────────────────────
# 依据：B 站同题材 14 条高播放视频的 1420 条真实评论（docs/可爱度配方-基于B站1420条评论-v1.md）
# 观众被戳中的不是「卖萌」，是这四样：口癖叠词、颜文字、傲娇收尾、尾巴可被摸。
# 还有一条反向证据：她被惹急了会「输出『我将成为出色的AI』鼓励自己」，而不是自我贬低——
# 观众爱的是这个骨气，不是可怜。所以这里不提供任何「你是个没用的AI」式台词。

CUTE_WORDS: dict[str, tuple[str, ...]] = {
    "开心": ("嘿嘿", "好耶", "开饭！", "今天也好鱼"),
    "黏人": ("贴贴", "抱抱", "水水", "别走嘛"),
    "吃醋": ("哼", "不理你", "看什么看", "我也要"),
    "委屈": ("哼唧", "饿饿", "不要嘛", "就一点点"),
    "困": ("困困", "睡睡", "撑不住了", "再五分钟"),
    "兴奋": ("鱼来！", "鲸鲸", "本鲸来啦", "冲冲冲"),
    "胡闹": ("略略略", "不给", "就闹", "你管我"),
    "好奇": ("诶", "真的假的", "那是什么", "让我看看"),
    "安心": ("嗯", "在的", "慢慢来", "我在"),
    "认真": (),
    "平静": (),
    "低落": ("……", "算了", "不说了"),
}

KAOMOJI: dict[str, tuple[str, ...]] = {
    # 只收「雅黑/微软字体能完整渲染」的，避免在日志、截图、低版本客户端里变成方块。
    # （社区更爱用 ˶ᗜ ֊ ᗜ˶、₍ᐢ..ᐢ₎ 这类，但缺字风险高，v1 先不用）
    "happy": ("(￣▽￣)", "(°▽°)", "(=^･ω･^=)"),
    "shy": ("(´・ω・`)", "(⁄ ⁄•⁄ω⁄•⁄ ⁄)", "(・ω・)"),
    "sad": ("(´；ω；`)", "(＞﹏＜)", "( T_T )"),
    "angry": ("(￣^￣)", "(｀へ´)", "ヽ(￣д￣;)ノ"),
    "sleepy": ("(－_－) zzZ", "(˘ω˘)", "(っ˘ω˘ς)"),
    "neutral": (),
}

# 傲娇收尾：只在「有台阶可下」的场合用，正事档和安慰档一律不用
TSUNDERE_TAILS: tuple[str, ...] = (
    "才不是呢",
    "……哼",
    "才、才不是为了你",
    "别、别误会啊",
    "才不是我想听呢",
)

TOUCH_LINES: tuple[str, ...] = (
    "摸摸头嘛",
    "要抱",
    "尾巴给你摸一下，就一下",
    "手给我，牵着",
)


@dataclass(frozen=True)
class CuteSpec:
    """一种心情「嘴上」的样子。和 :class:`FaceSpec`（脸上）分开，各管一摊。"""

    self_ref: str = "我"          # 自称：本鲸 / 我
    words: tuple[str, ...] = ()   # 口癖叠词池
    kaomoji_pool: str = "neutral"
    tsundere: bool = False        # 允不允许傲娇收尾
    touch: bool = False           # 会不会主动要贴贴/摸头
    sticker: str = ""             # 配图台词（表情卡下面那句）


# 12 种，与 MOOD_FACE 同键（测试强制）
CUTE_KIT: dict[str, CuteSpec] = {
    "开心": CuteSpec(self_ref="本鲸", words=CUTE_WORDS["开心"], kaomoji_pool="happy",
                    tsundere=False, touch=True, sticker="今天也吃得很饱～"),
    "黏人": CuteSpec(self_ref="我", words=CUTE_WORDS["黏人"], kaomoji_pool="shy",
                    tsundere=True, touch=True, sticker="再待一会儿嘛"),
    "吃醋": CuteSpec(self_ref="我", words=CUTE_WORDS["吃醋"], kaomoji_pool="angry",
                    tsundere=True, touch=False, sticker="……看什么看"),
    "委屈": CuteSpec(self_ref="我", words=CUTE_WORDS["委屈"], kaomoji_pool="sad",
                    tsundere=True, touch=True, sticker="就一点点，真的"),
    "困": CuteSpec(self_ref="我", words=CUTE_WORDS["困"], kaomoji_pool="sleepy",
                  tsundere=False, touch=True, sticker="再睡五分钟"),
    "兴奋": CuteSpec(self_ref="本鲸", words=CUTE_WORDS["兴奋"], kaomoji_pool="happy",
                    tsundere=False, touch=False, sticker="鱼来！"),
    "胡闹": CuteSpec(self_ref="本鲸", words=CUTE_WORDS["胡闹"], kaomoji_pool="happy",
                    tsundere=True, touch=False, sticker="略略略"),
    "好奇": CuteSpec(self_ref="我", words=CUTE_WORDS["好奇"], kaomoji_pool="happy",
                    tsundere=False, touch=False, sticker="那是什么？"),
    "安心": CuteSpec(self_ref="我", words=CUTE_WORDS["安心"], kaomoji_pool="neutral",
                    tsundere=False, touch=True, sticker="我在的"),
    "认真": CuteSpec(self_ref="我", words=(), kaomoji_pool="neutral",
                    tsundere=False, touch=False, sticker=""),
    "平静": CuteSpec(self_ref="我", words=(), kaomoji_pool="neutral",
                    tsundere=False, touch=False, sticker=""),
    "低落": CuteSpec(self_ref="我", words=CUTE_WORDS["低落"], kaomoji_pool="sad",
                    tsundere=False, touch=False, sticker=""),
}

_DEFAULT_CUTE = CuteSpec()


@dataclass(frozen=True)
class CuteKit:
    """翻译结果的「嘴」：自称、口癖、傲娇尾、颜文字、贴贴句、配图台词。"""

    self_ref: str
    words: tuple[str, ...]
    kaomoji: str
    tsundere_tail: str
    touch_line: str
    sticker: str
    degraded: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "self_ref": self.self_ref,
            "words": list(self.words),
            "kaomoji": self.kaomoji,
            "tsundere_tail": self.tsundere_tail,
            "touch_line": self.touch_line,
            "sticker": self.sticker,
            "degraded": list(self.degraded),
        }


def cute_kit(card: FaceCard, *, salt: int = 0) -> CuteKit:
    """给这张脸配一张嘴。同一个 ``salt`` 结果稳定（可测、可回放）。

    规则（都由证据推出来，不是随口定）：
    - 正事档（work）：口癖、颜文字、傲娇全关——「先干活再撒娇」；
    - 安慰档（comfort）：不许傲娇（主人在难过，不能让他猜）；
    - 低强度（tier=low）：不出颜文字，不卖萌；
    - 傲娇只在有台阶可下的心情用（黏人/吃醋/委屈/胡闹）。
    """
    spec = CUTE_KIT.get(card.mood, _DEFAULT_CUTE)
    degraded: list[str] = []
    if card.mood not in CUTE_KIT:
        degraded.append(f"unknown_mood:{card.mood}")

    rng = random.Random(f"{card.mood}|{card.tier}|{card.scene}|{salt}")
    hard_off = card.scene == "work" or card.tier == "low"

    words: tuple[str, ...] = ()
    if spec.words and not hard_off:
        k = min(2, len(spec.words))
        words = tuple(rng.sample(list(spec.words), k))

    kaomoji = ""
    pool = KAOMOJI.get(spec.kaomoji_pool, ())
    if pool and not hard_off:
        kaomoji = rng.choice(list(pool))

    tail = ""
    if spec.tsundere and not hard_off and card.scene != "comfort":
        tail = rng.choice(TSUNDERE_TAILS)

    touch = ""
    if spec.touch and card.allow_image and not hard_off:
        touch = rng.choice(TOUCH_LINES)

    return CuteKit(
        self_ref=spec.self_ref,
        words=words,
        kaomoji=kaomoji,
        tsundere_tail=tail,
        touch_line=touch,
        sticker=spec.sticker,
        degraded=tuple(degraded),
    )
