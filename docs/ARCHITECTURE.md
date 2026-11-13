# 架构与数据流

## 组件

| 组件 | 端口 | 职责 |
|---|---|---|
| `laya_emotion/emotion_server.py` | 8901 | laya encoder + 8 类分类头，HTTP 情感分类 |
| `gateway/beam_gateway.py` | 8920 | LLM 管线：读身体/情绪/快照，注入底色，转发上游 |
| `emotion_core/` | — | 情绪事实源、开关、moodfusion、闸门、出场模块 |
| `framework/框架重构/` | — | beams_core 等框架代码与测试 |

## 数据流

```
文本/事件 ──> laya 分类器（8901）
                   │ label/confidence/probs
                   ▼
            whale_affect（写入方）
                   │ 每拍
                   ▼
        emotion_snapshot.json（唯一事实源）
          │        │        │        │
          ▼        ▼        ▼        ▼
      state_bus  brain    face    expression gate
          │        │        │        │
          └────────┴───┬────┴────────┘
                       ▼
              上层运行时 prompt
                       │
                       ▼
              gateway（8920）再读同一快照
                       │
                       ▼
                  模型输出
```

## 关键文件

- `emotion_core/emotion.py`：`EmotionHub`，单写者 `claim/update`。
- `emotion_core/switches.py`：统一开关（5s 热读）。
- `emotion_core/moodfusion.py`：底色/温度/表达合成。
- `emotion_core/voicegate.py`：开口闸门（want/threshold/decision）。
- `gateway/beam_gateway.py`：8920 网关。
- `laya_emotion/emotion_server.py`：8901 分类服务。
- `laya_emotion/emotion_cls.pt`：8 类分类头（26KB，不含 encoder 权重）。

## 设计原则

1. **单一事实源**：情绪只允许一个写者。
2. **读方降级**：快照过期/缺失时回退 legacy，不阻塞。
3. **网关同源**：避免 prompt 内部两个心情。
4. **开关热读**：回滚只改 JSON，不重启。
5. **证据优先**：影子日志、gate JSON、快照时间戳。