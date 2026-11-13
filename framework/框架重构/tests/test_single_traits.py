# -*- coding: utf-8 -*-
"""单文件版 beams.py 的 traits（长期人格）专测 [iter36]。

背景：44 号文档遗留「单文件版尚未同步 traits」，本测试锁住 C1 的验收口径：
  1. 数据层：_traits.json 载入 / 缺省默认 / 硬边界钳制 / 写盘格式与模块版一致；
  2. 评估时机：feed 累积 chat/speech 信号与轮数 → tick 里每 8 轮评估一次（重启不丢）；
  3. 规则一致：词表 / 步长 / 阻尼 / density / 安全话题当轮跳过；
  4. 输出：observation / snapshot 带 traits，render() 追加「长期」一句且不超预算；
  5. 顺带锁住 iter36 顺手修的 gate NameError（单文件版闸门原本永远不更新）。

只依赖标准库 + beams 单文件；不 import beams_core（内核层另一路在改，避免测试互染）。
跑法（项目根 框架重构/ 下任选其一）：
  python tests\test_single_traits.py
  python -m unittest discover -s tests -p "test_single_traits.py"
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import beams  # noqa: E402

CTX = {"hour": 12}

WARM_MSGS = [
    "宝宝抱抱，你好厉害呀",
    "想你啦，贴贴",
    "今天好可爱，真棒",
    "亲亲，爱你",
    "宝宝真优秀，夸你",
    "摸摸头，辛苦啦",
    "么么，陪陪我",
    "乖宝，喜欢你",
]

SHORT_MSGS = ["简短一点，说重点%d号" % i for i in range(8)]      # asksShort
DETAIL_MSGS = ["详细讲讲，展开细节%d号" % i for i in range(8)]    # asksDetail
SAFETY_MSGS = ["第%d条 密码: abc123 帮我看下" % i for i in range(8)]  # safetyTouched


class TestSingleTraits(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="beams_traits_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---------------- 小工具 ----------------

    def _kernel(self, name="s.json"):
        return beams.Kernel(os.path.join(self.tmp, name), autosave=False)

    def _traits_path(self, name="s.json"):
        return os.path.join(self.tmp, os.path.splitext(name)[0] + "_traits.json")

    def _audit_path(self, name="s.json"):
        return os.path.join(self.tmp, os.path.splitext(name)[0] + "_audit.jsonl")

    def _run(self, kernel, msgs):
        for msg in msgs:
            kernel.feed("chat", goal_congruence=0.3, intensity=0.6, why=msg)
            kernel.tick(ctx=CTX, dt_s=5.0)

    def _read_json(self, path):
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def _write_traits_json(self, path, traits, extra=None):
        payload = {"ts": 1.0, "traits": traits}
        if extra:
            payload.update(extra)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)

    # ---------------- 1. 数据层 ----------------

    def test_default_traits(self):
        k = self._kernel()
        self.assertEqual(beams._traits_view(k.traits), {
            "clinginess": 0.35, "verbosity": 0.50, "humor": 0.30,
            "dependence": 0.50, "diligence": 0.65, "emotionRate": 0.30})
        self.assertEqual(k._traits_eval_round, 0)
        self.assertEqual(k._turns, 0)

    def test_load_traits_file_and_meta(self):
        path = self._traits_path()
        self._write_traits_json(path, {
            "clinginess": 0.62, "verbosity": 0.44, "humor": 0.51,
            "dependence": 0.38, "diligence": 0.71, "emotionRate": 0.42,
        }, {"eval_round": 24, "turns": 24,
            "last": {"ts": 1.0, "round": 24, "changes": [{"key": "clinginess"}]}})
        k = self._kernel()
        self.assertEqual(k.traits["clinginess"], 0.62)
        self.assertEqual(k._traits_eval_round, 24)
        self.assertEqual(k._turns, 24)          # 重启后轮数不丢
        self.assertEqual(k._traits_last.get("round"), 24)

    def test_bounds_clamped(self):
        path = self._traits_path()
        self._write_traits_json(path, {"clinginess": 2.0, "humor": -1.0,
                                       "verbosity": "bad", "emotionRate": None})
        k = self._kernel()
        self.assertEqual(k.traits["clinginess"], beams.TRAIT_BOUNDS["clinginess"][1])
        self.assertEqual(k.traits["humor"], beams.TRAIT_BOUNDS["humor"][0])
        self.assertEqual(k.traits["verbosity"], beams.TRAIT_BOUNDS["verbosity"][0])
        self.assertEqual(k.traits["emotionRate"], beams.TRAIT_BOUNDS["emotionRate"][0])
        # 越界值也写不进文件
        self.assertEqual(beams._traits_view(k.traits)["clinginess"], 0.90)

    # ---------------- 2. 评估时机 ----------------

    def test_eval_after_8_chat_turns(self):
        k = self._kernel()
        self._run(k, WARM_MSGS)
        self.assertEqual(k._turns, 8)
        self.assertEqual(k._traits_eval_round, 8)
        # 被夸/说软话 → 撒娇↑、幽默↑、情绪外化↑
        self.assertGreater(k.traits["clinginess"], 0.35)
        self.assertGreater(k.traits["humor"], 0.30)
        self.assertGreater(k.traits["emotionRate"], 0.30)
        # 落盘：traits + eval_round + turns + last
        path = self._traits_path()
        self.assertTrue(os.path.isfile(path))
        doc = self._read_json(path)
        self.assertEqual(doc["eval_round"], 8)
        self.assertEqual(doc["turns"], 8)
        self.assertEqual(doc["traits"]["clinginess"], round(k.traits["clinginess"], 3))
        self.assertEqual(doc["last"]["round"], 8)
        self.assertTrue(doc["last"]["changes"])
        # 审计流水
        self.assertTrue(os.path.isfile(self._audit_path()))
        with open(self._audit_path(), "r", encoding="utf-8") as fh:
            kinds = [json.loads(x).get("kind") for x in fh if x.strip()]
        self.assertIn("traits_eval", kinds)

    def test_no_double_eval_without_new_turns(self):
        k = self._kernel()
        self._run(k, WARM_MSGS)
        first = dict(k.traits)
        for _ in range(3):
            k.tick(ctx=CTX, dt_s=5.0)
        self.assertEqual(k._traits_eval_round, 8)
        self.assertEqual(k.traits, first)

    def test_second_eval_after_next_8_turns(self):
        k = self._kernel()
        self._run(k, WARM_MSGS)
        mid = dict(k.traits)
        self._run(k, SHORT_MSGS)
        self.assertEqual(k._traits_eval_round, 16)
        # 连续两轮「嫌长/要简短」→ 话量↓
        self.assertLess(k.traits["verbosity"], mid["verbosity"])

    def test_reload_resumes_eval_schedule(self):
        k = self._kernel()
        self._run(k, WARM_MSGS)
        k.save()
        k2 = beams.Kernel(os.path.join(self.tmp, "s.json"), autosave=False)
        self.assertEqual(k2._turns, 8)
        self.assertEqual(k2._traits_eval_round, 8)
        self.assertEqual(beams._traits_view(k2.traits), beams._traits_view(k.traits))
        self.assertEqual(k2.observation()["traits"], beams._traits_view(k.traits))

    # ---------------- 3. 规则一致 ----------------

    def test_safety_topic_skips_evolution(self):
        k = self._kernel()
        self._run(k, SAFETY_MSGS)
        self.assertEqual(k._traits_eval_round, 8)
        self.assertEqual(beams._traits_view(k.traits), beams._traits_view(beams._traits_default()))
        doc = self._read_json(self._traits_path())
        self.assertEqual(doc["last"], {})   # 跳过 → 没有变更记录

    def test_asks_short_lowers_verbosity(self):
        k = self._kernel("short.json")
        self._run(k, SHORT_MSGS)
        self.assertLess(k.traits["verbosity"], 0.50)

    def test_asks_detail_raises_verbosity(self):
        k = self._kernel("detail.json")
        self._run(k, DETAIL_MSGS)
        self.assertGreater(k.traits["verbosity"], 0.50)

    def test_same_why_deduped_within_5s(self):
        k = self._kernel()
        for _ in range(8):
            k.feed("chat", goal_congruence=0.3, why="宝宝抱抱")
        # 5 秒指纹去重：同内容只吃一条 → 轮数只有 1
        self.assertEqual(k._turns, 1)
        k.tick(ctx=CTX, dt_s=5.0)
        self.assertEqual(k._traits_eval_round, 0)

    def test_speech_accumulates_but_does_not_count_turn(self):
        k = self._kernel()
        for i in range(8):
            k.feed("speech", goal_congruence=0.2, why="宝宝抱抱，第%d句" % i)
        self.assertEqual(k._turns, 0)
        self.assertGreater(k._trait_signals.get("warm", 0.0), 0.0)

    # ---------------- 4. 输出 ----------------

    def test_render_has_longterm_line_and_budget(self):
        k = self._kernel()
        self._run(k, WARM_MSGS)
        mind = k.render()
        self.assertIn("长期：", mind)
        self.assertIn("撒娇", mind)
        self.assertLessEqual(len(mind), 700)
        self.assertLessEqual(len(k.render_lite()), 240)

    def test_observation_and_snapshot_expose_traits(self):
        k = self._kernel()
        self._run(k, WARM_MSGS)
        obs = k.observation()
        snap = k.snapshot()
        self.assertIn("traits", obs)
        self.assertEqual(obs["traits"], beams._traits_view(k.traits))
        self.assertEqual(snap["traits"], beams._traits_view(k.traits))
        self.assertIsInstance(snap["traits_summary"], str)
        self.assertTrue(snap["traits_summary"])
        self.assertEqual(snap["traits_last"].get("round"), 8)

    def test_traits_bias_expression(self):
        # 撒娇↑ → warmth↑（有界）；话量↑ → brevity↓。直接调低层函数验证偏置生效。
        st = beams.default_state()
        st["traits"] = {"clinginess": 0.80, "verbosity": 0.80, "humor": 0.70,
                        "dependence": 0.50, "diligence": 0.65, "emotionRate": 0.60}
        exp_hi = beams.compute_expression(st)
        st2 = beams.default_state()
        st2["traits"] = beams._traits_default()
        exp_lo = beams.compute_expression(st2)
        self.assertGreater(exp_hi["warmth"], exp_lo["warmth"])
        self.assertLess(exp_hi["brevity"], exp_lo["brevity"])
        self.assertGreater(exp_hi["talkative"], exp_lo["talkative"])

    # ---------------- 5. iter36 顺手修复：gate 不再静默失效 ----------------

    def test_gate_updates(self):
        k = self._kernel()
        k.tick(ctx=CTX, dt_s=5.0)
        gate = k.state["gate"]
        self.assertGreater(gate["probability"], 0.0)
        self.assertIn("概率", gate["reason"])
        self.assertIn(gate["open"], (True, False))


if __name__ == "__main__":
    unittest.main(verbosity=2)