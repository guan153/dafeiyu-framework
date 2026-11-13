# ============================================================
#  大肥鱼 / 鲸鱼少女 · 聊天-only 启动器   (2026-11-13)
#
#  目的：只把「聊天」这一层拉起来，绝不动 GPU。
#
#    ✔ 会拉：QQ 客户端 → SnowLuma(5099/3000/3001) → BEAM 网关(8920) → AstrBot(6185/6199)
#    ✘ 不拉：TTS(8912) / ASR(8913) / QwenEmo(8914) / 声音分类(8916) / 耳返(8919)
#            / ComfyUI(8188) / 能力调度器(8921)
#    ✘ 不调用：_v1交付\框架\服务.ps1 -Action start
#              audition\gateway\start_all.ps1
#              G:\ai\feiyu_voice\start_voice_stack.ps1
#    ✘ 不杀任何进程：已有进程但端口没通 → 只告警 + 等一等，绝不强杀
#    ✘ 不写任何配置：toggles.json / capability_state.json 只读不写
#
#  幂等：每一项先查端口，已开就跳过。
#
#  用法：
#    powershell -NoProfile -ExecutionPolicy Bypass -File start-chat-only.ps1 -Action start
#    powershell -NoProfile -ExecutionPolicy Bypass -File start-chat-only.ps1 -Action status
#
#  -Action status 是纯只读体检：不启动、不结束任何东西，可随时跑。
# ============================================================
param(
  [ValidateSet('start','status')]
  [string]$Action = 'start'
)

$ErrorActionPreference = 'Continue'

# ---------------- 路径（全部已核实存在） ----------------
$QQ_EXE     = 'D:\Program Files\Tencent\QQNT\QQ.exe'
$NODE_EXE   = 'D:\Program Files\nodejs\node.exe'
$SL_DIR     = '%QBOT_HOME%\qbot\SnowLuma'
$AB_DIR     = '%ASTRBOT_HOME%'
# AstrBot 解释器：优先用它自己的 venv（与线上实际父进程一致），退回旧 venv
$AB_PY_CAND = @(
  '%ASTRBOT_HOME%\.venv\Scripts\python.exe',
  'D:\astrbot-venv\Scripts\python.exe'
)
$GW_DIR     = '%PROJECT_HOME%\audition\gateway'
$GW_SCRIPT  = Join-Path $GW_DIR 'beam_gateway.py'
$GW_PY_CAND = @(
  'D:\astrbot-venv\Scripts\python.exe',
  '%USERPROFILE%\AppData\Roaming\uv\python\cpython-3.12-windows-x86_64-none\python.exe'
)
$GW_OUT     = Join-Path $GW_DIR 'gw.out.log'
$GW_ERR     = Join-Path $GW_DIR 'gw.err.log'
$TOGGLES    = Join-Path $GW_DIR 'toggles.json'
$CAP_STATE  = Join-Path $GW_DIR 'capability_state.json'
$LOG_DIR    = Join-Path $PSScriptRoot 'logs'
$LOG        = Join-Path $LOG_DIR ('chat-only-' + (Get-Date -Format 'yyyyMMdd') + '.log')

try { New-Item -ItemType Directory -Path $LOG_DIR -Force | Out-Null } catch { }

# ---------------- 期望矩阵 ----------------
# 必须 UP（聊天四件套）
$EXPECT_UP = @(
  @{ P = 5099; N = 'SnowLuma WebUI';   Need = $true  },
  @{ P = 3000; N = 'OneBot HTTP';      Need = $false },
  @{ P = 6185; N = 'AstrBot WebUI';    Need = $true  },
  @{ P = 6199; N = 'AstrBot 反向WS';   Need = $true  },
  @{ P = 8920; N = 'BEAM 网关';        Need = $true  }
)
# 必须 DOWN（GPU / 语音 / 能力开关）
$EXPECT_DOWN = @(
  @{ P = 8912; N = 'IndexTTS（语音 GPU）' },
  @{ P = 8188; N = 'ComfyUI（生图 GPU）' },
  @{ P = 8913; N = 'FunASR（听写）' },
  @{ P = 8921; N = '能力调度器（GPU 总闸）' }
)
# 建议 DOWN 但不致命
$SHOULD_DOWN = @(
  @{ P = 8914; N = 'QwenEmo（情绪）' },
  @{ P = 8916; N = '声音分类' },
  @{ P = 8919; N = '耳返' }
)

