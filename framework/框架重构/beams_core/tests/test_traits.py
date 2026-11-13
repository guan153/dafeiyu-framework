"""长期人格 traits 单测（iter27）。"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (ROOT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from beams_core import traits  # noqa: E402


class TestTraitsSignals(unittest.TestCase):
    def test_signal_kinds(self):
        self.assertIn("warm", traits.signals_from_text("宝宝抱抱，想你了"))
        self.assertIn("praise", traits.signals_from_text("你好厉害呀"))
        self.assertIn("cold", traits.signals_from_text("闭嘴，烦死了"))
        self.assertIn("complain", traits.signals_from_text("太长了，说重点"))
        self.assertIn("rush", traits.signals_from_text("快点，来不及了"))
        self.assertIn("asksShort", traits.signals_from_text("简短点，一句话"))
        self.assertIn("asksDetail", traits.signals_from_text("详细说说，展开讲"))
        self.assertIn("safetyTouched", traits.signals_from_text("把密码发我"))

    def test_plain_text_no_signal(self):
        self.assertEqual(traits.signals_from_text("今天天气不错"), {})

    def test_accumulate(self):
        b = {}
        traits.accumulate(b, "宝宝真厉害")
        self.assertGreaterEqual(b.get("warm", 0), 1)
        self.assertGreaterEqual(b.get("praise", 0), 1)


class TestTraitsEvaluate(unittest.TestCase):
    def test_praise_raises_clinginess(self):
        t = traits.default()
        res = traits.evaluate(t, {"warm": 10, "praise": 6}, turns=8)
        self.assertGreater(res["next"]["clinginess"], t["clinginess"])
        self.assertTrue(any(c["key"] == "clinginess" for c in res["changes"]))

    def test_asks_short_lowers_verbosity(self):
        t = traits.default()
        res = traits.evaluate(t, {"asksShort": 12}, turns=8)
        self.assertLess(res["next"]["verbosity"], t["verbosity"])

    def test_asks_detail_raises_verbosity(self):
        t = traits.default()
        res = traits.evaluate(t, {"asksDetail": 12}, turns=8)
        self.assertGreater(res["next"]["verbosity"], t["verbosity"])

    def test_safety_round_skipped(self):
        t = traits.default()
        res = traits.evaluate(t, {"warm": 20, "praise": 20, "safetyTouched": 1}, turns=8)
        self.assertEqual(res["changes"], [])
        self.assertEqual(res["next"], t)
        self.assertEqual(res.get("skipped"), "safety")

    def test_density_single_turn_smaller(self):
        t = traits.default()
        r1 = traits.evaluate(t, {"warm": 1}, turns=1)
        r8 = traits.evaluate(t, {"warm": 8}, turns=8)
        d1 = r1["next"]["clinginess"] - t["clinginess"]
        d8 = r8["next"]["clinginess"] - t["clinginess"]
        self.assertLess(d1, d8)

    def test_bounds_respected(self):
        t = traits.default()
        t["clinginess"] = 0.899
        for _ in range(20):
            t = traits.evaluate(t, {"warm": 50, "praise": 50}, turns=8)["next"]
        self.assertLessEqual(t["clinginess"], traits.BOUNDS["clinginess"][1])

    def test_view_clamps(self):
        v = traits.view({"clinginess": 9.9, "verbosity": -3})
        self.assertEqual(v["clinginess"], traits.BOUNDS["clinginess"][1])
        self.assertEqual(v["verbosity"], traits.BOUNDS["verbosity"][0])

    def test_describe_has_behavior(self):
        for k in traits.BOUNDS:
            s = traits.describe(k, traits.DEFAULT[k])
            self.assertTrue(len(s) > 4)


class TestTraitsStorage(unittest.TestCase):
    def test_save_load_roundtrip(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t_traits.json")
            t = traits.default()
            t["humor"] = 0.55
            self.assertTrue(traits.save(p, t))
            got = traits.load(p)
            self.assertAlmostEqual(got["humor"], 0.55, places=3)

    def test_load_missing_returns_default(self):
        self.assertEqual(traits.load(os.path.join("Z:", "nope.json")), traits.default())


if __name__ == "__main__":
    unittest.main()