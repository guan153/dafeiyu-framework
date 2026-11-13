# 常见问题与修复

## 1. AstrBot 回复空 / 连接失败

**现象**：消息进来但不回，日志 `APIConnectionError` / `Connection error`。

**原因**：8920 没起，或 `api_base` 不是 8920。

**修复**：
1. `curl http://127.0.0.1:8920/health` 必须 `ok=true`。
2. 查 AstrBot provider 配置：`api_base = http://127.0.0.1:8920/v1`。
3. 8920 重启后 AstrBot 会自动重连；若仍失败，重启 AstrBot。

## 2. 一轮出现两个心情（黏人 + 安心/平静）

**原因**：某个读方还在读旧文件，或 `mood_composed` 覆盖了主情绪。

**修复**：
1. 查 `shadow_switches.json`：7 个 `emotion.reader.*` 必须都是 `snapshot`。
2. 查 `voice.want_source` 是否为 `broker`。
3. 查 `state_bus` 是否仍用 `mood_composed` 覆盖主情绪（本仓库已修；旧版需改）。
4. 看日志 `[emotion-shadow]` 的 reader.* 是否同一个 mood。

## 3. `emotion_snapshot.json` 不生成

**原因**：`whale_affect` 主循环没启动，常见于只热重载插件。

**修复**：
- 重启 AstrBot（不是只 reload 插件）。
- 确认日志有「[情绪内核] 主循环已启动」。
- 确认 `emotion.snapshot` 不是 `off`。

## 4. 热重载后插件空转

**现象**：插件加载了，但后台 60s/5s 循环不跑，状态文件不更新。

**原因**：AstrBot 热重载不触发 `on_astrbot_loaded`。

**修复**：
- 重启 AstrBot；或给插件加 `on_plugin_loaded` 启动循环（见源码注释）。
- 不要只依赖热重载。

## 5. 看板 8899 打不开 / 妈妈不回

**排查顺序**：
1. `server.py` 是否在跑，8899 是否被占用。
2. `mom_agent_hb.json` 是否 <90s。
3. 8920 是否 UP（妈妈也用它）。
4. `mom_agent.log` 最新错误。

## 6. 妈妈读不到日记

**原因**：`mom_agent.py` 的 `DIARY_DIR` 路径不对，或日记文件不存在。

**修复**：
- 改 `DIARY_DIR` 到你的 Obsidian 日记目录。
- 手动跑 `read_latest_diary()` 看返回。
- 确认文件名是 `YYYY-MM-DD.md`。

## 7. 语音/生图自己起来

**原因**：调用了 `start_all.ps1` / `服务.ps1 start`，或 `toggles.json` 不是双关，或 8921 调度器在跑。

**修复**：
- 只用聊天-only 启动器。
- 确认 `toggles.json`：`image=false, voice=false`。
- 不要启动 8921（能力调度器）。
- 检查 `start_all.ps1` 是否被误调。

## 8. `ModuleNotFoundError` / 路径找不到

**原因**：占位符没替换；启动目录不对；venv 不对。

**修复**：
- 替换所有 `%ASTRBOT_HOME%` 等占位符。
- 在对应目录用 `python -m` 或正确 `cwd`。
- 用 Python 3.12；确认依赖已装。

## 9. PowerShell 中文乱码

**原因**：Windows PowerShell 5.1 按 ANSI 解析 UTF-8。

**修复**：
- 用 `pwsh` 7；或把 `.ps1` 保存为 UTF-8 with BOM。
- 设置 `$env:PYTHONIOENCODING="utf-8"`。

## 10. 端口占用

```powershell
Get-NetTCPConnection -LocalPort 8920,8899,6185 -State Listen
```

找到 PID 后确认是不是自己的进程，再决定停止；不要盲杀。

## 11. 权限拒绝（Desktop/受控文件夹）

Windows 受控文件夹访问可能阻止新建目录/写文件。

**修复**：
- 改到可写目录（如 `G:\`）。
- 或用 PowerShell 创建目录后确认 ACL。
- 不要为了省事关闭系统安全策略。

## 12. 公开仓库相关

- 不要提交 `data/`、`*.log`、`*.bak*`、`*.sqlite3`、凭据。
- 不要把 8899/8765/8920 暴露公网。
- 自动发布（B站/QZone）默认不要无人值守开启。

## 证据位置

- `runtime.log`：AstrBot 主日志
- `gateway.log`：8920
- `plugin_data\whale_core\emotion_snapshot.json`
- `plugin_data\astrbot_plugin_whale_voice\voice_gate.json`
- `family\data\mom_agent_hb.json` / `task_runner_hb.json`
- `family\data\server.log` / `mom_agent.log`