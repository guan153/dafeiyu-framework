# -*- coding: utf-8 -*-
"""统一召回原型（影子模式）。

目标：六层记忆不再各自往 prompt 里塞，而是**一次召回、统一打分、统一截断**。
这一版只做「召回 + 打分 + 渲染」，**不注入**——先影子运行，用真实对话算覆盖率，
确认不会丢记忆，再切正式注入。

召回方式：关键词召回（不是整句 LIKE）。
- 中文按连续片段切 2-gram（「今天想听你唱歌」→ 今天/天想/想听/听你/你唱/唱歌），
- 去掉高频停用词，最多 8 个词，
- 任一命中即候选，命中越多分越高，再乘来源权重。

为什么不用 embedding：iris 有 FAISS，但跨模块调它的检索器不稳定；
关键词召回完全可解释，在「小体量、强相关」的陪伴场景够用。
"""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from . import paths
from .memory import fingerprint

SOURCE_WEIGHT = {
    "profile": 1.25,   # 用户档案/项目约定：最稳定，优先
    "semantic": 1.1,   # 摘要过的语义记忆
    "episodic": 1.0,   # 原话
    "journal": 0.7,    # 每日日志：本质是原始对话，最容易重复，压最低
}

STOPWORDS = set(
    "的 了 我 你 他 她 它 是 在 有 吗 呢 啊 吧 嘛 就 都 也 和 与 这 那 个 不 会 能 要 想 说 去 来 "
    "把 被 让 给 对 好 很 太 再 又 还 一 二 三 上 下 里 中 么 呀 哦 嗯 吧 啦 咯 哇 嘿 哈 "
    # 常见疑问/功能二元组：这些词命中不代表相关
    "今天 明天 昨天 现在 怎么 么样 什么 一句 一下 一起 这些 那些 还是 就是 不是 没有 知道 觉得 "
    "时候 一个 这个 那个 我的 你的 随便 聊聊 过得 可以 帮我 给我 告诉 告诉 一下 行吗 好吗 "
    "点啥 吃啥 干啥 事吗 了吗 呢吗 你说 说句 句话 故事 一段 每句 结尾 用句 小故 "
    "记得 我们 你们 他们 她们 什么 怎么样 是不是 能不能 要不要 有没有 一起 现在".split()
)


@dataclass
class Hit:
    text: str
    source: str
    score: float
    meta: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


try:  # jieba 在 AstrBot venv 里有；本地测试环境没有就走 2-gram 兜底
    import jieba  # type: ignore

    _HAS_JIEBA = True
except Exception:  # noqa: BLE001
    jieba = None  # type: ignore
    _HAS_JIEBA = False


def extract_terms(query: str, max_terms: int = 8) -> list[str]:
    """从一句话里抽可检索的词。

    优先 jieba 分词（「我喜欢什么」→ 喜欢），没有 jieba 才退化成 2-gram。
    2-gram 的问题很实在：会产生「我喜/欢什」这种永远匹配不上的碎片。
    """
    query = (query or "").strip()
    if not query:
        return []

    terms: list[str] = []
    if _HAS_JIEBA:
        try:
            for word in jieba.cut(query):
                word = word.strip().lower()
                if len(word) < 2 or word in STOPWORDS:
                    continue
                if not re.match(r"[\u4e00-\u9fffA-Za-z0-9]", word):
                    continue
                terms.append(word)
        except Exception:  # noqa: BLE001
            terms = []
    if not terms:
        # 兜底：中文 2-gram + 英文/数字整词
        for chunk in re.findall(r"[\u4e00-\u9fff]+|[A-Za-z0-9_]+", query):
            if re.match(r"[A-Za-z0-9_]+", chunk):
                if len(chunk) >= 2 and chunk.lower() not in STOPWORDS:
                    terms.append(chunk.lower())
                continue
            if len(chunk) <= 2:
                if chunk not in STOPWORDS:
                    terms.append(chunk)
                continue
            for i in range(len(chunk) - 1):
                gram = chunk[i:i + 2]
                if gram in STOPWORDS:
                    continue
                terms.append(gram)

    seen: set[str] = set()
    ordered: list[str] = []
    for term in terms:
        if term in seen:
            continue
        seen.add(term)
        ordered.append(term)
        if len(ordered) >= max_terms:
            break
    return ordered


