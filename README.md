# 大肥鱼 · 框架与情绪闭环 v0.1

> 从私有项目整理出的**预览版**：保留框架、情绪闭环内核、BEAM LLM 网关、一家三口看板/妈妈、开关工具。
> **不包含 AstrBot 插件本体**，不包含账号、密钥、聊天数据库或私有数据。

这是一个"能看、能学、能改"的工程切片，不是开箱即用的聊天机器人发行版。

## 这是什么

- 一套把"情绪"从多个插件各说各话，收成**唯一事实源**的架构
- 一个把 LLM 调用收进本地网关（8920）的管线
- 一套 Windows 下可手动启停的运维脚本与开关
- 一个本地家庭看板（8899）和妈妈回复器

## 这不是什么

- 不是完整 QQ 机器人：AstrBot 插件不在本仓库，需要你自己的宿主
- 不是一键安装包：路径、环境变量、密钥占位都要自己填
- 不是生产级：没有鉴权、没有 CI、没有完整测试矩阵，部分服务只适合本机
- 不是"情绪 AI 模型"：这里没有训练好的模型权重，只有状态机、快照、注入和调度

## 目录

| 目录 | 内容 |
|---|---|
| `framework/` | 启动/运维脚本、框架重构文档与测试 |
| `emotion_core/` | 情绪闭环核心：`emotion_snapshot`、统一开关、情绪/记忆/出场模块 |
| `gateway/` | BEAM LLM 网关（8920） |
| `switches/` | 一键开关（聊天-only、生图/语音、启停） |
| `family/` | 一家三口看板、妈妈回复器、任务看护器 |
| `docs/` | 架构、优缺点、操作流程、排错 |

## 情绪闭环（本版核心）

1. `whale_affect` 每拍写唯一事实源 `emotion_snapshot.json`（schema v2，原子写）。
2. `state_bus / brain / face / meme / urge / bili / voice` 通过 `shadow_switches.json` 读同一快照。
3. `voice_gate` 使用 broker 的 `want`，不再被 `urge.v` 覆盖；`want/threshold/decision/explain` 同口径。
4. BEAM 网关（8920）读取同一快照，避免一轮 prompt 出现两个心情。

## 优点

- **单一事实源**：情绪快照 + 开关，读完一处就知道她此刻什么心情。
- **可回滚**：每个读方一个开关；回滚只改 `shadow_switches.json`，多数情况不用重启。
- **可观测**：`[emotion-shadow]` 日志、`voice_gate.json`、`emotion_snapshot.json` 都能直接查。
- **本地优先**：LLM 网关、看板、家庭服务都能跑在 `127.0.0.1`，不依赖云。
- **模块清晰**：网关 / 情绪内核 / 家庭 / 开关分开，方便替换。
- **聊天-only 开关**：只开 QQ/SnowLuma/网关/AstrBot，不拉语音和生图。

## 缺点与边界（先说清楚）

- **不能直接跑**：AstrBot 插件、模型、账号、配置都不在仓库里。
- **Windows 绑定**：大量 `.ps1`、盘符路径、PowerShell 5.1 编码坑；跨平台要自己改。
- **没有鉴权**：`family/` 看板、部分本机端点默认无认证，只能本机用，禁止公网暴露。
- **单点依赖**：8920 网关挂了，聊天和一家三口一起哑；仓库没有内置守护。
- **测试不足**：有零散测试，没有 CI/覆盖率；改动前请自己跑 `docs/OPERATIONS.md` 的验收清单。
- **路径占位**：`%ASTRBOT_HOME%`、`%PROJECT_HOME%`、`%QBOT_HOME%`、`REPLACE_ME` 都要替换。
- **部分能力未收口**：`proactive_chat` 还没完全切到快照；`will` 的跨天锁、`state.json` 睡眠快照仍是已知问题。
- **许可证未定**：当前是"保留所有权利，License TBD"，公开前请作者确认。
- **中文文档**：暂无英文版。

## 快速开始（最短路径）

