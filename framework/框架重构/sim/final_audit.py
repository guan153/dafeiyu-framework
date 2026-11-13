r"""综合验收：把全套测试与线上状态汇总成一份报告。

用法：python final_audit.py
输出：21_综合验收报告.md

为什么启动时就把 TMP/TEMP/TMPDIR 指到 G: 盘可写目录：
  DSH 沙箱里系统 TEMP（%LOCALAPPDATA%\Temp）不可写，子进程里的
  tempfile.mkdtemp()/mkstemp() 会退避重试直到 180s 超时 —— 内核单测
  （tempfile 建临时目录）与闭环接口（写 memory.jsonl）都会因此假红。
  本脚本在 CHECKS 开跑前把三个环境变量统一改成项目旁的可写目录，
  subprocess.run 默认继承 os.environ，于是所有子进程都拿到可写 TEMP。

退出码：测试全过且门闩全过 → 0；任一失败 → 1（供 CI / 计划任务判定）。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time

try:
    # 无控制台/GBK 环境跑计划任务时，emoji 会抛 UnicodeEncodeError
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

PY = r"D:\astrbot-venv\Scripts\python.exe"
ROOT = r"%PROJECT_HOME%\框架重构"
OUT = os.path.join(ROOT, "21_综合验收报告.md")
BEAMS = r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_whale_beams"

# DSH 沙箱内系统 TEMP 不可写 → 统一改到项目可写目录；不存在就创建。
# 必须在下面所有 subprocess.run 之前设置，子进程默认继承 os.environ。
TMPDIR = r"%PROJECT_HOME%\tmp"
try:
    os.makedirs(TMPDIR, exist_ok=True)
except OSError:
    pass
os.environ["TMP"] = TMPDIR
os.environ["TEMP"] = TMPDIR
os.environ["TMPDIR"] = TMPDIR
# 子进程输出统一 UTF-8：否则父进程按 utf-8 解码会把中文末行解成乱码（报告表格）
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUTF8"] = "1"

CHECKS = [
    # 内核单测的项数不写死，从 run_tests.py 的 `SUMMARY: run=N` 动态取（见 _test_count）。
    ("内核单测", [PY, os.path.join(ROOT, "beams_core", "tests", "run_tests.py")]),
    ("闭环接口（6 项）", [PY, os.path.join(ROOT, "tests", "test_feedback_loop.py")]),
    ("单文件版一致性", [PY, os.path.join(ROOT, "tests", "test_single_file.py")]),
    ("闲置→紧迫度链条", [PY, os.path.join(ROOT, "tests", "test_idle_urgency.py")]),
    ("参数敏感度", [PY, os.path.join(ROOT, "tests", "test_sensitivity.py")]),
    ("3000 拍压力测试", [PY, os.path.join(ROOT, "tests", "test_stress.py")]),
    ("接管覆盖度（8 出口）", [PY, os.path.join(ROOT, "sim", "check_takeover.py")]),
    ("指标报告 §G（生成）", [PY, os.path.join(ROOT, "sim", "metrics_report.py")]),
    ("风格抽检 §I/§N（生成）", [PY, os.path.join(ROOT, "sim", "style_audit.py")]),
    ("切条回放（真实文本）", [PY, os.path.join(ROOT, "sim", "bubble_replay.py")]),
]


def run(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180,
                           encoding="utf-8", errors="replace")
        out = (r.stdout or "") + (r.stderr or "")
        return r.returncode == 0, out
    except Exception as exc:
        return False, str(exc)


def _test_count(out):
    """从 run_tests.py 的 `SUMMARY: run=N failures=...` 动态取用例数。

    口径会随测试增长（26 → 39 → …），写死数字必然过期，故取实际执行数。
    """
    m = re.search(r"SUMMARY:\s*run=(\d+)", out or "")
    return int(m.group(1)) if m else None


def _read(p):
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def check_gates():
    """iter11/iter12 门闩静态检查（对应 02 §M B/C/E 项）。"""
    base = r"%ASTRBOT_HOME%\data\plugins"
    brain = _read(os.path.join(base, "astrbot_plugin_whale_brain", "main.py"))
    fish = _read(os.path.join(base, "astrbot_plugin_whale_fishtalk", "main.py"))
    rows = [
        ("切条：brain 气泡整形（_bubble_split）", "_bubble_split" in brain and "条气泡" in brain),
        ("切条：fishtalk 不再强制单段（_as_chain）", "_as_chain" in fish and "强制单段：" not in fish),
        ("反八股：fishtalk AI味禁表（AI_SLOP_BANS）", "AI_SLOP_BANS" in fish),
        ("反八股：brain 系统侧硬禁注入", "AI味禁令" in brain),
        ("反八股：fishtalk 工具调用保护", "tool_call" in fish),
        ("气泡口径：brain 允许 2-3 条连发", "可以连发 2-3 条短气泡" in brain),
    ]
    for name in ("whale_bili", "dafeiyu_soul", "speech_style", "whale_memoryflow", "virtual_life"):
        src = _read(os.path.join(base, "astrbot_plugin_" + name, "main.py"))
        rows.append(("热更 grace：%s terminate 不阻塞" % name,
                     ("asyncio.wait" in src or "wait_for" in src) and "async def terminate" in src))
    voice = _read(os.path.join(base, "astrbot_plugin_whale_voice", "main.py"))
    # 主动投递（2026-11-03 由 webchat 排除改为 owner_session 绑定，旧静态标记 `real =` 已不存在）：
    # 现语义 = _guess_owner_umo() 读 owner_session 配置 → MessageSession.from_str 解析校验，
    # 注入/回话前比对 event.unified_msg_origin != owner_session 即拦截，即"只投绑定会话"。
    rows.append(("主动投递：只投 owner_session 绑定会话",
                 "owner_session" in voice and "unified_msg_origin" in voice))
    iris = r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_iris_chat_memory"
    rows.append(("记忆 L2：向量索引在位",
                 os.path.isfile(os.path.join(iris, "faiss", "memory_default", "index.faiss"))))
    rows.append(("记忆 L2：索引元数据可读",
                 bool(_read(os.path.join(iris, "faiss", "memory_default", "index_meta.json")))))
    return rows


def main():
    lines = ["# 综合验收报告 · %s" % time.strftime("%Y-%m-%d %H:%M"), ""]
    rows = []
    for name, cmd in CHECKS:
        ok, out = run(cmd)
        tail = [ln for ln in out.strip().splitlines() if ln.strip()][-1:] or [""]
        if name == "内核单测":
            n = _test_count(out)
            name = "内核单测（%d 项）" % n if n else "内核单测（项数未取到）"
        rows.append((name, ok, tail[0][:80]))
    lines.append("## 一、测试")
    lines.append("")
    lines.append("| 项目 | 结果 | 末行 |")
    lines.append("|---|---|---|")
    for name, ok, tail in rows:
        lines.append("| %s | %s | %s |" % (name, "✅ PASS" if ok else "❌ FAIL", tail))
    lines.append("")
    passed = sum(1 for _n, ok, _t in rows if ok)

    # 线上状态
    age = float("inf")  # 状态文件读不到时也保证「结论」段不抛 NameError
    lines.append("## 二、线上状态")
    lines.append("")
    try:
        st = json.load(open(os.path.join(BEAMS, "beams_state.json"), encoding="utf-8"))
        snap = json.load(open(os.path.join(BEAMS, "beams_snapshot.json"), encoding="utf-8"))
        age = time.time() - float(st.get("ts") or 0)
        lines.append("- 内核：tick %s，心跳 %.1fs 前 %s" % (
            st.get("tick_count"), age, "✅" if age < 30 else "⚠️"))
        lines.append("- 情绪：%s（%+.2f）· 动机：%s · 闸门：%s" % (
            (st.get("fast") or {}).get("label"),
            float((st.get("fast") or {}).get("valence") or 0),
            (st.get("arbitration") or {}).get("winner"),
            (st.get("gate") or {}).get("open")))
        lines.append("- 对外契约：outlet=%s urgency=%.2f gate_open=%s route=%s" % (
            snap.get("outlet"), float(snap.get("urgency") or 0),
            snap.get("gate_open"), snap.get("route")))
    except Exception as exc:
        lines.append("- 读取失败：%s" % exc)
    lines.append("")

    # iter11/iter12 门闩
    lines.append("## 三、本轮门闩（iter11/iter12）")
    lines.append("")
    lines.append("| 门闩 | 结果 |")
    lines.append("|---|---|")
    gate_rows = check_gates()
    for _n, _ok in gate_rows:
        lines.append("| %s | %s |" % (_n, "✅" if _ok else "❌"))
    lines.append("")
    gate_fail = [n for n, ok in gate_rows if not ok]

    # 文件清单
    lines.append("## 四、交付物")
    lines.append("")
    docs = sorted(f for f in os.listdir(ROOT) if f.endswith((".md", ".mmd", ".py", ".html", ".png")))
    lines.append("- 框架目录文件：%d 个" % len(docs))
    lines.append("- 关键：00_总纲 / 10_架构v2 / 13 流程图 v5 / 16 S3接管 / 19 调参手册 / beams.py / beams_core/")
    lines.append("")

    # 结论 + 退出码：测试全过且门闩全过才算通过
    all_ok = passed == len(rows) and not gate_fail
    lines.append("## 五、结论")
    lines.append("")
    lines.append("- 测试通过：%d/%d" % (passed, len(rows)))
    lines.append("- 线上：%s" % ("运行中" if age < 30 else "需检查"))
    lines.append("- 门闩：%s" % ("全部通过" if not gate_fail else ("失败 " + "、".join(gate_fail))))
    lines.append("- 退出码：%d（0=全过，1=有失败）" % (0 if all_ok else 1))
    text = "\n".join(lines)
    print(text)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    print("\n报告：" + OUT)
    print("结论：测试 %d/%d，门闩 %s → exit %d" % (
        passed, len(rows), "全过" if not gate_fail else "失败 %d 项" % len(gate_fail),
        0 if all_ok else 1))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())