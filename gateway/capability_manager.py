# -*- coding: utf-8 -*-
"""能力调度器（8921）：谁上场谁占卡。

GET  /status                    -> 各服务/显存/当前能力
POST /switch {"cap": "sing"}    -> 切到某能力（停掉冲突的 GPU 服务，起目标的）
POST /free                      -> 把所有 GPU 服务停掉，释放显存
"""
import json, os, subprocess, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8921
PY311 = r"%USERPROFILE%\AppData\Roaming\uv\python\cpython-3.11-windows-x86_64-none\python.exe"
PY312 = r"%USERPROFILE%\AppData\Roaming\uv\python\cpython-3.12-windows-x86_64-none\python.exe"

SERVICES = {
    "tts": {"cn": "说话", "port": 8912, "gpu": True,
            "cmd": [r"G:\ai\index-tts\.venv\Scripts\python.exe", r"G:\ai\feiyu_voice\tts_service.py"],
            "cwd": r"G:\ai\index-tts",
            "env": {"INDEX_TTS_DIFF_STEPS": "25", "PYTHONPATH": "."},
            "match": "tts_service"},
    "asr": {"cn": "听写", "port": 8913, "gpu": False,
            "cmd": [r"%PROJECT_HOME%\asr_env\Scripts\python.exe", r"G:\ai\feiyu_voice\asr_service.py"], "match": "asr_service"},
    "draw": {"cn": "生图", "port": 8188, "gpu": True,
             "cmd": [r"G:\ai\ComfyUI\.venv\Scripts\python.exe", r"G:\ai\ComfyUI\main.py", "--listen", "127.0.0.1", "--port", "8188", "--offline",
                     "--enable-manager", "--enable-manager-legacy-ui", "--disable-auto-launch", "--lowvram",
                     "--disable-dynamic-vram", "--reserve-vram", "1.2",
                     "--output-directory", r"G:\ai\ComfyUI\output",
                     # [2026-11-04 冲突修复] 与 comfyui-watchdog 的启动参数对齐，避免两套启动方式
                     # 写不同临时目录导致工作流缓存错乱。
                     "--temp-directory", r"G:\ai\character-training\20261027_dafeiyu_v2\temp\comfy_gpu"],
             "match": "ComfyUI\\main.py"},
}

# 能力 -> 需要的服务 + 是否独占显存
CAPS = {
    "speak": {"cn": "说话", "need": ["tts"], "exclusive": False},
    "listen": {"cn": "听写", "need": ["asr"], "exclusive": False},
    "draw": {"cn": "生图", "need": ["draw"], "exclusive": True},
    "sing": {"cn": "唱歌", "need": [], "exclusive": True, "installed": False},
    "music": {"cn": "听音乐", "need": [], "exclusive": False, "installed": True},
}

LOG = r"%PROJECT_HOME%\audition\gateway\capability.log"
_SWITCH_LOCK = None  # 延迟创建：切换串行化
# [2026-11-07 自动恢复] draw 闲置多久后自动切回 speak。
# 背景：切 draw 会停 TTS（显存互斥），而原来切过去就不回来了，语音会一直哑。
DRAW_IDLE_RESTORE_SEC = float(__import__("os").environ.get("DRAW_IDLE_RESTORE_SEC", "600"))
_LAST_SWITCH_TS = 0.0

_STATE = {"current": "unknown"}  # 当前激活能力，供 comfyui-watchdog / verify 判断
_STATE_FILE = r"%PROJECT_HOME%\audition\gateway\capability_state.json"


def _load_state():
    try:
        with open(_STATE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        v = data.get("current")
        if v == "draw":
            globals()["_LAST_SWITCH_TS"] = float(data.get("ts") or 0.0)
        if v:
            _STATE["current"] = str(v)
    except Exception:
        pass


def _save_state():
    try:
        tmp = _STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"current": _STATE["current"], "ts": time.time()}, fh, ensure_ascii=False)
        os.replace(tmp, _STATE_FILE)
    except Exception:
        pass


def log(m):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), m))
    except Exception:
        pass


def port_up(p):
    import socket
    s = socket.socket()
    s.settimeout(1)
    try:
        s.connect(("127.0.0.1", p))
        return True
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def pids_of(match):
    out = []
    try:
        import psutil
        for pr in psutil.process_iter(["cmdline"]):
            try:
                cl = " ".join(pr.info.get("cmdline") or [])
                if match in cl:
                    out.append(pr.pid)
            except Exception:
                continue
    except Exception:
        pass
    return out


