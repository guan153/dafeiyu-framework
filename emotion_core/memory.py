# -*- coding: utf-8 -*-
"""记忆分层与写入契约（MemoryHub）。

先说清楚为什么不是「合并成一个库」：
本机五套记忆存的根本不是同一种东西——

    astrbot_core  原始对话流水（platform_message_history，1543 条）
    soul          情景记忆：原始话语 + 重要性 + 命中次数（265 条）
    iris          语义记忆：LLM 摘要 + 向量 + 知识图谱（58 条 / 60 节点）
    brain         当日摘要（jsonl，按天）
    memoryflow    召回排序层（从 iris 派生，不是独立记忆）

把它们硬合并会丢信息（摘要丢了原话，原话没有向量）。
正确做法是**按层分工，每层一个写者**，再加一个统一召回入口。

本模块负责三件事（v3.1 只做审计，不改任何人的写入行为）：
1. 声明每层有哪些 provider、谁在写；
2. 普查：谁真的在写、写多少、多久没写、有没有废弃；
3. 查重：跨库文本指纹比对，量化「重复记忆」到底有多少。

风险控制：本模块只读。要改写入行为，先看普查报告，再由人拍板。
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from . import paths
from .store import get_store

# 记忆分层：每层必须有且只有一个写者
LAYERS: dict[str, str] = {
    "raw": "原始对话流水",
    "episodic": "情景记忆（原话+重要性）",
    "semantic": "语义记忆（摘要+向量+图谱）",
    "profile": "档案与稳定事实（用户画像/项目约定/每日日志）",
    "digest": "当日摘要",
    "index": "召回索引（派生，不算事实源）",
}


@dataclass
class Provider:
    name: str
    layer: str
    role: str
    path: Path
    writer: bool = True
    kind: str = "sqlite"          # sqlite / jsonl / json / dir
    table: str = ""
    time_column: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["path"] = str(self.path)
        return data


def _norm(text: str) -> str:
    """归一化：去标点空白、转小写，用于查重。"""
    return re.sub(r"[\s\W_]+", "", str(text or "").lower())


def fingerprint(text: str) -> str:
    return hashlib.md5(_norm(text)[:120].encode("utf-8")).hexdigest()[:16]


class MemoryHub:
    def __init__(self, providers: list[Provider] | None = None) -> None:
        self.providers: list[Provider] = providers or self.default_providers()
        self._audit = get_store(paths.core_dir() / "memory_audit.sqlite3")
        self._audit.connect().executescript(
            """
            CREATE TABLE IF NOT EXISTS census (
                ts REAL NOT NULL,
                provider TEXT NOT NULL,
                layer TEXT,
                records INTEGER,
                last_write REAL,
                stale_sec REAL,
                note TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_census_ts ON census(ts);
            """
        )

    # ── 默认 provider 表（本机实况） ────────────────────────
    @staticmethod
    def default_providers() -> list[Provider]:
        data = paths.data_dir()
        pd = data / "plugin_data"
        return [
            Provider(
                name="astrbot_core", layer="raw", role="AstrBot 内置对话历史",
                path=data / "data_v4.db", writer=True, kind="sqlite",
                table="platform_message_history", time_column="", note="框架写入，不可关",
            ),
            Provider(
                name="soul", layer="episodic", role="情景记忆（原话+重要性）",
                path=pd / "astrbot_plugin_dafeiyu_soul" / "soul.sqlite3", writer=True,
                kind="sqlite", table="memories", time_column="created",
            ),
            Provider(
                name="iris", layer="semantic", role="语义记忆（摘要+向量+图谱）",
                path=pd / "astrbot_plugin_iris_chat_memory" / "faiss" / "memory_default" / "metadata.db",
                writer=True, kind="sqlite", table="memories", time_column="timestamp",
            ),
            Provider(
                name="brain", layer="digest", role="当日摘要",
                path=pd / "astrbot_plugin_whale_brain" / "brain_day_memory.jsonl", writer=True,
                kind="jsonl",
            ),
            Provider(
                name="memoryflow", layer="index", role="召回排序层（派生自 iris）",
                path=pd / "astrbot_plugin_whale_memoryflow" / "index.json", writer=True,
                kind="json", note="派生层，不算事实源；重建即可",
            ),
            Provider(
                name="whale_memory", layer="profile", role="档案与稳定事实（USER/KEY/每日日志）",
                path=pd / "astrbot_plugin_whale_memory" / "memory", writer=True, kind="dir",
                note="文件式档案（markdown），不是逐条记忆",
            ),
        ]

    # ── 普查 ────────────────────────────────────────────────
    def census(self) -> dict[str, Any]:
        now = time.time()
        rows: list[dict[str, Any]] = []
        for p in self.providers:
            item = {
                "name": p.name, "layer": p.layer, "layer_label": LAYERS.get(p.layer, p.layer),
                "role": p.role, "kind": p.kind, "writer": p.writer,
                "path": str(p.path), "exists": p.path.exists(),
                "records": 0, "last_write": 0.0, "stale_sec": 0.0,
                "size_kb": 0.0, "note": p.note, "error": "",
            }
            try:
                item.update(self._inspect(p))
            except Exception as exc:  # noqa: BLE001
                item["error"] = f"{type(exc).__name__}: {exc}"
            item["stale_sec"] = round(now - item["last_write"], 1) if item["last_write"] else 0.0
            rows.append(item)
            self._audit.execute(
                "INSERT INTO census (ts,provider,layer,records,last_write,stale_sec,note) VALUES (?,?,?,?,?,?,?)",
                (now, p.name, p.layer, item["records"], item["last_write"], item["stale_sec"], item["error"] or p.note),
            )
        return {
            "ts": now,
            "providers": rows,
            "layers": {k: {"label": v, "providers": [r["name"] for r in rows if r["layer"] == k]} for k, v in LAYERS.items()},
            "total_records": sum(r["records"] for r in rows if r["layer"] != "index"),
        }

    def _inspect(self, p: Provider) -> dict[str, Any]:
        out = {"records": 0, "last_write": 0.0, "size_kb": 0.0}
        if not p.path.exists():
            return out
        if p.kind == "dir":
            files = [f for f in p.path.rglob("*") if f.is_file()]
            out["records"] = len(files)
            out["size_kb"] = round(sum(f.stat().st_size for f in files) / 1024, 1)
            out["last_write"] = max((f.stat().st_mtime for f in files), default=0.0)
            return out

        out["size_kb"] = round(p.path.stat().st_size / 1024, 1)
        out["last_write"] = p.path.stat().st_mtime

        if p.kind == "sqlite" and p.table:
            con = sqlite3.connect(f"file:{p.path}?mode=ro", uri=True)
            try:
                out["records"] = int(con.execute(f'SELECT COUNT(*) FROM "{p.table}"').fetchone()[0])
                if p.time_column:
                    val = con.execute(f'SELECT MAX("{p.time_column}") FROM "{p.table}"').fetchone()[0]
                    ts = _parse_ts(val)
                    if ts:
                        out["last_write"] = max(out["last_write"], ts)
            finally:
                con.close()
        elif p.kind == "jsonl" and p.path.exists():
            with p.path.open("r", encoding="utf-8", errors="replace") as fh:
                out["records"] = sum(1 for _ in fh)
        elif p.kind == "json" and p.path.exists():
            try:
                data = json.loads(p.path.read_text(encoding="utf-8", errors="replace"))
                if isinstance(data, dict):
                    out["records"] = len(data.get("items") or data.get("memories") or data)
                elif isinstance(data, list):
                    out["records"] = len(data)
            except Exception:
                out["records"] = 0
        return out

    # ── 查重 ────────────────────────────────────────────────
    def duplicates(self, sample: int = 400) -> dict[str, Any]:
        """跨库文本查重：同一句话被两套记忆各记一遍有多少。"""
        buckets: dict[str, list[dict[str, str]]] = {}
        for p in self.providers:
            if p.kind != "sqlite" or p.layer == "raw":
                continue
            if not p.path.exists():
                continue
            try:
                texts = self._texts(p, sample)
            except Exception:  # noqa: BLE001
                continue
            for text in texts:
                if len(_norm(text)) < 6:
                    continue
                buckets.setdefault(fingerprint(text), []).append({"provider": p.name, "text": text[:80]})

        dups = {k: v for k, v in buckets.items() if len({i["provider"] for i in v}) > 1}
        same = {k: v for k, v in buckets.items() if len(v) > 1}
        return {
            "cross_provider_duplicates": len(dups),
            "same_provider_duplicates": len(same),
            "samples": list(dups.values())[:5],
            "checked_texts": sum(len(v) for v in buckets.values()),
        }

    def _texts(self, p: Provider, limit: int) -> list[str]:
        con = sqlite3.connect(f"file:{p.path}?mode=ro", uri=True)
        try:
            cols = {
                "soul": ("text", "memories"),
                "iris": ("content", "memories"),
            }.get(p.name, ("text", p.table))
            col, table = cols
            rows = con.execute(f'SELECT "{col}" FROM "{table}" ORDER BY rowid DESC LIMIT ?', (limit,)).fetchall()
            return [r[0] for r in rows if r[0]]
        finally:
            con.close()

    # ── 契约检查 ────────────────────────────────────────────
    def contract(self) -> dict[str, Any]:
        """每层只能有一个写者；多写者就是冲突，进账本。"""
        violations: list[dict[str, Any]] = []
        for layer, label in LAYERS.items():
            writers = [p.name for p in self.providers if p.layer == layer and p.writer]
            if len(writers) > 1:
                violations.append({
                    "layer": layer, "label": label, "writers": writers,
                    "reason": "同一层有多个写者，语义会漂移",
                })
        return {
            "layers": {
                layer: {
                    "label": label,
                    "writers": [p.name for p in self.providers if p.layer == layer and p.writer],
                    "readers_allowed": "全部",
                }
                for layer, label in LAYERS.items()
            },
            "violations": violations,
            "ok": not violations,
        }

    # ── 报告与入库 ──────────────────────────────────────────
    def report(self) -> dict[str, Any]:
        census = self.census()
        dups = self.duplicates()
        contract = self.contract()
        return {"census": census, "duplicates": dups, "contract": contract}

    def ingest_findings(self, ledger: Any) -> list[str]:
        """把普查结论写进弱点账本。

        诚实原则：分层不是问题，别为了凑数把「分层」写成「冲突」。
        当前真实的病是：五套各自注入、没有统一召回入口。
        """
        census = self.census()
        dups = self.duplicates()
        contract = self.contract()
        touched: list[str] = []

        facts = [p for p in census["providers"] if p["layer"] != "index" and p["records"] > 0]
        if len(facts) >= 3:
            key = "memory:no_recall_gateway"
            if ledger.get(key) is None:
                ledger.record(
                    "五套记忆各自注入，没有统一召回入口",
                    category="conflict", severity="P2",
                    root_cause="每层都由自己的模块在 on_llm_request 里插一段，预算与去重各自为政",
                    symptom="同一轮可能同时注入档案/摘要/原话/向量召回，互相挤占且可能矛盾",
                    evidence={"providers": [p["name"] for p in facts], "total_records": census["total_records"]},
                    source="memory_census", key=key,
                )
            touched.append(key)

        for item in census["providers"]:
            if item["writer"] and item["records"] > 0 and item["stale_sec"] > 7 * 86400:
                key = f"memory:stale:{item['name']}"
                if ledger.get(key) is None:
                    ledger.record(
                        f"记忆库长期未写入：{item['name']}（{int(item['stale_sec'] / 86400)} 天）",
                        plugin=item["name"], category="conflict", severity="P3",
                        symptom=f"最后写入 {datetime.fromtimestamp(item['last_write']):%m-%d %H:%M}",
                        source="memory_census", key=key,
                    )
                touched.append(key)

        if dups["cross_provider_duplicates"] > 0:
            key = "memory:cross_dup"
            if ledger.get(key) is None:
                ledger.record(
                    "跨库重复记忆",
                    category="conflict", severity="P3",
                    symptom=f"抽样发现 {dups['cross_provider_duplicates']} 条同一内容被两套记忆各记一遍",
                    evidence={"samples": dups["samples"]}, source="memory_census", key=key,
                )
            touched.append(key)

        for v in contract["violations"]:
            key = f"memory:multi_writer:{v['layer']}"
            if ledger.get(key) is None:
                ledger.record(
                    f"记忆层多写者：{v['label']}",
                    category="conflict", severity="P2",
                    symptom="同层多个写者，语义会漂移：" + "、".join(v["writers"]),
                    source="memory_census", key=key,
                )
            touched.append(key)

        return touched

    def history(self, days: int = 7) -> list[dict[str, Any]]:
        cutoff = time.time() - days * 86400
        rows = self._audit.query(
            "SELECT ts,provider,records,stale_sec,note FROM census WHERE ts>? ORDER BY ts DESC LIMIT 200", (cutoff,)
        )
        return [dict(r) for r in rows]


def _parse_ts(value: Any) -> float:
    """时间列可能是 epoch、ISO 字符串或 None。"""
    if value is None:
        return 0.0
    try:
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip()
        if not text:
            return 0.0
        if text.replace(".", "", 1).isdigit():
            return float(text)
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except Exception:  # noqa: BLE001
        return 0.0


_HUB: MemoryHub | None = None


def get_memory_hub() -> MemoryHub:
    global _HUB
    if _HUB is None:
        _HUB = MemoryHub()
    return _HUB
