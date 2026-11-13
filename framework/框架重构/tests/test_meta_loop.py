"""端到端验证第 4 层（元认知闭环）：12 轮对话后会不会真的审核/调参/写叙事/更新自我模型。

场景：前 11 轮正常，第 12 轮用户明显变冷（sentiment -0.6）→ 应该判 "too_hot"，
warmth_bias 下调、inhibit_bias 上调、写一条叙事、自我模型置信度下降。
后面再给正向反馈 → 应该判 "ok"，参数不往回猛拉（步长有上限）。
"""
import os
import sys
import tempfile

sys.path.insert(0, r"%PROJECT_HOME%\框架重构")
sys.path.insert(0, r"%ASTRBOT_HOME%\data\plugins")

import beams  # noqa: E402
from beams_core.kernel import BeamsKernel  # noqa: E402

BODY = {"cpu": 0.2, "mem": 0.4, "latency_ms": 100.0, "errors_1h": 0, "disk_free": 0.5,
        "tools": {"net": True, "vision": True, "tts": False, "code": True}}
CTX = {"hour": 20, "pending_tasks": 0.0}


def simulate(kernel, script):
    """script: [(user_sentiment, reply_len)] 每条代表一轮对话。"""
    for idx, (sent, reply_len) in enumerate(script, start=1):
        kernel.feed("chat", goal_congruence=sent, intensity=0.7)
        fb = None
        if idx % 12 == 0:
            fb = {"user_sentiment": sent, "reply_len": reply_len, "ignored": False}
        kernel.tick(body_ctx=BODY, ctx=CTX, dt_s=5.0, round_count=idx, feedback=fb)


def check(name, kernel):
    st = kernel.state
    audit = st["meta"]["audit"]
    narrative = st["meta"]["narrative"]
    sm = st["meta"]["self_model"]
    params = st["params"]
    print("\n=== %s ===" % name)
    print("audit verdict :", audit.get("last_verdict"))
    print("audit history :", len(audit.get("history") or []))
    print("warmth_bias   : %.3f（默认 0）" % params["warmth_bias"])
    print("inhibit_bias  : %.3f（默认 0.15）" % params["inhibit_bias"])
    print("narrative     :", [x.get("text") for x in narrative][:3])
    print("confidence    : %.3f（默认 0.62）" % sm.get("confidence"))
    assert audit.get("history"), "审核历史为空——第 4 层没跑"
    assert audit.get("last_verdict") in ("too_hot", "too_cold", "ok", "too_long", "too_short")
    assert len(narrative) >= 1, "叙事没写"
    return audit.get("last_verdict")


def main():
    tmp = tempfile.mkdtemp(prefix="meta_loop_")
    script = [(0.15, 30)] * 11 + [(-0.6, 120)] + [(0.15, 30)] * 11 + [(0.7, 260)]

    mod = BeamsKernel(os.path.join(tmp, "mod.json"), autosave=False)
    simulate(mod, script)
    verdict_mod = check("模块版", mod)

    single = beams.Kernel(os.path.join(tmp, "single.json"), autosave=False)
    simulate(single, script)
    verdict_single = check("单文件版", single)

    assert verdict_mod == "too_long", "最后一轮（夸 + 回复太长）应判 too_long，实际 %s" % verdict_mod
    assert verdict_single == "too_long", "单文件版判定不一致"
    # 参数确实被调了且没越界
    verdicts = [h.get("verdict") for h in (mod.state["meta"]["audit"].get("history") or [])]
    assert "too_hot" in verdicts, "history 里应该留下 too_hot：%s" % verdicts
    for k in (mod, single):
        assert k.state["params"]["warmth_bias"] <= 0.0
        assert -0.2 <= k.state["params"]["warmth_bias"] <= 0.3
    print("\nOK：元认知闭环在两版上都生效（审核→调参→叙事→自我模型）")


if __name__ == "__main__":
    main()