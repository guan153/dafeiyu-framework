# -*- coding: utf-8 -*-
"""路径迁移助手：把写死的 AstrBot data 路径换成 whale_core.paths 引用。

只替换能证明等价的两类写法：
    A) 某个构造调用里的字面量： _Path(r"...\\plugin_data\\X\\y.json")
    B) 裸字符串：              r"...\\plugin_data\\X"
    C) join 的第一个参数：      join(r"...\\plugin_data", "X")
    D) 数据根下的裸文件：       r"...\\data\\data_v4.db"

其余（拼接、f-string 带变量、split）一律不动——盲替换会悄悄改坏。

默认 dry-run；--apply 才写，且先备份。
"""

from __future__ import annotations

import argparse
import re
import shutil
import time
from pathlib import Path

PLUGINS = Path(r"%ASTRBOT_HOME%\data\plugins")
DATA = r"%ASTRBOT_HOME%\data"
PDL = DATA + r"\plugin_data"

_QL = re.escape('r"' + PDL + "\\")      # r"...\plugin_data\  （含结尾反斜杠）
_QLD = re.escape('r"' + DATA + "\\")     # r"...\data\

PAT_WRAP = re.compile(r'([A-Za-z_]\w*)\(\s*' + _QL + r'([^"]+)"\s*\)')
PAT_JOIN = re.compile(r'(join\(\s*)' + re.escape('r"' + PDL + '"'))
PAT_STR = re.compile(_QL + r'([^"]+)"')
PAT_DB = re.compile(_QLD + r'([^"\\]+)"')

IMPORT_BLOCK = (
    "try:  # [path-migrate] 唯一出处；拿不到时才退回字面量\n"
    "    from whale_core.paths import DATA, PD\n"
    "except Exception:  # noqa: BLE001\n"
    "    from pathlib import Path as _P0\n"
    '    DATA = _P0(r"' + DATA + '")\n'
    '    PD = _P0(r"' + PDL + '")\n'
)


def _expr(rest: str) -> str:
    parts = [x for x in rest.split("\\") if x]
    out = "PD"
    for p in parts:
        out += f' / "{p}"'
    return out


PH = "\x00PATHMIGRATE_BLOCK\x00"


def migrate_text(text: str) -> tuple[str, list[str]]:
    log: list[str] = []
    # 已经迁移过的兜底块要原样保住，否则第二次跑会把它自己也改掉（不幂等）
    protected = ""
    if IMPORT_BLOCK.strip() and IMPORT_BLOCK.strip() in text:
        protected = text[text.find(IMPORT_BLOCK.strip()): text.find(IMPORT_BLOCK.strip()) + len(IMPORT_BLOCK.strip())]
        text = text.replace(protected, PH, 1)

    def repl_wrap(m: re.Match) -> str:
        cls = m.group(1)
        expr = f"{cls}({_expr(m.group(2))})"
        log.append(f"{cls}(...) → {expr}")
        return expr

    def repl_join(m: re.Match) -> str:
        log.append("join(字面量, …) → join(PD, …)")
        return m.group(1) + "PD"

    def repl_str(m: re.Match) -> str:
        expr = f"str({_expr(m.group(1))})"
        log.append(f"字符串 → {expr}")
        return expr

    def repl_db(m: re.Match) -> str:
        log.append(f'字面量 → str(DATA / "{m.group(1)}")')
        return f'str(DATA / "{m.group(1)}")'

    text = PAT_WRAP.sub(repl_wrap, text)
    text = PAT_JOIN.sub(repl_join, text)
    text = PAT_STR.sub(repl_str, text)
    text = PAT_DB.sub(repl_db, text)

    if protected:
        text = text.replace(PH, protected, 1)

    if log and "[path-migrate]" not in text:
        lines = text.splitlines()
        depth = 0
        last = -1
        for i, l in enumerate(lines):
            depth += l.count("(") - l.count(")")
            if depth < 0:
                depth = 0
            if (l.startswith("import ") or l.startswith("from ")) and depth == 0:
                last = i
        if last < 0:
            last = 0
            while last < len(lines) and not lines[last].strip():
                last += 1
        block = [""] + IMPORT_BLOCK.rstrip("\n").split("\n")
        lines[last + 1:last + 1] = block
        text = "\n".join(lines) + "\n"
        log.append("已加 import")
    return text, log


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plugin", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    d = PLUGINS / args.plugin
    if not d.exists():
        print(f"插件不存在：{d}")
        return 2
    total = 0
    for f in sorted(d.rglob("*.py")):
        if ".bak" in f.name or "__pycache__" in str(f):
            continue
        src = f.read_text(encoding="utf-8", errors="ignore")
        out, log = migrate_text(src)
        if not log:
            continue
        total += len(log)
        print(f"\n{f.relative_to(PLUGINS)}：{len(log)} 处")
        for item in log[:12]:
            print("   ", item)
        if args.apply and out != src:
            shutil.copy2(f, f.with_suffix(f".py.bak-path-{time.strftime('%H%M%S')}"))
            f.write_text(out, encoding="utf-8")
            print("    已写入（含备份）")
    print(f"\n合计可迁移 {total} 处" + ("" if args.apply else "（dry-run，加 --apply 才写）"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
