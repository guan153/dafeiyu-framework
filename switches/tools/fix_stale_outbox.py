# -*- coding: utf-8 -*-
"""一次性运维脚本：把「当时无会话 UMO」的历史失败行改成 expired。

背景：2026-11-07 16:29 自省插件首次告警时，内核还没拿到会话 UMO，
消息注定发不出去，队列重试 3 次后标 failed。配置已修（owner_umo），
这些行本来就过了 TTL，改成 expired 才是事实，不是掩盖。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB = Path(r"%ASTRBOT_HOME%\data\plugin_data\whale_core\outbound.sqlite3")
NOTE = "当时无会话 UMO；已配置 owner_umo，不再重试"

con = sqlite3.connect(DB)
rows = con.execute("SELECT id, status FROM outbox WHERE status='failed'").fetchall()
for ticket, status in rows:
    con.execute("UPDATE outbox SET status='expired', last_error=? WHERE id=?", (NOTE, ticket))
    print(f"expired {ticket}")
con.commit()
con.close()
print(f"共处理 {len(rows)} 行")
