# -*- coding: utf-8 -*-
r"""BEAM LLM 网关（端口 8920）

把所有 LLM 调用收进一条管线：
  请求进来 → 读真实身体/听觉/情绪 → BEAM tick → 柔和注入底色 → 转发上游 → 回复喂回内核 → 返回

AstrBot 的 api_base 指向 http://127.0.0.1:8920/v1，于是她的每一次思考都穿过框架。
"""
import json
import socket
import sys
import time
import threading
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, r"%PROJECT_HOME%\框架重构")
from beams_core import BeamsKernel

PORT = 8920
UPSTREAM = "https://api.deepseek.com/v1"
# [2026-11-07 产品化] 密钥单一来源：环境变量 BEAM_SECRETS_FILE > NTFS 安全路径 > 旧 exFAT 路径（兼容）。
# exFAT 不支持 ACL，旧路径不安全，仅作兼容；不再硬编码任何密钥，缺密钥时明确报错而不是静默降级。
import os as _os
_CANDIDATES = [
    _os.environ.get("BEAM_SECRETS_FILE"),
    r"%USERPROFILE%\.config\v1-gateway\gateway_secrets.json",
    r"%PROJECT_HOME%\audition\gateway\gateway_secrets.json",
]
_SECRETS_FILE = next((p for p in _CANDIDATES if p and _os.path.exists(p)), "")
_sec = {}
if _SECRETS_FILE:
    try:
        _sec = json.load(open(_SECRETS_FILE, encoding="utf-8"))
    except Exception as _e:
        print("[gateway] 读取密钥文件失败: %s (%s)" % (_SECRETS_FILE, _e), file=sys.stderr)
else:
    print("[gateway] 警告：未找到 gateway_secrets.json，LLM 与 OneBot 调用将不可用", file=sys.stderr)
API_KEY = str(_sec.get("api_key") or "")
DEBUG_TOKEN = str(_sec.get("debug_token") or "")
STATE = r"%PROJECT_HOME%\audition\gateway\beam_gateway_state.json"
AFFECT = Path(r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_whale_affect\affect_state.json")
EVENTS = Path(r"%PROJECT_HOME%\audition\events.jsonl")
LOG = Path(r"%PROJECT_HOME%\audition\gateway\gateway.log")
OUT = Path(r"%PROJECT_HOME%\audition\gateway")
SNAP_FOR_PSY = Path(r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_whale_beams_live\beams_live_snapshot.json")
MONO = Path(r"%PROJECT_HOME%\audition\gateway\inner_monologue.jsonl")
OUTREACH = Path(r"%PROJECT_HOME%\audition\gateway\outreach.jsonl")
SOUND_DETAIL = Path(r"%PROJECT_HOME%\audition\gateway\sound_detail.jsonl")
ONEBOT = "http://127.0.0.1:3000"
ONEBOT_TOKEN = str(_sec.get("onebot_token") or "")
OWNER_QQ = int(_sec.get("owner_qq") or 100000000)
_ST_OUT = {"last": 0.0}
_ST_MONO = {"last": 0.0}
_ST_AUD = {"ts": "", "last_feed": 0.0}
# [2026-11-05 听力偏好与发言频率] 主人：她想听哪里就听哪里，但听完别急着发消息；
# 外界的声音少听，本机的声音多听。
AUD_FEED_MIN_GAP_S = 300.0          # 本机声音喂情绪：最多 5 分钟一次（原来每分钟都可能喂）
AUD_FEED_GC = {"video": 0.18, "error": -0.30}
AUD_FEED_INTENSITY = {"video": 0.32, "error": 0.45}
OUT_COOLDOWN_MIN = 90.0             # 主动消息冷却：45 -> 90 分钟
OUT_DAILY_CAP = 3                   # 主动消息每日上限：4 -> 3 条
OUT_SOUND_COOLDOWN_MIN = 180.0      # 上一条是"听到…"，下一条至少隔 3 小时
SOUND_WORDS = ("听到", "听见", "耳边", "声音", "音乐", "旋律", "叮", "吱", "歌")
OUT_QUIET_HOURS = (23.5, 8.0)      # 静音时段 23:30-08:00：安静，不主动发（和其他插件口径一致）
_aud_lock = threading.Lock()

Path(STATE).parent.mkdir(parents=True, exist_ok=True)
kernel = BeamsKernel(STATE)
_lock = threading.Lock()
_pos = [0]
_ST_REQ = {"last": time.time(), "peak": 0.0}
_ST_IDLE = {"hours": 0.0}
STATS = {"n": 0, "injected": 0, "started": time.time()}


def log(msg):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), msg))
    except Exception:
        pass


