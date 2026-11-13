# 操作流程

## 0. 前置

- Windows（脚本按 Windows 写；其他平台需自行改路径）
- Python 3.12
- laya 运行环境（`LAYA_HOME`）
- 8920 网关的上游 API Key

## 1. 环境变量

```powershell
$env:LAYA_HOME="C:\path\to\laya"
$env:EMOTION_CLS_MODEL="C:\path\to\emotion_cls.pt"
$env:BEAM_SECRETS_FILE="C:\path\to\gateway_secrets.json"
```

`gateway_secrets.json` 示例（不要提交）：

```json
{"deepseek_api_key": "sk-..."}
```

## 2. 启动 laya 分类器（8901）

```powershell
python laya_emotion/emotion_server.py --port 8901 --device cpu
```

验证：

```powershell
curl http://127.0.0.1:8901/health
curl -X POST http://127.0.0.1:8901/classify -H "Content-Type: application/json" -d "{\"text\":\"今天有点累\"}"
```

## 3. 启动 BEAM 网关（8920）

```powershell
python gateway/beam_gateway.py
```

验证：

```powershell
curl http://127.0.0.1:8920/health
```

## 4. 接入上层运行时

- 上层运行时加载 `emotion_core`，并让 `whale_affect` `claim()` 成为写者。
- 8920 的 provider `api_base` 指到 `http://127.0.0.1:8920/v1`。
- 各读方按 `shadow_switches.json` 选择 `snapshot`。

## 5. 验收

- [ ] 8901 `/health` 返回 8 类
- [ ] 8920 `/health` `ok=true`
- [ ] `emotion_snapshot.json` 60s 内更新
- [ ] 日志有 `情绪写者已登记`、`主循环已启动`
- [ ] `[emotion-shadow]` 各 reader mood 一致
- [ ] `voice_gate.json` want/threshold/decision/explain 一致
- [ ] 8920 `/health` 的 `requests == injected`

## 6. 停止

```powershell
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match "emotion_server\.py|beam_gateway\.py" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

## 7. 回滚

- 读方：`shadow_switches.json` 对应键改回 `legacy`（5s 热读）。
- 闸门：`voice.want_source` 改回 `legacy`。
- 代码：git 回退；生产先备份。
- 网关：恢复 `beam_gateway.py` 备份后重启 8920。

## 8. 日志

- 8901：`emotion_server` 控制台/日志
- 8920：`gateway.log` / `gateway.out.log` / `gateway.err.log`
- 快照：`plugin_data/whale_core/emotion_snapshot.json`（按你的运行时路径）
- 闸门：`plugin_data/astrbot_plugin_whale_voice/voice_gate.json`（按你的运行时路径）