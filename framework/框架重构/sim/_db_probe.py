import sqlite3, json, os
db = r"%ASTRBOT_HOME%\data\data_v4.db"
c = sqlite3.connect(db)
tables = [r[0] for r in c.execute("select name from sqlite_master where type='table'")]
print("tables:", tables)
try:
    rows = c.execute("select count(*) from platform_message_history").fetchone()[0]
    print("messages:", rows)
    senders = c.execute("select sender_name, count(*) from platform_message_history group by sender_name").fetchall()
    print("senders:", senders)
    sessions = c.execute("select count(distinct user_id) from platform_message_history").fetchone()[0]
    print("sessions:", sessions)
    first = c.execute("select id, sender_name, substr(content,1,120), created_at from platform_message_history order by id asc limit 3").fetchall()
    for r in first:
        print("first:", r)
    last = c.execute("select id, sender_name, substr(content,1,120), created_at from platform_message_history order by id desc limit 3").fetchall()
    for r in last:
        print("last:", r)
except Exception as e:
    print("err:", e)