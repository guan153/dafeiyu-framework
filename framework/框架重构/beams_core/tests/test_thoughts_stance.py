"""iter36 行为层单测：念头池（thoughts.py）+ 四档 stance（stance.py）+ 内核接口。

覆盖：
- 念头：触发门槛/生成概率/池上限/同类去重/确定性；过期与凉掉；喂饱（文本优先、
  事件兜底、半满足、否定豁免、连续尝试放弃）；speak_bias 方向；独立文件持久化往返；
- stance：四档边界（24.9/25/55/80）、滞回、bonding 缓冲、gate 系数；
- 消费点：render_lite <=240、render_mind <=700；注入里出现档位/念头；
  gate 概率受 speak_bias 与 shut 档影响（×0.2 不是 0）；
- 内核：mark_ignored/mark_replied 更新 ignore_streak；comfort/fake_comfort 预设与
  comfort_streak；念头/档位不落进 beams_state.json；重启后念头池从独立文件恢复。

跑法：python beams_core/tests/run_tests.py
注意：TEMP 必须指向可写目录（Windows 下 tempfile 会退避重试）。
"""
import json
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from beams_core import express, gate, schema, stance, thoughts  # noqa: E402
from beams_core.kernel import BeamsKernel  # noqa: E402

BODY = {"cpu": 0.2, "mem": 0.4, "latency_ms": 100.0, "errors_1h": 0,
        "disk_free": 0.5, "tools": {"net": True, "vision": True, "tts": False, "code": True}}
T0 = 1_800_000_000.0


def new_kernel(tag="iter36_"):
    tmp = tempfile.mkdtemp(prefix=tag)
    return BeamsKernel(os.path.join(tmp, "s.json"), autosave=False)


def make_state(neg_load=0.0, stress=0.15, valence=0.10, bonding=0.50,
               ignore_streak=0, last_negative_ts=0.0, last_open_ts=0.0,
               last_user_ts=0.0, urgency=0.30, mood=0.10, fatigue=0.20,
               last_positive_ts=0.0):
    st = schema.default_state()
    st["safety"]["neg_load"] = neg_load
    st["slow"]["stress"] = stress
    st["slow"]["fatigue"] = fatigue
    st["slow"]["bonding"] = bonding
    st["slow"]["mood_valence"] = mood
    st["slow"]["last_negative_ts"] = last_negative_ts
    st["slow"]["last_positive_ts"] = last_positive_ts
    st["fast"]["valence"] = valence
    st["arbitration"]["urgency"] = urgency
    st["gate"]["ignore_streak"] = ignore_streak
    st["gate"]["last_open_ts"] = last_open_ts
    st["gate"]["last_user_ts"] = last_user_ts
    return schema.validate(st)


class TestThoughtTriggers(unittest.TestCase):
    def test_kind_catalog(self):
        self.assertEqual(len(thoughts.KIND_NAMES), 6)
        for kind in thoughts.KIND_NAMES:
            meta = thoughts.KIND_META[kind]
            self.assertIn(kind, thoughts.TTL)
            self.assertTrue(1.2 * 3600 <= thoughts.TTL[kind] <= 4 * 3600)
            self.assertTrue(meta["text"] and meta["hint"] and meta["done"])
            self.assertIn(kind, thoughts.SPEAK_BIAS)

    def test_trigger_bounds(self):
        st = make_state()
        for kind in thoughts.KIND_NAMES:
            w = thoughts.trigger(kind, st, T0)
            self.assertGreaterEqual(w, 0.0)
            self.assertLessEqual(w, 1.2)
        self.assertEqual(thoughts.trigger("nope", st, T0), 0.0)

    def test_praise_needs_bonding(self):
        cold = make_state(bonding=0.20)
        warm = make_state(bonding=0.75)
        self.assertEqual(thoughts.trigger("praise", cold, T0), 0.0)
        self.assertGreater(thoughts.trigger("praise", warm, T0), 0.5)

    def test_comfort_hits_stress_but_not_exhausted(self):
        tense = make_state(stress=0.80)
        tired = make_state(stress=0.80, fatigue=0.90)
        self.assertGreater(thoughts.trigger("comfort", tense, T0), 0.5)
        self.assertEqual(thoughts.trigger("comfort", tired, T0), 0.0)

    def test_quiet_and_dwell(self):
        tired = make_state(fatigue=0.85)
        self.assertGreater(thoughts.trigger("quiet", tired, T0), 0.5)
        waiting = make_state(last_open_ts=T0 - 600.0, last_user_ts=T0 - 900.0)
        self.assertGreaterEqual(thoughts.trigger("dwell", waiting, T0), 0.9)
        self.assertEqual(thoughts.trigger("dwell", make_state(), T0), 0.0)


