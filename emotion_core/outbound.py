# -*- coding: utf-8 -*-
"""统一出站队列：申请 → 仲裁 → 发放 → 回执。

为什么要有它：历史上「主动消息有 7 个源各自直发」，
于是同一分钟她能发三条、深夜也会被叫醒、发失败了没人知道、发过什么自己也记不住。

现在的规矩：
1. 任何主动消息先 `request()` 排队，拿 ticket；
2. `GatePolicy` 统一仲裁：最小间隔、每日上限、静默时段、单源限流、重复内容去重；
3. 发送失败自动重试（指数退避），彻底失败进弱点账本；
4. 每次结果写回执（sent/failed/dropped/expired），供自省与「她记得自己发过」；
5. 进程重启不丢队列（SQLite 持久化，sending 状态标记 unknown，不自动重放）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from . import paths, voicegate
from .bus import get_bus
from .store import Store, get_store

log = logging.getLogger("whale_core.outbound")

class DeliveryReceipt(Protocol):
    """Structural receipt contract without importing a platform dependency."""

    @property
    def state(self) -> str:
        """Return the aggregate delivery state."""
        ...


Sender = Callable[[dict[str, Any]], Awaitable[bool | DeliveryReceipt]]
_STORE: Store | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox (
    id            TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    text          TEXT NOT NULL,
    priority      INTEGER NOT NULL DEFAULT 5,
    dedupe_key    TEXT DEFAULT '',
    created_at    REAL NOT NULL,
    next_attempt  REAL NOT NULL,
    attempts      INTEGER NOT NULL DEFAULT 0,
    max_attempts  INTEGER NOT NULL DEFAULT 3,
    status        TEXT NOT NULL DEFAULT 'pending',
    last_error    TEXT DEFAULT '',
    meta          TEXT DEFAULT '{}',
    expires_at    REAL DEFAULT 0,
    sent_at       REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_outbox_status ON outbox(status, next_attempt);
CREATE INDEX IF NOT EXISTS idx_outbox_dedupe ON outbox(dedupe_key);
"""


@dataclass(slots=True)
class GatePolicy:
    """仲裁策略。默认值偏克制：宁可少发，不可轰炸。"""

    min_interval_sec: float = 90.0        # 全局最小间隔
    per_source_interval: float = 300.0    # 同一来源最小间隔
    daily_cap: int = 40                   # 每日上限
    quiet_hours: tuple[int, int] = (0, 7) # 静默时段 [起, 止)，跨零点也支持
    dedupe_window_sec: float = 1800.0     # 同内容去重窗口
    max_attempts: int = 3

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "GatePolicy":
        data = data or {}
        quiet = data.get("quiet_hours") or [0, 7]
        try:
            quiet_hours = (int(quiet[0]), int(quiet[1]))
        except Exception:
            quiet_hours = (0, 7)
        return cls(
            min_interval_sec=float(data.get("min_interval_sec", 90)),
            per_source_interval=float(data.get("per_source_interval", 300)),
            daily_cap=int(data.get("daily_cap", 40)),
            quiet_hours=quiet_hours,
            dedupe_window_sec=float(data.get("dedupe_window_sec", 1800)),
            max_attempts=int(data.get("max_attempts", 3)),
        )

    def in_quiet_hours(self, now: datetime | None = None) -> bool:
        start, end = self.quiet_hours
        if start == end:
            return False
        hour = (now or datetime.now()).hour
        if start < end:
            return start <= hour < end
        return hour >= start or hour < end


