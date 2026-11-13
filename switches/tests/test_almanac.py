# -*- coding: utf-8 -*-
"""历法模块测试：节气、季节、星期。"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from whale_core import almanac as a  # noqa: E402


class TestSolarTerms(unittest.TestCase):
    def test_known_2026_terms(self):
        """2026 年节气日（已对照天文时刻核过）。"""
        cases = [
            ("小寒", 2026, 1, 5), ("立春", 2026, 2, 4), ("清明", 2026, 4, 5),
            ("立夏", 2026, 5, 5), ("夏至", 2026, 6, 21), ("立秋", 2026, 8, 7),
            ("白露", 2026, 9, 7), ("立冬", 2026, 11, 7), ("冬至", 2026, 12, 22),
        ]
        for name, y, m, d in cases:
            with self.subTest(term=name):
                self.assertEqual(a.solar_term_of(datetime(y, m, d).date()), name)

    def test_non_term_day(self):
        self.assertIsNone(a.solar_term_of(datetime(2026, 11, 9).date()))

    def test_current_term_progresses(self):
        name, at, nxt = a.current_term(datetime(2026, 11, 9, 20, 0))
        self.assertEqual(name, "立冬")
        self.assertEqual(nxt, "小雪")
        self.assertLess(at, datetime(2026, 11, 9, 20, 0))


class TestSeason(unittest.TestCase):
    def test_seasons(self):
        cases = [
            (datetime(2026, 2, 10), "春"), (datetime(2026, 5, 10), "夏"),
            (datetime(2026, 8, 10), "秋"), (datetime(2026, 11, 9), "冬"),
        ]
        for dt, expect in cases:
            with self.subTest(dt=dt):
                self.assertEqual(a.season_of(dt), expect)


class TestRender(unittest.TestCase):
    def test_contains_key_facts(self):
        text = a.render(datetime(2026, 11, 9, 20, 0).timestamp())
        for key in ("2026年11月09日", "星期一", "20:00", "冬", "立冬"):
            self.assertIn(key, text)

    def test_block_shape(self):
        block = a.render_block()
        self.assertTrue(block.startswith("<almanac>"))
        self.assertTrue(block.endswith("</almanac>"))
        self.assertIn("现在：", block)

    def test_budget(self):
        """预算管的是正文，标签本身不占预算（否则永远超）。"""
        block = a.render_block(budget=30)
        body = block.split("现在：", 1)[1].rsplit("\n</almanac>", 1)[0]
        self.assertLessEqual(len(body), 30)
        self.assertTrue(block.endswith("</almanac>"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
