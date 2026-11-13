"""BEAM-AI · 内核主循环（kernel.py）

一拍（tick）做完整条链：
    身体采样 → 内感受 → 双速情绪 → 动机 → 仲裁 → 安全 → 元认知(按需) → 留痕

设计约束：
- 内核可以**离线**跑（不依赖 AstrBot），也可以被宿主定时器驱动；
- 状态持久化到单个 JSON，进程重启后情绪不归零（"持续在线"的物理基础）；
- 每拍耗时 < 1ms 量级（纯算术），跑 5s 一拍毫无压力；
- 任何一层异常都不许把内核打死：逐层 try/except，降级为上一拍状态。
"""
from __future__ import annotations

import json
import math
import os
import time
from typing import Any, Dict, List, Optional

from . import (affect2, body as body_mod, bus, drive as drive_mod,
               express, gate as gate_mod, intero as intero_mod, meta as meta_mod,
               recovery, safety as safety_mod, schema, stance as stance_mod,
               thoughts as thoughts_mod, traits as traits_mod)

#: 默认 tick 间隔（秒）
DEFAULT_TICK_S = 5.0

#: 单拍最多消化的排队事件数（防止积压爆掉）
MAX_EVENTS_PER_TICK = 8

#: 轨迹保留条数
TRACE_MAX = 40

#: [iter35][A] 闭式补算：心跳中断后最多补 7 天（借鉴 fish_heart main.py:921-939 的 96 次封顶）
CATCHUP_MAX_S = 7 * 24 * 3600.0

#: [iter35][B] 同类事件退热：10 分钟窗口内，同类第 n+1 个正向事件强度 × 1/(1+n/12)
HEAT_WINDOW_S = 600.0
HEAT_DIVISOR = 12.0
HEAT_MAX_PER_KIND = 200  # 每个 kind 最多记 200 个时间戳（防内存无限涨）

#: [iter36] 行为层事件预设：真哄 / 假哄。
#: affect2.EVENT_PRESETS 是动力学红线（不改），所以在 feed() 里把这两个 kind 映射成
#: 等价 appraisal 塞进原通道：
#:   - comfort（真哄）：正效价 + 高可控/高应对 → 压力降、底色暖（由既有方程完成）；
#:   - fake_comfort（敷衍）：「行了别闹」式 → 负效价 + 低可控 → 压力小升、心情变凉。
COMFORT_APPRAISAL = {
    "comfort": {"relevance": 0.80, "goal_congruence": 0.60, "certainty": 0.70,
                "control": 0.65, "coping": 0.80, "agency": "other", "intensity": 0.70},
    "fake_comfort": {"relevance": 0.60, "goal_congruence": -0.35, "certainty": 0.55,
                     "control": 0.40, "coping": 0.35, "agency": "other", "intensity": 0.60},
}


