# -*- coding: utf-8 -*-
"""脸层健康巡检（只读，不改任何东西）。

长线用：一条命令回答「脸层现在到底还活着吗、有没有在注入、坏在哪」。
不靠感觉，每一条都有来源：端口 / 文件 / 日志 / 导入探测。

跑法：
    python tools/face_health.py
    python tools/face_health.py --json      # 只输出 JSON
退出码：0=全绿，1=有 WARN，2=有 FAIL
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ASTRBOT = Path(r"%ASTRBOT_HOME%")
DATA = ASTRBOT / "data"
PLUGIN = DATA / "plugins" / "astrbot_plugin_whale_face"
STATS = DATA / "plugin_data" / "astrbot_plugin_whale_face" / "stats.json"
EMOTION = DATA / "plugin_data" / "whale_core" / "emotion_state.json"   # 真实位置（paths.core_dir）
EMOTION_LEGACY = DATA / "whale_core" / "emotion_state.json"
LOG = ASTRBOT / "runtime.log"
BOT_PY = ASTRBOT / ".venv" / "Scripts" / "python.exe"
REPORT = ROOT / "docs" / "face_health.json"

PORTS = {6185: "AstrBot WebUI", 6199: "AstrBot WS", 3000: "OneBot", 5099: "SnowLuma"}

# 已知噪声：loguru 往 GBK 控制台写 emoji
NOISE = re.compile(r"Logging error in Loguru Handler|UnicodeEncodeError: .gbk")


def read_log_text(limit: int = 600_000) -> str:
    """读日志：中文是 GBK 写的；`errors="ignore"` 会吞掉错误字节导致假阴性，
    所以先严格解码，失败才换下一个。"""
    if not LOG.exists():
        return ""
    raw = LOG.read_bytes()[-limit:]
    for enc in ("gbk", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("gbk", errors="ignore")


def port_up(port: int, host: str = "127.0.0.1", timeout: float = 0.7) -> bool:
    s = socket.socket()
    s.settimeout(timeout)
    try:
        return s.connect_ex((host, port)) == 0
    finally:
        s.close()


def age_sec(ts: float | None) -> float | None:
    return None if ts is None else max(0.0, time.time() - ts)


def check() -> list[dict]:
    items: list[dict] = []

    def add(name: str, level: str, detail: str) -> None:
        items.append({"check": name, "level": level, "detail": detail})

    # 1) 端口
    for port, label in PORTS.items():
        up = port_up(port)
        add(f"port:{label}({port})", "OK" if up else "FAIL", "UP" if up else "DOWN")

    # 2) 插件文件
    for f, label in ((PLUGIN / "main.py", "插件 main.py"), (PLUGIN / "face_block.py", "纯逻辑模块"),
                     (PLUGIN / "metadata.yaml", "metadata")):
        add(f"file:{label}", "OK" if f.exists() else "FAIL", str(f))

    # 3) 内核库 + 在机器人解释器里能不能 import
    core_face = DATA / "whale_core" / "face.py"
    add("file:whale_core/face.py", "OK" if core_face.exists() else "FAIL", str(core_face))
    if BOT_PY.exists() and core_face.exists():
        try:
            out = subprocess.run(
                [str(BOT_PY), "-c", "import sys;sys.path.insert(0, r'%s');from whale_core.face import card_for;print(card_for('开心',0.6).exp3)" % DATA],
                capture_output=True, text=True, timeout=60, encoding="utf-8", errors="ignore",
            )
            ok = out.returncode == 0 and out.stdout.strip()
            add("import:whale_core.face(机器人解释器)", "OK" if ok else "FAIL",
                (out.stdout or out.stderr).strip().splitlines()[-1][:120] if (out.stdout or out.stderr) else "")
        except Exception as e:  # noqa: BLE001
            add("import:whale_core.face(机器人解释器)", "FAIL", f"{type(e).__name__}: {e}")

    # 4) 插件是否被加载
    if LOG.exists():
        tail = read_log_text()
        loaded = "Loading plugin astrbot_plugin_whale_face" in tail
        add("log:插件加载", "OK" if loaded else "FAIL", "找到加载行" if loaded else "日志里没有加载行")

        real_err = []
        lines = tail.splitlines()
        for i, line in enumerate(lines):
            if not re.search(r"plugin_whale_face.*(Traceback|Error)|Failed to load plugin", line):
                continue
            ctx = "\n".join(lines[max(0, i - 2): i + 6])
            if NOISE.search(ctx):
                continue
            real_err.append(line.strip()[:160])
        add("log:脸层报错", "OK" if not real_err else "FAIL", "无" if not real_err else "; ".join(real_err[:2]))

        injects = re.findall(r"\[脸层\] 注入 mood=(\S+)\(([\d.]+)\) scene=(\S+) len=(\d+)", tail)
        if injects:
            last = injects[-1]
            add("log:注入次数", "OK", f"{len(injects)} 次 · 最近 {last[0]} {last[1]} {last[2]} {last[3]}字")
        else:
            add("log:注入次数", "WARN", "还没注入过（等一条私聊消息）")
    else:
        add("log:运行时日志", "FAIL", "runtime.log 不存在")

    # 5) 情绪源新鲜度
    if EMOTION.exists():
        try:
            st = json.loads(EMOTION.read_text(encoding="utf-8"))
            updated = float(st.get("updated_at") or 0) or None
            a = age_sec(updated)
            level = "OK" if (a is not None and a < 1800) else "WARN"
            add("emotion:快照", level,
                f"mood={st.get('mood')} 强度={st.get('intensity')} 版本={st.get('version')} 距今={a:.0f}s" if a is not None else "无 updated_at")
        except Exception as e:  # noqa: BLE001
            add("emotion:快照", "WARN", f"解析失败：{e}")
    else:
        add("emotion:快照", "WARN", "emotion_state.json 还没生成（affect 未推送过）")

    # 6) 注入统计
    if STATS.exists():
        try:
            s = json.loads(STATS.read_text(encoding="utf-8"))
            last = s.get("last") or {}
            ts = last.get("ts")
            a = age_sec(float(ts)) if ts else None
            detail = f"注入 {s.get('injected', 0)} 轮 / 跳过 {s.get('skipped', 0)} 轮"
            if last:
                detail += f" · 最近 {last.get('mood', '?')}/{last.get('scene', '?')} · 距今 {a:.0f}s" if a is not None else ""
            add("stats:注入统计", "OK" if s.get("injected") else "WARN", detail)

            # 触达率：注入了 ≠ 用上了。没有样本时是 WARN，不是 OK——
            # 不能拿「没数据」冒充「很健康」。
            total = int(s.get("hit", 0)) + int(s.get("miss", 0))
            if total == 0:
                add("stats:触达率", "WARN", "还没有回复样本（等一轮对话）")
            else:
                rate = float(s.get("hit", 0)) / total
                level = "OK" if rate >= 0.3 else "WARN"
                lh = s.get("last_hit") or {}
                extra = f" · 最近命中 {lh.get('mood')}" + (f" {lh.get('words')}" if lh.get("words") else "")
                add("stats:触达率", level, f"{rate:.0%}（{s.get('hit', 0)}/{total}）{extra}")
        except Exception as e:  # noqa: BLE001
            add("stats:注入统计", "WARN", f"解析失败：{e}")
    else:
        add("stats:注入统计", "WARN", "stats.json 还未生成（说明还没有效注入）")
        add("stats:触达率", "WARN", "无数据")

    # 7) 配置
    cfg = DATA / "plugin_data" / "astrbot_plugin_whale_face" / "config.json"
    if cfg.exists():
        try:
            c = json.loads(cfg.read_text(encoding="utf-8"))
            add("config:开关", "OK" if c.get("enabled", True) else "WARN",
                f"enabled={c.get('enabled')} budget={c.get('budget')}")
        except Exception:  # noqa: BLE001
            add("config:开关", "WARN", "配置解析失败")
    else:
        add("config:开关", "OK", "未生成 → 用默认值（enabled=True, budget=240）")

    # 7.5) plugin_set 白名单：钩子是按 event.plugins_name 过滤的，
    #      不在名单里 = 插件能加载、能打日志，但钩子和命令**一次都不会被调用**。
    #      这个坑真实发生过：脸层上线后一直“没注入”，查到最后就是这一条。
    cfg_file = ASTRBOT / "data" / "cmd_config.json"
    if cfg_file.exists():
        try:
            c = json.loads(cfg_file.read_text(encoding="utf-8"))
            pset = c.get("plugin_set", ["*"])
            if not isinstance(pset, list) or "*" in pset:
                add("config:plugin_set", "OK", "通配符 *（全部启用）")
            elif "astrbot_plugin_whale_face" in pset:
                add("config:plugin_set", "OK", f"在名单内（共 {len(pset)} 个）")
            else:
                add("config:plugin_set", "FAIL",
                    "脸层不在 plugin_set → 钩子/命令永远不会被调用（改了配置还得重启才生效）")
        except Exception as e:  # noqa: BLE001
            add("config:plugin_set", "WARN", f"解析失败：{e}")
    else:
        add("config:plugin_set", "WARN", "cmd_config.json 不存在")

    # 8) 两个心情源是否一致（Hub vs affect 的旧文件）    #    这是 v3 的老病根：同一轮注入两种心情。这里只报，不改。
    hub_mood = None
    if EMOTION.exists():
        try:
            hub_mood = (json.loads(EMOTION.read_text(encoding="utf-8")) or {}).get("mood")
        except Exception:  # noqa: BLE001
            hub_mood = None
    affect_file = DATA / "plugin_data" / "astrbot_plugin_whale_affect" / "affect_state.json"
    if affect_file.exists() and hub_mood:
        try:
            a = json.loads(affect_file.read_text(encoding="utf-8"))
            a_mood = a.get("label") or (a.get("core") or {}).get("label")
            if a_mood and a_mood != hub_mood:
                add("consistency:心情双源", "WARN", f"Hub={hub_mood} / affect文件={a_mood}")
            else:
                add("consistency:心情双源", "OK", f"一致（{hub_mood}）")
        except Exception as e:  # noqa: BLE001
            add("consistency:心情双源", "WARN", f"读取失败：{e}")
    else:
        add("consistency:心情双源", "OK", "affect 旧文件不存在，只有 Hub 一个源")

    return items


def overall(items: list[dict]) -> str:
    levels = {i["level"] for i in items}
    if "FAIL" in levels:
        return "FAIL"
    if "WARN" in levels:
        return "WARN"
    return "OK"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    items = check()
    verdict = overall(items)
    payload = {
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "verdict": verdict,
        "items": items,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    else:
        icon = {"OK": "✅", "WARN": "⚠️ ", "FAIL": "❌"}
        print(f"脸层巡检 · {payload['checked_at']} · 结论 {verdict}\n" + "-" * 66)
        for it in items:
            print(f"{icon.get(it['level'], '?')} {it['check']:<28} {it['detail']}")
        print("-" * 66 + f"\n报告 → {REPORT}")
    return {"OK": 0, "WARN": 1, "FAIL": 2}[verdict]


if __name__ == "__main__":
    raise SystemExit(main())
