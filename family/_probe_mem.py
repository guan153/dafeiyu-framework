# -*- coding: utf-8 -*-
import sqlite3, json, time
from pathlib import Path

p = Path(r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_dafeiyu_soul\soul.sqlite3")
con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=8)
cur = con.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in cur.fetchall()]
print("表:", tables)
for t in tables:
    cur.execute(f"PRAGMA table_info({t})")
    cols = [(r[1], r[2]) for r in cur.fetchall()]
    cur.execute(f"SELECT COUNT(*) FROM {t}")
    n = cur.fetchone()[0]
    print(f"\n[{t}] {n} 行, 列={cols}")
    if n:
        cur.execute(f"SELECT * FROM {t} LIMIT 2")
        for row in cur.fetchall():
            print("  样例:", str(row)[:300])
con.close()

print("\n\n=== l1_snapshot ===")
l1 = Path(r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_iris_chat_memory\l1_snapshot.json")
d = json.loads(l1.read_text(encoding="utf-8"))
if isinstance(d, dict):
    print("顶层键:", list(d.keys())[:20])
    for k, v in list(d.items())[:3]:
        print(f"  {k}: {str(v)[:200]}")
