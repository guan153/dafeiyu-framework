# 一家三口信箱 · 一键打开
# 用法：右键「使用 PowerShell 运行」，或双击
$ErrorActionPreference = 'Continue'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$port = 8899
$url  = "http://127.0.0.1:$port/"

$py = (Get-Command pythonw -ErrorAction SilentlyContinue).Source
if (-not $py) { $py = (Get-Command python -ErrorAction SilentlyContinue).Source }

# 1) 看板
$alive = $false
try {
    $r = Invoke-WebRequest -Uri "$url/api/data" -UseBasicParsing -TimeoutSec 3
    $alive = ($r.StatusCode -eq 200)
} catch { $alive = $false }

if (-not $alive) {
    Write-Host "看板没开，正在启动…"
    Start-Process -FilePath $py -ArgumentList @("$here\server.py", "--port", "$port") -WorkingDirectory $here -WindowStyle Hidden
    Start-Sleep -Seconds 2
} else {
    Write-Host "看板已经开着。"
}

# 1.5) LLM 网关 8920（只起 BEAM，不带 TTS/ASR；妈妈和日记都靠它）
$gwAlive = $false
try { $tc = New-Object Net.Sockets.TcpClient; $tc.Connect('127.0.0.1', 8920); $gwAlive = $true; $tc.Close() } catch { $gwAlive = $false }
if (-not $gwAlive) {
    Write-Host "LLM 网关 8920 不在，正在单独拉起 BEAM（不启动语音）…"
    Start-Process -FilePath 'D:\astrbot-venv\Scripts\python.exe' -ArgumentList '%PROJECT_HOME%\audition\gateway\beam_gateway.py' -WorkingDirectory '%PROJECT_HOME%\audition\gateway' -WindowStyle Hidden
    Start-Sleep -Seconds 6
} else {
    Write-Host "LLM 网关 8920 在线。"
}

# 2) 妈妈的回复器（心跳 90 秒内算活着）
$momAlive = $false
$hb = Join-Path $here "data\mom_agent_hb.json"
if (Test-Path $hb) {
    try {
        $d = Get-Content $hb -Raw -Encoding utf8 | ConvertFrom-Json
        $t = [DateTimeOffset]::FromUnixTimeMilliseconds([int64]([double]$d.ts * 1000)).LocalDateTime
        $momAlive = (((Get-Date) - $t).TotalSeconds -lt 90)
    } catch { $momAlive = $false }
}
if (-not $momAlive) {
    Write-Host "妈妈不在线，正在把她的回复器拉起来…"
    Start-Process -FilePath $py -ArgumentList @("$here\mom_agent.py") -WorkingDirectory $here -WindowStyle Hidden
    Start-Sleep -Seconds 2
} else {
    Write-Host "妈妈在线。"
}

# 3) 任务看护器（心跳 90 秒内算活着）
$runnerAlive = $false
$rhb = Join-Path $here "data\task_runner_hb.json"
if (Test-Path $rhb) {
    try {
        $rd = Get-Content $rhb -Raw -Encoding utf8 | ConvertFrom-Json
        $rt = [DateTimeOffset]::FromUnixTimeMilliseconds([int64]([double]$rd.ts * 1000)).LocalDateTime
        $runnerAlive = (((Get-Date) - $rt).TotalSeconds -lt 90)
    } catch { $runnerAlive = $false }
}
if (-not $runnerAlive) {
    Write-Host "任务看护器不在线，正在拉起来…"
    Start-Process -FilePath $py -ArgumentList @("$here\task_runner.py") -WorkingDirectory $here -WindowStyle Hidden
    Start-Sleep -Seconds 1
} else {
    Write-Host "任务看护器在线。"
}

Write-Host "地址：$url"
Start-Process $url