# -*- coding: utf-8 -*-
"""v3 验收：九条硬指标，逐条给证据，PASS/FAIL 落盘。

设计原则：不测「我觉得应该好」，只测「现在真的怎么样」。
- 端口用 TCP 实测；
- 情绪只认带 version 和 source 的快照，且要求足够新鲜；
- 账本只认真的有行；
- 加载失败从 runtime.log 里捞，不靠猜。

用法：
    python tools/acceptance.py            # 跑一遍，写 docs/验收结果-v3.json
    python tools/acceptance.py --json     # 只输出 JSON
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("ASTRBOT_ROOT", r"%ASTRBOT_HOME%")

from whale_core import paths  # noqa: E402

RESULTS: list[dict] = []


def check(name: str, ok: bool, detail: str, *, weight: int = 1) -> None:
    RESULTS.append({"check": name, "ok": ok, "detail": detail, "weight": weight})
    print(f"{'PASS' if ok else 'FAIL'}  {name} ｜ {detail}")


def tcp(port: int, host: str = "127.0.0.1", timeout: float = 1.5) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex((host, port)) == 0
    finally:
        s.close()


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    root = paths.astrbot_root()
    data = paths.data_dir()
    core_pkg = data / "whale_core" / "__init__.py"
    plug_core = data / "plugins" / "astrbot_plugin_whale_core" / "main.py"
    plug_intro = data / "plugins" / "astrbot_plugin_whale_introspect" / "main.py"

    check("内核已部署", core_pkg.exists(), str(core_pkg))
    check("内核插件已部署", plug_core.exists() and plug_intro.exists(), "whale_core + whale_introspect")

    version = ""
    if core_pkg.exists():
        for line in core_pkg.read_text(encoding="utf-8").splitlines():
            if line.startswith("__version__"):
                version = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    check("内核版本 v3", version.startswith("3."), f"version={version or '?'}")

    for port, label in ((6185, "AstrBot WebUI"), (6199, "反向 WS"), (3000, "OneBot HTTP")):
        check(f"{label} 端口在线", tcp(port), f"127.0.0.1:{port}")

    led_path = paths.ledger_db()
    rows = 0
    if led_path.exists():
        import sqlite3

        try:
            con = sqlite3.connect(f"file:{led_path}?mode=ro", uri=True)
            rows = con.execute("SELECT COUNT(*) FROM weakness").fetchone()[0]
            con.close()
        except Exception:
            rows = 0
    check("弱点账本有内容", rows >= 8, f"{rows} 条（期望 ≥8 条架构债）", weight=2)

    emo = load_json(paths.emotion_file())
    emo_age = time.time() - float(emo.get("updated_at") or 0)
    check(
        "情绪单一事实源在更新",
        bool(emo) and emo_age < 1800 and bool(emo.get("source")),
        f"source={emo.get('source') or '—'} version={emo.get('version')} 距今 {int(emo_age)}s",
        weight=2,
    )
    check(
        "情绪写者是 affect",
        str(emo.get("source") or "") == "astrbot_plugin_whale_affect",
        f"source={emo.get('source') or '—'}",
        weight=2,
    )

    report = load_json(paths.core_dir() / "self_report.json")
    dims = (report.get("scores") or {}).get("dimensions") or []
    check(
        "自省报告有五个维度",
        len(dims) == 5 and 0 <= float((report.get("scores") or {}).get("overall", -1)) <= 100,
        f"overall={(report.get('scores') or {}).get('overall')} dims={len(dims)}",
        weight=2,
    )
    self_block = report.get("self_block") or ""
    check(
        "自省块可注入",
        "<self_awareness>" in self_block and len(self_block) <= 400,
        f"{len(self_block)} 字符",
    )

    cfg = load_json(paths.config_json())
    outbound = cfg.get("outbound") or {}
    check(
        "出站闸门有配置",
        outbound.get("daily_cap", 0) and "quiet_hours" in outbound,
        f"daily_cap={outbound.get('daily_cap')} quiet={outbound.get('quiet_hours')}",
        weight=2,
    )

    # 记忆分层：六层都在、契约合规
    try:
        from whale_core.memory import get_memory_hub

        mem = get_memory_hub().report()
        layers = {p["layer"] for p in mem["census"]["providers"]}
        check(
            "记忆六层齐全",
            {"raw", "episodic", "semantic", "profile", "digest", "index"} <= layers,
            f"layers={sorted(layers)}",
        )
        check(
            "记忆分层契约合规",
            mem["contract"]["ok"],
            "每层单写者" if mem["contract"]["ok"] else str(mem["contract"]["violations"]),
            weight=2,
        )
        check(
            "记忆普查可统计",
            mem["census"]["total_records"] > 0,
            f"{mem['census']['total_records']} 条事实",
        )
    except Exception as exc:  # noqa: BLE001
        check("记忆普查可运行", False, f"{type(exc).__name__}: {exc}", weight=2)

    # 注入观测：模块可用 + 统计文件可写
    try:
        from whale_core import injectwatch

        probe = injectwatch.measure(["<affect_state>开心</affect_state>"], prompt="hi", cap_chars=100)
        check(
            "注入观测可用",
            probe["total_chars"] > 0 and "affect_state" in probe["tags"],
            f"total={probe['total_chars']} tags={probe['tags']}",
        )
    except Exception as exc:  # noqa: BLE001
        check("注入观测可用", False, f"{type(exc).__name__}: {exc}")

    # 联网检索：模块可用（不跑真实网络，避免验收变慢/受网络影响）
    try:
        from whale_core import websearch

        check(
            "联网检索模块可用",
            websearch.search("") == [] and "没搜到" in websearch.render([], "x"),
            "空查询不联网、空结果有文案",
        )
    except Exception as exc:  # noqa: BLE001
        check("联网检索模块可用", False, f"{type(exc).__name__}: {exc}")

    # 记忆向量索引：有内容且能语义检索（不联网也能看规模）
    try:
        from whale_core import memory_index

        stats = memory_index.get_index().stats()
        check(
            "记忆向量索引就绪",
            int(stats.get("total", 0)) > 0,
            f"{stats.get('total')} 条（{stats.get('by_source')}）",
            weight=2,
        )
    except Exception as exc:  # noqa: BLE001
        check("记忆向量索引就绪", False, f"{type(exc).__name__}: {exc}")

    # 注入配置：向量/重排模式 + 阈值
    mem_cfg = load_json(paths.config_json()).get("memory_inject") or {}
    check(
        "记忆注入走向量/重排模式",
        mem_cfg.get("mode") in ("vector", "vector_rerank")
        and float(mem_cfg.get("min_score", 0)) >= 0.6,
        f"mode={mem_cfg.get('mode')} coarse={mem_cfg.get('coarse_score')} "
        f"min_score={mem_cfg.get('min_score')} limit={mem_cfg.get('limit')}",
        weight=2,
    )

    # 唱歌：资产齐全（只看不跑，不占 CPU）
    try:
        from whale_core import sing as sing_mod

        ok, why = sing_mod.assets_ok()
        songs = sing_mod.list_songs()
        check("唱歌资产齐全", ok and len(songs) >= 1, f"{why}；可唱 {[s['label'] for s in songs]}", weight=2)
    except Exception as exc:  # noqa: BLE001
        check("唱歌资产齐全", False, f"{type(exc).__name__}: {exc}")

    # 生图兜底：决策模块可用，且插件配置默认开启
    try:
        from whale_core import imagegen

        decision = imagegen.can_fallback(local_ok=False, local_error="probe", meta_ready=True, meta_remaining=1)
        paint_cfg = load_json(paths.data_dir() / "config" / "astrbot_plugin_whale_paint_config.json")
        check(
            "生图兜底已启用",
            decision.use_meta and bool(paint_cfg.get("fallback_meta_on_local_fail", False)),
            f"decision={decision.reason} plugin_flag={paint_cfg.get('fallback_meta_on_local_fail')} "
            f"cap={paint_cfg.get('fallback_daily_cap')}",
            weight=2,
        )
    except Exception as exc:  # noqa: BLE001
        check("生图兜底已启用", False, f"{type(exc).__name__}: {exc}")

    # 情感模型三件套 + GPU 能力状态
    import urllib.request as _ur

    def _http_json(url: str, timeout: float = 3.0):
        try:
            with _ur.urlopen(url, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            return {}

    emotion_ok = {}
    for name, url in (("laya", "http://127.0.0.1:8901/health"),
                      ("qwenemo", "http://127.0.0.1:8914/health"),
                      ("audioclass", "http://127.0.0.1:8916/health")):
        emotion_ok[name] = bool(_http_json(url).get("ok"))
    check(
        "情感模型三件套在线",
        all(emotion_ok.values()),
        ", ".join(f"{k}={'OK' if v else 'DOWN'}" for k, v in emotion_ok.items()),
        weight=2,
    )

    cap = _http_json("http://127.0.0.1:8921/status")
    services = cap.get("services") or {}
    tts_up = bool((services.get("tts") or {}).get("up"))
    draw_up = bool((services.get("draw") or {}).get("up"))
    check(
        "GPU 能力有可用项（说话或生图）",
        tts_up or draw_up,
        f"current={cap.get('current')} tts={tts_up} draw={draw_up}",
        weight=2,
    )

    # 情绪读者统一：state_bus 与 brain.psyche 都必须从 EmotionHub 取
    try:
        import importlib.util as _iu

        def _load_module(path: Path, name: str):
            spec = _iu.spec_from_file_location(name, path)
            mod = _iu.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod

        sb = _load_module(paths.data_dir() / "plugins" / "astrbot_plugin_whale_state" / "state_bus.py", "sb_probe")
        ps = _load_module(paths.data_dir() / "plugins" / "astrbot_plugin_whale_brain" / "psyche.py", "psy_probe")
        src_state = (sb._read_affect_uncached() or {}).get("source")
        src_brain = (ps.emotion() or {}).get("_source")
        check(
            "情绪读者统一走 Hub",
            src_state == "emotion_hub" and src_brain == "emotion_hub",
            f"state_bus={src_state} brain={src_brain}",
            weight=2,
        )
    except Exception as exc:  # noqa: BLE001
        check("情绪读者统一走 Hub", False, f"{type(exc).__name__}: {exc}")

    # 闸门：必须能读到既有 voice_gate 的决策（只读，不改）
    try:
        from whale_core import voicegate

        info = voicegate.describe()
        check(
            "语音闸门可读",
            bool(info.get("dir")),
            f"dir={info.get('dir') or '未找到'} code={info['decision']['code']}",
        )
    except Exception as exc:  # noqa: BLE001
        check("语音闸门可读", False, f"{type(exc).__name__}: {exc}")

    log = root / "runtime.log"
    bad = []
    if log.exists():
        with log.open("rb") as fh:
            fh.seek(max(0, log.stat().st_size - 2 * 1024 * 1024))
            text = fh.read().decode("utf-8", "replace")
        for name in ("astrbot_plugin_whale_core", "astrbot_plugin_whale_introspect"):
            if f"Failed to load plugin {name}" in text:
                bad.append(name)
    check("两个新插件无加载失败", not bad, "无失败记录" if not bad else f"失败：{bad}", weight=2)

    passed = sum(1 for r in RESULTS if r["ok"])
    total = len(RESULTS)
    weight_total = sum(r["weight"] for r in RESULTS)
    weight_ok = sum(r["weight"] for r in RESULTS if r["ok"])
    score = round(weight_ok / weight_total * 100, 1) if weight_total else 0.0

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "version": version,
        "passed": passed,
        "total": total,
        "weighted_score": score,
        "verdict": "PASS" if passed == total else ("PARTIAL" if score >= 80 else "FAIL"),
        "results": RESULTS,
    }
    if not args.json:
        out = ROOT / "docs" / "验收结果-v3.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n总结：{passed}/{total} 通过，加权 {score} 分，判定 {payload['verdict']}")
        print(f"结果已写入：{out}")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    return 0 if payload["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
