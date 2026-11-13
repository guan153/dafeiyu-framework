# laya 情感分类器改装

## 为什么用 laya

laya 是一个多语言文本 encoder。我们用它的 embedding 作为特征，再训练一个**轻量 8 类分类头**，把"这句话是什么情绪"变成可计算、可缓存、可降级的输入。

它不是情绪事实源；它只是 `whale_affect` 的一个输入特征。最终情绪仍由情绪内核写快照。

## 模型结构

```
文本
  → laya multilingual encoder（冻结）
  → 768 维 embedding
  → Linear(768, 8)
  → softmax
  → label / confidence / probs
```

- 基座：`convaiinnovations/laya`，`subfolder="multilingual"`
- 分类头：`emotion_cls.pt`（26KB，只存 head + 两个映射，不含 encoder 权重）
- 类别（8 类）：

| id | label |
|---|---|
| 0 | 兴奋 |
| 1 | 吃醋 |
| 2 | 平静 |
| 3 | 开心 |
| 4 | 撒娇 |
| 5 | 生气 |
| 6 | 疲惫 |
| 7 | 难过 |

## 改装点（相对原版）

1. **CPU 默认**：`--device cpu`，省显存；`--device cuda` 可切回 GPU。
2. **自定义 8 类头**：不是通用情感三分类，是按角色表达习惯标注的 8 类。
3. **问候词纠正**：`在吗`/`你好` 这类问候，分类器容易判成"撒娇"；上层用 `goal_congruence` 规则改归"期待"，不信模型的撒娇判断。
4. **低置信回退**：分类失败或置信度低时，回退词表/neutral，不阻塞主流程。
5. **HTTP 常驻**：8901 端口，`/classify` 同步返回，便于多进程复用。
6. **标签映射**：8 类 → `goal_congruence`/强度，供情绪内核和闸门使用。

## HTTP 接口

### `POST /classify`

```bash
curl -X POST http://127.0.0.1:8901/classify \
  -H "Content-Type: application/json" \
  -d '{"text":"今天有点累"}'
```

返回：

```json
{
  "label": "疲惫",
  "confidence": 0.87,
  "probs": {"兴奋": 0.01, "吃醋": 0.01, "平静": 0.03, "开心": 0.01,
            "撒娇": 0.02, "生气": 0.01, "疲惫": 0.87, "难过": 0.03}
}
```

### `GET /health`

```json
{"ok": true, "device": "cpu", "classes": ["兴奋","吃醋","平静","开心","撒娇","生气","疲惫","难过"]}
```

## 启动

```powershell
$env:LAYA_HOME="C:\path\to\laya"
$env:EMOTION_CLS_MODEL="C:\path\to\emotion_cls.pt"
python laya_emotion/emotion_server.py --port 8901 --device cpu
```

`emotion_server.py` 会：

1. `laya.load(...)` 加载 multilingual encoder；
2. `torch.load(emotion_cls.pt)` 加载 8 类头；
3. 监听 `127.0.0.1:8901`；
4. 输入截断到 500 字，返回 label/confidence/probs。

## 与情绪闭环的关系

```
laya 分类器 → 文本情绪特征
                ↓
whale_affect（写者）→ emotion_snapshot
                ↓
state_bus / brain / face / gate / gateway
```

分类器只提供输入；它**不能直接写快照**，也不能绕过 `claim()`。

## 训练与替换

- 训练数据：按 8 类标注的短文本；保持与角色表达一致。
- 损失：交叉熵；encoder 冻结，只训 head。
- 产物：`emotion_cls.pt`（`head`、`label2id`、`id2label`）。
- 替换后先跑 `/classify` 看 probs，不要直接上线；用低置信回退兜底。

## 已知边界

- 8 类固定，不能自动发现新情绪。
- confidence 未做校准，只能当相对信号。
- 强反讽/长上下文识别弱；必须和词表/上下文规则一起用。
- CPU 单条推理毫秒级，但高并发要排队；生产需要加超时和熔断。
## 模型包（可选）

- **分类头**：`emotion_cls.pt`，26KB，仓库自带。
- **laya encoder**：646.8MB（5 个文件，`model.safetensors` 约 614MB）。
- GitHub 仓库不包含 encoder（单文件超 100MB）；桌面安装包 `大肥鱼-情绪框架-安装包-v0.1` 内已包含，路径：
  `laya_emotion/model/laya/multilingual/`
- 代码优先读包内模型，找不到才回退 `convaiinnovations/laya` 在线下载；也可用 `LAYA_MODEL_DIR` 指定。
- 本机离线加载实测：`LOAD_OK`，耗时 11.4s（CPU，HF_HUB_OFFLINE=1）。
## 完整模型下载

- Release：[v0.1.0-laya-model](https://github.com/guan153/dafeiyu-framework/releases/tag/v0.1.0-laya-model)
- 文件：dafeiyu-laya-emotion-model-v0.1.zip（599,118,209 B / 约 571 MB）
- 内容：model.safetensors（614MB）、	okenizer.json（32.8MB）、encoder/tokenizer 配置、motion_cls.pt（26KB）
- SHA256：E3DFD5F84415716C72BDCD5B11FE4022D0A38AAE937AEE7BC95B8E2F6312231C
- 仓库树不放 614MB 单文件（GitHub 100MB 限制），所以放 Release；桌面安装包内也有同款。