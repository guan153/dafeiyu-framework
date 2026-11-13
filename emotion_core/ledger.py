# -*- coding: utf-8 -*-
"""弱点账本（Weakness Ledger）——v3 的核心资产。

一句话：**她的不足必须被记下来，才算真的知道。**

设计要点
- 一条弱点是稳定的（key 去重），重复出现只累加次数、刷新时间，不产生垃圾条目；
- 每条弱点有状态机（open → triage → proposed → fixing → verified / regressed / wontfix），
  改状态留痕，可回放「这个问题我们处理过没有」；
- 有分（score）：严重度 × 频次 × 新鲜度，用来排序「先修哪个」；
- 有证据（evidence）：报错样本、探针结果，不靠印象。

与其他模块的关系
    logscan/health/probe  ——产生候选弱点（bump）
    introspect            ——读账本算分、生成提案
    emotion/introspect    ——把 top 弱点注入 prompt，让她能诚实说「这个我还不会」
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from . import paths
from .store import Store, get_store

STATUSES = ("open", "triage", "proposed", "fixing", "verified", "wontfix", "regressed")
STATUS_LABEL = {
    "open": "待处理",
    "triage": "已分诊",
    "proposed": "已有提案",
    "fixing": "修复中",
    "verified": "已验证",
    "wontfix": "不修",
    "regressed": "回归了",
}
CATEGORIES = ("bug", "gap", "conflict", "perf", "ux", "safety", "doc")
CATEGORY_LABEL = {
    "bug": "缺陷",
    "gap": "能力缺口",
    "conflict": "模块冲突",
    "perf": "性能",
    "ux": "体验",
    "safety": "安全",
    "doc": "文档",
}
SEVERITIES = ("P0", "P1", "P2", "P3")
SEVERITY_WEIGHT = {"P0": 40.0, "P1": 20.0, "P2": 8.0, "P3": 3.0}
# 这些状态下不计分（已解决或明确不修的，不该继续压着她的自评）
CLOSED_STATUSES = ("verified", "wontfix")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS weakness (
    id           TEXT PRIMARY KEY,
    key          TEXT NOT NULL UNIQUE,
    title        TEXT NOT NULL,
    category     TEXT NOT NULL DEFAULT 'bug',
    severity     TEXT NOT NULL DEFAULT 'P2',
    plugin       TEXT DEFAULT '',
    symptom      TEXT DEFAULT '',
    root_cause   TEXT DEFAULT '',
    evidence     TEXT DEFAULT '{}',
    status       TEXT NOT NULL DEFAULT 'open',
    occurrences  INTEGER NOT NULL DEFAULT 1,
    first_seen   REAL NOT NULL,
    last_seen    REAL NOT NULL,
    source       TEXT DEFAULT 'manual',
    proposal_id  TEXT DEFAULT '',
    tags         TEXT DEFAULT '',
    note         TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_weakness_status ON weakness(status);
CREATE INDEX IF NOT EXISTS idx_weakness_plugin ON weakness(plugin);

CREATE TABLE IF NOT EXISTS weakness_event (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    key       TEXT NOT NULL,
    ts        REAL NOT NULL,
    kind      TEXT NOT NULL,
    detail    TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_event_key ON weakness_event(key);

CREATE TABLE IF NOT EXISTS proposal (
    id          TEXT PRIMARY KEY,
    key         TEXT NOT NULL,
    title       TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'manual',
    risk        TEXT NOT NULL DEFAULT 'medium',
    plan        TEXT NOT NULL DEFAULT '',
    rollback    TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'draft',
    created_at  REAL NOT NULL,
    decided_at  REAL,
    result      TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_proposal_key ON proposal(key);
"""


def _now() -> float:
    return time.time()


def _slug(text: str) -> str:
    text = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", text.strip().lower())
    return text.strip("-")[:64] or hashlib.md5(text.encode("utf-8")).hexdigest()[:12]