def _like_clause(column: str, terms: list[str]) -> tuple[str, list[str]]:
    parts = [f"{column} LIKE ?" for _ in terms]
    return "(" + " OR ".join(parts) + ")", [f"%{t}%" for t in terms]


def _soul_recall(terms: list[str], limit: int) -> list[Hit]:
    db = paths.plugin_data_dir("astrbot_plugin_dafeiyu_soul") / "soul.sqlite3"
    if not db.exists() or not terms:
        return []
    where, params = _like_clause("text", terms)
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute(
            f"SELECT text, importance, created FROM memories WHERE {where} "
            "ORDER BY importance DESC, created DESC LIMIT ?",
            (*params, limit * 4),
        ).fetchall()
    finally:
        con.close()
    hits = []
    for text, importance, created in rows:
        matched = sum(1 for t in terms if t in str(text))
        imp = float(importance or 0) / 5.0
        age_days = max(0.0, (time.time() - float(created or 0)) / 86400)
        fresh = 1.0 if age_days < 7 else (0.6 if age_days < 30 else 0.3)
        score = (0.55 + 0.35 * imp) * fresh + 0.12 * max(0, matched - 1)
        hits.append(Hit(str(text), "episodic", round(score, 3),
                        {"importance": importance, "age_days": round(age_days, 1), "matched": matched}))
    return hits


def _iris_recall(terms: list[str], limit: int) -> list[Hit]:
    db = paths.plugin_data_dir("astrbot_plugin_iris_chat_memory") / "faiss" / "memory_default" / "metadata.db"
    if not db.exists() or not terms:
        return []
    where, params = _like_clause("content", terms)
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute(
            f"SELECT content, timestamp FROM memories WHERE {where} ORDER BY rowid DESC LIMIT ?",
            (*params, limit * 4),
        ).fetchall()
    finally:
        con.close()
    hits = []
    for content, ts in rows:
        matched = sum(1 for t in terms if t in str(content))
        score = 0.7 + 0.12 * max(0, matched - 1)
        hits.append(Hit(str(content), "semantic", round(score, 3), {"timestamp": ts, "matched": matched}))
    return hits


_PROFILE_FILES = ("USER.md", "KEY.md", "MEMORY.md")


def _profile_recall(terms: list[str], limit: int) -> list[Hit]:
    """稳定档案：USER.md / KEY.md / MEMORY.md（不含每日日志）。"""
    root = paths.plugin_data_dir("astrbot_plugin_whale_memory") / "memory"
    if not root.exists() or not terms:
        return []
    hits: list[Hit] = []
    for name in _PROFILE_FILES:
        path = root / name
        if not path.exists():
            continue
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                matched = sum(1 for t in terms if t in line)
                if matched and len(line.strip()) > 4:
                    hits.append(Hit(line.strip()[:200], "profile", round(0.9 + 0.12 * (matched - 1), 3),
                                    {"file": name, "mtime": path.stat().st_mtime, "matched": matched}))
                    if len(hits) >= limit * 3:
                        return hits
        except Exception:  # noqa: BLE001
            continue
    return hits


_JOURNAL_PREFIX = re.compile(r"^\s*-?\s*\d{1,2}:\d{2}\s*\[[^\]]{1,12}\]\s*")


def _strip_journal_prefix(text: str) -> str:
    """日志行形如 `- 13:22 [主人] 原话`，剥掉前缀才能和原话去重。"""
    return _JOURNAL_PREFIX.sub("", text or "").strip()


def _journal_recall(terms: list[str], limit: int) -> list[Hit]:
    """每日日志：原始对话的按天归档，权重最低，只补最近 7 天。"""
    root = paths.plugin_data_dir("astrbot_plugin_whale_memory") / "memory"
    if not root.exists() or not terms:
        return []
    hits: list[Hit] = []
    for path in sorted(root.glob("daily/*.md"), reverse=True)[:7]:
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                matched = sum(1 for t in terms if t in line)
                if matched and len(line.strip()) > 4:
                    text = _strip_journal_prefix(line)[:200]
                    hits.append(Hit(text, "journal", round(0.55 + 0.12 * (matched - 1), 3),
                                    {"file": path.name, "mtime": path.stat().st_mtime, "matched": matched}))
                    if len(hits) >= limit * 3:
                        return hits
        except Exception:  # noqa: BLE001
            continue
    return hits


