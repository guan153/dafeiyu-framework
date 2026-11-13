# 常见问题

## 1. laya 分类器起不来

**现象**：`ModuleNotFoundError: laya` 或 `torch`。

**处理**：
1. 确认 `LAYA_HOME` 指向 laya 安装目录。
2. 用 laya 的 Python 环境启动，或把 laya 装进当前环境。
3. `python -c "import laya, torch"` 先过。

## 2. 找不到 `emotion_cls.pt`

**处理**：设置 `EMOTION_CLS_MODEL` 到实际路径；默认占位 `%MODEL_HOME%` 不会自动展开。

## 3. 8901 端口占用

```powershell
Get-NetTCPConnection -LocalPort 8901 -State Listen
```

确认是旧进程后再停；不要盲杀。

## 4. 分类结果总是"平静"

**原因**：输入太短/无情绪词；或模型没加载对。

**处理**：
- 用 `/classify` 直接测一句明显情绪的话。
- 看 `confidence`；低置信时上层会回退词表。
- 确认 `emotion_cls.pt` 的 `label2id` 是 8 类。

## 5. `emotion_snapshot.json` 不生成

**原因**：写者没 `claim()`，或主循环没启动。

**处理**：
1. 看日志有没有 `情绪写者已登记`、`主循环已启动`。
2. 只热重载通常不会重启后台循环；重启上层运行时。
3. 确认 `emotion.snapshot` 不是 `off`。

## 6. 一轮出现两个心情

**处理**：
1. 7 个 `emotion.reader.*` 是否都是 `snapshot`。
2. `voice.want_source` 是否 `broker`。
3. 网关是否已重启并读快照（看代码 mtime 与进程启动时间）。
4. 看 `[emotion-shadow]` 每个 reader 的 mood 是否一致。

## 7. 闸门 want > threshold 却 deny

**原因**：`want` 被别的字段覆盖（旧版常见）。

**处理**：
- `want_source` 必须是 `broker`。
- 检查输出层是否又用 `urge.v` 覆盖 want。
- `explain` 必须和 `decision` 同口径。

## 8. 8920 连接失败

**处理**：
1. `curl http://127.0.0.1:8920/health`。
2. 查 `BEAM_SECRETS_FILE` 指向的密钥文件。
3. 8920 重启后，上层会自动重连；仍失败就重启上层运行时。

## 9. 路径/占位符报错

**原因**：`%LAYA_HOME%`、`%MODEL_HOME%`、`%ASTRBOT_HOME%` 不会自动展开。

**处理**：用环境变量替换，或直接把路径写进配置（不要提交）。

## 10. PowerShell 中文乱码

**处理**：
- 用 `pwsh` 7；或 `.ps1` 存 UTF-8 with BOM。
- 设置 `$env:PYTHONIOENCODING="utf-8"`。

## 11. 端口/进程排查

```powershell
Get-NetTCPConnection -LocalPort 8901,8920 -State Listen
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match "emotion_server|beam_gateway" }
```

先确认是自己的进程，再决定停止。