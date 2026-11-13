# -*- coding: utf-8 -*-
"""情绪外显层（whale_core.face）的回归测试。

原则：每个断言都对应一条「她会不会看起来不对」的真实故障。
跑法：
    python -m unittest tests.test_face -v
    python tests/test_face.py
"""

from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORE_ROOT = HERE.parent  # qqbot_v3/
if str(CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(CORE_ROOT))

from whale_core import emotion  # noqa: E402
from whale_core.face import (  # noqa: E402
    CUTE_KIT,
    EXPRESSIONS,
    KAOMOJI,
    MOOD_FACE,
    SCENES,
    TSUNDERE_TAILS,
    card_for,
    cute_kit,
    pick_expression,
    should_send_face,
)

# 资产目录（离线环境可能没有，相关测试自动跳过）
LIVE2D_EXP3 = Path(r"%PROJECT_HOME%\live2d\deepseek-chan\DeepSeek\Exp3")


class TestTableIntegrity(unittest.TestCase):
    def test_covers_all_emotion_moods(self):
        """表里必须和 EmotionHub 的 12 种心情完全对齐——多一个少一个都是隐患。"""
        self.assertEqual(set(MOOD_FACE), set(emotion.MOODS))

    def test_every_spec_expression_is_known(self):
        """表里用的表情名必须在资产词典里，否则渲染器会静默失败。"""
        for mood, spec in MOOD_FACE.items():
            for name in (spec.exp3, spec.exp3_strong):
                self.assertIn(name, EXPRESSIONS, msg=f"{mood} 用了未知表情 {name!r}")

    def test_scene_keys(self):
        self.assertEqual(set(SCENES), {"chat", "work", "comfort", "night"})

    @unittest.skipUnless(LIVE2D_EXP3.is_dir(), "Live2D 资产不在本机")
    def test_expressions_exist_on_disk(self):
        """资产词典和真实 exp3 文件对得上（空串除外）。"""
        have = {p.name[: -len(".exp3.json")] for p in LIVE2D_EXP3.glob("*.exp3.json")}
        for name in EXPRESSIONS:
            if not name:
                continue
            self.assertIn(name, have, msg=f"资产词典里的 {name!r} 在 Exp3 目录里不存在")


class TestCardFor(unittest.TestCase):
    def test_unknown_mood_falls_back(self):
        card = card_for("不存在的情绪", 0.6)
        self.assertEqual(card.mood, "平静")
        self.assertTrue(any(d.startswith("unknown_mood:") for d in card.degraded))

    def test_intensity_clamped(self):
        self.assertEqual(card_for("开心", 5).intensity, 1.0)
        self.assertEqual(card_for("开心", -3).intensity, 0.0)
        self.assertEqual(card_for("开心", float("nan")).intensity, 0.4)
        self.assertEqual(card_for("开心", "abc").intensity, 0.4)  # type: ignore[arg-type]

    def test_tier_boundaries(self):
        self.assertEqual(card_for("开心", 0.24).tier, "low")
        self.assertEqual(card_for("开心", 0.25).tier, "mid")
        self.assertEqual(card_for("开心", 0.69).tier, "mid")
        self.assertEqual(card_for("开心", 0.70).tier, "high")

    def test_high_intensity_uses_stronger_expression(self):
        """委屈拉满时应该是「流泪」，不是弱弱的「＞＜」。"""
        self.assertEqual(card_for("委屈", 0.9).exp3, "流泪")
        self.assertEqual(card_for("委屈", 0.5).exp3, "＞＜")

    def test_low_intensity_suppresses_expression(self):
        """强度低就别做鬼脸，靠尾巴说话。"""
        self.assertEqual(card_for("委屈", 0.1).exp3, "")

    def test_keep_soft_moods_keep_expression_when_low(self):
        """困/认真是状态不是表情，低强度也要戴着眼镜。"""
        self.assertEqual(card_for("困", 0.1).exp3, "圈圈眼")
        self.assertEqual(card_for("认真", 0.1).exp3, "黑框眼镜")

    def test_meme_boost_scales_with_intensity(self):
        low = card_for("开心", 0.2).meme_boost
        mid = card_for("开心", 0.5).meme_boost
        high = card_for("开心", 1.0).meme_boost
        self.assertLess(low, mid)
        self.assertLess(mid, high)
        self.assertAlmostEqual(mid, 3.0, places=2)  # 0.5 强度 = 表里的原值

    def test_stale_halves_intensity(self):
        fresh = card_for("吃醋", 0.8)
        old = card_for("吃醋", 0.8, stale=True)
        self.assertAlmostEqual(old.intensity, 0.4, places=4)
        self.assertIn("stale", old.degraded)
        self.assertNotEqual(fresh.tier, old.tier)

    def test_deterministic(self):
        a = card_for("胡闹", 0.66).to_dict()
        b = card_for("胡闹", 0.66).to_dict()
        self.assertEqual(a, b)

    def test_describe_is_readable(self):
        text = card_for("委屈", 0.72).describe()
        self.assertIn("委屈", text)
        self.assertIn("尾巴", text)


