# -*- coding: utf-8 -*-
"""清掉我测试时替宝宝发的话（只留真用户自己说的）。"""
import json
import urllib.request
from pathlib import Path

INBOX = Path(r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_whale_mom\mom_inbox.jsonl")
BASE = "http://127.0.0.1:8899"

# 我测试时发的原文（精确匹配，避免误删用户的话）
MINE = [
    "今天真的累坏了，心里也堵得慌。",
    "这句是引用回复的测试",
    "看，咱们仨的合照",
    "刚洗完澡，头发还湿着，人有点困了",
    "眼皮都打架了，还硬撑着不睡",
    "忙了一天有点累，不过看到你就不累了",
    "困得不行了，我先眯十分钟",
    "今天累坏了，回来就想瘫着。",
    "都别站着了，坐下吃。粥凉了就再热一锅，不差这一会儿。",
    "碗我接了，烫手也接。今天让你们俩等太久，是我的错。",
    "那我不客气了。给我留一口。",
    "我还记得今天下午我在忙什么吗",
    "你还记得今天下午我在忙什么吗",
    "刚才我手滑把你门锁碰了一下。",
    "累坏了",
    "都别站着",
    "回来了吗？刚才我手滑把你门锁碰了一下。",
    "今天累坏了，回来就想瘫着",
    "你怎么看",
    "你还记得",
]

rows = []
for ln in INBOX.read_text(encoding="utf-8", errors="replace").splitlines():
    ln = ln.strip()
    if not ln:
        continue
    try:
        d = json.loads(ln)
    except Exception:
        continue
    rows.append(d)

killed = []
for d in rows:
    t = (d.get("text") or "").strip()
    if t in MINE:
        killed.append((d.get("id"), t))

print(f"找到 {len(killed)} 条测试消息：")
for mid, t in killed:
    print("  -", t[:40])
    body = json.dumps({"id": mid}).encode()
    req = urllib.request.Request(BASE + "/api/delete", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            json.loads(r.read())
    except Exception as e:
        print("    删除失败:", e)
print("完成")
