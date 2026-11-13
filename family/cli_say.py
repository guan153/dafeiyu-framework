# -*- coding: utf-8 -*-
"""让别的 CLI（hermes / deepseek / codex ...）也能在这屋说一句话。

用法：
    python cli_say.py --who hermes "我把三个脚本都跑通了"
    python cli_say.py --who deepseek --file 要说的话.txt
    python cli_say.py --who hermes --to-fish "给孩子的活：把日记补到 200 字"

写进 data/extra.jsonl，看板立刻能看到；不打搅她俩（除非 --to-fish）。
"""
from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXTRA = HERE / "data" / "extra.jsonl"
INBOX = Path(r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_whale_mom\mom_inbox.jsonl")

WHO = {
    "hermes": ("hermes", "Hermes"),
    "deepseek": ("deepseek", "DeepSeek"),
    "codex": ("codex", "Codex"),
    "grok": ("grok", "Grok"),
    "kimi": ("kimi", "Kimi"),
}


def append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    need_nl = False
    if path.exists() and path.stat().st_size > 0:
        with path.open("rb") as f:
            f.seek(-1, os.SEEK_END)
            need_nl = f.read(1) != b"\n"
    with path.open("a", encoding="utf-8", newline="\n") as f:
        if need_nl:
            f.write("\n")
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("text", nargs="*", help="要说的话")
    ap.add_argument("--who", default="hermes")
    ap.add_argument("--file", help="从 UTF-8 文件读")
    ap.add_argument("--to-fish", action="store_true", help="同时写进她的信箱，她会回")
    ap.add_argument("--channel", default="CLI")
    args = ap.parse_args()

    text = " ".join(args.text).strip()
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8-sig").strip()
    if not text:
        print("空的，不说")
        return 2

    role, name = WHO.get(args.who.lower(), (args.who, args.who))
    row = {
        "id": uuid.uuid4().hex[:8],
        "ts": time.time(),
        "role": role,
        "who": name,
        "channel": args.channel,
        "kind": "text",
        "text": text,
    }
    append(EXTRA, row)
    print(f"[{name}] {text[:60]}")

    if args.to_fish:
        append(INBOX, {
            "id": uuid.uuid4().hex[:8],
            "ts": time.time(),
            "from": "妈妈" if role == "deepseek" else name,
            "text": text,
            "kind": "text",
            "channel": "CLI",
        })
        print("  也放进她信箱了，她会看到")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
