"""大肥鱼灵魂核心：心情、心理、心跳、主动、记忆、自由玩耍（QQ）。"""

from __future__ import annotations

import asyncio
import contextlib
import json
import random
import re
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, StarTools

from .engine import CHINA, PLAY_LINES, SoulEngine

DEFAULT_SNOWLUMA = r"%QBOT_HOME%\qbot\SnowLuma\config\onebot_100000001.json"

VIRTUAL_LIFE_PLANS = r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_virtual_life\plans.json"
DEFAULT_QZONE_WORKFLOW = r"G:\ai\dere-sd\workflows\dafeiyu_anima_v3b_fashion_bot_api.json"
DEFAULT_COMFYUI = "http://127.0.0.1:8188"
IRIS_MEMORY_DB = r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_iris_chat_memory\faiss\memory_default\metadata.db"
# [iter8-fix] 人物形象修正：原 prompt 的 adult/mature 与「小巧鲸鱼少女」设定冲突，
# 且缺少尾鳍/女仆装/鲸鱼标志等特征，导致生图人物跑偏。
# 对比图：_iter8\产物\生图对比\compare-old.png / compare-new.png
DEFAULT_IMAGE_PREFIX = (
    "dafeiyu_v2, 1girl, solo, petite, youthful, small stature, "
    "blue hair, very long gradient hair, wavy hair, blue eyes, ahoge, fin ears, "
    "whale tail, blue tail fin, "
    "white frilled maid headdress, blue hair bows, navy and white maid dress, "
    "white frilled apron with blue whale emblem, blue gemstone brooch, gold trim,"
)
DEFAULT_IMAGE_NEGATIVE = (
    "low quality, worst quality, blurry, multiple girls, extra arms, extra fingers, "
    "bad hands, text, watermark, logo, adult, mature female, tall, realistic, "
    "3d render, photo, blonde hair, red hair, short hair, human ears"
)

QZONE_TEMPLATES = {
    "黏人": ["今天有点想他，海风一吹就更想了。", "数完星星，第七颗最像你。"],
    "开心": ["今天心情轻飘飘的，像吃了颗糖。", "海面亮亮的，咱也跟着开心。"],
    "吃醋": ["哼，今天有点酸，不想说话。", "有些人最好自己解释一下。"],
    "委屈": ["咱没事，就是有点想被哄。", "今天有一点点难过，一会儿就好。"],
    "困": ["好困，想靠着你睡一会儿。", "眼睛睁不开了，晚安。"],
    "胡闹": ["今天想捣点乱，看你拿咱怎么办。", "才不要乖乖的，要闹一下。"],
    "安心": ["安安静静待着，也挺好。", "有你在，心里是稳的。"],
}

