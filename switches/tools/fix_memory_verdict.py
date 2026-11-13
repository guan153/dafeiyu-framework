# -*- coding: utf-8 -*-
"""一次性：修正账本里「记忆五套重复」这条误判。

2026-11-07 普查结论：
- 跨库重复 0 条；
- soul / iris / whale_memory / brain / memoryflow 是**分层**（情景/语义/档案/摘要/派生索引），
  每层单写者，不是五份互相重复的拷贝。
真正的病另立 P2：「五套记忆各自注入，没有统一召回入口」。

所以把原来那条 P1 标为 verified 并写清纠正理由，而不是留着吓人或悄悄删掉。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("ASTRBOT_ROOT", r"%ASTRBOT_HOME%")

from whale_core.ledger import get_ledger

led = get_ledger()
target = "记忆有五套实现，写入方还没唯一"
hits = [w for w in led.list(status=None, limit=200) if w.title == target]
for w in hits:
    led.set_status(
        w.key,
        "verified",
        note="2026-11-07 普查纠正：不是重复而是分层（情景/语义/档案/摘要/派生），每层单写者；真实问题另立 P2「无统一召回入口」",
    )
    print(f"verified: {w.key} {w.title}")

if not hits:
    print("没有找到该条（可能已改过）：", target)

stats = led.stats()
print("账本：", {k: stats[k] for k in ("total", "open_count", "open_score")})