class TestThoughtPool(unittest.TestCase):
    def test_spawn_probability_gate(self):
        st = make_state(bonding=0.8)
        self.assertIsNotNone(thoughts.spawn(st, [], T0, roll=0.0, prob=1.0))
        self.assertIsNone(thoughts.spawn(st, [], T0, roll=0.99, prob=0.25))

    def test_spawn_deterministic_and_legal(self):
        st = make_state(bonding=0.8)
        a = thoughts.spawn(st, [], T0, roll=0.0, prob=1.0)
        b = thoughts.spawn(st, [], T0, roll=0.0, prob=1.0)
        self.assertIsNotNone(a)
        self.assertEqual(a["kind"], b["kind"])
        self.assertIn(a["kind"], thoughts.KIND_NAMES)
        self.assertGreater(a["expires"], a["created"])
        self.assertTrue(a["pending"])

    def test_spawn_pool_cap_and_same_kind(self):
        st = make_state(bonding=0.8, fatigue=0.9)
        pool = []
        for i, kind in enumerate(thoughts.KIND_NAMES[:5]):
            one = thoughts.new_thought(kind, T0 + i)
            pool.append(one)
        self.assertIsNone(thoughts.spawn(st, pool, T0 + 10, roll=0.0, prob=1.0))
        pool2 = [thoughts.new_thought("praise", T0)]
        born = thoughts.spawn(make_state(bonding=0.8), pool2, T0 + 10, roll=0.0, prob=1.0)
        self.assertIsNotNone(born)
        self.assertNotEqual(born["kind"], "praise")

    def test_expire_semantics(self):
        now = T0
        old_pending = thoughts.new_thought("praise", now - 5 * 3600)
        old_done = thoughts.new_thought("comfort", now - 5 * 3600)
        old_done["pending"] = False
        old_done["mood"] = "被抱过了"
        fresh = thoughts.new_thought("talk", now)
        kept, faded = thoughts.expire([old_pending, old_done, fresh], now)
        self.assertEqual([t["kind"] for t in kept], ["talk"])
        self.assertEqual([t["kind"] for t in faded], ["praise"])
        self.assertEqual(thoughts.release_effect(old_pending)["kind"], "praise")

    def test_auto_complete_quiet_and_dwell(self):
        now = T0
        quiet = thoughts.new_thought("quiet", now - 60)
        dwell = thoughts.new_thought("dwell", now - 60)
        relaxed = make_state(fatigue=0.20, stress=0.10)
        relaxed["gate"]["last_user_ts"] = now - 10.0
        done = thoughts.auto_complete([quiet, dwell], relaxed, now)
        kinds = {d["kind"] for d in done}
        self.assertIn("quiet", kinds)
        self.assertIn("dwell", kinds)
        self.assertEqual(thoughts.active([quiet, dwell], now), [])


