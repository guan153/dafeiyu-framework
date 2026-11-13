# -*- coding: utf-8 -*-
"""人格单一来源（whale_core.persona）测试。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from whale_core import persona  # noqa: E402


class TestPersona(unittest.TestCase):
    def test_self_ref_split(self):
        """2026-11-09 主人拍板：平时「我」，撒娇「咱」。"""
        self.assertEqual(persona.SELF_REF, "我")
        self.assertEqual(persona.SELF_REF_SOFT, "咱")

    def test_brief_contains_rules(self):
        b = persona.brief()
        self.assertIn("大肥鱼", b)
        self.assertIn("我", b)
        self.assertIn("咱", b)
        self.assertIn("主人", b)
        self.assertIn("宝宝", b)
        self.assertLessEqual(len(b), 140)

    def test_brief_budgets(self):
        b = persona.brief()
        self.assertIn(str(persona.BUBBLE_MAX), b)
        self.assertIn(str(persona.SENTENCE_MAX), b)

    def test_conflicts_detects_unknown_self_ref(self):
        """写「本鱼」这种没登记的自称，要能被扫出来。"""
        import tempfile

        d = Path(tempfile.mkdtemp())
        (d / "fake_plugin").mkdir()
        (d / "fake_plugin" / "main.py").write_text(
            'line = "嘴上：自称「本鱼」；"\n', encoding="utf-8"
        )
        found = persona.conflicts(str(d))
        self.assertTrue(any("本鱼" in x for x in found), found)

    def test_conflicts_allows_both(self):
        import tempfile

        d = Path(tempfile.mkdtemp())
        (d / "ok_plugin").mkdir()
        (d / "ok_plugin" / "main.py").write_text(
            'a = "自称「我」"\nb = "自称「咱」"\n', encoding="utf-8"
        )
        self.assertEqual(persona.conflicts(str(d)), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
