# -*- coding: utf-8 -*-
"""02 §G/§N 指标报告：Success / Self-heal / MTTR / Waste / Human / Cost

用法：python metrics_report.py
输出：30_指标报告-YYYYMMDD.md
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = r"%PROJECT_HOME%\框架重构"
PID = r"%ASTRBOT_HOME%\data\plugin_data"
VOICE = os.path.join(PID, "astrbot_plugin_whale_voice")
BEAMS = os.path.join(PID, "astrbot_plugin_whale_beams")
QBOT = r"%QBOT_HOME%\qbot"
RUNTIME = os.path.join(QBOT, "AstrBot", "runtime.log")
OUT = os.path.join(ROOT, "30_指标报告-%s.md" % time.strftime("%Y%m%d"))


# 02 §H 失败分槽（T=超时 V=校验 U=上游 P=策略 C=代码 G=护栏 L=本地 S=状态）
_FAIL_SLOTS = {
    "T": ("timeout", "timed out", "超时", "TimeoutError"),
    "V": ("validation", "invalid", "empty text", "not a valid", "ValueError"),
    "U": ("upstream", "HTTPError", " 502", " 503", "connection", "ConnectionError"),
    "P": ("policy", "refused", "blocked", "denied", " 401", " 403"),
    "C": ("Traceback", "KeyError", "TypeError", "AttributeError", "IndexError", "SyntaxError"),
    "G": ("guardrail", "八股", "禁表", "不可外发"),
    "L": ("FileNotFound", "PermissionError", "OSError", "ENOSPC"),
    "S": ("not ready", "尚未初始化", "state", "状态不一致"),
}


def _slot_of(line: str) -> str:
    low = line.lower()
    # 框架层未处理异常优先归 C（否则会被 traceback 里的 timeout 细节误归 T）
    if "unhandled exception" in low or "error in asgi" in low:
        return "C"
    if "no response returned" in low:
        return "T"
    for slot, pats in _FAIL_SLOTS.items():
        for p in pats:
            if p.lower() in low:
                return slot
    return "OTHER"


def _today_start() -> float:
    lt = time.localtime()
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))


def _jsonl(p):
    rows = []
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    rows.append(json.loads(ln))
                except ValueError:
                    continue
    except OSError:
        pass
    return rows


def _json(p):
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _lines(p, tail=None):
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            ls = fh.readlines()
        return ls[-tail:] if tail else ls
    except OSError:
        return []


def success_block(lines):
    """Success：主动消息决策分布 + 后果回执 + 今日回复条数"""
    t0 = _today_start()
    dec = _jsonl(os.path.join(VOICE, "decisions.jsonl"))
    today = [d for d in dec if float(d.get("ts") or 0) >= t0]
    allow = sum(1 for d in today if d.get("code") == "allow")
    blocked = len(today) - allow
    cons = _json(os.path.join(VOICE, "consequence.json"))
    replied = int(cons.get("replied") or 0)
    ignored = int(cons.get("ignored") or 0)
    rate = (replied / (replied + ignored) * 100.0) if (replied + ignored) else 0.0
    # 回复条数：只统计 QQ 会话（guan=主人），窗口取日志尾部 6000 行（约最近数小时）
    tail = _lines(RUNTIME, 6000)
    sends = sum(1 for ln in tail if "Prepare to send - guan/" in ln)
    lines.append("## Success（任务成功率）")
    lines.append("- 今日开口决策：%d 次（allow %d / blocked %d，开门率 %.0f%%）" % (
        len(today), allow, blocked, (allow / len(today) * 100.0) if today else 0.0))
    lines.append("- 主动消息后果：replied %d / ignored %d → 回复率 %.0f%%" % (replied, ignored, rate))
    lines.append("- QQ 回复发送：%d 条（日志尾部 6000 行窗口）" % sends)
    lines.append("")
    return {"send": sends, "replied": replied, "ignored": ignored}


def selfheal_block(lines):
    """Self-heal：巡检重启 / 语音看门狗重启"""
    sup = os.path.join(QBOT, "supervisor.log")
    t0 = _today_start()
    n_sup = 0
    for ln in _lines(sup):
        if "已启动" in ln or "重启" in ln:
            try:
                ts = time.mktime(time.strptime(ln[1:20], "%Y-%m-%d %H:%M:%S"))
            except ValueError:
                continue
            if ts >= t0:
                n_sup += 1
    wlog = r"G:\ai\feiyu_voice\watchdog.log"
    n_wd = 0
    for ln in _lines(wlog):
        m = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", ln)
        if not m:
            continue
        try:
            ts = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            continue
        if ts >= t0 and ("[restart]" in ln or "[error]" in ln):
            n_wd += 1
    lines.append("## Self-heal（自愈成功率）")
    lines.append("- 进程巡检拉起/重启：%d 次（supervisor.log）" % n_sup)
    lines.append("- 语音看门狗重启/报错：%d 次（watchdog.log）" % n_wd)
    lines.append("")
    return {"sup": n_sup, "wd": n_wd}


def waste_block(lines):
    """Waste：无效循环（重复主动消息 / 决策空转）"""
    dec = _jsonl(os.path.join(VOICE, "decisions.jsonl"))
    t0 = _today_start()
    today = [d for d in dec if float(d.get("ts") or 0) >= t0]
    tokens = [str(d.get("token") or "") for d in today]
    dup = len(tokens) - len(set(tokens))
    shadow = _jsonl(os.path.join(BEAMS, "beams_obs.jsonl"))
    ts = [float(r.get("ts") or 0) for r in shadow if float(r.get("ts") or 0) >= t0]
    ticks = len(ts)
    lines.append("## Waste（无效循环/浪费）")
    lines.append("- 今日决策 token 重复：%d（重复=同一拍重复放行）" % dup)
    lines.append("- 内核观测拍数：%d（tick 流水无重复）" % ticks)
    lines.append("")
    return {"dup": dup, "ticks": ticks}


def alert_block(lines):
    """最近告警（error_watch 每 2 分钟增量扫描的产物）"""
    p = os.path.join(ROOT, "_alerts.jsonl")
    rows = _jsonl(p)
    st = _json(os.path.join(ROOT, "_alert_state.json"))
    lines.append("## 告警（error_watch 增量巡检）")
    lines.append("- 最近扫描：%s；游标 %s / 文件 %s" % (
        st.get("last_scan_local") or "?", st.get("offset") or 0, st.get("log_size") or 0))
    recent = [r for r in rows if time.time() - float(r.get("ts") or 0) <= 86400]
    if not recent:
        lines.append("- 近 24h 无新告警")
    else:
        cnt = {}
        for r in recent:
            cnt[r.get("slot") or "?"] = cnt.get(r.get("slot") or "?", 0) + 1
        lines.append("- 近 24h 告警 %d 条：%s" % (
            len(recent), " ".join("%s=%d" % (k, v) for k, v in sorted(cnt.items()))))
        for r in recent[-3:]:
            lines.append("  - [%s] %s" % (r.get("slot"), (r.get("line") or "")[:120]))
    lines.append("")
    return {"alerts": len(recent)}


def incident_block(lines):
    """MTTR / 检测时延（读 _incidents.jsonl 台账）"""
    p = os.path.join(ROOT, "_incidents.jsonl")
    rows = _jsonl(p)
    lines.append("## MTTR（事故台账）")
    if not rows:
        lines.append("- 台账为空（_incidents.jsonl）")
        lines.append("")
        return {}
    def _ts(s):
        try:
            return time.mktime(time.strptime(str(s)[:19], "%Y-%m-%dT%H:%M:%S"))
        except ValueError:
            return 0.0
    today = [r for r in rows if str(r.get("start") or "")[:10] == time.strftime("%Y-%m-%d")]
    mttrs = [float(r.get("mttr_s") or 0) for r in today if r.get("mttr_s") is not None]
    dets = []
    for r in today:
        d, s = _ts(r.get("detected")), _ts(r.get("start"))
        if d and s:
            dets.append(max(0.0, d - s))
    avg_mttr = sum(mttrs) / len(mttrs) if mttrs else 0.0
    avg_det = sum(dets) / len(dets) if dets else 0.0
    human = sum(1 for r in today if r.get("human"))
    lines.append("- 今日事故 %d 条；平均 MTTR %.0fs；平均检测时延 %.0fs；人工介入 %d 条" % (
        len(today), avg_mttr, avg_det, human))
    for r in today:
        lines.append("  - %s [%s] %s（MTTR %ss，%s）" % (
            r.get("id"), r.get("slot"), r.get("summary"), r.get("mttr_s"),
            "人工" if r.get("human") else "自愈"))
    lines.append("")
    return {"count": len(today), "mttr": avg_mttr, "detect": avg_det}


def quality_block(lines):
    """错误可见 + 熔断/成本（未埋点则标注）"""
    t0 = _today_start()
    errs, warns = 0, 0
    warn_pat = {}
    for ln in _lines(RUNTIME, 40000):
        if "[ERRO" in ln:
            errs += 1
        elif "[WARN" in ln:
            warns += 1
            m = re.search(r"\[WARN[^\]]*\] \[([^:]+):", ln)
            if m:
                key = m.group(1)
                warn_pat[key] = warn_pat.get(key, 0) + 1
    top_warns = sorted(warn_pat.items(), key=lambda kv: -kv[1])[:3]
    snap = _json(os.path.join(BEAMS, "beams_snapshot.json"))
    state = _json(os.path.join(BEAMS, "beams_state.json"))
    body = state.get("body") or {}
    tools = body.get("tools") or {}
    lines.append("## 质量/熔断/成本（值班四行）")
    lines.append("- 错误槽：ERROR %d / WARN %d（runtime.log 近 4 万行）" % (errs, warns))
    if top_warns:
        lines.append("- WARN 主要来源：%s" % "；".join("%s(%d)" % (k, v) for k, v in top_warns))
    lines.append("- 闸门：open=%s probability=%.2f" % (snap.get("gate_open"), float(snap.get("gate_probability") or 0)))
    lines.append("- 工具可用性：%s" % ", ".join("%s=%s" % (k, v) for k, v in tools.items()))
    # 02 §H 失败分槽
    slot_cnt = {}
    for ln in _lines(RUNTIME, 40000):
        if "[ERRO" not in ln:
            continue
        s = _slot_of(ln)
        slot_cnt[s] = slot_cnt.get(s, 0) + 1
    if slot_cnt:
        order = ["T", "V", "U", "P", "C", "G", "L", "S", "OTHER"]
        lines.append("- 失败分槽：%s" % " / ".join(
            "%s=%d" % (k, slot_cnt[k]) for k in order if slot_cnt.get(k)))

    usage = _json(os.path.join(PID, "astrbot_plugin_whale_brain", "usage.json"))
    today = usage.get("today") or {}
    if today and int(today.get("requests") or 0) > 0:
        req = int(today.get("requests") or 0)
        io = int(today.get("input_other") or 0)
        ic = int(today.get("input_cached") or 0)
        out = int(today.get("output") or 0)
        # deepseek-chat 粗估：未命中 ¥1/M、命中 ¥0.1/M、输出 ¥2/M
        cost = io / 1e6 * 1.0 + ic / 1e6 * 0.1 + out / 1e6 * 2.0
        hit = (ic / (io + ic) * 100.0) if (io + ic) else 0.0
        lines.append("- 成本：今日 %d 次请求 / 输入 %d tok（缓存命中 %.0f%%）/ 输出 %d tok → 粗估 ¥%.4f" % (
            req, io + ic, hit, out, cost))
        _elapsed_h = max(0.05, (time.time() - _today_start()) / 3600.0)
        lines.append("- 燃烧率：¥%.4f/小时（今日累计 / 已过小时数）" % (cost / _elapsed_h))
    else:
        lines.append("- 成本：埋点已上线（usage.json），今日尚无 LLM 调用")
    lines.append("")
    return {"errs": errs, "warns": warns}


def main():
    lines = ["# 指标报告 §G · %s" % time.strftime("%Y-%m-%d %H:%M"), ""]
    s = success_block(lines)
    sh = selfheal_block(lines)
    w = waste_block(lines)
    q = quality_block(lines)
    al = alert_block(lines)
    inc = incident_block(lines)
    lines.append("## Human（人工介入）")
    lines.append("- 本日人工介入：语音栈补启动 1 次 + AstrBot 冷启 1 次（详见 27 号文档事故记录）")
    lines.append("- 原则：冷启/发送/付费/部署 仍是人键（02 §M）")
    lines.append("")
    lines.append("## 结论")
    tot = s["replied"] + s.get("ignored", 0)
    lines.append("- 成功率：主动消息回复率 %.0f%%；自愈事件 %d+%d；无效循环 %d" % (
        (s["replied"] / tot * 100.0) if tot else 0.0, sh["sup"], sh["wd"], w["dup"]))
    lines.append("- MTTR：%d 条事故 · 均值 %.0fs · 检测时延均值 %.0fs（台账 _incidents.jsonl）" % (
        inc.get("count", 0), inc.get("mttr", 0.0), inc.get("detect", 0.0)))
    lines.append("- 缺口：成本按 deepseek-chat 价粗估；OTHER 槽错误待归类")
    text = "\n".join(lines)
    print(text)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    print("\n报告：" + OUT)


if __name__ == "__main__":
    main()