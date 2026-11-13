"""S3.1 接管演练：连续采样新旧 outlet，统计一致率与危险分歧。

用法：python takeover_drill.py --n 10 --interval 5
输出：%PROJECT_HOME%\框架重构\17_接管演练.md
"""
from __future__ import annotations
import sys

import argparse
import json
import os
import time


try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
PLUGIN = r"%ASTRBOT_HOME%\data\plugin_data"
OLD = os.path.join(PLUGIN, "astrbot_plugin_whale_state", "outlet.json")
NEW = os.path.join(PLUGIN, "astrbot_plugin_whale_beams", "outlet_shadow.json")
OUT = r"%PROJECT_HOME%\框架重构\17_接管演练.md"

OLD_CLASS = {"speak": "说话", "meme": "说话", "post": "分享", "draw": "探索",
             "scout": "探索", "quiet": "安静", "none": "安静"}
NEW_CLASS = {"connect": "说话", "complete": "分享", "explore": "探索",
             "repair": "安静", "avoid": "安静"}


def read(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--interval", type=float, default=5.0)
    args = ap.parse_args()

    rows, agree, danger = [], 0, 0
    for i in range(args.n):
        old = read(OLD)
        new = read(NEW)
        o = str(old.get("outlet") or "none")
        n = str(new.get("outlet") or "?")
        oc = OLD_CLASS.get(o, "其他")
        # 注意：outlet_shadow 里写的是"旧格式的值"（speak/scout/quiet/post），
        # 所以也要用 OLD_CLASS 映射大类；NEW_CLASS 只用于真实 winner（connect 等）
        nc = OLD_CLASS.get(n) or NEW_CLASS.get(n, "其他")
        ok = oc == nc
        # 危险分歧：旧说安静、新说要说话（或反过来）
        risky = (oc == "安静" and nc in ("说话", "分享")) or (oc in ("说话", "分享") and nc == "安静")
        agree += 1 if ok else 0
        danger += 1 if risky else 0
        rows.append({"i": i + 1, "old": o, "old_class": oc, "new": n, "new_class": nc,
                     "ok": ok, "risky": risky,
                     "old_urgency": old.get("urgency"), "new_urgency": new.get("urgency")})
        time.sleep(max(0.5, args.interval))

    rate = agree / max(1, len(rows))
    lines = ["# S3.1 接管演练报告", ""]
    lines.append("- 采样 %d 次（每 %.0fs 一次）" % (len(rows), args.interval))
    lines.append("- 行动大类一致率：**%.0f%%**（%d/%d）" % (rate * 100, agree, len(rows)))
    lines.append("- 危险分歧（安静↔说话）：%d 次" % danger)
    lines.append("")
    lines.append("| # | 旧 outlet | 旧大类 | 新 winner | 新大类 | 一致 | 危险 |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in rows:
        lines.append("| %d | %s | %s | %s | %s | %s | %s |"
                     % (r["i"], r["old"], r["old_class"], r["new"], r["new_class"],
                        "✅" if r["ok"] else "❌", "⚠️" if r["risky"] else ""))
    lines.append("")
    lines.append("## 判定")
    if rate >= 0.8 and danger == 0:
        lines.append("**可以进入 S3.2 单出口切换**（一致率 ≥80% 且无危险分歧）。")
    elif rate >= 0.6:
        lines.append("接近可切，但仍有分歧——建议继续影子观察，或先切低风险出口（post/scout）。")
    else:
        lines.append("**暂不切换**：一致率偏低，先修映射或内核参数。")
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print("\n报告：" + OUT)


if __name__ == "__main__":
    main()