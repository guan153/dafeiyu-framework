$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
$vpy = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $vpy)) { $vpy = "python" }
$env:PYTHONUTF8 = "1"
& $vpy "laya_emotion\emotion_server.py" --port 8901 --device cpu