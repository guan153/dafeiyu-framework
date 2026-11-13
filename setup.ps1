param([switch]$CpuTorch)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
Write-Host "== 安装包目录: $root" -ForegroundColor Cyan

# 1. Python
$py = "python"
try { $ver = & $py -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" } catch { throw "找不到 python，请先安装 Python 3.12" }
if ($ver -ne "3.12") { Write-Warning "当前 Python $ver，建议 3.12；继续可能失败" }

# 2. venv
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "== 创建 .venv" -ForegroundColor Cyan
    & $py -m venv .venv
}
$vpy = Join-Path $root ".venv\Scripts\python.exe"
& $vpy -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip 升级失败" }

# 3. torch（CPU 可选）
if ($CpuTorch) {
    Write-Host "== 安装 CPU torch" -ForegroundColor Cyan
    & $vpy -m pip install torch==2.6.0 --index-url https://download.pytorch.org/whl/cpu
    if ($LASTEXITCODE -ne 0) { throw "torch 安装失败" }
}

# 4. 依赖
Write-Host "== 安装 requirements.txt" -ForegroundColor Cyan
& $vpy -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "依赖安装失败" }

# 5. 检查模型
$model = Join-Path $root "laya_emotion\model\laya\multilingual\model.safetensors"
$head  = Join-Path $root "laya_emotion\emotion_cls.pt"
if (-not (Test-Path $model)) { throw "缺少模型：$model（GitHub 版需从 Release 下载并放到该路径）" }
if (-not (Test-Path $head))  { throw "缺少分类头：$head" }
Write-Host "== 模型/分类头都在" -ForegroundColor Green
Write-Host "下一步：powershell -ExecutionPolicy Bypass -File .\verify.ps1" -ForegroundColor Yellow