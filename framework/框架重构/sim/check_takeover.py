"""接管覆盖度检查：验证哪些插件已经在读新内核状态。

用法：python check_takeover.py
"""
from __future__ import annotations
import sys

import os
import re


try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
PLUGINS = r"%ASTRBOT_HOME%\data\plugins"
BEAMS_SNAP = "beams_snapshot.json"
SHADOW = "outlet_shadow.json"

#: 期望已接管的插件（文件名包含即可）
TARGETS = [
    ("astrbot_plugin_whale_drive", SHADOW),
    ("astrbot_plugin_proactive_chat", SHADOW),
    ("astrbot_plugin_dafeiyu_meme", BEAMS_SNAP),
    ("astrbot_plugin_whale_bili", BEAMS_SNAP),
    ("astrbot_plugin_whale_state", BEAMS_SNAP),
    ("astrbot_plugin_whale_voice", BEAMS_SNAP),
    ("astrbot_plugin_whale_urge", BEAMS_SNAP),
    ("astrbot_plugin_whale_brain", "ENABLE_BEAMS_EMOTION"),
]


def scan(plugin, needle):
    d = os.path.join(PLUGINS, plugin)
    if not os.path.isdir(d):
        return None, "目录不存在"
    for root, _dirs, files in os.walk(d):
        if "__pycache__" in root:
            continue
        for fn in files:
            if not fn.endswith(".py"):
                continue
            try:
                txt = open(os.path.join(root, fn), encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            if needle in txt:
                return fn, "OK"
    return None, "未找到 " + needle


def main():
    print("接管覆盖度检查（%d 个目标）\n" % len(TARGETS))
    ok = 0
    for plugin, needle in TARGETS:
        fn, msg = scan(plugin, needle)
        mark = "✅" if msg == "OK" else "❌"
        ok += 1 if msg == "OK" else 0
        print("  %s %-32s %s（%s）" % (mark, plugin.replace("astrbot_plugin_", ""), msg, fn or "-"))
    print("\n覆盖：%d/%d" % (ok, len(TARGETS)))
    # flag 状态
    flag = r"%PROJECT_HOME%\框架重构\ENABLE_BEAMS_EMOTION.flag"
    print("whale_brain 灰度开关：%s" % ("开" if os.path.isfile(flag) else "关"))
    print("iris_chat_memory：保留旧链路（低优先级，见 16 号文档）")


if __name__ == "__main__":
    main()