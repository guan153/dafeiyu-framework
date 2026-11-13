# -*- coding: utf-8 -*-
"""看板体检：接口、资源、数据、进程。有问题就列出来。"""
from __future__ import annotations
import json
import time
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

BASE = "http://127.0.0.1:8899"
HERE = Path(r"%PROJECT_HOME%\母女聊天室")
issues: list[str] = []
oks: list[str] = []


def get(path: str, timeout: int = 25):
    # 中文文件名要 percent-encode，浏览器会自动做，脚本得自己来
    safe = urllib.parse.quote(path, safe="/?=&%:")
    try:
        with urllib.request.urlopen(BASE + safe, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:  # noqa: BLE001
        return 0, str(e).encode()


# 1) 接口
for p in ("/api/data", "/api/memes", "/api/memory", "/api/tasks", "/"):
    code, body = get(p)
    if code == 200:
        oks.append(f"{p} 200 ({len(body)//1024} KB)")
    else:
        issues.append(f"{p} -> {code} {body[:120]!r}")

# 2) 页面里引用的资源
page = HERE / "index.html"
html = page.read_text(encoding="utf-8")
for asset in ("/assets/mom.png", "/assets/fish_smile.png", "/assets/fish_cheer.png"):
    code, body = get(asset, 10)
    (oks if code == 200 else issues).append(f"{asset} {code}")

# 3) 数据
code, body = get("/api/data")
data = json.loads(body) if code == 200 else {}
msgs = data.get("messages") or []
if msgs:
    oks.append(f"消息 {len(msgs)} 条")
    bad_media = [m for m in msgs if m.get("kind") == "meme" and m.get("media")]
    for m in bad_media:
        c, _ = get(m["media"], 8)
        if c != 200:
            issues.append(f"表情消息图片 404: {m['media']}")
    if not bad_media:
        oks.append("没有失效的表情消息")

# 4) 表情包全量可访问（抽样 + 全量 HEAD）
code, body = get("/api/memes", 40)
memes = (json.loads(body).get("items") or []) if code == 200 else []
if memes:
    oks.append(f"表情包 {len(memes)} 张")
    # 全量检查（本地很快）
    missing = []
    for it in memes:
        p = Path(r"%ASTRBOT_HOME%\data\plugins\astrbot_plugin_dafeiyu_meme") / "memes" / it["url"].replace("/memes/", "")
        if not p.exists():
            missing.append(it["url"])
    if missing:
        issues.append(f"表情包文件缺失 {len(missing)} 个，例：{missing[:3]}")
    else:
        oks.append(f"{len(memes)} 张图文件全在")
    caps = sum(1 for it in memes if it.get("caption"))
    oks.append(f"有描述的 {caps}/{len(memes)}")

# 5) 进程
hb = HERE / "data" / "mom_agent_hb.json"
if hb.exists():
    d = json.loads(hb.read_text(encoding="utf-8"))
    age = time.time() - float(d.get("ts") or 0)
    (oks if age < 60 else issues).append(f"妈妈回复器心跳 {age:.1f}s")
else:
    issues.append("妈妈回复器心跳文件不存在")

# 6) 任务文件
tk = HERE / "data" / "tasks.json"
try:
    tasks = json.loads(tk.read_text(encoding="utf-8"))
    oks.append(f"任务 {len(tasks)} 件")
except Exception as e:  # noqa: BLE001
    issues.append(f"tasks.json 读不了: {e}")

print("=" * 50)
for o in oks:
    print("  ✓", o)
if issues:
    print("\n问题：")
    for i in issues:
        print("  ✗", i)
else:
    print("\n没有发现问题")
print("=" * 50)