class BeamsKernel:
    """BEAM-AI 内核：持有状态、推进时间、接收事件、输出 <mind>。"""

    def __init__(self, state_path: str, audit_path: Optional[str] = None,
                 config: Optional[Dict] = None, autosave: bool = True):
        self.state_path = state_path
        self.audit_path = audit_path or (os.path.splitext(state_path)[0] + "_audit.jsonl")
        self.memory_path = os.path.splitext(state_path)[0] + "_memory.jsonl"
        self.config = config if isinstance(config, dict) else {}
        self.autosave = bool(autosave)
        self.bus = bus.EventBus()
        self.tick_s = schema.clamp(self.config.get("tick_s"), 1.0, 300.0, DEFAULT_TICK_S)
        self.state: Dict = schema.default_state()
        self._events: List[Dict] = []
        self._last_body: Dict = {}
        # [iter27] 长期人格（天~周级漂移），独立文件持久化
        self.traits_path = os.path.splitext(state_path)[0] + "_traits.json"
        self.traits: Dict[str, float] = traits_mod.default()
        self._trait_signals: Dict[str, float] = {}
        self._traits_eval_round: int = 0
        self._traits_last: Dict = {}
        self._turns: int = 0  # 内核自己的对话轮计数（每来一条 chat 事件 +1）
        # [iter35][B] 同类事件退热时间戳（kind → [ts, ...]）。纯内存、不落盘：
        #   - 不写进 beams_state.json（不改状态契约、不升版本）；
        #   - 重启清零可接受：退热只是防"连聊刷分"，冷启动本来就是重新开始。
        self._heat: Dict[str, List[float]] = {}
        # [iter36] 行为层：念头池 + 四档 stance。
        #   - 两者都**不进 beams_state.json**（红线：不改 schema 契约、不升 STATE_VERSION）；
        #   - 念头池落独立文件（仿 traits：state_path 去掉扩展名 + _thoughts.json），是权威；
        #   - stance 是纯函数，每拍现算放内存；最近一次快照写进念头文件的 meta 供面板读。
        self.behavior_path = os.path.splitext(state_path)[0] + "_thoughts.json"
        self.thoughts: List[Dict] = []
        self._thoughts_meta: Dict = {}
        self.stance: Dict = {}
        self.comfort: Dict = {"streak": 0, "last_ts": 0.0}
        self._thoughts_dirty = False
        self._neg_ts: List[float] = []
        self.load()

    # ------------------------------------------------------------ 持久化

    def load(self) -> Dict:
        self.state = schema.load_state(self.state_path)
        # [iter27] 载入长期人格（独立文件里是权威；state 里的只是快照）
        try:
            self.traits = traits_mod.load(self.traits_path)
            meta = traits_mod.load_meta(self.traits_path)
            self._traits_eval_round = int(meta.get("eval_round") or 0)
            self._traits_last = meta.get("last") if isinstance(meta.get("last"), dict) else {}
            self._turns = max(int(meta.get("turns") or 0), self._traits_eval_round)
        except Exception:
            self.traits = traits_mod.default()
        # [iter36] 载入行为层：念头池独立文件是权威；stance 由 state 现算（不落 state 文件）
        try:
            self.thoughts, self._thoughts_meta = thoughts_mod.load(self.behavior_path)
            cf = self._thoughts_meta.get("comfort")
            cf = cf if isinstance(cf, dict) else {}
            self.comfort = {"streak": int(schema.clamp(cf.get("streak"), 0, 999, 0)),
                            "last_ts": max(0.0, schema.finite(cf.get("last_ts"), 0.0))}
            # 新建/无历史：把生成评估的锚点定在现在，避免"启动第一拍就长念头"
            if schema.finite(self._thoughts_meta.get("last_eval"), 0.0) <= 0.0:
                self._thoughts_meta["last_eval"] = schema.now_ts()
            self.stance = stance_mod.compute(self.state)
        except Exception:
            self.thoughts, self._thoughts_meta = [], {}
            self.comfort = {"streak": 0, "last_ts": 0.0}
            self.stance = {}
        return self.state

    def save(self) -> bool:
        ok = schema.save_state(self.state_path, self.state)
        # [iter36] 行为层独立文件只在有改动时落盘（5s 一拍，不能每拍写两个文件）
        if self._thoughts_dirty:
            self._save_behavior()
        return ok

    def _save_behavior(self) -> bool:
        """[iter36] 行为层旁路落盘：念头池 + 元信息 + 最近 stance 快照。

        刻意**不写** beams_state.json：那是 schema 契约（白名单 validate 会丢 meta 子键），
        念头/档位属于行为层，用独立文件才能既持久化又不升契约版本。
        """
        try:
            meta = dict(self._thoughts_meta or {})
            meta["comfort"] = dict(self.comfort or {})
            meta["stance"] = dict(self.stance or {})
            ok = thoughts_mod.save(self.behavior_path, self.thoughts, meta)
            if ok:
                self._thoughts_dirty = False
            return ok
        except Exception:
            return False

    def _note_comfort(self, kind: str, now: float) -> None:
        """[iter36] 真哄/假哄进度：comfort_streak = 连续真哄轮数；假哄清零。

        口径同 fish_heart：真哄要连续够轮数才算哄回来（第二批插件按档位消费），
        假哄（一边哄一边"行了别闹"）直接作废进度——但压力小升由既有 appraisal 完成，
        本函数只记数，不改任何动力学。
        """
        if kind == "comfort":
            cur = int(schema.clamp((self.comfort or {}).get("streak"), 0, 999, 0))
            self.comfort = {"streak": cur + 1, "last_ts": now}
        elif kind == "fake_comfort":
            self.comfort = {"streak": 0, "last_ts": now}
        else:
            return
        self._thoughts_dirty = True

    def _queue_thought_reward(self, payload: Dict) -> None:
        """[iter36] 念头喂饱奖励：只往既有事件队列里塞一条小事件。

        走的是 feed 之后 tick 里 affect2.appraisal_of 的原通道（既有慢通道），
        不复用 feed()：避免重复计 traits 轮数/同类退热/记忆流水，也避免"奖励事件再喂念头"递归。
        """
        preset = payload.get("reward") if isinstance(payload, dict) else None
        preset = preset if isinstance(preset, dict) else {}
        kind = schema.as_text(preset.get("kind"), 24, "")
        if kind not in affect2.EVENT_PRESETS:
            return
        ev = {
            "ts": schema.now_ts(),
            "kind": kind,
            "why": "",
            "goal_congruence": schema.clamp(preset.get("goal_congruence"), -1.0, 1.0, 0.0),
            "intensity": schema.clamp01(preset.get("intensity"), 0.3),
        }
        self._events.append(ev)
        if len(self._events) > 200:
            self._events = self._events[-200:]

    def _behavior_tick(self, st: Dict, now: float) -> None:
        """[iter36] 念头池一拍：自动满足 → 过期淡出 → 每 EVAL_EVERY_S 评估生成。"""
        # 1) 状态自己缓过来的（quiet / dwell）
        auto = thoughts_mod.auto_complete(self.thoughts, st, now)
        if auto:
            self._thoughts_dirty = True
            self._audit([{"ts": now, "kind": "thought_settle", "auto": True,
                          "thought": a.get("kind"), "mood": a.get("mood")} for a in auto])
        # 2) 过期：未满足的凉掉（只记审计，不改动力学），已满足的静默删掉
        kept, faded = thoughts_mod.expire(self.thoughts, now)
        self.thoughts = kept
        if faded:
            self._thoughts_dirty = True
            rows = []
            for item in faded:
                eff = thoughts_mod.release_effect(item)
                rows.append({"ts": now, "kind": "thought_fade",
                             "thought": eff.get("kind"), "text": eff.get("text")})
            self._audit(rows)
        # 3) 生成评估：默认每 10 分钟一次（5s 拍不能每拍掷骰），概率可配
        last_eval = schema.finite((self._thoughts_meta or {}).get("last_eval"), 0.0)
        if now - last_eval >= thoughts_mod.EVAL_EVERY_S:
            self._thoughts_meta["last_eval"] = now
            self._thoughts_dirty = True
            prob = schema.clamp(self.config.get("thought_spawn_prob"),
                                0.0, 1.0, thoughts_mod.SPAWN_PROB)
            roll = gate_mod._det_random(st, salt=7)
            born = thoughts_mod.spawn(st, self.thoughts, now, roll=roll, prob=prob)
            if born:
                self.thoughts.append(born)
                self._audit([{"ts": now, "kind": "thought_spawn",
                              "thought": born.get("kind"), "weight": born.get("weight")}])
        # 4) 有改动才落盘
        if self._thoughts_dirty and self.autosave:
            self._save_behavior()

    def _audit(self, items: List[Dict]) -> None:
        for item in items or []:
            if isinstance(item, dict):
                schema.append_jsonl(self.audit_path, item)

    # ------------------------------------------------------------ 对外接口（闭环）

    def memory_feedback(self, text: str, tags=None) -> Dict:
        """回复完成后回流记忆：她说了什么 + 当时的情绪状态。"""
        st = self.state
        item = {
            "ts": schema.now_ts(),
            "kind": "her_reply",
            "text": schema.as_text(text, 300, ""),
            "tags": [schema.as_text(t, 16, "") for t in (tags or [])][:6],
            "fast": {"v": round(schema.finite(st["fast"].get("valence"), 0.0), 3),
                     "a": round(schema.finite(st["fast"].get("arousal"), 0.0), 3),
                     "label": schema.as_text(st["fast"].get("label"), 16, "")},
            "slow": {"mood": round(schema.finite(st["slow"].get("mood_valence"), 0.0), 3),
                     "stress": round(schema.finite(st["slow"].get("stress"), 0.0), 3)},
            "winner": schema.as_text(st["arbitration"].get("winner"), 16, ""),
            "gate": bool(st["gate"].get("open")),
        }
        try:
            schema.append_jsonl(self.memory_path, item)
        except Exception:
            pass
        try:
            self.bus.publish("memory_feedback", item)
        except Exception:
            pass
        return item

    def notify_memory_change(self, kind: str, note: str = "") -> None:
        """记忆系统变动（清库/衰减/重建）通知内核：当作事件，避免"失忆逻辑断层"。"""
        why = "记忆%s%s" % (schema.as_text(kind, 20, ""),
                           ("：" + schema.as_text(note, 40, "")) if note else "")
        self.feed("memory_change", goal_congruence=-0.05, intensity=0.35, why=why)
        try:
            self.bus.publish("memory_change", {"kind": kind, "note": note, "ts": schema.now_ts()})
        except Exception:
            pass

    def recall(self, query: str = "", limit: int = 5) -> List[Dict]:
        """从记忆流水里检索（情绪强度 50% + 新近度 30% + 文本命中 20% 加权）。"""
        rows = []
        try:
            with open(self.memory_path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        continue
        except OSError:
            return []
        if not rows:
            return []
        now = schema.now_ts()
        q = str(query or "").strip()
        scored = []
        for r in rows[-500:]:
            fast = r.get("fast") if isinstance(r.get("fast"), dict) else {}
            v = schema.finite(fast.get("v"), 0.0)
            recency = 1.0 / (1.0 + max(0.0, now - schema.finite(r.get("ts"), now)) / 3600.0)
            text = schema.as_text(r.get("text"), 200, "")
            hit = 1.0 if (q and q[:6] in text) else 0.0
            score = 0.50 * abs(v) + 0.30 * recency + 0.20 * hit
            scored.append((score, r))
        scored.sort(key=lambda kv: -kv[0])
        return [r for _s, r in scored[:max(1, limit)]]

    def apply_memory_influence(self, query: str = "", limit: int = 3) -> Dict:
        """记忆 → 情绪反哺：把检索到的强情绪记忆转成 appraisal 事件。

        - 记忆里的效价决定 goal_congruence（正=暖，负=刺）；
        - 强度打折（0.35），不让旧事完全盖住当下。
        """
        items = self.recall(query=query, limit=limit)
        fed = 0
        for it in items:
            fast = it.get("fast") if isinstance(it.get("fast"), dict) else {}
            v = schema.finite(fast.get("v"), 0.0)
            if abs(v) < 0.15:
                continue
            self.feed("memory_recall",
                      goal_congruence=round(schema.clamp(v * 0.35, -0.5, 0.5), 3),
                      intensity=round(schema.clamp(0.25 + abs(v) * 0.4, 0.2, 0.7), 3),
                      why="想起：%s" % schema.as_text(it.get("text"), 30, ""))
            fed += 1
        out = {"recalled": len(items), "fed": fed}
        try:
            self.bus.publish("memory_influence", out)
        except Exception:
            pass
        return out

    def contract(self, capability: str) -> Dict:
        """契约/权限检查（供出口与插件使用）。"""
        return gate_mod.contract_check(self.state, capability)

    def failure(self, action: str, err: str = "", attempt: int = 1) -> Dict:
        """出口失败 → 回退决策（tts/send/feiyu/llm/asr）。"""
        decision = recovery.failure_retry(action, err, attempt)
        try:
            self.bus.publish("failure", {"action": action, "err": str(err)[:80],
                                         "attempt": attempt, "decision": decision})
        except Exception:
            pass
        return decision

    # ------------------------------------------------------------ 事件

    def feed(self, kind: str, **kwargs) -> None:
        """排队一个事件。kind 见 affect2.EVENT_PRESETS。

        带幂等：同一 (kind, why, ts秒) 5 秒内重复喂入只吃一次（防抖/去重）。
        """
        # [iter26] 隐私闸门：进内核的事件文本先脱敏（密码/密钥/手机号等不进流水）
        why = schema.scrub_text(kwargs.pop("why", ""), 120)
        now = schema.now_ts()
        _kind = schema.as_text(kind, 24, "")
        # [iter36] 真哄/假哄：映射成等价 appraisal 走原通道（affect2 的预设表不动）
        if _kind in COMFORT_APPRAISAL:
            if not isinstance(kwargs.get("appraisal"), dict):
                kwargs["appraisal"] = dict(COMFORT_APPRAISAL[_kind])
            self._note_comfort(_kind, now)
        ev = {"ts": now, "kind": _kind, "why": why}
        for k, v in kwargs.items():
            if isinstance(v, (int, float, str, bool)):
                ev[k] = v
        if isinstance(kwargs.get("appraisal"), dict):
            ev["appraisal"] = kwargs["appraisal"]
        # 指纹 = kind + why + 秒级时间桶：5 秒内同内容视为重复
        fingerprint = "%s|%s|%d" % (ev["kind"], why[:60], int(now // 5))
        seen = self.state.get("seen") if isinstance(self.state.get("seen"), list) else []
        if fingerprint in seen:
            return
        seen = (seen + [fingerprint])[-200:]
        self.state["seen"] = seen
        # [iter27] 长期人格信号：把聊天/说法事件读成词表信号（安全/凭据话题会让整轮跳过）
        try:
            if ev.get("kind") in ("chat", "speech"):
                traits_mod.accumulate(self._trait_signals, why)
                if ev.get("kind") == "chat":
                    self._turns += 1
        except Exception:
            pass
        # [iter35][B] 同类事件退热：10 分钟窗口内同一 kind 的第 n+1 个正向事件，
        #   强度 × 1/(1+n/12)（n = 窗口内已发生次数，先取 n 再追加当前这条）。
        #   只压正向（goal_congruence > 0）；负向（gc <= 0）完全不乘 —— 难过/被冷落
        #   不该因为"说了很多遍"而被稀释。没给 intensity 的事件只记时间戳、不臆造强度。
        try:
            gc_in = ev.get("goal_congruence")
            if isinstance(gc_in, (int, float)) and float(gc_in) > 0.0:
                hist = self._heat.setdefault(ev["kind"], [])
                cut = now - HEAT_WINDOW_S
                hist[:] = [t for t in hist if t >= cut]
                n_heat = len(hist)
                factor = 1.0 / (1.0 + n_heat / HEAT_DIVISOR)
                if isinstance(ev.get("intensity"), (int, float)):
                    ev["intensity"] = round(schema.clamp01(
                        schema.finite(ev.get("intensity"), 0.5) * factor, 0.0), 4)
                hist.append(now)
                if len(hist) > HEAT_MAX_PER_KIND:
                    del hist[:len(hist) - HEAT_MAX_PER_KIND]
        except Exception:
            pass
        self._events.append(ev)
        if len(self._events) > 200:
            self._events = self._events[-200:]
        # [iter36] 念头喂饱：一条用户侧事件最多喂饱一条念头（奖励走既有慢通道）
        try:
            if ev.get("kind") in thoughts_mod.USER_FEED_KINDS:
                payload = thoughts_mod.settle(self.thoughts, ev.get("kind"), why, now)
                if payload:
                    self._thoughts_dirty = True
                    self._queue_thought_reward(payload)
                    self._audit([{"ts": now, "kind": "thought_settle",
                                  "thought": payload.get("kind"),
                                  "mood": payload.get("mood", ""),
                                  "soft": bool(payload.get("soft"))}])
        except Exception:
            pass
        # 事件落库（结构化记忆流水）
        try:
            schema.append_jsonl(self.memory_path, {
                "ts": now, "kind": ev["kind"], "why": why,
                "gc": ev.get("goal_congruence"), "intensity": ev.get("intensity"),
            })
        except Exception:
            pass

    def pending(self) -> int:
        return len(self._events)

    # ------------------------------------------------------------ 一拍

    def tick(self, body_ctx: Optional[Dict] = None, ctx: Optional[Dict] = None,
             round_count: int = 0, feedback: Optional[Dict] = None,
             dt_s: Optional[float] = None, now: Optional[float] = None) -> Dict:
        """推进一拍，返回观测字典（含 arbitration / safety / 表达摘要）。

        now 可传虚拟时间（回放用）：idle/时间窗口/衰减都跟着它走，与真实时钟解耦。
        """
        st = schema.validate(self.state)
        now = schema.finite(now, 0.0) or schema.now_ts()
        # [iter35][A] 补算锚点：用推进前的旧 ts（st["ts"] 在拍尾才写成 now）
        old_ts = schema.finite(st.get("ts"), now)
        if dt_s is None:
            dt_s = schema.clamp(now - old_ts, 0.0, 3600.0, 0.0)
        dt_s = schema.clamp(dt_s, 0.0, 3600.0, 0.0)
        if dt_s <= 0.0:
            dt_s = self.tick_s
        dt_h = dt_s / 3600.0
        ctx = ctx if isinstance(ctx, dict) else {}

        # 1) 身体
        try:
            body = body_mod.sample(body_ctx)
            self._last_body = body
            st["body"] = body
        except Exception:
            body = self._last_body or body_mod.sample({})
            st["body"] = body

        # 2) 内感受
        pe_in = ctx.get("prediction_error")
        try:
            st["intero"] = intero_mod.update(st["intero"], body, dt_s,
                                             {"prediction_error": pe_in})
        except Exception:
            pass

        # 3) 消化事件（最多 8 个）
        events = self._events[:MAX_EVENTS_PER_TICK]
        consumed = len(events)
        appr_last = None
        for ev in events:
            try:
                appr = affect2.appraisal_of(ev)
            except Exception:
                appr = None
            if appr is not None:
                appr_last = appr
                # [iter36] 行为层：近 30 分钟的负性事件计数（内存轻量计数，不落盘）
                if schema.num(appr, "goal_congruence", 0.0) < -0.15:
                    self._neg_ts.append(now)
                try:
                    st["fast"] = affect2.step_fast(st["fast"], appr, st["slow"], st["intero"],
                                                   st["params"])
                    st["fast"]["last_event"] = {"ts": ev.get("ts", now),
                                                "kind": ev.get("kind", ""),
                                                "why": ev.get("why", "")}
                except Exception:
                    pass
            try:
                st["slow"] = affect2.step_slow(st["slow"], st["fast"], appr, dt_h, st["params"])
            except Exception:
                pass
        self._events = self._events[consumed:]

        # 无事件的一拍：快通道自然衰减
        if not events:
            try:
                st["fast"] = affect2.step_fast(st["fast"], None, st["slow"], st["intero"], st["params"])
            except Exception:
                pass
            try:
                st["slow"] = affect2.step_slow(st["slow"], st["fast"], None, dt_h, st["params"])
            except Exception:
                pass

        # [iter35][A] 闭式补算：心跳可能因宿主停机/卡死中断很久（dt_s 仍按老规矩钳 1h）。
        #   这里只补"超出 1h 的差额"：本拍内部已按名义 dt_s 推进过一次慢通道，
        #   再把 (real_gap - dt_s) 交给同一个半衰期闭式（appr=None → 只衰减、不回填事件）。
        #   两次衰减因子相乘 = 0.5^(real_gap/hl)，等价于全程推进；事件循环里
        #   "每个事件各推进一次 dt" 是既有行为，不在本次修正范围（故不重复算）。
        #   补算封顶 7 天：更久远的停机没有信息量，按封顶衰减到底即可。
        #   step_slow 里疲劳除了半衰期还有"每拍负载漂移"（与 dt 无关的常数项）；
        #   补算只想过时间，故把 fast.arousal 固定 0.8（load=0.4，
        #   0.045*0.4 - 0.030*0.6 = 0 净漂移为零），只保留纯衰减。
        try:
            real_gap = schema.clamp(now - old_ts, 0.0, CATCHUP_MAX_S, 0.0)
            extra_s = real_gap - dt_s
            if extra_s > 1.0:
                catch_fast = dict(st["fast"])
                catch_fast["arousal"] = 0.8
                st["slow"] = affect2.step_slow(st["slow"], catch_fast, None,
                                               extra_s / 3600.0, st["params"])
        except Exception:
            pass

        # 4) 动机输入
        last_pos = schema.finite(st["slow"].get("last_positive_ts"), 0.0)
        idle = drive_mod._idle_factor(last_pos, now, st["slow"])
        pending_tasks = schema.clamp(ctx.get("pending_tasks"), 0.0, 20.0, 0.0)
        inputs = {
            "valence": schema.finite(st["fast"].get("valence"), 0.0),
            "arousal": schema.finite(st["fast"].get("arousal"), 0.0),
            "dominance": schema.finite(st["fast"].get("dominance"), 0.0),
            "resource": schema.finite(st["intero"].get("resource"), 0.7),
            "prediction_error": schema.finite(st["intero"].get("prediction_error"), 0.1),
            "load": schema.finite(st["intero"].get("load"), 0.25),
            "stress": schema.finite(st["slow"].get("stress"), 0.15),
            "fatigue": schema.finite(st["slow"].get("fatigue"), 0.2),
            "bonding": schema.finite(st["slow"].get("bonding"), 0.5),
            "idle": idle,
            "pending_tasks": pending_tasks,
            "error_rate": schema.num(st["body"], "errors_1h", 0) / 20.0,
        }
        try:
            st["drives"] = drive_mod.compute(inputs, st["params"], ctx)
            st["arbitration"] = drive_mod.arbitrate(st["drives"], st["params"],
                                                    st["arbitration"], st["slow"])
        except Exception:
            pass

        # 5) 安全层
        try:
            st["safety"] = safety_mod.evaluate(st["safety"], st["fast"], st["slow"],
                                               st["arbitration"])
        except Exception:
            pass

        # 5.5) 开口闸门（L3.5）：动机说不等于现在说
        try:
            trigger = "direct" if events else "ambient"
            st["gate"] = gate_mod.evaluate(st, trigger=trigger, ctx=ctx,
                                           ev=(events[-1] if events else None),
                                           stance=self.stance, thoughts=self.thoughts)
        except Exception:
            pass

        # 5.6) [iter36] 四档行为状态：纯函数现算（不写 state；上一拍档位用于滞回）
        try:
            cut = now - stance_mod.NEG_WINDOW_S
            self._neg_ts = [t for t in self._neg_ts if t >= cut][-12:]
            _prev_lvl = ""
            if isinstance(self.stance, dict):
                _prev_lvl = schema.as_text(self.stance.get("level"), 8, "")
            self.stance = stance_mod.compute(st, now=now, prev=_prev_lvl,
                                             neg_recent=len(self._neg_ts))
        except Exception:
            pass

        # 5.7) [iter36] 念头池生命周期（独立文件持久化；不改 state 契约）
        try:
            self._behavior_tick(st, now)
        except Exception:
            pass

        # 6) 元认知：有反馈/到轮数才审核
        if isinstance(feedback, dict) or meta_mod.should_audit(st, round_count):
            try:
                result = meta_mod.audit(st, feedback or {}, round_count)
                if result.get("verdict") == "none" and not feedback:
                    # 例行检查（没有真实反馈）：只推进时间/轮数，不覆盖上次结论、不写叙事
                    prev = st["meta"]["audit"] if isinstance(st["meta"].get("audit"), dict) else {}
                    prev["last_ts"] = result.get("ts", now)
                    prev["last_round"] = round_count
                    st["meta"]["audit"] = prev
                    result = {"verdict": "none", "adjustments": {}, "note": ""}
                else:
                    # 字段名对齐 schema 契约（audit 里的 verdict → last_verdict）
                    st["meta"]["audit"] = {
                        "last_ts": result.get("ts", now),
                        "last_verdict": result.get("verdict", "none"),
                        "pending_review": False,
                        "last_round": result.get("last_round", round_count),
                        "history": result.get("history", []),
                    }
                if result.get("adjustments"):
                    st, audits = bus.apply_patch(st, result["adjustments"],
                                                 source="meta_audit",
                                                 why=schema.as_text(result.get("note"), 60, ""))
                    self._audit(audits)
                if result.get("note"):
                    st["meta"]["narrative"] = meta_mod.add_narrative(
                        st["meta"].get("narrative"), result["note"],
                        ts=now, tags=[result.get("verdict", "")])
                # 第 4 层闭环：审核结论回流自我模型（成功涨置信，失败记局限）
                try:
                    verdict = schema.as_text(result.get("verdict"), 16, "none")
                    st["meta"]["self_model"] = meta_mod.update_self_model(
                        st["meta"].get("self_model"),
                        {"kind": "chat", "success": verdict == "ok",
                         "note": "" if verdict == "ok" else schema.as_text(result.get("note"), 40, "")})
                except Exception:
                    pass
            except Exception:
                pass

        # 7) 留痕
        trace = st["trace"].get("events") if isinstance(st["trace"].get("events"), list) else []
        trace = (trace + [{
            "ts": now,
            "fast": round(schema.finite(st["fast"].get("valence"), 0.0), 3),
            "slow": round(schema.finite(st["slow"].get("mood_valence"), 0.0), 3),
            "stress": round(schema.finite(st["slow"].get("stress"), 0.0), 3),
            "winner": schema.as_text(st["arbitration"].get("winner"), 16, ""),
            "urgency": round(schema.finite(st["arbitration"].get("urgency"), 0.0), 3),
            "neg_load": round(schema.finite(st["safety"].get("neg_load"), 0.0), 3),
            "isolation": bool(st["safety"].get("isolation")),
        }])[-TRACE_MAX:]
        st["trace"]["events"] = trace
        st["trace"]["last_tick_ts"] = now
        st["ts"] = now
        st["tick_count"] = int(schema.num(st, "tick_count", 0)) + 1
        try:
            self.bus.publish("tick", {"ts": now, "tick": st["tick_count"],
                                      "mood": st["slow"].get("mood_valence"),
                                      "winner": st["arbitration"].get("winner")})
            if st["gate"].get("open"):
                self.bus.publish("gate_open", {"ts": now, "route": st["gate"].get("route"),
                                               "reason": st["gate"].get("reason")})
        except Exception:
            pass
        # 健康：成功一拍就清零失败计数、退出安全模式
        health = st.get("health") if isinstance(st.get("health"), dict) else {}
        health["ts"] = now
        health["consecutive_failures"] = 0
        health["safe_mode"] = False
        health["last_ok_ts"] = now
        health["total_ticks"] = int(schema.clamp(health.get("total_ticks"), 0, 10 ** 9, 0)) + 1
        st["health"] = health
        # [iter27] 长期人格漂移：每 EVAL_EVERY 轮评估一次（进度存 traits.json，state.meta 会被重建丢掉）
        try:
            cur_round = int(self._turns or 0)  # 用内核自己的 chat 计数（与插件轮数解耦）
            base_round = int(self._traits_eval_round or 0)
            if cur_round > base_round and (cur_round - base_round) >= traits_mod.EVAL_EVERY:
                res = traits_mod.evaluate(self.traits, self._trait_signals,
                                          turns=max(1, cur_round - base_round))
                self.traits = res["next"]
                self._traits_eval_round = cur_round
                if res.get("changes"):
                    self._traits_last = {"ts": now, "round": cur_round,
                                         "changes": res["changes"][:6]}
                self._trait_signals = {}
                traits_mod.save(self.traits_path, self.traits,
                                extra={"eval_round": cur_round, "turns": cur_round,
                                       "last": self._traits_last})
                if res.get("changes"):
                    self._audit([{"ts": now, "kind": "traits_eval",
                                  "round": cur_round, "changes": res["changes"]}])
            st["traits"] = traits_mod.view(self.traits)
        except Exception:
            pass
        # [iter32] 情绪记忆：强情绪时刻写盘（记忆闭环：下次对话注入 → 她会"记得"那几刻）
        try:
            _f = st.get("fast") if isinstance(st.get("fast"), dict) else {}
            _v = schema.finite(_f.get("valence"), 0.0)
            _a = schema.finite(_f.get("arousal"), 0.0)
            if abs(_v) >= 0.55 or _a >= 0.80:
                _peak_path = os.path.splitext(self.state_path)[0] + "_emotional_peaks.jsonl"
                _last_ts = 0.0
                try:
                    if os.path.isfile(_peak_path):
                        with open(_peak_path, "r", encoding="utf-8") as _fh:
                            for _ln in _fh.readlines()[-3:]:
                                try:
                                    _last_ts = max(_last_ts, float(json.loads(_ln).get("ts") or 0))
                                except Exception:
                                    pass
                except Exception:
                    pass
                if now - _last_ts >= 1800:  # 30 分钟冷却，避免刷屏
                    _ev = _f.get("last_event") if isinstance(_f.get("last_event"), dict) else {}
                    schema.append_jsonl(_peak_path, {
                        "ts": now,
                        "label": schema.as_text(_f.get("label"), 16, ""),
                        "valence": round(_v, 3), "arousal": round(_a, 3),
                        "why": schema.scrub_text(_ev.get("why") or "", 60),
                    })
        except Exception:
            pass
        self.state = schema.validate(st)
        if self.autosave:
            self.save()
        return self.observation()

    # ------------------------------------------------------------ 输出

    def observation(self) -> Dict:
        st = self.state
        exp = express.compute(st, stance=self.stance)
        return {
            "ts": schema.finite(st.get("ts"), 0.0),
            "tick": int(schema.num(st, "tick_count", 0)),
            "label": exp["label"],
            "drive": exp["drive"],
            "urgency": exp["urgency"],
            "talkative": exp["talkative"],
            "warmth": exp["warmth"],
            "stress": round(schema.finite(st["slow"].get("stress"), 0.0), 3),
            "mood": round(schema.finite(st["slow"].get("mood_valence"), 0.0), 3),
            "bonding": round(schema.finite(st["slow"].get("bonding"), 0.0), 3),
            "neg_load": round(schema.finite(st["safety"].get("neg_load"), 0.0), 3),
            "isolation": bool(st["safety"].get("isolation")),
            "inhibit": round(schema.finite(st["arbitration"].get("inhibit"), 0.0), 3),
            "open": bool(st["gate"].get("open")),
            "gate_reason": schema.as_text(st["gate"].get("reason"), 60, ""),
            "route": schema.as_text(st["gate"].get("route"), 12, "normal"),
            "pending": len(st["gate"].get("pending") or []),
            "safe_mode": bool(st["health"].get("safe_mode")),
            # [iter27] 长期人格（面板/日志用）
            "traits": traits_mod.view(self.traits),
            # [iter36] 行为层（面板/日志/第二批插件用；不进 state 契约）
            "stance": dict(self.stance or {}),
            "thoughts": thoughts_mod.summary(self.thoughts),
            "comfort_streak": int(schema.clamp((self.comfort or {}).get("streak"), 0, 999, 0)),
            "mind": express.render_mind(st, thoughts=self.thoughts, stance=self.stance),
        }

    def render(self) -> str:
        return express.render_mind(self.state, thoughts=self.thoughts, stance=self.stance)

    def render_lite(self) -> str:
        return express.render_lite(self.state, thoughts=self.thoughts, stance=self.stance)

    # ------------------------------------------------------------ [iter36] 行为层接口（第二批插件用）

    @property
    def comfort_streak(self) -> int:
        """连续真哄轮数（fake_comfort 清零）。第二批插件按档位消费，本轮不接插件。"""
        return int(schema.clamp((self.comfort or {}).get("streak"), 0, 999, 0))

    def thoughts_summary(self) -> Dict:
        """念头池摘要（面板/日志用；念头不进 state 契约）。"""
        return thoughts_mod.summary(self.thoughts)

    def mark_ignored(self, why: str = "") -> Dict:
        """宿主调用：上一次主动开口没被接住。

        - gate.ignore_streak +1（gate.mark_ignored 之前没有任何调用方，这里接上）；
        - 被忽略的主动消息原文收进 gate.pending（C6"补一句"用；why 由宿主传）；
        - 念头尝试 +1，连续 3 次没人应 → 放弃该念头（"算了，先不想了"）。
        不改表达、不直接喂负面情绪（那是宿主 feed 的事，避免双计）。
        """
        try:
            self.state = gate_mod.mark_ignored(self.state)
            st = self.state
            g = dict(st.get("gate") or {})
            pending = [x for x in (g.get("pending") or []) if isinstance(x, dict)]
            pending.append({"ts": schema.now_ts(), "trigger": "ignored",
                            "text": schema.scrub_text(why, 40)})
            g["pending"] = pending[-gate_mod.PENDING_MAX:]
            st["gate"] = g
            self.state = schema.validate(st)
        except Exception:
            pass
        try:
            giveup = thoughts_mod.note_attempt(self.thoughts)
            if giveup:
                self._thoughts_dirty = True
                self._audit([{"ts": schema.now_ts(), "kind": "thought_giveup",
                              "thought": giveup}])
        except Exception:
            pass
        if self.autosave:
            self.save()
        return {
            "ignore_streak": int(schema.num(self.state.get("gate"), "ignore_streak", 0)),
            "pending": len(self.state["gate"].get("pending") or []),
        }

    def mark_replied(self) -> Dict:
        """宿主调用：主动消息被接住 → 忽略计数清零（念头尝试次数不动，未满足的继续等）。"""
        try:
            self.state = gate_mod.mark_replied(self.state)
        except Exception:
            pass
        if self.autosave:
            self.save()
        return {"ignore_streak": 0,
                "open": bool(self.state["gate"].get("open"))}

    def up_packet(self) -> Dict:
        return bus.up_packet(self.state)

    # ------------------------------------------------------------ 旧内核热启动 / 对外契约

    def bootstrap_from_legacy(self, legacy: Dict) -> bool:
        """把旧 whale_affect 的状态映射进新内核（让新内核一开始就"认识"这段关系）。

        只映射慢变量（关系/疲劳/压力/底色），不臆造情绪事件；
        旧内核偏乐观的效价按 0.6 折进来，避免新内核一上来就过嗨。
        """
        lg = legacy if isinstance(legacy, dict) else {}
        needs = lg.get("needs") if isinstance(lg.get("needs"), dict) else {}
        core = lg.get("core") if isinstance(lg.get("core"), dict) else {}
        if not needs and not core:
            return False

        def nv(key: str, default: float = 50.0) -> float:
            return schema.clamp(schema.num(needs, key, default), 0.0, 100.0)

        bond, energy = nv("bond"), nv("energy")
        matter, exist = nv("mattering"), nv("existence")
        st = schema.validate(self.state)
        # [iter28] 渐进饱和：bond 100 映射到 ~0.88（旧映射直接 1.0 撞顶，永远没有成长空间）
        _b = schema.clamp(schema.finite(bond, 0.0) / 100.0, 0.0, 1.0, 0.0)
        st["slow"]["bonding"] = schema.clamp01(0.25 + 0.68 * (1.0 - math.exp(-2.6 * _b)), 0.50)
        st["slow"]["fatigue"] = schema.clamp01(1.0 - energy / 100.0, 0.20)
        st["slow"]["stress"] = schema.clamp01(0.55 - matter / 200.0, 0.15)
        st["slow"]["mood_valence"] = schema.clamp(
            schema.num(core, "valence", 0.10) * 0.60, -1.0, 1.0, 0.10)
        st["intero"]["resource"] = schema.clamp01(exist / 100.0, 0.72)
        st["intero"]["arousal"] = schema.clamp01(schema.num(core, "arousal", 0.30) * 0.45, 0.30)
        st["intero"]["dominance"] = schema.clamp01(schema.num(core, "dominance", 0.55), 0.55)
        st["fast"]["valence"] = schema.clamp_valence(st["slow"]["mood_valence"])
        st["fast"]["arousal"] = st["intero"]["arousal"]
        st["fast"]["dominance"] = st["intero"]["dominance"]
        st["fast"]["label"] = affect2.label_of(st["fast"], st["slow"])
        self.state = st
        self.save()
        return True

    def snapshot(self) -> Dict:
        """对外契约快照（写 beams_snapshot.json）。

        字段刻意兼容旧 outlet.json 的常用键（outlet/urgency），
        让旧插件将来能"一行改动"读新内核，而不是重写。
        """
        st = schema.validate(self.state)
        exp = express.compute(st)
        arb = st["arbitration"]
        return {
            "ts": schema.finite(st.get("ts"), 0.0),
            "tick": int(schema.num(st, "tick_count", 0)),
            "outlet": schema.as_text(arb.get("winner"), 16, "connect"),
            "urgency": round(schema.finite(arb.get("urgency"), 0.0), 3),
            "inhibit": round(schema.finite(arb.get("inhibit"), 0.0), 3),
            "label": exp["label"],
            "valence": round(schema.finite(st["fast"].get("valence"), 0.0), 3),
            "arousal": round(schema.finite(st["fast"].get("arousal"), 0.0), 3),
            "dominance": round(schema.finite(st["fast"].get("dominance"), 0.0), 3),
            "mood": round(schema.finite(st["slow"].get("mood_valence"), 0.0), 3),
            "stress": round(schema.finite(st["slow"].get("stress"), 0.0), 3),
            "bonding": round(schema.finite(st["slow"].get("bonding"), 0.0), 3),
            "fatigue": round(schema.finite(st["slow"].get("fatigue"), 0.0), 3),
            "talkative": exp["talkative"],
            "warmth": exp["warmth"],
            "brevity": exp["brevity"],
            "playfulness": exp["playfulness"],
            "emoji_rate": exp["emoji_rate"],
            "isolation": bool(st["safety"].get("isolation")),
            "neg_load": round(schema.finite(st["safety"].get("neg_load"), 0.0), 3),
            "gate_open": bool(st["gate"].get("open")),
            "gate_probability": round(schema.finite(st["gate"].get("probability"), 0.0), 3),
            "gate_reason": schema.as_text(st["gate"].get("reason"), 60, ""),
            "route": schema.as_text(st["gate"].get("route"), 12, "normal"),
            "pending_mentions": len(st["gate"].get("pending") or []),
            "hard_floor": dict(safety_mod.HARD_FLOOR),
            # [iter27] 长期人格快照（供面板/其他插件读）
            "traits": traits_mod.view(self.traits),
            "traits_summary": traits_mod.summary(self.traits),
            "traits_last": self._traits_last or {},
            # [iter36] 行为层快照（面板/第二批插件用；state 契约不变）
            "stance": stance_mod.summary(self.stance),
            "thoughts": thoughts_mod.summary(self.thoughts),
            "comfort_streak": int(schema.clamp((self.comfort or {}).get("streak"), 0, 999, 0)),
        }

    def apply_patch(self, patch: Dict, source: str = "host", why: str = "") -> List[Dict]:
        self.state, audits = bus.apply_patch(self.state, patch, source=source, why=why)
        self._audit(audits)
        self.save()
        return audits