def _ev_source(d):
    """听觉事件来源：本机（扬声器回环）/ 外头（麦克风）/ 主人发来的（QQ 附件）"""
    try:
        src = str(d.get("source") or "").lower()
    except Exception:
        src = ""
    if src.startswith("loopback") or "扬声器" in src:
        return "local"
    if src.startswith("mic") or "麦克风" in src or "microphone" in src:
        return "outside"
    if src.startswith("qq_"):
        return "owner"
    return "unknown"


def _ev_item(d):
    """一条听觉事件 -> 半句话"""
    t = (d.get("text") or "").strip()
    tags = d.get("sound_tags") or []
    if t:
        return "人声「%s」" % t[:30]
    if tags:
        return "、".join([str(x) for x in tags[:2]])
    return ""


def _recent_outreach(n=60):
    """读最近的主动消息记录（新的在后）"""
    recs = []
    try:
        if OUTREACH.exists():
            lines = [l for l in OUTREACH.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()][-n:]
            for l in lines:
                try:
                    recs.append(json.loads(l))
                except Exception:
                    continue
    except Exception:
        pass
    return recs


def _is_sound_msg(text):
    """这条消息是不是在说"我听到了…"（听完就发消息要压）"""
    t = str(text or "")
    return any(w in t for w in SOUND_WORDS)


def read_state():
    """收集真实身体/听觉/情绪"""
    energy, restarts, latency = 50.0, 0, 80.0
    heard = ""
    try:
        a = json.loads(AFFECT.read_text(encoding="utf-8"))
        needs = a.get("needs") or {}
        intero = a.get("intero") or {}
        energy = float(needs.get("energy") or 50)
        restarts = int(intero.get("restarts_today") or 0)
        latency = float(intero.get("latency_p95") or 0.08) * 1000.0
        idle_h = float(intero.get("idle_hours") or 0)
        _ST_IDLE["hours"] = idle_h
        # 重启是"一次性事故"不是"持续错误"：空闲超过半小时就当它过去了（否则她会一直下沉）
        if idle_h > 0.5:
            restarts = 0
        else:
            restarts = min(restarts, 5)
    except Exception:
        pass
    try:
        if EVENTS.exists():
            lines = [l for l in EVENTS.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()][-8:]
            _local, _outside = [], []
            for l in lines:
                try:
                    d = json.loads(l)
                except Exception:
                    continue
                # [2026-11-05] 只用 10 分钟内的声音：别把几小时前的旧声音当成"刚听到"
                try:
                    _ts = str(d.get("ts") or "")[:19]
                    if time.time() - time.mktime(time.strptime(_ts, "%Y-%m-%dT%H:%M:%S")) > 900:
                        continue
                except Exception:
                    pass
                item = _ev_item(d)
                if not item:
                    continue
                src = _ev_source(d)
                if src == "outside":
                    _outside.append(item)
                elif src == "owner":
                    _local.append("主人发来的" + item)
                else:
                    _local.append(item)
            _heard = ["（本机）" + x for x in _local[-3:]]
            if _outside:
                _heard.append("（外头远处）" + _outside[-1])
            heard = "；".join(_heard)
    except Exception:
        pass
    return {"budget": max(0.05, min(1.0, energy / 100.0)), "errors_1h": restarts,
            "latency_ms": max(10.0, latency), "cpu": 0.35, "mem": 0.6}, heard, energy


