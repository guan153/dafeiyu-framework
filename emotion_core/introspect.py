# -*- coding: utf-8 -*-
"""自省引擎：巡检 → 归因 → 记账 → 评分 → 提案。

它回答四个问题，且每个答案都有证据：
1. 我现在哪里不行？（弱点账本 top N，按分数）
2. 有多不行？（五个维度评分，扣分项可追溯）
3. 先修哪个？（分数排序 + 规则排序）
4. 怎么修？（提案模板：计划 / 风险 / 回滚）

关键取舍：
- 评分只做「解释得清」的线性扣分，不做玄学加权；
- 自动提案保守（只写提案，不改代码），高风险动作必须人工批准；
- 每次巡检写快照，历史可回看——她可以说出「我上周这个毛病改好了」。
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from . import config, health, ledger as ledger_mod, logscan, paths
from .bus import get_bus
from .capabilities import CapabilityRegistry, TARGET_CAPABILITIES, get_registry
from .ledger import Ledger, STATUS_LABEL, CATEGORY_LABEL

log = logging.getLogger("whale_core.introspect")

OPEN_STATUSES = tuple(s for s in ledger_mod.STATUSES if s not in ledger_mod.CLOSED_STATUSES)

# 已知的架构级不足：这些不是「偶发 bug」，是设计债。
# 如实记在账上，她才有资格说自己知道哪里不行。
KNOWN_ARCH_DEBTS: list[dict[str, str]] = [
    {
        "title": "情绪有四份来源，还没收敛成一份",
        "category": "conflict", "severity": "P1", "plugin": "astrbot_plugin_whale_affect",
        "root_cause": "affect/beams/state/网关各持有心情快照，无单一写者",
        "symptom": "模型一轮可能收到 4 个矛盾心情，表现漂移",
    },
    {
        "title": "记忆有五套实现，写入方还没唯一",
        "category": "conflict", "severity": "P1", "plugin": "astrbot_plugin_iris_chat_memory",
        "root_cause": "iris / whale_memory / memoryflow / brain / soul 各写各的，召回入口也不统一",
        "symptom": "她说「不记得」或重复记同一件事",
    },
    {
        "title": "主动消息：第三方模块仍可绕过统一队列直发",
        "category": "conflict", "severity": "P2", "plugin": "astrbot_plugin_proactive_chat",
        "root_cause": "v3 内核已有申请-仲裁-发放队列，但社区模块不经过它",
        "symptom": "深夜打扰、同一时段多条、发失败没人知道",
    },
    {
        "title": "回复后处理多层改写与切条，最终内容不可预测",
        "category": "conflict", "severity": "P2", "plugin": "",
        "root_cause": "chunker/speech_style/brain/fishtalk/fsm 依次改写",
        "symptom": "同样输入不同形状，偶尔吞句",
    },
    {
        "title": "唱歌还不稳，只有短片段 RVC 混音",
        "category": "gap", "severity": "P2", "plugin": "", "key": "gap:sing",
        "root_cause": "缺少真人歌声训练数据，RVC 目标音色未定稿",
        "symptom": "只能唱副歌片段，长句会飘",
    },
    {
        "title": "群聊默认关闭，尚未验证多人场景",
        "category": "gap", "severity": "P3", "plugin": "", "key": "gap:group",
        "root_cause": "私聊陪伴优先，群聊易刷屏",
        "symptom": "群里被 @ 时可能不回应",
    },
    {
        "title": "截图能力三套实现（bili/pc_explore/computer_use），有竞态风险",
        "category": "conflict", "severity": "P3", "plugin": "astrbot_plugin_computer_use",
        "root_cause": "各自调用 8765，kill 逻辑未收敛",
        "symptom": "偶发截不到或串图",
    },
    {
        "title": "技术关键词门卫代码复制了 4 份",
        "category": "doc", "severity": "P3", "plugin": "",
        "root_cause": "缺少共享库，改动需要同步多处",
        "symptom": "改一处漏三处",
    },
]


@dataclass
class Dimension:
    name: str
    label: str
    value: float
    weight: float
    deductions: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "value": round(self.value, 2),
            "weight": self.weight,
            "deductions": self.deductions,
        }


class SelfModel:
    def __init__(
        self,
        ledger: Ledger | None = None,
        registry: CapabilityRegistry | None = None,
    ) -> None:
        self.ledger = ledger or ledger_mod.get_ledger()
        self.registry = registry or get_registry()
        self._last_report: dict[str, Any] | None = None

    # ── 记录来源 ────────────────────────────────────────────
    def seed_known(self) -> int:
        """把架构级不足写进账本（幂等：已存在就完全不动，连次数都不加）。

        注意：不能用 record() 直接写——它遇到已存在会 +1 次，
        而两个模块启动时都会调用 seed，会凭空把架构债次数刷成 2。
        """
        added = 0
        for item in KNOWN_ARCH_DEBTS:
            category = item.get("category", "bug")
            key = item.get("key") or ledger_mod.make_key(item.get("plugin", ""), category, item["title"])
            if self.ledger.get(key) is not None:
                continue
            self.ledger.record(
                item["title"],
                plugin=item.get("plugin", ""),
                category=category,
                severity=item.get("severity", "P2"),
                symptom=item.get("symptom", ""),
                root_cause=item.get("root_cause", ""),
                source="architecture",
                key=key,
            )
            self.ledger.set_status(key, "triage", note="架构级设计债，已知未解")
            added += 1
        return added

    def ingest_log(self, findings: Iterable[logscan.Finding]) -> list[str]:
        """日志命中 → 账本。同 key 反复命中只累加，不新增条目。"""
        touched: list[str] = []
        for f in findings:
            key, is_new = self.ledger.record(
                f.title,
                plugin=f.plugin,
                category=f.category,
                severity=f.severity,
                symptom=f.samples[0] if f.samples else "",
                evidence={"count": f.count, "samples": f.samples, "last_ts": f.last_ts},
                source="logscan",
                occurrence_detail=f"窗口内 {f.count} 次",
            )
            touched.append(key)
            if is_new:
                log.info("新弱点：%s（%s）", f.title, f.severity)
        return touched

    def ingest_probes(self, results: list[health.ProbeResult], specs: list[health.ProbeSpec] | None = None) -> list[str]:
        """探针失败 → 账本；恢复了的自动标 verified（她真的能说出「修好了」）。"""
        critical = {s.name: s.critical for s in (specs or health.DEFAULT_SPECS)}
        touched: list[str] = []
        for r in results:
            key = f"probe:{r.name}"
            if not r.ok:
                sev = "P1" if critical.get(r.name) else "P3"
                self.ledger.record(
                    f"服务探针失败：{r.name}",
                    plugin="system",
                    category="bug",
                    severity=sev,
                    symptom=r.detail,
                    evidence={"detail": r.detail, "latency_ms": r.latency_ms},
                    source="probe",
                    key=key,
                )
                touched.append(key)
            else:
                existing = self.ledger.get(key)
                if existing and existing.status not in ledger_mod.CLOSED_STATUSES:
                    self.ledger.set_status(key, "verified", note=f"探针恢复：{r.detail}")
                    touched.append(key)
        return touched

    def sync_gaps(self) -> list[str]:
        """能力缺口 ↔ 账本双向同步：缺了记上，补了标好。"""
        touched: list[str] = []
        gaps = {g["capability"]: g["label"] for g in self.registry.gaps()}
        for name, label in gaps.items():
            key = f"gap:{name}"
            if self.ledger.get(key) is None:
                self.ledger.record(
                    f"能力缺口：{label}",
                    category="gap",
                    severity="P2",
                    symptom=f"目标能力 {name} 没有可用提供者",
                    source="capability",
                    key=key,
                )
                touched.append(key)
        for cap_name, cap in self.registry.report()["capabilities"].items():
            key = f"gap:{cap_name}"
            existing = self.ledger.get(key)
            if existing and existing.status not in ledger_mod.CLOSED_STATUSES and cap.get("providers"):
                self.ledger.set_status(key, "verified", note=f"{cap.get('preferred')} 提供")
                touched.append(key)
        return touched

    # ── 评分 ────────────────────────────────────────────────
    def score(
        self,
        *,
        probe_summary: dict[str, Any] | None = None,
        outbound_stats: dict[str, Any] | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        now = now or time.time()
        items = self.ledger.list(status=list(OPEN_STATUSES), limit=1000)
        for item in items:
            item.score = ledger_mod.compute_score(item.severity, item.occurrences, item.last_seen, item.status)

        # 1) 可靠性：按未解决缺陷的严重度扣分（开方衰减，避免 20 个小 bug 直接归零）
        reliability = 100.0
        ded: list[dict[str, Any]] = []
        for sev, weight in (("P0", 30.0), ("P1", 12.0), ("P2", 5.0), ("P3", 1.5)):
            group = [w for w in items if w.severity == sev]
            if not group:
                continue
            penalty = min(60.0, weight * (len(group) ** 0.7))
            reliability -= penalty
            ded.append({
                "dim": "reliability",
                "points": round(penalty, 1),
                "reason": f"{len(group)} 条 {sev} 未解决",
                "examples": [w.title for w in group[:3]],
            })
        reliability = max(0.0, min(100.0, reliability))

        # 2) 一致性：能力冲突 + 架构分裂
        conflicts = self.registry.conflicts()
        coherence = max(0.0, 100.0 - len(conflicts) * 15.0)
        cded = [{
            "dim": "coherence",
            "points": round(len(conflicts) * 15.0, 1),
            "reason": f"{len(conflicts)} 组能力有多个提供者",
            "examples": [c["capability"] for c in conflicts[:4]],
        }] if conflicts else []

        # 3) 覆盖度：目标能力清单覆盖比例
        rep = self.registry.report()
        coverage = rep["coverage"] * 100.0

        # 4) 响应性：探针通过率 + 出站成功率
        responsiveness = 50.0
        rded: list[dict[str, Any]] = []
        if probe_summary:
            ratio = float(probe_summary.get("ratio", 0.0))
            responsiveness = ratio * 70.0
            if probe_summary.get("failed"):
                rded.append({
                    "dim": "responsiveness",
                    "points": round((1 - ratio) * 70.0, 1),
                    "reason": f"{probe_summary['failed']}/{probe_summary['total']} 探针失败",
                    "examples": [r["name"] for r in probe_summary.get("results", []) if not r["ok"]][:4],
                })
        if outbound_stats:
            # 用 24h 窗口，不用累计值：一次历史失败不该永远压着她
            sent = int(outbound_stats.get("sent_24h", outbound_stats.get("sent_total", 0)))
            failed = int(outbound_stats.get("failed_24h", outbound_stats.get("failed_total", 0)))
            # 样本太少时不动这项：0 发 1 败说明不了什么，别误伤
            if sent + failed >= 2:
                success = sent / (sent + failed)
                responsiveness = responsiveness * 0.7 + success * 30.0
                if failed:
                    rded.append({
                        "dim": "responsiveness",
                        "points": round((1 - success) * 30.0, 1),
                        "reason": f"24h 出站失败 {failed} 次 / 成功 {sent} 次",
                    })

        # 5) 自知之明：未处理的弱点里有几成她已经「认了」
        triaged = [w for w in items if w.status in ("triage", "proposed", "fixing")]
        awareness = (len(triaged) / len(items) * 100.0) if items else 100.0
        aded = []
        untriaged = len(items) - len(triaged)
        if untriaged:
            aded.append({
                "dim": "awareness",
                "points": round(100.0 - awareness, 1),
                "reason": f"{untriaged} 条不足还没归档定性",
                "examples": [w.title for w in items if w.status == "open"][:3],
            })

        dims = [
            Dimension("reliability", "可靠性", reliability, 0.35, ded),
            Dimension("coherence", "一致性", coherence, 0.20, cded),
            Dimension("coverage", "能力覆盖", coverage, 0.20, []),
            Dimension("responsiveness", "响应性", responsiveness, 0.15, rded),
            Dimension("awareness", "自知之明", awareness, 0.10, aded),
        ]
        overall = sum(d.value * d.weight for d in dims)
        return {
            "overall": round(overall, 1),
            "grade": _grade(overall),
            "dimensions": [d.to_dict() for d in dims],
            "open_weaknesses": len(items),
            "computed_at": now,
        }

    # ── 注入块 ──────────────────────────────────────────────
    def self_block(self, *, budget: int = 320, top: int = 4) -> str:
        """她会「知道自己不足」的那段话。带预算，不许挤占正文。"""
        items = self.ledger.top(top)
        if not items:
            return ""
        lines = ["<self_awareness>", "我心里清楚自己现在还有这些不足："]
        for item in items:
            lines.append("- " + item.line())
        lines.append("遇到做不到的事就坦白说做不到，别编；能做到就先做好。")
        lines.append("</self_awareness>")
        text = "\n".join(lines)
        if len(text) <= budget:
            return text
        # 超预算就逐条砍，保留开头结尾
        keep = lines[:2]
        for line in lines[2:-1]:
            candidate = "\n".join(keep + [line] + lines[-1:])
            if len(candidate) > budget:
                break
            keep.append(line)
        return "\n".join(keep + [lines[-1]])

    # ── 提案 ────────────────────────────────────────────────
    def propose(self, *, min_score: float = 18.0, limit: int = 5) -> list[dict[str, Any]]:
        """给最该修的几条写改进提案。只写提案，不动代码。"""
        created: list[dict[str, Any]] = []
        for item in self.ledger.top(limit, include_closed=False):
            if item.score < min_score or item.status in ("proposed", "fixing"):
                continue
            plan, risk, rollback = _plan_for(item.category, item.severity, item.title)
            pid = self.ledger.add_proposal(
                item.key, f"改进：{item.title}", kind=f"rule:{item.category}",
                risk=risk, plan=plan, rollback=rollback,
            )
            created.append({"proposal_id": pid, "weakness": item.key, "title": item.title, "risk": risk, "plan": plan})
        return created

    # ── 巡检主流程 ──────────────────────────────────────────
    async def reflect(
        self,
        *,
        outbound_stats: dict[str, Any] | None = None,
        specs: list[health.ProbeSpec] | None = None,
        window_min: float = 30.0,
        do_propose: bool = True,
        min_score: float = 18.0,
    ) -> dict[str, Any]:
        started = time.time()
        cfg = config.section("introspect")

        log_collection: dict[str, Any] = {}
        findings = logscan.scan(window_min=window_min, diagnostics=log_collection)
        log_keys = self.ingest_log(findings)

        probe_specs = specs or default_specs_from_config()
        results = await health.run_probes(probe_specs)
        probe_summary = health.summarize(results)
        probe_keys = self.ingest_probes(results, probe_specs)

        gap_keys = self.sync_gaps()
        scores = self.score(probe_summary=probe_summary, outbound_stats=outbound_stats)
        proposals = self.propose(min_score=min_score, limit=5) if do_propose else []

        report = {
            "started_at": started,
            "elapsed_ms": round((time.time() - started) * 1000, 1),
            "log": {
                "window_min": window_min,
                "collection": log_collection,
                "findings": [f.to_dict() for f in findings],
                "touched": log_keys,
            },
            "probes": probe_summary,
            "gap_touched": gap_keys,
            "capabilities": self.registry.report(),
            "scores": scores,
            "proposals": proposals,
            "ledger": self.ledger.stats(),
            "self_block": self.self_block(
                budget=int(cfg.get("inject_budget", 320)),
                top=int(cfg.get("inject_top", 4)),
            ),
        }
        self._last_report = report
        self._write_snapshot(report)
        get_bus().publish("introspect.cycle", {"overall": scores["overall"], "open": report["ledger"]["open_count"]},
                          source="whale_introspect")
        return report

    def last_report(self) -> dict[str, Any] | None:
        if self._last_report:
            return self._last_report
        path = self.snapshot_path()
        if path.exists():
            try:
                self._last_report = json.loads(path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                return None
        return self._last_report

    # ── 快照 ────────────────────────────────────────────────
    @staticmethod
    def snapshot_path() -> Path:
        return paths.core_dir() / "self_report.json"

    def _write_snapshot(self, report: dict[str, Any]) -> None:
        path = self.snapshot_path()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    # ── 人读报告 ────────────────────────────────────────────
    def markdown_report(self) -> str:
        report = self.last_report()
        if not report:
            return "还没有巡检记录。"
        scores = report["scores"]
        lines = [
            f"# 她的自省报告（{datetime.now():%Y-%m-%d %H:%M}）",
            "",
            f"**综合 {scores['overall']} 分（{scores['grade']}）**，未解决不足 {scores['open_weaknesses']} 条。",
            "",
            "## 分项",
        ]
        for dim in scores["dimensions"]:
            lines.append(f"- {dim['label']}：{dim['value']}（权重 {dim['weight']}）")
            for d in dim["deductions"]:
                ex = "、".join(d.get("examples") or [])
                lines.append(f"  - −{d['points']}：{d['reason']}" + (f"｜例：{ex}" if ex else ""))
        lines += ["", "## 最该修的"]
        for item in self.ledger.top(8):
            lines.append(
                f"- [{item.severity}｜{CATEGORY_LABEL.get(item.category, item.category)}] "
                f"{item.title}（{item.occurrences} 次，{STATUS_LABEL.get(item.status, item.status)}，分 {item.score}）"
            )
        if report.get("proposals"):
            lines += ["", "## 本轮新提案"]
            for p in report["proposals"]:
                lines.append(f"- {p['title']}（风险 {p['risk']}）→ {p['plan']}")
        return "\n".join(lines)


def _grade(overall: float) -> str:
    if overall >= 90:
        return "很不错"
    if overall >= 80:
        return "良好"
    if overall >= 70:
        return "及格"
    if overall >= 55:
        return "需要照顾"
    return "病得不轻"


def _plan_for(category: str, severity: str, title: str) -> tuple[str, str, str]:
    """按类别给一份能落地的提案骨架（计划/风险/回滚）。"""
    if category == "conflict":
        return (
            "1) 确认该能力的唯一提供者；2) 其余提供者降为 standby 或停用；3) 加一致性探针，冲突再现即报警。",
            "medium",
            "恢复各模块原配置并重启对应模块；账本状态改回 open。",
        )
    if category == "gap":
        return (
            "1) 评估三种路径：自研 / 接社区模块 / 明确不做；2) 若不做，写清理由并标 wontfix；3) 若做，先出最小可用验证。",
            "low",
            "删掉提案并把弱点状态改回 triage，不影响运行。",
        )
    if category == "perf":
        return (
            "1) 定位热点（日志/探针数据）；2) 降频或改异步/缓存；3) 观察 24h 事件循环延迟与发送时延。",
            "medium",
            "回滚对应阈值配置（core_config.json 有历史值）。",
        )
    if category == "safety":
        return (
            "1) 立即收紧默认值（关掉高危能力/加鉴权）；2) 补审计日志；3) 再决定是否重开。",
            "high",
            "按变更单逐项恢复，并核对审计日志。",
        )
    return (
        "1) 复现并抓证据（账本 evidence）；2) 定位根因；3) 修复 + 加回归探针；4) 观察一个巡检周期后标 verified。",
        "medium" if severity in ("P1", "P2") else "low",
        "代码回滚到本轮备份；账本记录回归（会自动标 regressed）。",
    )


def default_specs_from_config() -> list[health.ProbeSpec]:
    specs: list[health.ProbeSpec] = []
    for name, raw in (config.section("probes") or {}).items():
        try:
            kind = raw.get("kind", "tcp")
            target = raw.get("target")
            specs.append(
                health.ProbeSpec(
                    name=name,
                    kind=kind,
                    target=tuple(target) if kind == "tcp" else target,
                    timeout=float(raw.get("timeout", 2.0)),
                    max_age_sec=float(raw.get("max_age_sec", 0)),
                    critical=bool(raw.get("critical", False)),
                )
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("探针配置无效 %s：%s", name, exc)
    return specs or list(health.DEFAULT_SPECS)


_MODEL: SelfModel | None = None


def get_self_model() -> SelfModel:
    global _MODEL
    if _MODEL is None:
        _MODEL = SelfModel()
    return _MODEL
