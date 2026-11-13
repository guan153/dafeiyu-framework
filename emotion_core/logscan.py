# -*- coding: utf-8 -*-
"""日志扫描：把 runtime.log 里的报错变成「可归因的弱点候选」。

不做的：不做全量审计（那是离线分析的事）。
要做的：只看最近窗口，只认已知模式，同一条反复出现只算一次事件，
避免「每轮巡检都新增一条弱点」把账本撑爆。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import paths

# (key, 标题, 分类, 严重度, 归属模块猜测, 正则)
PATTERNS: list[tuple[str, str, str, str, str, str]] = [
    ("send_api_unavailable", "OneBot 通道不可用，消息发不出去", "bug", "P1", "",
     r"ApiNotAvailable|Failed to send|发送失败"),
    ("proactive_undelivered", "主动消息未能送达", "bug", "P1", "astrbot_plugin_proactive_chat",
     r"主动消息未能送达"),
    ("inject_unbound_local", "prompt 注入时变量未定义（注入被吞）", "bug", "P1", "astrbot_plugin_whale_brain",
     r"注入失败.*(UnboundLocalError|cannot access local variable)"),
    ("embedding_batch", "嵌入批大小超限（补向量失败）", "bug", "P2", "astrbot_plugin_iris_chat_memory",
     r"batch size is invalid|batch size is invalid, should not be larger than"),
    ("faiss_desync", "FAISS 索引与 SQLite 不同步", "bug", "P2", "astrbot_plugin_iris_chat_memory",
     r"FAISS 索引与 SQLite 脱同步|缺失向量"),
    ("event_loop_lag", "事件循环卡顿（可能阻塞 AstrBot）", "perf", "P1", "",
     r"Event loop lag detected.*?(\d+(?:\.\d+)?)s"),
    ("provider_missing", "Provider 未就绪（启动竞态）", "bug", "P3", "",
     r"Provider .* was not found|Embedding Provider 未就绪"),
    ("plugin_load_fail", "模块加载失败", "bug", "P1", "",
     r"Failed to load plugin|模块加载失败|Load plugin .* failed"),
    ("asgi_endofstream", "ASGI 连接被客户端中断", "doc", "P3", "",
     r"EndOfStream|No response returned"),
    ("send_lock_bypass", "绕过了发送闸门（疑似抢发）", "conflict", "P2", "",
     r"voice_gate.*bypass|绕过.*闸门"),
    # ── 脸层（情绪外显）──────────────────────────────────────────────
    # 注入失败只打 debug 不报错，所以必须靠日志模式抓：否则「她不说话了」永远查不到原因。
    ("face_inject_fail", "脸层注入失败（表情/口癖这轮丢了）", "bug", "P2", "astrbot_plugin_whale_face",
     r"\[脸层\] 注入失败"),
    ("face_core_missing", "脸层读不到内核库（脸退回默认）", "bug", "P2", "astrbot_plugin_whale_face",
     r"\[脸层\].*(whale_core|ModuleNotFoundError|ImportError)"),
    ("face_block_empty", "脸层连续生成空块（情绪或配置异常）", "bug", "P3", "astrbot_plugin_whale_face",
     r"\[脸层\] 注入 mood=\S+.*len=0"),
]

_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_TS_RE = re.compile(
    r"^\[(?:(\d{4}-\d{2}-\d{2})[ T])?"
    r"(\d{2}):(\d{2}):(\d{2})\.(\d{3})\]"
)


@dataclass
class Finding:
    key: str
    title: str
    category: str
    severity: str
    plugin: str
    count: int
    samples: list[str]
    first_ts: float
    last_ts: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_ts(line: str, now: datetime) -> float | None:
    """Parse a dated timestamp or infer the most recent day for a time-only line.

    Args:
        line: A runtime log line, possibly containing ANSI color escapes.
        now: Local time used for legacy logs without a date.

    Returns:
        A Unix timestamp, or None for an invalid timestamp.
    """
    match = _TS_RE.match(_ANSI_RE.sub("", line).lstrip("\ufeff"))
    if not match:
        return None
    date, hh, mm, ss, ms = match.groups()
    try:
        candidate = datetime.strptime(date, "%Y-%m-%d") if date else now
        candidate = candidate.replace(
            hour=int(hh), minute=int(mm), second=int(ss), microsecond=int(ms) * 1000
        )
        if not date and candidate > now + timedelta(seconds=120):
            candidate -= timedelta(days=1)
        return candidate.timestamp()
    except ValueError:
        return None


def _tail_text(
    path: Path, max_bytes: int, diagnostics: dict[str, Any] | None = None
) -> str:
    """Read a bounded tail, decoding mixed UTF-8 and legacy Chinese log lines.

    Args:
        path: Runtime log file.
        max_bytes: Maximum tail size, discarding a leading partial line.
        diagnostics: Optional counters for decode health.

    Returns:
        Decoded log text. Unrecoverable bytes remain visible as replacements.
    """
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    with path.open("rb") as fh:
        fh.seek(0, 2)
        size = fh.tell()
        if size > max_bytes:
            offset = size - max_bytes
            fh.seek(offset - 1)
            previous = fh.read(1)
            if previous != b"\n":
                fh.readline()
        else:
            fh.seek(0)
        data = fh.read()
    lines = []
    for raw_line in data.splitlines(keepends=True):
        try:
            line = raw_line.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                line = raw_line.decode("gb18030")
                if diagnostics is not None:
                    diagnostics["legacy_encoding_lines"] += 1
            except UnicodeDecodeError:
                line = raw_line.decode("utf-8", "replace")
                if diagnostics is not None:
                    diagnostics["decode_replacements"] += line.count("\ufffd")
        lines.append(line)
    return "".join(lines)


def scan(
    *,
    window_min: float = 30.0,
    max_bytes: int = 4 * 1024 * 1024,
    log_path: Path | str | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> list[Finding]:
    """Scan known error patterns and expose collection health separately.

    Args:
        window_min: Lookback window in minutes.
        max_bytes: Maximum tail size.
        log_path: Optional runtime log override.
        diagnostics: Optional mutable collector status, cleared on entry.

    Returns:
        Aggregated findings. An empty list alone does not imply healthy collection.
    """
    status = diagnostics if diagnostics is not None else {}
    status.clear()
    status.update(
        status="missing",
        lines_total=0,
        timestamp_lines=0,
        window_lines=0,
        legacy_encoding_lines=0,
        decode_replacements=0,
        last_parsed_ts=None,
    )
    path = Path(log_path or paths.runtime_log())
    try:
        text = _tail_text(path, max_bytes, status)
    except FileNotFoundError:
        return []
    except OSError as exc:
        status.update(status="unreadable", error_type=type(exc).__name__)
        return []

    now_dt = datetime.now()
    now_ts = now_dt.timestamp()
    cutoff = now_ts - window_min * 60
    buckets: dict[str, dict[str, Any]] = {}
    for raw_line in text.splitlines():
        status["lines_total"] += 1
        line = _ANSI_RE.sub("", raw_line).lstrip("\ufeff")
        ts = _parse_ts(line, now_dt)
        if ts is None or ts > now_ts + 120:
            continue
        status["timestamp_lines"] += 1
        status["last_parsed_ts"] = max(status["last_parsed_ts"] or ts, ts)
        if ts < cutoff:
            continue
        status["window_lines"] += 1
        for key, title, category, severity, plugin, pattern in PATTERNS:
            if not re.search(pattern, line):
                continue
            bucket = buckets.setdefault(
                key,
                {"key": key, "title": title, "category": category, "severity": severity,
                 "plugin": plugin, "count": 0, "samples": [], "first_ts": ts, "last_ts": ts},
            )
            bucket["count"] += 1
            bucket["last_ts"] = max(bucket["last_ts"], ts)
            bucket["first_ts"] = min(bucket["first_ts"], ts)
            if len(bucket["samples"]) < 3:
                bucket["samples"].append(line[-500:])

    if not status["lines_total"]:
        status["status"] = "empty"
    elif not status["timestamp_lines"]:
        status["status"] = "unparseable"
    elif status["decode_replacements"]:
        status["status"] = "degraded"
    elif not status["window_lines"]:
        status["status"] = "stale"
    else:
        status["status"] = "ok"
    findings = [Finding(**bucket) for bucket in buckets.values()]
    findings.sort(key=lambda finding: (-_weight(finding.severity), -finding.count))
    return findings


def _weight(severity: str) -> int:
    return {"P0": 4, "P1": 3, "P2": 2, "P3": 1}.get(severity, 1)


def scan_and_report(**kwargs: Any) -> dict[str, Any]:
    diagnostics = kwargs.pop("diagnostics", None)
    if diagnostics is None:
        diagnostics = {}
    findings = scan(diagnostics=diagnostics, **kwargs)
    return {
        "collection": diagnostics,
        "count": len(findings),
        "findings": [f.to_dict() for f in findings],
        "errors_total": sum(f.count for f in findings),
    }
