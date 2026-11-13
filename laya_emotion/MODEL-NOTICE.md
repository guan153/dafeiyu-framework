# laya 模型包说明

本目录在**桌面安装包**里包含 laya multilingual encoder（约 646.8MB，5 个文件），放在 `laya_emotion/model/laya/multilingual/`。

GitHub 仓库不包含这份权重（单文件超 100MB），只包含 26KB 的 `emotion_cls.pt` 分类头；GitHub 版运行时会尝试从 Hugging Face 拉取 `convaiinnovations/laya`。

## 路径

- 包内模型：`laya_emotion/model/laya/multilingual/`
- 分类头：`laya_emotion/emotion_cls.pt`
- 代码默认：优先读包内模型；没有则回退 `convaiinnovations/laya`

## 环境变量（可选）

```powershell
$env:LAYA_HOME="C:\path\to\laya"          # laya Python 包
$env:LAYA_MODEL_DIR="C:\path\to\model\laya"  # 模型根目录，里面要有 multilingual/
```

## 许可

encoder 权重来自 `convaiinnovations/laya`，使用/再分发前请确认上游许可；本包只用于本地运行。
