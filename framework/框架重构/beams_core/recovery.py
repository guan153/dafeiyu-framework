"""BEAM-AI · 出口失败恢复（recovery.py）

出问题不等于硬编：每一类出口失败都有明确的回退策略。

    action        attempt  策略
    tts           1        重试一次（换快档/缩短文本）
    tts           2+       降级纯文字
    send          1-2      重试（间隔递增）
    send          3+       静默 + 记日志（不刷屏、不重发）
    asr           任意     当纯文字消息处理（语音入口不动主线）
    feiyu         1        重试一次
    feiyu         2+       降级纯文字
    llm           1-2      降级 quick 路由（短回答）
    llm           3+       兜底话术

所有决策是纯函数（无随机、无 IO），可单测、可回放。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

#: 每类出口最多重试几次（超过就降级）
MAX_RETRY = {"tts": 1, "send": 2, "feiyu": 1, "llm": 2, "asr": 0}

#: 重试后的动作
FALLBACK = {
    "tts": "text_only",
    "send": "silent",
    "feiyu": "text_only",
    "llm": "quick_text",
    "asr": "text_only",
}

#: 降级给用户看的话（兜底话术，不编、不慌）
FALLBACK_TEXT = {
    "tts": "",
    "send": "",
    "feiyu": "",
    "llm": "这会儿脑子卡住了，回头再说。",
    "asr": "",
}


def failure_retry(action: str, err: str = "", attempt: int = 1) -> Dict[str, Any]:
    """出口失败 → 下一步怎么办。"""
    act = str(action or "").strip().lower()
    att = max(1, int(attempt or 1))
    if act not in MAX_RETRY:
        return {"retry": False, "fallback": "silent", "text": "", "reason": "未知动作，静默"}
    if att <= MAX_RETRY[act]:
        return {"retry": True, "fallback": "", "text": "",
                "reason": "%s 第 %d 次失败（%s），重试" % (act, att, str(err)[:40])}
    return {"retry": False, "fallback": FALLBACK[act],
            "text": FALLBACK_TEXT.get(act, ""),
            "reason": "%s 连续失败 %d 次（%s），降级为 %s" % (act, att, str(err)[:40], FALLBACK[act])}
