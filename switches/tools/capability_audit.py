# -*- coding: utf-8 -*-
"""能力边界清单：她到底能做什么？

越自主，越说不清"她能干什么、不能干什么"最危险。
这个脚本扫一遍插件目录，把**有副作用的能力**列出来，供人过目。

分类（按危险度）：
    shell     能执行命令
    browser   能控制浏览器 / 看屏幕
    files     能写/删文件
    network   能对外发请求
    speak     能主动发消息（不经主人开口）
    llm       能调模型（花钱）
    gpu       会占用显存

跑法：python tools/capability_audit.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PLUGINS = Path(r"%ASTRBOT_HOME%\data\plugins")

RULES = {
    "shell": [r"subprocess\.(run|Popen|check_output)", r"os\.system\(", r"Start-Process"],
    "browser": [r"webdriver|playwright|pyautogui|pygetwindow", r"Get-NetTCPConnection|screenshot"],
    "files": [r"\.unlink\(", r"shutil\.rmtree", r"\.write_text\(", r"\.write_bytes\("],
    "network": [r"urlopen\(", r"requests\.(get|post)", r"aiohttp"],
    "speak": [r"send_message\(", r"send_private_msg", r"mark_as_temp\(\)"],
    "llm": [r"llm_generate\(", r"token", r"deepseek"],
    "gpu": [r"cuda|torch\.cuda|ComfyUI"],
}


def scan() -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for d in sorted(PLUGINS.iterdir()):
        if not d.is_dir() or d.name.startswith("_"):
            continue
        counts = {k: 0 for k in RULES}
        for f in d.rglob("*.py"):
            if ".bak" in f.name:
                continue
            try:
                t = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for kind, pats in RULES.items():
                for p in pats:
                    counts[kind] += len(re.findall(p, t))
        if any(counts.values()):
            out[d.name] = counts
    return out


def main() -> int:
    data = scan()
    # 只显示"能干危险事"的
    rows = [(n, c) for n, c in data.items()
            if c["shell"] or c["browser"] or c["speak"]]
    rows.sort(key=lambda x: -(x[1]["shell"] * 5 + x[1]["browser"] * 3 + x[1]["speak"]))
    print(f"共 {len(data)} 个插件有能力痕迹，其中 {len(rows)} 个能产生外部影响：\n")
    print(f"{'插件':<40}{'shell':>6}{'browser':>8}{'speak':>7}{'llm':>6}{'files':>7}")
    for n, c in rows:
        print(f"{n:<40}{c['shell']:>6}{c['browser']:>8}{c['speak']:>7}{c['llm']:>6}{c['files']:>7}")
    print("\n说明：数字是源码里出现次数，不代表同一时刻都在用；只用来找『谁能干危险事』。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
