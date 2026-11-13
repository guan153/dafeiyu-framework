# -*- coding: utf-8 -*-
"""路径迁移器测试：只改能证明等价的写法，复杂写法必须原样不动。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import path_migrate as pm  # noqa: E402

DATA = r"%ASTRBOT_HOME%\data"
PD = DATA + r"\plugin_data"


class TestMigrate(unittest.TestCase):
    def test_wrapper_call(self):
        src = f'_Path(r"{PD}\\astrbot_plugin_whale_state\\urge.json")'
        out, log = pm.migrate_text(src)
        self.assertIn('_Path(PD / "astrbot_plugin_whale_state" / "urge.json")', out)
        self.assertTrue(any("_Path" in x for x in log))

    def test_bare_string(self):
        src = f'p = r"{PD}\\astrbot_plugin_whale_beams\\beams_state_traits.json"'
        out, _ = pm.migrate_text(src)
        self.assertIn('str(PD / "astrbot_plugin_whale_beams" / "beams_state_traits.json")', out)

    def test_join(self):
        src = f'_os.path.join(r"{PD}", "x", "y.json")'
        out, _ = pm.migrate_text(src)
        self.assertIn('_os.path.join(PD, "x", "y.json")', out)

    def test_db(self):
        src = f'db = r"{DATA}\\data_v4.db"'
        out, _ = pm.migrate_text(src)
        self.assertIn('str(DATA / "data_v4.db")', out)

    def test_complex_untouched(self):
        """f-string / 拼接 / split 一律不动。"""
        cases = [
            'p = f"{base}/plugin_data/{name}/state.json"',
            'p = base + "\\\\plugin_data\\\\" + name',
            'parts = path.split("plugin_data")',
        ]
        for src in cases:
            out, log = pm.migrate_text(src)
            self.assertEqual(out, src, msg=src)
            self.assertEqual(log, [], msg=src)


    def test_import_not_inserted_inside_parenthesized_import(self):
        """回归：多行 import 里插 import 会把语法搞坏（2026-11-09 真踩过）。"""
        src = (
            "from a import (\n"
            "    x,\n"
            "    y,\n"
            ")\n"
            "from pathlib import Path\n"
            f'p = Path(r"{PD}\\a\\b.json")\n'
        )
        out, _ = pm.migrate_text(src)
        # 新的 import 块必须出现在整个括号闭合之后
        self.assertIn('from pathlib import Path', out)
        idx_block = out.find("[path-migrate]")
        idx_close = out.find(")\n")
        self.assertGreater(idx_block, idx_close)
        compile(out, "<test>", "exec")   # 必须能编译

    def test_import_added_once(self):
        src = f'import os\np = _Path(r"{PD}\\a\\b.json")'
        out1, _ = pm.migrate_text(src)
        out2, _ = pm.migrate_text(out1)
        self.assertEqual(out1, out2)          # 幂等
        self.assertEqual(out1.count("[path-migrate]"), 1)

    def test_import_has_fallback(self):
        src = f'p = _Path(r"{PD}\\a\\b.json")'
        out, _ = pm.migrate_text(src)
        self.assertIn("except Exception", out)
        self.assertIn("from whale_core.paths import DATA, PD", out)


class TestPathsModule(unittest.TestCase):
    def test_paths_resolve(self):
        from whale_core import paths

        self.assertTrue(str(paths.ROOT))
        self.assertTrue(str(paths.DATA))
        self.assertEqual(paths.PD.name, "plugin_data")

    def test_env_override(self):
        import importlib
        import os

        os.environ["WHALE_ROOT"] = r"X:\fake"
        try:
            from whale_core import paths

            importlib.reload(paths)
            self.assertEqual(str(paths.ROOT), r"X:\fake")
        finally:
            os.environ.pop("WHALE_ROOT", None)
            from whale_core import paths

            importlib.reload(paths)


if __name__ == "__main__":
    unittest.main(verbosity=2)
