"""973 条真实聊天记录回放（sim/replay_chat.py）

把过去十几天和她的真实对话喂进 BEAM 内核，看：
1) 情绪轨迹是否跟得上对话（有没有长期低落/尖峰）；
2) 动机分布是否合理（不是一直被一个念头霸占）；
3) 开口闸门的概率与通过率；
4) 元认知审核触发了几次、结论是什么。

时间压缩：消息间隔按 dt 传给内核（最多 3600s），每 5 分钟最多折算 1 拍，
既能保留衰减，又不至于 12 万拍跑一天。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, r"%PROJECT_HOME%\框架重构")
from beams_core import schema  # noqa: E402
from beams_core.kernel import BeamsKernel  # noqa: E402

DB = r"%ASTRBOT_HOME%\data\data_v4.db"
OUT = r"%PROJECT_HOME%\框架重构\15_回放报告.md"
LAYA = "http://127.0.0.1:8901/classify"
BODY = {"cpu": 0.2, "mem": 0.4, "latency_ms": 100.0, "errors_1h": 0, "disk_free": 0.5,
        "tools": {"net": True, "vision": True, "tts": True, "code": True}}
OWNER = "100000000"


def load_messages():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = con.execute(
        "select id, sender_name, content, created_at from platform_message_history order by id asc"
    ).fetchall()
    con.close()
    out = []
    for _id, sender, content, ts in rows:
        try:
            obj = json.loads(content)
        except (ValueError, TypeError):
            continue
        text = ""
        for part in (obj.get("message") or []):
            if part.get("type") == "plain":
                text += str(part.get("text") or "")
        if not text.strip():
            continue
        try:
            dt = datetime.fromisoformat(str(ts)).replace(tzinfo=timezone.utc)
            epoch = dt.timestamp()
        except Exception:
            continue
        out.append({"id": _id, "from": sender, "text": text.strip(), "ts": epoch})
    return out


#: 词表兜底（--laya 才走模型，默认离线快跑）
_NEG = ("烦", "累", "难过", "生气", "不理", "算了", "没空", "困", "哭", "滚", "冷")
_POS = ("开心", "喜欢", "爱", "抱", "乖", "宝", "谢谢", "哈哈", "好棒", "陪", "想你", "可爱")
_STYLE = {"兴奋": "兴奋", "开心": "开心", "撒娇": "撒娇", "平静": "平静",
          "吃醋": "吃醋", "生气": "生气", "疲惫": "疲惫", "难过": "难过"}


def classify(text, use_laya=False):
    if use_laya:
        try:
            req = urllib.request.Request(
                LAYA, data=json.dumps({"text": text}).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=5) as r:
                return json.loads(r.read().decode())
        except Exception:
            pass
    neg = sum(1 for w in _NEG if w in text)
    pos = sum(1 for w in _POS if w in text)
    if neg and not pos:
        return {"label": "难过" if neg >= 2 else "疲惫", "confidence": 0.6}
    if pos and not neg:
        return {"label": "开心" if pos >= 2 else "撒娇", "confidence": 0.6}
    return {"label": "平静", "confidence": 0.45}


import os as _os
USE_LAYA = "--laya" in sys.argv


def main():
    msgs = load_messages()
    print("消息数：", len(msgs))
    if not msgs:
        print("没有可回放的消息")
        return

    import tempfile
    tmp = tempfile.mkdtemp(prefix="beam_chat_")
    k = BeamsKernel(os.path.join(tmp, "s.json"), autosave=False)
    # 初始时间锚定到第一条消息前
    last_ts = msgs[0]["ts"] - 60.0

    winners, gates, audits, moods, stresses = {}, {"open": 0, "total": 0, "reasons": {}}, [], [], []
    user_n = her_n = 0

    for i, m in enumerate(msgs):
        dt = max(5.0, m["ts"] - last_ts)
        last_ts = m["ts"]
        # 折算拍数：每 5 分钟 1 拍，最多 12 拍
        ticks = int(schema.clamp(dt / 600.0, 1, 4))
        dt_s = min(dt / max(1, ticks), 3600.0)
        hour = datetime.fromtimestamp(m["ts"]).hour
        for ti in range(ticks):
            vt = m["ts"] - (ticks - ti) * dt_s
            obs = k.tick(body_ctx=BODY, ctx={"hour": hour, "msgs_last_hour": 6},
                         now=vt, dt_s=dt_s)
        # 场景事件：从对话里推断任务成败 / 被忽略（让 repair/complete 动机有输入）
        txt = m["text"]
        if any(w in txt for w in ("失败", "报错", "崩了", "不通", "错")) and m["from"] != OWNER:
            k.feed("task_fail", goal_congruence=-0.5, intensity=0.7, why=txt[:40])
        elif any(w in txt for w in ("好了", "搞定", "成功", "写完", "做好", "通过")) and m["from"] != OWNER:
            k.feed("task_done", goal_congruence=0.55, intensity=0.6, why=txt[:40])
        if m["from"] == OWNER:
            if dt > 7200:  # 两小时以上没理她
                k.feed("ignored", goal_congruence=-0.5, intensity=0.6, why="两个多小时没回")
            user_n += 1
            emo = classify(m["text"], use_laya=USE_LAYA)
            gc = {"兴奋": 0.7, "开心": 0.6, "撒娇": 0.5, "平静": 0.1,
                  "吃醋": -0.35, "生气": -0.6, "疲惫": -0.4, "难过": -0.7}.get(emo.get("label"), 0.1)
            gc = 0.1 + (gc - 0.1) * min(1.0, float(emo.get("confidence") or 0) / 0.6)
            k.feed("chat", goal_congruence=round(gc, 3),
                   intensity=round(min(1.0, 0.45 + 0.55 * float(emo.get("confidence") or 0)), 3),
                   why=m["text"][:40])
        else:
            her_n += 1
            k.memory_feedback(m["text"][:200], tags=["replay"])
        # 每 12 轮把"这条用户消息的情绪"作为上一轮回复的反馈，喂给 L4
        fb = None
        if m["from"] == OWNER and user_n % 12 == 0 and user_n > 0:
            emo_fb = classify(m["text"], use_laya=USE_LAYA)
            sent = {"兴奋": 0.7, "开心": 0.6, "撒娇": 0.5, "平静": 0.1,
                    "吃醋": -0.35, "生气": -0.6, "疲惫": -0.4, "难过": -0.7}.get(emo_fb.get("label"), 0.1)
            fb = {"user_sentiment": sent, "reply_len": 30, "ignored": False}
        obs = k.tick(body_ctx=BODY, ctx={"hour": hour, "msgs_last_hour": 6},
                     round_count=user_n, feedback=fb, now=m["ts"], dt_s=dt_s)
        winners[obs["drive"]] = winners.get(obs["drive"], 0) + 1
        gates["total"] += 1
        if obs["open"]:
            gates["open"] += 1
            gates["reasons"][obs["gate_reason"]] = gates["reasons"].get(obs["gate_reason"], 0) + 1
        a = k.state["meta"]["audit"]
        if a.get("last_verdict") not in (None, "", "none"):
            if not audits or audits[-1] != a.get("last_verdict"):
                audits.append(a.get("last_verdict"))
        moods.append(obs["mood"])
        stresses.append(obs["stress"])

    st = k.state
    lines = ["# 973 条真实聊天记录回放报告", ""]
    lines.append("- 回放消息：%d 条（主人 %d / 她 %d），时间跨度 %s → %s" % (
        len(msgs), user_n, her_n,
        datetime.fromtimestamp(msgs[0]["ts"]).strftime("%m-%d %H:%M"),
        datetime.fromtimestamp(msgs[-1]["ts"]).strftime("%m-%d %H:%M")))
    lines.append("- 内核推进：%d 拍（时间压缩）" % st["tick_count"])
    lines.append("")
    lines.append("## 一、情绪轨迹")
    lines.append("")
    lines.append("- 心情底色：均值 %+.3f，末值 %+.3f，区间 [%+.2f, %+.2f]" % (
        sum(moods) / len(moods), moods[-1], min(moods), max(moods)))
    lines.append("- 压力：均值 %.3f，峰值 %.3f" % (sum(stresses) / len(stresses), max(stresses)))
    lines.append("- 末态：%s / 联结 %.3f / 疲劳 %.3f / 负性负荷 %.3f" % (
        st["fast"]["label"], st["slow"]["bonding"], st["slow"]["fatigue"],
        st["safety"]["neg_load"]))
    lines.append("")
    lines.append("## 二、动机分布")
    lines.append("")
    total_w = sum(winners.values()) or 1
    for n, c in sorted(winners.items(), key=lambda kv: -kv[1]):
        lines.append("- %-9s %5.1f%%" % (n, 100.0 * c / total_w))
    lines.append("")
    lines.append("## 三、开口闸门")
    lines.append("")
    lines.append("- 评估 %d 次，开门 %d 次（%.1f%%）" % (
        gates["total"], gates["open"], 100.0 * gates["open"] / max(1, gates["total"])))
    for r, c in sorted(gates["reasons"].items(), key=lambda kv: -kv[1])[:6]:
        lines.append("  - %s：%d 次" % (r, c))
    lines.append("")
    lines.append("## 四、元认知审核")
    lines.append("- 触发结论序列：%s" % (" → ".join(audits) if audits else "（未触发）"))
    lines.append("- 叙事条数：%d" % len(st["meta"]["narrative"]))
    lines.append("")
    lines.append("## 五、观察与待调")
    lines.append("1. 如果长时间同一动机霸榜 → 调反失衡的 WARN/FORCE。")
    lines.append("2. 如果心情底色长期趴在负值 → 检查事件标的准确性（laya 分类对反讽不敏感）。")
    lines.append("3. 如果闸门开门率过高/过低 → 调 MAX_OPENS_PER_HOUR 与概率基数。")
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print("\n报告已写入：" + OUT)


if __name__ == "__main__":
    main()