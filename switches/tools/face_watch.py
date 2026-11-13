# -*- coding: utf-8 -*-
"""夜间记账：只读 tail AstrBot 日志，把脸层每一轮的事实写成 JSONL。

为什么要有它：插件自己的 stats.json 只有「总计 + 最近一次」，
过夜积累后想知道「哪一轮什么心情、注入了多少字、有没有用上口癖」就查不到了。
这个脚本不改任何东西、不碰机器人进程，只是把日志里的三行事实抄下来。

跑法：
    python tools/face_watch.py            # 常驻，从当前日志末尾开始
    python tools/face_watch.py --once     # 只扫一遍就退出（调试用）
输出：docs/face_turns.jsonl（每行一条）
"""

from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = Path(r"%ASTRBOT_HOME%\runtime.log")
OUT = ROOT / "docs" / "face_turns.jsonl"

INJECT = re.compile(r"\[脸层\] 注入 mood=(\S+?)\(([\d.]+)\) scene=(\S+) len=(\d+) stale=(\w+)")
HIT = re.compile(r"\[脸层\] 触达 mood=(\S+) 命中=(\w+)")
SNAP = re.compile(r"\[脸层\] 请求快照 parts=(\d+) chars=(\d+) 来源=(\S+)")


def parse(line: str) -> dict | None:
    m = INJECT.search(line)
    if m:
        return {"kind": "inject", "mood": m.group(1), "intensity": float(m.group(2)),
                "scene": m.group(3), "len": int(m.group(4)), "stale": m.group(5)}
    m = HIT.search(line)
    if m:
        return {"kind": "hit", "mood": m.group(1), "hit": m.group(2) == "True"}
    m = SNAP.search(line)
    if m:
        return {"kind": "prompt", "parts": int(m.group(1)), "chars": int(m.group(2)),
                "sources": m.group(3)}
    return None


def read_new(fh, state: dict) -> list[str]:
    """从上次偏移继续读；日志被重写（变小）就从头。"""
    try:
        size = LOG.stat().st_size
    except OSError:
        return []
    if size < state["pos"]:
        state["pos"] = 0
    if size == state["pos"]:
        return []
    fh.seek(state["pos"])
    chunk = fh.read(size - state["pos"])
    state["pos"] = size
    text = chunk.decode("gbk", errors="ignore")
    lines = (state["buf"] + text).splitlines()
    state["buf"] = lines.pop() if lines and not text.endswith("\n") else ""
    return lines


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--from-start", action="store_true", help="从文件开头扫（会重复历史）")
    args = ap.parse_args()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    if not LOG.exists():
        print(f"[fail] 日志不存在：{LOG}")
        return 2

    state = {"pos": 0 if args.from_start else LOG.stat().st_size, "buf": ""}
    print(f"[watch] {LOG} → {OUT}  (起始偏移 {state['pos']})")

    with LOG.open("rb") as fh, OUT.open("a", encoding="utf-8") as out:
        while True:
            for line in read_new(fh, state):
                rec = parse(line)
                if not rec:
                    continue
                rec["ts"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                out.flush()
                print(f"[rec] {rec}")
            if args.once:
                break
            time.sleep(3)


if __name__ == "__main__":
    raise SystemExit(main())
