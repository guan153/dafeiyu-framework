# -*- coding: utf-8 -*-
"""脸层插件的「真环境」集成测试（必须用 AstrBot 的 venv 跑）。

和 test_face_block.py 的分工：
- 那边测纯函数（任何 python 都能跑）；
- 这边测**插件本体**：滤镜、配置、owner 判断、真的往 req 里塞 TextPart、写 stats。

跑法：
    %ASTRBOT_HOME%\.venv\Scripts\python.exe tests\test_face_plugin_live.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

DATA = Path(r"%ASTRBOT_HOME%\data")
PLUGINS = DATA / "plugins"
for p in (str(DATA), str(PLUGINS)):
    if p not in sys.path:
        sys.path.insert(0, p)

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + ("" if cond else f"  ← {detail}"))


def make_plugin(tmpdir: Path):
    from astrbot_plugin_whale_face.main import WhaleFacePlugin

    obj = object.__new__(WhaleFacePlugin)
    obj.config = {"enabled": True, "owner_qq": "100000000", "budget": 240, "salt_period_min": 90}
    obj.data_dir = tmpdir
    obj._stats_file = tmpdir / "stats.json"
    obj._injected = 0
    obj._skipped = 0
    obj._hit = 0
    obj._miss = 0
    obj._last_hit = {}
    obj._last = {}
    obj._started_at = 0.0
    return obj


def fake_event(text: str, uid: str = "100000000", private: bool = True):
    return SimpleNamespace(
        message_str=text,
        is_private_chat=lambda: private,
        get_sender_id=lambda: uid,
        _whale_face_done=False,
    )


def fake_req(with_parts: bool = True):
    parts = [] if with_parts else None
    return SimpleNamespace(extra_user_content_parts=parts, prompt="")


def text_of(req) -> str:
    parts = getattr(req, "extra_user_content_parts", None)
    if parts:
        return "\n".join(str(getattr(p, "text", "") or p) for p in parts)
    return getattr(req, "prompt", "") or ""


def main() -> int:
    print("== 脸层插件 · 真环境集成测试 ==")
    try:
        from astrbot_plugin_whale_face import main as plugin_main
        check("插件模块可导入", True)
        check("插件类存在", hasattr(plugin_main, "WhaleFacePlugin"))
    except Exception as e:  # noqa: BLE001
        check("插件模块可导入", False, f"{type(e).__name__}: {e}")
        traceback.print_exc()
        return 2

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        # 1) 正常私聊：应该有注入
        p = make_plugin(tmp)
        ev, req = fake_event("在干嘛"), fake_req()
        asyncio.run(p._inject_face(ev, req))
        block = text_of(req)
        check("私聊有注入", "<face_state>" in block, block[:120])
        check("注入含表情与尾巴", ("表情" in block) and ("尾巴" in block), block[:120])
        check("只注入一次", asyncio.run(p._inject_face(ev, req)) is None and text_of(req).count("<face_state>") == 1)
        check("stats 已落盘", (tmp / "stats.json").exists())
        if (tmp / "stats.json").exists():
            st = json.loads((tmp / "stats.json").read_text(encoding="utf-8"))
            check("stats 计数为 1", st.get("injected") == 1, str(st))

        # 2) 正事场景：不许撒娇
        p = make_plugin(tmp)
        ev, req = fake_event("帮我看下这个 traceback 怎么修"), fake_req()
        asyncio.run(p._inject_face(ev, req))
        block = text_of(req)
        check("正事档注入含「正事优先」", "正事优先" in block, block[:160])
        check("正事档不出现颜文字提示", "颜文字" not in block, block[:160])

        # 3) 安慰场景：不傲娇
        p = make_plugin(tmp)
        ev, req = fake_event("今天好累啊，什么都不想干"), fake_req()
        asyncio.run(p._inject_face(ev, req))
        block = text_of(req)
        check("安慰档不傲娇", "可以傲娇" not in block, block[:160])
        check("安慰档不发图", "不发图" in block, block[:160])

        # 4) 非主人：不注入
        p = make_plugin(tmp)
        ev, req = fake_event("在干嘛", uid="1000000001"), fake_req()
        asyncio.run(p._inject_face(ev, req))
        check("非主人不注入", text_of(req) == "", text_of(req)[:80])

        # 5) 群聊：不注入
        p = make_plugin(tmp)
        ev, req = fake_event("在干嘛", private=False), fake_req()
        asyncio.run(p._inject_face(ev, req))
        check("群聊不注入", text_of(req) == "", text_of(req)[:80])

        # 6) 关掉开关：不注入
        p = make_plugin(tmp)
        p.config["enabled"] = False
        ev, req = fake_event("在干嘛"), fake_req()
        asyncio.run(p._inject_face(ev, req))
        check("开关关闭不注入", text_of(req) == "", text_of(req)[:80])

        # 7) 老版本 req（没有 extra_user_content_parts）：走 prompt 兜底
        p = make_plugin(tmp)
        ev, req = fake_event("在干嘛"), fake_req(with_parts=False)
        asyncio.run(p._inject_face(ev, req))
        check("无 parts 时写 prompt", "<face_state>" in (req.prompt or ""), (req.prompt or "")[:120])

        # 8) 触达观测：回复里用上了口癖 → hit+1
        p = make_plugin(tmp)
        ev, req = fake_event("在干嘛"), fake_req()
        asyncio.run(p._inject_face(ev, req))
        meta = getattr(ev, "_whale_face_meta", None)
        check("注入时留下了 meta", isinstance(meta, dict) and "words" in meta, str(meta)[:120])
        word = (meta or {}).get("words") or []
        reply = "好呀，" + (word[0] if word else "嗯") + "，在的"
        resp = SimpleNamespace(completion_text=reply)
        asyncio.run(p._observe_usage(ev, resp))
        check("用上口癖时记 hit", p._hit == 1 and p._miss == 0, f"hit={p._hit} miss={p._miss}")

        # 9) 没用上 → miss+1，且不改她的回复
        p = make_plugin(tmp)
        ev, req = fake_event("在干嘛"), fake_req()
        asyncio.run(p._inject_face(ev, req))
        resp = SimpleNamespace(completion_text="好的，已经处理完了。")
        asyncio.run(p._observe_usage(ev, resp))
        check("没用上时记 miss", p._hit == 0 and p._miss == 1, f"hit={p._hit} miss={p._miss}")
        check("观测不改回复", resp.completion_text == "好的，已经处理完了。", resp.completion_text)

        # 10) 没注入过的事件不参与统计
        p = make_plugin(tmp)
        ev = fake_event("在干嘛")
        asyncio.run(p._observe_usage(ev, SimpleNamespace(completion_text="随便")))
        check("无 meta 不计入", p._hit == 0 and p._miss == 0, f"hit={p._hit} miss={p._miss}")

    print(f"\n结果：{len(PASS)} 通过 / {len(FAIL)} 失败")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
