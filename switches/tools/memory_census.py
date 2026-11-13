# -*- coding: utf-8 -*-
"""记忆普查：五套记忆到底谁在写、写了多少、有没有重复。

用法：
    python tools/memory_census.py             # 打印表格 + 写 docs/记忆普查.md
    python tools/memory_census.py --json      # 只输出 JSON
    python tools/memory_census.py --ledger    # 顺便把结论写进弱点账本
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("ASTRBOT_ROOT", r"%ASTRBOT_HOME%")

from whale_core.memory import LAYERS, get_memory_hub  # noqa: E402


def _fmt_time(ts: float) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")


def _fmt_age(sec: float) -> str:
    if not sec:
        return "—"
    if sec < 3600:
        return f"{int(sec / 60)} 分钟前"
    if sec < 86400:
        return f"{sec / 3600:.1f} 小时前"
    return f"{sec / 86400:.1f} 天前"


def render(rep: dict) -> str:
    census = rep["census"]
    dups = rep["duplicates"]
    contract = rep["contract"]
    lines = [
        f"# 记忆普查（{datetime.now():%Y-%m-%d %H:%M}）",
        "",
        "> 由 `qqbot_v3/tools/memory_census.py` 生成。只读，不改任何写入行为。",
        "",
        f"共 {census['total_records']} 条事实性记忆（不含派生索引）。",
        "",
        "## 一、分层与写者",
        "",
        "| 层 | 说明 | 写者 | 是否合规 |",
        "|---|---|---|---|",
    ]
    violations = {v["layer"] for v in contract["violations"]}
    for layer, meta in census["layers"].items():
        writers = "、".join(meta["providers"]) or "—"
        ok = "✅" if layer not in violations else "❌ 多写者"
        lines.append(f"| {meta['label']} | `{layer}` | {writers} | {ok} |")

    lines += [
        "",
        "## 二、逐库实况",
        "",
        "| 库 | 层 | 条数 | 体积 | 最后写入 | 距今 | 备注 |",
        "|---|---|---|---|---|---|---|",
    ]
    for p in census["providers"]:
        lines.append(
            f"| `{p['name']}` | {p['layer_label']} | {p['records']} | {p['size_kb']:.1f} KB | "
            f"{_fmt_time(p['last_write'])} | {_fmt_age(p['stale_sec'])} | {p['error'] or p['note'] or '—'} |"
        )

    lines += [
        "",
        "## 三、查重",
        "",
        f"- 跨库重复：**{dups['cross_provider_duplicates']}** 条（同一句话被两套记忆各记一遍）",
        f"- 库内重复：**{dups['same_provider_duplicates']}** 条",
        f"- 抽样比对文本量：{dups['checked_texts']} 条",
    ]
    if dups["samples"]:
        lines += ["", "样例："]
        for sample in dups["samples"][:5]:
            desc = " ／ ".join(f"{i['provider']}：{i['text']}" for i in sample)
            lines.append(f"- {desc}")

    lines += ["", "## 四、契约检查", ""]
    if contract["ok"]:
        lines.append("- 每层都只有一个写者，合规。")
    else:
        for v in contract["violations"]:
            lines.append(f"- ❌ `{v['layer']}`（{v['label']}）多写者：{'、'.join(v['writers'])}")
    lines += [
        "",
        "## 五、结论与建议",
        "",
        _conclusion(rep),
    ]
    return "\n".join(lines) + "\n"


def _conclusion(rep: dict) -> str:
    census = rep["census"]
    by_name = {p["name"]: p for p in census["providers"]}
    dups = rep["duplicates"]
    bullets = []
    facts = [p for p in census["providers"] if p["layer"] != "index" and p["records"] > 0]
    if dups["cross_provider_duplicates"] == 0:
        bullets.append(
            "- 跨库重复为 0：所谓「五套记忆互相重复」是传说。它们是**分层**，不是复制。"
        )
    if len(facts) >= 3:
        bullets.append(
            f"- 真正的病：{len(facts)} 套记忆都在 `on_llm_request` 里各自插一段，"
            "没有统一召回入口与全局预算 → 互相挤占、可能互相矛盾。"
        )
    if by_name.get("memoryflow", {}).get("records", 0) > 0:
        bullets.append("- `memoryflow` 是派生索引（从 iris 重建），坏了重建即可，不需要迁移或备份。")
    if by_name.get("soul", {}).get("records", 0) > 0 and by_name.get("iris", {}).get("records", 0) > 0:
        bullets.append(
            "- `soul`（原话+重要性）与 `iris`（摘要+向量）**不是重复，是两层**："
            "情景记忆回答「她记得那句原话」，语义记忆回答「她能想起来并泛化」，不建议合并。"
        )
    if by_name.get("whale_memory", {}).get("records", 0) > 0:
        bullets.append(
            "- `whale_memory` 写的是档案与每日日志（markdown），属于「档案层」，"
            "和 soul 的原话记忆不同层；它体积小、可读，适合保留。"
        )
    if not bullets:
        bullets.append("- 暂无明显问题。")
    return "\n".join(bullets)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--ledger", action="store_true", help="把结论写进弱点账本")
    args = parser.parse_args()

    hub = get_memory_hub()
    rep = hub.report()

    if args.ledger:
        from whale_core.ledger import get_ledger

        touched = hub.ingest_findings(get_ledger())
        if not args.json:
            print(f"已写入账本 {len(touched)} 条：{touched}\n")

    text = render(rep)
    if args.json:
        print(json.dumps({"report": rep, "markdown": text}, ensure_ascii=False, indent=2))
        return 0

    out = ROOT / "docs" / "记忆普查.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(text)
    print(f"已写入：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
