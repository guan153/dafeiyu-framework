# -*- coding: utf-8 -*-
"""峰顶审计：DeepSeek 计价高峰（工作日 9-12 / 14-18）里，她有没有主动开口。

判据：
  · 发出消息（post_type=message_sent）
  · 前面 15 分钟内没有任何人给她发消息 → 算「主动」；否则算「应答」
  · 连发（60 秒内上一条也是她说的）跟着上一条归类，不重复算

额外核对 will_log.jsonl：峰顶窗口内 result=sent 的决策。

用法：
    python peak_audit.py                  # 今天
    python peak_audit.py --days 3         # 最近三天
    python peak_audit.py --since 17:50    # 只看今天 17:50 之后
    python peak_audit.py --json
    python peak_audit.py --fail-on-leak   # 有主动泄漏就返回 1（给自动化用）

不写任何东西，只读。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, date, timedelta
from pathlib import Path

DB = Path(r"%QBOT_HOME%\qbot\SnowLuma\data\100000001\messages.db")
WILL_LOG = Path(r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_whale_will\will_log.jsonl")
BOT_ID = 100000001
REPLY_WINDOW = 15 * 60      # 15 分钟内有来信就算应答
BURST_GAP = 60              # 60 秒内的连发算同一串


def is_peak(dt: datetime) -> bool:
    if dt.weekday() >= 5:
        return False
    try:
        import holidays as _hol
        if _hol.country_holidays("CN", years=[dt.year]).get(dt.date()):
            return False
    except Exception:
        pass
    return (9 <= dt.hour < 12) or (14 <= dt.hour < 18)


def load_rows(since: datetime) -> list[tuple[datetime, str, str, int]]:
    """[(时间, 方向 out/in, 文本, 发送者id)]"""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=10)
    cur = con.cursor()
    out: list[tuple[datetime, str, str, int]] = []
    for ts, data in cur.execute("SELECT timestamp, data FROM messages ORDER BY timestamp"):
        try:
            d = json.loads(data)
        except Exception:
            continue
        dt = datetime.fromtimestamp(ts)
        if dt < since:
            continue
        kind = d.get("post_type")
        text = str(d.get("raw_message") or "")
        sender = (d.get("sender") or {}).get("user_id") or 0
        if kind == "message_sent":
            out.append((dt, "out", text, BOT_ID))
        elif kind == "message":
            out.append((dt, "in", text, int(sender or 0)))
    con.close()
    out.sort(key=lambda r: r[0])
    return out


def audit(rows: list, days: int, since: datetime | None = None) -> dict:
    peak_sends = []
    peak_proactive = []
    per_day: dict[str, dict] = {}
    last_in = datetime.min
    last_out = datetime.min
    last_proactive = True

    for dt, direction, text, uid in rows:
        if direction == "in":
            last_in = dt
            continue
        # out
        if (dt - last_in).total_seconds() <= REPLY_WINDOW:
            proactive = False
        elif (dt - last_out).total_seconds() <= BURST_GAP:
            proactive = last_proactive          # 连发跟上一句同归类
        else:
            proactive = True
        last_out, last_proactive = dt, proactive

        if not is_peak(dt):
            continue
        item = {"time": dt.strftime("%Y-%m-%d %H:%M:%S"), "text": text[:80],
                "proactive": proactive}
        peak_sends.append(item)
        key = dt.strftime("%Y-%m-%d")
        d = per_day.setdefault(key, {"peak": 0, "proactive": 0})
        d["peak"] += 1
        if proactive:
            d["proactive"] += 1
            peak_proactive.append(item)

    # will_log 交叉核对
    will_sent = []
    if WILL_LOG.exists():
        for ln in WILL_LOG.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                d = json.loads(ln)
                dt = datetime.strptime(d.get("ts", ""), "%Y-%m-%d %H:%M:%S")
            except Exception:
                continue
            if is_peak(dt) and d.get("result") == "sent":
                if since and dt < since:
                    continue
                will_sent.append({"time": d.get("ts"), "text": str(d.get("text") or "")[:60]})

    return {
        "peak_sends": peak_sends,
        "peak_proactive": peak_proactive,
        "per_day": per_day,
        "will_sent": will_sent,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=1)
    ap.add_argument("--since", type=str, default="")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--fail-on-leak", action="store_true")
    args = ap.parse_args()

    now = datetime.now()
    if args.since:
        if "T" in args.since or "-" in args.since:
            since = datetime.fromisoformat(args.since)
        else:
            h, m = args.since.split(":")
            since = now.replace(hour=int(h), minute=int(m), second=0, microsecond=0)
    else:
        since = (now - timedelta(days=args.days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)

    rows = load_rows(since)
    res = audit(rows, args.days, since)
    res["since"] = since.strftime("%Y-%m-%d %H:%M:%S")
    res["now"] = now.strftime("%Y-%m-%d %H:%M:%S")

    if args.json:
        print(json.dumps(res, ensure_ascii=False))
        return 1 if (args.fail_on_leak and res["peak_proactive"]) else 0

    print(f"审计区间：{res['since']} → {res['now']}")
    print(f"{'日期':<12}{'峰顶发出':>8}{'其中主动':>8}")
    for day, d in sorted(res["per_day"].items()):
        print(f"{day:<12}{d['peak']:>8}{d['proactive']:>8}")
    if not res["per_day"]:
        print("（区间内没有落在峰顶窗口的发送）")

    if res["peak_proactive"]:
        print("\n峰顶里的主动消息：")
        for x in res["peak_proactive"]:
            print(f"  {x['time']}  {x['text']}")
    else:
        print("\n峰顶主动消息：0 条")

    if res["will_sent"]:
        print("\nwill_log 里峰顶 result=sent：")
        for x in res["will_sent"]:
            print(f"  {x['time']}  {x['text']}")
    else:
        print("will_log 里峰顶 sent：0 条")

    return 1 if (args.fail_on_leak and res["peak_proactive"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
