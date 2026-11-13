# -*- coding: utf-8 -*-
import sqlite3, json, time
db = r"%QBOT_HOME%\qbot\SnowLuma\data\100000001\messages.db"
con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
cur = con.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
print("表:", [r[0] for r in cur.fetchall()])
cur.execute("PRAGMA table_info(messages)")
print("列:", [(r[1], r[2]) for r in cur.fetchall()])
cur.execute("SELECT COUNT(*) FROM messages")
print("消息数:", cur.fetchone()[0])
cur.execute("SELECT timestamp, is_group, data FROM messages ORDER BY timestamp DESC LIMIT 4")
for ts, g, d in cur.fetchall():
    try:
        j = json.loads(d)
    except Exception:
        j = {}
    segs = []
    for s in (j.get("message") or []):
        if isinstance(s, dict) and s.get("type") == "text":
            segs.append(s.get("data", {}).get("text", ""))
    print("---", time.strftime("%m-%d %H:%M", time.localtime(ts)),
          "群" if g else "私", j.get("post_type"), "user=", j.get("user_id"),
          "|", " / ".join(segs)[:80])
con.close()
