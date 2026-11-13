# -*- coding: utf-8 -*-
"""脸层一键验收（只读）。

你发一句给机器人，然后跑这个，它会回答三件事：
1. 钩子到底有没有跑（日志里有没有 `[脸层] 注入`）
2. 这轮真正塞给模型的是什么（stats.json 里存的 block 原文）
3. 她有没有把这些词用出来（触达率）

跑法：
    python tools/face_verify.py           # 看最近一次
    python tools/face_verify.py --wait 180  # 等最多 180 秒，等到一次注入就打印
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASTRBOT = Path(r"%ASTRBOT_HOME%")
LOG = ASTRBOT / "runtime.log"
STATS = ASTRBOT / "data" / "plugin_data" / "astrbot_plugin_whale_face" / "stats.json"

INJECT_RE = re.compile(r"\[脸层\] 注入 mood=(\S+)\(([\d.]+)\) scene=(\S+) len=(\d+)")
HIT_RE = re.compile(r"\[脸层\] 触达 mood=(\S+) 命中=(\w+)")


def read_stats() -> dict:
    try:
        return json.loads(STATS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _read_log_text(limit: int = 600_000) -> str:
    """读日志：AstrBot 的 runtime.log 中文是 GBK 写的。

    坑（2026-11-08 踩过两次）：`decode("utf-8", errors="ignore")` **不会抛异常**，
    它只是把坏字节丢掉，所以 `try utf-8 / except -> gbk` 的写法永远走不到 gbk，
    中文全变替换字符，正则永远匹配不上 —— 典型的假阴性。
    正确做法：先**严格**解码，失败才换下一个编码。
    """
    if not LOG.exists():
        return ""
    raw = LOG.read_bytes()[-limit:]
    for enc in ("gbk", "utf-8"):  # 已知这台机器是 gbk，先试它
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("gbk", errors="ignore")


def scan_log() -> dict:
    text = _read_log_text()
    if not text:
        return {}
    inj = INJECT_RE.findall(text)
    hits = HIT_RE.findall(text)
    return {
        "injections": len(inj),
        "last_injection": inj[-1] if inj else None,
        "hits": len(hits),
        "last_hit": hits[-1] if hits else None,
    }


def report(wait: int = 0) -> int:
    deadline = time.time() + max(0, wait)
    while True:
        info = scan_log()
        if info.get("injections") or time.time() >= deadline:
            break
        time.sleep(3)

    st = read_stats()
    print("=" * 62)
    if not info.get("injections"):
        print("❌ 还没有任何注入")
        print("   可能原因（按这个顺序查）：")
        print("   1) 没发过消息 / 发在了重启窗口里")
        print("   2) 插件不在 plugin_set 白名单（巡检会 FAIL）")
        print("   3) 消息不是主人私聊（群聊/别人发的不注入）")
        print(f"   当前 stats：{json.dumps(st, ensure_ascii=False) if st else '不存在'}")
        return 2

    mood, inten, scene, length = info["last_injection"]
    print(f"✅ 注入次数：{info['injections']}")
    print(f"   最近一次：mood={mood} 强度={inten} 场景={scene} 长度={length} 字")
    if info.get("hits"):
        print(f"   触达：{info['hits']} 次，最近 {info['last_hit']}")
    else:
        print("   触达：暂无（回复里还没用上口癖/颜文字/尾巴）")

    block = (st.get("last") or {}).get("block")
    if block:
        print("-" * 62)
        print("真正塞给模型的那段：")
        print(block)
    else:
        print("-" * 62)
        print("（stats 里还没存 block 原文——该版本尚未重启生效，但不影响注入本身）")

    if st.get("hit_rate") is not None:
        print("-" * 62)
        print(f"触达率：{st['hit_rate']:.0%}（{st.get('hit')} 命中 / {st.get('miss')} 未中）")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait", type=int, default=0, help="最多等多少秒")
    args = ap.parse_args()
    raise SystemExit(report(args.wait))
