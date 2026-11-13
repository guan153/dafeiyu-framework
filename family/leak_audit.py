# -*- coding: utf-8 -*-
"""串台审计：确认四个方向都不串。只读，不发任何消息。"""
import json
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8899"
MOM = Path(r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_whale_mom")
issues, oks = [], []


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=25) as r:
        return json.loads(r.read())


def tail_jsonl(p, n=400):
    out = []
    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]:
        ln = ln.strip()
        if ln:
            try:
                out.append(json.loads(ln))
            except Exception:
                pass
    return out


# ① QQ 消息不该出现在页面任何房间
for room in ("family", "fish", "mom"):
    d = get(f"/api/data?limit=800&room={room}")
    qq = [m for m in d["messages"] if m.get("channel") == "QQ"]
    (oks if not qq else issues).append(
        f"[{room}] QQ 消息 {len(qq)} 条" + ("" if not qq else " ← 还在串！"))

# ② 私聊房间不该出现另一个人的话
for room, nobody in (("fish", "妈妈"), ("mom", "大肥鱼")):
    d = get(f"/api/data?limit=800&room={room}")
    bad = [m for m in d["messages"] if m.get("who") == nobody]
    (oks if not bad else issues).append(
        f"[{room}] 混进{nobody}的话 {len(bad)} 条" + ("" if not bad else " ← 还在串！"))

# ③ 她的回复必须带着正确房间
bad_room = 0
for d in tail_jsonl(MOM / "mom_outbox.jsonl"):
    if d.get("from") != "大肥鱼":
        continue
    r = str(d.get("room") or "")
    if r and r not in ("family", "fish"):
        bad_room += 1
(oks if bad_room == 0 else issues).append(f"她回复带错房间 {bad_room} 条")

# ④ 页面收信箱不该有 QQ 来源（channel=QQ）
bad_ch = 0
for d in tail_jsonl(MOM / "mom_inbox.jsonl"):
    if str(d.get("channel") or "") == "QQ":
        bad_ch += 1
(oks if bad_ch == 0 else issues).append(f"收信箱里 QQ 来源 {bad_ch} 条")

# ⑤ 表情包消息只在本房间
d = get("/api/data?limit=800&room=fish")
meme_in_fish = [m for m in d["messages"] if m.get("kind") == "meme"]
oks.append(f"[fish] 表情包消息 {len(meme_in_fish)} 条（本房间自己的）")

# ⑥ 房间列表
rooms = get("/api/data?limit=1")["rooms"]
oks.append("房间：" + " / ".join(f"{r['icon']}{r['name']}({r['count']})" for r in rooms))

print("=" * 54)
for o in oks:
    print("  ✓", o)
if issues:
    print("\n串台问题：")
    for i in issues:
        print("  ✗", i)
else:
    print("\n四个方向都干净")
print("=" * 54)
