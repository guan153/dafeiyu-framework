# -*- coding: utf-8 -*-
"""记忆向量索引：把「事实层」记忆嵌成向量，按语义召回。

为什么要有它：关键词召回精度只有 43.6%–57.1%（见 docs/召回精度.md），
问「今天过得怎么样」会命中「我喜欢你不穿的怎么样」——字面像，语义不像。

索引范围（只放事实，不放原始对话）：
- profile：USER.md / KEY.md / MEMORY.md 的每一行
- semantic：iris 的摘要记忆（58 条）
不放 soul 原话与每日日志：它们是「对话碎片」，对长期记忆是噪声。

存储：SQLite 单表 + float32 BLOB，零额外依赖；增量构建，按文本哈希去重。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from array import array
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable

from . import paths
from .embeddings import EmbeddingClient, cosine
from .memory import fingerprint

_SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
    id        TEXT PRIMARY KEY,
    text      TEXT NOT NULL,
    source    TEXT NOT NULL,
    meta      TEXT DEFAULT '{}',
    vec       BLOB NOT NULL,
    dims      INTEGER NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_docs_source ON docs(source);
"""


@dataclass
class Doc:
    id: str
    text: str
    source: str
    meta: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def collect_docs() -> list[Doc]:
    """收集事实层文本；同一句话只留一条（跨源去重）。"""
    docs: list[Doc] = []
    seen: set[str] = set()

    root = paths.plugin_data_dir("astrbot_plugin_whale_memory") / "memory"
    for name in ("USER.md", "KEY.md", "MEMORY.md"):
        path = root / name
        if not path.exists():
            continue
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                text = line.strip().lstrip("-").strip()
                if len(text) < 6:
                    continue
                key = fingerprint(text)
                if key in seen:
                    continue
                seen.add(key)
                docs.append(Doc(id=key, text=text[:400], source="profile", meta={"file": name}))
        except Exception:  # noqa: BLE001
            continue

    db = paths.plugin_data_dir("astrbot_plugin_iris_chat_memory") / "faiss" / "memory_default" / "metadata.db"
    if db.exists():
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            rows = con.execute("SELECT content, timestamp FROM memories").fetchall()
            con.close()
        except Exception:  # noqa: BLE001
            rows = []
        for content, ts in rows:
            text = str(content or "").strip()
            if len(text) < 6:
                continue
            key = fingerprint(text)
            if key in seen:
                continue
            seen.add(key)
            docs.append(Doc(id=key, text=text[:400], source="semantic", meta={"timestamp": ts}))
    return docs


class MemoryIndex:
    def __init__(self, db_path: Path | str | None = None, client: EmbeddingClient | None = None):
        self.path = Path(db_path or (paths.core_dir() / "memory_index.sqlite3"))
        self.client = client or EmbeddingClient.from_config()
        self._ensure()

    def _ensure(self) -> None:
        con = sqlite3.connect(self.path)
        con.executescript(_SCHEMA)
        con.commit()
        con.close()

    # ── 构建 ────────────────────────────────────────────────
    def build(self, *, force: bool = False, docs: list[Doc] | None = None) -> dict[str, Any]:
        if self.client is None:
            return {"ok": False, "error": "no_embedding_client", "added": 0, "total": 0}

        docs = docs if docs is not None else collect_docs()
        con = sqlite3.connect(self.path)
        try:
            existing = {r[0] for r in con.execute("SELECT id FROM docs")}
            todo = [d for d in docs if force or d.id not in existing]
            if force:
                con.execute("DELETE FROM docs")
            added = 0
            for start in range(0, len(todo), 10):  # DashScope 单批上限 10
                chunk = todo[start:start + 10]
                vectors = self.client.embed([d.text for d in chunk], text_type="document")
                now = time.time()
                for doc, vec in zip(chunk, vectors):
                    if not vec:
                        continue
                    buf = array("f", vec).tobytes()
                    con.execute(
                        "INSERT OR REPLACE INTO docs (id,text,source,meta,vec,dims,updated_at) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (doc.id, doc.text, doc.source, json.dumps(doc.meta, ensure_ascii=False),
                         buf, len(vec), now),
                    )
                    added += 1
            con.commit()
            total = int(con.execute("SELECT COUNT(*) FROM docs").fetchone()[0])
        finally:
            con.close()
        return {"ok": True, "added": added, "total": total, "collected": len(docs)}

    # ── 检索 ────────────────────────────────────────────────
    def search(
        self,
        query: str,
        limit: int = 3,
        *,
        min_score: float = 0.45,
        low_score: float = 0.0,
        keyword_min: int = 0,
    ) -> list[dict[str, Any]]:
        """语义检索，支持「混合放行」：

        - 相似度 ≥ min_score：直接放行；
        - 相似度 ≥ low_score 且与查询有 ≥keyword_min 个词重叠：也放行。

        混合的目的：把 0.55–0.65 这段「语义有点像但不够确定」的候选，
        用关键词做二次确认，提覆盖而不明显掉精度。
        """
        if self.client is None or not (query or "").strip():
            return []
        try:
            qvec = self.client.embed([query], text_type="query")[0]
        except Exception:  # noqa: BLE001
            return []
        if not qvec:
            return []

        terms = []
        if low_score > 0 and keyword_min > 0:
            from .memory_recall import extract_terms

            terms = extract_terms(query)

        con = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        try:
            rows = con.execute("SELECT id,text,source,meta,vec,dims FROM docs").fetchall()
        finally:
            con.close()

        scored = []
        for doc_id, text, source, meta, blob, dims in rows:
            vec = array("f")
            vec.frombytes(blob)
            if len(vec) != len(qvec):
                continue
            score = cosine(qvec, vec)
            matched = sum(1 for t in terms if t in str(text)) if terms else 0
            accept = score >= min_score or (
                low_score > 0 and score >= low_score and matched >= keyword_min
            )
            if accept:
                scored.append({
                    "id": doc_id, "text": text, "source": source,
                    "meta": json.loads(meta or "{}"), "score": round(score, 4),
                    "matched": matched,
                })
        scored.sort(key=lambda x: (-x["score"], -x["matched"]))
        return scored[:limit]

    def stats(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"total": 0, "by_source": {}}
        con = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        try:
            total = int(con.execute("SELECT COUNT(*) FROM docs").fetchone()[0])
            by_source = {r[0]: r[1] for r in con.execute("SELECT source, COUNT(*) FROM docs GROUP BY source")}
            last = con.execute("SELECT MAX(updated_at) FROM docs").fetchone()[0]
        finally:
            con.close()
        return {"total": total, "by_source": by_source, "updated_at": last or 0}


_INDEX: MemoryIndex | None = None


def get_index() -> MemoryIndex:
    global _INDEX
    if _INDEX is None:
        _INDEX = MemoryIndex()
    return _INDEX


def render(hits: Iterable[dict[str, Any]], *, budget: int = 400) -> str:
    """渲染成注入块，带相似度便于复盘。"""
    items = list(hits)
    if not items:
        return ""
    lines = ["<memory_digest>", "（我记着的、和这句有关的事）"]
    for hit in items:
        label = {"profile": "档案", "semantic": "摘要", "episodic": "原话"}.get(hit.get("source"), hit.get("source"))
        line = f"- [{label}] {hit.get('text', '')}"
        if sum(len(x) for x in lines) + len(line) > budget:
            break
        lines.append(line)
    lines.append("</memory_digest>")
    return "\n".join(lines)