class TestScenes(unittest.TestCase):
    def test_work_scene_kills_cuteness(self):
        """正事档：撒娇全关、不发图、一句说完——人设不能牺牲准确性。"""
        card = card_for("黏人", 0.9, scene="work")
        self.assertEqual(card.cute, 0)
        self.assertEqual(card.particles, 0)
        self.assertEqual(card.emoji, 0)
        self.assertFalse(card.allow_image)
        self.assertEqual(card.exp3, "黑框眼镜")
        self.assertEqual(card.tail, "收起来")

    def test_comfort_scene_no_image(self):
        """主人在难过，图收起来（AGENTS 斗图规则的硬条款）。"""
        card = card_for("开心", 0.9, scene="comfort")
        self.assertFalse(card.allow_image)
        self.assertEqual(card.emoji, 0)
        self.assertLessEqual(card.cute, 1)

    def test_night_slows_typing(self):
        """深夜说话更慢。"""
        base = card_for("安心", 0.5)
        night = card_for("安心", 0.5, scene="night")
        self.assertGreater(night.delay_x, base.delay_x)
        self.assertIn("night", night.degraded)

    def test_unknown_scene_degrades_to_chat(self):
        card = card_for("开心", 0.5, scene="打游戏")
        self.assertIn("unknown_scene:打游戏", card.degraded)
        self.assertTrue(card.allow_image)


class TestPickExpression(unittest.TestCase):
    def test_returns_expression_when_available(self):
        card = card_for("委屈", 0.9)
        self.assertEqual(pick_expression(card, {"流泪", "黑脸"}), "流泪")

    def test_falls_back_when_missing(self):
        card = card_for("委屈", 0.9)
        self.assertEqual(pick_expression(card, {"黑脸"}), "黑脸")

    def test_no_assets_returns_empty(self):
        self.assertEqual(pick_expression("", {"黑脸"}), "")

    def test_none_available_skips_validation(self):
        self.assertEqual(pick_expression("流泪", None), "流泪")
        self.assertEqual(pick_expression("瞎编的表情", None), "")


class TestShouldSendFace(unittest.TestCase):
    NOON = time.mktime((2026, 11, 8, 14, 0, 0, 0, 0, -1))
    NIGHT = time.mktime((2026, 11, 8, 3, 0, 0, 0, 0, -1))

    def test_image_disabled(self):
        card = card_for("低落", 0.9)
        ok, why = should_send_face(card, now=self.NOON)
        self.assertFalse(ok)
        self.assertEqual(why, "image_disabled")

    def test_low_intensity_never_sends(self):
        card = card_for("开心", 0.1)
        ok, why = should_send_face(card, now=self.NOON)
        self.assertFalse(ok)
        self.assertEqual(why, "intensity_low")

    def test_quiet_hours(self):
        card = card_for("开心", 0.9)
        ok, why = should_send_face(card, now=self.NIGHT)
        self.assertFalse(ok)
        self.assertEqual(why, "quiet_hours")

    def test_cooldown(self):
        card = card_for("开心", 0.9)
        ok, why = should_send_face(card, now=self.NOON, last_sent_at=self.NOON - 60)
        self.assertFalse(ok)
        self.assertEqual(why, "cooldown")

    def test_sends_when_all_clear(self):
        card = card_for("吃醋", 0.8)
        ok, why = should_send_face(card, now=self.NOON, last_sent_at=self.NOON - 3600)
        self.assertTrue(ok)
        self.assertEqual(why, "ok")

    def test_stronger_emoji_comes_more_often(self):
        """情绪越强，冷却越短——但不能短于 5 分钟。"""
        strong = card_for("吃醋", 1.0)
        weak = card_for("吃醋", 0.3)
        # 用同样的「刚发过 6 分钟」当输入
        six_min = 360
        ok_strong, _ = should_send_face(strong, now=self.NOON, last_sent_at=self.NOON - six_min)
        ok_weak, why_weak = should_send_face(weak, now=self.NOON, last_sent_at=self.NOON - six_min)
        self.assertTrue(ok_strong)
        self.assertFalse(ok_weak)
        self.assertEqual(why_weak, "cooldown")