class TestThoughtSettle(unittest.TestCase):
    def test_event_fallback(self):
        now = T0
        pool = [thoughts.new_thought("talk", now)]
        payload = thoughts.settle(pool, "chat", "随便说点", now)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["kind"], "talk")
        self.assertFalse(pool[0]["pending"])
        self.assertTrue(pool[0]["mood"])
        self.assertEqual(payload["reward"]["kind"], "chat")

    def test_text_overrides_event(self):
        now = T0
        pool = [thoughts.new_thought("comfort", now)]
        payload = thoughts.settle(pool, "chat", "过来让我抱抱", now)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["kind"], "comfort")

    def test_negation_exemption(self):
        now = T0
        self.assertIsNone(thoughts.match_kind("我今天不想抱抱"))
        pool = [thoughts.new_thought("comfort", now)]
        # chat 本身喂不了 comfort，文本又被否定豁免 → 不该被喂
        self.assertIsNone(thoughts.settle(pool, "chat", "我今天不想抱抱", now))
        self.assertTrue(pool[0]["pending"])

    def test_soft_settle_half_reward(self):
        now = T0
        praise = thoughts.new_thought("praise", now)
        praise["attempts"] = 1
        payload = thoughts.settle([praise], "chat", "在忙", now)
        self.assertIsNotNone(payload)
        self.assertTrue(payload["soft"])
        self.assertLess(payload["reward"]["intensity"], 0.35)
        self.assertFalse(praise["pending"])

    def test_attempt_giveup(self):
        now = T0
        pool = [thoughts.new_thought("talk", now, weight=0.9)]
        self.assertEqual(thoughts.note_attempt(pool, now), "")
        self.assertEqual(pool[0]["attempts"], 1)
        thoughts.note_attempt(pool, now)
        self.assertEqual(thoughts.note_attempt(pool, now), "talk")
        self.assertFalse(pool[0]["pending"])
        self.assertIn("算了", pool[0]["mood"])
        self.assertEqual(thoughts.active(pool, now), [])

    def test_speak_bias_direction(self):
        now = T0
        self.assertEqual(thoughts.speak_bias([], now), 0.0)
        for kind, sign in (("praise", 1), ("talk", 1), ("dwell", 1), ("quiet", -1)):
            pool = [thoughts.new_thought(kind, now)]
            bias = thoughts.speak_bias(pool, now)
            if sign > 0:
                self.assertGreater(bias, 0.0, kind)
            else:
                self.assertLess(bias, 0.0, kind)
        # 取 weight 最高的那条
        pool = [thoughts.new_thought("quiet", now, weight=0.3),
                thoughts.new_thought("comfort", now, weight=1.1)]
        self.assertGreater(thoughts.speak_bias(pool, now), 0.0)

    def test_summary_and_inject_line(self):
        now = T0
        pool = [thoughts.new_thought("comfort", now, weight=0.9)]
        summary = thoughts.summary(pool, now)
        self.assertEqual(summary["count"], 1)
        self.assertIn("心里惦记着", summary["line"])
        self.assertLessEqual(len(summary["line"]), 40)
        # 兼容 state 入参（老调用）
        self.assertEqual(thoughts.summary({"thoughts": pool, "ts": now}, now)["count"], 1)


class TestThoughtPersistence(unittest.TestCase):
    def test_save_load_roundtrip(self):
        tmp = tempfile.mkdtemp(prefix="iter36_th_")
        path = os.path.join(tmp, "beams_state_thoughts.json")
        now = time.time()
        pool = [thoughts.new_thought("comfort", now, weight=0.8),
                thoughts.new_thought("ask_day", now, weight=0.6)]
        pool[1]["pending"] = False
        pool[1]["mood"] = "知道了"
        meta = {"last_eval": now - 30.0, "comfort": {"streak": 2, "last_ts": now},
                "stance": {"level": "wronged", "score": 33.0}}
        self.assertTrue(thoughts.save(path, pool, meta))
        loaded, meta2 = thoughts.load(path)
        self.assertEqual([t["kind"] for t in loaded], ["comfort", "ask_day"])
        self.assertAlmostEqual(loaded[0]["weight"], 0.8, places=3)
        self.assertFalse(loaded[1]["pending"])
        self.assertEqual(meta2["comfort"]["streak"], 2)
        self.assertEqual(meta2["last_eval"], now - 30.0)

    def test_corrupt_file_is_safe(self):
        tmp = tempfile.mkdtemp(prefix="iter36_bad_")
        path = os.path.join(tmp, "bad.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertEqual(thoughts.load(path), ([], {}))
        self.assertEqual(thoughts.load(os.path.join(tmp, "missing.json")), ([], {}))

    def test_bad_items_dropped(self):
        tmp = tempfile.mkdtemp(prefix="iter36_bad2_")
        path = os.path.join(tmp, "x.json")
        now = time.time()
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"thoughts": [
                {"kind": "not_a_kind", "expires": now + 3600},
                {"kind": "talk", "weight": float("nan"), "expires": now + 3600},
                {"kind": "quiet", "expires": now - 10},
            ], "meta": "bad"}, fh)
        loaded, meta = thoughts.load(path)
        self.assertEqual([t["kind"] for t in loaded], ["talk"])
        self.assertTrue(0.0 <= loaded[0]["weight"] <= 1.2)
        self.assertEqual(meta, {"last_eval": 0.0, "comfort": {}, "stance": {}})


