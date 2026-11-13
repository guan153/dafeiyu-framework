# -*- coding: utf-8 -*-
"""把弱点账本翻译成「下一轮该做什么」。

这是自主迭代闭环的出口：
    自省巡检 → 账本+提案 → plan.py 生成迭代计划 → 人（或 agent）照着改 → 改好了标 verified

用法：
    python tools/plan.py              # 写 docs/迭代计划.md 并打印摘要
    python tools/plan.py --print      # 只打印，不写文件
    python tools/plan.py --limit 12   # 最多纳入多少条
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 默认读真机数据；可用 ASTRBOT_ROOT 指到别处做演练
os.environ.setdefault("ASTRBOT_ROOT", r"%ASTRBOT_HOME%")

from whale_core import paths  # noqa: E402
from whale_core.capabilities import get_registry  # noqa: E402
from whale_core.ledger import CATEGORY_LABEL, STATUS_LABEL, Ledger  # noqa: E402

CATEGORY_ORDER = {"conflict": 0, "bug": 1, "gap": 2, "perf": 3, "ux": 4, "safety": -1, "doc": 5}


def build(limit: int = 12) -> str:
    led = Ledger()
    stats = led.stats()
    items = led.top(limit)
    proposals = {p["key"]: p for p in led.proposals(limit=100)}
    registry = get_registry()

    lines = [
        f"# 鲸鱼少女 v3 迭代计划（{datetime.now():%Y-%m-%d %H:%M} 生成）",
        "",
        "> 本文件由 `qqbot_v3/tools/plan.py` 从弱点账本自动生成，**不要手改**；",
        "> 改完一项，用 `改好了 <关键词>` 或在账本里把状态置 verified，下次生成就会消失。",
        "",
        f"当前账本：{stats['total']} 条，未解决 {stats['open_count']} 条，总权重分 {stats['open_score']}。",
        "",
        "## 一、本轮建议动的（按分数）",
        "",
        "| # | 严重度 | 类别 | 状态 | 分数 | 问题 | 为什么（根因） | 建议动作 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for i, item in enumerate(items, 1):
        prop = proposals.get(item.key)
        action = prop["plan"] if prop else _default_action(item.category)
        action = action.replace("|", "／").replace("\n", " ")
        lines.append(
            f"| {i} | {item.severity} | {CATEGORY_LABEL.get(item.category, item.category)} | "
            f"{STATUS_LABEL.get(item.status, item.status)} | {item.score} | {item.title} | "
            f"{item.root_cause or '—'} | {action} |"
        )
    if not items:
        lines.append("| — | — | — | — | — | 账本是空的 | — | 先跑一轮 `自省` |")

    lines += ["", "## 二、能力缺口（她不会的事）", ""]
    # 能力表在机器人进程内存里，离线拿不到；优先读最近一次自省快照
    snap = {}
    snap_path = paths.core_dir() / "self_report.json"
    if snap_path.exists():
        try:
            snap = json.loads(snap_path.read_text(encoding="utf-8")).get("capabilities") or {}
        except Exception:
            snap = {}
    gaps = snap.get("gaps") or registry.gaps()
    if gaps:
        for g in gaps:
            lines.append(f"- **{g['label']}**（`{g['capability']}`）：没人提供 → 自研 / 接社区插件 / 明确不做")
    else:
        lines.append("- 没有缺口（或在离线环境读不到运行态能力表，需在机器人进程内查看）")

    conflicts = snap.get("conflicts")
    if conflicts is None:
        conflicts = registry.conflicts(include_resolved=True)
    lines += ["", "## 三、多提供者能力（确认仲裁是否合理）", ""]
    if conflicts:
        for c in conflicts:
            state = "已指定首选" if c["resolved"] else "**未仲裁**"
            lines.append(f"- `{c['capability']}`：{state}，提供者 {c['providers']}，首选 {c['preferred'] or '—'}")
    else:
        lines.append("- 无（或离线读不到运行态）")

    lines += [
        "",
        "## 四、验证纪律（每项都按这个走）",
        "",
        "1. 先复现并抓证据（账本 `evidence` 里有样本）；",
        "2. 改完加一条回归探针（探针/日志模式/单测任选）；",
        "3. 观察至少一个巡检周期（15 分钟）；",
        "4. 状态改 `verified` 并写一句「怎么验的」；",
        "5. 若再次出现，账本会自动标 `regressed`——这不是失败，是证据。",
        "",
        "## 五、本轮数字",
        "",
        f"- 生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}",
        f"- 账本路径：`{paths.ledger_db()}`",
        f"- 报告路径：`{paths.core_dir() / 'self_report.json'}`",
        f"- 未解决：{stats['open_count']}（P1 {stats['by_severity'].get('P1', 0)}｜"
        f"P2 {stats['by_severity'].get('P2', 0)}｜P3 {stats['by_severity'].get('P3', 0)}）",
    ]
    return "\n".join(lines) + "\n"


def _default_action(category: str) -> str:
    return {
        "conflict": "指定唯一提供者，其余降 standby",
        "bug": "复现→定位→修复→加回归探针",
        "gap": "评估三选一：自研 / 接插件 / 明确不做",
        "perf": "降频或异步化，观察 24h",
        "ux": "先改交互文案/节奏，再考虑代码",
        "safety": "立即收紧默认值",
        "doc": "抽公共库，消灭复制",
    }.get(category, "先复现再说")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--print", action="store_true", help="只打印")
    parser.add_argument("--limit", type=int, default=12)
    args = parser.parse_args()

    text = build(limit=args.limit)
    if not args.print:
        out = ROOT / "docs" / "迭代计划.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, out)
        print(f"已生成：{out}")
        # 顺便存一份机器可读的，给看板/后续 agent 用
        led = Ledger()
        data = {
            "generated_at": time.time(),
            "stats": led.stats(),
            "top": [w.to_dict() for w in led.top(args.limit)],
        }
        json_out = ROOT / "docs" / "迭代计划.json"
        json_out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已生成：{json_out}")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
