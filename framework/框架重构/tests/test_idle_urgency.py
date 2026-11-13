"""离线验证：久未聊天 → urgency 上涨 → 闸门开门 → 主动消息放行。

这是 S3.2 接管的关键行为验证：她"憋不住"时才主动找你。
"""
import os
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, r"%PROJECT_HOME%\框架重构")
from beams_core import schema, gate  # noqa: E402
from beams_core.kernel import BeamsKernel  # noqa: E402

BODY = {"cpu": 0.2, "mem": 0.4, "latency_ms": 100.0, "errors_1h": 0, "disk_free": 0.5,
        "tools": {"net": True, "vision": True, "tts": True, "code": True}}


def run(hours_idle):
    tmp = tempfile.mkdtemp(prefix="idle_")
    k = BeamsKernel(os.path.join(tmp, "s.json"), autosave=False)
    now = 1_800_000_000.0
    k.state["ts"] = now - 300
    k.state["slow"]["last_positive_ts"] = now - hours_idle * 3600.0
    k.state["slow"]["bonding"] = 0.6
    k.state["slow"]["stress"] = 0.05
    # 空转若干拍（每 5 分钟一拍，模拟安静的时间）
    for i in range(6):
        obs = k.tick(body_ctx=BODY, ctx={"hour": 20}, now=now + i * 300, dt_s=300.0)
    st = k.state
    probe = gate.evaluate(st, trigger="ambient", ctx={"hour": 20})
    return {
        "hours_idle": hours_idle,
        "winner": st["arbitration"]["winner"],
        "urgency": round(schema.finite(st["arbitration"].get("urgency"), 0.0), 3),
        "gate_prob": round(schema.finite(probe.get("probability"), 0.0), 3),
        "gate_open": bool(probe.get("open")),
        "route": probe.get("route"),
    }


def main():
    print("久未聊天 → 紧迫度/闸门（每 5 分钟一拍，模拟安静时间）")
    print("%-8s %-10s %-9s %-10s %-8s %s" % ("闲置", "winner", "urgency", "gate_prob", "gate_open", "route"))
    for h in (0.1, 0.5, 1, 2, 4, 8, 12):
        r = run(h)
        print("%-8s %-10s %-9s %-10s %-8s %s" % (
            "%gh" % h, r["winner"], r["urgency"], r["gate_prob"],
            "✅开" if r["gate_open"] else "关", r["route"]))
    print("\n判定标准：")
    print("- 闲置越久 urgency 越高（connect 动机累积）")
    print("- urgency ≥0.55 才允许 proactive 主动消息")
    print("- 早晨/深夜有门禁折扣（这是设计）")


if __name__ == "__main__":
    main()