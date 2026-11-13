# -*- coding: utf-8 -*-
"""统一时间线：谁在什么时候，因为什么，做了什么。

现在她的状态散在十几个文件里、三种编码里。出问题时只能一个文件一个文件扒，
而且每次都要重新猜格式。这个模块只有一个职责：**把发生过的事按时间排成一行一行**。

写入：record(src, kind, **fields)  → data/plugin_data/whale_core/timeline.jsonl
读取：tail(n)                      → 最近 n 条
回填：tools/timeline_build.py       → 把历史文件（will_log / paint / 桥日志）并进来

约定
----
* 一行一个 JSON，UTF-8，append-only，永不改写历史。
* 字段尽量扁平：src / kind / 其余随事。时间戳统一 epoch 秒 + 本地 HH:MM:SS。
* 任何一次写入失败都静默——时间线坏了不能影响她说话。
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from .paths import DATA  # noqa: E402
TIMELINE = DATA / "plugin_data" / "whale_core" / "timeline.jsonl"
MAX_BYTES = 8 * 1024 * 1024          # 超过 8MB 就滚到 .1，防止无限长


def _rotate_if_big() -> None:
    try:
        if TIMELINE.exists() and TIMELINE.stat().st_size > MAX_BYTES:
            old = TIMELINE.with_suffix(".jsonl.1")
            if old.exists():
                old.unlink()
            TIMELINE.replace(old)
    except Exception:
        pass


def record(src: str, kind: str, ts: float | None = None, **fields) -> None:
    """记一条。永远不抛异常。"""
    try:
        t = ts if ts is not None else time.time()
        row = {
            "ts": round(float(t), 3),
            "t": datetime.fromtimestamp(t).strftime("%m-%d %H:%M:%S"),
            "src": str(src)[:24],
            "kind": str(kind)[:32],
        }
        for k, v in fields.items():
            if v is None or v == "" or v == [] or v == {}:
                continue
            if isinstance(v, (str, int, float, bool)):
                row[k] = v[:160] if isinstance(v, str) else v
            else:
                row[k] = json.dumps(v, ensure_ascii=False)[:160]
        _rotate_if_big()
        TIMELINE.parent.mkdir(parents=True, exist_ok=True)
        with TIMELINE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def rows(limit: int = 200) -> list[dict]:
    """最近 limit 条（按写入顺序）。"""
    try:
        lines = TIMELINE.read_text(encoding="utf-8").splitlines()[-limit:]
    except Exception:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def tail(n: int = 30) -> str:
    """给人看的一小段。"""
    out = []
    for r in rows(n):
        extra = " ".join(f"{k}={v}" for k, v in r.items() if k not in ("ts", "t", "src", "kind"))
        out.append(f"{r.get('t')} [{r.get('src')}] {r.get('kind')} {extra}"[:160])
    return "\n".join(out)