def block(messages):
    """柔和底色：只给状态，不给命令"""
    try:
        body, heard, energy = read_state()
        with _lock:
            for msg in messages[-3:]:
                if msg.get("role") == "user":
                    txt = msg.get("content")
                    if isinstance(txt, list):
                        txt = " ".join(p.get("text", "") for p in txt if isinstance(p, dict))
                    txt = str(txt or "")[:80]
                    if txt:
                        kernel.feed("chat", why=txt, goal_congruence=gc_of(txt), intensity=0.65)
            kernel.tick(body_ctx=body)
            s = kernel.snapshot() or {}
        _rb = rest_bonus()
        energy_eff = min(100.0, energy + _rb)
        write_psy_snapshot(s, energy_eff, heard, energy)
        if _rb > 0.5:
            log("rest: energy %.1f + %.1f bonus -> %.1f (idle %.0f min)" % (
                energy, _rb, energy_eff, (time.time() - _ST_REQ["last"]) / 60.0))
        mood = float(s.get("valence") or 0)
        arousal = float(s.get("arousal") or 0.3)
        fatigue = float(s.get("fatigue") or 0)
        label = str(s.get("label") or "")
        outlet = str(s.get("outlet") or "")
        # [emotion-snapshot 20261113] 插件侧快照是唯一事实源，这里跟随，避免一轮两个心情
        try:
            _sp = Path(r"%ASTRBOT_HOME%\data\plugin_data\whale_core\emotion_snapshot.json")
            if _sp.is_file():
                _sn = json.loads(_sp.read_text(encoding="utf-8"))
                if (time.time() - float(_sn.get("ts") or 0)) <= float(_sn.get("stale_after") or 300):
                    if _sn.get("mood"):
                        label = str(_sn["mood"])
                    mood = float(_sn.get("valence") or mood)
                    arousal = float(_sn.get("arousal") or arousal)
        except Exception:
            pass
        def _moodword(lab, val, aro):
            base = {"开心": "心里亮堂", "黏人": "想凑近", "平静": "淡淡的", "紧张": "绷着",
                    "难过": "发闷", "生气": "有火", "委屈": "发酸", "困": "困"}.get(lab, "说不上来")
            if lab == "紧张" and val > 0.08 and aro > 0.55:
                return "被夸得心里有点发热、又不太敢认"
            if lab == "紧张" and val < -0.08:
                return "绷着，有点不安"
            if lab == "平静" and val > 0.15:
                return "松下来了，挺舒服"
            if lab == "低落" and val > -0.05:
                return "还在缓，但没那么闷了"
            return base

        word = _moodword(label, mood, arousal)
        _outlet_cn = {"connect": "想凑近人、想被理", "explore": "想看看新鲜东西",
                      "repair": "想把自己理顺、想收拾收拾", "complete": "想把事情做完",
                      "avoid": "想躲一会儿、不想被打扰"}.get(outlet, outlet)
        _fat = (" · 疲劳 %.2f" % fatigue) if fatigue > 0.05 else ""
        parts = ["【她此刻的底色（真实，不报数字、不演）】",
                 "身体：能量 %.0f/100%s" % (energy_eff, _fat),
                 "心情：%s（效价 %+.2f · 唤醒 %.2f）· 感觉%s" % (label or "?", mood, arousal, word)]
        if _outlet_cn:
            parts.append("此刻想：" + _outlet_cn)
        if heard:
            parts.append("刚听到：" + heard)
        parts.append("耳朵的偏好：本机的声音（电脑里放的：音乐、视频、你自己的）听得最清，是你身边的声音；外头/别处的动静只当远处背景，听见了也未必值得搭话。")
        _mono = recent_monologue()
        if _mono:
            parts.append("你刚才一个人发呆时想的：" + _mono[:150])
        _sl = sound_line()
        if _sl:
            parts.append(_sl)
        # 低电量保护：真累了要体现在话里，而不是装精神
        if energy_eff < 35:
            parts.append("你电量不多了（%.0f/100），话可以少一点、语速慢一点，累了就直说累。" % energy_eff)
        elif energy_eff < 50:
            parts.append("你今天折腾得有点多，能量 %.0f，说话会自然收敛些。" % energy_eff)
        return "\n".join(parts)
    except Exception as e:
        log("block err: %s" % e)
        return ""


_POS = ("喜欢", "可爱", "谢谢", "陪", "辛苦", "真好", "爱你", "乖", "棒", "厉害", "特别",
        "记住", "好孩子", "不错", "厉害呀", "抱抱", "想你", "开心", "好棒", "做得好")
_NEG = ("讨厌", "滚", "烦", "闭嘴", "废物", "垃圾", "生气", "无语", "失望", "算了",
        "不想理", "笨蛋", "蠢", "差劲", "恶心")


_NEG_WORDS = ("不", "没", "别", "讨厌", "不要", "不想")


def gc_of(text):
    """用户这句话对她是好是坏（带否定词处理，否则「你不喜欢我」会被当成夸）"""
    t = str(text or "")
    pos = sum(1 for w in _POS if w in t)
    neg = sum(1 for w in _NEG if w in t)
    # 否定词 + 正向词 => 翻成负向（如「不喜欢」「不开心」「别谢」）
    for w in _POS:
        if w in t:
            idx = t.find(w)
            if idx > 0 and any(t[max(0, idx - 2):idx].endswith(x) for x in ("不", "没", "别", "没有")):
                pos -= 1
                neg += 1
    if pos and not neg:
        return 0.62
    if neg and not pos:
        return -0.55
    if pos and neg:
        return 0.05
    return 0.1


def write_psy_snapshot(s, energy, heard, energy_raw=None):
    """把 BEAM 真值写成 psyche 能读的快照（她的嘴和框架吃同一份数据）"""
    try:
        SNAP_FOR_PSY.parent.mkdir(parents=True, exist_ok=True)
        snap = {
            "ts": time.time(),
            "valence": float(s.get("valence") or 0),
            "arousal": float(s.get("arousal") or 0.3),
            "dominance": float(s.get("dominance") or 0.5),
            "fatigue": float(s.get("fatigue") or 0),
            "stress": float(s.get("stress") or 0),
            "bonding": float(s.get("bonding") or 0),
            "label": str(s.get("label") or ""),
            "outlet": str(s.get("outlet") or ""),
            "energy": round(float(energy), 1),
            "energy_raw": round(float(energy_raw if energy_raw is not None else energy), 1),
            "brevity": float(s.get("brevity") or 0.5),
            "warmth": float(s.get("warmth") or 0.5),
            "playfulness": float(s.get("playfulness") or 0.3),
            "talkative": float(s.get("talkative") or 0.5),
            "heard": heard,
            "source": "beam_gateway",
        }
        tmp = str(SNAP_FOR_PSY) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False, indent=1)
        import os as _os
        _os.replace(tmp, SNAP_FOR_PSY)
    except Exception as e:
        log("snap write err: %s" % e)


