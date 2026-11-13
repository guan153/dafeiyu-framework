# -*- coding: utf-8 -*-
"""10 分钟浸泡测试：只读采样，不改任何东西。

盯六件事：
1. 端口是否一直 UP（掉线 = 大问题）
2. 日志有没有新报错（Traceback / ERRO / 注入失败 / UnboundLocalError）
3. 脸层到底有没有在注入（合成 + 注入两行日志）
4. `mood_composed.json` 有没有在更新（说明合成链路在跑）
5. 状态总线的「主情绪」和合成结果是否一致（这是本次设计的核心验收）
6. AstrBot 进程内存有没有明显爬升（是否有泄漏迹象）

跑法：python tools/soak.py --minutes 10
输出：docs/soak.jsonl（每条一次采样）
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = Path(r"%ASTRBOT_HOME%\runtime.log")
DATA = Path(r"%ASTRBOT_HOME%\data")
COMPOSED = DATA / "plugin_data" / "whale_core" / "mood_composed.json"
STATS = DATA / "plugin_data" / "astrbot_plugin_whale_face" / "stats.json"
OUT = ROOT / "docs" / "soak.jsonl"
PORTS = (6185, 6199, 8920, 5099, 3000)

ERR_PAT = re.compile(r"Traceback|\[ERRO\]|ERROR|注入失败|UnboundLocalError")
MY_PAT = re.compile(r"Traceback.*whale_face|whale_face.*ERRO")
# 忽略已知噪声：loguru 写 emoji 的 GBK 报错
NOISE = re.compile(r"Logging error in Loguru Handler|UnicodeEncodeError: .gbk|'gbk' codec can't encode")


def port_up(p: int) -> bool:
    s = socket.socket()
    s.settimeout(0.6)
    try:
        return s.connect_ex(("127.0.0.1", p)) == 0
    finally:
        s.close()


def read_log_from(pos: int) -> tuple[str, int]:
    try:
        size = LOG.stat().st_size
    except OSError:
        return "", pos
    if size < pos:
        pos = 0
    with LOG.open("rb") as f:
        f.seek(pos)
        raw = f.read(size - pos)
    return raw.decode("gbk", errors="ignore"), size


def astrbot_rss() -> int:
    """AstrBot 主进程内存（KB），查不到返回 -1。"""
    try:
        import subprocess

        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-Process python -ErrorAction SilentlyContinue | Where-Object { $_.Path -like '*AstrBot*' -or $_.WorkingSet64 -gt 0 } | Sort-Object WorkingSet64 -Descending | Select-Object -First 1 -ExpandProperty WorkingSet64"],
            capture_output=True, text=True, timeout=20,
        )
        return int((out.stdout or "0").strip() or 0) // 1024
    except Exception:
        return -1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=10)
    ap.add_argument("--interval", type=float, default=20)
    args = ap.parse_args()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    pos = LOG.stat().st_size if LOG.exists() else 0
    start = time.time()
    end = start + args.minutes * 60
    samples = []

    print(f"[soak] 开始 {datetime.now():%H:%M:%S}，共 {args.minutes} 分钟，每 {args.interval}s 一次")
    while time.time() < end:
        time.sleep(args.interval)
        chunk, pos = read_log_from(pos)
        lines = chunk.splitlines()

        errs = [l.strip()[:160] for l in lines if ERR_PAT.search(l) and not NOISE.search(l)]
        mine = [l.strip()[:160] for l in lines if "whale_face" in l and ERR_PAT.search(l)]

        comp = {}
        try:
            comp = json.loads(COMPOSED.read_text(encoding="utf-8"))
        except Exception:
            pass
        stats = {}
        try:
            stats = json.loads(STATS.read_text(encoding="utf-8"))
        except Exception:
            pass

        # 状态总线这一拍说的主情绪
        bus_mood = None
        for l in reversed(lines):
            m = re.search(r"\[状态总线\] 已注入：主情绪=(\S+)", l)
            if m:
                bus_mood = m.group(1)
                break

        sample = {
            "ts": datetime.now().strftime("%H:%M:%S"),
            "ports": {str(p): port_up(p) for p in PORTS},
            "new_lines": len(lines),
            "errors": errs[:5],
            "face_errors": mine[:3],
            "inject_lines": sum(1 for l in lines if "[脸层] 注入" in l),
            "compose_lines": sum(1 for l in lines if "[脸层] 合成" in l),
            "composed": {"mood": comp.get("mood"), "intensity": comp.get("intensity"),
                         "age": round(time.time() - float(comp.get("ts") or 0)) if comp.get("ts") else None},
            "bus_mood": bus_mood,
            "stats": {"injected": stats.get("injected"), "hit": stats.get("hit"), "miss": stats.get("miss")},
            "rss_kb": astrbot_rss(),
        }
        samples.append(sample)
        with OUT.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(sample, ensure_ascii=False) + "\n")
        print(f"[{sample['ts']}] ports={sum(sample['ports'].values())}/5 err={len(errs)} "
              f"注入+{sample['inject_lines']} 合成+{sample['compose_lines']} "
              f"composed={sample['composed']['mood']} bus={bus_mood} rss={sample['rss_kb']}KB")
        if sample["face_errors"]:
            print("   ⚠ 脸层报错:", sample["face_errors"][0])

    # 收尾结论
    print("\n===== 浸泡结论 =====")
    total = len(samples)
    if not total:
        print("没有采样")
        return 2
    port_ok = sum(1 for s in samples if sum(s["ports"].values()) == len(PORTS))
    err_rate = sum(1 for s in samples if s["errors"]) / total
    injected = sum(s["inject_lines"] for s in samples)
    composed_n = sum(s["compose_lines"] for s in samples)
    rss = [s["rss_kb"] for s in samples if s["rss_kb"] > 0]
    mismatch = [s for s in samples if s["bus_mood"] and s["composed"]["mood"]
                and s["bus_mood"] != s["composed"]["mood"]]
    print(f"采样 {total} 次 · 端口全通 {port_ok}/{total} · 有报错的采样 {sum(1 for s in samples if s['errors'])}")
    print(f"注入 {injected} 次 · 合成 {composed_n} 次")
    if rss:
        print(f"内存 {rss[0]}KB → {rss[-1]}KB（{'无明显爬升' if rss[-1] - rss[0] < 150_000 else '⚠ 持续爬升'}）")
    print(f"主情绪与合成不一致的采样: {len(mismatch)}/{total}"
          + ("" if not mismatch else f" · 例：{mismatch[0]['bus_mood']} vs {mismatch[0]['composed']['mood']}"))
    print(f"报告：{OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
