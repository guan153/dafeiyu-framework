param([switch]$LoadModel)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
$vpy = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $vpy)) { $vpy = "python" }
$fail = 0

function Check($name, $ok) {
    if ($ok) { Write-Host "[PASS] $name" -ForegroundColor Green }
    else { Write-Host "[FAIL] $name" -ForegroundColor Red; $script:fail++ }
}

Check "model.safetensors" (Test-Path "laya_emotion\model\laya\multilingual\model.safetensors")
Check "tokenizer.json" (Test-Path "laya_emotion\model\laya\multilingual\tokenizer\tokenizer.json")
Check "emotion_cls.pt" (Test-Path "laya_emotion\emotion_cls.pt")
Check "laya_pkg" (Test-Path "laya_emotion\laya_pkg\laya\__init__.py")

$env:PYTHONUTF8 = "1"
$env:HF_HUB_OFFLINE = "1"
& $vpy -c "import sys; sys.path.insert(0, r'laya_emotion\laya_pkg'); import laya; print('laya import ok:', laya.__file__)" 2>&1 | ForEach-Object { Write-Host $_ }
if ($LASTEXITCODE -ne 0) { $fail++ }

if ($LoadModel) {
    Write-Host "== 加载模型（可能 10-30s）" -ForegroundColor Cyan
    & $vpy -c "import sys; sys.path.insert(0, r'laya_emotion\laya_pkg'); import laya; a=laya.load(r'laya_emotion\model\laya', subfolder='multilingual', device='cpu'); print('LOAD_OK')" 2>&1 | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0) { $fail++ }
}

foreach ($port in @(8901,8920)) {
    try { $r = Invoke-RestMethod -Uri "http://127.0.0.1:$port/health" -TimeoutSec 3; Write-Host "[PASS] port $port health: $($r | ConvertTo-Json -Compress)" -ForegroundColor Green }
    catch { Write-Host "[SKIP] port $port 未启动" -ForegroundColor Yellow }
}

if ($fail -gt 0) { Write-Host "FAIL: $fail 项" -ForegroundColor Red; exit 1 }
Write-Host "ALL CHECKS PASS" -ForegroundColor Green