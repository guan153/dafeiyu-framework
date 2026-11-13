# -*- coding: utf-8 -*-
"""SQLite 单写者连接。

为什么要包一层：AstrBot 里多个插件/多个协程同时写一个库，是历史上
「跨插件并发写库」和「FAISS 与 SQLite 脱同步」的根源。这里强制：
- 一个进程内一个连接（WAL 模式，读写不互斥）
- 所有写操作走同一把锁 + 显式事务
- 失败重试（busy_timeout + 少量 sleep 重试）
"""

from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence


class Store:
    """单连接 SQLite 包装，线程安全（粗锁），适合小体量状态存储。"""

    def __init__(self, path: Path | str, timeout: float = 5.0) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._timeout = timeout
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None

    # ── 连接 ────────────────────────────────────────────────
    def connect(self) -> sqlite3.Connection:
        with self._lock:
            if self._conn is None:
                conn = sqlite3.connect(
                    self.path,
                    timeout=self._timeout,
                    check_same_thread=False,
                    isolation_level=None,  # 自己管事务
                )
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA busy_timeout=5000")
                conn.execute("PRAGMA foreign_keys=ON")
                self._conn = conn
            return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                finally:
                    self._conn = None

    # ── 读写 ────────────────────────────────────────────────
    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        conn = self.connect()
        with self._lock:
            return conn.execute(sql, params)

    def executemany(self, sql: str, rows: Sequence[Sequence[Any]]) -> sqlite3.Cursor:
        conn = self.connect()
        with self._lock:
            return conn.executemany(sql, rows)

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self.connect().execute(sql, params).fetchall())

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def scalar(self, sql: str, params: Sequence[Any] = (), default: Any = None) -> Any:
        row = self.query_one(sql, params)
        if row is None:
            return default
        return row[0]

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """显式事务；异常回滚。"""
        conn = self.connect()
        with self._lock:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except Exception:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")

    def execute_retry(
        self, sql: str, params: Sequence[Any] = (), attempts: int = 3
    ) -> sqlite3.Cursor | None:
        """遇到 database is locked 时短暂重试（跨进程场景兜底）。"""
        last: Exception | None = None
        for i in range(attempts):
            try:
                return self.execute(sql, params)
            except sqlite3.OperationalError as exc:  # 常见就是 locked
                last = exc
                if "locked" not in str(exc).lower():
                    raise
                time.sleep(0.08 * (i + 1))
        if last is not None:
            raise last
        return None

    def table_exists(self, name: str) -> bool:
        row = self.query_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
        )
        return row is not None

    def counts(self) -> dict[str, int]:
        """每张表的行数，体检用。"""
        out: dict[str, int] = {}
        tables = self.query("SELECT name FROM sqlite_master WHERE type='table'")
        for row in tables:
            name = row[0]
            out[name] = int(self.scalar(f"SELECT COUNT(*) FROM {name}", default=0) or 0)
        return out


_STORES: dict[str, Store] = {}


def get_store(path: Path | str) -> Store:
    """按路径复用连接，避免同库多连接互相锁。"""
    key = str(Path(path))
    store = _STORES.get(key)
    if store is None:
        store = Store(key)
        _STORES[key] = store
    return store
