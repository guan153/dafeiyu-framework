# -*- coding: utf-8 -*-
"""一家三口 · 家庭看板服务。

一个屋子，接三路：
  看板    mom_inbox.jsonl / mom_outbox.jsonl（宝宝、妈妈、她）
  QQ      SnowLuma messages.db（只读）+ OneBot HTTP（发）
  文件    data/extra.jsonl（任务、系统消息、别的 CLI 的发言）

API：
  GET  /api/data      合并后的对话流 + 状态 + 任务
  POST /api/say       说一句（文字/表情包），可选同步到 QQ
  GET  /api/memes     表情包清单
  GET  /memes/<cat>/<f>   表情包图片
  GET  /assets/<f>    头像等素材
  POST /api/tasks     建/改任务
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import unquote, urlparse

# ── 路径 ──────────────────────────────────────────────
HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
ASSETS = HERE / "assets"
EXTRA = DATA_DIR / "extra.jsonl"
TASKS = DATA_DIR / "tasks.json"
DELETED = DATA_DIR / "deleted.json"
HEARTBEAT = DATA_DIR / "mom_agent_hb.json"

MOM = Path(r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_whale_mom")
INBOX = MOM / "mom_inbox.jsonl"
OUTBOX = MOM / "mom_outbox.jsonl"

MEME_ROOT = Path(r"%ASTRBOT_HOME%\data\plugins\astrbot_plugin_dafeiyu_meme")
MEME_INDEX = MEME_ROOT / "index.json"
MEME_PACK = "deepseek-fanart-meme-pack"   # 旧的 AstrBot 包，作兜底

# 她的记忆（QQ 机器人：大肥鱼的灵魂库）
SOUL = Path(r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_dafeiyu_soul\soul.sqlite3")
L1 = Path(r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_iris_chat_memory\l1_snapshot.json")

# 我的记忆（工作区）
MY_MEMORY = DATA_DIR / "my_memory.md"

INTENT = Path(r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_whale_bili\intent_now.json")
QQ_DB = Path(r"%QBOT_HOME%\qbot\SnowLuma\data\100000001\messages.db")

# ── QQ ────────────────────────────────────────────────
ONEBOT = "http://127.0.0.1:3000"
ONEBOT_TOKEN = "REPLACE_ME"
BOSS_QQ = 100000000

_lock = threading.Lock()
_write_lock = threading.Lock()
_cache = {"inbox": (None, []), "outbox": (None, []), "qq": (0, [])}


# ── 基础读取 ──────────────────────────────────────────
def _stamp(path: Path):
    try:
        st = path.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if isinstance(row, dict):
                    out.append(row)
    except OSError:
        return []
    return out


def cached(key: str, path: Path) -> list[dict]:
    with _lock:
        st = _stamp(path)
        stamp, rows = _cache[key]
        if st != stamp:
            rows = read_jsonl(path)
            _cache[key] = (st, rows)
        return list(rows)


def read_intent() -> dict:
    try:
        return json.loads(INTENT.read_text(encoding="utf-8"))
    except Exception:
        return {}


def read_seen() -> list[str]:
    try:
        return json.loads((MOM / "mom_state.json").read_text(encoding="utf-8")).get("seen", [])
    except Exception:
        return []


# ── QQ 读写 ───────────────────────────────────────────
def qq_recent(limit: int = 40, since_ts: float = 0, ttl: float = 20.0) -> list[dict]:
    """读 QQ 私聊。20 秒缓存——库 120MB，别让页面每 2 秒扫一遍。"""
    now = time.time()
    with _lock:
        stamp, rows = _cache["qq"]
        if stamp and now - stamp < ttl:
            return list(rows)
    if not QQ_DB.exists():
        return []
    try:
        con = sqlite3.connect(f"file:{QQ_DB}?mode=ro", uri=True, timeout=8)
        cur = con.cursor()
        if since_ts:
            cur.execute(
                "SELECT timestamp, data FROM messages WHERE is_group=0 AND timestamp>? "
                "ORDER BY timestamp ASC LIMIT ?", (int(since_ts), limit))
        else:
            cur.execute(
                "SELECT timestamp, data FROM messages WHERE is_group=0 "
                "ORDER BY timestamp DESC LIMIT ?", (limit,))
        rows = cur.fetchall()
        con.close()
    except Exception:
        return []

    out = []
    for ts, raw in rows:
        try:
            d = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
        except Exception:
            continue
        segs, has_img = [], False
        for seg in (d.get("message") or []):
            if not isinstance(seg, dict):
                continue
            t = seg.get("type")
            if t == "text":
                s = str((seg.get("data") or {}).get("text") or "").strip()
                if s:
                    segs.append(s)
            elif t in ("image", "mface"):
                has_img = True
        text = " / ".join(segs)
        if has_img:
            text = (text + " [图片]").strip()
        if not text:
            continue
        sent = str(d.get("post_type")) == "message_sent"
        out.append({
            "key": f"qq-{ts}-{d.get('message_id') or d.get('random') or ''}",
            "role": "fish" if sent else "baby",
            "who": "大肥鱼" if sent else "宝宝",
            "channel": "QQ",
            "ts": float(ts),
            "text": text[:500],
            "kind": "text",
            "id": "",
            "answered": True,
        })
    with _lock:
        _cache["qq"] = (time.time(), out)
    return out


def qq_send(text: str) -> dict:
    body = json.dumps({"user_id": BOSS_QQ, "message": [{"type": "text", "data": {"text": text}}]},
                      ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(f"{ONEBOT}/send_private_msg", data=body, method="POST")
    req.add_header("Authorization", f"Bearer {ONEBOT_TOKEN}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "error": str(exc)}


# ── 合并 ──────────────────────────────────────────────
def is_mom_sender(s: str) -> bool:
    s = str(s or "")
    return s.startswith("妈妈") or s in ("妈", "鲸鱼少女")


ROOMS = {
    "family": {"name": "一家三口", "desc": "三个人都在", "icon": "🏠"},
    "fish": {"name": "大肥鱼", "desc": "只跟她说话", "icon": "🐟"},
    "mom": {"name": "鲸鱼少女", "desc": "只跟妈妈说话", "icon": "🐋"},
}


def room_of(row: dict) -> str:
    r = str(row.get("room") or "").strip()
    if r == "test":
        return "test"          # 测试房：不给人看，她俩也不回
    return r if r in ROOMS else "family"


def build_payload(limit: int = 260, room: str | None = None) -> dict:
    inbox = cached("inbox", INBOX)
    outbox = cached("outbox", OUTBOX)
    extra = read_jsonl(EXTRA)
    deleted = read_deleted()

    out_by_fish: dict[str, dict] = {}
    out_by_mom: dict[str, dict] = {}
    for row in outbox:
        rid = str(row.get("id") or "")
        if not rid:
            continue
        (out_by_mom if is_mom_sender(row.get("from")) else out_by_fish)[rid] = row

    msgs: list[dict] = []

    for row in inbox:
        rid = str(row.get("id") or "")
        sender = str(row.get("from") or "宝宝")
        msgs.append({
            "key": f"in-{rid}",
            "role": "baby" if ("宝" in sender or "主" in sender) else "mom",
            "who": sender,
            "room": room_of(row),
            "channel": str(row.get("channel") or "看板"),
            "id": rid,
            "ts": float(row.get("ts") or 0),
            "text": str(row.get("text") or ""),
            "kind": str(row.get("kind") or "text"),
            "media": row.get("media"),
            "quote": row.get("quote"),
            "answered": rid in out_by_fish,
            "answered_fish": rid in out_by_fish,
            "answered_mom": rid in out_by_mom,
        })

    for row in outbox:
        rid = str(row.get("id") or "")
        sender = str(row.get("from") or "大肥鱼")
        mom = is_mom_sender(sender)
        msgs.append({
            "key": f"out-{rid}-{row.get('ts')}",
            "role": "mom" if mom else "fish",
            "who": "妈妈" if mom else "大肥鱼",
            "room": room_of(row),
            "channel": "看板",
            "to": str(row.get("to") or "baby"),
            "id": rid,
            "ts": float(row.get("ts") or 0),
            "text": str(row.get("reply") or ""),
            "kind": str(row.get("kind") or "reply") if row.get("kind") not in ("reply", "proactive") else "text",
            "media": row.get("media"),
            "answered": True,
            "answered_fish": True,
            "answered_mom": True,
        })

    for row in extra:
        msgs.append({
            "key": f"ex-{row.get('id') or row.get('ts')}",
            "role": str(row.get("role") or "baby"),
            "who": str(row.get("who") or "宝宝"),
            "room": room_of(row),
            "channel": str(row.get("channel") or "看板"),
            "ts": float(row.get("ts") or 0),
            "text": str(row.get("text") or ""),
            "kind": str(row.get("kind") or "text"),
            "media": row.get("media"),
            "answered": True,
            "answered_fish": True,
            "answered_mom": True,
        })

    # QQ 归 QQ。他的 QQ 聊天记录不进这个屋子——
    # 要看 QQ 的，去 QQ 里看；这里只显示在本页面里说的话。
    # （她的 prompt 里仍然会带上 QQ 上下文，那是「记忆」，不是把消息摆出来）

    msgs = [m for m in msgs if room_of(m) != "test"]   # 测试消息永远不进真对话
    msgs.sort(key=lambda m: (m["ts"], 0 if m["role"] == "baby" else 1))
    if deleted:
        msgs = [m for m in msgs if str(m.get("id") or m.get("key")) not in deleted]

    # 每个房间的摘要（不受当前房间过滤影响）
    room_sum = {}
    for r in ROOMS:
        room_sum[r] = {"count": 0, "last": "", "last_ts": 0}
    for m in msgs:
        r = room_of(m)
        room_sum[r]["count"] += 1
        if m["ts"] >= room_sum[r]["last_ts"]:
            room_sum[r]["last_ts"] = m["ts"]
            room_sum[r]["last"] = (m.get("text") or "")[:40]

    if room:
        msgs = [m for m in msgs if room_of(m) == room]

    total = len(msgs)
    has_more = total > limit
    shown = msgs[-limit:] if limit > 0 else msgs

    intent = read_intent()
    st = intent.get("state") or {}
    now = time.time()

    her_last = 0.0
    for m in msgs:
        if m["role"] == "fish":
            her_last = max(her_last, m["ts"])

    mom_age = None
    try:
        hb = json.loads(HEARTBEAT.read_text(encoding="utf-8"))
        if hb.get("ts"):
            mom_age = round(now - float(hb["ts"]), 1)
    except Exception:
        pass

    return {
        "messages": shown,
        "total": total,
        "has_more": has_more,
        "counts": {
            "inbox": len(inbox),
            "outbox": len(outbox),
            "qq": 0,
        },
        "agents": {
            "mom_alive": bool(mom_age is not None and mom_age <= 60),
            "mom_age": mom_age,
            "fish_age": round(now - her_last, 1) if her_last else None,
        },
        "status": {
            "label": st.get("label"),
            "intent": intent.get("intent"),
            "why": intent.get("why"),
            "energy": st.get("energy"),
            "her_last": her_last or None,
            "vals": {k: st.get(k) for k in ("委屈", "黏人", "吃醋", "开心", "困", "好奇")
                     if st.get(k) is not None},
        },
        "rooms": [{"id": k, **v, **room_sum.get(k, {})} for k, v in ROOMS.items()],
        "tasks": read_tasks(),
        "server_ts": now,
    }


# ── 写入 ──────────────────────────────────────────────
def _append_jsonl(path: Path, row: dict) -> None:
    with _write_lock:
        need_nl = False
        if path.exists() and path.stat().st_size > 0:
            with path.open("rb") as f:
                f.seek(-1, os.SEEK_END)
                need_nl = f.read(1) != b"\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as f:
            if need_nl:
                f.write("\n")
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())


def say(text: str, sender: str = "宝宝", kind: str = "text",
        media: str | None = None, channel: str = "看板",
        to_qq: bool = False, quote: dict | None = None,
        room: str = "family") -> dict:
    text = (text or "").strip()
    if not text and not media:
        raise ValueError("空的")
    if len(text) > 600:
        raise ValueError(f"太长了（{len(text)} 字）")
    rid = uuid.uuid4().hex[:8]
    row = {
        "id": rid,
        "ts": time.time(),
        "from": sender,
        "text": text or ("[表情]" if media else ""),
        "kind": kind,
        "channel": channel,
        "room": room if (room in ROOMS or room == "test") else "family",
    }
    if media:
        row["media"] = media
    if quote and quote.get("text"):
        row["quote"] = {
            "who": str(quote.get("who") or "")[:20],
            "text": str(quote.get("text") or "")[:160],
        }
    _append_jsonl(INBOX, row)

    result = {"ok": True, "row": row}
    if to_qq and not media:
        sent = qq_send(text)
        result["qq"] = "ok" if sent.get("status") == "ok" or sent.get("retcode") == 0 else sent
    return result


# ── 任务 ──────────────────────────────────────────────
# ── 删除（墓碑，只删自己发的）────────────────────────
def read_deleted() -> set[str]:
    try:
        return set(json.loads(DELETED.read_text(encoding="utf-8")))
    except Exception:
        return set()


def add_deleted(mid: str) -> None:
    if not mid:
        return
    s = read_deleted()
    s.add(mid)
    with _write_lock:
        DELETED.parent.mkdir(parents=True, exist_ok=True)
        DELETED.write_text(json.dumps(sorted(s), ensure_ascii=False), encoding="utf-8")


def read_tasks() -> list[dict]:
    try:
        return json.loads(TASKS.read_text(encoding="utf-8"))
    except Exception:
        return []


def write_tasks(tasks: list[dict]) -> None:
    with _write_lock:
        TASKS.parent.mkdir(parents=True, exist_ok=True)
        TASKS.write_text(json.dumps(tasks[-200:], ensure_ascii=False, indent=2), encoding="utf-8")


# ── 表情包（跟她 QQ 里用的是同一套库）──────────────
PACK_NAME = {
    "dafeiyu-emoji": "大肥鱼·鲸鱼女仆",
    "dafeiyu-learned": "大肥鱼·学来的",
    "deepseek-chan": "深鲸·日常",
    "deepseek-girl-pack": "深鲸·女孩",
    "maid-emoji": "女仆",
    "pack-mvc2p5cv": "小剧场",
    "paopao-eyi": "泡泡·鹅衣",
    "paopao-short": "泡泡·短",
    "paopao-whale-girl": "泡泡·鲸鱼娘",
    "whale-girl-emoji": "鲸鱼少女",
    "wuwa-phoebe-pack": "菲比",
}


def meme_list() -> list[dict]:
    """读她表情包插件的 index.json——和她在 QQ 里用的是同一套。"""
    try:
        idx = json.loads(MEME_INDEX.read_text(encoding="utf-8"))
    except Exception:
        return []
    out = []
    for it in idx.get("items") or []:
        f = str(it.get("file") or "")
        if not f.startswith("memes/"):
            continue
        rel = f[len("memes/"):]
        pack = str(it.get("pack") or "")
        out.append({
            "pack": pack,
            "packName": PACK_NAME.get(pack, pack),
            "category": str(it.get("tag") or "daily"),
            "caption": str(it.get("caption") or ""),
            "keywords": str(it.get("keywords") or ""),
            "url": f"/memes/{rel}",
        })
    return out


# ── 记忆 ──────────────────────────────────────────────
def her_memories(limit: int = 12) -> dict:
    """她的记忆：soul.sqlite3 里的 memories + diary + 情绪。"""
    out = {"memories": [], "diary": [], "state": {}, "count": 0}
    if not SOUL.exists():
        return out
    try:
        con = sqlite3.connect(f"file:{SOUL}?mode=ro", uri=True, timeout=8)
        cur = con.cursor()
        cur.execute("SELECT COUNT(*) FROM memories")
        out["count"] = cur.fetchone()[0]
        # 最近的 + 最重要的，各取一半
        cur.execute(
            "SELECT text, importance, created, hits FROM memories "
            "ORDER BY created DESC LIMIT ?", (limit,))
        recent = cur.fetchall()
        cur.execute(
            "SELECT text, importance, created, hits FROM memories "
            "ORDER BY importance DESC, created DESC LIMIT ?", (limit,))
        top = cur.fetchall()
        seen, rows = set(), []
        for r in recent + top:
            if r[0] in seen:
                continue
            seen.add(r[0])
            rows.append(r)
        out["memories"] = [
            {"text": r[0], "importance": round(float(r[1] or 0), 2),
             "created": float(r[2] or 0), "hits": int(r[3] or 0)}
            for r in rows[:limit]
        ]
        cur.execute("SELECT day, content FROM diary ORDER BY day DESC LIMIT 2")
        out["diary"] = [{"day": d, "content": c} for d, c in cur.fetchall()]
        cur.execute("SELECT mood FROM state LIMIT 1")
        row = cur.fetchone()
        if row and row[0]:
            try:
                out["state"] = json.loads(row[0])
            except Exception:
                pass
        con.close()
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)
    return out


def my_memory() -> str:
    try:
        return MY_MEMORY.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def my_memory_append(line: str) -> None:
    try:
        MY_MEMORY.parent.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%m-%d %H:%M")
        with MY_MEMORY.open("a", encoding="utf-8") as f:
            f.write(f"- [{stamp}] {line.strip()}\n")
    except Exception:
        pass


# ── 训练 / 学习状态（给页面看的）──────────────────────
AVOID_FILE = DATA_DIR / "avoid_words.json"
CHECK_FILE = DATA_DIR / "self_check.json"
TRAIN_LOG = DATA_DIR / "train_log.jsonl"
MEME_INDEX_PATH = Path(r"%ASTRBOT_HOME%\data\plugins\astrbot_plugin_dafeiyu_meme\index.json")


def train_status() -> dict:
    avoid = {}
    try:
        avoid = json.loads(AVOID_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    check = {}
    try:
        check = json.loads(CHECK_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    log = []
    try:
        for ln in TRAIN_LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-12:]:
            ln = ln.strip()
            if ln:
                try:
                    log.append(json.loads(ln))
                except Exception:
                    pass
    except Exception:
        pass
    return {
        "avoid": avoid.get("avoid") or [],
        "avoid_counts": avoid.get("counts") or {},
        "avoid_updated": avoid.get("updated"),
        "check": {
            "time": check.get("time"),
            "passed": check.get("passed"),
            "total": check.get("total"),
            "ok": check.get("ok"),
            "failed": check.get("failed") or [],
        },
        "history": log,
        "vocab": {"total": 14450, "kid": 6864, "nsfw": 7586},
        "now": time.time(),
    }


# ── HTTP ──────────────────────────────────────────────
CTYPE = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif", ".svg": "image/svg+xml",
    ".css": "text/css; charset=utf-8", ".js": "application/javascript; charset=utf-8",
    ".html": "text/html; charset=utf-8", ".ico": "image/x-icon", ".json": "application/json",
}


def safe_child(root: Path, rel: str) -> Path | None:
    try:
        p = (root / unquote(rel)).resolve()
        if str(p).startswith(str(root.resolve())):
            return p
    except Exception:
        pass
    return None


def logline(msg: str) -> None:
    """服务日志——出问题时我这边能直接翻。超过 1MB 自动归档。"""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        log = DATA_DIR / "server.log"
        if log.exists() and log.stat().st_size > 1_000_000:
            old = DATA_DIR / "server.log.1"
            try:
                if old.exists():
                    old.unlink()
                log.rename(old)
            except Exception:
                pass
        with log.open("a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _file(self, path: Path):
        try:
            data = path.read_bytes()
        except OSError:
            self._send(404, b"not found", "text/plain")
            return
        self._send(200, data, CTYPE.get(path.suffix.lower(), "application/octet-stream"))

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = dict(kv.split("=", 1) for kv in parsed.query.split("&") if "=" in kv)

        if path in ("/", "/index.html"):
            self._file(HERE / "index.html")
            return
        if path.startswith("/assets/"):
            p = safe_child(ASSETS, path[len("/assets/"):])
            if p and p.is_file():
                self._file(p)
            else:
                self._send(404, b"no asset", "text/plain")
            return
        if path.startswith("/memes/"):
            rel = path[len("/memes/"):]
            p = safe_child(MEME_ROOT / "memes", rel)
            if p and p.is_file():
                self._file(p)
                return
            # 兜底 1：.dsh 原仓同名
            q = safe_child(Path(r"%USERPROFILE%\.dsh\meme-packs"), rel)
            if q and q.is_file():
                self._file(q)
                return
            # 兜底 2：旧表情包仓库里按文件名找（历史消息里的老路径）
            name = rel.split("/")[-1]
            for old in (
                Path(r"%ASTRBOT_HOME%\data\plugin_data\meme_manager\packs"),
                Path(r"%USERPROFILE%\.dsh\meme-packs"),
            ):
                if old.exists():
                    hit = next(old.rglob(name), None)
                    if hit and hit.is_file():
                        self._file(hit)
                        return
            self._send(404, b"no meme", "text/plain")
            return
        if path == "/api/memory":
            try:
                self._json({"her": her_memories(), "mine": my_memory()})
            except Exception as exc:  # noqa: BLE001
                logline(f"api/memory 出错：{exc!r}")
                self._json({"her": {}, "mine": ""})
            return
        if path == "/api/data":
            try:
                try:
                    lim = int(qs.get("limit") or 260)
                except Exception:
                    lim = 260
                lim = max(20, min(lim, 3000))
                room = qs.get("room") or None
                self._json(build_payload(lim, room))
            except Exception as exc:  # noqa: BLE001
                logline(f"api/data 出错：{exc!r}")
                self._json({"error": str(exc)}, 500)
            return
        if path == "/api/memes":
            self._json({"items": meme_list()})
            return
        if path == "/api/tasks":
            self._json({"items": read_tasks()})
            return
        if path == "/api/train":
            self._json(train_status())
            return
        self._send(404, b"not found", "text/plain")

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            self._json({"ok": False, "error": "bad json"}, 400)
            return

        if path == "/api/say":
            try:
                res = say(
                    text=str(payload.get("text") or ""),
                    sender=str(payload.get("from") or "宝宝"),
                    kind=str(payload.get("kind") or "text"),
                    media=payload.get("media"),
                    channel=str(payload.get("channel") or "看板"),
                    to_qq=bool(payload.get("to_qq")),
                    quote=payload.get("quote") if isinstance(payload.get("quote"), dict) else None,
                    room=str(payload.get("room") or "family"),
                )
                self._json(res)
            except Exception as exc:  # noqa: BLE001
                logline(f"api/say 出错：{exc!r}")
                self._json({"ok": False, "error": str(exc)})
            return

        if path == "/api/delete":
            try:
                mid = str(payload.get("id") or "")
                add_deleted(mid)
                self._json({"ok": True})
            except Exception as exc:  # noqa: BLE001
                logline(f"api/delete 出错：{exc!r}")
                self._json({"ok": False, "error": str(exc)})
            return

        if path == "/api/tasks":
            try:
                tasks = read_tasks()
                op = str(payload.get("op") or "add")
                if op == "add":
                    t = {
                        "id": uuid.uuid4().hex[:8],
                        "ts": time.time(),
                        "title": str(payload.get("title") or "").strip(),
                        "detail": str(payload.get("detail") or "").strip(),
                        "assignee": str(payload.get("assignee") or "both"),
                        "status": "todo",
                        "by": str(payload.get("by") or "宝宝"),
                    }
                    if t["title"]:
                        tasks.append(t)
                        # 自动派单：以「系统」身份通知，不是冒充宝宝
                        who_cn = {"mom": "鲸鱼少女", "fish": "大肥鱼", "both": "你们俩"}.get(
                            t["assignee"], "你们俩")
                        room = {"mom": "mom", "fish": "fish", "both": "family"}.get(
                            t["assignee"], "family")
                        _append_jsonl(INBOX, {
                            "id": uuid.uuid4().hex[:8],
                            "ts": time.time(),
                            "from": "系统",
                            "text": f"📋 新任务（交给{who_cn}）：{t['title']}",
                            "kind": "task",
                            "room": room,
                            "task_id": t["id"],
                        })
                elif op == "update":
                    tid = str(payload.get("id") or "")
                    for t in tasks:
                        if t.get("id") == tid:
                            for k in ("status", "assignee", "detail", "title"):
                                if k in payload:
                                    t[k] = payload[k]
                            break
                elif op == "delete":
                    tid = str(payload.get("id") or "")
                    tasks = [t for t in tasks if t.get("id") != tid]
                write_tasks(tasks)
                self._json({"ok": True, "items": tasks})
            except Exception as exc:  # noqa: BLE001
                self._json({"ok": False, "error": str(exc)})
            return

        self._send(404, b"not found", "text/plain")


class FamilyServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        import sys
        import traceback
        exc = sys.exc_info()[1]
        # 浏览器刷新/关页面导致的断连不是错误，别往日志里灌
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        logline("未捕获异常:\n" + traceback.format_exc()[-800:])


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ASSETS.mkdir(parents=True, exist_ok=True)
    srv = FamilyServer((args.host, args.port), Handler)
    srv.daemon_threads = True
    logline(f"启动 http://{args.host}:{args.port}")
    print(f"家庭看板: http://{args.host}:{args.port}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
