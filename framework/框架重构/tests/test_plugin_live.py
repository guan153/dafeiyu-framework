"""验证 live 注入链路（不经过 QQ，直接调插件的 on_llm_request）。

跑法：D:\astrbot-venv\Scripts\python.exe test_plugin_live.py
验证：事件入队 → live 注入 <inner>/<mind> → tick 后情绪真的变化 → shadow 不注入。
"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, r"%ASTRBOT_HOME%")
sys.path.insert(0, r"%ASTRBOT_HOME%\data\plugins")
sys.path.insert(0, r"%PROJECT_HOME%\框架重构")

from astrbot_plugin_whale_beams.main import WhaleBeams  # noqa: E402
from beams_core.kernel import BeamsKernel  # noqa: E402


class FakeEvent:
    def __init__(self, text="", private=True, sender="100000000", platform="aiocqhttp"):
        self.message_str = text
        self._private = private
        self._sender = sender
        self._platform = platform

    def is_private_chat(self):
        return self._private

    def get_sender_id(self):
        return self._sender

    def get_platform_name(self):
        return self._platform


class FakeReq:
    def __init__(self):
        self.prompt = "SYSTEM PROMPT"
        self.extra_user_content_parts = None


def make_plugin(mode="live", mind_mode="mini"):
    inst = object.__new__(WhaleBeams)
    inst.config = {"enable": True, "mode": mode, "mind_mode": mind_mode, "tick": 5.0}
    tmp = tempfile.mkdtemp(prefix="beams_live_")
    inst.kernel = BeamsKernel(os.path.join(tmp, "s.json"), autosave=False)
    inst._round = 0
    return inst


def main():
    inst = make_plugin("live", "mini")
    req = FakeReq()
    asyncio.run(inst.on_llm_request(FakeEvent("宝宝我回来了，抱抱"), req))
    assert inst.kernel.pending() == 1, "消息事件没有喂进内核"
    assert "<inner>" in req.prompt, "live 模式没有注入 <inner>"
    assert inst._round == 1
    print("[1] live 注入 OK：\n" + req.prompt.split("SYSTEM PROMPT")[1].strip())

    before = inst.kernel.state["fast"]["valence"]
    inst.kernel.tick(dt_s=5.0)
    after = inst.kernel.state["fast"]["valence"]
    assert after > before, "正向消息没有抬高效价：%.3f -> %.3f" % (before, after)
    print("[2] 事件生效 OK：fast.valence %.3f -> %.3f" % (before, after))

    inst2 = make_plugin("shadow")
    req2 = FakeReq()
    asyncio.run(inst2.on_llm_request(FakeEvent("你好"), req2))
    assert req2.prompt == "SYSTEM PROMPT", "shadow 模式不该注入"
    assert inst2.kernel.pending() == 1, "shadow 模式也要喂事件"
    print("[3] shadow 不注入 OK（事件仍然入队）")

    inst3 = make_plugin("live", "full")
    req3 = FakeReq()
    asyncio.run(inst3.on_llm_request(FakeEvent("在吗"), req3))
    assert "<mind>" in req3.prompt, "full 模式没有注入 <mind>"
    inject_len = len(req3.prompt.split("SYSTEM PROMPT")[1].strip())
    assert inject_len < 1500, "full 注入过长"
    print("[4] full 注入 OK（%d 字）" % inject_len)

    print("\nALL OK：live 注入链路已验证")


if __name__ == "__main__":
    main()