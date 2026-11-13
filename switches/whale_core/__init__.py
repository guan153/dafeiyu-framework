# -*- coding: utf-8 -*-
"""whale_core —— 鲸鱼少女 v3 插件内核。

设计原则（写给未来的自己和接手的人）：

1. **纯库**：不 import astrbot，可脱离机器人单测。
2. **单写者**：每个事实（情绪、记忆、出站消息）只有一个写入口，
   其他插件只能读或提交申请，从根上消灭「四份心情、五个记忆库、七个主动源」。
3. **可观测**：一切失败进弱点账本（ledger），一切健康有探针（health），
   评分可解释——扣分必须说明扣在哪。
4. **可降级**：任何子系统缺失只降级不崩溃，机器人先活着。

模块地图：
    paths       —— 目录约定（唯一事实源）
    store       —— SQLite 单写者连接
    bus         —— 进程内事件总线
    ledger      —— 弱点账本（本内核的核心资产）
    outbound    —— 统一出站队列（申请→仲裁→发放→回执）
    emotion     —— 情绪单一事实源
    face        —— 情绪外显层（心情 → 表情/尾巴/语气/表情包权重）
    capabilities—— 能力注册表
    health      —— 健康探针
    logscan     —— 日志扫描归因
    introspect  —— 自省引擎（巡检→评分→提案）
    config      —— 统一配置
"""

from __future__ import annotations

__version__ = "3.0.0"
__all__ = ["__version__"]

# 门面延迟导入：只 import whale_core 时不拉 sqlite/asyncio 之外的东西
def __getattr__(name: str):
    if name in ("Store", "get_store"):
        from . import store

        return getattr(store, name)
    if name in ("Bus", "get_bus"):
        from . import bus

        return getattr(bus, name)
    if name in ("Ledger", "get_ledger"):
        from . import ledger

        return getattr(ledger, name)
    if name in ("EmotionHub", "get_emotion_hub"):
        from . import emotion

        return getattr(emotion, name)
    if name in ("FaceCard", "FaceSpec", "card_for", "pick_expression", "should_send_face", "MOOD_FACE"):
        from . import face

        return getattr(face, name)
    if name in ("OutboundQueue", "get_outbound"):
        from . import outbound

        return getattr(outbound, name)
    if name == "VERSION_INFO":
        from . import paths

        return paths.version_info()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
