"""BEAM-AI × 语音闭环桥（beams_voice.py）

把新内核的内部状态映射成 IndexTTS 需要的 8 维情绪向量，并做双向：

  输出方向：BEAM 状态 → emo_vector（8维）→ /speak（8912）
  输入方向：ASR 文本 + laya/QwenEmo 情绪 → BEAM 事件（speech）

8 维顺序（feiyu_voice 契约）：[happy, angry, sad, afraid, disgusted, melancholic, surprised, calm]
服务建议：向量和 ≤ 0.6。

设计原则：
- 不新增模型（GPU 只剩 1G，用户明确不要往 GPU 加东西）；
- 情绪来源以 BEAM 内核为主（新框架），旧 affect_state 不再作为主源；
- 文本情绪（laya 8 类 + QwenEmo 校准）作为"快通道补充"，不反转主状态。
"""
from __future__ import annotations

import math
import sys
from typing import Dict, List, Optional

CORE_ROOT = r"%PROJECT_HOME%\框架重构"
if CORE_ROOT not in sys.path:
    sys.path.insert(0, CORE_ROOT)

try:
    from beams_core import schema
except Exception:  # pragma: no cover - 单独跑时兜底
    class _S:  # noqa
        @staticmethod
        def clamp(x, lo, hi, d=None):
            try:
                v = float(x)
            except Exception:
                v = lo if d is None else d
            return max(lo, min(hi, v))

        @staticmethod
        def clamp01(x, d=0.0):
            return _S.clamp(x, 0.0, 1.0, d)

        @staticmethod
        def finite(x, d=0.0):
            try:
                v = float(x)
            except Exception:
                return d
            return v if math.isfinite(v) else d

    schema = _S()

#: 语音模型的情绪档位（feiyu_voice 契约）
EMO_IDX = ("happy", "angry", "sad", "afraid", "disgusted", "melancholic", "surprised", "calm")

#: BEAM 主情绪 → IndexTTS 档位
LABEL_TO_PRESET = {
    "开心": "happy", "兴奋": "happy", "黏人": "coquettish", "安心": "calm",
    "平静": "calm", "低落": "sad", "委屈": "sad", "烦躁": "angry",
    "紧张": "surprised", "困": "sleepy",
}

#: 向量和上限（服务建议 ≤0.6）
VEC_SUM_CAP = 0.6


def _c01(x, d=0.0):
    return schema.clamp01(x, d)


def _n(x, d=0.0):
    return schema.finite(x, d)


def emo_vector(state: Dict) -> List[float]:
    """BEAM 状态 → 8 维语音情绪向量（和 ≤ 0.6）。

    公式意图（都能手算、可解释）：
      happy      = 正效价 + 适度唤醒
      angry      = 负效价 + 高唤醒 + 想压住（低支配）
      sad        = 负效价 + 低唤醒 + 底色偏凉
      afraid     = 高唤醒 + 低支配 + 负效价（紧张/害怕）
      disgusted  = 0（语音场景不用，避免听感怪）
      melancholic= 慢通道心情凉 + 疲劳
      surprised  = 唤醒高于常态 + 不确定性（内核里用 prediction_error 近似）
      calm       = 中性 + 低唤醒
    """
    if not isinstance(state, dict):
        state = {}
    fast = state.get("fast") or {}
    slow = state.get("slow") or {}
    intero = state.get("intero") or {}
    v = _n(fast.get("valence"), 0.1)
    a = _n(fast.get("arousal"), 0.3)
    d = _n(fast.get("dominance"), 0.55)
    mood = _n(slow.get("mood_valence"), 0.1)
    stress = _c01(slow.get("stress"), 0.15)
    fatigue = _c01(slow.get("fatigue"), 0.2)
    pe = _c01(intero.get("prediction_error"), 0.1)

    happy = _c01(0.55 * max(0.0, v) + 0.25 * max(0.0, a - 0.35) + 0.10 * max(0.0, mood))
    angry = _c01(0.50 * max(0.0, -v) + 0.25 * max(0.0, a - 0.55) + 0.20 * stress - 0.15 * d)
    sad = _c01(0.55 * max(0.0, -v) + 0.25 * max(0.0, 0.42 - a) + 0.25 * max(0.0, -mood))
    afraid = _c01(0.30 * max(0.0, a - 0.55) + 0.25 * max(0.0, -v) + 0.25 * (1.0 - d) - 0.10)
    melancholic = _c01(0.45 * max(0.0, -mood) + 0.25 * fatigue + 0.15 * max(0.0, 0.4 - a))
    surprised = _c01(0.35 * max(0.0, a - 0.55) + 0.30 * pe)
    calm = _c01(0.55 - 0.45 * abs(v - 0.05) - 0.35 * a + 0.15 * (1.0 - stress))

    vec = [happy, angry, sad, afraid, 0.0, melancholic, surprised, calm]
    # 归一：和超过 0.6 就等比压到 0.6（保留相对比例）
    total = sum(vec)
    if total > VEC_SUM_CAP and total > 0:
        scale = VEC_SUM_CAP / total
        vec = [x * scale for x in vec]
    return [round(x, 3) for x in vec]


