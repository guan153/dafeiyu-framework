# -*- coding: utf-8 -*-
"""整理 mom_agent.py 的 import 块。"""
from pathlib import Path

p = Path(r"%PROJECT_HOME%\母女聊天室\mom_agent.py")
src = p.read_text(encoding="utf-8")
lines = src.splitlines()

# 找到 DATA = Path( 那一行，之前全是 import 区
cut = next(i for i, ln in enumerate(lines) if ln.startswith("DATA = Path("))
head = """from __future__ import annotations

import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

# 看图能力（同目录的 see_image.py）
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from see_image import describe as describe_image  # noqa: E402
except Exception:  # noqa: BLE001
    describe_image = None

"""
new = head + "\n".join(lines[cut:])
p.write_text(new, encoding="utf-8")

import ast
ast.parse(new)
print("import 块整理好了，语法 OK")
print("行数:", len(new.splitlines()))