class OutboundQueue:
    def __init__(self, db_path: Path | str | None = None, policy: GatePolicy | None = None) -> None:
        self.store = get_store(db_path or paths.outbound_db())
        self.store.connect().executescript(_SCHEMA)
        self.policy = policy or GatePolicy()
        self._sender: Sender | None = None
        self._task: asyncio.Task | None = None
        self._loop_lock = asyncio.Lock()
        self._stats = {"sent": 0, "failed": 0, "dropped": 0, "expired": 0}
        self._recover()

    # ── 生命周期 ────────────────────────────────────────────
    def _recover(self) -> None:
        """Quarantine interrupted sends instead of replaying uncertain deliveries.

        Returns:
            None.
        """
        # A crashed send may already have reached QQ; never replay it automatically.
        self.store.execute(
            "UPDATE outbox SET status='unknown', last_error='restart_delivery_unknown' "
            "WHERE status='sending'"
        )

    def set_sender(self, sender: Sender) -> None:
        self._sender = sender

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name="whale_core.outbound")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    # ── 申请 ────────────────────────────────────────────────
    def request(
        self,
        text: str,
        *,
        source: str,
        priority: int = 5,
        dedupe_key: str = "",
        ttl: float = 900.0,
        meta: dict[str, Any] | None = None,
        max_attempts: int | None = None,
    ) -> str:
        """入队一条待发消息，返回 ticket id。

        priority 越小越先发（0=最急）。ttl 到期仍没发出去就作废，避免发过时的话。
        """
        text = (text or "").strip()
        ticket = f"{int(time.time() * 1000):x}-{abs(hash((source, text, dedupe_key))) % 0xFFFFFF:06x}"
        now = time.time()

        # 同内容去重：窗口内已有 pending/sent 的同 dedupe_key 就丢弃
        if dedupe_key and self.policy.dedupe_window_sec > 0:
            hit = self.store.query_one(
                "SELECT id,status FROM outbox WHERE dedupe_key=? AND created_at>? AND status IN ('pending','sending','sent','unknown','partial')",
                (dedupe_key, now - self.policy.dedupe_window_sec),
            )
            if hit:
                log.info("出站去重：%s（命中 %s/%s）", dedupe_key, hit["id"], hit["status"])
                self._stats["dropped"] += 1
                return ""

        self.store.execute(
            "INSERT INTO outbox (id,source,text,priority,dedupe_key,created_at,next_attempt,"
            "attempts,max_attempts,status,meta,expires_at) VALUES (?,?,?,?,?,?,?,0,?,?,?,?)",
            (
                ticket, source[:64], text[:4000], int(priority), dedupe_key[:120],
                now, now, max_attempts or self.policy.max_attempts,
                "pending", json.dumps(meta or {}, ensure_ascii=False),
                now + ttl if ttl > 0 else 0,
            ),
        )
        return ticket

    # ── 仲裁 ────────────────────────────────────────────────
    def gate_check(self, candidate: dict[str, Any], now: datetime | None = None) -> tuple[bool, str]:
        """能不能发。返回 (允许, 原因)。原因会记进回执，方便复盘。"""
        now_ts = time.time()
        if self.policy.in_quiet_hours(now):
            return False, "quiet_hours"

        # Proactive delivery requires a readable, fresh shared gate.
        try:
            meta = self._loads(candidate.get("meta") or "{}")
            umo = str(meta.get("umo") or "")
            verdict = voicegate.evaluate(session_id=umo, outlet="proactive", fail_closed=True)
            if verdict.get("allow") is not True:
                return False, f"voice_gate:{verdict.get('code')}"
        except Exception:  # noqa: BLE001
            return False, "voice_gate:unavailable"

        last_any = self.store.scalar(
            "SELECT MAX(sent_at) FROM outbox WHERE status='sent'", default=0
        ) or 0
        if last_any and now_ts - float(last_any) < self.policy.min_interval_sec:
            return False, "min_interval"

        last_same = self.store.scalar(
            "SELECT MAX(sent_at) FROM outbox WHERE status='sent' AND source=?",
            (candidate["source"],), default=0,
        ) or 0
        if last_same and now_ts - float(last_same) < self.policy.per_source_interval:
            return False, "per_source_interval"

        day_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        today = self.store.scalar(
            "SELECT COUNT(*) FROM outbox WHERE status='sent' AND sent_at>=?", (day_start,), default=0
        ) or 0
        if int(today) >= self.policy.daily_cap:
            return False, "daily_cap"

        return True, "ok"

    # ── 发放 ────────────────────────────────────────────────
    def due(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = self.store.query(
            "SELECT * FROM outbox WHERE status='pending' AND next_attempt<=? "
            "AND (expires_at=0 OR expires_at>?) ORDER BY priority ASC, created_at ASC LIMIT ?",
            (time.time(), time.time(), limit),
        )
        return [dict(r) for r in rows]

    async def dispatch_once(self) -> dict[str, Any] | None:
        """试着发一条。返回处理结果，没有可发的返回 None。"""
        async with self._loop_lock:
            # Expired rows are excluded by due(); retire them before selecting work.
            expired = self.store.execute(
                "UPDATE outbox SET status='expired', last_error='ttl' "
                "WHERE status='pending' AND expires_at>0 AND expires_at<=?",
                (time.time(),),
            ).rowcount
            self._stats["expired"] += expired
            if expired:
                get_bus().publish("outbound.expired", {"count": expired}, source="whale_core.outbound")
            for row in self.due():
                now_ts = time.time()
                if row["expires_at"] and now_ts > row["expires_at"]:
                    self._finish(row["id"], "expired", "ttl")
                    self._stats["expired"] += 1
                    continue

                allowed, reason = self.gate_check(row)
                if not allowed:
                    # 闸门不放行：推迟 60 秒再看，不算失败
                    self.store.execute(
                        "UPDATE outbox SET next_attempt=? WHERE id=?", (now_ts + 60, row["id"])
                    )
                    return {"id": row["id"], "result": "deferred", "reason": reason}

                if self._sender is None:
                    self.store.execute("UPDATE outbox SET next_attempt=? WHERE id=?", (now_ts + 30, row["id"]))
                    return {"id": row["id"], "result": "deferred", "reason": "no_sender"}

                self.store.execute("UPDATE outbox SET status='sending' WHERE id=?", (row["id"],))
                payload = {
                    "id": row["id"],
                    "text": row["text"],
                    "source": row["source"],
                    "meta": self._loads(row["meta"]),
                }
                delivery_state = "failed"
                try:
                    result = await self._sender(payload)
                    if isinstance(result, bool):
                        delivery_state = "sent" if result else "failed"
                    else:
                        delivery_state = getattr(result, "state", "unknown")
                        if not isinstance(
                            delivery_state, str
                        ) or delivery_state not in {
                            "sent",
                            "failed",
                            "partial",
                            "unknown",
                            "not_attempted",
                        }:
                            delivery_state = "unknown"
                    ok = delivery_state == "sent"
                    error = "" if ok else f"receipt_{delivery_state}"
                    if result is False:
                        error = "sender_returned_false"
                except Exception as exc:  # noqa: BLE001 —— 发送端异常不能带走队列
                    ok = False
                    error = f"{type(exc).__name__}: {exc}"

                if ok:
                    self._finish(row["id"], "sent", "")
                    self._stats["sent"] += 1
                    return {"id": row["id"], "result": "sent", "source": row["source"]}

                attempts = int(row["attempts"]) + 1
                if delivery_state in {"partial", "unknown"}:
                    # Replaying a chain could duplicate an accepted segment.
                    self._finish(row["id"], delivery_state, error, attempts=attempts)
                    self._stats[delivery_state] = self._stats.get(delivery_state, 0) + 1
                    return {
                        "id": row["id"],
                        "result": delivery_state,
                        "error": error,
                        "source": row["source"],
                    }
                # 超时/未知结果不能重试：消息可能已经送达，重放 = 发两遍。
                # （这条来自头部陪伴模块的踩坑记录：平台 timeout/unknown 绝不自动重放）
                if _is_ambiguous(error):
                    self._finish(row["id"], "unknown", error, attempts=attempts)
                    self._stats["unknown"] = self._stats.get("unknown", 0) + 1
                    return {"id": row["id"], "result": "unknown", "error": error, "source": row["source"]}

                # 没有可用会话这种，重试三次也是白搭
                if _is_permanent(error):
                    self._finish(row["id"], "failed", error, attempts=attempts)
                    self._stats["failed"] += 1
                    return {"id": row["id"], "result": "failed", "error": error, "source": row["source"]}

                if attempts >= int(row["max_attempts"]):
                    self._finish(row["id"], "failed", error, attempts=attempts)
                    self._stats["failed"] += 1
                    return {"id": row["id"], "result": "failed", "error": error, "source": row["source"]}
                backoff = min(300, 15 * (2 ** (attempts - 1)))
                self.store.execute(
                    "UPDATE outbox SET status='pending', attempts=?, next_attempt=?, last_error=? WHERE id=?",
                    (attempts, time.time() + backoff, error[:500], row["id"]),
                )
                log.warning("出站失败(第%d次)，%ds 后重试：%s", attempts, backoff, error)
                return {"id": row["id"], "result": "retry", "error": error, "attempt": attempts}
        return None

    def _finish(self, ticket: str, status: str, error: str, attempts: int | None = None) -> None:
        now = time.time()
        row = self.store.query_one("SELECT source, text FROM outbox WHERE id=?", (ticket,))
        source = row["source"] if row else ""
        if status == "sent":
            self.store.execute(
                "UPDATE outbox SET status='sent', sent_at=?, last_error='' WHERE id=?", (now, ticket)
            )
            # 回执上总线：别人可以据此写记忆（她得记得自己说过什么）
            get_bus().publish(
                "outbound.sent",
                {"id": ticket, "source": source, "text": (row["text"] if row else "")[:500]},
                source="whale_core.outbound",
            )
        else:
            if attempts is None:
                attempts = int(self.store.scalar("SELECT attempts FROM outbox WHERE id=?", (ticket,), default=0) or 0)
            self.store.execute(
                "UPDATE outbox SET status=?, attempts=?, last_error=? WHERE id=?",
                (status, attempts, error[:500], ticket),
            )
            get_bus().publish(
                "outbound.failed",
                {"id": ticket, "source": source, "status": status, "error": error[:300]},
                source="whale_core.outbound",
            )

    async def _run(self) -> None:
        while True:
            try:
                await self.dispatch_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.exception("出站循环异常：%s", exc)
            await asyncio.sleep(5)

    # ── 观测 ────────────────────────────────────────────────
    def stats(self) -> dict[str, Any]:
        day_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        by_status = {
            r["status"]: r["c"] for r in self.store.query("SELECT status, COUNT(*) c FROM outbox GROUP BY status")
        }
        return {
            "by_status": by_status,
            "pending": by_status.get("pending", 0),
            "sent_total": by_status.get("sent", 0),
            "sent_today": int(self.store.scalar(
                "SELECT COUNT(*) FROM outbox WHERE status='sent' AND sent_at>=?", (day_start,), default=0) or 0),
            "failed_total": by_status.get("failed", 0),
            "last_success_ts": float(self.store.scalar("SELECT MAX(sent_at) FROM outbox WHERE status='sent'", default=0) or 0),
            "session": dict(self._stats),
            "policy": {
                "min_interval_sec": self.policy.min_interval_sec,
                "per_source_interval": self.policy.per_source_interval,
                "daily_cap": self.policy.daily_cap,
                "quiet_hours": list(self.policy.quiet_hours),
            },
        }

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.store.query("SELECT * FROM outbox ORDER BY created_at DESC LIMIT ?", (limit,))
        return [
            {
                "id": r["id"], "source": r["source"], "text": (r["text"] or "")[:80],
                "status": r["status"], "attempts": r["attempts"], "error": r["last_error"],
                "created_at": r["created_at"], "sent_at": r["sent_at"],
            }
            for r in rows
        ]

    @staticmethod
    def _loads(raw: str) -> dict[str, Any]:
        try:
            return json.loads(raw or "{}")
        except Exception:
            return {}


_QUEUE: OutboundQueue | None = None


def _is_ambiguous(error: str) -> bool:
    """结果未知 vs 明确失败。

    超时 = 我们不知道对方收没收到 → 不能重试（否则可能发两遍）。
    连接被拒/地址错误 = 明确没发出去 → 可以重试。
    """
    text = (error or "").lower()
    if "timeout" in text or "timed out" in text:
        return True
    return "unknown" in text and "connect" not in text


def _is_permanent(error: str) -> bool:
    """重试也没用的错误：没有匹配的会话/平台，等下一轮再说。"""
    text = (error or "").lower()
    return (
        "sender_returned_false" in text
        or "no_platform" in text
        or text in {"receipt_failed", "receipt_not_attempted"}
    )


def get_outbound() -> OutboundQueue:
    global _QUEUE
    if _QUEUE is None:
        _QUEUE = OutboundQueue()
    return _QUEUE


def read_stats(db_path: Path | str | None = None) -> dict[str, Any]:
    """只读统计：不创建队列、不做恢复，给自省/看板用（避免副作用）。"""
    store = get_store(db_path or paths.outbound_db())
    try:
        store.connect().executescript(_SCHEMA)
    except Exception:  # noqa: BLE001 —— 库还没建也算「空」
        return {"by_status": {}, "pending": 0, "sent_total": 0, "sent_today": 0,
                "failed_total": 0, "last_success_ts": 0.0, "session": {}, "policy": {}}
    day_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    day_ago = time.time() - 86400
    by_status = {r["status"]: r["c"] for r in store.query("SELECT status, COUNT(*) c FROM outbox GROUP BY status")}
    sent_24h = int(store.scalar(
        "SELECT COUNT(*) FROM outbox WHERE status='sent' AND sent_at>=?", (day_ago,), default=0) or 0)
    failed_24h = int(
        store.scalar(
            "SELECT COUNT(*) FROM outbox WHERE status IN ('failed','unknown','partial') AND created_at>=?",
            (day_ago,),
            default=0,
        )
        or 0
    )
    return {
        "by_status": by_status,
        "pending": by_status.get("pending", 0),
        "sent_total": by_status.get("sent", 0),
        "sent_today": int(store.scalar(
            "SELECT COUNT(*) FROM outbox WHERE status='sent' AND sent_at>=?", (day_start,), default=0) or 0),
        "sent_24h": sent_24h,
        "failed_24h": failed_24h,
        "failed_total": by_status.get("failed", 0),
        "last_success_ts": float(store.scalar("SELECT MAX(sent_at) FROM outbox WHERE status='sent'", default=0) or 0),
        "session": {},
        "policy": {},
    }
