# -*- coding: utf-8 -*-
"""内核冒烟测试：不依赖 pytest，直接 python tests/smoke.py。

覆盖：账本去重与状态机、评分、自省块预算、出站队列闸门/重试/去重、
事件总线异常隔离、情绪单写者、能力缺口。

用临时目录当 ASTRBOT_ROOT，绝不碰真机数据。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 注意：DSH 沙箱下 sqlite 无法在系统临时目录建库，测试目录必须放工作区内
TMP = ROOT / ".test_tmp" / f"{os.getpid()}-{int(time.time())}"
TMP.mkdir(parents=True, exist_ok=True)
os.environ["ASTRBOT_ROOT"] = str(TMP)

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {detail}")


def test_ledger() -> None:
    from whale_core import ledger as L

    print("[账本]")
    db = TMP / "ledger.sqlite3"
    led = L.Ledger(db)

    key, new = led.record("测试弱点 A", plugin="p", category="bug", severity="P1", source="test")
    check("首次写入 is_new", new is True)
    key2, new2 = led.record("测试弱点 A", plugin="p", category="bug", severity="P1", source="test")
    check("同键去重", key == key2 and new2 is False)
    item = led.get(key)
    check("次数累加", item is not None and item.occurrences == 2, f"occ={item.occurrences if item else None}")

    led.set_status(key, "triage", note="看过了")
    check("状态变更", led.get(key).status == "triage")
    check("状态留痕", any(e["kind"] == "status:triage" for e in led.history(key)))

    closed = L.compute_score("P1", 5, time.time(), "verified")
    open_score = L.compute_score("P1", 5, time.time(), "open")
    check("已解决不计分", closed == 0.0 and open_score > 0, f"{closed} vs {open_score}")

    # 已解决的再次出现应自动标回归
    led.set_status(key, "verified")
    led.record("测试弱点 A", plugin="p", category="bug", severity="P1", source="test")
    check("回归自动标记", led.get(key).status == "regressed", led.get(key).status)

    stats = led.stats()
    check("统计可用", stats["total"] >= 1 and "by_severity" in stats)
    check("top 排序", len(led.top(3)) >= 1)


def test_bus() -> None:
    from whale_core.bus import Bus

    print("[总线]")
    bus = Bus()
    got: list[str] = []

    def bad_handler(event):  # 故意抛异常
        raise RuntimeError("boom")

    bus.subscribe("emotion.*", lambda e: got.append(e.topic))
    bus.subscribe("*", bad_handler)
    bus.publish("emotion.changed", {"mood": "开心"}, source="test")
    check("订阅收到事件", got == ["emotion.changed"], str(got))
    check("异常被隔离", len(bus.recent_errors()) == 1)
    check("历史可读", bus.recent(1)[0]["topic"] == "emotion.changed")


def test_emotion() -> None:
    from whale_core.emotion import EmotionHub

    print("[情绪单写者]")
    hub = EmotionHub(state_path=TMP / "emotion.json")
    ok, _ = hub.claim("astrbot_plugin_whale_affect")
    check("首个写者认领成功", ok)
    ok2, reason = hub.claim("astrbot_plugin_whale_beams")
    check("第二个写者被拒", not ok2 and "owner_is" in reason, reason)
    check("非写者更新返回 None", hub.update("astrbot_plugin_whale_beams", {"mood": "紧张"}) is None)
    st = hub.update("astrbot_plugin_whale_affect", {"mood": "开心", "intensity": 0.8, "valence": 0.6})
    check("写者可写", st is not None and st["version"] == 1)
    block = hub.render_block()
    check("注入块有心情", "开心" in block and "<emotion_state>" in block, block)
    hub.observe("astrbot_plugin_whale_bili", "主人在看视频", mood_hint="好奇")
    check("观察不写事实", hub.snapshot()["mood"] == "开心")


def test_capabilities() -> None:
    from whale_core.capabilities import CapabilityRegistry, TARGET_CAPABILITIES

    print("[能力表]")
    reg = CapabilityRegistry()
    reg.provide("chat", "astrbot_core")
    reg.provide("draw_local", "whale_paint")
    reg.provide("draw_local", "anima_master")
    rep = reg.report()
    check("覆盖度计算", 0 < rep["coverage"] < 1, str(rep["coverage"]))
    check("缺口识别", any(g["capability"] == "sing" for g in rep["gaps"]))
    check("已仲裁的多提供者不算冲突", not any(c["capability"] == "draw_local" for c in rep["conflicts"]))
    check("可查全部多提供者", any(c["capability"] == "draw_local" for c in reg.conflicts(include_resolved=True)))
    reg.declare("emotion")
    reg.provide("emotion", "plugin_a")
    reg.provide("emotion", "plugin_b")
    reg.get("emotion").preferred = ""
    check("未仲裁冲突能报出", any(c["capability"] == "emotion" for c in reg.conflicts()))
    check("目标清单非空", len(TARGET_CAPABILITIES) >= 10)


def test_introspect() -> None:
    from whale_core import ledger as L, logscan
    from whale_core.capabilities import CapabilityRegistry
    from whale_core.introspect import SelfModel

    print("[自省]")
    led = L.Ledger(TMP / "ledger2.sqlite3")
    reg = CapabilityRegistry()
    reg.provide("chat", "astrbot_core")
    model = SelfModel(ledger=led, registry=reg)

    seeded = model.seed_known()
    check("架构债入库", seeded >= 5, str(seeded))
    check("重复 seed 幂等", model.seed_known() == 0)

    finding = logscan.Finding(
        key="send_api_unavailable", title="OneBot 通道不可用，消息发不出去",
        category="bug", severity="P1", plugin="", count=7,
        samples=["ApiNotAvailable"], first_ts=time.time() - 60, last_ts=time.time(),
    )
    keys = model.ingest_log([finding])
    check("日志弱点入库", len(keys) == 1 and led.get(keys[0]).occurrences == 1)
    model.ingest_log([finding])
    check("重复命中只累加", led.get(keys[0]).occurrences == 2)

    scores = model.score()
    check("综合分在 0-100", 0 <= scores["overall"] <= 100, str(scores["overall"]))
    check("分项齐全", len(scores["dimensions"]) == 5)
    check("扣分可解释", all("reason" in d for dim in scores["dimensions"] for d in dim["deductions"]))

    block = model.self_block(budget=200)
    check("自省块有预算", len(block) <= 200, f"len={len(block)}")
    check("自省块点出不足", "<self_awareness>" in block)

    proposals = model.propose(min_score=0, limit=3)
    check("能生成提案", len(proposals) >= 1, str(len(proposals)))
    check("提案有回滚方案", all("plan" in p for p in proposals))


def test_outbound() -> None:
    from whale_core.outbound import GatePolicy, OutboundQueue

    print("[出站队列]")
    # Autonomous delivery now requires a fresh gate, even with a fake sender.
    gate_dir = TMP / "data" / "plugin_data" / "astrbot_plugin_whale_voice"
    gate_dir.mkdir(parents=True, exist_ok=True)
    (gate_dir / "voice_gate.json").write_text(json.dumps({
        "expires": time.time() + 3600, "decision": "allow", "enforce": True,
        "token": "offline-smoke-token",
    }), encoding="utf-8")
    q = OutboundQueue(db_path=TMP / "outbound.sqlite3", policy=GatePolicy(quiet_hours=(0, 0)))

    # 去重
    t1 = q.request("你好呀", source="a", dedupe_key="greet")
    t2 = q.request("你好呀", source="a", dedupe_key="greet")
    check("重复内容被去重", t1 and not t2, f"{t1} {t2}")

    # 发送成功
    sent: list[dict] = []

    async def fake_sender(payload):
        sent.append(payload)
        return True

    q.set_sender(fake_sender)

    async def run() -> None:
        result = await q.dispatch_once()
        check("发送成功", result and result["result"] == "sent", str(result))
        check("回执记录", q.stats()["sent_total"] >= 1)
        # 最小间隔生效（上一条刚发完）
        t3 = q.request("第二条", source="b")
        r2 = await q.dispatch_once()
        check("最小间隔拦截", r2 and r2["result"] == "deferred" and r2["reason"] == "min_interval", str(r2))

        # 失败重试（明确失败才重试；超时/无会话不重试）
        q2 = OutboundQueue(db_path=TMP / "outbound2.sqlite3", policy=GatePolicy(quiet_hours=(0, 0), min_interval_sec=0))
        calls = {"n": 0}

        async def flaky(payload):
            calls["n"] += 1
            if calls["n"] < 2:
                raise ConnectionError("connection refused")
            return True

        q2.set_sender(flaky)
        q2.request("重试我", source="c")
        r3 = await q2.dispatch_once()
        check("明确失败转重试", r3 and r3["result"] == "retry", str(r3))
        # 把下次尝试时间提前，模拟退避到期
        q2.store.execute("UPDATE outbox SET next_attempt=0 WHERE status='pending'")
        r4 = await q2.dispatch_once()
        check("重试后成功", r4 and r4["result"] == "sent", str(r4))

        # 无会话（sender 返回 False）是永久失败，不重试
        q3 = OutboundQueue(db_path=TMP / "outbound3.sqlite3", policy=GatePolicy(quiet_hours=(0, 0), min_interval_sec=0))

        async def no_channel(payload):
            return False

        q3.set_sender(no_channel)
        q3.request("发不出去", source="d")
        r5 = await q3.dispatch_once()
        check("无通道不重试", r5 and r5["result"] == "failed", str(r5))

        # 超时是「结果未知」，既不算成功也不重试
        q4 = OutboundQueue(db_path=TMP / "outbound4.sqlite3", policy=GatePolicy(quiet_hours=(0, 0), min_interval_sec=0))

        async def timeout_sender(payload):
            raise TimeoutError("read timeout")

        q4.set_sender(timeout_sender)
        q4.request("可能已送达", source="e")
        r6 = await q4.dispatch_once()
        check("超时标 unknown", r6 and r6["result"] == "unknown", str(r6))

    asyncio.run(run())


def test_injectwatch() -> None:
    from whale_core import injectwatch

    print("[注入观测]")
    block = "<affect_state>黏人</affect_state>"
    self_block = "<self_awareness>" + "不足" * 40 + "</self_awareness>"
    m = injectwatch.measure([block, self_block], prompt="你好", cap_chars=100)
    check("按标签归因", m["tags"].get("affect_state") == 1 and m["tags"].get("self_awareness") == 1, str(m["tags"]))
    check("统计总量", m["total_chars"] == len(block) + len(self_block) + 2, str(m["total_chars"]))
    check("超预算判定", m["over_cap"] is True and m["over_by"] > 0)
    check("归属可查", "情绪内核" in injectwatch.attribute("affect_state"))

    s = injectwatch.summarize([m, injectwatch.measure(["短"], cap_chars=100)])
    check("汇总轮次", s["turns"] == 2)
    check("汇总最大值", s["max_chars"] >= m["total_chars"])
    check("超预算轮次", s["over_cap_turns"] == 1, str(s["over_cap_turns"]))
    check("空输入不炸", injectwatch.summarize([])["turns"] == 0)


def test_memory() -> None:
    import sqlite3

    from whale_core.memory import MemoryHub, Provider

    print("[记忆普查]")
    db = TMP / "mem.sqlite3"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, text TEXT, created REAL)")
    con.executemany("INSERT INTO memories (text, created) VALUES (?,?)",
                    [("我喜欢你", time.time()), ("今天下雨了", time.time() - 100)])
    con.commit()
    con.close()

    hub = MemoryHub(providers=[
        Provider(name="t", layer="episodic", role="测试", path=db, kind="sqlite",
                 table="memories", time_column="created"),
        Provider(name="missing", layer="semantic", role="缺失库", path=TMP / "nope.db"),
    ])
    rep = hub.report()
    first = rep["census"]["providers"][0]
    check("SQLite 计数", first["records"] == 2, str(first["records"]))
    check("时间列解析", first["last_write"] > 0)
    check("缺失库不炸", rep["census"]["providers"][1]["records"] == 0)
    check("契约单写者合规", rep["contract"]["ok"] is True)
    check("查重可用", "cross_provider_duplicates" in rep["duplicates"])


def test_voicegate() -> None:
    from whale_core import voicegate

    print("[闸门]")
    d = TMP / "voice_plugin"
    (d / "config").mkdir(parents=True, exist_ok=True)
    check("无闸门文件则放行", voicegate.evaluate(data_dir=d)["allow"] is True)

    gate = d / "voice_gate.json"
    now = time.time()
    gate.write_text(json.dumps({"expires": now + 600, "decision": "deny", "enforce": True,
                                "code": "quiet", "token": "t1"}), encoding="utf-8")
    v = voicegate.evaluate(data_dir=d)
    check("enforce=deny 时否决", v["allow"] is False and v["code"].startswith("deny"), str(v["code"]))

    gate.write_text(json.dumps({"expires": now - 1, "decision": "deny", "enforce": True}), encoding="utf-8")
    check("过期即放行", voicegate.evaluate(data_dir=d)["allow"] is True)

    gate.write_text(json.dumps({"expires": now + 600, "decision": "deny", "enforce": False}), encoding="utf-8")
    v = voicegate.evaluate(data_dir=d)
    check("影子模式只记不拦", v["allow"] is True and "shadow" in v["code"], v["code"])

    gate.write_text(json.dumps({"expires": now + 600, "decision": "allow", "enforce": True,
                                "token": "tok-x"}), encoding="utf-8")
    check("allow 放行", voicegate.evaluate(data_dir=d)["allow"] is True)

    (d / "consequence.json").write_text(json.dumps({"pending": [{"token": "tok-x"}]}), encoding="utf-8")
    v = voicegate.evaluate(data_dir=d)
    check("已消费的 token 视为重复", v["allow"] is False and v["code"] == "duplicate_token", v["code"])

    gate.write_text(json.dumps({"expires": now + 600, "decision": "allow", "enforce": True,
                                "token": "tok-y", "outlets": {"proactive": False}}), encoding="utf-8")
    v = voicegate.evaluate(data_dir=d, outlet="proactive")
    check("出口被关闭时否决", v["allow"] is False and "outlet_deny" in v["code"], v["code"])


def test_websearch() -> None:
    from whale_core import websearch

    print("[联网检索]")
    check("空关键词不发请求", websearch.search("") == [])
    check("空结果有可用文案", "没搜到" in websearch.render([], "x"))

    bing_page = """
    <ol id="b_results">
      <li class="b_algo"><h2><a href="https://example.com/a">标题 A</a></h2>
        <div class="b_caption"><p>摘要 A</p></div></li>
      <li class="b_algo"><h2><a href="https://example.com/b">标题 B</a></h2>
        <div class="b_caption"><p>摘要 B</p></div></li>
    </ol>
    """
    parsed = websearch._parse_bing(bing_page, 5)
    check("Bing 解析条数", len(parsed) == 2, str(len(parsed)))
    check("Bing 解析标题", parsed[0].title == "标题 A", parsed[0].title)
    check("Bing 解析摘要", "摘要 A" in parsed[0].snippet)
    check("Bing 解析引擎标记", parsed[0].engine == "bing")

    sogou_page = """
    <div class="vrwrap"><h3 class="vr-title"><a href="/link?url=abc">搜狗标题</a></h3>
      <div class="text-layout">搜狗摘要</div></div>
    """
    parsed2 = websearch._parse_sogou(sogou_page, 5)
    check("搜狗解析链接补全", parsed2 and parsed2[0].url.startswith("https://www.sogou.com"), parsed2[0].url if parsed2 else "")
    check("搜狗解析摘要", parsed2 and "搜狗摘要" in parsed2[0].snippet)


def test_memory_recall() -> None:
    import sqlite3

    from whale_core import memory_recall as mr

    print("[统一召回]")
    base = TMP / "data" / "plugin_data"

    # 情景层
    soul_dir = base / "astrbot_plugin_dafeiyu_soul"
    soul_dir.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(soul_dir / "soul.sqlite3")
    con.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, text TEXT, importance REAL, created REAL)")
    now = time.time()
    con.executemany("INSERT INTO memories (text, importance, created) VALUES (?,?,?)",
                    [("我喜欢唱歌", 5.0, now), ("今天下雨了", 1.0, now)])
    con.commit()
    con.close()

    # 语义层
    iris_dir = base / "astrbot_plugin_iris_chat_memory" / "faiss" / "memory_default"
    iris_dir.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(iris_dir / "metadata.db")
    con.execute("CREATE TABLE memories (content TEXT, timestamp TEXT)")
    con.execute("INSERT INTO memories (content, timestamp) VALUES (?,?)", ("主人喜欢唱歌和画画", "2026-11-01"))
    con.commit()
    con.close()

    # 档案层 + 日志层
    mem_dir = base / "astrbot_plugin_whale_memory" / "memory"
    (mem_dir / "daily").mkdir(parents=True, exist_ok=True)
    (mem_dir / "USER.md").write_text("- 喜欢：白米饭、唱歌\n", encoding="utf-8")
    (mem_dir / "daily" / "2026-11-07.md").write_text("- 13:00 [主人] 我喜欢唱歌\n", encoding="utf-8")

    hits = mr.recall("唱歌", limit=5)
    sources = {h.source for h in hits}
    check("召回覆盖多层", {"episodic", "profile"} <= sources, str(sources))
    check("档案优先于原话", hits[0].source == "profile", hits[0].source)
    text_map = {h.text: h for h in hits}
    check("跨层去重", "我喜欢唱歌" in text_map and "journal" in (text_map["我喜欢唱歌"].meta.get("also_in") or []),
          str(text_map.get("我喜欢唱歌").meta if "我喜欢唱歌" in text_map else "missing"))
    check("渲染块带标签", "<memory_digest>" in mr.render(hits, budget=400))
    check("预算可截断", len(mr.render(hits, budget=60)) <= 120, str(len(mr.render(hits, budget=60))))
    check("规模统计", mr.stats().get("episodic") == 2, str(mr.stats()))
    check("空查询返回空", mr.recall("") == [])


def test_digest() -> None:
    from whale_core import memory_recall as mr

    print("[注入摘要]")
    block = mr.build_digest("我喜欢唱歌", limit=3, budget=400)
    check("事实层能召回", block.startswith("<memory_digest>") and "喜欢" in block, block[:60])
    check("注入块有来源标签", "[档案]" in block or "[摘要]" in block, block[:60])

    empty = mr.build_digest("今天天气怎么样", limit=3, budget=400)
    check("不相关就不注入", empty == "", repr(empty[:40]))

    small = mr.build_digest("我喜欢唱歌", limit=3, budget=40)
    check("预算可截断", len(small) <= 80, str(len(small)))

    # 只要事实层：原话（episodic）不进来
    check("不混原始对话", "[原话]" not in block, block[:60])


def test_imagegen() -> None:
    from whale_core import imagegen

    print("[生图兜底]")
    d = imagegen.can_fallback(local_ok=False, local_error="cuda oom", meta_ready=True, meta_remaining=5)
    check("本地挂且元衡可用 → 兜底", d.use_meta and "oom" in d.reason, d.reason)
    check("元衡没就绪不兜底", imagegen.can_fallback(local_ok=False, meta_ready=False, meta_remaining=5).use_meta is False)
    check("额度用完不兜底", imagegen.can_fallback(local_ok=False, meta_ready=True, meta_remaining=0).use_meta is False)
    check("总开关关闭不兜底",
          imagegen.can_fallback(local_ok=False, meta_ready=True, meta_remaining=5, fallback_enabled=False).use_meta is False)
    check("超过日上限不兜底",
          imagegen.can_fallback(local_ok=False, meta_ready=True, meta_remaining=5,
                                fallback_used_today=3, fallback_daily_cap=3).use_meta is False)
    check("本地正常不兜底",
          imagegen.can_fallback(local_ok=True, meta_ready=True, meta_remaining=5).use_meta is False)
    check("已选 metal 时不再重试",
          imagegen.should_retry_cloud_after_local_failure(engine="meta", meta_ready=True, meta_remaining=5) is False)
    check("local 失败应重试云",
          imagegen.should_retry_cloud_after_local_failure(engine="local", meta_ready=True, meta_remaining=5) is True)


def main() -> int:
    print(f"临时根目录：{TMP}\n")
    test_ledger()
    test_bus()
    test_emotion()
    test_capabilities()
    test_introspect()
    test_outbound()
    test_injectwatch()
    test_memory()
    test_voicegate()
    test_websearch()
    test_memory_recall()
    test_digest()
    test_imagegen()
    print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
