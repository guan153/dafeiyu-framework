# 操作流程（Windows）

## 0. 前置

- Windows 10/11，PowerShell 7 推荐
- Python 3.12
- 一个自己的 AstrBot 宿主（本仓库不含插件）
- 本地端口：8920（网关）、8899（看板）、6185/6199（AstrBot）、3000/3001/5099（SnowLuma，如需）

## 1. 克隆与占位符

```powershell
git clone https://github.com/guan153/dafeiyu-framework.git
cd dafeiyu-framework

# 全局替换以下占位符：
# REPLACE_ME        -> 你的 token/key
# %ASTRBOT_HOME%    -> AstrBot 安装目录
# %PROJECT_HOME%    -> 本项目目录
# %QBOT_HOME%       -> qbot 工作目录
# %USERPROFILE%     -> 用户目录
# 100000000/100000001/100000002 -> 你的 QQ/B站 ID
```

建议用环境变量而不是写死在代码里。

## 2. 准备网关密钥

创建 `gateway_secrets.json`（不要提交）：

```json
{
  "deepseek_api_key": "sk-...",
  "onebot_token": "..."
}
```

启动时用环境变量指定：

```powershell
$env:BEAM_SECRETS_FILE="C:\path\to\gateway_secrets.json"
```

## 3. 启动 BEAM 网关（8920）

```powershell
python gateway/beam_gateway.py
```

验证：

```powershell
curl http://127.0.0.1:8920/health
# {"ok":true,"requests":0,"injected":0,"uptime_s":...}
```

如果失败：看 `gateway.log`；确认密钥文件路径；确认 8920 没被占用。

## 4. 启动 AstrBot（宿主不在本仓库）

- 用你自己的 AstrBot 和插件。
- 把 provider 的 `api_base` 指到 `http://127.0.0.1:8920/v1`。
- 确认 6185/6199 起来、适配器已连接。

## 5. 启动一家三口

```powershell
python family/server.py --port 8899
python family/mom_agent.py
python family/task_runner.py
```

验证：

```powershell
curl http://127.0.0.1:8899/api/data?limit=1
# 看 data\mom_agent_hb.json / task_runner_hb.json 是否新鲜（<90s）
```

## 6. 发一条消息验收

- 发 QQ 私聊（如「在吗」），确认回复。
- 查：
  - `plugin_data\whale_core\emotion_snapshot.json` 是否在写
  - `plugin_data\astrbot_plugin_whale_voice\voice_gate.json` 的 want/threshold/decision/explain 是否同口径
  - 日志里 `[emotion-shadow]` 的 reader.* 是否都是同一个 mood

## 7. 开关

| 目标 | 操作 |
|---|---|
| 只开聊天 | 网关 + AstrBot + QQ，不启动 TTS/ASR/ComfyUI |
| 全关生图/语音 | `switches/开关/全部关-生图语音.bat` |
| 全栈（会开语音） | `switches/开关/QQ机器人-开-全栈(会开语音).bat` |
| 停止机器人 | `switches/开关/QQ机器人-关.bat` |

按你的环境改 bat 里的路径。

## 8. 停止

```powershell
# 停止 family
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match "family\\(server|mom_agent|task_runner)\.py" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

# 停止网关
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match "beam_gateway\.py" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

## 9. 验收清单

- [ ] 8920 `/health` ok
- [ ] AstrBot 6185/6199 UP，适配器已连接
- [ ] 8899 `/api/data` 200
- [ ] 妈妈/runner 心跳新鲜
- [ ] 发一条 QQ 有回复
- [ ] `emotion_snapshot.json` 在写
- [ ] `voice_gate.json` want/decision/explain 一致
- [ ] 8188/8912/8913 未启动（如果只要聊天）

## 10. 回滚

- 情绪读方：改 `emotion_core/shadow_switches.json` 的 `emotion.reader.*` 回 `legacy`
- voice 口径：`voice.want_source` 回 `legacy`
- 代码：用仓库 git 回退；生产环境先备份
- 网关：恢复 `beam_gateway.py` 备份后重启 8920

## 11. 日志位置

- 网关：`gateway.log` / `gateway.out.log` / `gateway.err.log`
- AstrBot：`runtime.log`
- 家庭：`family/data/server.log`、`mom_agent.log`
- 情绪：`plugin_data\whale_core\emotion_snapshot.json`
- 主动性：`plugin_data\astrbot_plugin_whale_voice\voice_gate.json`