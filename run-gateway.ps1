$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
$vpy = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $vpy)) { $vpy = "python" }
$env:PYTHONUTF8 = "1"
if (-not $env:BEAM_SECRETS_FILE) { $env:BEAM_SECRETS_FILE = Join-Path $root "gateway_secrets.json" }
& $vpy "gateway\beam_gateway.py"