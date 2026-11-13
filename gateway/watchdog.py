# -*- coding: utf-8 -*-
"""BEAM 网关守护 v2：15s 巡检 + 60s 启动冷却 + 每 5 分钟验证留痕"""
import os, subprocess, time, urllib.request

GW = r"%PROJECT_HOME%\audition\gateway\beam_gateway.py"
VERIFY = r"%PROJECT_HOME%\audition\gateway\verify_longline.py"
MAINTAIN = r"%PROJECT_HOME%\audition\gateway\maintain.py"
SOUND_BRIDGE = r"%PROJECT_HOME%\audition\gateway\sound_bridge.py"
ASR_PY = r"%PROJECT_HOME%\asr_env\Scripts\python.exe"
PY = r"D:\astrbot-venv\Scripts\python.exe"
WD = r"%PROJECT_HOME%\audition\gateway"
LOG = os.path.join(WD, "watchdog.log")
CREATE_NO_WINDOW = 0x08000000

_ST = {"last_start": 0.0, "tick": 0, "last_maint": 0.0}


def log(m):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), m))
    except Exception:
        pass


def healthy():
    try:
        with urllib.request.urlopen("http://127.0.0.1:8920/health", timeout=10) as r:
            return r.status == 200
    except Exception:
        return False


def port_open():
    import socket
    s = socket.socket()
    s.settimeout(1)
    try:
        s.connect(("127.0.0.1", 8920))
        return True
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def kill_stale():
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=10,
                             creationflags=CREATE_NO_WINDOW).stdout
        for line in out.splitlines():
            if ":8920" in line and "LISTENING" in line:
                pid = line.split()[-1]
                if pid.isdigit() and int(pid) != os.getpid():
                    subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True,
                                   creationflags=CREATE_NO_WINDOW)
                    log("killed stale pid %s" % pid)
    except Exception as e:
        log("kill_stale err: %s" % e)


def start():
    logs = open(os.path.join(WD, "gw.out.log"), "a", encoding="utf-8")
    errs = open(os.path.join(WD, "gw.err.log"), "a", encoding="utf-8")
    subprocess.Popen([PY, GW], cwd=WD, stdout=logs, stderr=errs, creationflags=CREATE_NO_WINDOW)


LOCK = os.path.join(WD, "watchdog.pid")


def _acquire_single_instance():
    """单实例锁：拿不到就退出，避免两个守护互相 kill_stale 形成互杀。"""
    try:
        if os.path.isfile(LOCK):
            try:
                old = int(open(LOCK, "r", encoding="utf-8").read().strip() or 0)
            except Exception:
                old = 0
            alive = False
            if old > 0:
                try:
                    import psutil
                    alive = psutil.pid_exists(old)
                except Exception:
                    try:
                        os.kill(old, 0)
                        alive = True
                    except Exception:
                        alive = False
            if alive:
                print("watchdog already running pid=%d, exit" % old)
                return False
        with open(LOCK, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
        return True
    except Exception:
        return True


if __name__ == "__main__":
    if not _acquire_single_instance():
        raise SystemExit(0)
    log("=== watchdog v2 start pid=%d ===" % os.getpid())
    while True:
        try:
            if not healthy():
                # [2026-11-05] 防误杀：连续 2 次不健康才重启（网关忙时 /health 偶发慢）
                _ST["unhealthy"] = _ST.get("unhealthy", 0) + 1
                if _ST["unhealthy"] < 2:
                    time.sleep(10)
                    continue
                _ST["unhealthy"] = 0
                if time.time() - _ST["last_start"] < 60:
                    time.sleep(10)
                    continue
                _ST["last_start"] = time.time()
                if port_open():
                    kill_stale()
                    time.sleep(1)
                log("gateway down -> restarting")
                start()
                time.sleep(8)
                log("restart %s" % ("OK" if healthy() else "FAILED"))
            else:
                _ST["unhealthy"] = 0
                _ST["tick"] += 1
                # 每 4 小时做一次降熵维护
                if time.time() - _ST["last_maint"] > 14400:
                    _ST["last_maint"] = time.time()
                    try:
                        subprocess.run([PY, MAINTAIN], capture_output=True, timeout=120,
                                       creationflags=CREATE_NO_WINDOW)
                        log("maintain done")
                    except Exception as e:
                        log("maintain err: %s" % e)
                # 每 12 拍（3 分钟）跑一次音乐理解桥
                if _ST["tick"] % 12 == 0:
                    try:
                        subprocess.run([ASR_PY, SOUND_BRIDGE], capture_output=True, timeout=300,
                                       stdin=subprocess.DEVNULL,
                                       creationflags=CREATE_NO_WINDOW)
                        log("sound_bridge done (tick %d)" % _ST["tick"])
                    except Exception as e:
                        log("sound_bridge err: %s" % e)
                if _ST["tick"] % 20 == 0:
                    try:
                        subprocess.run([PY, VERIFY], capture_output=True, timeout=60,
                                       creationflags=CREATE_NO_WINDOW)
                        log("verify done (tick %d)" % _ST["tick"])
                    except Exception as e:
                        log("verify err: %s" % e)
        except Exception as e:
            log("loop err: %s" % e)
        time.sleep(15)