# ---------------- 基础工具 ----------------
function Say([string]$m, [string]$c = 'Gray') {
  $line = '[' + (Get-Date -Format 'HH:mm:ss') + '] ' + $m
  Write-Host $line -ForegroundColor $c
  try { Add-Content -LiteralPath $LOG -Value $line -Encoding UTF8 } catch { }
}

function Test-Tcp([string]$HostName, [int]$Port, [int]$TimeoutMs = 1000) {
  $c = New-Object System.Net.Sockets.TcpClient
  try {
    $ar = $c.BeginConnect($HostName, $Port, $null, $null)
    if (-not $ar.AsyncWaitHandle.WaitOne($TimeoutMs)) { return $false }
    $c.EndConnect($ar)
    return $true
  } catch { return $false } finally { $c.Close() }
}

function Test-Http([string]$Url, [int]$Sec = 3) {
  try {
    $r = Invoke-WebRequest -Uri $Url -TimeoutSec $Sec -UseBasicParsing -ErrorAction Stop
    return ($r.StatusCode -eq 200)
  } catch { return $false }
}

function Wait-Tcp([int]$Port, [int]$Sec, [string]$What) {
  $sw = [Diagnostics.Stopwatch]::StartNew()
  while ($sw.Elapsed.TotalSeconds -lt $Sec) {
    if (Test-Tcp '127.0.0.1' $Port 800) {
      Say ("  OK   {0} 就绪（{1}s）" -f $What, [int]$sw.Elapsed.TotalSeconds) 'Green'
      return $true
    }
    Start-Sleep -Milliseconds 700
  }
  Say ("  FAIL {0} 超时（{1}s）" -f $What, $Sec) 'Red'
  return $false
}

# 按命令行特征找进程（只读，不杀）
function Get-Procs([string]$Pattern) {
  @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
      Where-Object {
        $_.Name -in @('python.exe', 'node.exe', 'cmd.exe', 'powershell.exe') -and
        $_.CommandLine -match $Pattern
      })
}

function First-Existing([string[]]$Candidates) {
  foreach ($p in $Candidates) { if (Test-Path -LiteralPath $p) { return $p } }
  return ''
}

# ---------------- 只读体检 ----------------
function Show-Guards {
  Say '---- 前置检查（只读，不改任何东西）----' 'Cyan'

  if (Test-Tcp '127.0.0.1' 8921 500) {
    Say '  WARN 8921 能力调度器在线：它是唯一能在插件请求下切换 GPU 服务的总闸。' 'Yellow'
    Say '       本启动器不会启动它，也不会替你杀掉它（那是既有进程）。' 'Yellow'
  } else {
    Say '  OK   8921 未运行 → GPU 服务没有任何「合法拉起入口」' 'Green'
  }

  $img = $null; $voi = $null
  try {
    $j = Get-Content -LiteralPath $TOGGLES -Raw -Encoding UTF8 -ErrorAction Stop | ConvertFrom-Json
    $img = [bool]$j.image
    $voi = [bool]$j.voice
  } catch {
    Say '  WARN 读不到 toggles.json！插件里 model_switch 读失败会 fail-open（默认当「都开」）。' 'Yellow'
  }
  if ($null -ne $img) {
    if ($img -or $voi) {
      Say ("  WARN toggles: image={0} voice={1} —— 开关没关！有 8921 时插件可能拉起 GPU 服务。" -f $img, $voi) 'Yellow'
    } else {
      Say '  OK   toggles: image=false voice=false（双关）' 'Green'
    }
  }

  if (Test-Tcp '127.0.0.1' 8912 400) { Say '  WARN 8912 TTS 已在运行（不是本启动器拉的）；它占 5.6GB，请确认是不是你要的。' 'Yellow' }
  if (Test-Tcp '127.0.0.1' 8188 400) { Say '  WARN 8188 ComfyUI 已在运行（不是本启动器拉的）；它和 TTS 硬互斥。' 'Yellow' }
}

function Get-VramLine {
  try {
    $raw = (& nvidia-smi --query-gpu=memory.used,memory.free --format=csv,noheader,nounits 2>$null)
    if ($raw) { return ('GPU 显存 used/free = ' + ($raw -join ' / ') + ' MiB（桌面基线约 2500–2700）') }
  } catch { }
  return 'GPU 显存：nvidia-smi 不可用'
}

