"""情绪分类校准：抽真实聊天记录，看 laya 8 类判得准不准。

用途：给情绪参数校准提供依据（如果 laya 把"撒娇"判成"生气"，情绪就会跑偏）。
输出：18_情绪校准样本.md
"""
from __future__ import annotations

import json
import sqlite3
import sys
import urllib.request

DB = r"%ASTRBOT_HOME%\data\data_v4.db"
OUT = r"%PROJECT_HOME%\框架重构\18_情绪校准样本.md"
LAYA = "http://127.0.0.1:8901/classify"
OWNER = "100000000"


def load_msgs(limit=40):
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = con.execute(
        "select content, sender_name, created_at from platform_message_history "
        "where sender_name=? order by id desc limit ?", (OWNER, limit)).fetchall()
    con.close()
    out = []
    for content, sender, ts in rows:
        try:
            obj = json.loads(content)
        except (ValueError, TypeError):
            continue
        text = "".join(str(p.get("text") or "") for p in (obj.get("message") or [])
                       if p.get("type") == "plain").strip()
        if text:
            out.append({"text": text, "ts": ts})
    return out


def classify(text):
    try:
        req = urllib.request.Request(
            LAYA, data=json.dumps({"text": text}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as exc:
        return {"error": str(exc)}


def main():
    msgs = load_msgs(40)
    lines = ["# 情绪分类校准样本（真实聊天）", ""]
    lines.append("- 样本：最近 %d 条主人消息，laya 8 类分类" % len(msgs))
    lines.append("- 用途：人工核验分类是否合理；不合理就调映射（不是调模型）")
    lines.append("")
    lines.append("| 消息 | laya 判定 | 置信度 | 备注 |")
    lines.append("|---|---|---|---|")
    dist = {}
    low = 0
    for m in msgs:
        r = classify(m["text"])
        if "error" in r:
            lines.append("| %s | 调用失败 | - | %s |" % (m["text"][:30], r["error"][:40]))
            continue
        label = r.get("label", "?")
        conf = float(r.get("confidence") or 0)
        dist[label] = dist.get(label, 0) + 1
        if conf < 0.5:
            low += 1
        lines.append("| %s | %s | %.2f | %s |" % (
            m["text"][:30], label, conf, "低置信" if conf < 0.5 else ""))
    lines.append("")
    lines.append("## 分布")
    for k, v in sorted(dist.items(), key=lambda kv: -kv[1]):
        lines.append("- %s：%d 条" % (k, v))
    lines.append("")
    lines.append("- 低置信（<0.5）：%d/%d" % (low, len(msgs)))
    lines.append("")
    lines.append("## 校准判断")
    lines.append("1. 如果\"撒娇/开心\"判成\"生气/难过\" → 情绪会整体偏负，需要调整 laya 极性映射。")
    lines.append("2. 低置信多 → 事件强度打折（当前 intensity=0.45+0.55*conf），避免误判带飞情绪。")
    lines.append("3. 人工核验后，把明显的错判记下来，改 `polarity` 表比换模型快。")
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines[:20]))
    print("\n报告：" + OUT)


if __name__ == "__main__":
    main()