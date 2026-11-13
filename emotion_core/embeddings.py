# -*- coding: utf-8 -*-
"""Embedding 客户端（DashScope text-embedding-v4）。

为什么自己写：whale_core 要能脱离 astrbot 单测，不想依赖 dashscope SDK；
DashScope 的原生 HTTP 接口很稳定，批量上限 10 条（本项目历史上被这个坑过）。

密钥来源：AstrBot 的 `data/cmd_config.json` → provider 里 `bailian_embedding`。
只读，不写回、不打印。
"""

from __future__ import annotations

import json
import math
import time
import urllib.request
from pathlib import Path
from typing import Any

from . import paths

BATCH = 10
DEFAULT_BASE = "https://dashscope.aliyuncs.com/api/v1"
DEFAULT_MODEL = "text-embedding-v4"
DEFAULT_DIM = 1024


def load_config() -> dict[str, Any]:
    """从 AstrBot 配置里找 embedding provider；找不到返回空。"""
    cfg_path = paths.data_dir() / "cmd_config.json"
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return {}
    for provider in cfg.get("provider") or []:
        if not isinstance(provider, dict):
            continue
        if provider.get("provider_type") == "embedding" or provider.get("type", "").endswith("embedding"):
            key = str(provider.get("embedding_api_key") or "")
            if not key:
                continue
            return {
                "api_key": key,
                "base": str(provider.get("embedding_api_base") or DEFAULT_BASE),
                "model": str(provider.get("embedding_model") or DEFAULT_MODEL),
                "dims": int(provider.get("embedding_dimensions") or DEFAULT_DIM),
                "provider_id": str(provider.get("id") or ""),
            }
    return {}


class EmbeddingClient:
    def __init__(self, api_key: str, base: str = DEFAULT_BASE, model: str = DEFAULT_MODEL, dims: int = DEFAULT_DIM):
        self.api_key = api_key
        self.base = base.rstrip("/")
        self.model = model
        self.dims = dims
        self._cache: dict[str, list[float]] = {}

    @classmethod
    def from_config(cls) -> "EmbeddingClient | None":
        cfg = load_config()
        if not cfg:
            return None
        return cls(cfg["api_key"], cfg["base"], cfg["model"], cfg["dims"])

    def embed(self, texts: list[str], *, text_type: str = "document", timeout: float = 20.0) -> list[list[float]]:
        """批量取向量；自动按 10 条分批、缓存命中直接返回。"""
        texts = [str(t or "")[:2000] for t in texts]
        out: list[list[float] | None] = [None] * len(texts)
        pending: list[tuple[int, str]] = []
        for i, text in enumerate(texts):
            cached = self._cache.get(text)
            if cached is not None:
                out[i] = cached
            else:
                pending.append((i, text))

        for start in range(0, len(pending), BATCH):
            chunk = pending[start:start + BATCH]
            vectors = self._request([t for _, t in chunk], text_type=text_type, timeout=timeout)
            for (index, text), vector in zip(chunk, vectors):
                out[index] = vector
                self._cache[text] = vector

        return [v if v is not None else [] for v in out]

    def _request(self, texts: list[str], *, text_type: str, timeout: float) -> list[list[float]]:
        url = f"{self.base}/services/embeddings/text-embedding/text-embedding"
        body = json.dumps({
            "model": self.model,
            "input": {"texts": texts},
            "parameters": {"dimension": self.dims, "text_type": text_type},
        }, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
            method="POST",
        )
        last: Exception | None = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                items = (data.get("output") or {}).get("embeddings") or []
                if not items:
                    raise RuntimeError(f"空向量返回：{str(data)[:200]}")
                items = sorted(items, key=lambda x: x.get("text_index", x.get("index", 0)))
                return [list(item.get("embedding") or []) for item in items]
            except Exception as exc:  # noqa: BLE001
                last = exc
                time.sleep(0.8 * (attempt + 1))
        raise RuntimeError(f"embedding 失败：{type(last).__name__}: {last}")


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
