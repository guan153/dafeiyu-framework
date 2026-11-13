"""BEAM-AI 内核单测（纯标准库 unittest）。

覆盖：状态契约、内感受惯性、双速时间尺度分离、动机仲裁、反失衡轮换、
参数总线、安全隔离、元认知审核、注入预算、持久化、长跑稳定性。
"""
import os
import sys
import tempfile
import unittest

# 让 beams_core 可导入（本文件在 beams_core/tests/ 下）
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from beams_core import affect2, body, bus, drive, express, intero, meta, safety, schema  # noqa: E402
from beams_core.kernel import BeamsKernel  # noqa: E402


class TestSchema(unittest.TestCase):
    def test_default_valid(self):
        st = schema.validate(schema.default_state())
        self.assertLessEqual(abs(st["fast"]["valence"]), 1.0)
        self.assertTrue(0.0 <= st["slow"]["stress"] <= 1.0)
        self.assertEqual(st["version"], schema.STATE_VERSION)

    def test_garbage_does_not_crash(self):
        for bad in (None, [], "x", {"fast": "not-a-dict"}, {"slow": {"stress": float("nan")}},
                    {"arbitration": {"winner": 123}}):
            st = schema.validate(bad)
            self.assertIsInstance(st, dict)
            self.assertIn("fast", st)

    def test_clamp(self):
        self.assertEqual(schema.clamp(5, 0, 1), 1.0)
        self.assertEqual(schema.clamp01(float("nan"), 0.3), 0.3)
        self.assertEqual(schema.clamp_valence(3.0), schema.VALENCE_MARGIN)


class TestBodyIntero(unittest.TestCase):
    def test_body_sample(self):
        b = body.sample({"latency_ms": 400, "tools": {"net": True, "vision": False}})
        self.assertTrue(0.0 <= b["cpu"] <= 1.0)
        self.assertEqual(b["latency_ms"], 400.0)

    def test_intero_inertia_no_jump(self):
        """一拍不能从基线跳到极端（身体是连续的）。"""
        prev = schema.default_state()["intero"]
        bad = body.sample({"latency_ms": 9000, "errors_1h": 20, "budget": 0.05, "cpu": 1.0, "mem": 1.0})
        new = intero.update(prev, bad, dt_s=5.0)
        self.assertGreater(new["valence"], -0.35)  # 一拍最多掉这么点

    def test_intero_converges(self):
        st = schema.default_state()
        prev = st["intero"]
        heavy = body.sample({"cpu": 0.95, "mem": 0.95, "latency_ms": 3000, "errors_1h": 10})
        for _ in range(60):
            prev = intero.update(prev, heavy, dt_s=5.0)
        self.assertLess(prev["resource"], 0.8)
        self.assertGreater(prev["load"], 0.5)


class TestDualSpeed(unittest.TestCase):
    def test_fast_recovers_faster_than_slow(self):
        st = schema.default_state()
        slow = st["slow"]
        fast = st["fast"]
        appr = affect2.appraisal_of({"kind": "cold", "intensity": 0.9})
        for _ in range(3):
            fast = affect2.step_fast(fast, appr, slow, st["intero"], st["params"])
            slow = affect2.step_slow(slow, fast, appr, dt_hours=5.0 / 3600.0, params=st["params"])
        v_after_neg = fast["valence"]
        self.assertLess(v_after_neg, 0.0)
        # 之后 30 拍空闲（约 2.5 分钟），快通道往回走，但不回到基线
        for _ in range(30):
            fast = affect2.step_fast(fast, None, slow, st["intero"], st["params"])
            slow = affect2.step_slow(slow, fast, None, dt_hours=5.0 / 3600.0, params=st["params"])
        self.assertGreater(fast["valence"], v_after_neg)
        self.assertLess(fast["valence"], 0.10)  # 还没完全好
        # 慢通道压力确实上去了
        self.assertGreater(slow["stress"], 0.15)

    def test_slow_has_memory(self):
        """慢通道跨"会话"仍在（不归零）。"""
        st = schema.default_state()
        slow = st["slow"]
        appr = affect2.appraisal_of({"kind": "cold", "intensity": 1.0})
        for _ in range(20):
            st["fast"] = affect2.step_fast(st["fast"], appr, slow, st["intero"], st["params"])
            slow = affect2.step_slow(slow, st["fast"], appr, dt_hours=0.01, params=st["params"])
        self.assertGreater(slow["stress"], 0.2)
        # 保存再读（模拟进程重启）
        st2 = schema.validate({"slow": slow})
        self.assertGreater(st2["slow"]["stress"], 0.2)

    def test_neg_load(self):
        fast = {"valence": -0.6, "arousal": 0.6, "dominance": 0.3}
        slow = {"stress": 0.7, "mood_valence": -0.3, "bonding": 0.4, "fatigue": 0.4}
        self.assertGreater(affect2.neg_load(fast, slow), 0.4)


