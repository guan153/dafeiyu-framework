# ============================================================
#  capability_state.json 修复工具   (2026-11-13)
#
#  背景：
#    能力调度器（capability_manager.py, 8921）把「当前激活能力」持久化在
#      %PROJECT_HOME%\audition\gateway\capability_state.json
#    格式：{"current": "draw"|"speak"|"listen"|"free"|"switching"|"unknown", "ts": <epoch>}
#
#    2026-11-12 12:10 调度器把 current 置成 "draw" 后就再没起来过，
#    文件里一直残留 draw。toggles.json 现在是 image=false / voice=false（都关）。
#
#    残留 draw 的危害：
#      1) 调度器下次启动时 _load_state() 会认为当前在生图模式；
#      2) _auto_guard() 每 30 秒命中 `current=="draw" and not toggles.image`
#         → 调 switch("free")，而 CAPS 里没有 "free" → 空转 + 每 30 秒刷一条日志；
#      3) 一旦 toggles.json 丢失/写坏（_read_toggles 是 fail-open 默认全开），
#         同一分支会走到 `if not port_up(8188): start_service("draw")`
#         → 把 ComfyUI 重新拉起来抢卡。
#
#  本工具只做一件事：把 current 写回 free（并留备份）。
#  不会启动/停止 8921，不会碰任何服务，不动 toggles.json。
#
#  用法：
#    powershell -NoProfile -ExecutionPolicy Bypass -File fix_capability_state.ps1 -Action show
#    powershell -NoProfile -ExecutionPolicy Bypass -File fix_capability_state.ps1 -Action fix
# ============================================================
param(
  [ValidateSet('show','fix')]
  [string]$Action = 'show',
  [string]$Target = 'free'
)

$ErrorActionPreference = 'Continue'

$STATE_FILE = '%PROJECT_HOME%\audition\gateway\capability_state.json'
# 备份放在本工具目录下（不往 gateway 目录里新增文件）
$BACKUP_DIR = Join-Path $PSScriptRoot 'backups'

function Test-Tcp([int]$Port, [int]$TimeoutMs = 600) {
  $c = New-Object System.Net.Sockets.TcpClient
  try {
    $ar = $c.BeginConnect('127.0.0.1', $Port, $null, $null)
    if (-not $ar.AsyncWaitHandle.WaitOne($TimeoutMs)) { return $false }
    $c.EndConnect($ar)
    return $true
  } catch { return $false } finally { $c.Close() }
}

function Show-State {
  if (-not (Test-Path -LiteralPath $STATE_FILE)) {
    Write-Host "  capability_state.json 不存在（调度器下次启动会自己 _save_state 建一个）" -ForegroundColor Yellow
    return $null
  }
  $raw = Get-Content -LiteralPath $STATE_FILE -Raw -Encoding UTF8
  Write-Host ('  原始内容: ' + $raw.Trim())
  try {
    $j = $raw | ConvertFrom-Json
    $cur = [string]$j.current
    $ts = 0.0
    try { $ts = [double]$j.ts } catch { }
    $when = ''
    if ($ts -gt 0) {
      $dto = [DateTimeOffset]::FromUnixTimeSeconds([long][math]::Floor($ts)).ToOffset([TimeSpan]::FromHours(8))
      $when = ' (北京 ' + $dto.ToString('yyyy-MM-dd HH:mm:ss') + ')'
    }
    Write-Host ("  解析后: current = '{0}'   ts = {1}{2}" -f $cur, $ts, $when)
    return $cur
  } catch {
    Write-Host ('  解析失败: ' + $_.Exception.Message) -ForegroundColor Red
    return $null
  }
}

Write-Host '==== capability_state.json 状态修正 ====' -ForegroundColor Cyan
Write-Host ('文件: ' + $STATE_FILE)

$cur = Show-State

Write-Host ''
Write-Host '---- 一致性检查（只读）----' -ForegroundColor Cyan
$gpuUp = @()
if (Test-Tcp 8912) { $gpuUp += '8912(TTS)' }
if (Test-Tcp 8188) { $gpuUp += '8188(ComfyUI)' }
if ($gpuUp.Count -gt 0) {
  Write-Host ('  WARN 有 GPU 服务在跑: ' + ($gpuUp -join ', ') + ' → 此时写 free 会与实际不符') -ForegroundColor Yellow
} else {
  Write-Host '  OK   GPU 服务全部 DOWN → 实际状态就是 free，写 free 与事实一致' -ForegroundColor Green
}
if (Test-Tcp 8921) {
  Write-Host '  WARN 8921 调度器在线：它手里可能持有本文件，改之前最好先确认它没在切换中。' -ForegroundColor Yellow
} else {
  Write-Host '  OK   8921 未运行 → 没有进程持有本文件，可安全改写' -ForegroundColor Green
}

if ($Action -eq 'show') {
  Write-Host ''
  Write-Host '（show 模式，未做任何修改。要修就加 -Action fix）' -ForegroundColor Gray
  exit 0
}

# ---------------- 修正 ----------------
if ($cur -eq $Target) {
  Write-Host ''
  Write-Host ("已经是 '{0}'，无需修改。" -f $Target) -ForegroundColor Green
  exit 0
}
if ($gpuUp.Count -gt 0 -and $Target -eq 'free') {
  Write-Host ''
  Write-Host '拒绝修改：有 GPU 服务在跑，写 free 是假状态。请先确认那些服务是否该关。' -ForegroundColor Red
  exit 2
}

try {
  $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
  New-Item -ItemType Directory -Path $BACKUP_DIR -Force | Out-Null
  $bak = Join-Path $BACKUP_DIR ('capability_state.json.bak-' + $stamp)
  if (Test-Path -LiteralPath $STATE_FILE) { Copy-Item -LiteralPath $STATE_FILE -Destination $bak -Force }
  Write-Host ('  已备份 -> ' + $bak) -ForegroundColor Gray

  $epoch = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
  $obj = [ordered]@{ current = $Target; ts = $epoch }
  $json = ($obj | ConvertTo-Json -Compress)
  # 与 python 端一致：UTF-8 无 BOM
  [IO.File]::WriteAllText($STATE_FILE, $json, (New-Object Text.UTF8Encoding($false)))

  Write-Host ''
  Write-Host '---- 写后复核 ----' -ForegroundColor Cyan
  Write-Host ('  新内容: ' + (Get-Content -LiteralPath $STATE_FILE -Raw -Encoding UTF8).Trim())
  $j2 = (Get-Content -LiteralPath $STATE_FILE -Raw -Encoding UTF8) | ConvertFrom-Json
  if ([string]$j2.current -eq $Target) {
    Write-Host ("  OK   current 已写为 '{0}'，修复完成。" -f $Target) -ForegroundColor Green
    Write-Host ('  回滚: Copy-Item -LiteralPath "' + $bak + '" -Destination "' + $STATE_FILE + '" -Force') -ForegroundColor Gray
    exit 0
  }
  Write-Host '  FAIL 复核不一致！请手动检查。' -ForegroundColor Red
  exit 1
} catch {
  Write-Host ('  写入失败: ' + $_.Exception.Message) -ForegroundColor Red
  exit 1
}
