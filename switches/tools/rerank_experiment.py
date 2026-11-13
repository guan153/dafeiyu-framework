# -*- coding: utf-8 -*-
"""重排实验：向量粗召回 top5 → DeepSeek 重排 → 只留模型认为相关的。

背景：向量绝对阈值不可靠（0.65 时精度 43.5%，0.70 时几乎不召回）。
这是检索系统的典型解法：**召回宽松一点，重排严格一点**。

实验口径：
- 粗召回：vector >= 0.50，top 5；
- 重排：问 DeepSeek「哪几条与这句相关」，只输出编号；
- 评判：再用一个裁判调用（同模型，标注偏差风险）判断最终留下的条目。

用法：
    python tools/rerank_experiment.py --sample 20
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
os.environ.setdefault("ASTRBOT_ROOT", r"%ASTRBOT_HOME%")

from recall_coverage import load_messages  # noqa: E402
from recall_precision import API_URL, judge, load_key  # noqa: E402
from whale_core import memory_index  # noqa: E402


def rerank(key: str, message: str, candidates: list[dict], timeout: float = 20.0) -> list[dict]:
    if not candidates:
        return []
    listing = "\n".join(f"{i}. {c['text']}" for i, c in enumerate(candidates, 1))
    prompt = (
        "下面是一句用户消息，以及候选的长期记忆。\n"
        "只挑出**真正相关、且看着这句消息值得想起**的记忆；宁缺勿滥。\n\n"
        f"用户消息：{message}\n\n候选记忆：\n{listing}\n\n"
        '只输出 JSON：{"keep":[1,3]}（没有相关的就 {"keep":[]}）'
    )
    body = json.dumps({
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 200,
        "response_format": {"type": "json_object"},
    }).encode("utf-8")
    req = urllib.request.Request(
        API_URL, data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
            keep = set(int(x) for x in (json.loads(content).get("keep") or []))
            return [c for i, c in enumerate(candidates, 1) if i in keep]
        except Exception:  # noqa: BLE001
            time.sleep(1.0)
    return []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=20)
    parser.add_argument("--low", type=float, default=0.50)
    args = parser.parse_args()

    key = load_key()
    if not key:
        print("没有 API key")
        return 1

    index = memory_index.get_index()
    messages = load_messages(max(300, args.sample * 12))

    rows = []
    for message in messages:
        cands = index.search(message, limit=5, min_score=args.low)
        if not cands:
            continue
        kept = rerank(key, message, cands)
        rows.append({
            "message": message,
            "candidates": [c["text"][:80] for c in cands],
            "kept": [c["text"] for c in kept],
        })
        if len(rows) >= args.sample:
            break

    judged = relevant = 0
    useful = 0
    for row in rows:
        if not row["kept"]:
            continue
        try:
            flags = judge(key, row["message"], row["kept"])
        except Exception:  # noqa: BLE001
            continue
        judged += len(flags)
        relevant += sum(1 for f in flags if f)
        if any(flags):
            useful += 1
        row["flags"] = flags

    precision = round(relevant / judged, 3) if judged else 0.0
    print(f"采样消息：{len(rows)}（有候选的）")
    print(f"粗召回后仍有保留的消息：{sum(1 for r in rows if r['kept'])}（{sum(1 for r in rows if r['kept'])/len(rows)*100:.1f}%）")
    print(f"判定记忆：{judged} 条，相关 {relevant} 条")
    print(f"精度：{precision*100:.1f}%")
    print(f"有相关记忆的消息占比：{useful/len(rows)*100:.1f}%")

    out = ROOT / "docs" / "重排实验.md"
    lines = [
        "# 重排实验（向量粗召回 + LLM 重排）",
        "",
        f"- 粗召回：vector >= {args.low}，top5",
        f"- 采样：{len(rows)} 句有候选的消息",
        f"- 重排后仍保留：{sum(1 for r in rows if r['kept'])} 句",
        f"- 判定 {judged} 条，相关 {relevant} 条，**精度 {precision*100:.1f}%**",
        "",
        "## 明细",
        "",
    ]
    for row in rows[:12]:
        lines.append(f"### 「{row['message'][:40]}」")
        lines.append(f"- 候选：{row['candidates']}")
        lines.append(f"- 重排保留：{row['kept']}")
        if "flags" in row:
            lines.append(f"- 裁判：{row['flags']}")
        lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"已写入：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
