# -*- coding: utf-8 -*-
"""LLM 重排：把「向量召回的一堆候选」交给模型挑真正相关的。

为什么需要：向量绝对阈值不可靠（实测 0.65 精度 43.5%、0.70 几乎不召回）。
检索系统的常规解法是「召回宽、重排严」——粗召回 5 条，模型只留相关的。

实测（docs/重排实验.md，20 句有候选的消息）：
- 重排后精度 **80.0%**（25 条判定中 20 条相关）；
- 60% 的候选消息能留下至少一条；
- 注意：重排与裁判同为 deepseek-chat，存在自偏好偏差，数值偏乐观，仅作方向判断。

工程约束：
- 超时 6 秒，失败**不注入**（回退到 score>=0.70 的极严阈值），绝不让重排拖慢/拖垮回复；
- 结果按「查询 + 候选集」缓存 30 分钟，同一句话不重复花 token。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.request
from pathlib import Path
from typing import Any

SECRETS = Path(os.environ.get("BEAM_SECRETS_FILE") or r"%USERPROFILE%\.config\v1-gateway\gateway_secrets.json")
API_URL = "https://api.deepseek.com/v1/chat/completions"
CACHE_TTL = 1800.0
_CACHE: dict[str, tuple[float, list[int]]] = {}


def load_key() -> str:
    try:
        return str(json.loads(SECRETS.read_text(encoding="utf-8")).get("api_key") or "")
    except Exception:  # noqa: BLE001
        return ""


def _cache_key(query: str, texts: list[str]) -> str:
    raw = query + "||" + "|".join(texts)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def choose(query: str, texts: list[str], *, timeout: float = 6.0, api_key: str = "") -> list[int] | None:
    """返回保留的候选下标（0-based）；失败返回 None（调用方走保守回退）。"""
    if not texts:
        return []
    key = _cache_key(query, texts)
    cached = _CACHE.get(key)
    if cached and time.time() - cached[0] < CACHE_TTL:
        return cached[1]

    api_key = api_key or load_key()
    if not api_key:
        return None

    listing = "\n".join(f"{i}. {t}" for i, t in enumerate(texts, 1))
    prompt = (
        "下面是一句用户消息和候选的长期记忆。只挑出**真正相关、值得想起**的；宁缺勿滥，"
        "仅仅共享虚词（怎么/什么/今天）不算相关。\n\n"
        f"用户消息：{query}\n\n候选记忆：\n{listing}\n\n"
        '只输出 JSON：{"keep":[1,3]}；没有相关就 {"keep":[]}'
    )
    body = json.dumps({
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 120,
        "response_format": {"type": "json_object"},
    }).encode("utf-8")
    req = urllib.request.Request(
        API_URL, data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        keep = [int(x) - 1 for x in (json.loads(content).get("keep") or [])]
        keep = [i for i in keep if 0 <= i < len(texts)]
    except Exception:  # noqa: BLE001
        return None

    _CACHE[key] = (time.time(), keep)
    if len(_CACHE) > 200:  # 简单上限，防止长跑无限增长
        for k in list(_CACHE)[:50]:
            _CACHE.pop(k, None)
    return keep


def stats() -> dict[str, Any]:
    return {"cached": len(_CACHE), "has_key": bool(load_key())}
