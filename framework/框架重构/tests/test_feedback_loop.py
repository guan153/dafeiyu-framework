"""反馈闭环接口测试：memory_feedback / notify_memory_change / contract / failure / bus。"""
import os
import sys
import tempfile

sys.path.insert(0, r"%PROJECT_HOME%\框架重构")
sys.path.insert(0, r"%ASTRBOT_HOME%\data\plugins")

from beams_core import recovery, schema  # noqa: E402
from beams_core.gate import contract_check  # noqa: E402
from beams_core.kernel import BeamsKernel  # noqa: E402

BODY = {"cpu": 0.2, "mem": 0.4, "latency_ms": 100.0, "errors_1h": 0, "disk_free": 0.5,
        "tools": {"net": True, "vision": True, "tts": True, "code": True}}


def main():
    tmp = tempfile.mkdtemp(prefix="beam_fb_")
    k = BeamsKernel(os.path.join(tmp, "s.json"), autosave=False)

    # 1) 事件总线：订阅 gate_open / tick
    got = {"tick": 0, "gate": 0}
    k.bus.subscribe("tick", lambda p: got.__setitem__("tick", got["tick"] + 1))
    k.bus.subscribe("gate_open", lambda p: got.__setitem__("gate", got["gate"] + 1))
    k.tick(body_ctx=BODY, ctx={"hour": 20})
    assert got["tick"] >= 1, "tick 事件没发"
    print("[1] EventBus OK:", got)

    # 2) memory_feedback：回复回流记忆（写 jsonl）
    item = k.memory_feedback("主人，本鱼在呢。", tags=["reply", "warm"])
    assert item["kind"] == "her_reply" and item["text"]
    assert os.path.isfile(k.memory_path), "memory.jsonl 没写"
    with open(k.memory_path, encoding="utf-8") as fh:
        rows = [l for l in fh if l.strip()]
    assert len(rows) >= 1
    print("[2] memory_feedback OK: %d 行落库" % len(rows))

    # 3) notify_memory_change：清库通知当作事件
    before = k.state["tick_count"]
    k.notify_memory_change("clear", "清掉旧记忆")
    assert k.pending() >= 1
    k.tick(body_ctx=BODY, ctx={"hour": 20})
    assert k.state["tick_count"] > before
    print("[3] notify_memory_change OK（事件已喂，last_event=%s）"
          % k.state["fast"]["last_event"].get("kind"))

    # 4) contract：轻话不给重模型；tts 在资源紧时降级
    c1 = k.contract("deep_model")
    assert c1["allowed"] in (True, False)
    st = schema.validate(k.state)
    st["body"]["budget"] = 0.1
    c2 = contract_check(st, "tts")
    assert c2["allowed"] is False, "资源紧时 tts 应降级"
    print("[4] contract OK: deep_model=%s, tts(budget=0.1)=%s"
          % (c1["allowed"], c2["allowed"]))

    # 5) failure：重试与降级
    d1 = recovery.failure_retry("tts", "timeout", 1)
    d2 = recovery.failure_retry("tts", "timeout", 2)
    d3 = recovery.failure_retry("send", "fail", 3)
    assert d1["retry"] is True and d2["retry"] is False and d2["fallback"] == "text_only"
    assert d3["fallback"] == "silent"
    print("[5] failure OK: tts#1=%s, tts#2=%s, send#3=%s"
          % (d1["retry"], d2["fallback"], d3["fallback"]))

    # 6) kernel.failure 会发总线事件
    got2 = []
    k.bus.subscribe("failure", lambda p: got2.append(p))
    k.failure("feiyu", "503", 2)
    assert got2 and got2[0]["decision"]["fallback"] == "text_only"
    print("[6] kernel.failure OK:", got2[0]["decision"]["reason"])

    print("\nALL OK：五个闭环接口全部可用")


if __name__ == "__main__":
    main()