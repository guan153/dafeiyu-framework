# -*- coding: utf-8 -*-
"""日报生成器：把她这一天的表现算成一张纸。

只读，不改任何东西。跑法：
    python tools/daily_report.py            # 今天
    python tools/daily_report.py 2026-11-09 # 指定日期

数据源
------
1. will_log.jsonl        意志每次决策（sent/preview/blocked）
2. will_state.json       被接住 / 被无视
3. face stats.json       注入次数、触达率
4. urge.json             当前冲动水位
5. paint_state.json      当天画图张数（本地 / 元衡）
6. snowluma-<date>.log   真实对话（谁说了几句、每批几条气泡）
"""

from __future__ import annotations

import sys
import pathlib as _pl
_ROOT = _pl.Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


import json
import re
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from whale_core.paths import DATA  # noqa: E402
PD = DATA / "plugin_data"
WILL_LOG = PD / "astrbot_plugin_whale_will" / "will_log.jsonl"
WILL_STATE = PD / "astrbot_plugin_whale_will" / "will_state.json"
FACE_STATS = PD / "astrbot_plugin_whale_face" / "stats.json"
URGE = PD / "astrbot_plugin_whale_state" / "urge.json"
PAINT = PD / "astrbot_plugin_whale_paint" / "paint_state.json"
from whale_core.paths import SNOW_LOGS  # noqa: E402
OUT_DIR = Path(__file__).resolve().parent.parent / "docs"
HISTORY = OUT_DIR / "日报-历史.jsonl"


def read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def read_lines(p: Path) -> list[str]:
    try:
        return p.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []


def chat_stats(day: str) -> dict:
    """从桥日志里数：主人几条、她几条、几批、平均每批几条、平均字数。"""
    log = SNOW_LOGS / f"snowluma-{day}.log"
    mine, hers, text_hers, batches, chars = 0, 0, 0, 0, 0
    last_was_her = False
    # 桥里用户行有多种写法：
    #   [Event] 私聊 [guan(100000000)]: ID:-145833076 好
    #   [Event] 私聊 1249997458: ID:-413064610 我是…
    # 统一：拿到 "ID:xxx" 后面的内容，只统计 owner(100000000) 的消息
    pat_user = re.compile(r"\[Event\] 私聊 .*?ID:\S+\s*(.*)$")
    pat_send = re.compile(r"私聊 (\d+) \| 发送：(.*)$")
    OWNER = "100000000"
    for line in read_lines(log):
        m = pat_user.search(line)
        if m is not None:
            body = (m.group(1) or "").strip()
            if body.startswith("[图片") or body.startswith("[语音"):
                body = ""
            if body and OWNER in line:
                mine += 1
                last_was_her = False
            continue
        m = pat_send.search(line)
        if m:
            if m.group(1) != OWNER:
                continue
            text = (m.group(2) or "").strip()
            hers += 1
            if text and not text.startswith("[图片") and not text.startswith("[语音"):
                text_hers += 1
                chars += len(text)
            if not last_was_her:
                batches += 1
            last_was_her = True
    return {
        "mine": mine, "hers": hers, "text_hers": text_hers, "batches": batches,
        "per_batch": round(hers / batches, 1) if batches else 0,
        "avg_chars": round(chars / text_hers, 1) if text_hers else 0,
    }