class TestDrive(unittest.TestCase):
    def _inputs(self, **kw):
        base = {"valence": 0.0, "arousal": 0.35, "dominance": 0.5, "resource": 0.7,
                "prediction_error": 0.1, "load": 0.25, "stress": 0.15, "fatigue": 0.2,
                "bonding": 0.5, "idle": 0.0, "pending_tasks": 0.0, "error_rate": 0.0}
        base.update(kw)
        return base

    def test_idle_connect_wins(self):
        d = drive.compute(self._inputs(idle=0.95, bonding=0.25), schema.default_state()["params"])
        arb = drive.arbitrate(d, schema.default_state()["params"])
        self.assertEqual(arb["winner"], "connect")

    def test_stress_avoid_or_repair(self):
        d = drive.compute(self._inputs(valence=-0.7, stress=0.85, fatigue=0.75), schema.default_state()["params"])
        arb = drive.arbitrate(d, schema.default_state()["params"])
        self.assertIn(arb["winner"], ("avoid", "repair"))

    def test_pending_complete(self):
        d = drive.compute(self._inputs(pending_tasks=6, fatigue=0.1), schema.default_state()["params"])
        arb = drive.arbitrate(d, schema.default_state()["params"])
        self.assertIn(arb["winner"], ("complete", "connect", "explore"))

    def test_imbalance_decay_lets_others_win(self):
        """连续同情境：占优动机被衰减，轮不到它一直霸榜。"""
        params = schema.default_state()["params"]
        d = drive.compute(self._inputs(idle=0.9, bonding=0.2), params)
        arb = drive.arbitrate(d, params)
        winners = []
        for _ in range(40):
            arb = drive.arbitrate(d, params, prev_arb=arb, slow={"stress": 0.2, "fatigue": 0.2})
            winners.append(arb["winner"])
        self.assertTrue(any(w != "connect" for w in winners), winners)
        self.assertTrue(len(arb["history"]) > 0)

    def test_forced_rotation_cooldown(self):
        """到强制阈值：让位给第二高，并给原 winner 冷却（直接测仲裁层逻辑）。"""
        params = schema.default_state()["params"]
        drives = {name: {"intensity": 0.10, "urgency": 0.10, "reason": ""}
                  for name in schema.DRIVE_NAMES}
        drives["connect"] = {"intensity": 1.0, "urgency": 1.0, "reason": ""}
        prev = {"streak_name": "connect", "streak_count": schema.IMBALANCE_STREAK_FORCE,
                "cooldown": {}, "history": [], "last_switch_ts": 0.0}
        arb = drive.arbitrate(drives, params, prev_arb=prev, slow={"stress": 0.2, "fatigue": 0.2})
        self.assertTrue(arb.get("forced_rotation"))
        self.assertNotEqual(arb["winner"], "connect")
        self.assertIn("connect", arb["cooldown"])


class TestBusSafety(unittest.TestCase):
    def test_param_whitelist_and_step(self):
        st = schema.default_state()
        st2, audits = bus.apply_patch(st, {"sensitivity_gain": 99, "hacker": 1.0})
        self.assertAlmostEqual(st2["params"]["sensitivity_gain"],
                               schema.PARAMS_SPEC["sensitivity_gain"][2] + schema.PARAMS_SPEC["sensitivity_gain"][3], places=3)
        rejected = [a for a in audits if a.get("rejected")]
        self.assertEqual(len(rejected), 1)

    def test_envelope_never_exceeded(self):
        st = schema.default_state()
        for _ in range(100):
            st, _ = bus.apply_patch(st, {"sensitivity_gain": 99})
        lo, hi = schema.PERSONALITY_ENVELOPE["sensitivity_gain"]
        self.assertLessEqual(st["params"]["sensitivity_gain"], hi)
        self.assertGreaterEqual(st["params"]["sensitivity_gain"], lo)

    def test_isolation_hysteresis(self):
        fast = {"valence": -0.8, "arousal": 0.7, "dominance": 0.3}
        slow = {"stress": 0.9, "mood_valence": -0.7, "bonding": 0.3, "fatigue": 0.6}
        arb = {"streak_name": "avoid", "streak_count": 12}
        sec = safety.evaluate({}, fast, slow, arb)
        self.assertTrue(sec["isolation"])
        self.assertGreater(sec["neg_load"], safety.ISOLATION_ON - 0.01)
        # 恢复后退出隔离
        good_fast = {"valence": 0.3, "arousal": 0.3, "dominance": 0.6}
        good_slow = {"stress": 0.1, "mood_valence": 0.2, "bonding": 0.6, "fatigue": 0.1}
        sec2 = safety.evaluate(sec, good_fast, good_slow, {})
        self.assertFalse(sec2["isolation"])