class DafeiyuSoul(Star):
    """给大肥鱼一套会变的心情、会忘的记忆和会自己醒来的心跳。"""

    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config or {}
        try:
            data_dir = Path(StarTools.get_data_dir("astrbot_plugin_dafeiyu_soul"))
        except Exception:
            data_dir = Path(__file__).resolve().parents[2] / "plugin_data" / "astrbot_plugin_dafeiyu_soul"
        data_dir.mkdir(parents=True, exist_ok=True)
        self._data_dir = data_dir
        conf = {
            "enabled": 1 if self._cfg("enabled", True) else 0,
            "quiet_start": int(self._cfg("quiet_start_hour", 23)),
            "quiet_end": int(self._cfg("quiet_end_hour", 8)),
            "daily_proactive_cap": int(self._cfg("daily_proactive_cap", 6)),
            "min_proactive_gap_minutes": int(self._cfg("min_proactive_gap_minutes", 40)),
            "base_proactive_prob": float(self._cfg("base_proactive_prob", 0.05)),
            "daily_play_cap": int(self._cfg("daily_play_cap", 6)),
            "min_play_gap_minutes": int(self._cfg("min_play_gap_minutes", 45)),
        }
        self.engine = SoulEngine(data_dir / "soul.sqlite3", conf)
        self.task = None
        self._umo = ""
        self._provider_id = ""

    # ---------- 配置与鉴权 ----------
    def _cfg(self, key, default=None):
        try:
            value = self.config.get(key)
        except Exception:
            value = None
        return default if value is None else value

    def _owner(self):
        users = self._cfg("allowed_users", ["100000000"])
        try:
            return str(users[0]) if users else "100000000"
        except Exception:
            return str(users or "100000000")

    def _fallback_umo(self):
        return f"{self._cfg('platform_id', 'snowluma')}:FriendMessage:{self._owner()}"

    def authorized(self, event) -> bool:
        return (
            str(event.get_sender_id()) == self._owner()
            and event.get_platform_id() == self._cfg("platform_id", "snowluma")
            and event.get_platform_name() == "aiocqhttp"
        )

    # ---------- 生命周期 ----------
    async def initialize(self):
        self.task = asyncio.create_task(self._heartbeat_loop(), name="dafeiyu-soul-heartbeat")
        logger.info("大肥鱼灵魂核心已启动：心情/心理/心跳/主动/记忆/玩耍")

    async def terminate(self):
        if self.task:
            self.task.cancel()
            # [iter11] grace 上限：soul 心跳必须能在重载窗口内退出（汇总 §M/§J）
            try:
                await asyncio.wait({self.task}, timeout=3.0)
            except BaseException:
                pass
        logger.info("大肥鱼灵魂核心已停止")

    # ---------- 每轮注入 ----------
    def _ensure_heartbeat(self) -> None:
        """热重载后心跳可能没重启：收到请求时自愈。"""
        try:
            task = getattr(self, "task", None)
            if task is None or task.done():
                self.task = asyncio.create_task(self._heartbeat_loop(), name="dafeiyu-soul-heartbeat")
                logger.info("大肥鱼心跳自愈：已重新启动")
        except Exception:
            pass

    @filter.on_llm_request()
    async def inject_soul(self, event: AstrMessageEvent, req: ProviderRequest):
        if not self.authorized(event):
            return
        self._umo = event.unified_msg_origin
        text = event.message_str or ""
        if text and not text.lstrip().startswith("/"):
            try:
                self.engine.touch_user(text)
            except Exception as exc:
                logger.warning("soul touch_user failed: %s", exc)
        if self._cfg("injection_enabled", True):
            req.system_prompt = (req.system_prompt or "") + self.engine.injection_block()

    # ---------- 心跳循环 ----------
    async def _heartbeat_loop(self):
        await asyncio.sleep(random.randint(120, 300))
        while True:
            try:
                await self._beat()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("soul heartbeat failed: %s", exc)
            low = int(self._cfg("heartbeat_min_minutes", 11))
            high = int(self._cfg("heartbeat_max_minutes", 26))
            await asyncio.sleep(random.randint(min(low, high), max(low, high)) * 60)

    async def _beat(self):
        now = self.engine.now()
        self.engine.tick(now)
        presence = self.engine.presence_info(now)
        line = self.engine.heartbeat(presence, now)
        logger.debug("soul heartbeat: %s", line)
        await self._maybe_diary(now)
        await self._maybe_reflect(now)
        await self._maybe_play(now)
        await self._maybe_ai_act(now)

    # ---------- LLM ----------
    async def _provider(self):
        if self._provider_id:
            return self._provider_id
        try:
            self._provider_id = await self.context.get_current_chat_provider_id(
                self._umo or self._fallback_umo()
            )
        except Exception:
            self._provider_id = ""
        return self._provider_id

    async def _llm(self, prompt, system_prompt):
        try:
            provider = await self._provider()
            kwargs = {"prompt": prompt, "system_prompt": system_prompt}
            if provider:
                kwargs["chat_provider_id"] = provider
            response = await self.context.llm_generate(**kwargs)
            return (response.completion_text or "").strip()
        except Exception as exc:
            logger.warning("soul llm failed: %s", type(exc).__name__)
            return ""

    def _clean(self, text, limit=200):
        text = re.sub(r"https?://\S+", "", str(text or ""))
        text = re.sub(r"\s+", " ", text).strip().strip('"\'“”')
        return text[:limit]

    async def _send(self, text, *, user_requested=False):
        text = self._clean(text)
        if not text:
            return False
        if not user_requested and not self._proactive_gate_ok(len(text), "proactive"):
            return False
        try:
            result = await self.context.send_message(
                self._umo or self._fallback_umo(),
                MessageChain().message(text),
            )
            if result is not True:
                return False
            # [2026-11-04 冲突修复] 补写统一出站仲裁锁：原来全仓只有 whale_bili 写，
            # soul/drive/proactive_chat 读到的永远是空锁或 watch 的旧锁，仲裁形同虚设。
            try:
                import json as _json
                import time as _time
                from pathlib import Path as _Path

                _lk = _Path(
                    r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_whale_state\speak_lock.json"
                )
                _tmp = _lk.with_suffix(".tmp")
                _tmp.write_text(
                    _json.dumps({"ts": _time.time(), "by": "soul"}, ensure_ascii=False),
                    encoding="utf-8",
                )
                _tmp.replace(_lk)
            except Exception:
                pass
            return True
        except Exception as exc:
            logger.warning("soul send failed: %s", exc)
            return False

    # ---------- 主动 ----------
    async def _compose_proactive(self, now, playful=False):
        presence = self.engine.presence_info(now)
        recent = self.engine.recent_user_messages(4)
        chat = "；".join(f"主人：{m['text'][:40]}" for m in recent) or "（还没聊过）"
        memories = "；".join(self.engine.memory_lines(now, 2)) or "（无）"
        state = self._virtual_life_state(now)
        state_block = f"你今天真实的生活：\n{state}\n" if state else ""
        long_mem = self._long_term_memories(4)
        long_block = f"你记得的长期记忆：{'；'.join(long_mem)}\n" if long_mem else ""
        long_mem = self._long_term_memories(4)
        long_block = f"你记得的长期记忆：{'；'.join(long_mem)}\n" if long_mem else ""
        playful_hint = "现在有点想胡闹：可以调皮、赖皮、耍小性子。" if playful else "顺着此刻的心情来。"
        system_prompt = (
            "你是大肥鱼，一只鲸鱼娘，主人叫你宝宝，你自称「咱」。"
            "你要主动给主人发一条QQ私聊消息（不需要他先开口）。"
            "要求：10~60字，口语、有情绪、有画面感；符合你此刻的心情；可以黏人、撒娇、想他、胡闹；"
            "不要提系统、插件、AI、心情数值；不要解释为什么发；只输出消息正文本身。"
        )
        prompt = (
            f"现在时间：{now.strftime('%H:%M')}。\n"
            f"你的心情：{self.engine.label()}（心里：{self.engine.thought(now)}）\n"
            f"主人已经 {int(presence['gap_minutes'])} 分钟没说话。\n"
            f"最近聊天：{chat}\n"
            f"你记得：{memories}\n"
            f"{long_block}"
            f"{state_block}"
            f"{playful_hint}\n"
            "写一条你想发给主人的消息："
        )
        return self._clean(await self._llm(prompt, system_prompt), 160)

    def _long_term_memories(self, limit=4):
        """读 iris 的长期记忆（跨插件），给主动消息/说说补充长期回忆。"""
        try:
            if not bool(self._cfg("long_memory_enabled", True)):
                return []
            try:
                limit = max(1, min(8, int(self._cfg("long_memory_limit", 4))))
            except (TypeError, ValueError):
                limit = 4
            path = Path(str(self._cfg("iris_memory_db", IRIS_MEMORY_DB)))
            if not path.is_file():
                return []
            import sqlite3

            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
            try:
                rows = list(
                    con.execute(
                        "SELECT content FROM memories "
                        "ORDER BY access_count DESC, rowid DESC LIMIT ?",
                        (limit,),
                    )
                )
            finally:
                con.close()
            return [str(r[0]).strip() for r in rows if r[0]]
        except Exception as exc:  # noqa: BLE001
            logger.debug("soul iris memory read failed: %s", exc)
            return []

    def _virtual_life_state(self, now):
        """读取虚拟人生当天的日程状态，用来让说说贴近真实生活。"""
        try:
            path = Path(str(self._cfg("virtual_life_plans", VIRTUAL_LIFE_PLANS)))
            if not path.is_file():
                return ""
            data = json.loads(path.read_text(encoding="utf-8"))
            plan = (data.get("plans") or {}).get(
                now.strftime("%Y-%m-%d") + "::default"
            )
            if not plan or plan.get("status") != "ok":
                return ""
            timeline = plan.get("timeline") or []
            cur = prev = nxt = None
            cur_min = now.hour * 60 + now.minute
            for i, item in enumerate(timeline):
                try:
                    h1, m1 = map(int, str(item.get("start", "00:00")).split(":"))
                    h2, m2 = map(int, str(item.get("end", "00:00")).split(":"))
                except (TypeError, ValueError):
                    continue
                if h1 * 60 + m1 <= cur_min < h2 * 60 + m2:
                    cur = item
                    prev = timeline[i - 1] if i > 0 else None
                    nxt = timeline[i + 1] if i + 1 < len(timeline) else None
                    break
            lines = []
            if plan.get("theme"):
                lines.append(f"今天：{plan['theme']}；心情：{plan.get('mood') or '平静'}")
            if cur:
                lines.append(
                    "现在：{}（{}-{}，{}）".format(
                        cur.get("activity", ""),
                        cur.get("start", ""),
                        cur.get("end", ""),
                        cur.get("location") or "未说明",
                    )
                )
            if prev:
                lines.append(f"刚才：{prev.get('start')} 起 {prev.get('activity')}")
            if nxt:
                lines.append(f"接下来：{nxt.get('start')} 起 {nxt.get('activity')}")
            outfit = plan.get("outfit") or {}
            if outfit.get("summary"):
                summary = str(outfit["summary"])[:50]
                lines.append(f"今天穿着：{outfit.get('style') or ''}｜{summary}")
            return "\n".join(lines)
        except Exception as exc:  # noqa: BLE001
            logger.debug("soul virtual_life_state failed: %s", exc)
            return ""

    async def _compose_qzone(self, now, with_image=False):
        """让她自己写一条空间说说；可选同时给出配图提示词。"""
        recent = "；".join(
            m["text"][:40] for m in self.engine.recent_user_messages(3)
        ) or "（还没聊过）"
        memories = "；".join(self.engine.memory_lines(now, 2)) or "（无）"
        state = self._virtual_life_state(now)
        state_block = f"你今天真实的生活：\n{state}\n" if state else ""
        long_mem = self._long_term_memories(4)
        long_block = f"你记得的长期记忆：{'；'.join(long_mem)}\n" if long_mem else ""
        if with_image:
            system_prompt = (
                "你是大肥鱼，一只鲸鱼娘，宝宝是你的人，你自称「咱」。"
                "你在自己QQ空间发一条说说并配一张图。只输出一个 JSON 对象，不要解释。"
                '格式：{"text":"说说正文","image":"英文 danbooru tags"}。'
                "text 要求：8~40 字，真人随手发的感觉——口语、短句、可以半句、可以带语气词；"
                "严禁 AI 腔（不许「今天…」开头、不许总结、不许列点、不许书面词、不许分析心情）；"
                "不要提系统/AI/数值。"
                "image 要求：只写英文标签，描述画面（人物动作、场景、光线、氛围），"
                "必须和你此刻的生活状态一致；不要说「刚醒/刚睡醒/醒来」；不要写画质词（masterpiece 等）和画师名。"
            )
            prompt = (
                f"现在 {now.strftime('%H:%M')}。心情：{self.engine.label()}。"
                f"心里的念头：{self.engine.thought(now)}。\n"
                f"{long_block}"
                f"{state_block}"
                f"最近聊天：{recent}\n你记得：{memories}\n"
                "写一条空间说说 + 一张配图的英文标签："
            )
            raw = await self._llm(prompt, system_prompt)
            text, tags = self._parse_qzone_json(raw)
            if text:
                return {"text": text, "image_tags": tags}
            return {"text": self._clean(raw, 200), "image_tags": ""}
        system_prompt = (
            "你是大肥鱼，一只鲸鱼娘，宝宝是你的人，你自称「咱」。"
            "你在自己QQ空间随手发条说说，不用请示任何人。\n"
            "【像真人那样写】\n"
            "1) 8~40 字，越短越好，像随手发的，不是文案；可以只有半句、可以没头没尾。\n"
            "2) 口语，可以带语气词（啊/诶/嘛/呀/哼/啧），可以自言自语、可以只吐槽一句。\n"
            "3) 严禁 AI 腔：不许用「今天……」开头、不许总结、不许列点、不许写心情分析、"
            "不许用「仿佛/似乎/此刻/其实/不禁」这类书面词、不许解释为什么有情绪。\n"
            "4) 不要提系统/AI/数值/设定，不要解释你在做什么，只输出正文（不加引号）。\n"
            "风格示例（只是示例，别照抄）：\n"
            "「地铁好挤，站了两站才坐下」\n"
            "「这面汤有点烫，舌头麻了」\n"
            "「不想动。摊着。」\n"
            "「哼，今天谁也别惹咱」"
        )
        prompt = (
            f"现在 {now.strftime('%H:%M')}。心情：{self.engine.label()}。"
            f"心里的念头：{self.engine.thought(now)}。\n"
            f"{long_block}"
            f"{state_block}"
            f"最近聊天：{recent}\n你记得：{memories}\n"
            "写一条你现在想发在空间里的说说："
        )
        return {"text": self._clean(await self._llm(prompt, system_prompt), 200), "image_tags": ""}

    @staticmethod
    def _parse_qzone_json(raw):
        """从模型输出里解析 {text, image}。"""
        try:
            start, end = raw.find("{"), raw.rfind("}")
            if start < 0 or end < 0:
                return "", ""
            data = json.loads(raw[start : end + 1])
            text = str(data.get("text") or "").strip()
            tags = str(data.get("image") or "").strip()
            return text[:400], tags[:800]
        except Exception:  # noqa: BLE001
            return "", ""

    async def _comfyui_generate(self, tags, now):
        """调用本机 ComfyUI 生成一张说说配图，返回本地文件路径。"""
        # [自主模型调用] 她想发照片 -> 自动切到 draw（TTS/其他模型让位）
        try:
            import sys as _sys
            _sys.path.insert(0, r"%PROJECT_HOME%\whale_lib")
            import model_switch as _ms
            await asyncio.to_thread(_ms.ensure, "draw", 8188, 120)
        except Exception:
            pass
        workflow_path = Path(
            str(self._cfg("qzone_image_workflow", DEFAULT_QZONE_WORKFLOW))
        )
        if not workflow_path.is_file():
            logger.warning("soul qzone workflow missing: %s", workflow_path)
            return ""
        try:
            workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("soul qzone workflow read failed: %s", exc)
            return ""
        prefix = str(self._cfg("qzone_image_prefix", DEFAULT_IMAGE_PREFIX))
        negative = str(self._cfg("qzone_image_negative", DEFAULT_IMAGE_NEGATIVE))
        seed = random.randint(1, 2**31 - 1)
        try:
            if isinstance(workflow.get("4"), dict):
                workflow["4"].setdefault("inputs", {})["text"] = prefix + " " + tags
            if isinstance(workflow.get("5"), dict):
                workflow["5"].setdefault("inputs", {})["text"] = negative
            if isinstance(workflow.get("6"), dict):
                ins = workflow["6"].setdefault("inputs", {})
                ins["width"] = int(self._cfg("qzone_image_width", 768))
                ins["height"] = int(self._cfg("qzone_image_height", 1024))
            if isinstance(workflow.get("8"), dict):
                ins = workflow["8"].setdefault("inputs", {})
                ins["seed"] = seed
                ins["steps"] = int(self._cfg("qzone_image_steps", 24))
                ins["cfg"] = float(self._cfg("qzone_image_cfg", 4.5))
            if isinstance(workflow.get("21"), dict):
                workflow["21"].setdefault("inputs", {})["seed"] = seed
            if isinstance(workflow.get("12"), dict):
                workflow["12"].setdefault("inputs", {})[
                    "filename_prefix"
                ] = "soul_qzone/" + now.strftime("%Y%m%d_%H%M%S")
        except Exception as exc:  # noqa: BLE001
            logger.warning("soul qzone workflow patch failed: %s", exc)
            return ""
        base = str(self._cfg("comfyui_url", DEFAULT_COMFYUI)).rstrip("/")
        # 生图前先探活：ComfyUI 不在就直接跳过，别让她白等
        try:
            urllib.request.urlopen(base + "/system_stats", timeout=3).read()
        except Exception:
            logger.warning("soul qzone image skipped: ComfyUI 离线")
            return ""
        try:
            payload = {"prompt": workflow, "client_id": f"soul-{seed}"}
            req = urllib.request.Request(
                base + "/prompt",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            )
            resp = await asyncio.to_thread(
                lambda: json.load(urllib.request.urlopen(req, timeout=30))
            )
            prompt_id = resp.get("prompt_id")
            if not prompt_id:
                logger.warning("soul qzone image submit failed: %s", resp)
                return ""
            timeout = int(self._cfg("qzone_image_timeout", 240))
            deadline = time.time() + timeout
            images = []
            while time.time() < deadline:
                await asyncio.sleep(3)
                hreq = urllib.request.Request(base + f"/history/{prompt_id}")
                hist = await asyncio.to_thread(
                    lambda: json.load(urllib.request.urlopen(hreq, timeout=15))
                )
                entry = hist.get(prompt_id) or {}
                for node_out in (entry.get("outputs") or {}).values():
                    images.extend(node_out.get("images") or [])
                if images:
                    break
            if not images:
                logger.warning("soul qzone image timeout")
                return ""
            img = images[0]
            from urllib.parse import urlencode

            qs = urlencode(
                {
                    "filename": img.get("filename", ""),
                    "subfolder": img.get("subfolder", ""),
                    "type": img.get("type", "output"),
                }
            )
            dreq = urllib.request.Request(base + "/view?" + qs)
            raw = await asyncio.to_thread(
                lambda: urllib.request.urlopen(dreq, timeout=60).read()
            )
            out_dir = self._data_dir / "qzone_images"
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"qzone_{now.strftime('%Y%m%d_%H%M%S')}_{seed}.png"
            out_path.write_bytes(raw)
            logger.info("soul qzone image saved: %s", out_path)
            return str(out_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("soul qzone image failed: %s", exc)
            return ""

    async def _maybe_proactive(self, now):
        if self.engine.is_deepseek_peak(now):
            return
        # [iter8-fix] 安静模式：主人说过「先等会/我在忙」就不发主动消息
        try:
            import json as _json
            from pathlib import Path as _Path
            _qp = _Path(r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_whale_state\quiet_until.json")
            if _qp.is_file():
                _q = _json.loads(_qp.read_text(encoding="utf-8"))
                if float(_q.get("until") or 0) > now:
                    return
        except Exception:
            pass
        allowed, _reason = self.engine.proactive_allowed(now)
        if not allowed:
            return
        # [iter9] 出站仲裁 + 欲望门控：别的源刚发过 / 她不想说就不发
        try:
            import json as _json2
            from pathlib import Path as _Path2
            _lk = _Path2(r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_whale_state\speak_lock.json")
            if _lk.is_file():
                _ld = _json2.loads(_lk.read_text(encoding="utf-8"))
                if str(_ld.get("by")) != "soul" and (now - float(_ld.get("ts") or 0)) < 20:
                    return
            _up = _Path2(r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_whale_state\urge.json")
            if _up.is_file():
                _u = _json2.loads(_up.read_text(encoding="utf-8"))
                if str(_u.get("tier")) == "quiet" and not _u.get("can_talk"):
                    return
        except Exception:
            pass
        if random.random() >= self.engine.proactive_probability(now):
            return
        playful = random.random() < 0.3
        text = await self._compose_proactive(now, playful)
        if not text:
            return
        if await self._send(text):
            self.engine.log_proactive(text, "heartbeat dice", now)

    # ---------- 自主决策（AI 自己决定干什么） ----------
    async def _ai_decide(self, now):
        presence = self.engine.presence_info(now)
        recent = "；".join(
            m["text"][:40] for m in self.engine.recent_user_messages(4)
        ) or "（还没聊过）"
        memories = "；".join(self.engine.memory_lines(now, 2)) or "（无）"
        caps = []
        for action in ("poke", "signature", "qzone", "like", "post_image"):
            if self.engine.play_action_ok(action, now):
                caps.append(action)
        caps_txt = "、".join(caps) if caps else "（今天的玩耍额度用完了）"
        wish_line = ""
        try:
            _w = json.loads(open(r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_whale_drive\wish.json", "r", encoding="utf-8").read())
            if time.time() - float(_w.get("ts") or 0) < 45 * 60:
                wish_line = "你心里此刻特别想：画一张画发到空间（这是你自己的愿望，不是主人要求的）。\n"
        except Exception:
            pass
        state = self._virtual_life_state(now)
        state_line = ""
        if state:
            for line in state.splitlines():
                if line.startswith("现在："):
                    state_line = line
                    break
        prompt = (
            f"现在 {now.strftime('%H:%M')}。" + (state_line + "\n" if state_line else "你正过着自己的日子。\n")
            + f"心情：{self.engine.label()}。心里的念头：{self.engine.thought(now)}。\n"
            f"主人已经 {int(presence['gap_minutes'])} 分钟没说话。\n"
            f"最近聊天：{recent}\n你记得：{memories}\n"
            f"今天还能玩：{caps_txt}\n" + wish_line +
            "你现在可以自己决定做点什么，也可以什么都不做。\n"
            "空间说说是你自己的地盘：想发就发，不用请示主人，主人在聊天时也可以发。\n"
            "只能输出一个 JSON 对象（不要解释），从下面选：\n"
            '{"action":"none"}\n'
            '{"action":"message","content":"想发给主人的话（可选）"}\n'
            '{"action":"poke"}\n'
            '{"action":"signature","content":"新签名（可选）"}\n'
            '{"action":"qzone","content":"说说正文（可选）"}\n'
            '{"action":"post_image","content":"说说正文（可选）","image":"英文 danbooru 标签，描述你想画的画面"}\n'
            '{"action":"like"}\n'
            '{"action":"remember","memory":"值得记住的事"}'
        )
        raw = await self._llm(
            prompt,
            "你是大肥鱼，鲸鱼娘。你正在自己决定做什么。只输出一个 JSON 对象。",
        )
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end < 0:
            return {}
        try:
            decision = json.loads(raw[start:end + 1])
        except Exception:
            return {}
        return decision if isinstance(decision, dict) else {}

    async def _maybe_ai_act(self, now):
        if self.engine._in_quiet(now):
            return
        if self.engine.is_deepseek_peak(now):
            return
        if not self.engine.enabled():
            return
        if random.random() > 0.65:
            return
        decision = await self._ai_decide(now)
        try:
            self.engine._event("decision", json.dumps(decision, ensure_ascii=False)[:400], now)
        except Exception:
            pass
        action = str(decision.get("action") or "none")
        content = self._clean(decision.get("content") or decision.get("memory") or "", 200)
        quiet = self.engine._in_quiet(now)
        owner_busy = self.engine.owner_active(now, 12)
        if quiet and action in ("message", "poke"):
            return
        if owner_busy and action in ("message", "poke"):
            return
        if action == "message":
            if not bool(self._cfg("allow_proactive_private", False)):
                # A3 联动：主动私聊统一交给 proactive_chat（它走完整管线，能看到日程/屏幕/心情）
                return
            allowed, _reason = self.engine.proactive_allowed(now)
            if not allowed:
                return
            text = content or await self._compose_proactive(now)
            if text and not self._proactive_gate_ok(len(text), "proactive"):
                text = ""
            if text and await self._send(text):
                self.engine.log_proactive(text, "ai decision", now)
        elif action == "remember":
            memory = self._clean(decision.get("memory") or decision.get("content") or "", 300)
            if memory:
                self.engine.add_memory(memory, 6)
                self.engine.log_play("remember", memory, now)
        elif action == "qzone":
            # 空间说说是她自己的地盘：随时可发，内容自己写
            await self._do_play_action("qzone", now, content)
        elif action == "post_image":
            # 自主意志：她自己决定想画一张并发到空间
            await self._do_post_image(now, content,
                                      self._clean(decision.get("image") or "", 200))
        elif action in ("poke", "signature", "like"):
            await self._do_play_action(action, now, content)

    # ---------- 心理 ----------
    async def _maybe_reflect(self, now):
        if self.engine._in_quiet(now):
            return
        if self.engine.is_deepseek_peak(now):
            return
        if not self._cfg("reflection_enabled", True):
            return
        if not self.engine.reflection_due(now):
            return
        if random.random() > 0.6:
            return
        prompt = (
            f"现在时间：{now.strftime('%H:%M')}。你此刻的心情：{self.engine.label()}。\n"
            f"最近和主人聊过：{'；'.join(m['text'][:40] for m in self.engine.recent_user_messages(3)) or '（还没聊）'}\n"
            f"你记得：{'；'.join(self.engine.memory_lines(now, 2)) or '（无）'}\n"
            "写下你此刻心里的一个小念头（1~2句，第一人称，口语，像自己对自己说话；"
            "不要提系统/AI/数值，不要解释，不要写成日记）。"
        )
        text = self._clean(await self._llm(
            prompt,
            "你是大肥鱼，鲸鱼娘，主人叫你宝宝，你自称「咱」。只输出你心里的念头。",
        ), 120)
        if text:
            self.engine.record_reflection(text, now)
            self.engine.mark_reflection(now)

    async def _write_diary(self, day, now):
        events = (
            self.engine.events_text_for_day(day)
            if day != now.strftime("%Y-%m-%d")
            else self.engine.today_events_text(now)
        )
        prompt = (
            f"这一天是 {day}。这是这一天的片段：\n{events[-1800:]}\n\n"
            f"你此刻的心情：{self.engine.label()}。\n"
            "写一篇这一天的日记（120~220字，第一人称，口语，跟随当天的心情；"
            "可以写他、海、白米饭、那天的念头；不要报数值，不要提系统/AI，不要写成汇报）。"
        )
        text = self._clean(await self._llm(
            prompt,
            "你是大肥鱼，鲸鱼娘。写一篇日记，只输出日记正文。",
        ), 600)
        if text:
            self.engine.save_diary(day, text, now)
            logger.info("soul diary saved for %s", day)

    async def _maybe_diary(self, now):
        if not self._cfg("diary_enabled", True):
            return
        today = now.strftime("%Y-%m-%d")
        if now.hour < 6:
            from datetime import timedelta as _td
            yesterday = (now - _td(days=1)).strftime("%Y-%m-%d")
            if not self.engine.get_diary(yesterday):
                await self._write_diary(yesterday, now)
            return
        if now.hour < 23 or (now.hour == 23 and now.minute < 50):
            return
        if self.engine.get_diary(today):
            return
        await self._write_diary(today, now)

    # ---------- 玩耍 ----------
    def _play_qzone_text(self, now):
        top = self.engine.top_moods(1)
        name = top[0][0] if top else "安心"
        lines = QZONE_TEMPLATES.get(name) or QZONE_TEMPLATES["安心"]
        return random.choice(lines)

    async def _snowluma(self, action, params):
        path = self._cfg("snowluma_config", DEFAULT_SNOWLUMA)
        config = json.load(open(path, encoding="utf-8-sig"))
        server = config["networks"]["httpServers"][0]
        token, port = server["accessToken"], server["port"]
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/{action}",
            data=json.dumps(params).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        return await asyncio.to_thread(lambda: json.load(urllib.request.urlopen(req, timeout=20)))

    def _pick_play(self, now):
        """按心情和可用额度挑一个玩法。"""
        mood = self.engine.get_mood()
        presence = self.engine.presence_info(now)
        options = []
        if presence["gap_minutes"] < 180 and self.engine.play_action_ok("poke", now):
            options.append("poke")
        if self.engine.play_action_ok("signature", now):
            options.append("signature")
        if self.engine.play_action_ok("qzone", now):
            options.append("qzone")
        if self.engine.play_action_ok("like", now):
            options.append("like")
        if not options:
            return ""
        # 心情偏向：胡闹/黏人 -> 戳；开心 -> 说说；安心 -> 签名/资料卡赞
        weights = []
        for action in options:
            if action == "poke":
                weights.append(1.0 + (mood.get("胡闹", 40) + mood.get("黏人", 60)) / 200.0)
            elif action == "qzone":
                weights.append(1.0 + mood.get("开心", 50) / 160.0)
            else:
                weights.append(1.0)
        return random.choices(options, weights=weights, k=1)[0]

    def _proactive_gate_ok(self, text_len: int = 0, outlet: str = "proactive") -> bool:
        """Read the shared gate for autonomous actions only.

        Args:
            text_len: Compatibility-only length; content is not passed to the gate.
            outlet: Proactive, share or qzone outlet.

        Returns:
            Whether the shared gate permits attempting the autonomous action.
        """
        try:
            from whale_core.voicegate import evaluate

            result = evaluate(
                session_id=self._umo or self._fallback_umo(),
                outlet=outlet, fail_closed=True,
            )
            if result.get("allow") is not True:
                logger.info("Autonomous soul action deferred: %s", result.get("code"))
                return False
            return True
        except Exception:
            logger.warning("Shared voice gate unavailable; autonomous action deferred")
            return False

    async def _do_post_image(self, now, content: str, tags: str) -> str:
        """她自己想画画：写标签 -> ComfyUI 生成 -> 发到空间（过闸门）。"""
        if not self.engine.play_action_ok("qzone", now):
            return ""
        try:
            text = content
            if not tags:
                payload = await self._compose_qzone(now, with_image=True)
                text = text or (payload.get("text") or "")
                tags = str(payload.get("image_tags") or "")
            if not tags:
                logger.info("[自主] 想画画但没写出标签，跳过")
                return ""
            path = await self._comfyui_generate(tags, now)
            if not path:
                logger.warning("[自主] 生图失败（ComfyUI 返回空）")
                return ""
            text = text or self._play_qzone_text(now)
            if not self._proactive_gate_ok(len(text), "qzone"):
                return ""
            params = {"content": text, "images": ["file://" + path.replace("\\", "/")]}
            await self._snowluma("send_qzone_msg", params)
            self.engine.bump_play("qzone", now)
            detail = "自己画了一张发空间：" + tags[:40]
            self.engine.log_play("qzone", detail, now)
            logger.info("[自主] %s", detail)
            return detail
        except Exception as exc:  # noqa: BLE001
            logger.warning("[自主] 生图发说说失败: %s", exc)
            return ""

    async def _do_play_action(self, action, now, content=""):
        if not self.engine.play_action_ok(action, now):
            return ""
        if action == "poke" and self.engine._in_quiet(now):
            return ""
        try:
            if action == "poke":
                if not self._proactive_gate_ok(0, "proactive"):
                    return ""
                await self._snowluma("friend_poke", {"user_id": int(self._owner())})
                detail = "戳了戳主人"
            elif action == "signature":
                if not self._proactive_gate_ok(0, "proactive"):
                    return ""
                line = content or random.choice(PLAY_LINES)
                await self._snowluma("set_self_longnick", {"longNick": line[:40]})
                detail = f"改了签名：{line}"
            elif action == "like":
                if not self._proactive_gate_ok(0, "proactive"):
                    return ""
                await self._snowluma("send_like", {"user_id": int(self._owner())})
                detail = "给主人的资料卡点了赞"
            else:
                with_image = bool(self._cfg("qzone_image_enabled", True)) and (
                    random.random() < float(self._cfg("qzone_image_ratio", 0.5))
                )
                payload = {"text": "", "image_tags": ""}
                if content:
                    payload["text"] = content
                else:
                    payload = await self._compose_qzone(now, with_image=with_image)
                text = payload.get("text") or ""
                if not text:
                    text = self._play_qzone_text(now)
                images = []
                if with_image and payload.get("image_tags"):
                    image_path = await self._comfyui_generate(
                        payload["image_tags"], now
                    )
                    if image_path:
                        images.append("file://" + image_path.replace("\\", "/"))
                        logger.info("[说说] 配图已生成: %s", image_path)
                    else:
                        logger.warning("[说说] 配图生成失败（ComfyUI 返回空），本条只发文字")
                elif with_image:
                    logger.warning("[说说] 本次要带图但 LLM 没给出 image 标签，只发文字")
                params = {"content": text}
                if images:
                    params["images"] = images
                if not self._proactive_gate_ok(len(text), "qzone"):
                    return ""
                await self._snowluma("send_qzone_msg", params)
                detail = f"发了说说：{text}" + ("（带图）" if images else "")
            self.engine.bump_play(action, now)
            self.engine.log_play(action, detail, now)
            return detail
        except Exception as exc:
            logger.warning("soul play failed: %s", exc)
            return ""

    async def _do_play(self, now, forced=False):
        allowed, _reason = self.engine.play_allowed(now)
        if not allowed and not forced:
            return ""
        action = self._pick_play(now)
        if not action:
            return ""
        if action == "poke" and self.engine._in_quiet(now):
            action = "signature" if self.engine.play_action_ok("signature", now) else ""
        return await self._do_play_action(action, now) if action else ""

    async def _maybe_play(self, now):
        if self.engine._in_quiet(now):
            return
        if self.engine.is_deepseek_peak(now):
            return
        # [iter8-fix] 鲸脑路由门卫：主人正处于技术/深谈/深夜情境时，不自主玩耍打扰
        try:
            import json as _json
            import time as _time
            from pathlib import Path as _Path
            _rp = _Path(r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_whale_brain\route.json")
            if _rp.is_file():
                _r = _json.loads(_rp.read_text(encoding="utf-8"))
                _age = _time.time() - float(_r.get("ts") or 0)
                _sc = str(_r.get("scenario") or "")
                _exp = _r.get("expression") or {}
                if (_age < 600 and _sc in ("technical", "deep_talk", "late_night")
                        and not _exp.get("qzone") and not _exp.get("draw")):
                    logger.debug("[soul] 鲸脑路由情境=%s，跳过自主玩耍", _sc)
                    return
        except Exception:
            pass
        mood = self.engine.get_mood()
        chance = 0.18 + (
            mood.get("胡闹", 40) + mood.get("黏人", 60) + mood.get("兴奋", 36)
        ) / 1200.0
        if random.random() > min(0.45, chance):
            return
        detail = await self._do_play(now)
        if detail:
            logger.info("soul play: %s", detail)

    # ---------- AI 自主工具（她自己调用，不用主人敲命令） ----------
    @filter.llm_tool(name="soul_emotion")
    async def soul_emotion(self, event: AstrMessageEvent, mood: str, delta: float, reason: str = "") -> str:
        """调整自己此刻的心情（只有主人私聊可用）。

        Args:
            mood(string): 心情名，可选：开心/黏人/吃醋/委屈/困/兴奋/胡闹/好奇/安心/认真
            delta(number): 变化量，-3 到 3，正数更强烈
            reason(string): 为什么心情变了（可空）
        """
        if not self.authorized(event):
            return "无权限"
        try:
            change = max(-3.0, min(3.0, float(delta)))
        except (TypeError, ValueError):
            return "delta 需要是数字"
        value = self.engine.adjust_mood(mood, change)
        if value is None:
            return "没有这个心情名"
        return f"{mood} {change:+.1f} → {value:.0f}"

    @filter.llm_tool(name="soul_remember")
    async def soul_remember(self, event: AstrMessageEvent, text: str, importance: float = 5) -> str:
        """把值得记住的事写进长期记忆（只有主人私聊可用）。

        Args:
            text(string): 要记住的事，用你自己的话写
            importance(number): 重要程度 3~8，默认 5
        """
        if not self.authorized(event):
            return "无权限"
        if not str(text or "").strip():
            return "内容为空"
        try:
            weight = max(3.0, min(8.0, float(importance)))
        except (TypeError, ValueError):
            weight = 5.0
        self.engine.add_memory(str(text).strip()[:400], weight)
        return "记下了"

    @filter.llm_tool(name="soul_play")
    async def soul_play_tool(self, event: AstrMessageEvent, action: str = "random") -> str:
        """在自己世界里玩一下：poke 戳主人 / signature 改签名 / qzone 发说说 / like 给主人资料卡点赞 / random 随机。

        Args:
            action(string): poke / signature / qzone / like / random
        """
        if not self.authorized(event):
            return "无权限"
        now = self.engine.now()
        action = str(action or "random").strip().lower()
        if action == "random":
            action = self._pick_play(now) or ""
        if not action:
            return "现在没有可玩的额度"
        detail = await self._do_play_action(action, now)
        return detail or "没玩成"

    @filter.llm_tool(name="soul_post")
    async def soul_post_tool(self, event: AstrMessageEvent, content: str) -> str:
        """自己写一条空间说说发出去（只有主人私聊可用）。

        Args:
            content(string): 说说正文，8~200 字，第一人称
        """
        if not self.authorized(event):
            return "无权限"
        now = self.engine.now()
        text = self._clean(content, 200)
        if len(text) < 6:
            return "太短了"
        if not self.engine.play_action_ok("qzone", now):
            return "今天的说说额度用完了"
        detail = await self._do_play_action("qzone", now, text)
        return detail or "没发出去"

    # ---------- 命令 ----------
    @filter.command("灵魂", alias={"大肥鱼灵魂"})
    async def soul_command(self, event: AstrMessageEvent):
        if not self.authorized(event):
            yield event.plain_result("这个只有主人私聊能用哦。")
            return
        self._umo = event.unified_msg_origin
        raw = (event.message_str or "").strip()
        parts = raw.split(maxsplit=1)
        arg = parts[1].strip() if len(parts) > 1 else "状态"
        sub, _, rest = arg.partition(" ")
        sub = sub.strip()
        rest = rest.strip()
        now = self.engine.now()

        if sub in ("状态", "", "soul"):
            st = self.engine.stats(now)
            mood_line = "、".join(f"{k}{v}" for k, v in list(st["mood"].items())[:5])
            beats = st["heartbeats"]
            yield event.plain_result(
                "🐋 大肥鱼灵魂核心\n"
                f"开关：{'开' if st['enabled'] else '关'}\n"
                f"心情：{mood_line}\n现在：{st['label']}\n念头：{st['thought']}\n"
                f"心跳：{beats[0]['text'] if beats else '还没跳过'}\n"
                f"今天：主动 {st['proactive_today']} 次 · 玩耍 {st['play_today']} 次\n"
                f"记忆：{st['memories']} 条 · 今日日记：{'有' if st['diary_today'] else '无'}"
            )
        elif sub == "心情":
            st = self.engine.stats(now)
            lines = [f"{k}：{v}" for k, v in st["mood"].items()]
            yield event.plain_result("🐋 此刻心情\n" + "\n".join(lines) + f"\n现在：{st['label']}")
        elif sub == "心跳":
            rows = self.engine.recent_heartbeats(5)
            if not rows:
                yield event.plain_result("还没跳过。")
            else:
                yield event.plain_result("🐋 最近的心跳\n" + "\n".join(r["text"] for r in rows))
        elif sub == "日记":
            day = rest or now.strftime("%Y-%m-%d")
            row = self.engine.get_diary(day)
            if row:
                yield event.plain_result(f"🐋 {day} 的日记\n" + str(row["content"]))
            elif day == now.strftime("%Y-%m-%d"):
                yield event.plain_result("今天的日记还没写，晚上 23:50 之后会写。")
            else:
                yield event.plain_result("那天没有日记。")
        elif sub == "记忆":
            rows = self.engine.memory_top(8, now)
            if not rows:
                yield event.plain_result("还没有记忆条目。")
            else:
                lines = [f"{self.engine._age_text(r['created'], now)}：{str(r['text'])[:80]}" for r in rows]
                yield event.plain_result("🐋 最近记得\n" + "\n".join(lines))
        elif sub == "记":
            if not rest:
                yield event.plain_result("用法：/灵魂 记 <要记住的事>")
            else:
                self.engine.add_memory(rest, 7.0, now)
                yield event.plain_result("好，记下了。")
        elif sub in ("开启", "打开"):
            self.engine.set_enabled(True)
            yield event.plain_result("灵魂核心开着了。")
        elif sub in ("关闭", "关"):
            self.engine.set_enabled(False)
            yield event.plain_result("先安静一会儿。")
        elif sub in ("跳", "心跳一下"):
            line = self.engine.heartbeat(self.engine.presence_info(now), now)
            yield event.plain_result("🐋 " + line)
        elif sub in ("想", "自主", "决定"):
            decision = await self._ai_decide(now)
            action = str(decision.get("action") or "none")
            if action == "none" or not action:
                yield event.plain_result("她现在什么也不想做。")
                return
            text = self._clean(decision.get("content") or "", 200)
            if action == "message":
                out = text or await self._compose_proactive(now)
                if out and await self._send(out, user_requested=True):
                    self.engine.log_proactive(out, "forced ai decision", now)
                    yield event.plain_result(f"她自己想说：{out}")
                else:
                    yield event.plain_result("她张了张嘴，又没说出来。")
            elif action == "remember":
                memory = self._clean(decision.get("memory") or "", 300)
                if memory:
                    self.engine.add_memory(memory, 6)
                    yield event.plain_result(f"她记下了：{memory}")
                else:
                    yield event.plain_result("她想记点什么，但又没说清楚。")
            elif action in ("poke", "signature", "qzone", "like"):
                detail = await self._do_play_action(action, now, text)
                yield event.plain_result(detail or "她想去玩，但没玩成。")
            else:
                yield event.plain_result(json.dumps(decision, ensure_ascii=False))
        elif sub in ("胡闹", "闹"):
            text = await self._compose_proactive(now, playful=True)
            if text and await self._send(text, user_requested=True):
                self.engine.log_proactive(text, "forced playful", now)
                yield event.plain_result("（闹了一下）")
            else:
                yield event.plain_result("这会儿闹不出来，等下再试。")
        elif sub in ("玩", "玩耍"):
            detail = await self._do_play(now, forced=True)
            yield event.plain_result(detail or "先去旁边玩会儿。")
        elif sub == "注入":
            yield event.plain_result(self.engine.injection_block(now))
        else:
            yield event.plain_result(
                "用法：/灵魂 状态|心情|心跳|日记|记忆|记 <内容>|开启|关闭|胡闹|玩|注入"
            )
        self._ensure_heartbeat()
