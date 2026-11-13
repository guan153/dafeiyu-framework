# -*- coding: utf-8 -*-
"""soundtry 桥：她耳朵听到音乐时，录一段连续音频做深度音乐理解，结果写进 sound_detail.jsonl"""
import sys, os, json, time, subprocess

sys.path.insert(0, r"%PROJECT_HOME%\audition")
import numpy as np
import mic_listener as ml

EVENTS = r"%PROJECT_HOME%\audition\events.jsonl"
DETAIL = r"%PROJECT_HOME%\audition\gateway\sound_detail.jsonl"
WORK = r"%PROJECT_HOME%\audition\gateway\sound_work"
PLUGIN = r"%USERPROFILE%\.dsh\profiles\desktop\node_modules\dsh-soundtry"
PY = os.path.join(PLUGIN, "runtime", "venv", "Scripts", "python.exe")
CLI = os.path.join(PLUGIN, "python", "soundtry.py")
os.makedirs(WORK, exist_ok=True)


def log(m):
    try:
        with open(os.path.join(WORK, "bridge.log"), "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), m))
    except Exception:
        pass


def recent_events(minutes=3):
    items = []
    try:
        if not os.path.exists(EVENTS):
            return items
        cut = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - minutes * 60))
        with open(EVENTS, "r", encoding="utf-8", errors="replace") as f:
            for line in f.read().splitlines()[-80:]:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if str(d.get("ts") or "") >= cut:
                    items.append(d)
    except Exception as e:
        log("recent_events err: %s" % e)
    return items


def run_cli(task, wav, extra=None):
    cmd = [PY, CLI, task, wav] + (extra or [])
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=300, encoding="utf-8", errors="replace")
    except Exception as e:
        log("cli %s err: %s" % (task, e))
        return None
    out = (p.stdout or "") + "\n" + (p.stderr or "")
    data = None
    for line in out.splitlines():
        if line.startswith("@@JSON@@"):
            try:
                data = json.loads(line[len("@@JSON@@"):])
            except Exception:
                pass
    return {"ok": p.returncode == 0, "json": data, "raw": out[-800:]}


def main():
    evs = recent_events(3)
    music = [e for e in evs if any("音乐" in str(t) or "乐器" in str(t) for t in (e.get("sound_tags") or []))]
    if len(music) < 4:
        log("skip: 最近3分钟音乐事件 %d 条（不足4条）" % len(music))
        return {"ok": False, "reason": "no_music"}
    # 关键：只有"现在还在放音乐"才录（否则录到的是音乐停了之后的静音）
    try:
        import datetime
        last_ts = max(str(e.get("ts") or "") for e in music)
        last_dt = datetime.datetime.strptime(last_ts[:19], "%Y-%m-%dT%H:%M:%S")
        age = (datetime.datetime.now() - last_dt).total_seconds()
        if age > 30:
            log("skip: 最近一条音乐事件是 %.0f 秒前（音乐已经停了）" % age)
            return {"ok": False, "reason": "stale"}
    except Exception:
        pass
    # 录 25 秒连续回环
    wav = os.path.join(WORK, "cap_%s.wav" % time.strftime("%H%M%S"))
    try:
        dev, name = ml.get_device(loopback=True)
        a = ml.record(dev, 25)
        rms = float(np.sqrt(np.mean(a ** 2))) if len(a) else 0.0
        if rms < 0.002:
            log("skip: 录到静音 rms=%.5f" % rms)
            return {"ok": False, "reason": "silence", "rms": rms}
        ml.save_wav(wav, a)
        log("recorded %s rms=%.4f" % (wav, rms))
    except Exception as e:
        log("record err: %s" % e)
        return {"ok": False, "reason": "record_err", "err": str(e)[:120]}
    # 分析：节奏 + 结构
    rh = run_cli("rhythm", wav)
    st = run_cli("structure", wav)
    rec = {"ts": time.time(), "time": time.strftime("%H:%M"), "wav": wav,
           "rms": round(rms, 4),
           "rhythm": (rh or {}).get("json"),
           "structure": (st or {}).get("json"),
           "rhythm_ok": bool((rh or {}).get("ok")),
           "structure_ok": bool((st or {}).get("ok"))}
    with open(DETAIL, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    log("detail written: bpm=%s sections=%s" % (
        (rec["rhythm"] or {}).get("bpm"), len((rec["structure"] or {}).get("sections") or [])))
    return {"ok": True, "bpm": (rec["rhythm"] or {}).get("bpm")}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False))