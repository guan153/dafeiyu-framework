# -*- coding: utf-8 -*-
"""召回覆盖率评测：用她真实的聊天记录，量「统一召回」到底能不能想起东西。

这是「记忆收敛」切注入前的最后一道证据：
如果对真实用户消息的召回覆盖率（≥1 条命中）达不到 80%，
切过去就是丢记忆，坚决不切。

用法：
    python tools/recall_coverage.py                # 默认最近 120 条用户消息
    python tools/recall_coverage.py --limit 200
    python tools/recall_coverage.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("ASTRBOT_ROOT", r"%ASTRBOT_HOME%")

from whale_core import memory_recall, paths  # noqa: E402

THRESHOLD = 0.80


def load_messages(limit: int) -> list[str]:
    db = paths.data_dir() / "data_v4.db"
    if not db.exists():
        return []
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT content FROM platform_message_history "
            "WHERE sender_name NOT IN ('bot','astrbot') ORDER BY id DESC LIMIT ?",
            (limit * 3,),
        ).fetchall()
    finally:
        con.close()

    out: list[str] = []
    seen: set[str] = set()
    for (raw,) in rows:
        try:
            data = json.loads(raw)
            if data.get("type") != "user":
                continue
            text = "".join(
                str(part.get("text") or "") for part in (data.get("message") or [])
                if isinstance(part, dict) and part.get("type") == "plain"
            ).strip()
        except Exception:  # noqa: BLE001
            continue
        if not (4 <= len(text) <= 120):
            continue
        key = text[:40]
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def evaluate(messages: list[str], limit: int = 5, variant: str = "all") -> dict:
    from recall_precision import VARIANTS  # type: ignore  # 同目录复用定义

    conf = VARIANTS.get(variant, {"sources": None, "min_matched": 1})
    rows = []
    by_source: dict[str, int] = {}
    misses: list[str] = []
    for text in messages:
        hits = memory_recall.recall(
            text, limit=limit, sources=conf["sources"], min_matched=conf["min_matched"]
        )
        sources = [h.source for h in hits]
        for s in sources:
            by_source[s] = by_source.get(s, 0) + 1
        strong = any(int((h.meta or {}).get("matched") or 0) >= 2 for h in hits)
        rows.append({
            "query": text,
            "hits": len(hits),
            "strong": strong,
            "matched": [int((h.meta or {}).get("matched") or 0) for h in hits],
            "sources": sources,
            "top": (hits[0].text[:60] if hits else ""),
            "terms": memory_recall.extract_terms(text),
        })
        if not hits and len(misses) < 30:
            misses.append(text)

    total = len(rows)
    covered = sum(1 for r in rows if r["hits"] > 0)
    strong = sum(1 for r in rows if r["strong"])
    avg_hits = round(sum(r["hits"] for r in rows) / total, 2) if total else 0.0
    return {
        "sampled": total,
        "covered": covered,
        "coverage_rate": round(covered / total, 3) if total else 0.0,
        "strong": strong,
        "strong_rate": round(strong / total, 3) if total else 0.0,
        "avg_hits": avg_hits,
        "by_source": by_source,
        "misses": misses,
        "rows": rows,
    }


def render(result: dict) -> str:
    rate = result["coverage_rate"]
    strong = result["strong_rate"]
    verdict = (
        "强命中达标，可以考虑切注入（仍需 48h 观察）"
        if strong >= THRESHOLD
        else f"强命中 {strong * 100:.1f}% 未达 {THRESHOLD * 100:.0f}%，**禁止切注入**"
    )
    lines = [
        f"# 召回覆盖率（{datetime.now():%Y-%m-%d %H:%M}）",
        "",
        "> 由 `qqbot_v3/tools/recall_coverage.py` 生成。只读，不注入。",
        "",
        f"- 采样：{result['sampled']} 条真实用户消息（去重、4-120 字）",
        f"- 弱命中（≥1 条）：**{rate * 100:.1f}%**（{result['covered']}/{result['sampled']}）",
        f"- 强命中（同一条里 ≥2 个词同时命中）：**{strong * 100:.1f}%**（{result['strong']}/{result['sampled']}）",
        f"- 平均命中：{result['avg_hits']} 条/句",
        f"- 阈值：{THRESHOLD * 100:.0f}%（看强命中）",
        f"- 判定：**{verdict}**",
        "",
        "## 为什么要分成弱/强",
        "",
        "中文 2-gram 很容易「看起来命中」：问「今天过得怎么样」，能和「我喜欢你不穿的怎么样」共享",
        "「怎么/么样」。弱命中率高只是说明词表覆盖广，不代表想起了对的事。",
        "强命中要求同一条记忆命中 ≥2 个词，才更可能是真的相关。",
        "",
        "## 来源分布（命中条数）",
        "",
    ]
    for source, count in sorted(result["by_source"].items(), key=lambda kv: -kv[1]):
        lines.append(f"- `{source}`：{count}")
    if not result["by_source"]:
        lines.append("- 无")

    lines += ["", "## 强命中样例（前 10）", ""]
    shown = 0
    for row in result["rows"]:
        if row["strong"] and shown < 10:
            lines.append(
                f"- 「{row['query'][:40]}」→ {row['hits']} 条｜词命中 {row['matched']}｜{row['top']}"
            )
            shown += 1
    if shown == 0:
        lines.append("- 无")

    lines += ["", "## 想不起来的 / 只有弱命中的（前 15）", ""]
    weak = [r["query"] for r in result["rows"] if not r["strong"]]
    for text in weak[:15]:
        lines.append(f"- 「{text[:50]}」")
    if not weak:
        lines.append("- 无")

    lines += [
        "",
        "## 口径说明",
        "",
        "- 「命中」= 关键词 2-gram 任一命中任意一层；",
        "- 覆盖率低通常不是记忆没了，而是**说法变了**（她说「唱歌」，你说「吼两嗓子」）；",
        "- 这也是为什么不能只靠关键词：切注入前要看 miss 里有多少是真该想起却没想起的。",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=120)
    parser.add_argument("--variant", default="all", help="all / facts / facts_plus")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    messages = load_messages(args.limit)
    if not messages:
        print("没读到用户消息（database 可能是空的或被占用）")
        return 1
    result = evaluate(messages, variant=args.variant)
    result["variant"] = args.variant
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    text = render(result)
    out = ROOT / "docs" / f"召回覆盖率-{args.variant}.md"
    out.write_text(text, encoding="utf-8")
    print(text)
    print(f"已写入：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
