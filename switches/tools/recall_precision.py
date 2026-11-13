# -*- coding: utf-8 -*-
"""召回精度评测：让另一个模型当裁判，判断「想起的这条到底相关不相关」。

为什么需要它：覆盖率只能说明「想起了东西」，说明不了「想起的是对的」。
中文 2-gram 召回的弱命中里混着大量误报（问「今天过得怎么样」召回「我喜欢你不穿的怎么样」），
必须用相关性判定来量精度，否则切注入就是把垃圾喂给模型。

做法：抽样真实用户消息 → 统一召回 top3 → 逐条问 DeepSeek「这条记忆和这句消息相关吗」。
成本：约 20 次短请求，很便宜；失败不写入结果。

用法：
    python tools/recall_precision.py                # 采样 20 句
    python tools/recall_precision.py --sample 30
    python tools/recall_precision.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("ASTRBOT_ROOT", r"%ASTRBOT_HOME%")

from whale_core import memory_recall  # noqa: E402

SECRETS = Path(r"%USERPROFILE%\.config\v1-gateway\gateway_secrets.json")
API_URL = "https://api.deepseek.com/v1/chat/completions"


def load_key() -> str:
    try:
        return str(json.loads(SECRETS.read_text(encoding="utf-8")).get("api_key") or "")
    except Exception:  # noqa: BLE001
        return ""


def load_queries(limit: int) -> list[str]:
    # 复用覆盖率工具的口径
    sys.path.insert(0, str(ROOT / "tools"))
    from recall_coverage import load_messages  # type: ignore

    return load_messages(limit)


VARIANTS = {
    # 全层关键词召回（现状）
    "all": {"sources": None, "min_matched": 1},
    # 只信事实层：档案 + 摘要（jieba 分词后，单关键词即可）
    "facts": {"sources": ["semantic", "profile"], "min_matched": 1},
    # 事实层 + 强情景（原话也算）
    "facts_plus": {"sources": ["semantic", "profile", "episodic"], "min_matched": 1},
    # 事实层但要求 ≥2 词命中（更保守）
    "facts2": {"sources": ["semantic", "profile"], "min_matched": 2},
    # 向量召回（DashScope text-embedding-v4 + 本地索引）
    "vector": {"vector": True, "min_score": 0.45},
    "vector_loose": {"vector": True, "min_score": 0.38},
    "vector_55": {"vector": True, "min_score": 0.55},
    "vector_60": {"vector": True, "min_score": 0.60},
    "vector_65": {"vector": True, "min_score": 0.65},
    "vector_70": {"vector": True, "min_score": 0.70},
    "hybrid": {"vector": True, "min_score": 0.65, "low_score": 0.55, "keyword_min": 2},
}


def judge(key: str, message: str, memories: list[str], timeout: float = 30.0) -> list[bool]:
    listing = "\n".join(f"{i}. {m}" for i, m in enumerate(memories, 1))
    prompt = (
        "你是记忆系统评测员。判断下列每条「记忆」与这句用户消息是否相关"
        "（相关 = 看着这句话想起这条记忆，对回应有帮助；同主题、同事件、同偏好才算，"
        "仅仅共享「怎么/什么/今天」这种虚词不算）。\n\n"
        f"用户消息：{message}\n\n记忆：\n{listing}\n\n"
        '只输出 JSON：{"items":[{"i":1,"relevant":true},{"i":2,"relevant":false}]}'
    )
    body = json.dumps({
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 400,
        "response_format": {"type": "json_object"},
    }).encode("utf-8")
    req = urllib.request.Request(
        API_URL, data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    parsed = json.loads(content)
    flags = {int(item.get("i")): bool(item.get("relevant")) for item in parsed.get("items") or []}
    return [flags.get(i, False) for i in range(1, len(memories) + 1)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=20)
    parser.add_argument("--variant", choices=list(VARIANTS), default="all")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    key = load_key()
    if not key:
        print("没有可用的 API key，跳过")
        return 1

    variant = VARIANTS[args.variant]
    queries = load_queries(max(200, args.sample * 12))
    rows = []
    if variant.get("vector"):
        from whale_core import memory_index

        index = memory_index.get_index()
        for message in queries:
            hits = index.search(
                message, limit=3,
                min_score=float(variant["min_score"]),
                low_score=float(variant.get("low_score", 0)),
                keyword_min=int(variant.get("keyword_min", 0)),
            )
            if not hits:
                continue
            rows.append({
                "message": message,
                "hits": [h["text"] for h in hits],
                "sources": [h["source"] for h in hits],
                "scores": [h["score"] for h in hits],
            })
            if len(rows) >= args.sample:
                break
    else:
        for message in queries:
            hits = memory_recall.recall(
                message, limit=3,
                sources=variant["sources"],
                min_matched=variant["min_matched"],
            )
            if not hits:
                continue
            rows.append({"message": message, "hits": [h.text for h in hits], "sources": [h.source for h in hits]})
            if len(rows) >= args.sample:
                break

    judged = 0
    relevant = 0
    useful_queries = 0
    details = []
    for row in rows:
        try:
            flags = judge(key, row["message"], row["hits"])
        except Exception as exc:  # noqa: BLE001
            print(f"跳过一条（{type(exc).__name__}: {exc}）")
            continue
        judged += len(flags)
        relevant += sum(1 for f in flags if f)
        if any(flags):
            useful_queries += 1
        details.append({
            "message": row["message"],
            "hits": [{"text": t, "source": s, "relevant": f} for t, s, f in zip(row["hits"], row["sources"], flags)],
        })
        time.sleep(0.3)

    if judged == 0:
        print("没有判定成功的结果")
        return 1

    precision = round(relevant / judged, 3)
    useful_rate = round(useful_queries / len(details), 3) if details else 0.0
    result = {
        "variant": args.variant,
        "sampled_queries": len(details),
        "judged_hits": judged,
        "relevant_hits": relevant,
        "precision": precision,
        "queries_with_relevant": useful_queries,
        "useful_query_rate": useful_rate,
        "details": details,
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    lines = [
        f"# 召回精度（{datetime.now():%Y-%m-%d %H:%M}）",
        "",
        "> 由 `qqbot_v3/tools/recall_precision.py` 生成；裁判 = deepseek-chat，temperature 0。",
        "",
        f"- 采样消息：{len(details)} 句",
        f"- 判定记忆：{judged} 条",
        f"- 相关：{relevant} 条",
        f"- **精度：{precision * 100:.1f}%**",
        f"- 有至少一条相关记忆的消息占比：**{useful_rate * 100:.1f}%**",
        "",
        "## 逐条明细（前 12）",
        "",
    ]
    for row in details[:12]:
        lines.append(f"### 「{row['message'][:40]}」")
        for item in row["hits"]:
            mark = "✅" if item["relevant"] else "❌"
            lines.append(f"- {mark} [{item['source']}] {item['text'][:70]}")
        lines.append("")
    text = "\n".join(lines) + "\n"
    out = ROOT / "docs" / "召回精度.md"
    out.write_text(text, encoding="utf-8")
    print(text)
    print(f"已写入：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