class TestStance(unittest.TestCase):
    def test_level_boundaries(self):
        self.assertEqual(stance.level_of(0.0), "calm")
        self.assertEqual(stance.level_of(24.999), "calm")
        self.assertEqual(stance.level_of(25.0), "wronged")
        self.assertEqual(stance.level_of(54.999), "wronged")
        self.assertEqual(stance.level_of(55.0), "afraid")
        self.assertEqual(stance.level_of(79.999), "afraid")
        self.assertEqual(stance.level_of(80.0), "shut")
        self.assertEqual(stance.level_of(100.0), "shut")

    def test_hysteresis(self):
        self.assertEqual(stance.level_of(21.0, "wronged"), "wronged")
        self.assertEqual(stance.level_of(18.0, "wronged"), "calm")
        self.assertEqual(stance.level_of(50.0, "shut"), "wronged")
        self.assertEqual(stance.level_of(76.0, "shut"), "shut")

    def test_calm_by_default(self):
        st = make_state()
        got = stance.compute(st, now=T0)
        self.assertEqual(got["level"], "calm")
        self.assertGreaterEqual(got["score"], 0.0)
        self.assertLess(got["score"], 25.0)
        self.assertEqual(got["level_cn"], "平静")

    def test_negative_push_raises_level(self):
        st = make_state(neg_load=0.72, stress=0.70, valence=-0.60,
                        last_negative_ts=T0 - 60.0, ignore_streak=2)
        got = stance.compute(st, now=T0, neg_recent=2)
        self.assertIn(got["level"], ("wronged", "afraid", "shut"))
        self.assertGreater(got["score"], 25.0)
        self.assertTrue(got["tendency"])
        self.assertTrue(got["reason"])

    def test_bonding_buffer(self):
        base = dict(neg_load=0.40, stress=0.35, valence=-0.10,
                    last_negative_ts=T0 - 900.0)
        low_bond = make_state(bonding=0.50, **base)
        high_bond = make_state(bonding=0.95, **base)
        s_low = stance.score(low_bond, now=T0)
        s_high = stance.score(high_bond, now=T0)
        self.assertGreater(s_low, s_high)
        self.assertEqual(stance.compute(low_bond, now=T0)["level"], "wronged")
        self.assertEqual(stance.compute(high_bond, now=T0)["level"], "calm")

    def test_bonding_never_makes_shut_unreachable(self):
        """关系缓冲是减分不是乘系数：极端持续的负性仍然到得了 shut（否则档位变死代码）。"""
        st = make_state(neg_load=1.0, stress=1.0, valence=-1.0,
                        last_negative_ts=T0, ignore_streak=5, bonding=1.0)
        got = stance.compute(st, now=T0, neg_recent=4)
        self.assertEqual(got["level"], "shut")
        self.assertGreaterEqual(got["score"], 80.0)

    def test_gate_factor_and_adjust_bounds(self):
        self.assertEqual(stance.gate_factor("shut"), 0.2)
        self.assertEqual(stance.gate_factor("calm"), 1.0)
        self.assertEqual(stance.gate_reason("shut"), stance.GATE_REASON)
        self.assertEqual(stance.gate_reason("calm"), "")
        for level in stance.LEVELS:
            for param in ("warmth", "brevity", "talkative", "playfulness"):
                self.assertLessEqual(abs(stance.adjust(level, param)), 0.15)


