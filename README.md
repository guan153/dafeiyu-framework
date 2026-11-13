# 大肥鱼 · 框架与情绪闭环 v0.1

从私有项目整理出的**预览版**：保留框架、情绪闭环内核、BEAM LLM 网关、一家三口看板/妈妈、开关工具；**不包含 AstrBot 插件本体**，不包含账号、密钥、聊天数据库或任何私有数据。

## 目录

| 目录 | 内容 |
|---|---|
| `framework/` | 启动/运维脚本、框架重构文档与验证脚本 |
| `emotion_core/` | 情绪闭环核心：`emotion_snapshot`、统一开关、情绪/记忆/出场模块 |
| `gateway/` | BEAM LLM 网关（8920），把模型调用收进一条管线 |
| `switches/` | 一键开关脚本（聊天-only、生图/语音、启停） |
| `family/` | 一家三口看板、妈妈回复器、任务看护器 |

## 情绪闭环（本版核心）

1. `whale_affect` 每拍写唯一事实源 `emotion_snapshot.json`（schema v2，原子写）。
2. `state_bus / brain / face / meme / urge / bili / voice` 通过 `shadow_switches.json` 读同一快照。
3. `voice_gate` 使用 broker 的 want，不再被 urge.v 覆盖；`want/threshold/decision/explain` 同口径。
4. BEAM 网关（8920）读取同一快照，避免一轮 prompt 出现两个心情。

## 运行前提

- Windows，Python 3.12
- 需要自己的 AstrBot 宿主（本仓库不含插件本体）
- 需要设置环境变量/占位符：
  - `%ASTRBOT_HOME%`：AstrBot 安装目录
  - `%PROJECT_HOME%`：本项目目录
  - `%QBOT_HOME%`：qbot 工作目录
  - `%USERPROFILE%`：用户目录
  - 代码中的 `100000000/100000001/100000002` 是 QQ/B站 ID 占位
  - `REPLACE_ME` 是 token/密钥占位，运行前必须用环境变量替换
- 不包含 `data/`、日志、数据库、备份、凭据；不要把这些提交回仓库

## 状态

- v0.1 预览：在作者本机验证过；不是开箱即用的生产发行版
- 插件、模型权重、账号数据均不在此仓库
- 公开前请先读 `SECURITY.md`

## License

License TBD（当前保留所有权利，未决定前请勿再分发）。