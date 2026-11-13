# 长线自动任务一键启动（开机自启用）：调度器 -> 语音栈 -> 听觉 -> 网关/守护
$ErrorActionPreference = "Continue"
$GW_DIR = "%PROJECT_HOME%\audition\gateway"
$PY = "D:\astrbot-venv\Scripts\python.exe"
$TTS_PY = "G:\ai\index-tts\.venv\Scripts\python.exe"
$ASR_PY = "%PROJECT_HOME%\asr_env\Scripts\python.exe"

function Check($port) { try { Invoke-RestMethod "http://127.0.0.1:$port/health" -TimeoutSec 2 | Out-Null; return $true } catch { return $false } }
function PortUp($port) { try { $c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction Stop; return $true } catch { return $false } }

if (-not (PortUp 8921)) { Start-Process -FilePath $PY -ArgumentList "$GW_DIR\capability_manager.py" -WorkingDirectory $GW_DIR -WindowStyle Hidden; Start-Sleep -Seconds 3 }
if (-not (Check 8913)) { Start-Process powershell -ArgumentList "-NoProfile","-WindowStyle","Hidden","-File","G:\ai\feiyu_voice\start_voice_stack.ps1" -WindowStyle Hidden }
if (-not (PortUp 8916)) { Start-Process -FilePath $TTS_PY -ArgumentList "%PROJECT_HOME%\audition\classifier\audio_classify_service.py" -WorkingDirectory "%PROJECT_HOME%\audition\classifier" -WindowStyle Hidden }
if (-not (PortUp 8919)) { Start-Process -FilePath $ASR_PY -ArgumentList "%PROJECT_HOME%\audition\mic_listener.py","--serve","--loopback" -WorkingDirectory "%PROJECT_HOME%\audition" -WindowStyle Hidden }

$runningWd = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match "watchdog\.py" }
if (-not $runningWd) { Start-Process -FilePath $PY -ArgumentList "$GW_DIR\watchdog.py" -WorkingDirectory $GW_DIR -WindowStyle Hidden; Start-Sleep -Seconds 8 }

$listeners = (netstat -ano | Select-String ":8920.*LISTENING").Line
$all = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match "beam_gateway\.py" }
foreach ($p in $all) { if ($listeners -notmatch ("\s" + $p.ProcessId + "\s*$")) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue } }

$msg = "[" + (Get-Date -Format "MM-dd HH:mm:ss") + "] 8921=" + $(if (PortUp 8921) {"UP"} else {"DOWN"}) + " 8912=" + $(if (PortUp 8912) {"UP"} else {"DOWN"}) + " 8913=" + $(if (PortUp 8913) {"UP"} else {"DOWN"}) + " 8916=" + $(if (PortUp 8916) {"UP"} else {"DOWN"}) + " 8919=" + $(if (PortUp 8919) {"UP"} else {"DOWN"}) + " 8920=" + $(if (PortUp 8920) {"UP"} else {"DOWN"})
Add-Content -Path "$GW_DIR\start_all.log" -Value $msg -Encoding UTF8