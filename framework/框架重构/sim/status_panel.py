"""造人系统状态面板：一条命令看全（内核/语音/接管/记忆/闸门）。

用法：python status_panel.py

[iter36] 新增：
  - 【她现在为什么安静】按六道闸（总开关→冷却→日限→睡眠→回避→概率）逐条解释，
    数据源全部是只读 JSON：beams_state.json / beams_state_traits.json / 声线 voice_* /
    data/config 下的三份插件配置；读不到就标「不可读」，绝不猜。
  - 归一化显示：依恋类存量 bonding 用 DISPLAY_MAX（0.879 = 旧 bond=100 等价点）
    映射成 100 刻度再显示，真实值用括号标注——数字与进度条同一口径。
脚本零第三方依赖、可直接跑。
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
PID = r"%ASTRBOT_HOME%\data\plugin_data"
CFG = r"%ASTRBOT_HOME%\data\config"
BEAMS = os.path.join(PID, "astrbot_plugin_whale_beams")
STATE = os.path.join(BEAMS, "beams_state.json")
SNAP = os.path.join(BEAMS, "beams_snapshot.json")
MEM = os.path.join(BEAMS, "beams_state_memory.jsonl")
CMP = os.path.join(BEAMS, "outlet_compare_at_dialog.jsonl")
VOICE = os.path.join(PID, "astrbot_plugin_whale_voice")

#: [iter36] 归一化显示：依恋类存量 bonding 的显示上限（0.879 = 旧 bond=100 等价点，
#: 迭代 28 渐进饱和曲线在 bond=100 时落到 ~0.879）。进度条/百分比都按这个刻度走，
#: 真实值在括号里标注，避免「数字 0.92 和进度 100% 打架」。
DISPLAY_MAX = {"bonding": 0.879}


def _rj(p):
    try:
        # utf-8-sig：data/config 下的配置是带 BOM 的 UTF-8，状态文件不带 BOM 也兼容
        with open(p, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _health(port):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/health" % port, timeout=4) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as exc:
        return {"ok": False, "err": str(exc)[:40]}


def _count(p):
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return sum(1 for line in fh if line.strip())
    except OSError:
        return 0


def _num(x, default=0.0):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return float(default)
    if v != v:  # NaN
        return float(default)
    return v


def _bar(pct, width=10):
    """0-100 → 字符进度条（与显示数字同一口径）。"""
    filled = int(round(max(0.0, min(100.0, pct)) / 100.0 * width))
    return "█" * filled + "░" * (width - filled)


def _norm100(value, ref):
    """按 display_max 归一到 0-100（超上限封顶 100）。"""
    ref = _num(ref)
    if ref <= 0:
        return 0.0
    return max(0.0, min(100.0, _num(value) / ref * 100.0))


def _ago_cn(seconds):
    sec = _num(seconds, -1.0)
    if sec < 0:
        return "未知"
    if sec < 90:
        return "%.0f 秒前" % sec
    if sec < 5400:
        return "%.0f 分钟前" % (sec / 60.0)
    if sec < 172800:
        return "%.1f 小时前" % (sec / 3600.0)
    return "%.1f 天前" % (sec / 86400.0)


def _mins(seconds):
    return max(0.0, _num(seconds, 0.0)) / 60.0


def _in_quiet_window(hour, spec):
    """静默时段判定：支持 '24-9' / '23-8' / '0-6' 这类跨零点写法。"""
    try:
        a, b = str(spec).split("-")
        a, b = int(a), int(b)
    except (TypeError, ValueError):
        return False, "未配置"
    a = 0 if a == 24 else a
    if a <= b:
        inside = a <= hour < b
    else:
        inside = hour >= a or hour < b
    return inside, "%s-%s 点" % (spec.split("-")[0], b)


#: [iter36] voice 闸门的 code → 人话（与 whale_voice 插件 CODE_CN 对齐）
VOICE_CODE_CN = {
    "allow": "允许开口",
    "disabled": "总开关关着",
    "quiet_hours": "静默时段",
    "blocked": "日程不可打扰",
    "daily_cap": "今天主动次数到上限",
    "no_reason": "没有由头",
    "cold_penalty": "冷落惩罚压着",
    "low_want": "想说程度不够",
}


def _why_quiet(st, now):
    """[iter36] 按六道闸顺序解释「她现在为什么安静」。

    返回 (rows, conclusion)：
      rows = [(序号, 闸名, 状态符号, 说明)]，状态符号 ∈ {✅ 没卡, ⚠️ 有压力, ⛔ 卡住, ❓ 数据不可读}
    """
    beams_cfg = _rj(os.path.join(CFG, "astrbot_plugin_whale_beams_config.json"))
    voice_cfg = _rj(os.path.join(CFG, "astrbot_plugin_whale_voice_config.json"))
    pro_cfg = _rj(os.path.join(CFG, "astrbot_plugin_proactive_chat_config.json"))
    pro_friend = (pro_cfg.get("friend_settings") or {}) if isinstance(pro_cfg, dict) else {}
    pro_sched = pro_friend.get("schedule_settings") or {}
    voice_state = _rj(os.path.join(VOICE, "voice_state.json"))
    voice_gate = _rj(os.path.join(VOICE, "voice_gate.json"))
    runtime = _rj(os.path.join(VOICE, "runtime.json"))
    cons = _rj(os.path.join(VOICE, "consequence.json"))
    gate = st.get("gate") or {}
    hour = int(time.localtime().tm_hour)
    rows = []

    # ① 总开关
    b_on = bool(beams_cfg.get("enable", True)) if beams_cfg else True
    b_mode = str(beams_cfg.get("mode") or "shadow").lower() if beams_cfg else "?"
    v_on = bool(voice_cfg.get("enable", True)) if voice_cfg else True
    v_inject = bool(voice_cfg.get("enable_inject", False)) if voice_cfg else False
    v_shadow = bool(voice_cfg.get("shadow_mode", True)) if voice_cfg else True
    p_on = bool(pro_friend.get("enable", True)) if pro_friend else True
    if not beams_cfg and not voice_cfg:
        rows.append((1, "总开关", "❓", "配置不可读（%s）" % CFG))
    else:
        reasons = []
        if not b_on:
            reasons.append("内核 enable=false")
        elif b_mode == "shadow":
            reasons.append("内核 shadow 模式（只观测不注入）")
        if not v_on:
            reasons.append("voice enable=false")
        elif not v_inject or v_shadow:
            reasons.append("voice 注入关/影子模式")
        if not p_on:
            reasons.append("主动消息 friend_settings.enable=false")
        if reasons:
            rows.append((1, "总开关", "⛔", "；".join(reasons)))
            status1 = "block"
        else:
            rows.append((1, "总开关", "✅", "内核 %s（mind_mode=%s）/ voice 注入开 / 主动消息开"
                         % (b_mode, beams_cfg.get("mind_mode") or "mini")))
            status1 = "ok"

    # ② 冷却（两次主动出口的最小间隔）
    cooldown_min = _num(voice_cfg.get("reply_window_min"), 30.0) if voice_cfg else 30.0
    last_out = _num(runtime.get("last_outlet_ts"), 0.0) or _num(runtime.get("last_poke_ts"), 0.0)
    if last_out <= 0:
        rows.append((2, "冷却", "✅", "还没有主动出口记录 → 不占冷却"))
        status2 = "ok"
    else:
        since_min = (now - last_out) / 60.0
        stamp = time.strftime("%H:%M", time.localtime(last_out))
        if since_min < cooldown_min:
            rows.append((2, "冷却", "⛔", "上次主动出口 %s（%s），冷却窗 %.0f 分钟，还差 %.0f 分钟"
                         % (stamp, _ago_cn(now - last_out), cooldown_min, cooldown_min - since_min)))
            status2 = "block"
        else:
            rows.append((2, "冷却", "✅", "上次主动出口 %s（%s），冷却窗 %.0f 分钟 → 已过"
                         % (stamp, _ago_cn(now - last_out), cooldown_min)))
            status2 = "ok"

    # ③ 日限 / 频限
    daily_cap = _num(voice_cfg.get("daily_cap"), 6.0) if voice_cfg else 6.0
    sent = _num(runtime.get("sent_today"), 0.0)
    if str(runtime.get("day") or "") != time.strftime("%Y-%m-%d"):
        sent = 0.0  # 跨天重置
    stats = voice_state.get("stats") if isinstance(voice_state.get("stats"), dict) else {}
    if stats:
        sent = max(sent, _num(stats.get("sent_today"), 0.0))
    opens_1h = 0
    for x in (gate.get("recent_opens") or []):
        if now - _num(x, 0.0) < 3600.0:
            opens_1h += 1
    beam_hour_cap = 18
    if daily_cap > 0 and sent >= daily_cap:
        rows.append((3, "日限", "⛔", "今日主动 %d/%.0f 已到上限" % (int(sent), daily_cap)))
        status3 = "block"
    elif opens_1h >= beam_hour_cap:
        rows.append((3, "日限", "⛔", "内核近 1 小时主动开口 %d/18 已到上限" % opens_1h))
        status3 = "block"
    else:
        rows.append((3, "日限", "✅", "今日主动 %d/%.0f；内核近 1 小时主动开口 %d/18"
                     % (int(sent), daily_cap, opens_1h)))
        status3 = "ok"

    # ④ 睡眠 / 静默时段
    quiet_hours = str(pro_sched.get("quiet_hours") or "")
    in_quiet, q_cn = _in_quiet_window(hour, quiet_hours) if quiet_hours else (False, "未配置")
    beam_night = hour in (0, 1, 2, 3, 4, 5, 6)
    beam_lunch = 12 <= hour <= 14
    if in_quiet:
        rows.append((4, "睡眠", "⛔", "现在 %d 点，在主动消息静默时段（%s）里" % (hour, q_cn)))
        status4 = "block"
    else:
        extra = ""
        if beam_night:
            extra = "；内核夜间门禁 0-6 点打折（这拍正是）"
        elif beam_lunch:
            extra = "；内核午睡 12-14 点打折（这拍正是）"
        rows.append((4, "睡眠", "✅", "现在 %d 点，不在静默时段（%s）%s" % (hour, q_cn, extra)))
        status4 = "ok"

    # ⑤ 回避（被忽略 → 冷落惩罚 / 识趣收敛）
    penalty = _num(cons.get("penalty"), _num(voice_state.get("penalty"), 0.0))
    ignored = int(_num(cons.get("ignored"), _num(stats.get("ignored"), 0.0)))
    replied = int(_num(cons.get("replied"), _num(stats.get("replied"), 0.0)))
    ignore_streak = int(_num(gate.get("ignore_streak"), 0.0))
    if penalty >= 1.0 or str(voice_state.get("code")) == "cold_penalty":
        rows.append((5, "回避", "⛔", "冷落惩罚已封顶 %.2f（被忽略 %d 次 / 被接住 %d 次）"
                     % (penalty, ignored, replied)))
        status5 = "block"
    elif penalty >= 0.3 or ignore_streak > 0:
        rows.append((5, "回避", "⚠️", "被忽略 %d 次 / 被接住 %d 次 → 冷落惩罚 %.2f（主动概率×%.2f）%s"
                     % (ignored, replied, penalty, max(0.0, 1.0 - penalty),
                        "，识趣降权已生效" if ignore_streak > 0 else "，还没到封顶")))
        status5 = "warn"
    else:
        rows.append((5, "回避", "✅", "被忽略 %d 次 / 被接住 %d 次 → 冷落惩罚 %.2f"
                     % (ignored, replied, penalty)))
        status5 = "ok"

    # ⑥ 概率 / 由头（voice 闸门 + 内核概率门）
    v_code = str(voice_state.get("code") or voice_gate.get("code") or "")
    v_decision = str(voice_state.get("decision") or voice_gate.get("decision") or "")
    v_explain = str(voice_state.get("explain") or "").strip()
    v_want = _num(voice_state.get("want"), _num(voice_gate.get("want"), 0.0))
    v_thr = _num(voice_state.get("threshold"), _num(voice_gate.get("threshold"), 0.0))
    b_open = bool(gate.get("open"))
    b_prob = _num(gate.get("probability"), 0.0)
    b_reason = str(gate.get("reason") or "")
    b_route = str(gate.get("route") or "")
    voice_txt = ""
    voice_ok = True
    if v_code or v_decision:
        cn = VOICE_CODE_CN.get(v_code, v_code or "?")
        if v_decision and v_decision != "allow":
            voice_ok = False
            voice_txt = "主动消息闸门 deny（%s）" % cn
            if v_explain:
                voice_txt += "：%s" % v_explain.rstrip("。！？；; ")
            else:
                voice_txt += "：want %.2f / 门槛 %.2f" % (v_want, v_thr)
        else:
            voice_txt = "主动消息闸门 allow"
    else:
        voice_ok = False
        voice_txt = "主动消息闸门数据不可读（voice_state/voice_gate 都空）"
    beam_txt = "内核概率门：%s %.2f（%s，route=%s）" % (
        "开" if b_open else "关", b_prob, b_reason or "无原因", b_route or "?")
    if voice_ok and not b_open:
        rows.append((6, "概率", "⚠️", "%s；%s —— 这拍没掷中，每 5 秒会重掷" % (voice_txt, beam_txt)))
        status6 = "warn"
    elif not voice_ok:
        rows.append((6, "概率", "⛔", "%s；%s" % (voice_txt, beam_txt)))
        status6 = "block"
    else:
        rows.append((6, "概率", "✅", "%s；%s" % (voice_txt, beam_txt)))
        status6 = "ok"

    statuses = [status1, status2, status3, status4, status5, status6]
    blocks = [r for r, s in zip(rows, statuses) if s == "block"]
    if blocks:
        first = blocks[0]
        if first[0] == 6:
            _cn = VOICE_CODE_CN.get(v_code, v_code or "?")
            _why = ("：" + v_explain.rstrip("。！？；; ")) if v_explain else ""
            conclusion = ("卡在第六道【概率】—— 主动消息闸门 deny（%s）%s。"
                          "内核这拍其实%s（%.2f），所以不是内核封口，"
                          "是「主动消息」这一关还没过。"
                          % (_cn, _why, "允许她开口" if b_open else "也没开", b_prob))
        else:
            conclusion = "卡在第 %d 道【%s】—— %s。这不是她冷淡，是闸门顺序上这一道先拦住了。" % (
                first[0], first[1], first[3].rstrip("。；; "))
    elif status6 == "warn":
        conclusion = "六道闸都没关死，但这拍她没主动开口：%s" % rows[5][3]
    elif status5 == "warn":
        conclusion = ("六道闸都没关死，她可以主动开口；只是【回避】有冷落压力（%s），"
                      "开口会比平时更收敛。" % rows[4][3])
    else:
        conclusion = "六道闸都没关死——她此刻是能主动开口的；安静只是还没到她想开口的那一下。"
    return rows, conclusion


def main():
    st = _rj(STATE)
    snap = _rj(SNAP)
    now = time.time()
    age = now - float(st.get("ts") or 0)
    print("=" * 56)
    print("造人系统状态面板  ", time.strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 56)

    print("\n【BEAM 内核】")
    print("  tick=%s  最后心跳 %s %s" % (st.get("tick_count"), _ago_cn(age),
                                        "✅" if age < 30 else "⚠️ 失联"))
    fast = st.get("fast") or {}
    slow = st.get("slow") or {}
    arb = st.get("arbitration") or {}
    print("  情绪：%s（效价 %+.2f / 唤醒 %.2f）" % (
        fast.get("label"), float(fast.get("valence") or 0), float(fast.get("arousal") or 0)))
    print("  慢通道：心情 %+.2f / 压力 %.2f / 疲劳 %.2f" % (
        float(slow.get("mood_valence") or 0), float(slow.get("stress") or 0),
        float(slow.get("fatigue") or 0)))
    # [iter36] 归一化显示：进度条/百分比按 display_max 刻度，真实值括注
    bonding = _num(slow.get("bonding"), 0.0)
    ref = DISPLAY_MAX["bonding"]
    shown = _norm100(bonding, ref)
    over = "，已越过旧刻度上限、真实值仍在长" if bonding > ref else ""
    print("  联结（亲密度）：%3d/100 %s（真实 %.3f；刻度上限 %.3f ≈ 旧 bond=100 等价点%s）" % (
        int(round(shown)), _bar(shown), bonding, ref, over))
    print("  动机：%s（紧迫 %.2f）· 次选 %s" % (
        arb.get("winner"), float(arb.get("urgency") or 0),
        (sorted((arb.get("candidates") or {}).items(), key=lambda kv: -float((kv[1] or {}).get("priority") or 0))[1][0]
         if len(arb.get("candidates") or {}) > 1 else "-")))

    # [iter27] 长期人格（天~周级漂移）
    tmeta = _rj(os.path.join(BEAMS, "beams_state_traits.json"))
    tr = tmeta.get("traits") or st.get("traits") or {}
    print("\n【长期人格（天级漂移，不是情绪）】")
    if tr:
        print("  撒娇 %.2f · 话量 %.2f · 幽默 %.2f" % (
            float(tr.get("clinginess") or 0), float(tr.get("verbosity") or 0),
            float(tr.get("humor") or 0)))
        print("  依赖 %.2f · 认真 %.2f · 情绪外化 %.2f" % (
            float(tr.get("dependence") or 0), float(tr.get("diligence") or 0),
            float(tr.get("emotionRate") or 0)))
        last = tmeta.get("last") or {}
        if last:
            print("  上次评估：第 %s 轮（%d 项变化）" % (
                last.get("round"), len(last.get("changes") or [])))
            for c in (last.get("changes") or [])[:3]:
                print("    - %s %.3f→%.3f（%s）" % (
                    c.get("label"), float(c.get("from") or 0),
                    float(c.get("to") or 0), c.get("why")))
        else:
            print("  评估进度：%s 轮（每 8 轮评估一次）" % (tmeta.get("eval_round") or 0))
    else:
        print("  （还没有人格数据，等内核累积到 8 轮对话）")

    # [iter32] 情绪记忆（她最近情绪最深的几刻）——峰记在 whale_state/state.json
    _ws = _rj(os.path.join(PID, "astrbot_plugin_whale_state", "state.json"))
    peaks = _ws.get("emotional_peaks") or st.get("emotional_peaks") or []
    if peaks:
        print("\n【情绪记忆（最近几刻）】")
        for pk in peaks[:3]:
            print("  %s（效价 %+.2f / 唤醒 %.2f）%s" % (
                pk.get("label") or "?", float(pk.get("valence") or 0),
                float(pk.get("arousal") or 0), ("← " + pk["why"]) if pk.get("why") else ""))

    gate = st.get("gate") or {}
    print("\n【开口闸门】")
    print("  open=%s  prob=%.3f  route=%s  原因=%s" % (
        gate.get("open"), float(gate.get("probability") or 0),
        gate.get("route"), gate.get("reason")))
    print("  未响应队列：%d 条" % len(gate.get("pending") or []))

    # [iter36] 她现在为什么安静（六道闸顺序解释）
    try:
        rows, conclusion = _why_quiet(st, now)
        print("\n【她现在为什么安静】")
        for idx, name, sym, detail in rows:
            print("  %s %-4s %s %s" % ("①②③④⑤⑥"[idx - 1], name, sym, detail))
        print("  → 结论：" + conclusion)
    except Exception as exc:  # noqa: BLE001 —— 面板不许因为这段挂掉
        print("\n【她现在为什么安静】")
        print("  ❓ 读取失败（不影响其它段）：%s" % exc)

    print("\n【语音四模型】")
    for name, port in (("ASR", 8913), ("laya", 8901), ("IndexTTS", 8912)):
        h = _health(port)
        if h.get("ok"):
            extra = ""
            if port == 8912:
                extra = " 缓存 %s / 显存 %s MB" % (h.get("cache_entries"), h.get("free_mb"))
            print("  %-8s ✅ ready%s" % (name, extra))
        else:
            print("  %-8s ❌ %s" % (name, h.get("err")))

    print("\n【接管状态】")
    print("  whale_drive    → 读 outlet_shadow（S3.2）")
    print("  proactive_chat → 读 shadow + gate + urgency≥0.55（S3.2）")
    print("  dafeiyu_meme   → 读 beams_snapshot 情绪 + playfulness（S3.2）")

    # [iter34] 开口决策与后果（voice 的 consequence 是唯一真相）
    try:
        cons = _rj(os.path.join(VOICE, "consequence.json"))
        vg = _rj(os.path.join(VOICE, "voice_gate.json"))
        print("\n【开口决策与后果】")
        print("  今日 replied %s / ignored %s / penalty %.2f" % (
            cons.get("replied"), cons.get("ignored"), float(cons.get("penalty") or 0)))
        if vg:
            _vcode = VOICE_CODE_CN.get(vg.get("code"), vg.get("code") or "?")
            print("  上次闸门：want %.2f → %s（%s）" % (
                float(vg.get("want") or 0), vg.get("decision") or vg.get("allow"),
                _vcode + (("：" + (vg.get("reason") or "")[:30]) if vg.get("reason") else "")))
        pend = cons.get("pending") or []
        print("  待结算主动消息：%d 条" % len(pend))
    except Exception:
        pass

    print("\n【记忆与观测】")
    print("  记忆流水：%d 条" % _count(MEM))
    print("  对话时刻出口对比：%d 条" % _count(CMP))
    print("  快照时间：%s" % (time.strftime("%H:%M:%S", time.localtime(float(snap.get("ts") or 0)))))
    print()


if __name__ == "__main__":
    main()