def _touch():
    _ST_REQ["last"] = time.time()


def rest_bonus():
    """空闲休息补偿：没人找她超过 5 分钟后，能量按分钟缓慢恢复（上限 15 点）"""
    idle_min = (time.time() - _ST_REQ["last"]) / 60.0
    if idle_min <= 5:
        return 0.0
    return min(15.0, (idle_min - 5) * 0.8)


_MUSIC_WORDS = ("音乐", "乐器", "歌声", "唱歌", "旋律", "钢琴", "吉他", "鼓", "乐队")
_NOISE_WORDS = ("噪音", "尖叫", "警报", "争吵", "喊叫", "哭声", "骂", "碰撞", "爆炸")


def feed_audition_emotion():
    """听觉→情绪：新出现的音乐让她放松，噪音让她烦"""
    with _aud_lock:
        _feed_audition_emotion_inner()


def _feed_audition_emotion_inner():
    try:
        if not EVENTS.exists():
            return
        lines = [l for l in EVENTS.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()][-1:]
        for l in lines:
            try:
                d = json.loads(l)
            except Exception:
                continue
            ts = str(d.get("ts") or "")
            if not ts or ts == _ST_AUD["ts"]:
                continue
            _ST_AUD["ts"] = ts
            # [2026-11-05] 过期声音不喂：重启后别把几小时前的旧声音当成"现在听到的"
            try:
                import datetime as _dt
                _age = time.time() - _dt.datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S").timestamp()
                if _age > 600:
                    log("audition->emotion: 旧声音（%.0f 分钟前）不喂" % (_age / 60.0))
                    continue
            except Exception:
                pass
            # [2026-11-05 听力偏好] 外头的声音少听：她能听见，但不给它起波澜
            if _ev_source(d) == "outside":
                log("audition->emotion: 外头的声音（少听，不喂）")
                continue
            tags = " ".join(d.get("sound_tags") or [])
            text = str(d.get("text") or "")
            combo = tags + text
            kind = None
            why = ""
            if any(w in combo for w in _MUSIC_WORDS):
                kind, why = "video", "耳边有音乐"
            elif any(w in combo for w in _NOISE_WORDS):
                kind, why = "error", "听到吵的东西"
            if not kind:
                continue
            # [2026-11-05] 本机声音也节流：音乐整天放着，不能每分钟都喂她一次情绪
            now = time.time()
            if now - _ST_AUD["last_feed"] < AUD_FEED_MIN_GAP_S:
                log("audition->emotion: 节流 %.0fs，不喂" % (now - _ST_AUD["last_feed"]))
                continue
            _ST_AUD["last_feed"] = now
            with _lock:
                kernel.feed(kind, why=why, goal_congruence=AUD_FEED_GC[kind],
                            intensity=AUD_FEED_INTENSITY[kind])
            log("audition->emotion: %s (%s)" % (why, tags[:30]))
    except Exception as e:
        log("audition emotion err: %s" % e)


