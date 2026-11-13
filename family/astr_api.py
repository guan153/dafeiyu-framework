# -*- coding: utf-8 -*-
"""查 AstrBot 插件状态 / 重载（用自签 JWT，不重启进程）。"""
from __future__ import annotations
import base64, hashlib, hmac, json, sys, time, urllib.error, urllib.request
from pathlib import Path

CFG = Path(r"%ASTRBOT_HOME%\data\cmd_config.json")
BASE = "http://127.0.0.1:6185"


def b64(raw: bytes) -> bytes:
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


def token() -> str:
    secret = json.loads(CFG.read_text(encoding="utf-8"))["dashboard"]["jwt_secret"]
    h = b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    p = b64(json.dumps({"username": "astrbot", "exp": int(time.time()) + 300},
                       separators=(",", ":")).encode())
    sig = b64(hmac.new(secret.encode(), h + b"." + p, hashlib.sha256).digest())
    return (h + b"." + p + b"." + sig).decode()


def call(path: str, method: str = "GET", body: dict | None = None) -> str:
    data = json.dumps(body or {}).encode() if method != "GET" else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    req.add_header("Authorization", f"Bearer {token()}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}: " + e.read().decode("utf-8", "replace")[:800]
    except Exception as e:  # noqa: BLE001
        return f"ERR: {e}"


if __name__ == "__main__":
    print(call("/api/v1/plugins"))
