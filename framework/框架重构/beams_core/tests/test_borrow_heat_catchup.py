"""iter35 借鉴补丁单测：A 闭式补算 + B 同类事件退热（模块版与单文件版同步）。

覆盖：
- A：停机 8h 后 tick 一次，fatigue 约 ×0.5、stress 约 ×0.5^(8/24)，误差 <5%；30 天封顶不炸；
      正常 gap（<=1h）补算差额为 0，行为不变；
- B：连发 30 条正向事件，第 n 条折损 ≈ 1/(1+(n-1)/12)；负向事件折损恒为 1.0，
      且负向连发 30 次的慢通道推进与"没有 heat"逐位一致；600s 窗口过期复位；heat 不落盘；
- 同步：beams.py 单文件版同场景补算结果与模块版一致（容差 1e-4）。

跑法：python beams_core/tests/run_tests.py（本文件会被 discover 到）
注意：TEMP 必须指向可写目录，否则 Windows 下 tempfile 会退避重试。
"""
import json
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))  # 框架重构/
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from beams_core import schema  # noqa: E402
from beams_core.kernel import BeamsKernel  # noqa: E402

BODY = {"cpu": 0.2, "mem": 0.4, "latency_ms": 100.0, "errors_1h": 0,
        "disk_free": 0.5, "tools": {"net": True, "vision": True, "tts": False, "code": True}}
CTX = {"hour": 20}
SLOW_KEYS = ("mood_valence", "mood_arousal", "stress", "bonding", "fatigue")


def new_kernel(tag):
    tmp = tempfile.mkdtemp(prefix="iter35_" + tag + "_")
    return BeamsKernel(os.path.join(tmp, "state.json"), autosave=False)


def set_clock(k, ts, **slow):
    st = schema.validate(k.state)
    st["ts"] = float(ts)
    for key, val in slow.items():
        st["slow"][key] = float(val)
    k.state = st


def assert_slow_legal(tc, slow):
    for key in SLOW_KEYS:
        val = float(slow[key])
        tc.assertFalse(val != val, "slow.%s 是 NaN" % key)
        tc.assertTrue(abs(val) < 1e9, "slow.%s 是 Inf" % key)
    tc.assertTrue(-1.0 <= float(slow["mood_valence"]) <= 1.0)
    tc.assertTrue(0.0 <= float(slow["mood_arousal"]) <= 1.0)
    tc.assertTrue(0.0 <= float(slow["stress"]) <= 1.0)
    tc.assertTrue(0.0 <= float(slow["bonding"]) <= 1.0)
    tc.assertTrue(0.0 <= float(slow["fatigue"]) <= 1.0)


class TestCatchup(unittest.TestCase):
    """A. 闭式补算：停机 gap 按真实时间一次补算，不再只按 1h 衰减。"""

    def test_catchup_8h_matches_half_life(self):
        k = new_kernel("catch8h")
        now = 1_800_000_000.0
        set_clock(k, now - 8 * 3600.0, fatigue=0.95, stress=0.60)
        k.tick(body_ctx=BODY, ctx=CTX, now=now)
        slow = k.state["slow"]
        exp_fatigue = 0.95 * 0.5 ** (8.0 / 8.0)   # 疲劳半衰期 8h → ×0.5
        exp_stress = 0.60 * 0.5 ** (8.0 / 24.0)   # 压力半衰期 24h → ×0.5^(1/3)
        fat_err = abs(float(slow["fatigue"]) - exp_fatigue) / exp_fatigue
        str_err = abs(float(slow["stress"]) - exp_stress) / exp_stress
        self.assertLess(fat_err, 0.05,
                        "fatigue 补算误差 %.1f%% > 5%%（实际 %.4f / 理论 %.4f）"
                        % (100.0 * fat_err, float(slow["fatigue"]), exp_fatigue))
        self.assertLess(str_err, 0.05,
                        "stress 补算误差 %.1f%% > 5%%（实际 %.4f / 理论 %.4f）"
                        % (100.0 * str_err, float(slow["stress"]), exp_stress))
        assert_slow_legal(self, slow)
        self.assertEqual(int(k.state["tick_count"]), 1)

    def test_catchup_capped_at_7_days(self):
        k = new_kernel("catch30d")
        now = 1_800_000_000.0
        set_clock(k, now - 30 * 24 * 3600.0, fatigue=0.90, stress=0.80)
        t0 = time.time()
        k.tick(body_ctx=BODY, ctx=CTX, now=now)
        self.assertLess(time.time() - t0, 5.0, "30 天补算不应卡住")
        slow = k.state["slow"]
        # 30 天远大于 7 天封顶：按封顶衰减后压力/疲劳应几乎归零，且仍合法
        self.assertLess(float(slow["stress"]), 0.02)
        self.assertLess(float(slow["fatigue"]), 0.02)
        assert_slow_legal(self, slow)
        self.assertEqual(int(k.state["tick_count"]), 1)

    def test_normal_gap_unchanged(self):
        """gap <= 1h 时补算差额为 0：自动 dt_s 与显式 dt_s 两条路径结果一致。"""
        now = 1_800_000_000.0
        a = new_kernel("normal_auto")
        b = new_kernel("normal_step")
        set_clock(a, now - 5.0, fatigue=0.6, stress=0.5)
        set_clock(b, now - 5.0, fatigue=0.6, stress=0.5)
        a.tick(body_ctx=BODY, ctx=CTX, now=now)            # dt_s 自动 = 5s
        b.tick(body_ctx=BODY, ctx=CTX, now=now, dt_s=5.0)  # dt_s 显式
        for key in SLOW_KEYS:
            self.assertAlmostEqual(float(a.state["slow"][key]), float(b.state["slow"][key]),
                                   places=12, msg="正常路径被补算改动：%s" % key)


