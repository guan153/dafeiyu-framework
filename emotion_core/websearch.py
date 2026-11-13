# -*- coding: utf-8 -*-
"""免密钥联网检索（Bing 主 / 搜狗备）。

为什么自己做：AstrBot 自带 web_search 工具，但需要 Tavily/Bocha/Brave 的 key，
本机一个都没配（`provider_settings.web_search=false`）。
与其让它一直挂着「能力缺口」，不如给一个**不需要密钥**的兜底。

实测定下的路线（2026-11-07）：
- DuckDuckGo：国内直连超时，弃用；
- 百度：返回「百度安全验证」，弃用；
- Bing（cn.bing.com）：可用，约 7-8 秒；
- 搜狗：可用，约 1-2 秒，作备用。

边界（写清楚，免得被当成万能）：
- 只取标题/链接/摘要，不抓正文、不做反爬对抗；
- 任一环节失败返回空列表，绝不抛异常打断对话；
- 结果一律标注「来自网络，未核实」，避免她当成事实转述。
"""

from __future__ import annotations

import html
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from typing import Any

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_CACHE: dict[str, tuple[float, list[dict[str, str]]]] = {}
CACHE_TTL = 300.0


@dataclass
class Result:
    title: str
    url: str
    snippet: str = ""
    engine: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def _fetch(url: str, timeout: float) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_bing(page: str, limit: int) -> list[Result]:
    results: list[Result] = []
    for block in re.findall(r'<li class="b_algo".*?</li>', page, re.S):
        m = re.search(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not m:
            continue
        url = html.unescape(m.group(1))
        title = _clean(m.group(2))
        snip_m = re.search(r'<p[^>]*>(.*?)</p>', block, re.S)
        snippet = _clean(snip_m.group(1)) if snip_m else ""
        if title and url.startswith("http"):
            results.append(Result(title=title, url=url, snippet=snippet[:240], engine="bing"))
        if len(results) >= limit:
            break
    return results


def _parse_sogou(page: str, limit: int) -> list[Result]:
    results: list[Result] = []
    for m in re.finditer(
        r'<h3[^>]*class="vr-title"[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
        page,
        re.S,
    ):
        href = html.unescape(m.group(1))
        title = _clean(m.group(2))
        if href.startswith("/"):
            href = "https://www.sogou.com" + href
        if not title:
            continue
        rest = page[m.end(): m.end() + 1200]
        snip_m = re.search(r'<div[^>]*class="text-layout"[^>]*>(.*?)</div>', rest, re.S)
        snippet = _clean(snip_m.group(1)) if snip_m else ""
        results.append(Result(title=title, url=href, snippet=snippet[:240], engine="sogou"))
        if len(results) >= limit:
            break
    return results


def search(query: str, limit: int = 3, timeout: float = 12.0) -> list[dict[str, str]]:
    """返回 [{title,url,snippet,engine}]；失败返回空列表，不抛异常。"""
    query = (query or "").strip()
    if not query:
        return []
    limit = max(1, min(5, int(limit or 3)))
    key = f"{query}|{limit}"
    cached = _CACHE.get(key)
    if cached and time.time() - cached[0] < CACHE_TTL:
        return cached[1]

    plan = [
        ("bing", "https://cn.bing.com/search?q=" + urllib.parse.quote(query), _parse_bing),
        ("sogou", "https://www.sogou.com/web?query=" + urllib.parse.quote(query), _parse_sogou),
    ]
    for _name, url, parser in plan:
        try:
            page = _fetch(url, timeout)
            results = [r.to_dict() for r in parser(page, limit)]
            if results:
                _CACHE[key] = (time.time(), results)
                return results
        except Exception:  # noqa: BLE001
            continue
    return []


def render(results: list[dict[str, str]], query: str = "") -> str:
    """给 LLM 看的一段；明确标注来源未核实。"""
    if not results:
        return f"没搜到「{query}」的结果（可能被限流或没有网络）。"
    lines = [
        f"网络检索结果（关键词：{query}）。以下内容来自公开网页，未核实，引用时要说「网上说」："
    ]
    for i, item in enumerate(results, 1):
        lines.append(f"{i}. {item['title']}")
        if item.get("snippet"):
            lines.append(f"   {item['snippet']}")
        lines.append(f"   {item['url']}")
    return "\n".join(lines)
