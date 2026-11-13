"""新旧情绪对比：记录两套系统同刻的判断，量化"旧系统有多不准"。

用法：python affect_compare.py [--append]
输出：24_新旧情绪对比.md
"""
from __future__ import annotations

import argparse
import json
import os
import time

PD = r"%ASTRBOT_HOME%\data\plugin_data"
NEW = os.path.join(PD, "astrbot_plugin_whale_beams", "beams_snapshot.json")
OLD = os.path.join(PD, "astrbot_plugin_whale_affect", "affect_state.json")
OUT = r"%PROJECT_HOME%\框架重构\24_新旧情绪对比.md"


def rj(p):
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--append", action="store_true", help="追加一次采样（不写整份报告）")
    args = ap.parse_args()
    n = rj(NEW)
    o = rj(OLD)
    old_ts = float(o.get("ts") or 0)
    new_ts = float(n.get("ts") or 0)
    old_fresh = time.time() - old_ts < 300
    row = {
        "ts": time.strftime("%H:%M:%S"),
        "new_label": n.get("label"),
        "new_v": round(float(n.get("valence") or 0), 3),
        "old_label": o.get("label") if old_fresh else "(旧数据过期)",
        "old_v": round(float((o.get("core") or {}).get("valence") or 0), 3) if old_fresh else None,
        "diff": round(abs(float(n.get("valence") or 0) - float((o.get("core") or {}).get("valence") or 0)), 3) if old_fresh else None,
    }
    print("采样：%s" % " · ".join("%s=%s" % (k, v) for k, v in row.items()))

    if args.append:
        with open(OUT, "a", encoding="utf-8") as fh:
            fh.write("| %s | %s | %+.3f | %s | %s | %s |\n" % (
                row["ts"], row["new_label"], row["new_v"], row["old_label"],
                ("%+.3f" % row["old_v"]) if row["old_v"] is not None else "—",
                ("%.3f" % row["diff"]) if row["diff"] is not None else "—"))
        print("已追加：" + OUT)
        return

    # 全量报告：读历史采样（如果表头存在）
    lines = ["# 新旧情绪对比（同一时刻）", "",
             "说明：新内核（beams）vs 旧 affect。旧系统判断明显错误时，差值会很大。", ""]
    if os.path.isfile(OUT):
        body = open(OUT, encoding="utf-8").read()
        if "| 时间 | 新 label | 新效价 | 旧 label | 旧效价 | 差值 |" in body:
            lines.append(body.split("| 时间 |", 1)[1])
            lines[2:3] = []
    else:
        lines += ["| 时间 | 新 label | 新效价 | 旧 label | 旧效价 | 差值 |",
                  "|---|---|---|---|---|---|"]
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()