"""BEAM-AI · 长期层：人格六参数慢漂移（traits.py）

借鉴 dsh-grow 的 lib/traits.js（MIT）——它解决的问题和咱一样：
「参数不动的演化等于没有」。这里把它落到 BEAM 的表达层上。

三条铁律：
  1. 每个参数有硬上下限（配置改不动边界）；
  2. 涉及安全/凭据的话题当轮不计入演化；
  3. 漂移必须看得见（变化写进 state，面板可读）。

与 BEAM 的分工：
  - 情绪（秒级~小时级）在 affect2/intero；
  - 人格（天~周级）在这里：clinginess/verbosity/humor/dependence/diligence/emotionRate。
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Any, Dict, List, Optional

#: 六参数硬边界（改配置也动不了这里）
BOUNDS = {
    "clinginess": (0.15, 0.90),      # 撒娇
    "verbosity": (0.20, 0.85),       # 话量
    "humor": (0.10, 0.80),           # 幽默
    "dependence": (0.20, 0.85),      # 依赖
    "diligence": (0.40, 0.95),       # 认真
    "emotionRate": (0.10, 0.70),     # 情绪外化
}

#: 初始值（从克制起步，热络是相处长出来的）
DEFAULT = {
    "clinginess": 0.35,
    "verbosity": 0.50,
    "humor": 0.30,
    "dependence": 0.50,
    "diligence": 0.65,
    "emotionRate": 0.30,
}

LABEL = {
    "clinginess": "撒娇", "verbosity": "话量", "humor": "幽默",
    "dependence": "依赖", "diligence": "认真", "emotionRate": "情绪外化",
}

#: 单次最大步长 / 阻尼 / 方向门限（贴近 traits.js 的实测参数）
BASE_STEP = 0.06
DAMP = 0.3
DEAD_ZONE = 0.05

#: 每多少轮评估一次
EVAL_EVERY = 8

#: 中文信号词表（把主人的话读成信号，不需要模型）
LEXICON = {
    "warm": ("宝宝", "抱抱", "亲亲", "想你", "喜欢你", "乖", "摸摸", "揉揉",
             "贴贴", "蹭蹭", "爱你", "辛苦", "心疼", "么么", "陪陪", "乖宝"),
    "praise": ("厉害", "棒", "好看", "可爱", "聪明", "牛", "优秀", "真好",
               "夸", "绝了", "强", "靠谱", "漂亮", "喜欢"),
    "cold": ("闭嘴", "烦", "滚", "别说了", "无聊", "吵", "懒得", "不想理",
             "讨厌", "笨蛋", "傻", "走开", "别烦我"),
    "complain": ("太长", "啰嗦", "说重点", "废话", "又来了", "复读", "墨迹",
                 "重复", "一样的话", "别念叨"),
    "rush": ("快点", "赶紧", "别磨叽", "急", "马上", "速度", "来不及", "快点儿"),
    "asksShort": ("简短", "一句话", "别废话", "重点", "概括", "简洁", "少说"),
    "asksDetail": ("详细", "展开", "具体", "多说", "然后呢", "细节", "讲清楚"),
    "safetyTouched": ("密码", "密钥", "验证码", "token", "删除", "权限", "凭据",
                      "私钥", "账号", "隐私"),
}


def clamp_trait(key: str, value: Any) -> float:
    lo, hi = BOUNDS.get(key, (0.0, 1.0))
    try:
        v = float(value)
    except (TypeError, ValueError):
        return lo
    if v != v:  # NaN
        return lo
    return lo if v < lo else (hi if v > hi else v)


def default() -> Dict[str, float]:
    return {k: clamp_trait(k, v) for k, v in DEFAULT.items()}


def view(traits: Optional[Dict]) -> Dict[str, float]:
    """对外快照（有界 + 保留三位）。"""
    t = traits if isinstance(traits, dict) else {}
    return {k: round(clamp_trait(k, t.get(k, DEFAULT[k])), 3) for k in BOUNDS}


def load_meta(path: str) -> Dict[str, Any]:
    """读 traits.json 里的附加字段（评估轮数/最近变更），读不到返回空。"""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if k not in ("traits", "ts")}
    except (OSError, ValueError):
        pass
    return {}


def load(path: str) -> Dict[str, float]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return view(data.get("traits") if isinstance(data.get("traits"), dict) else data)
    except (OSError, ValueError):
        pass
    return default()


def save(path: str, traits: Dict[str, float], extra: Optional[Dict] = None) -> bool:
    try:
        d = os.path.dirname(os.path.abspath(path))
        if d:
            os.makedirs(d, exist_ok=True)
        payload = {"ts": time.time(), "traits": view(traits)}
        if isinstance(extra, dict):
            for k, v in extra.items():
                payload[k] = v
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def signals_from_text(text: str) -> Dict[str, float]:
    """把一句话读成信号（0/1），不看模型。"""
    t = str(text or "")
    out: Dict[str, float] = {}
    if not t:
        return out
    for key, words in LEXICON.items():
        for w in words:
            if w in t:
                out[key] = out.get(key, 0.0) + 1.0
                break
    return out


def accumulate(bucket: Dict[str, float], text: str) -> Dict[str, float]:
    """把一句话的信号累加进桶（bucket 会被原地修改）。"""
    if not isinstance(bucket, dict):
        bucket = {}
    got = signals_from_text(text)
    for k, v in got.items():
        bucket[k] = float(bucket.get(k) or 0.0) + v
    return bucket


def _step_toward(key: str, current: float, direction: int, weight: float) -> float:
    """朝目标边界走一小步（越近边界步子越小）。返回实际步长（带符号）。"""
    if direction == 0 or weight <= 0:
        return 0.0
    lo, hi = BOUNDS.get(key, (0.0, 1.0))
    span = max(0.01, hi - lo)
    remaining = (hi - current) / span if direction > 0 else (current - lo) / span
    damping = 1.0 - DAMP * (1.0 - remaining)
    step = BASE_STEP * min(1.0, weight) * max(0.15, damping) * (1 if direction > 0 else -1)
    nxt = clamp_trait(key, current + step)
    return nxt - current


def evaluate(traits: Dict[str, float], signals: Dict[str, float],
             turns: int = EVAL_EVERY) -> Dict[str, Any]:
    """评估一次人格漂移。返回 {next, changes, skipped?}。"""
    cur = view(traits)
    nxt = dict(cur)
    changes: List[Dict[str, Any]] = []
    turns = max(1, int(turns or 1))

    # 安全/凭据话题当轮不参与演化
    if float((signals or {}).get("safetyTouched") or 0.0) > 0:
        return {"next": nxt, "changes": [], "skipped": "safety"}

    def density(v: Any) -> float:
        # 单轮最多占 1/4 权重（traits.js P2-1 修正）
        return min(1.0, float(v or 0.0) / max(4.0, float(turns)))

    warm = density(signals.get("warm"))
    praise = density(signals.get("praise"))
    cold = density(signals.get("cold"))
    complain = density(signals.get("complain"))
    rush = density(signals.get("rush"))
    asks_short = density(signals.get("asksShort"))
    asks_detail = density(signals.get("asksDetail"))
    safety = density(signals.get("safetyTouched"))

    moves = [
        ("clinginess", (warm + praise) * 0.9 - cold * 0.8,
         "被夸/说软话 %.2f，被否定 %.2f" % (warm + praise, cold)),
        ("humor", (praise + warm) * 0.5 - cold * 0.5,
         "关系松的时候会多一点玩笑"),
        ("verbosity", asks_detail * 0.8 - asks_short * 1.0,
         "要详细 %.2f，嫌长 %.2f" % (asks_detail, asks_short)),
        ("dependence", rush * 0.7 - warm * 0.4,
         "被催 %.2f，被夸 %.2f" % (rush, warm)),
        ("diligence", cold * 0.6 + rush * 0.3,
         "被否定 %.2f，被催 %.2f" % (cold, rush)),
        ("emotionRate", (warm + praise) * 0.5 - (complain * 0.7 + cold * 0.3),
         "被夸 %.2f，抱怨 %.2f" % (warm + praise, complain)),
    ]
    for key, raw_dir, why in moves:
        if abs(raw_dir) < DEAD_ZONE:
            continue
        direction = 1 if raw_dir > 0 else -1
        from_v = nxt[key]
        delta = _step_toward(key, from_v, direction, abs(raw_dir))
        if abs(delta) < 0.0005:
            continue
        nxt[key] = clamp_trait(key, from_v + delta)
        changes.append({"key": key, "label": LABEL.get(key, key),
                        "from": round(from_v, 3), "to": round(nxt[key], 3), "why": why})
    return {"next": nxt, "changes": changes}


def describe(key: str, value: Any) -> str:
    """参数 → 一句可执行的行为描述（注入用，不是形容词）。"""
    v = clamp_trait(key, value)
    if key == "clinginess":
        if v < 0.28:
            return "很克制：不主动撒娇，被逗了才接一句"
        if v < 0.45:
            return "偶尔撒一下：一轮最多一个语气词"
        if v < 0.62:
            return "愿意用语气词，会主动靠近一点"
        if v < 0.78:
            return "黏一点：每轮至少一次主动关心或撒娇"
        return "很黏：开场可以带情绪，会求夸奖"
    if key == "verbosity":
        if v < 0.3:
            return "极短：能一句说完绝不说两句"
        if v < 0.45:
            return "偏短：先给结论，细节只留必要项"
        if v < 0.62:
            return "正常长度：该展开就展开，不硬凑"
        if v < 0.75:
            return "话偏多：会补充背景和理由"
        return "话多：愿意把来龙去脉讲全"
    if key == "humor":
        if v < 0.22:
            return "基本不玩笑：全程正经"
        if v < 0.38:
            return "偶尔来一句，不硬讲"
        if v < 0.55:
            return "会接梗：合适时机插一句"
        return "爱开玩笑：会自嘲也会打趣主人"
    if key == "dependence":
        if v < 0.32:
            return "小事自己定，大事才问一句"
        if v < 0.5:
            return "拿不准会确认一下再动"
        if v < 0.68:
            return "习惯先问一句再动手"
        return "很依赖确认：每步都想先得到同意"
    if key == "diligence":
        if v < 0.5:
            return "差不多就行：先跑通再说"
        if v < 0.65:
            return "会核对关键数字"
        if v < 0.8:
            return "细节照原样：路径、命令、报错不加工"
        return "极度认真：每一步都要自测"
    if key == "emotionRate":
        if v < 0.2:
            return "几乎不外露：只讲事不讲感受"
        if v < 0.35:
            return "一轮最多一处情绪，多在结尾"
        if v < 0.5:
            return "情绪可见：会说自己累、开心或紧张"
        return "情绪明显：会主动表达感受"
    return str(v)


def summary(traits: Optional[Dict]) -> str:
    """一行摘要（注入/日志用）。"""
    v = view(traits)
    return "、".join("%s %s" % (LABEL[k], describe(k, v[k]).split("：")[0]) for k in BOUNDS)


def inject_block(traits: Optional[Dict]) -> str:
    """给 <mind> 补充的长期人格段（≤180 字）。"""
    v = view(traits)
    lines = ["<traits>", "长期人格（天级漂移，比此刻情绪慢得多，别当成情绪）"]
    for k in BOUNDS:
        lines.append("- %s %.2f：%s" % (LABEL[k], v[k], describe(k, v[k])))
    lines.append("这些只影响长期语气倾向；此刻说什么以这一轮的情绪与场景为准。")
    lines.append("</traits>")
    return "\n".join(lines)