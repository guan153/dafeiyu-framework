# -*- coding: utf-8 -*-
"""心情合成（whale_core.moodfusion · 分层版）的回归测试。

核心不变式：**扰动层（laya 判读）永远不能改心情标签。**
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from whale_core import moodfusion as mf  # noqa: E402


class TestBaseLayer(unittest.TestCase):
    def test_blend_is_slow(self):
        """底色是小时级的：一次极端读数翻不动它，要持续好几拍才会转。"""
        prev = {"安心": 0.70, "黏人": 0.60}
        soul = {"安心": 10, "黏人": 95}
        out = mf.blend_base(prev, soul)
        self.assertAlmostEqual(out["安心"], 0.70 * 0.92 + 0.10 * 0.08, places=4)
        self.assertEqual(mf.base_mood(out)[0], "安心")   # 一拍之后仍是安心

        # 连续 20 拍同样的读数，才慢慢转过去
        cur = prev
        for _ in range(20):
            cur = mf.blend_base(cur, soul)
        self.assertEqual(mf.base_mood(cur)[0], "黏人")

    def test_no_prev_takes_current(self):
        out = mf.blend_base(None, {"困": 80, "开心": 10})
        self.assertEqual(mf.base_mood(out)[0], "困")

    def test_base_switch_margin(self):
        base = {"黏人": 0.51, "安心": 0.50}
        self.assertEqual(mf.base_mood(base, prev_label="安心")[0], "安心")
        base2 = {"黏人": 0.80, "安心": 0.50}
        self.assertEqual(mf.base_mood(base2, prev_label="安心")[0], "黏人")

    def test_empty_base(self):
        self.assertEqual(mf.base_mood({}), ("平静", 0.4))


class TestTemperature(unittest.TestCase):
    def test_intensity_moves_with_temp(self):
        base = {"黏人": 0.5}
        cold = mf.compose(base, {"intensity": 0.1})
        hot = mf.compose(base, {"intensity": 0.95})
        self.assertLess(cold.intensity, hot.intensity)
        self.assertEqual(cold.mood, hot.mood)   # 温度不改标签

    def test_intensity_bounds(self):
        for t in (-5, 0, 0.5, 1, 9):
            c = mf.compose({"困": 0.9}, {"intensity": t})
            self.assertGreaterEqual(c.intensity, 0.05)
            self.assertLessEqual(c.intensity, 1.0)


class TestPerturbation(unittest.TestCase):
    def test_never_changes_mood(self):
        """最重要的不变式：你说难过，她还是她，只是变软。"""
        base = {"安心": 0.8}
        for label in ["疲惫", "难过", "生气", "撒娇", "开心", "兴奋", "平静"]:
            c = mf.compose(base, {"intensity": 0.5}, {"label": label, "confidence": 0.95})
            self.assertEqual(c.mood, "安心", msg=label)

    def test_tired_softens(self):
        c = mf.compose({"安心": 0.6}, {}, {"label": "疲惫", "confidence": 0.9})
        self.assertTrue(c.expression["soften"])
        self.assertTrue(c.expression["brevity"])
        self.assertEqual(c.expression["scene_hint"], "comfort")

    def test_low_confidence_ignored(self):
        c = mf.compose({"安心": 0.6}, {}, {"label": "疲惫", "confidence": 0.2})
        self.assertFalse(c.expression["soften"])

    def test_sad_user_brightness_negative(self):
        c = mf.compose({"开心": 0.6}, {}, {"label": "难过", "confidence": 0.8})
        self.assertLess(c.expression["brightness"], 0)

    def test_missing_perturb(self):
        c = mf.compose({"安心": 0.6}, {"intensity": 0.4}, None)
        self.assertFalse(c.expression["soften"])
        self.assertEqual(c.mood, "安心")


class TestIntegration(unittest.TestCase):
    def test_full_chain_shapes(self):
        base = mf.blend_base({"安心": 0.7}, {"安心": 70, "黏人": 62})
        c = mf.compose(base, {"mood": "黏人", "intensity": 0.47}, {"label": "撒娇", "confidence": 0.8})
        self.assertIn(c.mood, mf.MOODS)
        d = c.to_dict()
        for k in ("mood", "intensity", "base", "temp", "expression"):
            self.assertIn(k, d)

    def test_laya_map_covers_all_labels(self):
        self.assertEqual(
            set(mf.LAYA_MAP),
            {"兴奋", "吃醋", "平静", "开心", "撒娇", "生气", "疲惫", "难过"},
        )
        for v in mf.LAYA_MAP.values():
            self.assertIn(v, mf.MOODS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