def make_key(plugin: str, category: str, title: str) -> str:
    """稳定的去重键：同一个模块、同一类问题的同义标题落到同一条。"""
    return f"{plugin or 'system'}:{category}:{_slug(title)}"


@dataclass(slots=True)
class Weakness:
    key: str
    title: str
    category: str = "bug"
    severity: str = "P2"
    plugin: str = ""
    symptom: str = ""
    root_cause: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    status: str = "open"
    occurrences: int = 1
    first_seen: float = 0.0
    last_seen: float = 0.0
    source: str = "manual"
    proposal_id: str = ""
    tags: str = ""
    note: str = ""
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        data = {
            "key": self.key,
            "title": self.title,
            "category": self.category,
            "severity": self.severity,
            "plugin": self.plugin,
            "symptom": self.symptom,
            "root_cause": self.root_cause,
            "evidence": self.evidence,
            "status": self.status,
            "occurrences": self.occurrences,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "source": self.source,
            "proposal_id": self.proposal_id,
            "tags": self.tags,
            "note": self.note,
            "score": self.score,
        }
        return data

    def line(self) -> str:
        """给注入块用的一行摘要（短，不占预算）。"""
        state = STATUS_LABEL.get(self.status, self.status)
        return (
            f"[{self.severity}｜{CATEGORY_LABEL.get(self.category, self.category)}｜{state}] "
            f"{self.title}" + (f"（出现 {self.occurrences} 次）" if self.occurrences > 1 else "")
        )


def compute_score(severity: str, occurrences: int, last_seen: float, status: str, now: float | None = None) -> float:
    """解释得清的排序分：越严重、越常犯、越新鲜，分越高。"""
    if status in CLOSED_STATUSES:
        return 0.0
    now = now or _now()
    base = SEVERITY_WEIGHT.get(severity, 8.0)
    freq = min(3.0, 1.0 + math.log2(1 + max(0, occurrences - 1)))
    age_days = max(0.0, (now - last_seen) / 86400.0)
    if age_days <= 7:
        fresh = 1.0
    elif age_days <= 30:
        fresh = 0.7
    elif age_days <= 90:
        fresh = 0.4
    else:
        fresh = 0.2
    status_factor = {"proposed": 0.85, "fixing": 0.6, "regressed": 1.2}.get(status, 1.0)
    return round(base * freq * fresh * status_factor, 2)