def stop_service(key):
    svc = SERVICES[key]
    killed = 0
    for pid in pids_of(svc["match"]):
        try:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=20)
            killed += 1
        except Exception:
            pass
    if killed:
        log("stopped %s (%d procs)" % (key, killed))
    return killed


def service_ready(key):
    """端口通了不算就绪：TTS 要等模型加载完 /health.ready=true"""
    svc = SERVICES[key]
    if not port_up(svc["port"]):
        return False
    if key == "tts":
        try:
            with urllib.request.urlopen("http://127.0.0.1:8912/health", timeout=5) as r:
                d = json.loads(r.read().decode("utf-8"))
            return bool(d.get("ready"))
        except Exception:
            return False
    return True


def start_service(key):
    svc = SERVICES[key]
    if port_up(svc["port"]):
        return True
    env = dict(os.environ)
    env.update(svc.get("env") or {})
    try:
        subprocess.Popen(svc["cmd"], cwd=svc.get("cwd"), env=env,
                         creationflags=0x08000000,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log("starting %s" % key)
    except Exception as e:
        log("start %s err: %s" % (key, e))
        return False
    for _ in range(150):
        time.sleep(2)
        if service_ready(key):
            log("%s ready" % key)
            return True
    log("%s start timeout" % key)
    return False


def _read_toggles():
    """读一键开关状态（tools/toggles.py 写）。读不到按「都开」处理。"""
    try:
        p = r"%PROJECT_HOME%\audition\gateway\toggles.json"
        with open(p, "r", encoding="utf-8-sig") as fh:
            d = json.load(fh)
        return {"image": bool(d.get("image", True)), "voice": bool(d.get("voice", True))}
    except Exception:
        return {"image": True, "voice": True}


def _comfy_queue_empty():
    """ComfyUI 队列是否空闲（拿不到就按「不空闲」处理，宁可不切）。"""
    try:
        with urllib.request.urlopen("http://127.0.0.1:8188/queue", timeout=4) as r:
            data = json.loads(r.read().decode("utf-8"))
        running = data.get("queue_running") or []
        pending = data.get("queue_pending") or []
        return not running and not pending
    except Exception:
        return False


def vram():
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.free", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        used, free = out.split(",")
        return {"used_mb": int(used), "free_mb": int(free)}
    except Exception:
        return {}


def status():
    st = {"vram": vram(), "current": _STATE["current"], "services": {}, "caps": {}}
    for k, v in SERVICES.items():
        st["services"][k] = {"cn": v["cn"], "up": port_up(v["port"]), "gpu": v["gpu"]}
    for k, v in CAPS.items():
        st["caps"][k] = {"cn": v["cn"], "installed": v.get("installed", True),
                         "ready": all(service_ready(s) for s in v["need"]) if v["need"] else v.get("installed", True)}
    return st


def switch(cap):
    global _LAST_SWITCH_TS
    if cap not in CAPS:
        return {"ok": False, "error": "unknown cap: %s" % cap}
    c = CAPS[cap]
    if not c.get("installed", True):
        return {"ok": False, "error": "%s 还没安装（唱歌模型未装）" % c["cn"]}
    # [2026-11-04 冲突修复] 切换窗口先落盘 switching，避免 comfyui-watchdog 在
    # "已杀旧服务、current 还没更新"的空档按旧状态把 ComfyUI 拉起来抢显存。
    _STATE["current"] = "switching"
    _save_state()
    # 8GB 只够一个：不管 exclusive，只要不是目标需要的 GPU 服务就停掉
    for k, svc in SERVICES.items():
        if svc["gpu"] and k not in c["need"]:
            stop_service(k)
    # 起需要的
    for k in c["need"]:
        if not start_service(k):
            # [2026-11-04 冲突修复] 启动失败必须回滚状态：否则持久化成 switching，
            # comfyui-watchdog 永久让位，ComfyUI 再也没人拉起。
            _STATE["current"] = "free"
            _save_state()
            return {"ok": False, "error": "启动失败: " + k}
    _STATE["current"] = cap
    _save_state()
    _LAST_SWITCH_TS = time.time()
    log("switch -> %s" % cap)
    return {"ok": True, "cap": cap, "status": status()}


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.startswith("/status"):
            self._send(200, status())
        elif self.path.startswith("/free"):
            for k, svc in SERVICES.items():
                if svc["gpu"]:
                    stop_service(k)
            _STATE["current"] = "free"
            self._send(200, {"ok": True, "freed": True, "vram": vram()})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        except Exception:
            body = {}
        if self.path.startswith("/switch"):
            cap = body.get("cap") or ""
            # [2026-11-04 自主模型调用] 立即应答，后台切换：插件不再阻塞 60 秒等模型加载
            def _do_switch(_cap=cap):
                try:
                    with _SWITCH_LOCK:
                        _r = switch(_cap)
                    log("async switch done: %s" % json.dumps(_r, ensure_ascii=False)[:200])
                except Exception as _e:
                    log("async switch err: %s" % _e)
            try:
                import threading as _t
                _t.Thread(target=_do_switch, daemon=True).start()
                self._send(200, {"ok": True, "accepted": True, "cap": cap})
            except Exception as e:
                self._send(500, {"ok": False, "error": str(e)[:200]})
        elif self.path.startswith("/free"):
            for k, svc in SERVICES.items():
                if svc["gpu"]:
                    stop_service(k)
            _STATE["current"] = "free"
            _save_state()
            self._send(200, {"ok": True, "vram": vram()})
        else:
            self._send(404, {"error": "not found"})


_PID_LOCK = r"%PROJECT_HOME%\audition\gateway\capability.pid"


def _acquire_single_instance():
    try:
        if os.path.isfile(_PID_LOCK):
            try:
                old = int(open(_PID_LOCK, "r", encoding="utf-8").read().strip() or 0)
            except Exception:
                old = 0
            if old > 0:
                try:
                    import psutil
                    alive = psutil.pid_exists(old)
                except Exception:
                    alive = False
                if alive:
                    print("capability manager already running pid=%d, exit" % old)
                    return False
        with open(_PID_LOCK, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
        return True
    except Exception:
        return True


def _auto_guard():
    """[2026-11-04 架构收敛] draw 能力激活时确保 ComfyUI 健康（单一所有者=能力调度器）。

    原来 ComfyUI 有第二个主：comfyui-watchdog 计划任务。现在调度器负责
    "能力激活状态下的自愈"，watchdog 仅在调度器不在时兜底。
    """
    import threading as _th
    while True:
        time.sleep(30)
        try:
            tog = _read_toggles()
            if _STATE.get("current") == "draw" and not tog.get("image", True):
                target = "speak" if tog.get("voice", True) else "free"
                log("auto-guard: 生图开关=关，切回 %s" % target)
                try:
                    switch(target)
                except Exception as exc:  # noqa: BLE001
                    log("auto-guard: 切回 %s 失败：%s" % (target, exc))
                continue
            if _STATE.get("current") != "draw":
                continue
            if not port_up(8188):
                log("auto-guard: draw 激活但 8188 不在，重启 ComfyUI")
                start_service("draw")
                continue
            # [2026-11-07] draw 闲置自动切回 speak，别再让她的语音哑着
            idle = time.time() - float(_LAST_SWITCH_TS or 0)
            if (_LAST_SWITCH_TS and idle > DRAW_IDLE_RESTORE_SEC
                    and tog.get("voice", True) and _comfy_queue_empty()):
                log("auto-guard: draw 已闲置 %d 秒且队列空闲，自动切回 speak" % int(idle))
                try:
                    switch("speak")
                except Exception as exc:  # noqa: BLE001
                    log("auto-guard: 自动切回 speak 失败：%s" % exc)
        except Exception as e:
            log("auto-guard err: %s" % e)


if __name__ == "__main__":
    import threading as _th_init
    _SWITCH_LOCK = _th_init.Lock()
    if not _acquire_single_instance():
        raise SystemExit(0)
    _load_state()
    _save_state()  # [2026-11-04] 保证 capability_state.json 存在（audit：文件缺失会导致 current 永远 unknown）
    log("capability manager start on %d (current=%s)" % (PORT, _STATE["current"]))
    print("能力调度器: http://127.0.0.1:%d/status" % PORT, flush=True)
    import threading as _th0
    _th0.Thread(target=_auto_guard, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
