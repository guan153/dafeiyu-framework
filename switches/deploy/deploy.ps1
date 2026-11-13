# -*- coding: utf-8 -*-
<#
.SYNOPSIS
    鲸鱼少女 v3 内核部署脚本（幂等、可回滚）。

.DESCRIPTION
    把 qqbot_v3 里的 whale_core + 两个插件同步到 AstrBot，然后重启 AstrBot 并校验。
    每一步都先备份；重启失败会自动回滚到上一版并再起一次。

.PARAMETER Action
    deploy   只同步文件（不重启）
    restart  只重启 AstrBot
    verify   只做校验（端口 + 插件加载 + 账本）
    rollback 回滚到最近一次备份
#>
param(
    [ValidateSet('deploy', 'restart', 'verify', 'rollback', 'all')]
    [string]$Action = 'all',
    # 重启/回滚必须由主人明确同意；不加这个开关就只同步文件，绝不杀进程。
    [switch]$ConfirmRestart
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# ── 路径 ────────────────────────────────────────────────────
$Src      = '%PROJECT_HOME%\qqbot_v3'
$AstrBot  = '%ASTRBOT_HOME%'
$Data     = Join-Path $AstrBot 'data'
$Plugins  = Join-Path $Data 'plugins'
$Backup   = Join-Path $AstrBot '_whale_v3_backup'
$PyExe    = Join-Path $AstrBot '.venv\Scripts\python.exe'
$Stamp    = Get-Date -Format 'yyyyMMdd-HHmmss'
$BackupDir = Join-Path $Backup $Stamp

$CorePkg     = Join-Path $Src 'whale_core'
$CorePlugin  = Join-Path $Src 'astrbot_plugin_whale_core'
$IntroPlugin = Join-Path $Src 'astrbot_plugin_whale_introspect'
$FacePlugin  = Join-Path $Src 'astrbot_plugin_whale_face'

function Say([string]$m, [string]$color = 'Gray') {
    Write-Host $m -ForegroundColor $color
}

function Test-Port([string]$H, [int]$Port, [int]$TimeoutMs = 1500) {
    $c = New-Object System.Net.Sockets.TcpClient
    try {
        $ar = $c.BeginConnect($H, $Port, $null, $null)
        if (-not $ar.AsyncWaitHandle.WaitOne($TimeoutMs)) { return $false }
        $c.EndConnect($ar)
        return $true
    } catch { return $false } finally { $c.Close() }
}

function Wait-Port([string]$H, [int]$Port, [int]$Seconds) {
    $sw = [Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt $Seconds) {
        if (Test-Port $H $Port 800) { return $true }
        Start-Sleep -Milliseconds 700
    }
    return $false
}

function Get-AstrBotPid {
    # 不依赖 CIM（沙箱里可能被禁），走 netstat 找 6185 的监听进程
    $lines = & netstat -ano 2>$null | Select-String -Pattern ':6185\s+.*LISTENING'
    foreach ($line in $lines) {
        $parts = ($line.Line -split '\s+') | Where-Object { $_ }
        if ($parts.Count -ge 5) {
            $candidate = [int]$parts[-1]
            if ($candidate -gt 4) { return $candidate }
        }
    }
    return 0
}

function Backup-Target([string]$Path, [string]$Label) {
    if (Test-Path $Path) {
        $dest = Join-Path $BackupDir $Label
        New-Item -ItemType Directory -Force -Path (Split-Path $dest -Parent) | Out-Null
        Copy-Item -Path $Path -Destination $dest -Recurse -Force
        Say ("  备份 $Label -> $dest") 'DarkGray'
    }
}

function Sync-Files {
    Say '== 同步内核与插件 ==' 'Cyan'
    New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
    New-Item -ItemType Directory -Force -Path $Data, $Plugins | Out-Null

    Backup-Target (Join-Path $Data 'whale_core') 'data_whale_core'
    Backup-Target (Join-Path $Plugins 'astrbot_plugin_whale_core') 'plugin_whale_core'
    Backup-Target (Join-Path $Plugins 'astrbot_plugin_whale_introspect') 'plugin_whale_introspect'
    Backup-Target (Join-Path $Plugins 'astrbot_plugin_whale_sing') 'plugin_whale_sing'
    Backup-Target (Join-Path $Plugins 'astrbot_plugin_speech_style') 'plugin_speech_style'
    Backup-Target (Join-Path $Plugins 'astrbot_plugin_whale_face') 'plugin_whale_face'

    # 1) 内核库
    $destCore = Join-Path $Data 'whale_core'
    if (Test-Path $destCore) { Remove-Item $destCore -Recurse -Force }
    Copy-Item -Path $CorePkg -Destination $destCore -Recurse -Force
    Get-ChildItem $destCore -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force
    Say ("  内核 -> $destCore") 'Green'

    # 2) 插件
    foreach ($pair in @(
            @($CorePlugin, 'astrbot_plugin_whale_core'),
            @($IntroPlugin, 'astrbot_plugin_whale_introspect'),
            @((Join-Path $Src 'astrbot_plugin_whale_sing'), 'astrbot_plugin_whale_sing'),
            @((Join-Path $Src 'astrbot_plugin_speech_style'), 'astrbot_plugin_speech_style'),
            @((Join-Path $Src 'astrbot_plugin_whale_will'), 'astrbot_plugin_whale_will'),
            @($FacePlugin, 'astrbot_plugin_whale_face'))) {
        $src = $pair[0]; $name = $pair[1]
        $dest = Join-Path $Plugins $name
        if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
        Copy-Item -Path $src -Destination $dest -Recurse -Force
        Get-ChildItem $dest -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force
        Say ("  插件 -> $dest") 'Green'
    }

    # 3) 内核配置（已存在就不覆盖，保护手工调过的值）
    $cfg = Join-Path $Data 'plugin_data\whale_core\core_config.json'
    if (-not (Test-Path $cfg)) {
        New-Item -ItemType Directory -Force -Path (Split-Path $cfg -Parent) | Out-Null
        $default = [ordered]@{
            core       = [ordered]@{ enabled = $true; start_outbound = $true }
            outbound   = [ordered]@{ min_interval_sec = 90; per_source_interval = 300; daily_cap = 40; quiet_hours = @(0, 7); dedupe_window_sec = 1800; max_attempts = 3 }
            introspect = [ordered]@{ enabled = $true; interval_min = 15; log_window_min = 30; inject_enable = $true; inject_budget = 320; inject_top = 4; auto_propose = $true; propose_min_score = 18; auto_apply_low_risk = $false; owner_umo = '' }
        }
        $json = $default | ConvertTo-Json -Depth 6
        [IO.File]::WriteAllText($cfg, $json, [Text.UTF8Encoding]::new($false))
        Say ("  配置 -> $cfg（首次生成）") 'Yellow'
    } else {
        Say ("  配置已存在，保留：$cfg") 'Yellow'
    }
}

function Restart-AstrBot {
    Say '== 重启 AstrBot ==' 'Cyan'
    $botPid = Get-AstrBotPid
    if ($botPid -gt 0) {
        Say ("  停止旧进程 PID $botPid")
        & taskkill /F /T /PID $botPid 2>&1 | Out-Null
        Start-Sleep -Seconds 2
    } else {
        Say '  没有检测到监听 6185 的进程，直接启动' 'Yellow'
    }

    $cmd = 'cd /d "' + $AstrBot + '" && "' + $PyExe + '" main.py --webui-dir "dist-webui\dist" >> runtime.log 2>&1'
    Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', $cmd -WindowStyle Hidden | Out-Null
    if (Wait-Port '127.0.0.1' 6185 150) {
        Say '  AstrBot 已就绪（6185 通）' 'Green'
        return $true
    }
    Say '  AstrBot 150 秒内没起来' 'Red'
    return $false
}

function Verify {
    Say '== 校验 ==' 'Cyan'
    $ok = $true
    foreach ($p in @(6185, 6199, 3000)) {
        $up = Test-Port '127.0.0.1' $p
        Say ("  端口 {0}: {1}" -f $p, $(if ($up) { 'UP' } else { 'DOWN' })) $(if ($up) { 'Green' } else { 'Red' })
        if (-not $up) { $ok = $false }
    }

    $logFile = Join-Path $AstrBot 'runtime.log'
    if (Test-Path $logFile) {
        $tail = Get-Content $logFile -Tail 400 -Encoding UTF8
        foreach ($name in @('whale_core', 'whale_introspect', 'whale_face')) {
            $hit = $tail | Select-String -Pattern $name -SimpleMatch | Select-Object -First 1
            Say ("  日志提及 {0}: {1}" -f $name, $(if ($hit) { 'YES' } else { 'NO' })) $(if ($hit) { 'Green' } else { 'Yellow' })
        }
        # 只认「真的插件加载失败」。已知噪声：loguru 往 GBK 控制台写 emoji 会抛
        # UnicodeEncodeError + Traceback，这是日志本身的问题，不是插件坏了——不能让它
        # 在每次验收里都报红，否则真正的加载失败会被淹没。
        $errLines = @()
        for ($i = 0; $i -lt $tail.Count; $i++) {
            $line = $tail[$i]
            if ($line -notmatch 'Failed to load plugin|插件加载失败|Traceback') { continue }
            $window = ($tail | Select-Object -Skip ([Math]::Max(0, $i - 2)) -First 6) -join "`n"
            if ($window -match 'Logging error in Loguru Handler|UnicodeEncodeError: .gbk') { continue }
            $errLines += $line
        }
        if ($errLines.Count -gt 0) {
            Say '  日志里有加载错误：' 'Red'
            $errLines | Select-Object -First 3 | ForEach-Object { Say ('    ' + $_.Trim()) 'DarkRed' }
            $ok = $false
        }
    }

    $ledger = Join-Path $Data 'plugin_data\whale_core\weakness.sqlite3'
    Say ("  账本文件: {0}" -f $(if (Test-Path $ledger) { 'EXISTS' } else { 'MISSING' })) 'Gray'
    return $ok
}

function Rollback {
    Say '== 回滚 ==' 'Cyan'
    if (-not (Test-Path $Backup)) { Say '  没有备份可回滚' 'Red'; return }
    $latest = Get-ChildItem $Backup -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $latest) { Say '  没有备份目录' 'Red'; return }
    Say ("  使用备份：$($latest.FullName)")

    $map = @{
        'data_whale_core'          = (Join-Path $Data 'whale_core')
        'plugin_whale_core'        = (Join-Path $Plugins 'astrbot_plugin_whale_core')
        'plugin_whale_introspect'  = (Join-Path $Plugins 'astrbot_plugin_whale_introspect')
        'plugin_whale_sing'        = (Join-Path $Plugins 'astrbot_plugin_whale_sing')
        'plugin_speech_style'       = (Join-Path $Plugins 'astrbot_plugin_speech_style')
    }
    foreach ($k in $map.Keys) {
        $from = Join-Path $latest.FullName $k
        $to = $map[$k]
        if (Test-Path $from) {
            if (Test-Path $to) { Remove-Item $to -Recurse -Force }
            Copy-Item $from $to -Recurse -Force
            Say ("  恢复 $k -> $to") 'Green'
        } elseif (Test-Path $to) {
            # 备份里没有 = 上一版不存在（这是首次安装），删掉新增文件
            Remove-Item $to -Recurse -Force
            Say ("  删除新增的 $to") 'Yellow'
        }
    }
    Restart-AstrBot | Out-Null
    Verify | Out-Null
}

