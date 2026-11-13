# -*- coding: utf-8 -*-
"""注入观测：每轮到底被塞了多少字、谁塞的、有没有超预算。

背景（v1 实测）：brain 预算 260 字，实际注入 1682 字，超 6.5 倍；
五个插件各自在 `on_llm_request` 里追加，谁也不知道总额。

做法：
- 在最低优先级（最后执行）的钩子里读**最终**请求；
- 用 `<tag>` 归因到来源（`<affect_state>` / `<whale_state>` / `<self_awareness>` …）；
- 超预算记一笔，长期统计给自省与迭代计划用。

本模块是纯函数，不依赖 astrbot，可单测。
"""

from __future__ import annotations

import re
import time
from typing import Any, Iterable

# 已知注入块的标签 → 归属（便于归因，而不是怪「整体太长」）
TAG_SOURCES = {
    "affect_state": "情绪内核（whale_affect）",
    "whale_state": "状态总线（whale_state）",
    "self_awareness": "自省（whale_introspect）",
    "emotion_state": "情绪单一源（whale_core）",
    "memory": "记忆（iris / whale_memory）",
    "memories": "记忆（iris / whale_memory）",
    "brain": "当日心智（whale_brain）",
    "speech_style": "口癖（speech_style）",
    "fishtalk": "口吻（whale_fishtalk）",
    "bili": "陪看（whale_bili）",
    "voice": "语音（whale_voice）",
    "dere": "恋人状态（dere_lover）",
    "soul": "灵魂层（dafeiyu_soul）",
    "virtual_life": "生活（virtual_life）",
}

_TAG_RE = re.compile(r"<([a-zA-Z_][\w-]{1,40})>")


def measure(
    part_texts: Iterable[str],
    *,
    prompt: str = "",
    system_prompt: str = "",
    cap_chars: int = 3000,
) -> dict[str, Any]:
    """统计一轮请求里的注入量。

    part_texts: extra_user_content_parts 的文本（临时注入块通常在这里）
    prompt/system_prompt: 其余可观测部分
    """
    parts = [str(t or "") for t in part_texts]
    part_chars = sum(len(t) for t in parts)
    prompt_chars = len(prompt or "")
    system_chars = len(system_prompt or "")

    tags: dict[str, int] = {}
    for text in parts:
        for tag in _TAG_RE.findall(text):
            tags[tag] = tags.get(tag, 0) + 1

    total = part_chars + prompt_chars
    return {
        "ts": time.time(),
        "part_chars": part_chars,
        "prompt_chars": prompt_chars,
        "system_chars": system_chars,
        "total_chars": total,
        "parts": len(parts),
        "tags": tags,
        "cap_chars": cap_chars,
        "over_cap": total > cap_chars,
        "over_by": max(0, total - cap_chars),
    }


def summarize(events: list[dict[str, Any]]) -> dict[str, Any]:
    """把最近若干轮汇总成人看的结论。"""
    if not events:
        return {"turns": 0, "avg_chars": 0, "max_chars": 0, "over_cap_turns": 0, "by_tag": {}}
    totals = [int(e.get("total_chars", 0)) for e in events]
    by_tag: dict[str, int] = {}
    for e in events:
        for tag, n in (e.get("tags") or {}).items():
            by_tag[tag] = by_tag.get(tag, 0) + int(n)
    over = [e for e in events if e.get("over_cap")]
    worst = max(events, key=lambda e: int(e.get("total_chars", 0)))
    budgets = [e["budget"] for e in events if isinstance(e.get("budget"), dict)]
    return {
        "turns": len(events),
        "avg_chars": round(sum(totals) / len(totals), 1),
        "max_chars": max(totals),
        "over_cap_turns": len(over),
        "over_ratio": round(len(over) / len(events), 3),
        "by_tag": dict(sorted(by_tag.items(), key=lambda kv: -kv[1])),
        "managed_budget": {
            "turns": len(budgets),
            "over_cap_turns": sum(b["before_chars"] > b["cap_chars"] for b in budgets),
            "dropped_parts": sum(b["dropped_parts"] for b in budgets),
            "max_before_chars": max((b["before_chars"] for b in budgets), default=0),
            "max_after_chars": max((b["after_chars"] for b in budgets), default=0),
        },
        "worst": worst,
        "last": events[-1],
    }


def attribute(tag: str) -> str:
    return TAG_SOURCES.get(tag, "未登记来源")


# Only complete, temporary, known plugin blocks are eligible for removal.
_BUDGET_TAGS = {
    "memory_index": 0,
    "emotion_state": 1,
    "whale_state": 2,
    "self_awareness": 3,
    "psyche": 4,
    "character_state": 5,
    "voice_state": 6,
    "screen_now": 7,
    "energy": 8,
    "affect_state": 9,
    "fishtalk_style": 10,
    "meme_capability": 11,
    "dynamic_context": 12,
    "speech_style": 13,
}
_BLOCK_RE = re.compile(r"\s*<([a-zA-Z_][\w-]{1,40})>.*</\1>\s*", re.DOTALL)


def apply_budget(
    parts: Iterable[Any], cap_chars: int = 3000
) -> tuple[list[Any], dict[str, Any]]:
    """Select whole optional injection blocks without editing user content.

    Args:
        parts: Provider content parts; only known temporary text blocks qualify.
        cap_chars: Maximum characters in managed optional blocks.

    Returns:
        Kept original objects and content-free budget diagnostics. Unknown,
        permanent, multimodal and untagged parts are never removed.
    """
    original = list(parts)
    cap = max(0, int(cap_chars))
    eligible = []
    for index, part in enumerate(original):
        text = getattr(part, "text", "")
        if getattr(part, "_no_save", False) is not True or not isinstance(text, str):
            continue
        if getattr(part, "type", "text") != "text":
            continue
        match = _BLOCK_RE.fullmatch(text)
        if (
            match
            and match[1] in _BUDGET_TAGS
            and text.count(f"<{match[1]}>") == 1
            and text.count(f"</{match[1]}>") == 1
        ):
            eligible.append((index, match[1], text))
    selected = set()
    used = 0
    dropped_tags: dict[str, int] = {}
    for index, tag, text in sorted(
        eligible, key=lambda item: (_BUDGET_TAGS[item[1]], item[0])
    ):
        if used + len(text) <= cap:
            selected.add(index)
            used += len(text)
        else:
            dropped_tags[tag] = dropped_tags.get(tag, 0) + 1
    managed_indices = {index for index, _, _ in eligible}
    kept = [
        part
        for index, part in enumerate(original)
        if index not in managed_indices or index in selected
    ]
    return kept, {
        "scope": "known_temporary_plugin_blocks",
        "cap_chars": cap,
        "before_chars": sum(len(text) for _, _, text in eligible),
        "after_chars": used,
        "dropped_parts": len(eligible) - len(selected),
        "dropped_tags": dropped_tags,
        "unmanaged_text_chars": sum(
            len(part.text)
            for index, part in enumerate(original)
            if index not in managed_indices
            and isinstance(getattr(part, "text", None), str)
        ),
    }
