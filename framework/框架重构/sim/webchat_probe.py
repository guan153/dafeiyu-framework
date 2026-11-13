"""webchat 自测：通过 AstrBot 内置 webchat API 发消息，触发她的完整链路。

用途：
1) 验证新内核 live 注入真的在回复里生效；
2) 攒够 12 轮，触发第 4 层元认知审核。
用法：python webchat_probe.py --rounds 3
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import time
import urllib.request
from pathlib import Path

CFG = Path(r"%ASTRBOT_HOME%\data\cmd_config.json")
BASE = "http://127.0.0.1:6185"
BEAMS_STATE = Path(r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_whale_beams\beams_state.json")

_cfg = json.loads(CFG.read_text(encoding="utf-8-sig"))
_dash = _cfg.get("dashboard") or {}
SECRET = _dash.get("jwt_secret") or ""
USER = _dash.get("username") or "astrbot"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def jwt() -> str:
    now = int(time.time())
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64(json.dumps({"username": USER, "exp": now + 86400, "iat": now},
                              separators=(",", ":")).encode())
    sig = _b64(hmac.new(SECRET.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest())
    return f"{header}.{payload}.{sig}"


HEADERS = {"Authorization": "Bearer " + jwt(), "Content-Type": "application/json"}


def http(method, url, body=None, timeout=180):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")


def new_session():
    st, body = http("GET", f"{BASE}/api/chat/new_session?platform_id=webchat")
    if st != 200:
        raise RuntimeError("new_session HTTP %s: %s" % (st, body[:200]))
    return json.loads(body)["data"]["session_id"]


def beams_state():
    try:
        return json.loads(BEAMS_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=3)
    args = ap.parse_args()

    sid = new_session()
    print("webchat session:", sid)
    before = beams_state()
    print("before: tick=%s last_event=%s audit=%s" % (
        before.get("tick_count"), (before.get("fast") or {}).get("last_event"),
        (before.get("meta") or {}).get("audit", {}).get("history") and "has" or "empty"))

    for i in range(args.rounds):
        msg = "在吗" if i == 0 else ("今天有点累" if i == 1 else "陪我聊会儿")
        t0 = time.time()
        st, body = http("POST", f"{BASE}/api/chat/send",
                        {"session_id": sid, "message": msg}, timeout=240)
        print("[%d] 发送 %r → HTTP %s（%.1fs）" % (i + 1, msg, st, time.time() - t0))
        time.sleep(1.5)

    after = beams_state()
    print("\nafter: tick=%s last_event=%s" % (
        after.get("tick_count"), (after.get("fast") or {}).get("last_event")))
    audit = (after.get("meta") or {}).get("audit") or {}
    print("audit history=%d last_verdict=%s" % (len(audit.get("history") or []), audit.get("last_verdict")))
    print("narrative:", [(x or {}).get("text") for x in ((after.get("meta") or {}).get("narrative") or [])][-3:])


if __name__ == "__main__":
    main()