# ── 主流程 ──────────────────────────────────────────────────
Say ("鲸·内核 v3 部署  action={0}  {1}" -f $Action, (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')) 'White'

switch ($Action) {
    'deploy'   { Sync-Files }
    'restart'  {
        # 铁律（2026-11-08 主人明令）：未经主人同意，不许停 AstrBot。
        # 重启 = kill + 起，必然有一段时间收不到消息；消息丢了不会重放。
        if (-not $ConfirmRestart) {
            Say '拒绝重启：需要主人明确同意。加 -ConfirmRestart 才会执行。' 'Red'
            Say '（只同步文件不重启：用 -Action deploy）' 'Yellow'
            exit 5
        }
        Restart-AstrBot | Out-Null
    }
    'verify'   { if (-not (Verify)) { exit 2 } }
    'rollback' {
        if (-not $ConfirmRestart) {
            Say '拒绝回滚：回滚会重启 AstrBot，需要主人明确同意（加 -ConfirmRestart）。' 'Red'
            exit 5
        }
        Rollback
    }
    'all' {
        Sync-Files
        if (-not $ConfirmRestart) {
            Say '' 'Gray'
            Say '⚠️ 已同步文件，但**没有重启**（未经主人同意不许停 AstrBot）。' 'Yellow'
            Say '   代码要在机器人下次自然重启后生效；需要立刻生效请主人同意后加 -ConfirmRestart。' 'Yellow'
            exit 0
        }
        if (Restart-AstrBot) {
            if (-not (Verify)) {
                Say '校验失败，自动回滚...' 'Red'
                Rollback
                exit 3
            }
        } else {
            Say '重启失败，自动回滚...' 'Red'
            Rollback
            exit 4
        }
    }
}
Say '完成。' 'Green'
