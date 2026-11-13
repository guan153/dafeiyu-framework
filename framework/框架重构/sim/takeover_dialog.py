"""S3 接管评估（对话时刻口径）：读 outlet_compare_at_dialog.jsonl 统计一致率。

正确口径：只在旧 outlet 真正刷新的时刻（收到消息时）对比新旧决策，
而不是任意时刻（旧 outlet 是懒更新，任意时刻比必然对不上）。

用法：python takeover_dialog.py
输出：%PROJECT_HOME%\框架重构\17_接管演练.md（追加）
"""
from __future__ import annotations
import sys

import json
import os


try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
SRC = r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_whale_beams\outlet_compare_at_dialog.jsonl"
OUT = r"%PROJECT_HOME%\框架重构\17_接管演练.md"

OLD_CLASS = {"speak": "说话", "meme": "说话", "post": "分享", "draw": "探索",
             "scout": "探索", "quiet": "安静", "none": "安静"}
NEW_CLASS = {"connect": "说话", "complete": "分享", "explore": "探索",
             "repair": "安静", "avoid": "安静"}


def load():
    rows = []
    try:
        with open(SRC, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        continue
    except OSError:
        pass
    return rows


def main():
    rows = load()
    lines = ["", "---", "", "## 对话时刻口径（正确口径）", ""]
    if not rows:
        lines.append("暂无对话时刻样本（等真实对话触发）。")
    else:
        agree = danger = 0
        detail = []
        for r in rows:
            oc = OLD_CLASS.get(str(r.get("old_outlet")), "其他")
            nc = NEW_CLASS.get(str(r.get("new_winner")), "其他")
            ok = oc == nc
            # 危险 = 旧想说、新变安静（接管后会变哑巴）
            # 改进 = 旧判安静、新想说话（用户主动找她时，旧系统判 quiet 本来就是错的）
            risky = (oc in ("说话", "分享")) and nc == "安静"
            improve = (oc == "安静") and nc in ("说话", "分享")
            agree += 1 if ok else 0
            danger += 1 if risky else 0
            detail.append((r.get("text"), r.get("old_outlet"), oc, r.get("new_winner"), nc,
                           ok, risky, r.get("gate_open"), r.get("route"), improve))
        rate = agree / len(rows)
        lines.append("- 样本 %d 条（对话时刻）" % len(rows))
        lines.append("- 行动大类一致率：**%.0f%%**（%d/%d）" % (rate * 100, agree, len(rows)))
        lines.append("- 危险分歧：%d 次" % danger)
        lines.append("")
        improves = sum(1 for d in detail if d[9])
        lines.append("- 改进（旧判安静、新想说话）：%d 次" % improves)
        lines.append("")
        lines.append("| 消息 | 旧 outlet | 旧大类 | 新 winner | 新大类 | 一致 | gate | route | 说明 |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for d in detail[-18:]:
            note = "走强" if d[5] else ("⚠️危险" if d[6] else ("改进" if d[9] else ""))
            lines.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s |"
                         % (str(d[0])[:18], d[1], d[2], d[3], d[4],
                            "✅" if d[5] else "❌", d[7], d[8], note))
        lines.append("")
        if rate >= 0.8 and danger == 0:
            lines.append("**可进入 S3.2 单出口切换**（对话时刻一致率 ≥80% 且无危险分歧）。")
        elif danger == 0:
            lines.append("无危险分歧，但大类仍不一致——建议先切低风险出口（post/scout），再观察 speak。")
        else:
            lines.append("**暂不切换**：存在危险分歧，先查 gate 与映射。")
    text = "\n".join(lines)
    print(text)
    with open(OUT, "a", encoding="utf-8") as fh:
        fh.write(text + "\n")
    print("\n已追加：" + OUT)


if __name__ == "__main__":
    main()