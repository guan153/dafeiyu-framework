# -*- coding: utf-8 -*-
import sqlite3, json
from pathlib import Path

# 1) .dsh 包的 index.db 里有什么
for pack in ["dafeiyu-emoji", "deepseek-chan", "paopao-whale-girl"]:
    p = Path(r"%USERPROFILE%\.dsh\meme-packs") / pack / "index.db"
    if not p.exists():
        print(f"[{pack}] 没有 index.db")
        continue
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    cur = con.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in cur.fetchall()]
    print(f"\n[{pack}] 表: {tables}")
    for t in tables:
        cur.execute(f"PRAGMA table_info({t})")
        print("  列:", [(r[1], r[2]) for r in cur.fetchall()])
        cur.execute(f"SELECT COUNT(*) FROM {t}")
        print("  行数:", cur.fetchone()[0])
        cur.execute(f"SELECT * FROM {t} LIMIT 2")
        for row in cur.fetchall():
            print("  样例:", str(row)[:260])
    con.close()

# 2) 插件 embeddings.json 结构
emb = Path(r"%ASTRBOT_HOME%\data\plugins\astrbot_plugin_dafeiyu_meme\embeddings.json")
d = json.loads(emb.read_text(encoding="utf-8"))
print("\n\nembeddings 顶层:", list(d.keys())[:10])
for k, v in list(d.items())[:2]:
    print(f"  {k}: {type(v).__name__} {str(v)[:120]}")
