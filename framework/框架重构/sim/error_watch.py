# -*- coding: utf-8 -*-
"""error_watch v3（修正 v2 的误报自愈 + 增加 --replay 历史验证）

v2 缺陷（audit-infra 实锤）：
- SEND_FAIL_PATTERNS 含裸 "发送失败:"，而 whale_bili 的 `[鲸眼] 发送失败:` 是正常陪看时的高频 WARN
  （实测每 50 秒一条，72KB 尾部有 37 条）→ 每 2 分钟巡检都 ≥2 条 → 周期性重启 AstrBot（一天最多 48 次）。
- 首次部署把游标置到 EOF，历史故障样本从未被真实回放验证。

v3：
- 只把核心链路断链特征视为故障：ApiNotAvailable / Failed to send the message chain
- 插件级「发送失败」只计数记录，绝不触发自愈
- 增加 --replay N：从各日志尾部 N 行全量扫描并打印命中，用于部署验证
- 连接抖动（2 分钟内 ≥2 次「适配器已连接」）升级为告警记录（不重启）
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = r"%PROJECT_HOME%\框架重构"
def _logs():
    """日志注册表：glob 所有 runtime*.log。今天盲区的本质就是"换启动方式→日志换文件"，
    写死文件名下次 runtime-voice3.out.log 又会失明。"""
    import glob
    base = r"%ASTRBOT_HOME%"
    out = []
    for p in [os.path.join(base, "runtime.log")] + sorted(glob.glob(os.path.join(base, "runtime*.log"))):
        if p not in out:
            out.append(p)
    return out


LOGS = _logs()
STATE = os.path.join(ROOT, "_alert_state.json")
ALERTS = os.path.join(ROOT, "_alerts.jsonl")
BROKEN = os.path.join(ROOT, "_send_broken.json")
CONNECT_ALERT = os.path.join(ROOT, "_connect_jitter.log")
WATCH_LOG = r"%PROJECT_HOME%\logs\error_watch.log"
FIX_PS1 = r"%PROJECT_HOME%\tools\fix_send_channel.ps1"
FIX_COOLDOWN = 1800

# ★ 只有这两类才是"收到消息但不回复"的直接特征（核心 respond/aiocqhttp 层）
CORE_BROKEN = (
    "ApiNotAvailable",
    "Failed to send the message chain",
)
# 插件级发送失败：只计数，不触发自愈（鲸眼/表情包等运行时抖动）
PLUGIN_SEND_WARN = ("发送失败:", "分享发送失败")

SLOTS = {
    "T": ("timeout", "timed out", "超时", "timeouterror"),
    "V": ("validation", "invalid", "empty text", "not a valid", "valueerror"),
    "U": ("upstream", "httperror", " 502", " 503", "connection", "connectionerror"),
    "P": ("policy", "refused", "blocked", "denied", " 401", " 403"),
    "C": ("traceback", "keyerror", "typeerror", "attributeerror", "indexerror", "syntaxerror"),
    "G": ("guardrail", "八股", "禁表", "不可外发"),
    "L": ("filenotfound", "permissionerror", "oserror", "enospc"),
    "S": ("not ready", "尚未初始化", "状态不一致"),
}
CONNECT_PAT = re.compile(r"适配器已连接")


def _slot(line: str) -> str:
    low = line.lower()
    if "unhandled exception" in low or "error in asgi" in low:
        return "C"
    if "no response returned" in low:
        return "T"
    for s, pats in SLOTS.items():
        for p in pats:
            if p in low:
                return s
    return "OTHER"


def _load(path, default):
    try:
        if os.path.isfile(path):
            return json.loads(open(path, encoding="utf-8").read())
    except (OSError, ValueError):
        pass
    return default


def _wlog(msg: str) -> None:
    try:
        os.makedirs(os.path.dirname(WATCH_LOG), exist_ok=True)
        with open(WATCH_LOG, "a", encoding="utf-8") as fh:
            fh.write(msg + "\n")
    except OSError:
        pass


def _iter_new_lines(path, offset, tail_bytes):
    """返回 (lines, new_offset)；offset 无效时回看 tail_bytes。"""
    try:
        size = os.path.getsize(path)
    except OSError:
        return [], offset
    if offset <= 0 or offset > size:
        offset = max(0, size - tail_bytes)
    try:
        with open(path, "rb") as fh:
            fh.seek(offset)
            chunk = fh.read()
            new_off = fh.tell()
    except OSError:
        return [], offset
    return chunk.decode("utf-8", "replace").splitlines(), new_off


def scan(replay_lines=0):
    """主扫描；replay_lines>0 时忽略游标，直接回看每份日志尾部 N 行。"""
    st = _load(STATE, {})
    logs_state = st.get("logs")
    if not isinstance(logs_state, dict):
        logs_state = {}
        if st.get("offset"):
            logs_state[LOGS[0]] = {"offset": int(st["offset"])}

    errors = core_broken = plugin_warn = connects = 0
    slot_cnt = {}
    samples = []
    connect_times = []

    with open(ALERTS, "a", encoding="utf-8") as af:
        for path in _logs():
            if not os.path.isfile(path):
                continue
            fs = logs_state.get(path) or {}
            if replay_lines > 0:
                lines, new_off = _iter_new_lines(path, 0, 10 ** 12)
                lines = lines[-replay_lines:]
                new_off = os.path.getsize(path)
            else:
                lines, new_off = _iter_new_lines(path, int(fs.get("offset") or 0), 400 * 180)
            logs_state[path] = {"offset": new_off, "size": os.path.getsize(path)}
            for ln in lines:
                s = ln.strip()
                if not s:
                    continue
                if CONNECT_PAT.search(s):
                    connects += 1
                    connect_times.append(s[:80])
                if any(p in s for p in CORE_BROKEN):
                    core_broken += 1
                    samples.append("[核心断链] " + s[:200])
                elif any(p in s for p in PLUGIN_SEND_WARN):
                    plugin_warn += 1
                if "[ERRO" not in s and "[CRIT" not in s:
                    continue
                slot = _slot(s)
                slot_cnt[slot] = slot_cnt.get(slot, 0) + 1
                errors += 1
                clean = re.sub(r"\x1b\[[0-9;]*m", "", s)[:300]
                af.write(json.dumps({
                    "ts": time.time(),
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "slot": slot, "src": os.path.basename(path), "line": clean,
                }, ensure_ascii=False) + "\n")

    state = {
        "logs": logs_state,
        "last_scan": time.time(),
        "last_scan_local": time.strftime("%Y-%m-%d %H:%M:%S"),
        "last_new_alerts": errors,
        "last_core_broken": core_broken,
        "last_plugin_warn": plugin_warn,
        "last_connects": connects,
    }
    if not replay_lines:
        tmp = STATE + ".tmp"
        open(tmp, "w", encoding="utf-8").write(json.dumps(state, ensure_ascii=False, indent=1))
        os.replace(tmp, STATE)

    # 连接抖动记录（不触发自愈，但留痕——今天故障前 12:43 就抖了两次）
    if connects >= 2:
        try:
            with open(CONNECT_ALERT, "a", encoding="utf-8") as fh:
                fh.write("[%s] 2 分钟窗口内 OneBot 连接抖动 %d 次：%s\n" % (
                    state["last_scan_local"], connects, " | ".join(connect_times[:4])))
        except OSError:
            pass

    # 自愈：仅在核心断链时触发（且 30 分钟冷却）；replay 验证模式绝不触发
    fixed = ""
    if not replay_lines and core_broken >= 1:
        bk = _load(BROKEN, {})
        now = time.time()
        last_fix = float(bk.get("last_fix") or 0)
        consecutive = int(bk.get("consecutive") or 0) + 1
        bk.update({"first_seen": bk.get("first_seen") or now, "last_seen": now,
                   "core_broken": core_broken, "consecutive": consecutive,
                   "count": int(bk.get("count") or 0) + 1})
        # 连续两轮巡检（约 4 分钟）都命中才动手：一次瞬态失败会产出 3 行特征，
        # 单轮命中不足以判定通道真的断了（audit 2026-11-04）。
        if consecutive >= 2 and now - last_fix > FIX_COOLDOWN and os.path.isfile(FIX_PS1):
            bk["last_fix"] = now
            bk["consecutive"] = 0
            log_before = 0.0
            fix_log = r"%PROJECT_HOME%\logs\send_channel_fix.log"
            try:
                log_before = os.path.getmtime(fix_log)
            except OSError:
                log_before = 0.0
            try:
                subprocess.Popen(
                    ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", FIX_PS1],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=0x08000000)
                time.sleep(3)
                log_after = 0.0
                try:
                    log_after = os.path.getmtime(fix_log)
                except OSError:
                    log_after = 0.0
                if log_after > log_before:
                    fixed = " -> 核心断链连2轮，已触发自愈(重启AstrBot)"
                else:
                    fixed = " -> 自愈脚本未产生日志（可能解析失败，检查编码！）"
            except Exception as e:  # noqa: BLE001
                fixed = " -> 自愈启动失败: %s" % e
        elif consecutive >= 2:
            fixed = " -> 核心断链连2轮，但冷却中（上次修复 <30min）"
        else:
            fixed = " -> 核心断链第1轮，观测中（连续2轮才自愈）"
        tmp2 = BROKEN + ".tmp"
        open(tmp2, "w", encoding="utf-8").write(json.dumps(bk, ensure_ascii=False, indent=1))
        os.replace(tmp2, BROKEN)
    elif not replay_lines and os.path.isfile(BROKEN):
        try:
            os.remove(BROKEN)
        except OSError:
            pass

    # [2026-11-04] 读取全链路验证的问题列表，作为告警出口（原来 issues 没人读）
    longline_note = ""
    try:
        vi = os.path.join(r"%PROJECT_HOME%\audition\gateway", "longline_issues.json")
        if os.path.isfile(vi):
            vd = json.loads(open(vi, encoding="utf-8").read())
            vissues = [str(x) for x in (vd.get("issues") or [])]
            if vissues:
                vf = os.path.join(ROOT, "_longline_seen.json")
                seen = _load(vf, {})
                new_v = [x for x in vissues if x not in seen]
                if new_v:
                    for x in new_v:
                        _wlog("[全链路验证] " + x[:200])
                        seen[x] = time.time()
                    open(vf + ".tmp", "w", encoding="utf-8").write(json.dumps(seen, ensure_ascii=False, indent=1))
                    os.replace(vf + ".tmp", vf)
                    longline_note = " 新验证问题 %d 条" % len(new_v)
    except Exception:
        pass

    msg = "[错误巡检] %s 新增 ERROR %d 条 核心断链 %d 插件发送告警 %d 连接抖动 %d%s" % (
        state["last_scan_local"], errors, core_broken, plugin_warn, connects, fixed + longline_note)
    if slot_cnt:
        msg += " 槽：" + " ".join("%s=%d" % (k, v) for k, v in sorted(slot_cnt.items()))
    print(msg)
    _wlog(msg)
    if replay_lines and samples:
        print("\n--- replay 命中样例（前 10 条）---")
        for s in samples[:10]:
            print(" ", s[:180])
    return 0


def main() -> int:
    replay = 0
    if "--replay" in sys.argv:
        i = sys.argv.index("--replay")
        if i + 1 < len(sys.argv):
            try:
                replay = int(sys.argv[i + 1])
            except ValueError:
                replay = 5000
        else:
            replay = 5000
    return scan(replay)


if __name__ == "__main__":
    raise SystemExit(main())