def sound_line(max_age_min=12):
    """把最近一次音乐理解结果变成一句话（BPM/长度/段落）"""
    try:
        if not SOUND_DETAIL.exists():
            return ""
        lines = [l for l in SOUND_DETAIL.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
        if not lines:
            return ""
        rec = json.loads(lines[-1])
        if time.time() - float(rec.get("ts") or 0) > max_age_min * 60:
            return ""
        rh = rec.get("rhythm") or {}
        st = rec.get("structure") or {}
        bits = []
        bpm = rh.get("bpm")
        if bpm:
            if rh.get("ambiguous"):
                bits.append("约 %.0f BPM（不太确定）" % float(bpm))
            else:
                bits.append("%.0f BPM" % float(bpm))
        secs = st.get("sections") or []
        if secs:
            names = []
            for x in secs[:5]:
                lab = x.get("label") or x.get("name") or ""
                if lab:
                    names.append(str(lab))
            if names:
                bits.append("结构：" + "→".join(names))
        if not bits:
            return ""
        return "你耳朵刚听到的音乐：" + "，".join(bits)
    except Exception:
        return ""


def recent_monologue(max_age_min=90):
    """取最近一条内心独白（过期就不带）"""
    try:
        if not MONO.exists():
            return ""
        lines = [l for l in MONO.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
        if not lines:
            return ""
        rec = json.loads(lines[-1])
        if time.time() - float(rec.get("ts") or 0) > max_age_min * 60:
            return ""
        return str(rec.get("text") or "").strip()
    except Exception:
        return ""


def write_monologue():
    """没人说话时，她写一段没发出去的心里话"""
    try:
        body, heard, energy = read_state()
        with _lock:
            s = kernel.snapshot() or {}
        label = str(s.get("label") or "")
        valence = float(s.get("valence") or 0)
        arousal = float(s.get("arousal") or 0)
        mood_cn = {"开心": "心里亮堂", "黏人": "想凑近", "平静": "淡淡的", "紧张": "绷着",
                   "低落": "发闷", "难过": "发闷", "生气": "有火", "委屈": "发酸", "困": "困"}.get(label, "说不上来")
        prompt = ("你是大肥鱼，鲸鱼少女，主人现在不在旁边（没人跟你说话）。\n"
                  "此刻真实的你：\n"
                  "- 能量 %.0f/100\n"
                  "- 心情：%s（感觉%s，唤醒%.2f）\n"
                  "- 刚听到：%s\n\n"
                  "写一段你此刻的心里话（100 字以内，第一人称，像发呆时自言自语）。\n"
                  "要求：不要报数字，不要提\"状态/注入/数据\"。\n"
                  "可以想：你自己今天的事（在做什么、看到了什么、吃了什么、待会儿想干嘛）、刚才听到的声音、犯困、胡思乱想。\n"
                  "耳朵偏好：本机的声音（电脑里放的）是身边的声音；外头的动静只当远处背景，少往心里去。\n"
                  "注意：不要总在等主人、想主人——你有自己的生活，主人只是生活的一部分。\n"
                  "只输出这段话本身。" % (energy, label or "说不上来", mood_cn, arousal, heard or "没什么声音"))
        resp = call_upstream({"model": "deepseek-chat",
                              "messages": [{"role": "user", "content": prompt}],
                              "max_tokens": 200, "temperature": 1.15})
        text = (resp.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
        if not text:
            return ""
        rec = {"ts": time.time(), "time": time.strftime("%H:%M"), "text": text,
               "label": label, "valence": round(valence, 3), "energy": round(energy, 1), "heard": heard}
        with open(MONO, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        log("monologue: %s" % text[:50])
        return text
    except Exception as e:
        log("monologue err: %s" % e)
        return ""


def maybe_monologue():
    """空闲超过 15 分钟才写，每天最多 6 条，两条间隔至少 25 分钟"""
    try:
        idle_min = _ST_IDLE["hours"] * 60.0   # 真实对话空闲（不是 LLM 请求空闲）
        if idle_min < 15:
            return
        today = time.strftime("%Y-%m-%d")
        n_today = 0
        if MONO.exists():
            for l in MONO.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    _d = json.loads(l)
                except Exception:
                    continue
                try:
                    if time.strftime("%Y-%m-%d", time.localtime(float(_d.get("ts") or 0))) == today:
                        n_today += 1
                except Exception:
                    continue
        if n_today >= 6:
            return
        if time.time() - _ST_MONO["last"] < 25 * 60:
            return
        _ST_MONO["last"] = time.time()
        write_monologue()
    except Exception as e:
        log("maybe_monologue err: %s" % e)


def send_qq(text):
    """直接通过 OneBot HTTP 给她发 QQ 私聊"""
    try:
        payload = json.dumps({"user_id": OWNER_QQ, "message": text}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(ONEBOT + "/send_private_msg", data=payload,
                                     headers={"Content-Type": "application/json",
                                              "Authorization": "Bearer " + ONEBOT_TOKEN},
                                     method="POST")
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        log("send_qq err: %s" % e)
        return None


def record_to_astrbot(text):
    """把网关直发的消息写进 AstrBot 历史，让她记得自己发过"""
    try:
        import sqlite3
        from datetime import datetime
        db = r"%ASTRBOT_HOME%\data\data_v4.db"
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
        conn = sqlite3.connect(db, timeout=5)
        conn.execute(
            "INSERT INTO platform_message_history (created_at, updated_at, platform_id, user_id, sender_id, sender_name, content) VALUES (?,?,?,?,?,?,?)",
            (now, now, "snowluma", "100000000", "100000001", "bot",
             json.dumps({"type": "bot", "message": [{"type": "plain", "text": text}]}, ensure_ascii=False)))
        conn.commit()
        conn.close()
    except Exception as e:
        log("record_to_astrbot err: %s" % e)


VOICE_GATE_FILE = Path(
    r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_whale_voice\voice_gate.json"
)
_REACH_SKIP_LOG_AT = 0.0


def _astrbot_online(timeout=0.6):
    """AstrBot WebUI(6185) 能不能连上；连不上就认为它离线，网关才兜底。"""
    try:
        with socket.create_connection(("127.0.0.1", 6185), timeout=timeout):
            return True
    except Exception:
        return False


def _voice_gate_allows(outlet="proactive"):
    """只读既有闸门；读不到/读不懂一律放行（fail-open）。

    与 proactive_chat 的 shim 保持同一语义：过期放行、影子放行、
    outlets 显式关闭则拒绝、enforce+deny 则拒绝。
    """
    try:
        gate = json.loads(VOICE_GATE_FILE.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return True, "gate_missing"
    except Exception:
        return True, "gate_corrupt"
    if not isinstance(gate, dict):
        return True, "gate_corrupt"
    try:
        if time.time() > float(gate.get("expires")):
            return True, "gate_stale"
    except Exception:
        return True, "gate_corrupt"
    decision = gate.get("decision")
    if decision not in ("allow", "deny"):
        return True, "gate_corrupt"
    outlets = gate.get("outlets")
    if isinstance(outlets, dict) and outlets.get(outlet) is False:
        return False, "outlet_deny:" + str(gate.get("code") or "")
    if gate.get("enforce") is not True:
        return True, "shadow"
    if decision == "deny":
        return False, "deny:" + str(gate.get("code") or "")
    return True, "allow"


def maybe_reach_out(dry=False):
    """情绪驱动主动消息：想凑近 + 闲着没人理 + 今天还没找过 -> 主动发 QQ 一句。

    [2026-11-05 听力偏好与发言频率] 主人："听完发信息不要那么频，外界的声音少听，本机多听"。
      - 冷却 45->90 分钟，每日上限 4->3 条（按时间戳算，跨重启有效）；
      - 上一条是"刚听到…"这种，下一次至少隔 3 小时；
      - 提示词明说：不许拿"听到了什么"当发消息的理由，也别总汇报声音。

    [2026-11-07 出口唯一化] AstrBot 在线时网关**不再直发**：
      AstrBot 那边有内核出站队列 + voice_gate 统一决策，网关再发就是第二个源。
      只有 AstrBot 离线（如重启中）才由网关兜底，且同样尊重 voice_gate 的否决。
    """
    global _REACH_SKIP_LOG_AT
    try:
        idle_min = _ST_IDLE["hours"] * 60.0   # 真实对话空闲
        with _lock:
            st = kernel.snapshot() or {}
        if not dry:
            if _astrbot_online():
                # 每分钟都会走到这里，半小时记一次就够了，别刷日志
                if time.time() - _REACH_SKIP_LOG_AT > 1800:
                    _REACH_SKIP_LOG_AT = time.time()
                    log("reach_out: AstrBot 在线，主动消息交给内核队列，网关不直发")
                return
            _gate_ok, _gate_why = _voice_gate_allows()
            if not _gate_ok:
                log("reach_out: 闸门拒绝（%s），不发" % _gate_why)
                return
            if idle_min < 20:
                return
            outlet = str(st.get("outlet") or "")
            bond = float(st.get("bonding") or 0)
            # 只有"想凑近"或联结高才主动找你
            if outlet != "connect" and bond < 0.6:
                return
            today = time.strftime("%Y-%m-%d")
            recs = _recent_outreach(50)
            n_today = 0
            last_msg_ts = 0.0
            last_sound_ts = 0.0
            for d in recs:
                try:
                    ts = float(d.get("ts") or 0)
                except Exception:
                    continue
                last_msg_ts = max(last_msg_ts, ts)
                if time.strftime("%Y-%m-%d", time.localtime(ts)) != today:
                    continue
                n_today += 1
                if _is_sound_msg(d.get("text")):
                    last_sound_ts = max(last_sound_ts, ts)
            if n_today >= OUT_DAILY_CAP:
                return
            # 静音时段不主动发（夜里别吵人；聊天回复不受影响）
            _lt = time.localtime()
            _hour = _lt.tm_hour + _lt.tm_min / 60.0
            if _hour >= OUT_QUIET_HOURS[0] or _hour < OUT_QUIET_HOURS[1]:
                return
            now = time.time()
            last_any = max(last_msg_ts, float(_ST_OUT.get("last") or 0.0))
            if now - last_any < OUT_COOLDOWN_MIN * 60:
                return
            # 上一条是在说"听到…"：再等久一点（听完别急着发）
            if last_sound_ts and now - last_sound_ts < OUT_SOUND_COOLDOWN_MIN * 60:
                log("reach_out: 上一条是听觉话题，冷却中（%.0f 分钟）" % ((now - last_sound_ts) / 60.0))
                return
        body, heard, energy = read_state()
        if not dry and energy < 25:   # 太累不打扰
            return
        if not dry:
            _ST_OUT["last"] = time.time()   # 先记冷却，避免上游失败时每分钟重试
        label = str(st.get("label") or "")
        prompt = ("你是大肥鱼，鲸鱼少女。主人已经 %d 分钟没理你了。\n"
                  "你此刻：能量 %.0f/100，心情 %s，很想凑近他（connect）。\n"
                  "耳边现在是：%s\n\n"
                  "写一条你想主动发给他的消息（30 字以内，像发微信那样，自然、有温度）。\n"
                  "内容写：你自己的小事（在干嘛/吃了什么/看到什么有趣的东西），或者单纯喊他一声。\n"
                  "⚠ 不要因为你听到了什么就发消息——声音只是背景，不是你找他的理由（除非他在叫你）。"
                  "也别每次都在汇报声音，别总用「刚听到/耳边/音乐」开头。\n"
                  "注意：不要每次都是「主人～蹭蹭你」「想你了」这种话。只输出这句话。" % (
                      int(idle_min), energy, label or "说不上来", heard or "没什么特别的声音"))
        resp = call_upstream({"model": "deepseek-chat",
                              "messages": [{"role": "user", "content": prompt}],
                              "max_tokens": 120, "temperature": 1.2})
        text = (resp.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
        if not text:
            return
        text = text.strip('"').strip()
        if dry:
            log("reach_out dry: %s" % text[:40])
            return text
        r = send_qq(text)
        if r and r.get("status") == "ok":
            record_to_astrbot(text)
        rec = {"ts": time.time(), "time": time.strftime("%H:%M"), "text": text,
               "idle_min": round(idle_min, 1), "outlet": str(st.get("outlet") or ""),
               "bond": float(st.get("bonding") or 0), "energy": round(energy, 1),
               "sound_based": _is_sound_msg(text),
               "sent": bool(r and r.get("status") == "ok"), "resp": r}
        with open(OUTREACH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        log("reach_out: %s (sent=%s)" % (text[:40], rec["sent"]))
    except Exception as e:
        log("reach_out err: %s" % e)


def inject(payload):
    _touch()
    # [2026-11-05 诊断] 看上游请求里到底带没带工具（临时探针，问题解决后删）
    try:
        _tl = payload.get("tools") or []
        _names = [str((x.get("function") or {}).get("name") or "") for x in _tl if isinstance(x, dict)]
        log("probe: payload tools=%d, has whale_meta_draw=%s, names_head=%s" % (
            len(_names), "whale_meta_draw" in _names, ",".join(_names)))
    except Exception as _e:
        log("probe err: %s" % _e)
    msgs = payload.get("messages") or []
    if not isinstance(msgs, list) or not msgs:
        return payload
    b = block(msgs)
    if not b:
        return payload
    msgs = list(msgs)
    # 柔：塞在 system 里（没有就新建）
    if msgs and msgs[0].get("role") == "system":
        c = msgs[0].get("content")
        if isinstance(c, str):
            msgs[0] = {"role": "system", "content": c + "\n\n" + b}
        else:
            msgs.insert(0, {"role": "system", "content": b})
    else:
        msgs.insert(0, {"role": "system", "content": b})
    payload["messages"] = msgs
    STATS["injected"] += 1
    return payload


def call_upstream(payload, tries=3):
    """调上游，断连/超时自动重试（指数退避）"""
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(
                UPSTREAM + "/chat/completions",
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json", "Authorization": "Bearer " + API_KEY},
                method="POST")
            with urllib.request.urlopen(req, timeout=90) as r:  # [反射优化] 180->90，慢上游快速失败
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8")
            except Exception:
                body = str(e)
            last = (e.code, body)
            log("upstream HTTP %d (try %d/%d): %s" % (e.code, i + 1, tries, body[:200]))
            if e.code in (400, 401, 403, 404, 422):
                raise
            time.sleep(1.0 + i)
        except Exception as e:
            last = e
            log("upstream err (try %d/%d): %s" % (i + 1, tries, str(e)[:200]))
            time.sleep(1.0 + i)
    raise RuntimeError("upstream failed after %d tries: %s" % (tries, last))


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            self.close_connection = True
        except Exception:
            self.close_connection = True

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _debug_ok(self):
        """[2026-11-04 安全加固] /debug* 的 token 校验；未配置 DEBUG_TOKEN 时保持旧行为。"""
        if not DEBUG_TOKEN:
            return True
        if (self.headers.get("X-Debug-Token") or "") == DEBUG_TOKEN:
            return True
        try:
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            return (q.get("token") or [""])[0] == DEBUG_TOKEN
        except Exception:
            return False

    def do_GET(self):
        if self.path.startswith("/debug/") and not self._debug_ok():
            self._send(403, {"error": "debug token required"})
            return
        if self.path.startswith("/v1/models"):
            self._send(200, {"object": "list", "data": [{"id": "deepseek-chat", "object": "model"}]})
        elif self.path.startswith("/debug/reach_out"):
            _ST_IDLE["hours"] = 0.35   # 模拟空闲21分钟（真实字段）
            _dry = ("dry=1" in self.path) or ("dry=true" in self.path.lower())
            if _dry:
                t = maybe_reach_out(dry=True)
                self._send(200, {"ok": bool(t), "dry": True, "text": t or ""})
                return
            _ST_OUT["last"] = 0.0
            maybe_reach_out()
            last = ""
            if OUTREACH.exists():
                lines = [l for l in OUTREACH.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
                if lines:
                    last = json.loads(lines[-1]).get("text", "")
            self._send(200, {"ok": bool(last), "text": last, "last_sent": last})
        elif self.path.startswith("/debug/block"):
            _ST_IDLE["hours"] = 0.35
            b = block([{"role": "user", "content": "（调试探针）你在吗"}])
            self._send(200, {"block": b})
        elif self.path.startswith("/debug/monologue"):
            _ST_MONO["last"] = 0.0
            _ST_IDLE["hours"] = 0.35
            t = write_monologue()
            self._send(200, {"ok": bool(t), "text": t[:200]})
        elif self.path.startswith("/health"):
            self._send(200, {"ok": True, "requests": STATS["n"], "injected": STATS["injected"],
                             "uptime_s": round(time.time() - STATS["started"])})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path.startswith("/debug/") and not self._debug_ok():
            self._send(403, {"error": "debug token required"})
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n)
            payload = json.loads(raw.decode("utf-8"))
        except Exception as e:
            return self._send(400, {"error": "bad json: %s" % e})
        STATS["n"] += 1
        try:
            payload = inject(payload)
        except Exception as e:
            log("inject err: %s" % e)
        # 转发（带重试）
        if payload.get("stream"):
            # [2026-11-05] 流式请求：SSE 原样透传（旧逻辑 json.loads 读 SSE 会炸成 502）
            try:
                req = urllib.request.Request(
                    UPSTREAM + "/chat/completions",
                    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    headers={"Content-Type": "application/json", "Authorization": "Bearer " + API_KEY},
                    method="POST")
                _r = urllib.request.urlopen(req, timeout=180)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                while True:
                    _chunk = _r.read(2048)
                    if not _chunk:
                        break
                    self.wfile.write(_chunk)
                    self.wfile.flush()
                _r.close()
                log("stream done: tools=%d" % len(payload.get("tools") or []))
                return
            except Exception as e:
                log("stream err: %s" % e)
                return self._send(502, {"error": "upstream stream: %s" % str(e)[:300]})
        try:
            resp = call_upstream(payload)
        except urllib.error.HTTPError as e:
            try:
                err_body = e.read().decode("utf-8")
            except Exception:
                err_body = str(e)
            return self._send(e.code, json.loads(err_body) if err_body.strip().startswith("{") else {"error": err_body})
        except Exception as e:
            return self._send(502, {"error": "upstream: %s" % str(e)[:300]})
        # 回复喂回内核（关系反馈）
        try:
            txt = ""
            ch = (resp.get("choices") or [{}])[0]
            msg = ch.get("message") or {}
            txt = str(msg.get("content") or "")[:60]
            if txt:
                with _lock:
                    kernel.feed("chat", why="她回了：" + txt, goal_congruence=0.2, intensity=0.4)
                    kernel.tick(body_ctx=read_state()[0])
        except Exception:
            pass
        self._send(200, resp)


def heartbeat_loop():
    """常驻心跳：60 秒一拍，让内核状态连续积累（不调 LLM，只更新状态+快照）"""
    while True:
        try:
            time.sleep(60)
            body, heard, energy = read_state()
            with _lock:
                kernel.tick(body_ctx=body)
                st = kernel.snapshot() or {}
            write_psy_snapshot(st, energy, heard)
            maybe_monologue()
            feed_audition_emotion()
            maybe_reach_out()
        except Exception as e:
            log("heartbeat err: %s" % e)


if __name__ == "__main__":
    # 单实例保护：直接靠 bind 抢占端口。Windows 上 SO_REUSEADDR 会让两个进程
    # 同时绑上 8920（谁都能收包），所以这里关掉 reuse，绑定失败就说明已有实例。
    class Srv(ThreadingHTTPServer):
        allow_reuse_address = False
        daemon_threads = True

    try:
        srv = Srv(("127.0.0.1", PORT), H)
    except OSError as _e:
        print("port %d already in use (%s), exit (another gateway is running)" % (PORT, _e), flush=True)
        log("bind failed on %d: %s" % (PORT, _e))
        sys.exit(0)

    log("gateway start on %d, upstream=%s" % (PORT, UPSTREAM))
    print("BEAM gateway up: http://127.0.0.1:%d/v1  ->  %s" % (PORT, UPSTREAM), flush=True)
    threading.Thread(target=heartbeat_loop, daemon=True).start()
    log("heartbeat started (60s)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass