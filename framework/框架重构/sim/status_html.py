"""生成可视化状态面板（单文件 HTML，浏览器直接打开）。

用法：python status_html.py
输出：22_状态面板.html

[iter36] 新增：
  - 「她为什么安静」一行：按六道闸（总开关→冷却→日限→睡眠→回避→概率）给结论；
  - 「状态变化」一行：与上次观测采样相比 label 是否变化；
  - 归一化显示：bonding 按 DISPLAY_MAX（0.879 = 旧 bond=100 等价点）映射到 100 刻度，
    真实值括注，进度条与数字同一口径。
脚本零第三方依赖。
"""
from __future__ import annotations
import sys

import json
import os
import time
import urllib.request


try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
ROOT = r"%PROJECT_HOME%\框架重构"
OUT = os.path.join(ROOT, "22_状态面板.html")
BEAMS = r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_whale_beams"
VOICE = r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_whale_voice"
CFG = r"%ASTRBOT_HOME%\data\config"
BLOCKS = "▁▂▃▄▅▆▇█"

#: [iter36] 归一化显示上限：bonding 0.879 = 旧 bond=100 等价点（迭代 28 渐进饱和）
DISPLAY_MAX = {"bonding": 0.879}

#: [iter36] voice 闸门 code → 人话（与 whale_voice 插件 CODE_CN 对齐）
CODE_CN = {
    "allow": "允许开口", "disabled": "总开关关着", "quiet_hours": "静默时段",
    "blocked": "日程不可打扰", "daily_cap": "今天主动次数到上限", "no_reason": "没有由头",
    "cold_penalty": "冷落惩罚压着", "low_want": "想说程度不够",
}


def rj(p):
    try:
        with open(p, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def health(port):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/health" % port, timeout=3) as r:
            return json.loads(r.read().decode())
    except Exception:
        return {}


def spark(vals):
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-9:
        return BLOCKS[3] * len(vals)
    return "".join(BLOCKS[max(0, min(7, int((v - lo) / (hi - lo) * 7)))] for v in vals)


def _num(x, default=0.0):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return float(default)
    if v != v:
        return float(default)
    return v


def _norm100(value, ref):
    ref = _num(ref)
    if ref <= 0:
        return 0.0
    return max(0.0, min(100.0, _num(value) / ref * 100.0))


def _in_quiet_window(hour, spec):
    try:
        a, b = str(spec).split("-")
        a, b = int(a), int(b)
    except (TypeError, ValueError):
        return False
    a = 0 if a == 24 else a
    return (a <= hour < b) if a <= b else (hour >= a or hour < b)


def _why_quiet_line(st):
    """[iter36] 六道闸一行结论（与 sim/status_panel.py 同口径，HTML 只放一行）。"""
    beams_cfg = rj(os.path.join(CFG, "astrbot_plugin_whale_beams_config.json"))
    voice_cfg = rj(os.path.join(CFG, "astrbot_plugin_whale_voice_config.json"))
    pro_cfg = rj(os.path.join(CFG, "astrbot_plugin_proactive_chat_config.json"))
    friend = (pro_cfg.get("friend_settings") or {}) if isinstance(pro_cfg, dict) else {}
    sched = friend.get("schedule_settings") or {}
    vstate = rj(os.path.join(VOICE, "voice_state.json"))
    vgate = rj(os.path.join(VOICE, "voice_gate.json"))
    runtime = rj(os.path.join(VOICE, "runtime.json"))
    cons = rj(os.path.join(VOICE, "consequence.json"))
    gate = st.get("gate") or {}
    hour = int(time.localtime().tm_hour)
    now = time.time()
    parts = []

    # ① 总开关
    b_on = bool(beams_cfg.get("enable", True))
    b_mode = str(beams_cfg.get("mode") or "shadow").lower()
    v_on = bool(voice_cfg.get("enable", True))
    v_inject = bool(voice_cfg.get("enable_inject", False)) and not bool(voice_cfg.get("shadow_mode", True))
    p_on = bool(friend.get("enable", True))
    if not (b_on and v_on and v_inject and p_on):
        return "① 总开关 ⛔ 有开关关着/影子模式 → 这是配置层，不是她不想说"
    parts.append("① 总开关✅(%s)" % b_mode)

    # ② 冷却
    cd = _num(voice_cfg.get("reply_window_min"), 30.0)
    last_out = _num(runtime.get("last_outlet_ts")) or _num(runtime.get("last_poke_ts"))
    if last_out > 0 and (now - last_out) / 60.0 < cd:
        return "② 冷却 ⛔ 上次主动出口 %s，冷却窗 %.0f 分钟" % (
            time.strftime("%H:%M", time.localtime(last_out)), cd)
    parts.append("② 冷却✅(%s)" % (time.strftime("%H:%M", time.localtime(last_out)) if last_out else "无出口"))

    # ③ 日限
    cap = _num(voice_cfg.get("daily_cap"), 6.0)
    sent = _num(runtime.get("sent_today"), 0.0)
    if str(runtime.get("day") or "") != time.strftime("%Y-%m-%d"):
        sent = 0.0
    opens_1h = sum(1 for x in (gate.get("recent_opens") or []) if now - _num(x) < 3600.0)
    if (cap > 0 and sent >= cap) or opens_1h >= 18:
        return "③ 日限 ⛔ 今日 %d/%.0f 或近 1 小时开口 %d/18" % (int(sent), cap, opens_1h)
    parts.append("③ 日限✅(%d/%.0f)" % (int(sent), cap))

    # ④ 睡眠
    quiet_hours = str(sched.get("quiet_hours") or "")
    if quiet_hours and _in_quiet_window(hour, quiet_hours):
        return "④ 睡眠 ⛔ 现在 %d 点，在静默时段（%s）" % (hour, quiet_hours)
    parts.append("④ 睡眠✅(%d点)" % hour)

    # ⑤ 回避
    penalty = _num(cons.get("penalty"), _num(vstate.get("penalty"), 0.0))
    ignored = int(_num(cons.get("ignored"), _num((vstate.get("stats") or {}).get("ignored"), 0.0)))
    if penalty >= 1.0 or str(vstate.get("code")) == "cold_penalty":
        return "⑤ 回避 ⛔ 冷落惩罚封顶（被忽略 %d 次）" % ignored
    if penalty >= 0.3 or int(_num(gate.get("ignore_streak"), 0)) > 0:
        parts.append("⑤ 回避⚠️(冷落惩罚%.2f)" % penalty)
    else:
        parts.append("⑤ 回避✅")

    # ⑥ 概率
    v_code = str(vstate.get("code") or vgate.get("code") or "")
    v_dec = str(vstate.get("decision") or vgate.get("decision") or "")
    v_explain = str(vstate.get("explain") or "").strip()
    b_open = bool(gate.get("open"))
    b_prob = _num(gate.get("probability"), 0.0)
    if v_dec and v_dec != "allow":
        cn = CODE_CN.get(v_code, v_code or "?")
        detail = ("：" + v_explain.rstrip("。！？；; ")) if v_explain else ""
        tail = "；内核其实%s(%.2f)" % ("允许她开口" if b_open else "也没开", b_prob)
        return " → ".join(parts) + " → ⑥ 概率⛔[%s]%s%s" % (cn, detail, tail)
    if not b_open:
        return " → ".join(parts) + " → ⑥ 概率⚠️ 这拍没掷中(%.2f)，每 5 秒重掷" % b_prob
    return " → ".join(parts) + " → ⑥ 概率✅ 六道闸都没关死，她此刻能主动开口"


def _state_change_line(obs):
    """[iter36] 与上次观测采样相比 label 是否变化。"""
    if len(obs) < 2:
        return "状态变化：采样不足（至少 2 条观测才有对比）"
    prev, cur = obs[-2], obs[-1]
    a = str(prev.get("label") or "?")
    b = str(cur.get("label") or "?")
    changed = a != b
    try:
        gap = int(_num(cur.get("tick")) - _num(prev.get("tick")))
    except (TypeError, ValueError):
        gap = -1
    return "状态变化：%s → %s（%s；第 %s 拍，距上次采样 %s 拍）" % (
        a, b, "变了" if changed else "未变", cur.get("tick"),
        gap if gap >= 0 else "?")


def main():
    st = rj(os.path.join(BEAMS, "beams_state.json"))
    snap = rj(os.path.join(BEAMS, "beams_snapshot.json"))
    obs = []
    try:
        with open(os.path.join(BEAMS, "beams_obs.jsonl"), "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        obs.append(json.loads(line))
                    except ValueError:
                        pass
    except OSError:
        pass
    obs = obs[-24:]
    fast = st.get("fast") or {}
    slow = st.get("slow") or {}
    arb = st.get("arbitration") or {}
    gate = st.get("gate") or {}
    age = time.time() - float(st.get("ts") or 0)

    # [iter36] 归一化显示：进度条/数字按 display_max 刻度，真实值括注
    bonding = _num(slow.get("bonding"), 0.0)
    ref = DISPLAY_MAX["bonding"]
    bonding_shown = _norm100(bonding, ref)
    bonding_txt = "%d/100（真实 %.2f%s）" % (
        int(round(bonding_shown)), bonding, "；已越过旧刻度上限" if bonding > ref else "")
    quiet_line = _why_quiet_line(st)
    change_line = _state_change_line(obs)

    healths = {"ASR 8913": health(8913), "laya 8901": health(8901), "IndexTTS 8912": health(8912)}
    voice_rows = "".join(
        "<tr><td>%s</td><td>%s</td></tr>" % (k, "✅ ready" if v.get("ok") else "❌ 掉线")
        for k, v in healths.items())
    mood_spark = spark([float(o.get("mood") or 0) for o in obs])
    stress_spark = spark([float(o.get("stress") or 0) for o in obs])
    talk_spark = spark([float(o.get("talkative") or 0) for o in obs])

    html = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>BEAM-AI 状态面板</title><style>
body{background:#f6f2ea;font-family:"Microsoft YaHei",Arial,sans-serif;color:#2b2b2b;margin:0;padding:28px}
.wrap{max-width:1100px;margin:0 auto}
h1{font-size:26px;margin:0 0 4px}
.sub{color:#7a7368;font-size:13px;margin-bottom:18px}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}
.card{background:#fff;border:1px solid #ddd5c6;border-radius:12px;padding:16px}
.card h3{margin:0 0 10px;font-size:15px;color:#5f584e}
.kv{font-size:13px;line-height:1.9}
.big{font-size:22px;font-weight:800}
.ok{color:#2f9e6e}.warn{color:#d99a2b}.bad{color:#d15656}
table{width:100%%;border-collapse:collapse;font-size:13px}
td{padding:4px 6px;border-bottom:1px solid #efe9dd}
.spark{font-family:Consolas,monospace;font-size:18px;letter-spacing:2px;color:#5b8def}
.quiet{font-size:13px;line-height:1.9;color:#4a443c}
</style></head><body><div class="wrap">
<h1>BEAM-AI 造人系统 · 状态面板</h1>
<div class="sub">%s · 内核心跳 %.1fs 前%s</div>
<div class="grid">
<div class="card"><h3>情绪</h3>
<div class="big">%s <span class="kv">效价 %+.2f / 唤醒 %.2f</span></div>
<div class="kv">慢通道：心情 %+.2f · 压力 %.2f · 疲劳 %.2f</div>
<div class="kv">联结（亲密度）：%s</div>
<div class="kv">负性负荷 %.2f %s</div></div>
<div class="card"><h3>动机与闸门</h3>
<div class="big">%s <span class="kv">紧迫 %.2f</span></div>
<div class="kv">闸门：%s · 概率 %.2f · route %s</div>
<div class="kv">原因：%s · 未响应 %d 条</div></div>
<div class="card"><h3>语音四模型</h3>
<table>%s</table>
<div class="kv">IndexTTS 缓存 %s 条 · 显存余 %s MB</div></div>
</div>
<div class="card" style="margin-top:16px"><h3>她为什么安静</h3>
<div class="quiet">%s</div>
<div class="quiet">%s</div>
</div>
<div class="card" style="margin-top:16px"><h3>情绪轨迹（最近 24 个采样）</h3>
<div class="kv">心情 <span class="spark">%s</span></div>
<div class="kv">压力 <span class="spark">%s</span></div>
<div class="kv">话多 <span class="spark">%s</span></div>
</div>
<div class="card" style="margin-top:16px"><h3>接管状态</h3>
<div class="kv">whale_drive（画画/动态）→ outlet_shadow · proactive_chat（主动消息）→ shadow+gate+urgency≥0.55 · dafeiyu_meme（表情包）→ beams_snapshot</div>
<div class="kv">对外契约：outlet=%s · urgency=%.2f · gate_open=%s · route=%s</div>
</div>
</div></body></html>""" % (
        time.strftime("%Y-%m-%d %H:%M:%S"), age, "✅" if age < 30 else "⚠️ 失联",
        fast.get("label"), float(fast.get("valence") or 0), float(fast.get("arousal") or 0),
        float(slow.get("mood_valence") or 0), float(slow.get("stress") or 0),
        float(slow.get("fatigue") or 0),
        bonding_txt,
        float((st.get("safety") or {}).get("neg_load") or 0),
        "（恢复期）" if (st.get("safety") or {}).get("isolation") else "",
        arb.get("winner"), float(arb.get("urgency") or 0),
        "✅开" if gate.get("open") else "关", float(gate.get("probability") or 0),
        gate.get("route"), gate.get("reason"), len(gate.get("pending") or []),
        voice_rows,
        (health(8912) or {}).get("cache_entries"), (health(8912) or {}).get("free_mb"),
        quiet_line, change_line,
        mood_spark, stress_spark, talk_spark,
        snap.get("outlet"), float(snap.get("urgency") or 0),
        snap.get("gate_open"), snap.get("route"),
    )
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(html)
    print("状态面板已生成：" + OUT)
    print("  她为什么安静：" + quiet_line)
    print("  " + change_line)


if __name__ == "__main__":
    main()