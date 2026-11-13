# -*- coding: utf-8 -*-
"""让她看见图：本地图片 → 一句话描述（用百炼 qwen3-vl-plus）。

插件和页面都能调。失败返回空串，不阻断对话。
"""
from __future__ import annotations

import base64
import json
import urllib.request
from pathlib import Path

KEY = ("sk-ws-H.PDPMERL.bQxV.MEUCIAo5GlPVYUaIcpjqE55e6zSfRbYJh5kTOthR-"
       "G29merlAiEAsQqX5EvL2Z6Yxma6ZmHTqS4AHZaDdTZxAK3_QZ6deDU")
ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
MODEL = "qwen3-vl-plus"

ASSETS = Path(r"%PROJECT_HOME%\母女聊天室\assets")
MEMES = Path(r"%ASTRBOT_HOME%\data\plugins\astrbot_plugin_dafeiyu_meme\memes")
OLD_ROOT = Path(r"%USERPROFILE%\.dsh\meme-packs")

MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".webp": "image/webp", ".gif": "image/gif"}


def resolve(media: str) -> Path | None:
    """把页面里的 /assets/xxx.png、/memes/cat/f.png 映射到真实文件。"""
    if not media:
        return None
    m = media.split("?")[0]
    if m.startswith("/assets/"):
        p = ASSETS / m[len("/assets/"):]
        return p if p.is_file() else None
    if m.startswith("/memes/"):
        rel = m[len("/memes/"):]
        p = MEMES / rel
        if p.is_file():
            return p
        # 旧路径兜底
        name = rel.split("/")[-1]
        for root in (OLD_ROOT, Path(r"%ASTRBOT_HOME%\data\plugin_data\meme_manager\packs")):
            if root.exists():
                hit = next(root.rglob(name), None)
                if hit and hit.is_file():
                    return hit
        return None
    p = Path(media)
    return p if p.is_file() else None


def describe(media: str, hint: str = "用一两句话说说这张图里有什么，像跟人聊天那样随口说，别写分析") -> str:
    p = resolve(media)
    if not p:
        return ""
    data = p.read_bytes()
    if len(data) > 4 * 1024 * 1024:
        return ""
    b64 = base64.b64encode(data).decode()
    mime = MIME.get(p.suffix.lower(), "image/png")
    body = {
        "model": MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": hint},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ],
        }],
        "max_tokens": 200,
    }
    req = urllib.request.Request(
        ENDPOINT, data=json.dumps(body).encode("utf-8"), method="POST")
    req.add_header("Authorization", f"Bearer {KEY}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        return (d["choices"][0]["message"]["content"] or "").strip()
    except Exception as e:  # noqa: BLE001
        print("看图失败:", e)
        return ""


if __name__ == "__main__":
    import sys
    m = sys.argv[1] if len(sys.argv) > 1 else "/assets/family_v2.png"
    print("图片:", m)
    print("看到:", describe(m))
