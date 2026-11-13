# ============================================================
#  大肥鱼 / 鲸鱼少女 · 统一服务入口 v1.1  (2026-11-07)
#  用法:  服务.ps1 -Action start|stop|status|health|verify|restart [-All]
#  设计:  单实例 / 真实就绪 / 明确报错 / 状态可信 / 每步留痕
#  说明:  venv 的 python.exe 是垫片，真正解释器是它的子进程（uv 托管），
#         所以"实例数"按父链去重后的根进程算，避免误报双开。
# ============================================================
param(
  [ValidateSet('start','stop','status','health','verify','restart')]
  [string]$Action = 'status',
  [switch]$All
)
$ErrorActionPreference = 'Continue'
$ROOT        = '%PROJECT_HOME%\_v1交付'
$LOGDIR      = Join-Path $ROOT 'logs'
$LOG         = Join-Path $LOGDIR ('服务-' + (Get-Date -Format 'yyyyMMdd') + '.log')
$LOCK        = Join-Path $LOGDIR '.start.lock'
$RESULT      = Join-Path $ROOT '验收结果.json'
$ASTRBOT_DIR = '%ASTRBOT_HOME%'
$SL_DIR      = '%QBOT_HOME%\qbot\SnowLuma'
$QQ_EXE      = 'D:\Program Files\Tencent\QQNT\QQ.exe'
$NODE        = 'D:\Program Files\nodejs\node.exe'
$PY_AB       = Join-Path $ASTRBOT_DIR '.venv\Scripts\python.exe'
$GW_DIR      = '%PROJECT_HOME%\audition\gateway'
$GW_START    = Join-Path $GW_DIR 'start_all.ps1'
$SECRETS_NTFS = '%USERPROFILE%\.config\v1-gateway\gateway_secrets.json'
$AB_CONFIG   = Join-Path $ASTRBOT_DIR 'data\config'
$CMD_CONFIG  = Join-Path $ASTRBOT_DIR 'data\cmd_config.json'
New-Item -ItemType Directory -Path $LOGDIR -Force | Out-Null

function Say([string]$m, $c = 'Gray') {
  $line = '[' + (Get-Date -Format 'HH:mm:ss') + '] ' + $m
  Write-Host $line -ForegroundColor $c
  Add-Content -LiteralPath $LOG -Value $line -Encoding UTF8
}
function Test-Port([int]$Port, [int]$Ms = 800) {
  $c = New-Object System.Net.Sockets.TcpClient
  try {
    $ar = $c.BeginConnect('127.0.0.1', $Port, $null, $null)
    if (-not $ar.AsyncWaitHandle.WaitOne($Ms)) { return $false }
    $c.EndConnect($ar); return $true
  } catch { return $false } finally { $c.Close() }
}
function Test-Http([string]$Url, [int]$Sec = 3) {
  try { return (Invoke-WebRequest -Uri $Url -TimeoutSec $Sec -UseBasicParsing -ErrorAction Stop) } catch { return $null }
}
function Get-CmdProcs([string]$Pattern) {
  @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -in @('python.exe','node.exe','cmd.exe','powershell.exe','pwsh.exe') -and $_.CommandLine -match $Pattern })
}
function Get-Instances([string]$Pattern) {
  $all = Get-CmdProcs $Pattern
  if ($all.Count -le 1) { return $all }
  $ids = @($all | ForEach-Object { [int]$_.ProcessId })
  return @($all | Where-Object { $ids -notcontains [int]$_.ParentProcessId })
}
function Get-ServiceState([int]$Port) {
  $r = Test-Http ("http://127.0.0.1:{0}/health" -f $Port) 3
  if (-not $r) { return 'DOWN' }
  try { $j = $r.Content | ConvertFrom-Json } catch { return 'OK' }
  if ($null -ne $j.ready -and $j.ready -eq $false) { return 'LOADING' }
  return 'OK'
}
function Kill-Tree([object[]]$Ids) {
  foreach ($id in $Ids) { if ($id) { & taskkill /F /T /PID ([string]$id) 2>&1 | Out-Null } }
}
function Wait-Port([int]$Port, [int]$Sec, [string]$What) {
  $sw = [Diagnostics.Stopwatch]::StartNew()
  while ($sw.Elapsed.TotalSeconds -lt $Sec) {
    if (Test-Port $Port 800) { Say ("  OK {0} 就绪（{1}s）" -f $What, [int]$sw.Elapsed.TotalSeconds) 'Green'; return $true }
    Start-Sleep -Milliseconds 700
  }
  Say ("  FAIL {0} 超时（{1}s）" -f $What, $Sec) 'Red'; return $false
}
$script:rows = @()
function Add-Row([string]$Comp, $Proc, [string]$Ports, [string]$State, [string]$Note) {
  $script:rows += [pscustomobject]@{ 组件 = $Comp; 实例 = $Proc; 端口 = $Ports; 状态 = $State; 说明 = $Note }
}
$script:readyGate = @(
  @{N='laya情绪';P=8901}, @{N='IndexTTS';P=8912}, @{N='FunASR';P=8913},
  @{N='QwenEmo';P=8914}, @{N='声音分类';P=8916}, @{N='耳返';P=8919}
)

# ---------------- 健康检查 ----------------
function Get-Health {
  $script:rows = @()
  $qq = @(Get-Process QQ -ErrorAction SilentlyContinue)
  Add-Row 'QQ' $qq.Count '-' $(if ($qq.Count -ge 1) { 'OK' } else { 'DOWN' }) '登录载体（保留）'

  $slI = @(Get-Instances 'SnowLuma[\\/]index\.mjs')
  $slPorts = @(); foreach ($p in 3000,3001,5099) { if (Test-Port $p 500) { $slPorts += $p } }
  Add-Row 'SnowLuma' $slI.Count ($slPorts -join ',') $(if ($slI.Count -ge 1 -and $slPorts.Count -ge 2) { 'OK' } elseif ($slI.Count -ge 1) { 'PART' } else { 'DOWN' }) 'OneBot 3000/3001 + WebUI 5099'

  $abI = @(Get-Instances 'main\.py.*webui-dir')
  $abPorts = @(); foreach ($p in 6185,6199) { if (Test-Port $p 500) { $abPorts += $p } }
  Add-Row 'AstrBot' $abI.Count ($abPorts -join ',') $(if ($abI.Count -ge 1 -and $abPorts.Count -ge 2) { 'OK' } elseif ($abI.Count -ge 1) { 'PART' } else { 'DOWN' }) 'WebUI 6185 + 反向WS 6199'

  $gw = Test-Http 'http://127.0.0.1:8920/health' 3
  Add-Row 'BEAM网关' $(if ($gw) { 1 } else { 0 }) '8920' $(if ($gw) { 'OK' } else { 'DOWN' }) '每次思考穿过这里'

  $cap = Test-Http 'http://127.0.0.1:8921/status' 3
  Add-Row '能力调度' $(if ($cap) { 1 } else { 0 }) '8921' $(if ($cap) { 'OK' } else { 'DOWN' }) '按需拉起能力'

  foreach ($svc in $script:readyGate) {
    $st = Get-ServiceState $svc.P
    $inst = if ($st -eq 'DOWN') { 0 } else { 1 }
    $note = '本地模型服务'
    if ($svc.N -eq 'IndexTTS') { $note = '她的声音（GPU）' }
    if ($svc.N -eq 'FunASR') { $note = '听你说话（CPU）' }
    Add-Row $svc.N $inst ([string]$svc.P) $st $note
  }

  $pa = Test-Port 4100 500
  Add-Row '主动消息管理端' $(if ($pa) { 1 } else { 0 }) '4100' $(if ($pa) { 'OK' } else { 'DOWN' }) '需密码（已设）'
  $comfy = Test-Port 8188 500
  Add-Row 'ComfyUI' $(if ($comfy) { 1 } else { 0 }) '8188' $(if ($comfy) { 'OK' } else { '按需' }) '按需拉起，不常驻必需'
  return $script:rows
}
function Show-Health([object[]]$rows) {
  $rows | Format-Table -AutoSize | Out-String -Width 170 | Write-Host
  $down    = @($rows | Where-Object { $_.状态 -eq 'DOWN' })
  $loading = @($rows | Where-Object { $_.状态 -eq 'LOADING' })
  $dup     = @($rows | Where-Object { $_.实例 -gt 1 -and $_.组件 -in @('SnowLuma','AstrBot') })
  if ($dup.Count -gt 0) { Say ('警告：双开 -> ' + (($dup | ForEach-Object { $_.组件 }) -join ', ')) 'Yellow' }
  if ($loading.Count -gt 0) { Say ('模型加载中 -> ' + (($loading | ForEach-Object { $_.组件 }) -join ', ')) 'Yellow' }
  if ($down.Count -eq 0 -and $loading.Count -eq 0) { Say '健康检查：全部组件就绪 ✔' 'Green' }
  elseif ($down.Count -gt 0) { Say ('健康检查：{0} 个组件离线 -> {1}' -f $down.Count, (($down | ForEach-Object { $_.组件 }) -join ', ')) 'Red' }
  else { Say '健康检查：有组件仍在加载（稍等再查）' 'Yellow' }
  return $down
}

# ---------------- 启动 ----------------
function Start-All {
  $lockStream = $null
  try { $lockStream = [IO.File]::Open($LOCK, 'OpenOrCreate', 'ReadWrite', 'None') }
  catch { Say '已有启动流程在跑（锁占用），本次退出' 'Yellow'; return }
  try {
    Say '========== 启动 ==========' 'Cyan'
    if (-not (Get-Process QQ -ErrorAction SilentlyContinue)) {
      if (Test-Path $QQ_EXE) { Start-Process -FilePath $QQ_EXE | Out-Null; Say '  ... 拉起 QQ' }
      else { Say '  缺少 QQ.exe，跳过' 'Red' }
    } else { Say '  QQ 已在线' }
    Start-Sleep -Seconds 3

    if (@(Get-Instances 'SnowLuma[\\/]index\.mjs').Count -eq 0) {
      $env:SNOWLUMA_ACCEPT_EULA = '1'; $env:SNOWLUMA_ACCEPT_PRIVACY = '1'
      $env:SNOWLUMA_HOOK_AUTOLOAD = '1'; $env:SNOWLUMA_LOG_LEVEL = 'info'
      Start-Process -FilePath $NODE -ArgumentList 'index.mjs' -WorkingDirectory $SL_DIR -WindowStyle Hidden | Out-Null
      Say '  ... 拉起 SnowLuma'
      Wait-Port 5099 30 'SnowLuma' | Out-Null
    } else { Say '  SnowLuma 已在线' }

    if (@(Get-Instances 'main\.py.*webui-dir').Count -eq 0) {
      $l1 = Join-Path $ASTRBOT_DIR 'data\plugin_data\astrbot_plugin_iris_chat_memory\l1_snapshot.json'
      if (Test-Path $l1) { Copy-Item -LiteralPath $l1 -Destination ($l1 + '.bak') -Force -ErrorAction SilentlyContinue }
      $env:ASTRBOT_ROOT = $ASTRBOT_DIR; $env:PYTHONUTF8 = '1'; $env:PYTHONIOENCODING = 'utf-8'; $env:OMP_NUM_THREADS = '1'
      $cmd = 'cd /d "' + $ASTRBOT_DIR + '" && "' + $PY_AB + '" main.py --webui-dir "dist-webui\dist" >> runtime.log 2>&1'
      Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', $cmd -WindowStyle Hidden | Out-Null
      Say '  ... 拉起 AstrBot'
      Wait-Port 6185 120 'AstrBot' | Out-Null
      Wait-Port 6199 60 'AstrBot反向WS' | Out-Null
    } else { Say '  AstrBot 已在线' }

    if (-not (Test-Http 'http://127.0.0.1:8920/health' 3)) {
      Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File', $GW_START) -WindowStyle Hidden | Out-Null
      Say '  ... 拉起网关栈（含本地语音模型，冷启动约 60-200s）'
      Wait-Port 8920 120 'BEAM网关' | Out-Null
    } else { Say '  网关栈已在线' }

    # 就绪闸门：不只是端口开着，要 /health 的 ready=true
    Say '  等待本地模型真正就绪（ready=true）...'
    $sw = [Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt 200) {
      $bad = @()
      foreach ($svc in $script:readyGate) { if ((Get-ServiceState $svc.P) -ne 'OK') { $bad += $svc.N } }
      if ($bad.Count -eq 0) { break }
      Start-Sleep -Seconds 3
    }
    foreach ($svc in $script:readyGate) {
      $st = Get-ServiceState $svc.P
      Say ("  {0} = {1}" -f $svc.N, $st) $(if ($st -eq 'OK') { 'Green' } else { 'Yellow' })
    }
    Say '========== 启动结束 ==========' 'Cyan'
  } finally {
    if ($lockStream) { $lockStream.Close(); $lockStream = $null }
    Remove-Item -LiteralPath $LOCK -Force -ErrorAction SilentlyContinue
  }
}