def report(day: str) -> str:
    rows = [json.loads(x) for x in read_lines(WILL_LOG) if x.strip().startswith("{")]
    rows = [r for r in rows if str(r.get("ts", "")).startswith(day)]
    results = Counter(r.get("result") for r in rows)
    gates = Counter(r.get("gate") for r in rows if r.get("result") == "blocked")

    st = read_json(WILL_STATE)
    face = read_json(FACE_STATS)
    urge = read_json(URGE)
    paint = read_json(PAINT)
    pday = (paint.get("days") or {}).get(day) or {}
    chat = chat_stats(day)

    sent = results.get("sent", 0)
    good = int(st.get("good") or 0)
    bad = int(st.get("bad") or 0)
    hit_rate = face.get("hit_rate")

    lines = []
    lines.append(f"# 鲸鱼少女日报 · {day}")
    lines.append("")
    lines.append("## 结论")
    verdict = []
    if sent == 0:
        verdict.append("今天没有主动开口（水位没到，或全被门拦下）")
    else:
        verdict.append(f"主动开口 {sent} 次")
    if bad and bad >= max(1, good):
        verdict.append("被无视多于被接住，需要看时间点")
    if hit_rate is not None and hit_rate < 0.4:
        verdict.append(f"触达率偏低（{hit_rate:.0%}）")
    lines.append("；".join(verdict) or "无明显异常")
    lines.append("")

    lines.append("## 主动性")
    lines.append(f"- 意志决策 {len(rows)} 次：{dict(results)}")
    if gates:
        top = "、".join(f"{k}×{v}" for k, v in gates.most_common(3))
        lines.append(f"- 主要拦截原因：{top}")
    lines.append(f"- 被接住 / 被无视：{good} / {bad}")
    if urge:
        lines.append(f"- 当前冲动 V={float(urge.get('v') or 0):.2f} / 门槛 θ={float(urge.get('theta') or 0):.2f}"
                     f"（{urge.get('tier_label') or '-'}）")
    lines.append("")

    lines.append("## 对话")
    lines.append(f"- 主人 {chat['mine']} 条｜她 {chat['hers']} 条（其中纯文字 {chat['text_hers']}）｜{chat['batches']} 批")
    lines.append(f"- 每批平均 {chat['per_batch']} 条气泡，单条平均 {chat['avg_chars']} 字")
    _t = trend_text(day, "per_batch")
    if _t:
        lines.append(f"- 气泡节奏趋势：{_t}")
    lines.append("")

    lines.append("## 画画")
    lines.append(f"- 今天共 {pday.get('total', 0)} 张（本地 {pday.get('local', 0)} / 元衡 {pday.get('meta', 0)}）")
    lines.append("")

    lines.append("## 注入")
    lp = face.get("last_prompt") or {}
    lines.append(f"- 注入 {face.get('injected', 0)} 轮，命中 {face.get('hit', 0)}，触达率 "
                 f"{'—' if hit_rate is None else f'{hit_rate:.0%}'}")
    if lp:
        lines.append(f"- 最近一次请求：{lp.get('parts')} 块 / {lp.get('chars')} 字")
    return "\n".join(lines) + "\n"


def append_history(day: str, **kv) -> None:
    """把每天的关键指标落一行，后面就能看趋势，而不是只看单日。"""
    try:
        HISTORY.parent.mkdir(parents=True, exist_ok=True)
        rows = []
        if HISTORY.exists():
            rows = [json.loads(x) for x in HISTORY.read_text(encoding="utf-8").splitlines() if x.strip()]
        rows = [r for r in rows if r.get("day") != day]
        rows.append({"day": day, **kv})
        rows.sort(key=lambda r: r.get("day") or "")
        HISTORY.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows[-60:]) + "\n",
                           encoding="utf-8")
    except Exception:
        pass


def trend_text(day: str, key: str) -> str:
    try:
        rows = [json.loads(x) for x in HISTORY.read_text(encoding="utf-8").splitlines() if x.strip()]
    except Exception:
        return ""
    prev = [r[key] for r in rows if r.get("day") != day and isinstance(r.get(key), (int, float))]
    if len(prev) < 2:
        return ""
    cur = next((r[key] for r in rows if r.get("day") == day), None)
    if not isinstance(cur, (int, float)):
        return ""
    avg = sum(prev) / len(prev)
    arrow = "↑" if cur > avg * 1.1 else ("↓" if cur < avg * 0.9 else "→")
    return f"{arrow}（近 {len(prev)} 天均值 {avg:.2f}）"


def main() -> int:
    day = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()
    text = report(day)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"日报-{day}.md"
    out.write_text(text, encoding="utf-8")
    # 落账：明天就能和今天比
    try:
        rows = [json.loads(x) for x in read_lines(WILL_LOG) if x.strip().startswith("{")]
        rows = [r for r in rows if str(r.get("ts", "")).startswith(day)]
        st = read_json(WILL_STATE)
        face = read_json(FACE_STATS)
        chat = chat_stats(day)
        append_history(day, sent=sum(1 for r in rows if r.get("result") == "sent"),
                       good=int(st.get("good") or 0), bad=int(st.get("bad") or 0),
                       hit_rate=face.get("hit_rate"), per_batch=chat["per_batch"],
                       avg_chars=chat["avg_chars"])
    except Exception:
        pass
    print(text)
    print(f"\n→ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