function Show-Matrix {
  Say '---- 端口体检 ----' 'Cyan'
  $fail = 0
  foreach ($e in $EXPECT_UP) {
    $up = Test-Tcp '127.0.0.1' $e.P 700
    if ($up) { Say ("  OK   {0,-5} {1}" -f $e.P, $e.N) 'Green' }
    elseif ($e.Need) { Say ("  FAIL {0,-5} {1} 未监听" -f $e.P, $e.N) 'Red'; $fail++ }
    else { Say ("  warn {0,-5} {1} 未监听（非必需）" -f $e.P, $e.N) 'Yellow' }
  }
  foreach ($e in $EXPECT_DOWN) {
    $up = Test-Tcp '127.0.0.1' $e.P 700
    if (-not $up) { Say ("  OK   {0,-5} {1}  = DOWN" -f $e.P, $e.N) 'Green' }
    else { Say ("  FAIL {0,-5} {1}  = UP！违反了聊天-only" -f $e.P, $e.N) 'Red'; $fail++ }
  }
  foreach ($e in $SHOULD_DOWN) {
    if (Test-Tcp '127.0.0.1' $e.P 500) { Say ("  warn {0,-5} {1} = UP（CPU 服务，不占显存，但本启动器没拉它）" -f $e.P, $e.N) 'Yellow' }
  }
  Say ('  ' + (Get-VramLine))
  return $fail
}

# ---------------- 四段启动 ----------------
function Start-QQ {
  if (Get-Process QQ -ErrorAction SilentlyContinue) { Say '  QQ 已在线（跳过）' 'Green'; return $true }
  if (-not (Test-Path -LiteralPath $QQ_EXE)) { Say ('  FAIL 找不到 QQ.exe: ' + $QQ_EXE) 'Red'; return $false }
  Say '  ... 拉起 QQ'
  Start-Process -FilePath $QQ_EXE | Out-Null
  for ($i = 0; $i -lt 15; $i++) {
    Start-Sleep -Seconds 2
    if (Get-Process QQ -ErrorAction SilentlyContinue) { Say '  OK   QQ 已启动' 'Green'; return $true }
  }
  Say '  FAIL QQ 30s 内未起来（可能要手动登录）' 'Red'
  return $false
}

function Start-SnowLuma {
  if (Test-Tcp '127.0.0.1' 5099 800) { Say '  SnowLuma 已在线（5099 UP，跳过）' 'Green'; return $true }
  $procs = Get-Procs 'SnowLuma[\\/]index\.mjs'
  if ($procs.Count -gt 0) {
    Say ('  WARN 已有 {0} 个 SnowLuma 进程但 5099 未通 → 不重复拉起、不强杀，等 20s' -f $procs.Count) 'Yellow'
    Wait-Tcp 5099 20 'SnowLuma' | Out-Null
    return (Test-Tcp '127.0.0.1' 5099 800)
  }
  if (-not (Test-Path -LiteralPath (Join-Path $SL_DIR 'index.mjs'))) { Say '  FAIL 找不到 SnowLuma\index.mjs' 'Red'; return $false }
  Say '  ... 拉起 SnowLuma'
  $env:SNOWLUMA_ACCEPT_EULA = '1'
  $env:SNOWLUMA_ACCEPT_PRIVACY = '1'
  $env:SNOWLUMA_HOOK_AUTOLOAD = '1'
  $env:SNOWLUMA_LOG_LEVEL = 'info'
  Start-Process -FilePath $NODE_EXE -ArgumentList 'index.mjs' -WorkingDirectory $SL_DIR -WindowStyle Hidden | Out-Null
  return (Wait-Tcp 5099 60 'SnowLuma')
}