class TestHeat(unittest.TestCase):
    """B. 同类事件退热：窗口 600s，只压正向，折损 ×1/(1+n/12)。"""

    def test_positive_heat_curve(self):
        k = new_kernel("heatpos")
        factors = []
        for i in range(30):
            k.feed("chat", goal_congruence=0.3, intensity=0.6, why="连聊 %d" % i)
            factors.append(float(k._events[-1]["intensity"]) / 0.6)
        self.assertEqual(len(factors), 30)
        self.assertAlmostEqual(factors[0], 1.0, places=6)
        for i, got in enumerate(factors):
            expect = 1.0 / (1.0 + i / 12.0)
            self.assertLess(abs(got - expect), 0.01,
                            "第 %d 条折损 %.4f，理论 %.4f" % (i + 1, got, expect))
        self.assertLess(abs(factors[-1] - 12.0 / 41.0), 0.01)  # 第 30 条 ≈ 0.2927
        self.assertLess(abs(factors[-1] - 0.29), 0.05)         # 任务书口径：0.29 ±0.05
        for i in range(29):
            self.assertGreaterEqual(factors[i], factors[i + 1] - 1e-9)

    def test_negative_events_not_heated(self):
        k = new_kernel("heatneg")
        ctl = new_kernel("heatneg_ctl")
        # 把 cold 的 heat 塞满 200 条（模拟已经连聊很久），负向仍不应被乘
        now0 = time.time()
        k._heat["cold"] = [now0 - 1.0] * 200
        for i in range(30):
            k.feed("cold", goal_congruence=-0.5, intensity=0.8, why="冷 %d" % i)
            ctl.feed("cold", goal_congruence=-0.5, intensity=0.8, why="冷 %d" % i)
            self.assertAlmostEqual(float(k._events[-1]["intensity"]), 0.8, places=9)
            self.assertAlmostEqual(float(ctl._events[-1]["intensity"]), 0.8, places=9)
        # 慢通道推进必须与"没有 heat"完全一致（每拍最多消化 8 条 → 4 拍清空）
        now = 1_800_000_000.0
        set_clock(k, now)
        set_clock(ctl, now)
        for t in range(4):
            tick_ts = now + (t + 1) * 1.0
            k.tick(body_ctx=BODY, ctx=CTX, now=tick_ts, dt_s=1.0)
            ctl.tick(body_ctx=BODY, ctx=CTX, now=tick_ts, dt_s=1.0)
        for key in SLOW_KEYS:
            self.assertAlmostEqual(float(k.state["slow"][key]),
                                   float(ctl.state["slow"][key]), places=12,
                                   msg="负向事件被 heat 影响：%s" % key)

    def test_heat_window_expiry(self):
        k = new_kernel("heatwin")
        now0 = time.time()
        k._heat["chat"] = [now0 - 601.0] * 5  # 全部在 600s 窗口外
        k.feed("chat", goal_congruence=0.3, intensity=0.6, why="窗口外")
        self.assertAlmostEqual(float(k._events[-1]["intensity"]), 0.6, places=9)
        k.feed("chat", goal_congruence=0.3, intensity=0.6, why="窗口内")
        expect = 0.6 / (1.0 + 1.0 / 12.0)
        self.assertAlmostEqual(float(k._events[-1]["intensity"]), expect, places=3)

    def test_heat_not_persisted(self):
        k = new_kernel("heatdisk")
        k.feed("chat", goal_congruence=0.3, intensity=0.6, why="别落盘")
        self.assertTrue(k._heat)
        k.save()
        with open(k.state_path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        self.assertNotIn("_heat", json.dumps(raw, ensure_ascii=False))


class TestSingleFileSync(unittest.TestCase):
    """单文件版 beams.py 必须同步：同一次 8h 停机补算，两版慢通道一致（容差 1e-4）。"""

    def test_catchup_parity(self):
        try:
            import beams
        except Exception as exc:  # noqa: BLE001
            self.skipTest("单文件版不可导入：%s" % exc)
        tmp = tempfile.mkdtemp(prefix="iter35_parity_")
        mod = BeamsKernel(os.path.join(tmp, "mod.json"), autosave=False)
        single = beams.Kernel(os.path.join(tmp, "single.json"), autosave=False)
        base = time.time() - 8 * 3600.0
        for kk in (mod, single):
            st = dict(kk.state)
            st["ts"] = base
            st["slow"] = dict(st["slow"], fatigue=0.80, stress=0.60)
            kk.state = st
        mod.tick(body_ctx=BODY, ctx=CTX, dt_s=5.0)
        single.tick(body_ctx=BODY, ctx=CTX, dt_s=5.0)
        for key in SLOW_KEYS:
            a = float(mod.state["slow"][key])
            b = float(single.state["slow"][key])
            self.assertLess(abs(a - b), 1e-4,
                            "两版补算不一致：%s %.6f vs %.6f" % (key, a, b))


if __name__ == "__main__":
    unittest.main(verbosity=2)