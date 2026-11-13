# -*- coding: utf-8 -*-
"""给四路消息都打上 room 标记。"""
from pathlib import Path

p = Path(r"%PROJECT_HOME%\母女聊天室\server.py")
s = p.read_text(encoding="utf-8")

# inbox
s = s.replace(
'''            "key": f"in-{rid}",
            "role": "baby" if ("宝" in sender or "主" in sender) else "mom",
            "who": sender,''',
'''            "key": f"in-{rid}",
            "role": "baby" if ("宝" in sender or "主" in sender) else "mom",
            "who": sender,
            "room": room_of(row),''')

# outbox
s = s.replace(
'''            "key": f"out-{rid}-{row.get('ts')}",
            "role": "mom" if mom else "fish",
            "who": "妈妈" if mom else "大肥鱼",''',
'''            "key": f"out-{rid}-{row.get('ts')}",
            "role": "mom" if mom else "fish",
            "who": "妈妈" if mom else "大肥鱼",
            "room": room_of(row),''')

# extra
s = s.replace(
'''            "key": f"ex-{row.get('id') or row.get('ts')}",
            "role": str(row.get("role") or "baby"),
            "who": str(row.get("who") or "宝宝"),''',
'''            "key": f"ex-{row.get('id') or row.get('ts')}",
            "role": str(row.get("role") or "baby"),
            "who": str(row.get("who") or "宝宝"),
            "room": room_of(row),''')

# qq -> family
s = s.replace(
'''    for m in qq_recent(80):
        msgs.append({**m, "answered_fish": True, "answered_mom": True})''',
'''    for m in qq_recent(80):
        msgs.append({**m, "room": "family", "answered_fish": True, "answered_mom": True})''')

# 返回里带上房间摘要
s = s.replace(
'''        "tasks": read_tasks(),''',
'''        "rooms": [{"id": k, **v, **room_sum.get(k, {})} for k, v in ROOMS.items()],
        "tasks": read_tasks(),''')

p.write_text(s, encoding="utf-8")
import ast
ast.parse(s)
print("room 字段加好了，语法 OK")
for kw in ('"room": room_of(row)', '"room": "family"', '"rooms": ['):
    print(f"  {kw} -> {s.count(kw)} 处")
