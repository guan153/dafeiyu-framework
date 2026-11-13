# 架构与数据流

## 组件

| 组件 | 端口 | 角色 |
|---|---|---|
| BEAM 网关 `gateway/beam_gateway.py` | 8920 | 把 LLM 请求收进一条管线，注入身体/情绪/听觉底色 |
| AstrBot 宿主 | 6185 / 6199 | QQ 接入、插件、人格；**插件不在本仓库** |
| 情绪内核 `emotion_core/` | — | `emotion_snapshot` 单写者、情绪/记忆/出场模块、统一开关 |
| 家庭看板 `family/server.py` | 8899 | 三人消息、表情包、记忆、任务 |
| 妈妈回复器 `family/mom_agent.py` | — | 读看板/记忆/日记，调 8920 回复 |
| 任务看护器 `family/task_runner.py` | — | 消费 tasks.json，调 DSH/agent |
| 开关 `switches/` | — | 聊天-only、生图/语音、启停 |

## 情绪闭环数据流

```
whale_affect (每 60s)
        │ 写
        ▼
emotion_snapshot.json  ── 唯一事实源
        │
        ├── state_bus   → 状态块 / <mood_tag>
        ├── brain       → <psyche>
        ├── face        → 表情/尾巴/语气
        ├── meme        → 表情包出口
        ├── urge        → 欲望
        ├── bili        → B站出口
        └── voice       → 主动性/voice_gate
                          │
                          ▼
                voice_gate.json（want/threshold/decision/explain）
                          │
                          ▼
                 主动消息 / 回复注入
```

BEAM 网关也读 `emotion_snapshot.json`，把它写成 system prompt 里的底色；避免网关和插件各注入一个心情。

## 关键文件

- `emotion_core/emotion.py`：`EmotionHub`，单写者 claim/update。
- `emotion_core/switches.py`：统一开关，5s 热读。
- `emotion_core/moodfusion.py`：底色/温度/表达的只读合成。
- `emotion_core/voicegate.py`：开口闸门。
- `gateway/beam_gateway.py`：8920，读快照、转发上游、写回。
- `family/server.py`：8899 看板，三路消息合并。
- `family/mom_agent.py`：妈妈回复器。
- `family/task_runner.py`：任务执行器。

## 设计取舍

- 单一事实源优先于"每个插件自己算"。
- 快照失败/过期时读方降级到 legacy，不阻塞聊天。
- 开关热读，回滚不需要重启；但**加代码**后需要重启或重载，且热重载不保证后台循环重启。
- 网关和插件都读同一快照，是"一轮一个心情"的关键。