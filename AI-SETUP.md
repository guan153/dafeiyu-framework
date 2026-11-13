# AI-SETUP：给另一个 AI 的配置说明

> 你拿到的是一个**本地情绪框架安装包**。  
> 你的任务不是改代码，而是把它配起来、跑起来、验证通过。  
> 先读本文件，再动任何命令。

## 0. 这个包是什么

- `emotion_core/`：情绪内核，负责写 `emotion_snapshot.json`
- `gateway/`：BEAM 网关（8920），把模型调用收进一条管线
- `laya_emotion/`：laya 8 类情感分类器（8901）
  - `model/laya/multilingual/`：完整 encoder（614MB）
  - `emotion_cls.pt`：咱改的 8 类分类头（26KB）
  - `laya_pkg/laya/`：laya Python 包源码（Apache-2.0），不需要再 pip install
- `framework/`、`docs/`：框架代码与文档
- `setup.ps1` / `verify.ps1` / `run-laya.ps1` / `run-gateway.ps1`：一键脚本
- `config.example.json`、`.env.example`：配置模板

**包内不含**：Python 运行时、torch 等依赖、上层聊天运行时、真实密钥。  
**包内含**：完整模型、laya 代码、分类头、启动/自检脚本。

## 1. 先检查环境

在包根目录执行：

```powershell
python --version
Test-Path .\laya_emotion\model\laya\multilingual\model.safetensors
Test-Path .\laya_emotion\emotion_cls.pt
```

期望：

- Python 3.12（3.11 可能也能跑，但按 3.12 验）
- 两个 `Test-Path` 都返回 `True`

如果模型不存在，优先下载完整安装包（已含模型）：

https://github.com/guan153/dafeiyu-framework/releases/tag/v0.1.0-ai-ready

或只下模型包：


`https://github.com/guan153/dafeiyu-framework/releases/tag/v0.1.0-laya-model`

解压后确保路径是：

`laya_emotion/model/laya/multilingual/model.safetensors`

## 2. 安装依赖

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

CPU 机器建议：

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1 -CpuTorch
```

这一步会：

1. 检查 Python 3.12；
2. 创建 `.venv`；
3. 安装 `requirements.txt`；
4. 检查模型和分类头。

如果 torch 太大/安装失败，先单独装 CPU 版：

```powershell
.\.venv\Scripts\python.exe -m pip install torch==2.6.0 --index-url https://download.pytorch.org/whl/cpu
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 3. 自检

```powershell
powershell -ExecutionPolicy Bypass -File .\verify.ps1
```

可选加载模型（约 10–30 秒）：

```powershell
powershell -ExecutionPolicy Bypass -File .\verify.ps1 -LoadModel
```

期望看到：

- `[PASS] model.safetensors`
- `[PASS] tokenizer.json`
- `[PASS] emotion_cls.pt`
- `[PASS] laya_pkg`
- `laya import ok: ...laya_pkg\laya\__init__.py`
- 如果 8901/8920 已启动，会显示 health；没启动是 `[SKIP]`
- 最后 `ALL CHECKS PASS`

## 4. 配置密钥

```powershell
Copy-Item .\config.example.json .\gateway_secrets.json
notepad .\gateway_secrets.json
```

把 `REPLACE_ME` 换成真实值：

```json
{
  "deepseek_api_key": "sk-...",
  "onebot_token": "...",
  "debug_token": "..."
}
```

**不要**把 `gateway_secrets.json` 提交、截图或发给别人。

## 5. 启动分类器（8901）

```powershell
powershell -ExecutionPolicy Bypass -File .\run-laya.ps1
```

验证：

```powershell
Invoke-RestMethod http://127.0.0.1:8901/health
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8901/classify -ContentType "application/json" -Body '{"text":"今天有点累"}'
```

期望：

- `/health` 返回 `ok=True`，`classes` 有 8 类
- `/classify` 返回 `label`、`confidence`、`probs`

## 6. 启动网关（8920）

另开一个 PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File .\run-gateway.ps1
```

验证：

```powershell
Invoke-RestMethod http://127.0.0.1:8920/health
```

期望：`ok=True`，`requests/injected` 数字在涨。

## 7. 接上层运行时

在你的上层聊天运行时里，把模型 `api_base` 指到：

`http://127.0.0.1:8920/v1`

然后确认：

- 上层运行时能加载 `emotion_core`；
- `whale_affect` 能 `claim()` 成为写者；
- 读方按 `shadow_switches.json` 选 `snapshot`；
- 发一条消息后，`emotion_snapshot.json` 在 60 秒内更新。

## 8. 验收清单

- [ ] 8901 `/health` 8 类
- [ ] 8901 `/classify` 返回 label/confidence/probs
- [ ] 8920 `/health` `ok=True`
- [ ] `emotion_snapshot.json` 有 `mood`、`version`、`ts`
- [ ] 日志有写者登记、主循环启动
- [ ] 读方 mood 一致
- [ ] 网关 `requests == injected`
- [ ] 没有把 8901/8920 暴露到公网

## 9. 不要做

- 不要改 `laya_emotion/model/` 下的文件名或目录结构；
- 不要提交 `gateway_secrets.json`、`.env`、token；
- 不要 `0.0.0.0` 绑定 8901/8920；
- 不要在没有确认的情况下重启上层运行时之外的进程；
- 不要为了“跑通”删模型或跳过校验。

## 10. 出问题先看

| 现象 | 先查 |
|---|---|
| `ModuleNotFoundError: laya` | `laya_emotion/laya_pkg/laya` 是否存在；`PYTHONPATH` |
| `ModuleNotFoundError: torch` | 是否执行了 setup；venv 是否激活 |
| `FileNotFoundError: model.safetensors` | 模型路径是否是 `laya_emotion/model/laya/multilingual/` |
| 8901 起不来 | 端口占用、Python 版本、依赖 |
| 分类总是平静 | 输入太短；看 confidence；确认分类头 8 类 |
| 8920 连接失败 | `gateway_secrets.json`、上游 key、端口 |
| 快照不更新 | 写者是否 claim、主循环是否启动、`emotion.snapshot` 是否关闭 |

## 11. 给 AI 的输出要求

配置完成后，输出：

1. 执行了哪些命令；
2. 哪些检查 PASS/FAIL；
3. 模型路径、分类器 health、网关 health；
4. 还有哪些需要人填的密钥/配置；
5. 不要声称“已上线”，除非验收清单全过。

---

**一句话**：先 `setup.ps1`，再 `verify.ps1`，填 `gateway_secrets.json`，起 8901 和 8920，最后按验收清单逐条打勾。