# -*- coding: utf-8 -*-
"""热重载 AstrBot 插件（不重启进程、不断她跟主人的 QQ 连接）。

用法：
    python astr_reload.py astrbot_plugin_whale_mom
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

CFG = Path(r"%ASTRBOT_HOME%\data\cmd_config.json")
BASE = "http://127.0.0.1:6185"


def b64(raw: bytes) -> bytes:
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


def make_token(secret: str, username: str = "astrbot", ttl: int = 300) -> str:
    header = b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = b64(json.dumps(
        {"username": username, "exp": int(time.time()) + ttl}, separators=(",", ":")).encode())
    signing = header + b"." + payload
    sig = b64(hmac.new(secret.encode(), signing, hashlib.sha256).digest())
    return (signing + b"." + sig).decode()


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python astr_reload.py <plugin_id>")
        return 2
    plugin_id = sys.argv[1]

    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    secret = cfg["dashboard"]["jwt_secret"]
    token = make_token(secret)

    url = f"{BASE}/api/v1/plugins/{plugin_id}/reload"
    req = urllib.request.Request(url, method="POST", data=b"{}")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read().decode("utf-8", "replace")
            print(f"HTTP {r.status}")
            print(body[:1500])
            return 0
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}")
        print(e.read().decode("utf-8", "replace")[:1500])
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"失败: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
