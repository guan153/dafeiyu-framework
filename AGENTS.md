# AGENTS.md

本包是**情绪闭环框架安装包**，不是完整聊天产品。

## 先读

1. `AI-SETUP.md` —— 配置步骤、验收、排错。
2. `README.md` —— 项目介绍与架构。
3. `docs/EMOTION-LOOP.md` —— 情绪闭环流程。

## 工作边界

- 不改 `laya_emotion/model/` 下的文件名、目录结构、权重。
- 不提交 `gateway_secrets.json`、`.env`、任何 token。
- 不把 8901/8920 绑到 `0.0.0.0`，不公网暴露。
- 不在未确认时重启上层聊天运行时；只启动/停止本包自己的服务。
- 写文件用 UTF-8（无 BOM）；`.ps1` 若给 Windows PowerShell 5.1 用，存 UTF-8 with BOM。
- 改代码前先备份；改完跑 `verify.ps1`。

## 目标

让 `laya_emotion/emotion_server.py` 在 8901 起来，`gateway/beam_gateway.py` 在 8920 起来，上层运行时能读到同一份 `emotion_snapshot.json`。

## 验收

按 `AI-SETUP.md` 第 8 节逐条打勾；没有全过不要写“已完成”。