class TestCuteKit(unittest.TestCase):
    def test_every_mood_has_a_mouth(self):
        self.assertEqual(set(CUTE_KIT), set(MOOD_FACE))

    def test_work_scene_shuts_up_the_cute(self):
        """正事档：口癖、颜文字、傲娇全关。"""
        card = card_for("黏人", 0.9, scene="work")
        kit = cute_kit(card)
        self.assertEqual(kit.words, ())
        self.assertEqual(kit.kaomoji, "")
        self.assertEqual(kit.tsundere_tail, "")

    def test_comfort_never_tsundere(self):
        """主人在难过时不许傲娇——不能让他猜。"""
        card = card_for("委屈", 0.9, scene="comfort")
        self.assertEqual(cute_kit(card).tsundere_tail, "")

    def test_low_intensity_no_kaomoji(self):
        card = card_for("开心", 0.1)
        kit = cute_kit(card)
        self.assertEqual(kit.kaomoji, "")
        self.assertEqual(kit.words, ())

    def test_deterministic_per_salt(self):
        card = card_for("吃醋", 0.8)
        self.assertEqual(cute_kit(card, salt=7).to_dict(), cute_kit(card, salt=7).to_dict())

    def test_salt_gives_variety(self):
        """换 salt 会换花样，不然她会像复读机。"""
        card = card_for("开心", 0.8)
        seen = {cute_kit(card, salt=i).kaomoji for i in range(12)}
        self.assertGreater(len(seen), 1)
        words = {cute_kit(card, salt=i).words for i in range(12)}
        self.assertGreater(len(words), 1)

    def test_tsundere_only_where_allowed(self):
        """傲娇只出现在有台阶可下的心情里，且台词来自固定池。"""
        for mood in ("黏人", "吃醋", "委屈", "胡闹"):
            tail = cute_kit(card_for(mood, 0.8), salt=1).tsundere_tail
            self.assertIn(tail, TSUNDERE_TAILS, msg=mood)
        self.assertEqual(cute_kit(card_for("认真", 0.8)).tsundere_tail, "")

    def test_kaomoji_comes_from_pool(self):
        kit = cute_kit(card_for("委屈", 0.8))
        self.assertIn(kit.kaomoji, KAOMOJI["sad"])

    def test_self_reference(self):
        """她自称「本鲸」——这是社区里真正在用的说法。"""
        self.assertEqual(cute_kit(card_for("兴奋", 0.8)).self_ref, "本鲸")
        self.assertEqual(cute_kit(card_for("开心", 0.8)).self_ref, "本鲸")
        self.assertEqual(cute_kit(card_for("平静", 0.5)).self_ref, "我")

    def test_touch_only_when_image_allowed(self):
        """低落时不许发图，也就不许「摸摸头嘛」。"""
        self.assertEqual(cute_kit(card_for("低落", 0.9)).touch_line, "")
        self.assertNotEqual(cute_kit(card_for("开心", 0.9)).touch_line, "")

    def test_unknown_mood_degrades(self):
        """不认识的心里，脸先退回「平静」，且卡上留了降级证据。"""
        card = card_for("莫名其妙的情绪", 0.5)
        self.assertTrue(any(d.startswith("unknown_mood:") for d in card.degraded))
        self.assertEqual(cute_kit(card).self_ref, "我")


if __name__ == "__main__":
    unittest.main(verbosity=2)
