# 大肥鱼 · 情绪闭环框架 v0.1

> 作者：guan153（GitHub）

<p align="center"><img src="assets/dafeiyu.png" width="260" alt="大肥鱼"></p>

一个把"情绪"从多源各说各话，收成**唯一事实源**，再稳定注入表达层的工程框架。  
包含：情绪内核、BEAM LLM 网关、laya 情感分类器改装、状态快照与回滚开关。  
不包含上层聊天运行时、完整基座模型权重或任何私有数据。

## 一句话

**不是让模型"演"情绪，而是先有一份可审计的情绪事实，再让表达层去读它。**

## 目录

| 目录 | 内容 |
|---|---|
| `emotion_core/` | 情绪内核：`emotion_snapshot`、统一开关、情绪/记忆/出场/闸门模块 |
| `gateway/` | BEAM LLM 网关（8920）：把模型调用收进一条管线，注入同一份情绪底色 |
| `laya_emotion/` | laya 情感分类器改装：8 类分类头、CPU 常驻、8901 HTTP 服务 |
| `framework/` | 框架重构代码与测试（beams_core 等） |
| `docs/` | 架构、情绪闭环流程、laya 改装、设计亮点、操作与排错 |
| `assets/` | 形象图 |

## 情绪闭环是什么

```
情感分类器（laya）──┐
身体/内感受/事件 ────┼──> whale_affect ──写──> emotion_snapshot.json
                    │                              │
                    └──> moodfusion（底色/温度/表达）│
                                                   ├── state_bus（状态块）
                                                   ├── brain（<psyche>）
                                                   ├── face（表情/语气）
                                                   ├── meme（表情出口）
                                                   ├── urge（欲望）
                                                   └── expression gate（开口闸门）
                                                   │
                                                   ▼
                                        gateway（8920）读同一快照
                                                   │
                                                   ▼
                                       上层运行时拿到一份情绪
```

核心规则：

1. **单写者**：只有 `whale_affect` 能写 `emotion_snapshot.json`。
2. **读方统一**：其余模块通过 `shadow_switches.json` 读同一快照。
3. **网关同源**：8920 也读快照，避免 prompt 里出现两个心情。
4. **可回滚**：每个读方一个开关，改 JSON 即可回退。
5. **可验收**：影子日志、`voice_gate.json`、快照时间戳都能直接查。

## 相比常见做法的优势

| 常见做法 | 问题 | 我们的做法 |
|---|---|---|
| 每个模块自己算情绪 | 一轮 prompt 出现"黏人/安心/平静"多个心情 | 单写者 + 统一快照 |
| 只靠 prompt 让模型演 | 不可审计、不可回滚 | 状态机 + 快照 + 闸门 |
| 情绪只有一档标签 | 表达扁平 | 底色/温度/表达分层 |
| 云端黑箱 | 隐私与成本不可控 | 本地网关，127.0.0.1 |
| 规则硬编码 | 改一处崩一片 | 开关集中到 JSON，热读 |
| 只看"像不像" | 无法定位 | 影子日志 + 证据 gate |

更完整的对比见 [docs/WHY-OURS.md](docs/WHY-OURS.md)。

## 快速开始

```powershell
git clone https://github.com/guan153/dafeiyu-framework.git
cd dafeiyu-framework

# 1) 环境变量（不要写死密钥）
$env:LAYA_HOME="C:\path\to\laya"
$env:EMOTION_CLS_MODEL="C:\path\to\emotion_cls.pt"
$env:BEAM_SECRETS_FILE="C:\path\to\gateway_secrets.json"

# 2) 启动 laya 情感分类器（8901）
python laya_emotion/emotion_server.py --port 8901 --device cpu

# 3) 启动 BEAM 网关（8920）
python gateway/beam_gateway.py

# 4) 验证
curl http://127.0.0.1:8901/health
curl http://127.0.0.1:8920/health
curl -X POST http://127.0.0.1:8901/classify -H "Content-Type: application/json" -d '{"text":"今天有点累"}'
```

各组件没有内置守护；生产使用请自己加进程管理。

## laya 情感分类器改装（重点）

- 基座：`convaiinnovations/laya` multilingual encoder
- 分类头：768 → 8 类线性层（兴奋 / 吃醋 / 平静 / 开心 / 撒娇 / 生气 / 疲惫 / 难过）
- 默认 CPU，省显存；`--device cuda` 可切
- HTTP：`POST /classify`，返回 `label / confidence / probs`
- 改装点：问候词纠正（"在吗"别判成撒娇）、低置信回退词表、CPU 常驻、8 类映射到 `goal_congruence`

详见 [docs/LAYA-EMOTION.md](docs/LAYA-EMOTION.md)。

## 文档

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — 组件、端口、数据流
- [docs/EMOTION-LOOP.md](docs/EMOTION-LOOP.md) — **情绪闭环完整流程**（启动/每拍/回复/失败/回滚/验收）
- [docs/LAYA-EMOTION.md](docs/LAYA-EMOTION.md) — laya 分类器改装细节
- [docs/WHY-OURS.md](docs/WHY-OURS.md) — 设计亮点与对比
- [docs/OPERATIONS.md](docs/OPERATIONS.md) — 启动/停止/验证
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) — 常见问题
- [SECURITY.md](SECURITY.md) — 安全边界

## License

License TBD（当前保留所有权利，未决定前请勿再分发）。