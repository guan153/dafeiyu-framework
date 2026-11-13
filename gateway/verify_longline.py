# -*- coding: utf-8 -*-
"""长线任务验证器 v2：服务/注入率/内核快照/真实身体/听觉，落盘留痕"""
import json, os, time, urllib.request, subprocess, datetime
from pathlib import Path

GWD = Path(r"%PROJECT_HOME%\audition\gateway")
STATUS = GWD / "longline_status.jsonl"
SNAP = Path(r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_whale_beams_live\beams_live_snapshot.json")
AFFECT = Path(r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_whale_affect\affect_state.json")
EVENTS = Path(r"%PROJECT_HOME%\audition\events.jsonl")


def jget(url, timeout=3):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))



def sched_current():
    """能力调度器当前激活能力；不可达返回 None（8GB 卡同时只能跑一个 GPU 服务）"""
    try:
        d = jget("http://127.0.0.1:8921/status", timeout=6)
        return d.get("current")
    except Exception:
        return None

def proc_match(kw):
    """统计逻辑进程数。

    [2026-11-04 修正] uv/venv 的 python 是「重定向器 + 真实解释器」两个进程，父子 cmdline
    都含脚本名；只数父进程不在命中集合里的根进程，否则一个进程会被数成 2（每 5 分钟假报重复）。
    """
    try:
        import psutil
        hits = {}
        for pr in psutil.process_iter(["pid", "ppid", "cmdline"]):
            try:
                cl = pr.info.get("cmdline") or []
                if kw in " ".join(cl):
                    hits[pr.info.get("pid")] = pr.info.get("ppid")
            except Exception:
                continue
        roots = [pid for pid, ppid in hits.items() if ppid not in hits]
        return len(roots)
    except Exception:
        return -1


def main():
    r = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "checks": {}, "metrics": {}, "issues": []}
    # A 服务
    for name, url in [("gateway", "http://127.0.0.1:8920/health"),
                      ("astrbot", "http://127.0.0.1:6185/"),
                      ("tts", "http://127.0.0.1:8912/health"),
                      ("asr", "http://127.0.0.1:8913/health"),
                      ("ear", "http://127.0.0.1:8919/health")]:
        try:
            if name == "astrbot":
                urllib.request.urlopen(url, timeout=3)
                r["checks"][name] = True
            else:
                d = jget(url)
                r["checks"][name] = bool(d.get("ok") or d.get("ready") or d.get("running"))
                if name == "gateway":
                    r["metrics"]["gateway_requests"] = d.get("requests", 0)
                    r["metrics"]["gateway_injected"] = d.get("injected", 0)
                    r["metrics"]["gateway_uptime"] = d.get("uptime_s", 0)
                if name == "ear":
                    r["metrics"]["ear_events"] = d.get("events", 0)
        except Exception as e:
            r["checks"][name] = False
            if name == "tts":
                cur = sched_current()
                if cur == "speak":
                    r["issues"].append("tts 不可达（调度器 current=speak，本应在线）")
                elif cur is None:
                    r["issues"].append("tts 不可达（调度器也不可达，按故障处理）")
                elif cur == "unknown":
                    r["issues"].append("tts 不可达（调度器 current=unknown，无法确认是主动停的）")
                else:
                    r["metrics"]["tts_state"] = "stopped_by_scheduler(%s)" % cur
            else:
                r["issues"].append("%s 不可达: %s" % (name, str(e)[:50]))
    # B 进程
    r["metrics"]["watchdog_procs"] = proc_match("watchdog.py")
    r["metrics"]["gateway_procs"] = proc_match("beam_gateway.py")
    if r["metrics"]["watchdog_procs"] < 1:
        r["issues"].append("守护进程不在")
    if r["metrics"]["gateway_procs"] > 1:
        r["issues"].append("网关进程重复 %d" % r["metrics"]["gateway_procs"])
    # C 内核快照（网关写的）
    try:
        snap = json.loads(SNAP.read_text(encoding="utf-8"))
        r["metrics"]["kernel_label"] = snap.get("label")
        r["metrics"]["kernel_valence"] = round(float(snap.get("valence") or 0), 3)
        r["metrics"]["kernel_fatigue"] = round(float(snap.get("fatigue") or 0), 3)
        r["metrics"]["snapshot_age_s"] = round(time.time() - float(snap.get("ts") or 0), 1)
        r["metrics"]["heard"] = snap.get("heard")
        age = r["metrics"]["snapshot_age_s"]
        if age > 180:
            r["issues"].append("快照 %.0f 秒没刷新（心跳可能没跑）" % age)
        # [2026-11-05] heard 空不一定是故障：安静时刻本来就没有新声音。放到 E 段结合新鲜度再判。
    except Exception as e:
        r["issues"].append("内核快照读不到: %s" % str(e)[:50])
    # D 真实身体
    try:
        a = json.loads(AFFECT.read_text(encoding="utf-8"))
        r["metrics"]["energy"] = round(float((a.get("needs") or {}).get("energy") or 0), 1)
        r["metrics"]["restarts_today"] = (a.get("intero") or {}).get("restarts_today")
        r["metrics"]["affect_label"] = a.get("label")
    except Exception as e:
        r["issues"].append("affect 读不到: %s" % str(e)[:50])
    # E 听觉累计
    try:
        if EVENTS.exists():
            lines = [l for l in EVENTS.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
            r["metrics"]["audition_total"] = len(lines)
            if lines:
                last = json.loads(lines[-1])
                r["metrics"]["audition_last"] = last.get("ts")
                r["metrics"]["audition_last_tags"] = (last.get("sound_tags") or [])[:3]
                # [2026-11-05] 听觉进快照检查：15 分钟内有新声音、但快照 heard 为空才报
                _fresh = False
                try:
                    _t = datetime.datetime.strptime(str(r["metrics"]["audition_last"])[:19], "%Y-%m-%dT%H:%M:%S")
                    _fresh = (time.time() - _t.timestamp()) <= 900
                except Exception:
                    _fresh = False
                if _fresh and not r["metrics"].get("heard"):
                    r["issues"].append("听觉没进快照（15 分钟内有新声音，但快照 heard 为空）")
    except Exception:
        pass
    # F 注入率
    req = r["metrics"].get("gateway_requests", 0)
    inj = r["metrics"].get("gateway_injected", 0)
    r["metrics"]["inject_ratio"] = round(inj / req, 2) if req else None
    if req and inj < req:
        r["issues"].append("有 %d 次调用没注入" % (req - inj))
    # [2026-11-04] issues 出口：原来 issues 只写进 longline_status.jsonl，没人读。
    # 写一份最新 issue 文件，由 error_watch 每轮读取并告警。
    try:
        ISSUES = GWD / "longline_issues.json"
        tmp_i = ISSUES.with_suffix(".tmp")
        tmp_i.write_text(json.dumps({"ts": r["ts"], "issues": r["issues"]}, ensure_ascii=False), encoding="utf-8")
        tmp_i.replace(ISSUES)
    except Exception:
        pass
    # 落盘
    with open(STATUS, "a", encoding="utf-8") as f:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")
    # 输出
    print("=== 长线验证 %s ===" % r["ts"])
    for k, v in r["checks"].items():
        print("  %-8s %s" % (k, "PASS" if v else "FAIL"))
    print("--- 指标 ---")
    for k, v in r["metrics"].items():
        print("  %-18s %s" % (k, v))
    print("--- 问题 ---")
    if r["issues"]:
        for i in r["issues"]:
            print("  ! " + i)
    else:
        print("  无")
    return r


if __name__ == "__main__":
    main()