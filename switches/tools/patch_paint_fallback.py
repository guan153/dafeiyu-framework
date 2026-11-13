# -*- coding: utf-8 -*-
"""一次性补丁：给 whale_paint 加「本地画不动 → 元衡兜底」。

改四种情况：
1. local_unavailable（本机画笔 ping 不通）
2. comfy_busy（队列忙，提交会被拒）
3. local_error（画的过程中抛异常：OOM/超时/显存被 TTS 占）
4. local_no_file（跑完没产出文件）

兜底受三个约束：元衡就绪、今天还有额度、兜底次数没超上限。
每次兜底写日志 + 记账（counter.fallback + history.fallback）。

幂等：已经打过补丁就不重复改。
"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

TARGET = Path(r"%ASTRBOT_HOME%\data\plugins\astrbot_plugin_whale_paint\main.py")
MARK = "whale_core.imagegen"

HELPERS = '''
    def _meta_remaining(self) -> int:
        """元衡今天还剩几张（读不到就按 0，避免瞎花额度）。"""
        try:
            backend = self._meta_backend_obj()
            return int(backend.remaining()) if backend is not None else 0
        except Exception:
            return 0

    def _can_fallback_meta(self, local_error: str = "local_failed") -> bool:
        """本地画不动时，能不能改走元衡（纯决策，便于单测）。"""
        try:
            import sys as _sys
            from pathlib import Path as _Path
            data_dir = _Path(__file__).resolve().parents[2]
            if str(data_dir) not in _sys.path:
                _sys.path.insert(0, str(data_dir))
            from whale_core.imagegen import can_fallback
        except Exception:
            logger.warning("[画兴] 兜底决策模块不可用，保持原行为")
            return False
        day = self._day_counter(self._today())
        decision = can_fallback(
            local_ok=False,
            local_error=local_error,
            meta_ready=self._meta_available(),
            meta_remaining=self._meta_remaining(),
            fallback_enabled=self._b("fallback_meta_on_local_fail", True),
            fallback_used_today=int(day.get("fallback", 0)),
            fallback_daily_cap=int(self._c("fallback_daily_cap", 3)),
        )
        if decision.use_meta:
            logger.info("[画兴] 允许元衡兜底：%s", decision.reason)
        return decision.use_meta

'''

OLD_AVAILABLE = '''    def _meta_available(self) -> bool:
        try:
            backend = self._meta_backend_obj()
            return bool(backend and not backend.available())
        except Exception:
            return False
'''

NEW_AVAILABLE = '''    def _meta_available(self) -> bool:
        """元衡就绪 = 脚本/key 齐 + 今天还有额度。"""
        try:
            backend = self._meta_backend_obj()
            if not backend:
                return False
            return (not backend.available()) and self._meta_remaining() > 0
        except Exception:
            return False
''' + HELPERS

OLD_GUARDS = '''            if engine == "local" and not local_ok:
                return {"ok": False, "reason": "local_unavailable"}
            if engine == "local" and not self._comfy_idle():
                return {"ok": False, "reason": "comfy_busy"}
'''

NEW_GUARDS = '''            if engine == "local" and not local_ok:
                if self._can_fallback_meta("local_unavailable"):
                    logger.info("[画兴] 本机画笔不可用，改走元衡")
                    engine, fell_back = "meta", True
                else:
                    return {"ok": False, "reason": "local_unavailable"}
            if engine == "local" and not self._comfy_idle():
                if self._can_fallback_meta("comfy_busy"):
                    logger.info("[画兴] ComfyUI 忙，改走元衡")
                    engine, fell_back = "meta", True
                else:
                    return {"ok": False, "reason": "comfy_busy"}
'''

OLD_DRAW = '''            t0 = time.time()
            if engine == "local":
                result = await asyncio.to_thread(self._draw_local, inp)
                engine_label = "本机画笔"
            else:
                result = await asyncio.to_thread(self._draw_meta, inp)
                engine_label = "元衡"
'''

NEW_DRAW = '''            t0 = time.time()
            engine_label = "本机画笔" if engine == "local" else "元衡"
            if engine == "local":
                try:
                    result = await asyncio.to_thread(self._draw_local, inp)
                    if not result.get("path") and self._can_fallback_meta("local_no_file"):
                        logger.warning("[画兴] 本机画笔没产出文件，改走元衡")
                        result = await asyncio.to_thread(self._draw_meta, inp)
                        engine, engine_label, fell_back = "meta", "元衡（本地无产出兜底）", True
                except Exception as exc:  # noqa: BLE001
                    if self._can_fallback_meta("local_error:%s" % type(exc).__name__):
                        logger.warning("[画兴] 本机画笔失败（%s），改走元衡", str(exc)[:120])
                        result = await asyncio.to_thread(self._draw_meta, inp)
                        engine, engine_label, fell_back = "meta", "元衡（本地失败兜底）", True
                    else:
                        raise
            else:
                result = await asyncio.to_thread(self._draw_meta, inp)
'''

OLD_INIT_FLAG = '''            local_ok = self._check_local()
            meta_ok = self._meta_available()
'''
NEW_INIT_FLAG = '''            local_ok = self._check_local()
            meta_ok = self._meta_available()
            fell_back = False
'''

OLD_RECORD = '''                "sent": sent,
                "manual": manual,
            }
'''
NEW_RECORD = '''                "sent": sent,
                "manual": manual,
                "fallback": bool(fell_back),
            }
'''

OLD_COUNTER = '''            if engine == "meta":
                counter["meta"] = int(counter.get("meta") or 0) + 1
            else:
                counter["local"] = int(counter.get("local") or 0) + 1
'''
NEW_COUNTER = '''            if engine == "meta":
                counter["meta"] = int(counter.get("meta") or 0) + 1
                if fell_back:
                    counter["fallback"] = int(counter.get("fallback") or 0) + 1
            else:
                counter["local"] = int(counter.get("local") or 0) + 1
'''


def apply(text: str) -> tuple[str, list[str]]:
    done: list[str] = []
    for name, old, new in (
        ("meta_available+helpers", OLD_AVAILABLE, NEW_AVAILABLE),
        ("guards", OLD_GUARDS, NEW_GUARDS),
        ("draw", OLD_DRAW, NEW_DRAW),
        ("init_flag", OLD_INIT_FLAG, NEW_INIT_FLAG),
        ("record", OLD_RECORD, NEW_RECORD),
        ("counter", OLD_COUNTER, NEW_COUNTER),
    ):
        if old not in text:
            raise SystemExit(f"锚点没找到：{name}（文件可能已改过）")
        text = text.replace(old, new, 1)
        done.append(name)
    return text, done


def main() -> int:
    raw = TARGET.read_text(encoding="utf-8")
    if MARK in raw:
        print("已经打过补丁（含 whale_core.imagegen），跳过")
        return 0
    new_text, done = apply(raw)
    backup = TARGET.with_suffix(f".bak-fallback-{time.strftime('%Y%m%d-%H%M%S')}.py")
    shutil.copy2(TARGET, backup)
    TARGET.write_text(new_text, encoding="utf-8")
    print(f"已备份：{backup.name}")
    print("已应用：" + "、".join(done))
    return 0


if __name__ == "__main__":
    sys.exit(main())