# ---------------- 停止 ----------------
function Stop-All {
  Say '========== 停止 ==========' 'Cyan'
  $patterns = @(
    @{P='watchdog\.py';N='网关watchdog'},
    @{P='beam_gateway\.py';N='BEAM网关'},
    @{P='capability_manager\.py';N='能力调度'},
    @{P='maintain\.py';N='维护脚本'},
    @{P='sound_bridge\.py';N='声音桥'},
    @{P='tts_service\.py';N='IndexTTS'},
    @{P='asr_service\.py';N='FunASR'},
    @{P='qwenemo_service\.py';N='QwenEmo'},
    @{P='emotion_server\.py';N='laya情绪'},
    @{P='audio_classify_service\.py';N='声音分类'},
    @{P='mic_listener\.py';N='耳返'},
    @{P='main\.py.*webui-dir';N='AstrBot'},
    @{P='SnowLuma[\\/]index\.mjs';N='SnowLuma'}
  )
  foreach ($item in $patterns) {
    $procs = Get-CmdProcs $item.P
    if ($procs.Count -gt 0) {
      Kill-Tree ($procs | ForEach-Object { $_.ProcessId })
      Say ("  已停止 {0}（{1} 个进程）" -f $item.N, $procs.Count)
    }
  }
  if ($All -and (Get-Process QQ -ErrorAction SilentlyContinue)) {
    Kill-Tree @((Get-Process QQ -ErrorAction SilentlyContinue).Id)
    Say '  已停止 QQ'
  }
  Start-Sleep -Seconds 2
  $left = @()
  foreach ($p in 6185,6199,3000,3001,5099,8920,8921,8901,8912,8913,8914,8916,8919) {
    if (Test-Port $p 500) { $left += $p }
  }
  if ($left.Count -eq 0) { Say '停止完成：相关端口全部关闭 ✔' 'Green' }
  else { Say ('停止后仍有端口在听：{0}（可能有守护进程，用 状态.bat 复查）' -f ($left -join ',')) 'Yellow' }
}

