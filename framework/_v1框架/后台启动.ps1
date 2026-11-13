# 后台启动器：通过计划任务把服务拉起，脱离当前 shell/沙箱进程树，
# 这样关闭终端、DSH 重载都不会把机器人带走。
$ErrorActionPreference = 'Stop'
$svc = Join-Path $PSScriptRoot '服务.ps1'
$task = 'V1-Bot-Stack'
$tr = 'powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $svc + '" -Action start'
Write-Host '[*] 创建/更新计划任务 ' $task
& schtasks /create /tn $task /tr $tr /sc once /st 23:59 /f 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Host '[!] 任务创建失败，改用直接启动（关闭本窗口不影响）'; & powershell -NoProfile -ExecutionPolicy Bypass -File $svc -Action start; exit }
Write-Host '[+] 触发任务...'
& schtasks /run /tn $task 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Host '[!] 任务触发失败'; exit 1 }
Write-Host '[+] 已触发，等待服务就绪（最多 240 秒）...'
$sw = [Diagnostics.Stopwatch]::StartNew()
$ok = $false
while ($sw.Elapsed.TotalSeconds -lt 240) {
  Start-Sleep -Seconds 5
  try {
    $r = Invoke-WebRequest 'http://127.0.0.1:8920/health' -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
    $t = (New-Object Net.Sockets.TcpClient); $ar = $t.BeginConnect('127.0.0.1', 6185, $null, $null)
    $ab = $ar.AsyncWaitHandle.WaitOne(500); $t.Close()
    if ($r.StatusCode -eq 200 -and $ab) { $ok = $true; break }
  } catch {}
}
if ($ok) { Write-Host ('[OK] 服务已脱离后台运行（{0}s）' -f [int]$sw.Elapsed.TotalSeconds) -ForegroundColor Green }
else { Write-Host '[!] 未在超时内就绪，请运行 状态.bat 查看' -ForegroundColor Yellow }
Write-Host ''
Write-Host '提示：之后用 停止.bat 停止；本任务只负责拉起，不会自动重启。'