```powershell
git clone https://github.com/guan153/dafeiyu-framework.git
cd dafeiyu-framework

# 1) 替换占位符（至少改这些）
#    REPLACE_ME -> 你的 token/key
#    %ASTRBOT_HOME% -> AstrBot 安装目录
#    %PROJECT_HOME% -> 本项目目录
#    %QBOT_HOME% -> qbot 工作目录
#    100000000/100000001/100000002 -> 你的 QQ/B站 ID

# 2) 启动 BEAM 网关（先设好密钥文件）
$env:BEAM_SECRETS_FILE="C:\path\to\gateway_secrets.json"
python gateway/beam_gateway.py

# 3) 启动 AstrBot（宿主不在本仓库，需要你自己的插件）
#    把 provider 的 api_base 指到 http://127.0.0.1:8920/v1

# 4) 启动一家三口
python family/server.py --port 8899
python family/mom_agent.py
python family/task_runner.py

# 5) 验证
curl http://127.0.0.1:8920/health
curl http://127.0.0.1:8899/api/data?limit=1
```

更细的启动顺序、端口、停止方式见 [docs/OPERATIONS.md](docs/OPERATIONS.md)。

## 操作流程（从零到能聊）

1. 准备 Windows + Python 3.12。
2. 克隆仓库，替换所有占位符。
3. 准备 AstrBot 宿主和插件（本仓库不含）。
4. 启动 `gateway/beam_gateway.py`，确认 `8920/health` 返回 `ok=true`。
5. 启动 AstrBot，确认它的 provider `api_base` 指向 `http://127.0.0.1:8920/v1`。
6. 启动 `family/server.py`、`mom_agent.py`、`task_runner.py`，确认 8899 和心跳。
7. 发一条私聊，确认回复；查 `emotion_snapshot.json` 和 `voice_gate.json`。
8. 要语音/生图时再单独开对应服务；不需要就保持关闭。

完整步骤与验收命令见 [docs/OPERATIONS.md](docs/OPERATIONS.md)。

## 注意事项

- **不要把 8899 / 8920 / 8765 暴露到公网**：看板和部分端点没有鉴权。
- **不要提交密钥**：`REPLACE_ME` 必须用环境变量替换，不要写回仓库。
- **自动发布要人工确认**：B站评论/动态、QZone 说说等路径默认不应无人值守开启。
- **GPU 服务互斥**：TTS（8912）和 ComfyUI（8188）在 8GB 卡上不要同时开。
- **路径不要硬编码**：优先用环境变量；改路径前先备份。
- **改插件代码后要重启/重载**：热重载可能不会重启后台循环（本项目踩过）。
- **先看日志再重启**：`runtime.log`、`gateway.log`、`voice_gate.json` 是主要证据。

## 常见问题

| 现象 | 优先排查 |
|---|---|
| AstrBot 回复空/连接失败 | 8920 是否 UP；`/health` 是否 `ok`；provider `api_base` 是否指向 8920 |
| 一轮出现两个心情 | `emotion.reader.*` 是否都切到 `snapshot`；`mood_composed` 是否被 state_bus 覆盖 |
| `emotion_snapshot.json` 不生成 | `whale_affect` 主循环是否启动；热重载后需重启 AstrBot |
| 看板 8899 打不开 | `server.py` 是否运行；端口占用；防火墙 |
| 妈妈不回 | 8920 是否 UP；`mom_agent_hb.json` 是否新鲜；任务看护器是否在跑 |
| `ModuleNotFoundError` | 是否在正确目录启动；`PYTHONPATH`/venv；占位符是否替换 |
| `voice_gate` 一直 deny | 看 `want/threshold`；`want_source` 是否为 `broker` |
| 语音/生图自己起来 | 检查 `toggles.json`、8921 调度器、`start_all.ps1` 是否被调用 |
| PowerShell 中文乱码 | 用 `pwsh` 7 或 UTF-8 BOM 保存 `.ps1` |

更多排错见 [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)。

## 文档

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — 架构与数据流
- [docs/PROS-CONS.md](docs/PROS-CONS.md) — 优缺点与适用场景
- [docs/OPERATIONS.md](docs/OPERATIONS.md) — 启动/停止/验收/开关
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) — 常见问题与修复
- [SECURITY.md](SECURITY.md) — 安全边界

## License

License TBD（当前保留所有权利，未决定前请勿再分发）。