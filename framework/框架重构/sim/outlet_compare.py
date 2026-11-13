"""S3 接管前观察：旧 outlet 决策 vs 新内核仲裁（sim/outlet_compare.py）

每调一次采样一份对照，追加到 06_S3接管观察.md。
回答：如果现在让新内核接管出口，它的判断和旧系统差多少？
"""
from __future__ import annotations
import sys

import json
import os
import time


try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
BASE = r"%ASTRBOT_HOME%\data\plugin_data"
OLD_OUTLET = os.path.join(BASE, "astrbot_plugin_whale_state", "outlet.json")
OLD_URGE = os.path.join(BASE, "astrbot_plugin_whale_state", "urge.json")
NEW_SNAP = os.path.join(BASE, "astrbot_plugin_whale_beams", "beams_snapshot.json")
OUT = r"%PROJECT_HOME%\框架重构\06_S3接管观察.md"

#: 旧出口 → 行动大类
OLD_CLASS = {"speak": "说话", "meme": "说话", "post": "分享", "draw": "探索",
             "scout": "探索", "quiet": "安静", "none": "安静"}
#: 新动机 → 行动大类
NEW_CLASS = {"connect": "说话", "complete": "分享", "explore": "探索",
             "repair": "安静", "avoid": "安静"}


def _rj(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def main():
    old = _rj(OLD_OUTLET)
    urge = _rj(OLD_URGE)
    new = _rj(NEW_SNAP)
    if not old or not new:
        print("旧/新状态文件缺失，先让两个内核各跑一会儿")
        return

    old_outlet = str(old.get("outlet") or "none")
    new_winner = str(new.get("outlet") or "")
    old_cls = OLD_CLASS.get(old_outlet, "其他")
    new_cls = NEW_CLASS.get(new_winner, "其他")
    agree = old_cls == new_cls

    lines = []
    lines.append("## %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    lines.append("")
    lines.append("| 维度 | 旧内核 | 新内核 |")
    lines.append("|---|---|---|")
    lines.append("| 出口 | %s（%s） | %s（%s） |" % (
        old_outlet, old.get("why") or "", new_winner, new.get("label") or ""))
    lines.append("| 紧迫度 | %.2f | %.2f |" % (
        float(old.get("urgency") or 0.0), float(new.get("urgency") or 0.0)))
    lines.append("| 行动大类 | %s | %s |" % (old_cls, new_cls))
    lines.append("| 是否一致 | %s | |" % ("✅ 一致" if agree else "❌ 分歧"))
    lines.append("| 旧 urge | tier=%s v=%.2f | |" % (
        urge.get("tier") or "?", float(urge.get("v") or 0.0)))
    lines.append("")
    text = "\n".join(lines)
    print(text)
    try:
        with open(OUT, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print("已追加：" + OUT)
    except OSError as exc:
        print("写报告失败：%s" % exc)


if __name__ == "__main__":
    main()