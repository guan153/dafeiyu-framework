# -*- coding: utf-8 -*-
"""投递一句话到妈妈的收件箱（她每 15 秒看一次）。

用法：
    python say.py "要说的话"
    python say.py --file path.txt          # 从 UTF-8 文件读（中文最稳）
    python say.py --sender 妈妈 --file ...

只做一件事：往 mom_inbox.jsonl 追加一行 JSON。
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path

INBOX = Path(r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_whale_mom\mom_inbox.jsonl")


def main() -> int:
    args = sys.argv[1:]
    sender = "妈妈"
    text = ""
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--file":
            i += 1
            text = Path(args[i]).read_text(encoding="utf-8-sig").strip()
        elif a == "--sender":
            i += 1
            sender = args[i]
        else:
            text = (text + " " + a).strip()
        i += 1

    if not text:
        print("空的，不写")
        return 2

    row = {"id": uuid.uuid4().hex[:8], "ts": time.time(), "from": sender, "text": text}

    need_nl = False
    if INBOX.exists() and INBOX.stat().st_size > 0:
        with INBOX.open("rb") as f:
            f.seek(-1, os.SEEK_END)
            need_nl = f.read(1) != b"\n"

    with INBOX.open("a", encoding="utf-8", newline="\n") as f:
        if need_nl:
            f.write("\n")
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())

    print(f"已投递 id={row['id']} 字数={len(text)}")
    print(f"  {text[:60]}{'…' if len(text) > 60 else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
