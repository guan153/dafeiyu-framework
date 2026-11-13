# -*- coding: utf-8 -*-
"""脸层插件（astrbot_plugin_whale_face）的离线测试。

这里的断言对应真会出事的场景：
- 主人在排障，她却在撒娇（work 场景失效）
- 主人在难过，她还在傲娇（comfort 场景失效）
- 注入块超长把上下文挤爆（预算失效）
- 情绪文件坏了，整个回复被拖死（降级失效）
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent  # qqbot_v3/
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from astrbot_plugin_whale_face import face_block  # noqa: E402


class TestDetectScene(unittest.TestCase):
    def test_comfort_beats_work(self):
        """「代码又报错了我好烦」——先接情绪，不是先修 bug。"""
        self.assertEqual(face_block.detect_scene("代码又报错了，我好烦"), "comfort")

    def test_work(self):
        self.assertEqual(face_block.detect_scene("帮我看下这个 traceback"), "work")
        self.assertEqual(face_block.detect_scene("插件加载失败，排查一下"), "work")

    def test_night(self):
        self.assertEqual(face_block.detect_scene("还没睡", hour=2), "night")

    def test_chat(self):
        self.assertEqual(face_block.detect_scene("在干嘛", hour=15), "chat")

    def test_empty_is_safe(self):
        self.assertIn(face_block.detect_scene("", hour=15), {"chat", "night"})


class TestBuildBlock(unittest.TestCase):
    def test_contains_mood_and_expression(self):
        block = face_block.build_block("委屈", 0.8)
        self.assertIn("<face_state>", block)
        self.assertIn("委屈", block)
        self.assertIn("尾巴", block)
        self.assertIn("</face_state>", block)

    def test_budget_is_hard(self):
        block = face_block.build_block("委屈", 0.8, budget=80)
        self.assertLessEqual(len(block), 80)

    def test_zero_budget_disables(self):
        self.assertEqual(face_block.build_block("开心", 0.8, budget=0), "")

    def test_work_scene_forbids_cute(self):
        block = face_block.build_block("黏人", 0.9, scene="work")
        self.assertIn("正事优先", block)
        self.assertNotIn("颜文字", block)
        self.assertNotIn("口癖可用", block)

    def test_comfort_scene_no_tsundere(self):
        block = face_block.build_block("委屈", 0.9, scene="comfort")
        self.assertNotIn("可以傲娇", block)

    def test_stale_degrades(self):
        fresh = face_block.build_block("吃醋", 0.8, stale=False)
        old = face_block.build_block("吃醋", 0.8, stale=True)
        self.assertNotEqual(fresh, old)

    def test_deterministic(self):
        a = face_block.build_block("开心", 0.7, salt=5)
        b = face_block.build_block("开心", 0.7, salt=5)
        self.assertEqual(a, b)

    def test_no_image_line_when_disallowed(self):
        block = face_block.build_block("低落", 0.9)
        self.assertIn("不发图", block)

    def test_meme_hint_present_when_allowed(self):
        """只有一套图库在跑，所以把心情偏好直接写进块里，让它选图时带上心情。"""
        block = face_block.build_block("吃醋", 0.9, budget=400)
        self.assertIn("优先「angry」类", block)

    def test_no_meme_hint_when_image_disallowed(self):
        block = face_block.build_block("低落", 0.9, budget=400)
        self.assertNotIn("优先「", block)

    def test_meme_hint_tags_exist_in_library(self):
        """偏好标签必须是图库里真实存在的 19 个之一，别指向不存在的类。"""
        from whale_core.face import MOOD_FACE

        library_tags = {
            "happy", "daily", "angry", "sad", "confused", "shy", "like", "surprised",
            "givemoney", "meow", "sigh", "baka", "fool", "color", "sleep", "see",
            "morning", "work", "cpu",
        }
        for mood, spec in MOOD_FACE.items():
            with self.subTest(mood=mood):
                self.assertIn(spec.meme_tag, library_tags)

    def test_conflict_arbitration_line(self):
        """与 speech_style 的「不强制字数上限」冲突时，必须有明确仲裁规则。"""
        block = face_block.build_block("委屈", 0.8, budget=400)
        self.assertIn("取更短、更少", block)

    def test_speak_line_mentions_bubbles(self):
        """多句心情要说「一句一个气泡」——否则发送层再会拆也没词可拆。"""
        block = face_block.build_block("黏人", 0.8, budget=400)
        self.assertIn("一句一个气泡", block)

    def test_single_bubble_moods_stay_one(self):
        """低落只发一条，不该出现「分 2~N 条」。"""
        block = face_block.build_block("低落", 0.8, budget=400)
        self.assertNotIn("一句一个气泡", block)
        self.assertIn("只发一条", block)

    def test_arbitration_survives_small_budget(self):
        """预算再紧，仲裁条款也不能先被丢掉——它比口癖重要。"""
        block = face_block.build_block("委屈", 0.8, budget=140)
        self.assertIn("取更短、更少", block)

    def test_strong_mood_asks_for_one_word(self):
        """强度高时要写成可验收的硬要求，不能只说「可用」（实测 6/42 触达）。"""
        strong = face_block.build_block("黏人", 0.8, budget=400)
        self.assertTrue("必须出现" in strong or "挑一个用上" in strong)

    def test_weak_mood_stays_soft(self):
        weak = face_block.build_block("黏人", 0.4, budget=400)
        self.assertIn("口癖可用", weak)

    def test_small_budget_keeps_tags(self):
        """预算被调小也不能切掉闭合标签——半个标签比没有注入更糟。"""
        block = face_block.build_block("委屈", 0.9, budget=60)
        self.assertTrue(block.startswith("<face_state>"), block)
        self.assertTrue(block.endswith("</face_state>"), block)
        self.assertLessEqual(len(block), 60)

    def test_tiny_budget_returns_empty(self):
        self.assertEqual(face_block.build_block("委屈", 0.9, budget=10), "")

    def test_all_combos_well_formed(self):
        """12 心情 × 4 场景 × 7 档预算，块必须完整且不超预算。"""
        from whale_core.face import MOOD_FACE

        for mood in MOOD_FACE:
            for scene in ("chat", "work", "comfort", "night"):
                for budget in (240, 120, 80, 60, 40, 20, 10):
                    block = face_block.build_block(mood, 0.78, scene=scene, salt=3, budget=budget)
                    if not block:
                        continue
                    with self.subTest(mood=mood, scene=scene, budget=budget):
                        self.assertTrue(block.startswith("<face_state>"))
                        self.assertTrue(block.endswith("</face_state>"))
                        self.assertLessEqual(len(block), budget)


class TestEmotionRead(unittest.TestCase):
    def test_never_raises(self):
        em = face_block.read_emotion()
        self.assertIn("mood", em)
        self.assertIn("intensity", em)
        self.assertIn("stale", em)
        self.assertIsInstance(em["stale"], bool)

    def test_render_command(self):
        text = face_block.render_command("吃醋", 0.8)
        self.assertIn("心情", text)
        self.assertIn("脸", text)
        self.assertIn("嘴", text)


class TestHitCheck(unittest.TestCase):
    def test_word_hit(self):
        r = face_block.hit_check("哼唧……就要贴贴", {"words": ["哼唧", "饿饿"], "kaomoji": "", "tail": ""})
        self.assertTrue(r["hit"])
        self.assertEqual(r["used_words"], ["哼唧"])

    def test_kaomoji_hit_ignores_spaces(self):
        r = face_block.hit_check("好耶 (￣▽￣)", {"words": [], "kaomoji": "(￣▽￣)", "tail": ""})
        self.assertTrue(r["used_kaomoji"])
        self.assertTrue(r["hit"])

    def test_miss(self):
        r = face_block.hit_check("好的，已经改完了。", {"words": ["哼唧"], "kaomoji": ">_<", "tail": "慢慢甩"})
        self.assertFalse(r["hit"])
        self.assertEqual(r["used_words"], [])

    def test_tail_counts(self):
        r = face_block.hit_check("尾巴慢慢甩着", {"words": [], "kaomoji": "", "tail": "慢慢甩"})
        self.assertTrue(r["used_tail"])
        self.assertTrue(r["hit"])

    def test_empty_meta_never_raises(self):
        self.assertFalse(face_block.hit_check("随便说点", {})["hit"])


class TestSelfTest(unittest.TestCase):
    def test_matrix_all_good(self):
        """线上自检命令的底座：192 组必须全绿。"""
        r = face_block.self_test(budget=240)
        self.assertTrue(r.get("core"), r.get("error"))
        self.assertGreater(r["total"], 100)
        self.assertEqual(r["bad"], [])
        self.assertTrue(r["ok"])

    def test_reports_emotion(self):
        r = face_block.self_test()
        self.assertIn("mood", r.get("emotion") or {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