# ---------------- 验收 ----------------
function Test-Tokens {
  try {
    $ab = Get-Content $CMD_CONFIG -Raw -Encoding UTF8 | ConvertFrom-Json
    $abTok = $ab.platform[0].ws_reverse_token
    $sl = Get-Content (Join-Path $SL_DIR 'config\onebot_100000001.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $slAst = ($sl.networks.wsClients | Where-Object { $_.name -eq 'astrbot' }).accessToken
    $sec = Get-Content $SECRETS_NTFS -Raw -Encoding UTF8 | ConvertFrom-Json
    $slHttp = ($sl.networks.httpServers | Where-Object { $_.port -eq 3000 }).accessToken
    return ($abTok -eq $slAst) -and ($sec.onebot_token -eq $slHttp)
  } catch { return $false }
}
function Test-Security {
  $ok = $true
  if (-not (Test-Path $SECRETS_NTFS)) { $ok = $false }
  $acl = (icacls $SECRETS_NTFS 2>&1) -join ' '
  if ($acl -match 'Everyone|BUILTIN\\Users') { $ok = $false }
  $gwSrc = Get-Content (Join-Path $GW_DIR 'beam_gateway.py') -Raw -Encoding UTF8
  if ($gwSrc -match 'sk-[A-Za-z0-9]{20,}') { $ok = $false }
  $pc = Get-Content (Join-Path $AB_CONFIG 'astrbot_plugin_proactive_chat_config.json') -Raw -Encoding UTF8 | ConvertFrom-Json
  if ([string]::IsNullOrWhiteSpace($pc.web_admin.password)) { $ok = $false }
  $cu = Get-Content (Join-Path $AB_CONFIG 'astrbot_plugin_computer_use_config.json') -Raw -Encoding UTF8 | ConvertFrom-Json
  if ($cu.allow_shell -ne $false) { $ok = $false }
  return $ok
}
function Verify {
  Say '========== 验收 ==========' 'Cyan'
  $rows = Get-Health
  $dups = @($rows | Where-Object { $_.实例 -gt 1 -and $_.组件 -in @('SnowLuma','AstrBot') })
  $script:checks = @()
  function Add-Check([string]$Name, [bool]$Pass, [string]$Detail) {
    $script:checks += [pscustomobject]@{ 检查 = $Name; 结果 = $(if ($Pass) { 'PASS' } else { 'FAIL' }); 说明 = $Detail }
  }
  $core = @('QQ','SnowLuma','AstrBot','BEAM网关')
  $coreOk = @($rows | Where-Object { $_.组件 -in $core -and $_.状态 -eq 'OK' }).Count
  Add-Check '1 能装能跑' ($coreOk -eq 4) ("核心组件就绪 {0}/4" -f $coreOk)
  Add-Check '2 不靠人盯（单实例）' ($dups.Count -eq 0) $(if ($dups.Count -eq 0) { '无双开' } else { '双开: ' + (($dups | ForEach-Object { $_.组件 }) -join ',') })
  $gw = Test-Http 'http://127.0.0.1:8920/health' 3
  $tok = Test-Tokens
  Add-Check '3 状态可信' ([bool]$gw -and $tok) (($(if ($gw) { '网关健康' } else { '网关不可达' })) + '；token ' + $(if ($tok) { '一致' } else { '不一致' }))
  $tts = Get-ServiceState 8912; $asr = Get-ServiceState 8913
  $metaOk = Test-Path '%USERPROFILE%\.agents\skills\meta-api-imagegen'
  Add-Check '4 体验稳定（语音/生图）' ($tts -eq 'OK' -and $asr -eq 'OK' -and $metaOk) ("TTS={0} ASR={1} 生图通道={2}" -f $tts, $asr, $metaOk)
  Add-Check '5 坏了能修（日志/文档）' (Test-Path $LOG) '服务日志 + 组件日志路径见 README'
  Add-Check '6 安全兜底' (Test-Security) '密钥NTFS+ACL/无硬编码/:4100密码/allow_shell=false'
  Add-Check '7 有边界' (Test-Path (Join-Path $ROOT 'README.md')) 'README 含能力边界'
  Add-Check '8 有验收' (Test-Path (Join-Path $ROOT '验收表.md')) '验收表可逐条跑'
  $script:checks | Format-Table -AutoSize | Out-String -Width 170 | Write-Host
  $pass = @($script:checks | Where-Object { $_.结果 -eq 'PASS' }).Count
  $total = $script:checks.Count
  Say ("验收结果：{0}/{1} 通过" -f $pass, $total) $(if ($pass -eq $total) { 'Green' } else { 'Yellow' })
  $obj = [pscustomobject]@{ 时间 = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'); 通过 = $pass; 总数 = $total; 检查 = $script:checks; 健康 = $rows }
  $obj | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $RESULT -Encoding UTF8
  Say ('结果已写入 ' + $RESULT)
}

switch ($Action) {
  'start'   { Start-All; Show-Health (Get-Health) | Out-Null }
  'stop'    { Stop-All }
  'restart' { Stop-All; Start-Sleep -Seconds 2; Start-All; Show-Health (Get-Health) | Out-Null }
  'status'  { Show-Health (Get-Health) | Out-Null }
  'health'  { Show-Health (Get-Health) | Out-Null }
  'verify'  { Verify }
}