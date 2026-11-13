# -*- coding: utf-8 -*-
"""进程内事件总线。

要解决的老问题：插件之间靠「读对方的状态文件」耦合——文件格式一变就集体失灵，
而且谁都能写，最后没人知道事实是谁定的。

新约定：
- 写事实的插件 `publish("emotion.changed", payload)`
- 关心的插件 `subscribe("emotion.*", handler)`
- 总线只保证「送到」，不保证顺序跨主题；处理器异常被隔离，永远不影响发布者。

同时保留最近 N 条事件（环形缓冲），供自省引擎判断「谁在狂发」。
"""

from __future__ import annotations

import asyncio
import fnmatch
import inspect
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

log = logging.getLogger("whale_core.bus")

Handler = Callable[["Event"], Any | Awaitable[Any]]


@dataclass(slots=True)
class Event:
    topic: str
    payload: dict[str, Any] = field(default_factory=dict)
    source: str = ""
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "source": self.source,
            "ts": self.ts,
            "payload": self.payload,
        }


@dataclass(slots=True)
class _Sub:
    pattern: str
    handler: Handler
    source: str = ""


class Bus:
    """轻量事件总线；同步处理器直接调用，异步处理器排队成 task。"""

    def __init__(self, history: int = 256) -> None:
        self._subs: list[_Sub] = []
        self._history: deque[Event] = deque(maxlen=history)
        self._tasks: set[asyncio.Task] = set()
        self._publish_counts: dict[str, int] = {}
        self._errors: list[dict[str, Any]] = []

    # ── 订阅 ────────────────────────────────────────────────
    def subscribe(self, pattern: str, handler: Handler, *, source: str = "") -> None:
        self._subs.append(_Sub(pattern=pattern, handler=handler, source=source))

    def unsubscribe(self, pattern: str, handler: Handler) -> bool:
        before = len(self._subs)
        self._subs = [s for s in self._subs if not (s.pattern == pattern and s.handler is handler)]
        return len(self._subs) != before

    def subscriber_count(self) -> int:
        return len(self._subs)

    # ── 发布 ────────────────────────────────────────────────
    def publish(self, topic: str, payload: dict[str, Any] | None = None, *, source: str = "") -> Event:
        event = Event(topic=topic, payload=dict(payload or {}), source=source)
        self._history.append(event)
        self._publish_counts[topic] = self._publish_counts.get(topic, 0) + 1

        for sub in list(self._subs):
            if not fnmatch.fnmatchcase(topic, sub.pattern):
                continue
            self._invoke(sub, event)
        return event

    def _invoke(self, sub: _Sub, event: Event) -> None:
        try:
            result = sub.handler(event)
        except Exception as exc:  # noqa: BLE001 —— 处理器炸了不能拖垮总线
            self._record_error(sub, event, exc)
            return

        if inspect.isawaitable(result):
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                # 没有事件循环：同步等一次，避免「事件悄悄丢了」
                try:
                    asyncio.run(result)  # type: ignore[arg-type]
                except Exception as exc:  # noqa: BLE001
                    self._record_error(sub, event, exc)
                return
            task = loop.create_task(self._await_handler(result, sub, event))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _await_handler(self, awaitable: Awaitable[Any], sub: _Sub, event: Event) -> None:
        try:
            await awaitable
        except Exception as exc:  # noqa: BLE001
            self._record_error(sub, event, exc)

    def _record_error(self, sub: _Sub, event: Event, exc: BaseException) -> None:
        item = {
            "topic": event.topic,
            "source": event.source,
            "handler": getattr(sub.handler, "__qualname__", repr(sub.handler)),
            "error": f"{type(exc).__name__}: {exc}",
            "ts": time.time(),
        }
        self._errors.append(item)
        if len(self._errors) > 100:
            del self._errors[:-100]
        log.warning("事件处理器异常 %s ← %s: %s", item["handler"], event.topic, item["error"])

    # ── 观测 ────────────────────────────────────────────────
    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        return [e.to_dict() for e in list(self._history)[-limit:]]

    def recent_errors(self, limit: int = 20) -> list[dict[str, Any]]:
        return self._errors[-limit:]

    def topic_counts(self) -> dict[str, int]:
        return dict(self._publish_counts)

    def noisiest(self, threshold: int) -> list[tuple[str, int]]:
        """一段时间内发布次数超过阈值的主题，给自省引擎报警用。"""
        return [(t, c) for t, c in sorted(self._publish_counts.items(), key=lambda x: -x[1]) if c >= threshold]


_BUS: Bus | None = None


def get_bus() -> Bus:
    global _BUS
    if _BUS is None:
        _BUS = Bus()
    return _BUS