def preset_of(state: Dict) -> str:
    """主情绪 → 档位；内核没标签时按向量最大值兜底。"""
    label = str((state or {}).get("fast", {}).get("label") or "")
    if label in LABEL_TO_PRESET:
        return LABEL_TO_PRESET[label]
    vec = emo_vector(state)
    names = dict(zip(EMO_IDX, vec))
    top = max(names, key=names.get)
    return "happy" if top == "happy" else top if top in ("angry", "sad", "surprised") else "calm"


def speech_event(asr_text: str, text_emotion: Optional[Dict] = None) -> Dict:
    """语音输入 → BEAM 事件（speech）。

    text_emotion：laya 分类结果 {label, confidence, probs}；
    融合规则：文本情绪做快通道 appraisal 的 goal_congruence，
    置信度低时向中性收缩（避免一句话把情绪带飞）。
    另外带 speech=True 标记，供表达层判断"这轮是语音场景"（回复要更短）。
    """
    text = str(asr_text or "").strip()
    emo = text_emotion if isinstance(text_emotion, dict) else {}
    label = str(emo.get("label") or "")
    conf = _c01(emo.get("confidence"), 0.0)
    # laya 8 类 → goal_congruence（正=舒服，负=难受）
    polarity = {"兴奋": 0.7, "开心": 0.6, "撒娇": 0.5, "平静": 0.1,
                "吃醋": -0.35, "生气": -0.6, "疲惫": -0.4, "难过": -0.7}
    gc = polarity.get(label, 0.1)
    # 置信度不够 → 往中性收
    gc = 0.1 + (gc - 0.1) * min(1.0, conf / 0.6)
    return {
        "kind": "speech",
        "goal_congruence": round(gc, 3),
        "intensity": round(min(1.0, 0.45 + 0.55 * conf), 3),
        "why": ("语音：" + text[:40]) if text else "语音（空）",
        "speech": True,
        "text_emotion": label,
        "confidence": round(conf, 3),
    }


def apply_text_emotion(state: Dict, text_emotion: Dict, weight: float = 0.35) -> Dict:
    """文本情绪对慢通道的轻量修正（第二方向：语音情绪影响内核底色）。

    只在置信度高时生效，且上限很小（一次最多 0.05），防止"一句话改心情"。
    """
    st = schema.copy_state(state) if hasattr(schema, "copy_state") else dict(state or {})
    emo = text_emotion if isinstance(text_emotion, dict) else {}
    label = str(emo.get("label") or "")
    conf = _c01(emo.get("confidence"), 0.0)
    polarity = {"兴奋": 0.7, "开心": 0.6, "撒娇": 0.5, "平静": 0.1,
                "吃醋": -0.35, "生气": -0.6, "疲惫": -0.4, "难过": -0.7}
    gc = polarity.get(label, 0.0) * min(1.0, conf / 0.6)
    delta = schema.clamp(gc * weight * 0.1, -0.05, 0.05)
    try:
        slow = st["slow"]
        slow["mood_valence"] = round(schema.clamp(slow.get("mood_valence", 0.0) + delta, -1.0, 1.0), 4)
    except Exception:
        pass
    return st


if __name__ == "__main__":
    demo = {"fast": {"valence": 0.45, "arousal": 0.62, "dominance": 0.6, "label": "兴奋"},
            "slow": {"mood_valence": 0.32, "stress": 0.12, "fatigue": 0.18},
            "intero": {"prediction_error": 0.2}}
    vec = emo_vector(demo)
    print("vec:", vec, "sum=%.3f" % sum(vec))
    print("preset:", preset_of(demo))