class Ledger:
    """弱点账本。单写者（内核），其他模块通过 bus 或本类实例写入。"""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.store: Store = get_store(db_path or paths.ledger_db())
        self._ensure()

    def _ensure(self) -> None:
        conn = self.store.connect()
        conn.executescript(_SCHEMA)

    # ── 写入 ────────────────────────────────────────────────
    def record(
        self,
        title: str,
        *,
        plugin: str = "",
        category: str = "bug",
        severity: str = "P2",
        symptom: str = "",
        root_cause: str = "",
        evidence: dict[str, Any] | None = None,
        source: str = "manual",
        tags: str = "",
        key: str | None = None,
        occurrence_detail: str = "",
    ) -> tuple[str, bool]:
        """写一条弱点；已存在则累加次数并刷新证据。

        返回 (key, is_new)。
        """
        if category not in CATEGORIES:
            category = "bug"
        if severity not in SEVERITIES:
            severity = "P2"
        key = key or make_key(plugin, category, title)
        now = _now()
        existing = self.get(key)
        if existing is None:
            wid = hashlib.md5(key.encode("utf-8")).hexdigest()[:16]
            with self.store.transaction() as conn:
                conn.execute(
                    "INSERT INTO weakness (id,key,title,category,severity,plugin,symptom,root_cause,"
                    "evidence,status,occurrences,first_seen,last_seen,source,tags) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        wid, key, title[:200], category, severity, plugin[:80], symptom[:1000],
                        root_cause[:1000], json.dumps(evidence or {}, ensure_ascii=False),
                        "open", 1, now, now, source, tags,
                    ),
                )
                conn.execute(
                    "INSERT INTO weakness_event (key,ts,kind,detail) VALUES (?,?,?,?)",
                    (key, now, "created", f"{severity}/{category} by {source}"),
                )
            return key, True

        merged = dict(existing.evidence)
        merged.update(evidence or {})
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE weakness SET occurrences=occurrences+1, last_seen=?, evidence=?, "
                "severity=?, "
                "status=CASE WHEN status IN ('verified','wontfix') THEN 'regressed' ELSE status END "
                "WHERE key=?",
                (now, json.dumps(merged, ensure_ascii=False), severity, key),
            )
            conn.execute(
                "INSERT INTO weakness_event (key,ts,kind,detail) VALUES (?,?,?,?)",
                (key, now, "occurrence", occurrence_detail[:500]),
            )
        return key, False

    def bump(self, key: str, detail: str = "", *, evidence: dict[str, Any] | None = None) -> bool:
        """给已知弱点 +1 次；不存在返回 False（调用方决定是否新建）。"""
        now = _now()
        existing = self.get(key)
        if existing is None:
            return False
        merged = dict(existing.evidence)
        merged.update(evidence or {})
        merged["last_detail"] = detail[:500]
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE weakness SET occurrences=occurrences+1, last_seen=?, evidence=?, "
                "status=CASE WHEN status IN ('verified','wontfix') THEN 'regressed' ELSE status END "
                "WHERE key=?",
                (now, json.dumps(merged, ensure_ascii=False), key),
            )
            conn.execute(
                "INSERT INTO weakness_event (key,ts,kind,detail) VALUES (?,?,?,?)",
                (key, now, "occurrence", detail[:500]),
            )
        return True

    def set_status(self, key: str, status: str, *, note: str = "") -> bool:
        if status not in STATUSES:
            raise ValueError(f"未知状态: {status}")
        now = _now()
        with self.store.transaction() as conn:
            cur = conn.execute("UPDATE weakness SET status=?, note=? WHERE key=?", (status, note[:500], key))
            if cur.rowcount == 0:
                return False
            conn.execute(
                "INSERT INTO weakness_event (key,ts,kind,detail) VALUES (?,?,?,?)",
                (key, now, f"status:{status}", note[:500]),
            )
        return True

    # ── 读取 ────────────────────────────────────────────────
    def get(self, key: str) -> Weakness | None:
        row = self.store.query_one("SELECT * FROM weakness WHERE key=?", (key,))
        return self._row_to(row) if row else None

    def list(
        self,
        *,
        status: str | Iterable[str] | None = None,
        plugin: str | None = None,
        category: str | None = None,
        limit: int = 200,
    ) -> list[Weakness]:
        sql = "SELECT * FROM weakness WHERE 1=1"
        params: list[Any] = []
        if status:
            statuses = [status] if isinstance(status, str) else list(status)
            sql += f" AND status IN ({','.join('?' * len(statuses))})"
            params.extend(statuses)
        if plugin:
            sql += " AND plugin=?"
            params.append(plugin)
        if category:
            sql += " AND category=?"
            params.append(category)
        sql += " ORDER BY last_seen DESC LIMIT ?"
        params.append(limit)
        return [self._row_to(r) for r in self.store.query(sql, params)]

    def top(self, n: int = 5, *, include_closed: bool = False) -> list[Weakness]:
        """按分数取最该处理的 n 条。"""
        items = self.list(status=None if include_closed else [s for s in STATUSES if s not in CLOSED_STATUSES], limit=500)
        for item in items:
            item.score = compute_score(item.severity, item.occurrences, item.last_seen, item.status)
        items.sort(key=lambda w: w.score, reverse=True)
        return items[:n]

    def stats(self) -> dict[str, Any]:
        by_status = {
            r["status"]: r["c"] for r in self.store.query("SELECT status, COUNT(*) c FROM weakness GROUP BY status")
        }
        by_category = {
            r["category"]: r["c"] for r in self.store.query("SELECT category, COUNT(*) c FROM weakness GROUP BY category")
        }
        by_severity = {
            r["severity"]: r["c"] for r in self.store.query("SELECT severity, COUNT(*) c FROM weakness GROUP BY severity")
        }
        total = sum(by_status.values())
        open_items = [w for w in self.list(status=[s for s in STATUSES if s not in CLOSED_STATUSES], limit=1000)]
        for item in open_items:
            item.score = compute_score(item.severity, item.occurrences, item.last_seen, item.status)
        open_score = round(sum(w.score for w in open_items), 2)
        return {
            "total": total,
            "by_status": by_status,
            "by_category": by_category,
            "by_severity": by_severity,
            "open_count": len(open_items),
            "open_score": open_score,
            "top": [w.to_dict() for w in sorted(open_items, key=lambda x: x.score, reverse=True)[:10]],
        }

    def history(self, key: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.store.query(
            "SELECT ts,kind,detail FROM weakness_event WHERE key=? ORDER BY ts DESC LIMIT ?", (key, limit)
        )
        return [{"ts": r["ts"], "kind": r["kind"], "detail": r["detail"]} for r in rows]

    # ── 提案 ────────────────────────────────────────────────
    def add_proposal(
        self,
        key: str,
        title: str,
        *,
        kind: str = "manual",
        risk: str = "medium",
        plan: str = "",
        rollback: str = "",
    ) -> str:
        pid = hashlib.md5(f"{key}:{title}:{_now()}".encode("utf-8")).hexdigest()[:12]
        with self.store.transaction() as conn:
            conn.execute(
                "INSERT INTO proposal (id,key,title,kind,risk,plan,rollback,status,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (pid, key, title[:200], kind, risk, plan, rollback, "draft", _now()),
            )
            conn.execute("UPDATE weakness SET proposal_id=? WHERE key=?", (pid, key))
        self.set_status(key, "proposed", note=f"提案 {pid}: {title}")
        return pid

    def proposals(self, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if status:
            rows = self.store.query(
                "SELECT * FROM proposal WHERE status=? ORDER BY created_at DESC LIMIT ?", (status, limit)
            )
        else:
            rows = self.store.query("SELECT * FROM proposal ORDER BY created_at DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    def decide_proposal(self, proposal_id: str, status: str, *, result: str = "") -> bool:
        with self.store.transaction() as conn:
            cur = conn.execute(
                "UPDATE proposal SET status=?, decided_at=?, result=? WHERE id=?",
                (status, _now(), result[:1000], proposal_id),
            )
            return cur.rowcount > 0

    # ── 维护 ────────────────────────────────────────────────
    def prune_events(self, keep_days: int = 90) -> int:
        cutoff = _now() - keep_days * 86400
        cur = self.store.execute("DELETE FROM weakness_event WHERE ts < ? AND kind='occurrence'", (cutoff,))
        return cur.rowcount

    @staticmethod
    def _row_to(row: Any) -> Weakness:
        try:
            evidence = json.loads(row["evidence"] or "{}")
        except Exception:
            evidence = {}
        return Weakness(
            key=row["key"],
            title=row["title"],
            category=row["category"],
            severity=row["severity"],
            plugin=row["plugin"] or "",
            symptom=row["symptom"] or "",
            root_cause=row["root_cause"] or "",
            evidence=evidence,
            status=row["status"],
            occurrences=int(row["occurrences"]),
            first_seen=float(row["first_seen"]),
            last_seen=float(row["last_seen"]),
            source=row["source"] or "manual",
            proposal_id=row["proposal_id"] or "",
            tags=row["tags"] or "",
            note=row["note"] or "",
        )


_LEDGER: Ledger | None = None


def get_ledger() -> Ledger:
    global _LEDGER
    if _LEDGER is None:
        _LEDGER = Ledger()
    return _LEDGER