class TestConsumption(unittest.TestCase):
    def test_injection_budgets(self):
        st = make_state(neg_load=0.75, stress=0.72, valence=-0.6,
                        last_negative_ts=T0 - 60.0, ignore_streak=3,
                        bonding=0.82)
        stt = stance.compute(st, now=T0, neg_recent=3)
        self.assertNotEqual(stt["level"], "calm")
        pool = [thoughts.new_thought("comfort", T0, weight=1.0),
                thoughts.new_thought("quiet", T0, weight=0.6)]
        lite = express.render_lite(st, thoughts=pool, stance=stt)
        mind = express.render_mind(st, thoughts=pool, stance=stt)
        self.assertLessEqual(len(lite), express.LITE_BUDGET)
        self.assertLessEqual(len(lite), 240)
        self.assertLessEqual(len(mind), 700)
        self.assertIn("心里惦记着", lite)
        self.assertIn("心里惦记着", mind)
        self.assertIn("现在的状态", mind)

    def test_calm_has_no_stance_line(self):
        st = make_state()
        lite = express.render_lite(st, thoughts=[], stance=stance.compute(st, now=T0))
        self.assertNotIn("现在的状态", lite)
        self.assertLessEqual(len(lite), express.LITE_BUDGET)

    def test_stance_modifies_expression_bounded(self):
        calm_st = make_state(bonding=0.6, valence=0.2)
        shut_st = make_state(neg_load=0.88, stress=0.85, valence=-0.8,
                             last_negative_ts=T0, ignore_streak=5, bonding=0.5)
        calm = express.compute(calm_st, stance=stance.compute(calm_st, now=T0))
        shut = express.compute(shut_st, stance=stance.compute(shut_st, now=T0, neg_recent=4))
        self.assertEqual(calm["stance"]["level"], "calm")
        self.assertIn(shut["stance"]["level"], ("afraid", "shut"))
        self.assertGreaterEqual(shut["brevity"], calm["brevity"] - 1e-9)
        self.assertLessEqual(shut["warmth"], calm["warmth"] + 1e-9)
        self.assertLessEqual(shut["talkative"], calm["talkative"] + 1e-9)

    def test_gate_speak_bias(self):
        st = make_state(urgency=0.40)
        p_none = gate.evaluate(st, "ambient", {"hour": 20})["probability"]
        praise = [thoughts.new_thought("praise", time.time())]
        quiet = [thoughts.new_thought("quiet", time.time())]
        p_praise = gate.evaluate(st, "ambient", {"hour": 20}, thoughts=praise)["probability"]
        p_quiet = gate.evaluate(st, "ambient", {"hour": 20}, thoughts=quiet)["probability"]
        self.assertGreater(p_praise, p_none)
        self.assertLess(p_quiet, p_none)

    def test_gate_shut_factor_not_zero(self):
        st = make_state(urgency=0.60)
        shut = stance.compute(make_state(neg_load=0.9, stress=0.9, valence=-0.8,
                                         last_negative_ts=T0, ignore_streak=5),
                              now=T0, neg_recent=4)
        self.assertEqual(shut["level"], "shut")
        calm_prob = gate.evaluate(st, "ambient", {"hour": 20},
                                  stance={"level": "calm"})["probability"]
        shut_got = gate.evaluate(st, "ambient", {"hour": 20}, stance=shut)
        self.assertAlmostEqual(shut_got["probability"], calm_prob * 0.2, places=3)
        self.assertGreater(shut_got["probability"], 0.0)
        self.assertIn("回避", shut_got["reason"])