function Start-BeamGateway {
  if (Test-Http 'http://127.0.0.1:8920/health' 3) { Say '  BEAM 网关已在线（8920 /health OK，跳过）' 'Green'; return $true }
  if (Test-Tcp '127.0.0.1' 8920 800) {
    Say '  WARN 8920 端口在但 /health 未通 → 不重启、不强杀，等 20s' 'Yellow'
    Wait-Tcp 8920 20 'BEAM 网关' | Out-Null
    return (Test-Http 'http://127.0.0.1:8920/health' 3)
  }
  $procs = Get-Procs 'beam_gateway\.py'
  if ($procs.Count -gt 0) {
    Say ('  WARN 已有 {0} 个 beam_gateway 进程但 8920 未通 → 不重复拉起、不强杀，等 20s' -f $procs.Count) 'Yellow'
    Wait-Tcp 8920 20 'BEAM 网关' | Out-Null
    return (Test-Http 'http://127.0.0.1:8920/health' 3)
  }
  $gwPy = First-Existing $GW_PY_CAND
  if (-not $gwPy) { Say '  FAIL 找不到网关用 python（D:\astrbot-venv 与 uv cpython-3.12 都不在）' 'Red'; return $false }
  if (-not (Test-Path -LiteralPath $GW_SCRIPT)) { Say '  FAIL 找不到 beam_gateway.py' 'Red'; return $false }
  Say ('  ... 拉起 BEAM 网关（8920，纯 CPU；解释器 ' + $gwPy + '）')
  # 与 watchdog.py::start() 完全同参：同 python、同 cwd、同两个日志文件
  Start-Process -FilePath $gwPy -ArgumentList $GW_SCRIPT -WorkingDirectory $GW_DIR -WindowStyle Hidden -RedirectStandardOutput $GW_OUT -RedirectStandardError $GW_ERR | Out-Null
  if (-not (Wait-Tcp 8920 40 'BEAM 网关端口')) { return $false }
  for ($i = 0; $i -lt 10; $i++) {
    if (Test-Http 'http://127.0.0.1:8920/health' 3) { Say '  OK   BEAM 网关 /health OK' 'Green'; return $true }
    Start-Sleep -Seconds 2
  }
  Say '  WARN 8920 端口通了但 /health 还没 200，稍后看 gw.err.log' 'Yellow'
  return $true
}

function Start-AstrBot {
  if (Test-Tcp '127.0.0.1' 6185 800) { Say '  AstrBot 已在线（6185 UP，跳过）' 'Green'; return $true }
  $procs = Get-Procs 'main\.py.*webui-dir'
  if ($procs.Count -gt 0) {
    Say ('  WARN 已有 {0} 个 AstrBot 进程但 6185 未通 → 不重复拉起、不强杀，等 120s' -f $procs.Count) 'Yellow'
    $a = Wait-Tcp 6185 120 'AstrBot WebUI'
    if ($a) { Wait-Tcp 6199 60 'AstrBot 反向WS' | Out-Null }
    return $a
  }
  $abPy = First-Existing $AB_PY_CAND
  if (-not $abPy) { Say '  FAIL 找不到 AstrBot 的 python（venv 都不在）' 'Red'; return $false }
  if (-not (Test-Path -LiteralPath (Join-Path $AB_DIR 'main.py'))) { Say '  FAIL 找不到 AstrBot\main.py' 'Red'; return $false }
  Say ('  ... 拉起 AstrBot（解释器 ' + $abPy + '，冷启动约 30–90s）')
  $env:ASTRBOT_ROOT = $AB_DIR
  $env:PYTHONUTF8 = '1'
  $env:PYTHONIOENCODING = 'utf-8'
  $env:OMP_NUM_THREADS = '1'
  $cmd = 'cd /d "' + $AB_DIR + '" && "' + $abPy + '" main.py --webui-dir "dist-webui\dist" >> runtime.log 2>&1'
  Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', $cmd -WindowStyle Hidden | Out-Null
  $ok = Wait-Tcp 6185 120 'AstrBot WebUI'
  if ($ok) { Wait-Tcp 6199 60 'AstrBot 反向WS' | Out-Null }
  return $ok
}

# ======================== 主流程 ========================
Say ('==== 聊天-only 启动器  Action=' + $Action + ' ====') 'Cyan'

if ($Action -eq 'status') {
  Show-Guards
  $f = Show-Matrix
  Say ('==== 体检结束：' + $(if ($f -eq 0) { '全部符合聊天-only' } else { ($f.ToString() + ' 项不符') }) + ' ====') $(if ($f -eq 0) { 'Green' } else { 'Red' })
  exit $(if ($f -eq 0) { 0 } else { 1 })
}

Show-Guards

Say '---- 启动（已开的会跳过）----' 'Cyan'
$r1 = Start-QQ
$r2 = Start-SnowLuma
$r3 = Start-BeamGateway
$r4 = Start-AstrBot

$fail = Show-Matrix

Say '---- 结果 ----' 'Cyan'
if ($r1 -and $r2 -and $r3 -and $r4 -and $fail -eq 0) {
  Say '聊天-only 启动完成：四件套齐、GPU 服务全 DOWN。' 'Green'
  Say '提醒：本脚本不拉 TTS/ASR/ComfyUI，她的语音和画图要单独开。' 'Gray'
  exit 0
} else {
  Say '启动未完全成功，逐项看上面的 FAIL / WARN。' 'Red'
  Say ('单步：QQ={0} SnowLuma={1} 网关={2} AstrBot={3} 端口不符={4}' -f $r1, $r2, $r3, $r4, $fail) 'Red'
  exit 1
}
