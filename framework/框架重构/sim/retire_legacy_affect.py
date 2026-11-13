"""旧 affect 停用评估：检查还有没有插件在读旧 affect_state。

用法：python retire_legacy_affect.py [--check]
输出：清单 + 停用建议（不自动执行）
"""
from __future__ import annotations
import sys

import argparse
import os
import re


try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
PLUGINS = r"%ASTRBOT_HOME%\data\plugins"
PD = r"%ASTRBOT_HOME%\data\plugin_data"

#: 白名单：新内核自己（写状态）、旧内核自己（写状态）、iris（用事件流不是 state）
WHITELIST = {"astrbot_plugin_whale_beams", "astrbot_plugin_whale_affect", "astrbot_plugin_iris_chat_memory"}

NEEDLES = ("affect_state.json", "whale_affect/affect_state")


def scan():
    readers = []
    for name in sorted(os.listdir(PLUGINS)):
        d = os.path.join(PLUGINS, name)
        if not os.path.isdir(d) or not name.startswith("astrbot_plugin_"):
            continue
        if name in WHITELIST:
            continue
        hit_old = None
        hit_new = None
        for root, _dirs, files in os.walk(d):
            if "__pycache__" in root or "/.git" in root:
                continue
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                try:
                    txt = open(os.path.join(root, fn), encoding="utf-8", errors="replace").read()
                except OSError:
                    continue
                if hit_old is None and any(n in txt for n in NEEDLES):
                    hit_old = os.path.relpath(os.path.join(root, fn), PLUGINS)
                if hit_new is None and ("beams_snapshot" in txt or "outlet_shadow" in txt):
                    hit_new = os.path.relpath(os.path.join(root, fn), PLUGINS)
        if hit_old and not hit_new:
            # 只读旧、完全没接新内核 → 真正的风险点
            readers.append((name, hit_old))
        elif hit_old and hit_new:
            # 已接新内核，旧只是回退路径
            pass
    return readers


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    readers = scan()
    print("旧 affect_state.json 仍有读取的插件：%d 个" % len(readers))
    for name, f in readers:
        print("  ⚠️ %-34s %s" % (name.replace("astrbot_plugin_", ""), f))
    if not readers:
        print("  ✅ 无读方（白名单除外）")
    print()
    print("白名单（不参与判定）：新内核 / 旧 affect 本体 / iris（用事件流）")
    print()
    print("停用建议（确认后再做）：")
    print("  1. AstrBot 管理面板 → 插件 → astrbot_plugin_whale_affect → 禁用/卸载")
    print("  2. 或临时把目录改名：astrbot_plugin_whale_affect → astrbot_plugin_whale_affect.disabled")
    print("  3. 停用后观察 1 小时：任何插件报'读不到情绪状态'则回滚")
    print("  4. 回滚：改回原名 + 重启")
    if readers:
        print()
        print("⚠️ 还有读方，暂不建议停用。")


if __name__ == "__main__":
    main()