class TestMeta(unittest.TestCase):
    def test_audit_verdicts(self):
        st = schema.default_state()
        r = meta.audit(st, {"ignored": True}, 3)
        self.assertEqual(r["verdict"], "too_cold")
        self.assertIn("warmth_bias", r["adjustments"])
        r2 = meta.audit(st, {"user_sentiment": -0.8}, 4)
        self.assertEqual(r2["verdict"], "too_hot")
        r3 = meta.audit(st, {"user_sentiment": 0.6, "reply_len": 400}, 5)
        self.assertEqual(r3["verdict"], "too_long")
        r4 = meta.audit(st, {}, 6)
        self.assertEqual(r4["verdict"], "none")

    def test_narrative_dedup(self):
        items = []
        items = meta.add_narrative(items, "刚才有点急，收一收")
        items = meta.add_narrative(items, "刚才有点急，收一收")
        self.assertEqual(len(items), 1)
        items = meta.add_narrative(items, "想主人了，主动说句话")
        self.assertEqual(len(items), 2)
        self.assertTrue(meta.narrative_context(items))

    def test_self_model_bounds(self):
        sm = {"capabilities": {"chat": 0.9}, "limits": [], "confidence": 0.62}
        for _ in range(200):
            sm = meta.update_self_model(sm, {"kind": "chat", "success": True})
        self.assertLessEqual(sm["capabilities"]["chat"], 1.0)
        for _ in range(200):
            sm = meta.update_self_model(sm, {"kind": "chat", "success": False})
        self.assertGreaterEqual(sm["capabilities"]["chat"], 0.0)


class TestExpress(unittest.TestCase):
    def test_mind_budget(self):
        st = schema.default_state()
        text = express.render_mind(st)
        self.assertLessEqual(len(text), express.MIND_BUDGET + 20)
        self.assertIn("<mind>", text)
        self.assertIn("怎么说", text)

    def test_style_changes_with_emotion(self):
        st = schema.default_state()
        happy = express.compute(schema.validate({"fast": {"valence": 0.6, "arousal": 0.7, "label": "开心"}}))
        low = express.compute(schema.validate({"fast": {"valence": -0.5, "arousal": 0.4, "label": "低落"},
                                               "slow": {"stress": 0.6}}))
        self.assertGreater(happy["playfulness"], low["playfulness"])
        self.assertGreater(low["brevity"], happy["brevity"] - 0.01)
        self.assertNotEqual(happy["style"], low["style"])


class TestKernel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="beams_test_")
        self.path = os.path.join(self.tmp, "beams_state.json")
        self.k = BeamsKernel(self.path, autosave=False)

    def test_tick_stability_long_run(self):
        for i in range(300):
            obs = self.k.tick(body_ctx={"latency_ms": 100 + (i % 7) * 50,
                                        "errors_1h": i % 3},
                              ctx={"hour": (i // 60) % 24})
            self.assertTrue(-1.0 <= obs["mood"] <= 1.0)
            self.assertIn(obs["drive"], schema.DRIVE_NAMES)
            self.assertTrue(0.0 <= obs["neg_load"] <= 1.0)
            self.assertFalse(schema.num(self.k.state, "ts", 0.0) != schema.num(self.k.state, "ts", 1.0))
        st = self.k.state
        for key in ("valence", "arousal", "dominance"):
            v = st["fast"][key]
            self.assertEqual(v, v)  # not NaN
        self.assertEqual(st["tick_count"], 300)

    def test_event_drives_fast(self):
        before = self.k.state["fast"]["valence"]
        self.k.feed("cold", intensity=0.9, why="被冷落")
        self.k.tick()
        self.assertLess(self.k.state["fast"]["valence"], before)

    def test_persistence(self):
        k = BeamsKernel(self.path, autosave=True)
        k.feed("praise", intensity=0.8)
        k.tick()
        saved = k.state["slow"]["mood_valence"]
        k2 = BeamsKernel(self.path, autosave=False)
        self.assertAlmostEqual(k2.state["slow"]["mood_valence"], saved, places=6)

    def test_render_and_patch(self):
        self.k.tick()
        self.assertIn("<mind>", self.k.render())
        audits = self.k.apply_patch({"warmth_bias": 0.2}, source="test", why="更暖一点")
        self.assertTrue(audits)
        self.assertLessEqual(self.k.state["params"]["warmth_bias"], 0.3)


if __name__ == "__main__":
    unittest.main(verbosity=2)