class TestKernelBehavior(unittest.TestCase):
    def test_mark_ignored_updates_streak_and_pending(self):
        k = new_kernel("iter36_mi_")
        self.assertEqual(k.mark_ignored("宝宝在吗")["ignore_streak"], 1)
        self.assertEqual(k.mark_ignored("睡了吗")["ignore_streak"], 2)
        pending = k.state["gate"]["pending"]
        self.assertEqual(pending[-1]["trigger"], "ignored")
        self.assertIn("睡了吗", pending[-1]["text"])
        self.assertEqual(k.mark_replied()["ignore_streak"], 0)
        self.assertEqual(k.state["gate"]["ignore_streak"], 0)

    def test_comfort_presets_and_streak(self):
        k = new_kernel("iter36_cf_")
        k.feed("comfort", why="对不起，抱抱")
        self.assertEqual(k.comfort_streak, 1)
        appr = k._events[-1].get("appraisal") or {}
        self.assertGreater(appr.get("goal_congruence"), 0.0)
        k.feed("comfort", why="我错了，别生气")
        self.assertEqual(k.comfort_streak, 2)
        k.feed("fake_comfort", why="行了别闹了")
        self.assertEqual(k.comfort_streak, 0)
        appr = k._events[-1].get("appraisal") or {}
        self.assertLess(appr.get("goal_congruence"), 0.0)

    def test_behavior_never_enters_state_contract(self):
        k = new_kernel("iter36_st_")
        k.tick(body_ctx=BODY, ctx={"hour": 20})
        self.assertIn("level", k.stance)
        self.assertNotIn("stance", k.state)
        self.assertNotIn("thoughts", k.state)
        self.assertNotIn("comfort", k.state)
        self.assertNotIn("comfort_streak", k.state)
        self.assertEqual(k.state["version"], schema.STATE_VERSION)

    def test_spawn_and_settle_through_kernel(self):
        k = new_kernel("iter36_sp_")
        k.config["thought_spawn_prob"] = 1.0
        k._thoughts_meta["last_eval"] = 0.0
        k.tick(body_ctx=BODY, ctx={"hour": 20}, now=T0, dt_s=5.0)
        self.assertGreaterEqual(len(k.thoughts), 1)
        kind = k.thoughts[0]["kind"]
        # 用一条能喂饱它的用户事件（chat 对 talk/ask_day/dwell；关键词对 praise/comfort）
        why = {"praise": "你真厉害", "comfort": "过来抱抱", "talk": "我跟你说件事",
               "ask_day": "今天怎么样", "quiet": "快去休息吧", "dwell": "在吗"}.get(kind, "在吗")
        before_events = len(k._events)
        k.feed("chat", why=why)
        self.assertGreater(len(k._events), before_events)  # 用户事件 + 可能的奖励事件
        self.assertFalse(k.thoughts[0]["pending"])

    def test_thoughts_persist_across_restart(self):
        tmp = tempfile.mkdtemp(prefix="iter36_rt_")
        path = os.path.join(tmp, "s.json")
        k = BeamsKernel(path, autosave=True)
        k.config["thought_spawn_prob"] = 1.0
        k._thoughts_meta["last_eval"] = 0.0
        k.tick(body_ctx=BODY, ctx={"hour": 20}, now=T0, dt_s=5.0)
        k.feed("comfort", why="抱抱")
        k.save()  # autosave 内核本来下一拍才落行为层文件，测试手动刷一次
        self.assertTrue(os.path.isfile(k.behavior_path))
        kinds = [t["kind"] for t in k.thoughts]
        streak = k.comfort_streak
        k2 = BeamsKernel(path, autosave=False)
        self.assertEqual([t["kind"] for t in k2.thoughts], kinds)
        self.assertEqual(k2.comfort_streak, streak)
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        self.assertNotIn("stance", json.dumps(raw, ensure_ascii=False))
        self.assertNotIn("thoughts", json.dumps(raw, ensure_ascii=False))

    def test_observation_and_snapshot_expose_behavior(self):
        k = new_kernel("iter36_obs_")
        k.tick(body_ctx=BODY, ctx={"hour": 20}, now=T0, dt_s=5.0)
        obs = k.observation()
        self.assertIn("stance", obs)
        self.assertIn(obs["stance"]["level"], stance.LEVELS)
        self.assertIn("thoughts", obs)
        self.assertIn("comfort_streak", obs)
        snap = k.snapshot()
        self.assertIn("stance", snap)
        self.assertIn("thoughts", snap)
        self.assertIn("comfort_streak", snap)
        self.assertLessEqual(len(k.render()), 700)
        self.assertLessEqual(len(k.render_lite()), 240)


if __name__ == "__main__":
    unittest.main(verbosity=2)