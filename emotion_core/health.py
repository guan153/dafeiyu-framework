# -*- coding: utf-8 -*-
"""健康探针：用「真实证据」回答「她现在到底行不行」。

原则：
- 不看端口就断言在线（历史上 supervisor 只探 5099 导致全绿假象）；
- 探针结果带延迟和错误原因，进自省评分；
- 全部有超时，绝不吊死事件循环。
"""

from __future__ import annotations

import asyncio
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


@dataclass
class ProbeResult:
    name: str
    ok: bool
    detail: str = ""
    latency_ms: float = 0.0
    ts: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


async def tcp_probe(name: str, host: str, port: int, timeout: float = 2.0) -> ProbeResult:
    start = time.perf_counter()
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
        writer.close()
        with_suppress = getattr(writer, "wait_closed", None)
        if with_suppress:
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
        del reader
        ok, detail = True, "tcp ok"
    except Exception as exc:  # noqa: BLE001
        ok, detail = False, f"{type(exc).__name__}: {exc}"
    latency = (time.perf_counter() - start) * 1000
    return ProbeResult(name=name, ok=ok, detail=detail, latency_ms=round(latency, 1), ts=time.time())


def _http_get(url: str, timeout: float) -> tuple[bool, str, int]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "whale-core/3.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(512).decode("utf-8", "replace")
            return True, f"HTTP {resp.status} {body[:120].strip()}", int(resp.status)
    except urllib.error.HTTPError as exc:
        # 4xx/5xx 说明服务活着但状态不对，算失败但保留信息
        return False, f"HTTP {exc.code}", int(exc.code)
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}", 0


async def http_probe(name: str, url: str, timeout: float = 3.0, expect: int = 200) -> ProbeResult:
    start = time.perf_counter()
    ok, detail, status = await asyncio.to_thread(_http_get, url, timeout)
    if ok and expect and status != expect:
        ok = False
        detail = f"{detail}; expected {expect}"
    latency = (time.perf_counter() - start) * 1000
    return ProbeResult(name=name, ok=ok, detail=detail, latency_ms=round(latency, 1), ts=time.time())


async def file_fresh_probe(name: str, path: Path | str, max_age_sec: float) -> ProbeResult:
    path = Path(path)
    start = time.perf_counter()
    if not path.exists():
        return ProbeResult(name=name, ok=False, detail="文件不存在", ts=time.time())
    age = time.time() - path.stat().st_mtime
    ok = age <= max_age_sec
    detail = f"{int(age)}s 前更新（容忍 {int(max_age_sec)}s）"
    latency = (time.perf_counter() - start) * 1000
    return ProbeResult(name=name, ok=ok, detail=detail, latency_ms=round(latency, 1), ts=time.time())


@dataclass(slots=True)
class ProbeSpec:
    name: str
    kind: str                     # tcp | http | file
    target: Any                   # (host, port) / url / path
    timeout: float = 2.0
    max_age_sec: float = 0.0
    critical: bool = False        # 挂了就是 P1，否则 P3


DEFAULT_SPECS: list[ProbeSpec] = [
    ProbeSpec("astrbot.webui", "tcp", ("127.0.0.1", 6185), critical=True),
    ProbeSpec("astrbot.ws", "tcp", ("127.0.0.1", 6199), critical=True),
    ProbeSpec("onebot.http", "tcp", ("127.0.0.1", 3000), critical=True),
    ProbeSpec("beam.gateway", "http", "http://127.0.0.1:8920/health", critical=True),
]


async def run_probes(specs: list[ProbeSpec] | None = None) -> list[ProbeResult]:
    specs = specs or DEFAULT_SPECS
    tasks = []
    for spec in specs:
        if spec.kind == "tcp":
            host, port = spec.target
            tasks.append(tcp_probe(spec.name, host, int(port), spec.timeout))
        elif spec.kind == "http":
            tasks.append(http_probe(spec.name, str(spec.target), spec.timeout))
        elif spec.kind == "file":
            tasks.append(file_fresh_probe(spec.name, spec.target, spec.max_age_sec))
    if not tasks:
        return []
    return list(await asyncio.gather(*tasks, return_exceptions=False))


def summarize(results: list[ProbeResult]) -> dict[str, Any]:
    total = len(results)
    ok = sum(1 for r in results if r.ok)
    return {
        "total": total,
        "ok": ok,
        "failed": total - ok,
        "ratio": round(ok / total, 3) if total else 0.0,
        "results": [r.to_dict() for r in results],
    }


def free_port(port: int, host: str = "127.0.0.1", timeout: float = 0.4) -> bool:
    """同步版端口探测，给不方便 await 的地方用。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) != 0
