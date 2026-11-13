# -*- coding: utf-8 -*-
"""把历史数据回填进统一时间线。

跑法：
    python tools/timeline_build.py --day 2026-11-09

来源与事件类型：
    will_log.jsonl   → will / sent | preview | blocked | generate_failed
    paint_state.json → paint / 出图（含引擎与心情）
    SnowLuma 日志    → chat / 主人说话、她说话
    urge_history     → urge / 水位变化（抽样，避免刷屏）
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from whale_core import timeline  # noqa: E402

DATA = Path(r"%ASTRBOT_HOME%\data")
PD = DATA / "plugin_data"
WILL_LOG = PD / "astrbot_plugin_whale_will" / "will_log.jsonl"
PAINT = PD / "astrbot_plugin_whale_paint" / "paint_state.json"
SNOW = Path(r"%QBOT_HOME%\qbot\SnowLuma\logs")
OWNER = "100000000"


def _ts(text: str, day: str) -> float | None:
    """把 'YYYY-MM-DD HH:MM:SS' 或 'HH:MM:SS' 变成 epoch。"""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).timestamp()
        except Exception:
            continue
    try:
        t = datetime.strptime(text, "%H:%M:%S")
        d = datetime.strptime(day, "%Y-%m-%d")
        return d.replace(hour=t.hour, minute=t.minute, second=t.second).timestamp()
    except Exception:
        return None


def backfill_will(day: str) -> int:
    n = 0
    try:
        lines = WILL_LOG.read_text(encoding="utf-8").splitlines()
    except Exception:
        return 0
    for line in lines:
        try:
            r = json.loads(line)
        except Exception:
            continue
        if not str(r.get("ts", "")).startswith(day):
            continue
        ts = _ts(str(r["ts"]), day)
        timeline.record("will", str(r.get("result") or "?"), ts=ts,
                        V=r.get("v"), tier=r.get("tier"), gate=r.get("gate"),
                        text=r.get("text"))
        n += 1
    return n


def backfill_paint(day: str) -> int:
    try:
        d = json.loads(PAINT.read_text(encoding="utf-8-sig"))
    except Exception:
        return 0
    n = 0
    for h in (d.get("history") or []):
        ts = float(h.get("ts") or 0)
        if not ts or datetime.fromtimestamp(ts).strftime("%Y-%m-%d") != day:
            continue
        timeline.record("paint", "drew", ts=ts, engine=h.get("engine"),
                        score=h.get("score"), mood=h.get("label"),
                        activity=h.get("activity"))
        n += 1
    return n


def backfill_chat(day: str) -> int:
    log = SNOW / f"snowluma-{day}.log"
    if not log.exists():
        return 0
    n = 0
    pat_user = re.compile(r"\[Event\] 私聊 .*?ID:\S+\s*(.*)$")
    pat_send = re.compile(r"私聊 (\d+) \| 发送：(.*)$")
    for line in log.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = re.match(r"^(\d\d:\d\d:\d\d)", line)
        if not m:
            continue
        ts = _ts(m.group(1), day)
        mu = pat_user.search(line)
        if mu and OWNER in line:
            body = (mu.group(1) or "").strip()
            if body:
                timeline.record("chat", "user", ts=ts, text=body)
                n += 1
            continue
        ms = pat_send.search(line)
        if ms and ms.group(1) == OWNER:
            timeline.record("chat", "her", ts=ts, text=(ms.group(2) or "").strip())
            n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--reset", action="store_true", help="先清空时间线再回填")
    args = ap.parse_args()
    if args.reset:
        try:
            timeline.TIMELINE.unlink()
        except Exception:
            pass
    a = backfill_will(args.day)
    b = backfill_paint(args.day)
    c = backfill_chat(args.day)
    print(f"回填完成：will {a} 条 / paint {b} 条 / chat {c} 条")
    print(f"时间线：{timeline.TIMELINE}")
    print("\n最近 12 条：")
    print(timeline.tail(12))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