def recall(
    query: str,
    limit: int = 5,
    *,
    sources: list[str] | None = None,
    min_matched: int = 1,
) -> list[Hit]:
    """跨层召回并去重。query 为空返回空。

    min_matched：同一条记忆至少要命中几个词才算候选（注入场景建议 2）。
    """
    query = (query or "").strip()
    if not query:
        return []
    limit = max(1, min(10, int(limit or 5)))
    terms = extract_terms(query)
    if not terms:
        return []
    wanted = set(sources or ("episodic", "semantic", "profile", "journal"))

    pool: list[Hit] = []
    if "episodic" in wanted:
        pool += _soul_recall(terms, limit)
    if "semantic" in wanted:
        pool += _iris_recall(terms, limit)
    if "profile" in wanted:
        pool += _profile_recall(terms, limit)
    if "journal" in wanted:
        pool += _journal_recall(terms, limit)

    # 强度门槛：注入场景下宁可不想起，也别想起错的
    if min_matched > 1:
        pool = [h for h in pool if int((h.meta or {}).get("matched") or 0) >= min_matched]

    # 去重：同一句话在多层出现，保留分最高的一条，并记下来源
    best: dict[str, Hit] = {}
    for hit in pool:
        key = fingerprint(hit.text)
        hit.score = round(hit.score * SOURCE_WEIGHT.get(hit.source, 1.0), 3)
        old = best.get(key)
        if old is None:
            best[key] = hit
        else:
            also = old.meta.setdefault("also_in", [])
            if hit.source != old.source and hit.source not in also:
                also.append(hit.source)
            if hit.score > old.score:
                hit.meta["also_in"] = also
                best[key] = hit

    ordered = sorted(best.values(), key=lambda h: (-h.score, len(h.text)))
    return ordered[:limit]


def render(hits: list[Hit], *, budget: int = 600) -> str:
    """渲染成注入块（影子阶段先给命令看，不直接注入）。"""
    if not hits:
        return ""
    lines = ["<memory_digest>", "（以下是我记得的事，按相关度排序）"]
    for hit in hits:
        label = {"episodic": "原话", "semantic": "摘要", "profile": "档案", "journal": "日志"}.get(
            hit.source, hit.source
        )
        line = f"- [{label}] {hit.text}"
        if sum(len(x) for x in lines) + len(line) > budget:
            break
        lines.append(line)
    lines.append("</memory_digest>")
    return "\n".join(lines)


def build_digest(
    query: str,
    *,
    limit: int = 3,
    budget: int = 400,
    min_matched: int = 1,
    sources: tuple[str, ...] = ("semantic", "profile"),
) -> str:
    """给注入用的严格版本：只信事实层（档案+摘要）。

    jieba 分词后单个关键词就有意义（「你喜欢什么」→ 喜欢），所以默认 min_matched=1；
    评测数据见 docs/召回精度.md。它仍是「追加」而不是「替换」：宁可少想起，不想起错的。
    """
    hits = recall(query, limit=limit, sources=list(sources), min_matched=min_matched)
    return render(hits, budget=budget)


def stats() -> dict[str, Any]:
    """各层可召回规模（体检用，不依赖关键词）。"""
    out: dict[str, int] = {}

    soul_db = paths.plugin_data_dir("astrbot_plugin_dafeiyu_soul") / "soul.sqlite3"
    try:
        con = sqlite3.connect(f"file:{soul_db}?mode=ro", uri=True)
        out["episodic"] = int(con.execute("SELECT COUNT(*) FROM memories").fetchone()[0])
        con.close()
    except Exception:  # noqa: BLE001
        out["episodic"] = -1

    iris_db = paths.plugin_data_dir("astrbot_plugin_iris_chat_memory") / "faiss" / "memory_default" / "metadata.db"
    try:
        con = sqlite3.connect(f"file:{iris_db}?mode=ro", uri=True)
        out["semantic"] = int(con.execute("SELECT COUNT(*) FROM memories").fetchone()[0])
        con.close()
    except Exception:  # noqa: BLE001
        out["semantic"] = -1

    root = paths.plugin_data_dir("astrbot_plugin_whale_memory") / "memory"
    try:
        out["profile"] = len([f for f in root.glob("*.md")])
        out["journal"] = len([f for f in root.glob("daily/*.md")])
    except Exception:  # noqa: BLE001
        out["profile"] = -1
        out["journal"] = -1
    return out
