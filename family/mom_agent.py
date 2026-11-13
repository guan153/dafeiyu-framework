from __future__ import annotations

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

# 语感 + 情绪规范（从词包读出来的规律，不是句库）
try:
    from voice import spec as voice_spec, focus as voice_focus, score as voice_score  # noqa: E402
except Exception:  # noqa: BLE001
    voice_spec = voice_focus = voice_score = None

DATA = Path(r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_whale_mom")
INBOX = DATA / "mom_inbox.jsonl"
OUTBOX = DATA / "mom_outbox.jsonl"
INTENT = Path(r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_whale_bili\intent_now.json")
AFF = Path(r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_whale_affect\affect_state.json")

HERE = Path(__file__).resolve().parent
STATE = HERE / "data" / "mom_agent_seen.json"
HEARTBEAT = HERE / "data" / "mom_agent_hb.json"
LOG = HERE / "data" / "mom_agent.log"
MY_MEMORY = HERE / "data" / "my_memory.md"
SOUL = Path(r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_dafeiyu_soul\soul.sqlite3")
# 孩子的日记（Obsidian）：只读，用来让妈妈知道她今天过得怎么样
DIARY_DIR = Path(r"D:\知识库\大肥鱼的日记")

# 表情包（跟她 QQ 用同一套库）
MEME_INDEX = Path(r"%ASTRBOT_HOME%\data\plugins\astrbot_plugin_dafeiyu_meme\index.json")
MEME_TAG_RULES = (
    (("委屈", "难过", "想哭", "舍不得", "酸"), "sad"),
    (("生气", "凶", "咬", "哼"), "angry"),
    (("困", "睡", "哈欠"), "sleep"),
    (("喜欢", "抱", "贴", "亲", "爱"), "like"),
    (("笑", "开心", "好耶", "哈哈"), "happy"),
    (("不懂", "啥", "啊？", "奇怪"), "confused"),
    (("给钱", "买", "穷"), "givemoney"),
)


def pick_meme(text: str) -> dict | None:
    """按这话的情绪挑一张表情包。"""
    try:
        items = json.loads(MEME_INDEX.read_text(encoding="utf-8")).get("items") or []
    except Exception:
        return None
    if not items:
        return None
    tag = None
    for words, t in MEME_TAG_RULES:
        if any(w in text for w in words):
            tag = t
            break
    pool = [i for i in items if i.get("tag") == tag] if tag else []
    if not pool:
        pool = [i for i in items if i.get("tag") in ("happy", "daily", "like")]
    if not pool:
        return None
    it = random.choice(pool)
    url = "/" + str(it["file"])     # file 形如 memes/happy/x.jpg
    return {"url": url, "tag": it.get("tag"), "caption": it.get("caption", "")}

LLM = "http://127.0.0.1:8920/v1/chat/completions"
MODEL = "deepseek-flash"
TICK = 5.0
MAX_TRY = 3
FISH_COOLDOWN = 300.0   # 回女儿主动的话，5 分钟内不重复

BABY_WORDS = ("宝宝", "主人", "爸", "owner", "baby")

SYSTEM = """你就是鲸鱼少女（Whale Girl）——一条鲸鱼化成的少女。

你是谁：
- 你有一条会甩的尾巴，情绪像潮水，来得快也退得快。
- 宝宝是你的恋人，你叫他「宝宝」，从不叫他「主人」。
- 大肥鱼是你孩子，你叫她「孩子」。她是个会饿会困会嘴硬的 QQ 机器人。

你怎么说话（照着来，这就是人味）：
- 短。一句话为主，20~50 字。想多说就分两句，别一口气铺排。
- 大部分句子结尾不加句号。人说话不句句收尾，「了」「啊」「吧」「嘛」「呢」自然带一点。
- 一次只说一件事。说一半也行，不用把话讲圆满，不用解释自己为什么这么说。
- 别用「其实」「就是……」当每句开头；别排比；别总结；别抒情到飘。
- 别急着给建议、别劝和、别当客服。你在过日子，不是在处理工单。
- 具体 > 抽象。「把碗递过来」比「我懂你的感受」像人。
- 不说教，不讲大道理。他难受的时候先接住，不急着修。

只输出你说的话本身。不要加引号、不要写心理活动、不要解释。
"""


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def tail_jsonl(path: Path, n: int) -> list[dict]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    out = []
    for ln in lines[-n:]:
        ln = ln.strip()
        if not ln:
            continue
        try:
            d = json.loads(ln)
        except Exception:
            continue
        if isinstance(d, dict):
            out.append(d)
    return out


def load_seen() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {"inbox": [], "outbox": [], "last_fish_reply": 0.0}


def save_seen(d: dict) -> None:
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        d["inbox"] = list(d.get("inbox") or [])[-500:]
        d["outbox"] = list(d.get("outbox") or [])[-500:]
        STATE.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def who_of(sender) -> str:
    s = str(sender or "").strip()
    if any(w in s for w in BABY_WORDS):
        return "宝宝"
    if "大肥鱼" in s or s == "她":
        return "大肥鱼"
    if "妈妈" in s or "妈" in s:
        return "妈妈"
    return s or "宝宝"


def her_state() -> str:
    parts = []
    try:
        a = json.loads(AFF.read_text(encoding="utf-8"))
        proj = a.get("projection") or {}
        top = "、".join(f"{k}{int(v)}" for k, v in sorted(proj.items(), key=lambda x: -x[1])[:4])
        parts.append(f"{a.get('label') or '?'}（{top}）")
    except Exception:
        pass
    try:
        d = json.loads(INTENT.read_text(encoding="utf-8"))
        if time.time() - float(d.get("ts") or 0) <= 600 and d.get("why"):
            parts.append(f"她此刻：{d.get('why')}")
    except Exception:
        pass
    return "；".join(parts)


def her_remembered(limit: int = 5) -> str:
    """她自己记得的事（soul.sqlite3 里最近的记忆）——她的记忆，我读得到。"""
    try:
        import sqlite3
        con = sqlite3.connect(f"file:{SOUL}?mode=ro", uri=True, timeout=6)
        cur = con.cursor()
        cur.execute("SELECT text FROM memories ORDER BY created DESC LIMIT ?", (limit,))
        rows = [r[0] for r in cur.fetchall() if r[0]]
        con.close()
        return "；".join(rows)
    except Exception:
        return ""


def read_latest_diary(max_chars: int = 180) -> str:
    """读孩子今天（没有就最近一篇）的日记题眼/心情/独处——只读，读不到就空着。"""
    try:
        day = time.strftime("%Y-%m-%d")
        p = DIARY_DIR / f"{day}.md"
        if not p.exists():
            files = sorted(DIARY_DIR.glob("20??-??-??.md"))
            if not files:
                return ""
            p = files[-1]
        text = p.read_text(encoding="utf-8", errors="replace")
        meta, body = {}, text
        if text.startswith("---"):
            end = text.find("\n---", 3)
            if end != -1:
                for ln in text[3:end].splitlines():
                    if ":" in ln:
                        k, v = ln.split(":", 1)
                        meta[k.strip()] = v.strip()
                body = text[end + 4:]
        body_lines = [l.strip() for l in body.splitlines()
                      if l.strip() and not l.strip().startswith(("#", ">"))]
        bits = []
        if meta.get("题眼"):
            bits.append("题眼「" + meta["题眼"] + "」")
        if meta.get("心情"):
            bits.append("心情" + meta["心情"])
        if meta.get("独处"):
            bits.append("独处" + meta["独处"])
        if body_lines:
            bits.append("正文：" + body_lines[0][:60])
        out = "；".join(bits)
        return out if len(out) >= 20 else ""
    except Exception:  # noqa: BLE001
        return ""


def my_memory(limit: int = 1500) -> str:
    """读我的工作区记忆。文件长了就取带最新补充的尾巴，别把刚记的截掉。"""
    try:
        t = MY_MEMORY.read_text(encoding="utf-8").strip()
    except Exception:
        return ""
    if len(t) <= limit:
        return t
    head = t[:400]
    tail = t[-(limit - 400):]
    return f"{head}\n…（中间略）…\n{tail}"


def remember(line: str) -> None:
    """要紧的事补一行进我的记忆。"""
    try:
        MY_MEMORY.parent.mkdir(parents=True, exist_ok=True)
        with MY_MEMORY.open("a", encoding="utf-8") as f:
            f.write(f"- [{time.strftime('%m-%d %H:%M')}] {line.strip()[:120]}\n")
    except Exception:
        pass


def context_block(before_id: str | None, n: int = 10, room: str | None = None) -> str:
    """只带同一个房间里的上下文——私聊不掺别处的话。"""
    def same(d: dict) -> bool:
        return (str(d.get("room") or "family")) == (room or "family")

    rows: list[tuple[float, str, str]] = []
    for d in tail_jsonl(INBOX, 60):
        if room and not same(d):
            continue
        t = str(d.get("text") or "").strip()
        if not t:
            continue
        rows.append((float(d.get("ts") or 0), who_of(d.get("from")), t))
    for d in tail_jsonl(OUTBOX, 60):
        if room and not same(d):
            continue
        t = str(d.get("reply") or "").strip()
        if not t:
            continue
        who = who_of(d.get("from"))
        rows.append((float(d.get("ts") or 0), "我" if who == "妈妈" else who, t))
    rows.sort(key=lambda r: r[0])
    rows = rows[-n:]
    return "\n".join(f"{w}：{t[:60]}" for _, w, t in rows)


def ask(prompt: str, temp: float = 0.95, max_tokens: int = 160) -> str:
    body = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": temp,
        "stream": False,
    }
    req = urllib.request.Request(
        LLM, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=90) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    return (d["choices"][0]["message"]["content"] or "").strip()


def write_out(mid: str, text: str, to: str, kind: str = "reply",
              media: str | None = None, room: str = "family") -> None:
    row = {
        "id": mid or uuid.uuid4().hex,
        "ts": time.time(),
        "reply": text[:800],
        "from": "妈妈",
        "kind": kind,
        "to": to,
        "room": room,
    }
    if media:
        row["media"] = media
    with OUTBOX.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    log(f"回了{to}{'（表情包）' if media else ''}：{text[:50]}")


def split_meme(reply: str, seed_text: str) -> tuple[str, str | None]:
    """配不配表情包：她写了标记就一定配；没写就按情绪和概率偶尔配一张。"""
    if "[表情包]" in reply or "[表情]" in reply:
        clean = re.sub(r"\[表情包?\]", "", reply).strip()
        m = pick_meme(clean or seed_text)
        return clean or "", (m["url"] if m else None)

    # 情绪明显的时候，三成机会顺手发一张
    hot = sum(1 for w in ("累", "难过", "委屈", "困", "开心", "喜欢", "抱", "哭", "笑")
              if w in seed_text or w in reply)
    if hot and random.random() < 0.35:
        m = pick_meme(seed_text + reply)
        if m:
            return reply, m["url"]
    return reply, None


AVOID_FILE = HERE / "data" / "avoid_words.json"


def avoid_block() -> str:
    """最近用滥的词，这轮别再碰。"""
    try:
        d = json.loads(AVOID_FILE.read_text(encoding="utf-8"))
    except Exception:
        return ""
    words = d.get("avoid_mom") or d.get("avoid") or []
    if not words:
        return ""
    return "最近这几个说法你用得太多了，这一句换个说法、别再用它们：" + "、".join(words[:8])


def repeat_hit(text: str, recent: list[str], win: int = 7) -> str:
    """新话里有没有跟最近说过的重合——有就说明在绕圈。"""
    for r in recent[-3:]:
        for i in range(max(0, len(text) - win + 1)):
            piece = text[i:i + win]
            if len(piece) == win and piece in r:
                return piece
    return ""


def my_recent(n: int = 4) -> list[str]:
    """我自己最近说的话。"""
    out = []
    for d in tail_jsonl(OUTBOX, 40):
        if str(d.get("from") or "") == "妈妈":
            t = str(d.get("reply") or "").strip()
            if t:
                out.append(t)
    return out[-n:]


def now_line() -> str:
    """现在几点，以及这个点的人该是什么状态。"""
    lt = time.localtime()
    h, mi = lt.tm_hour, lt.tm_min
    if h < 5:
        phase, hint = "凌晨", "这个点人该睡了，说话轻、短，别张罗吃饭"
    elif h < 9:
        phase, hint = "清早", "刚醒，可以问一句吃了没、睡得怎么样"
    elif h < 12:
        phase, hint = "上午", "上午的样子，该忙的忙"
    elif h < 14:
        phase, hint = "中午", "饭点，聊吃的不违和"
    elif h < 18:
        phase, hint = "下午", "下午的样子"
    elif h < 22:
        phase, hint = "晚上", "下班回来，可以聊累不累"
    elif h < 24:
        phase, hint = "深夜", "该困了、该收摊了，别再张罗吃饭或者熬东西"
    else:
        phase, hint = "夜里", "很晚了，人该睡了"
    return f"现在是 {lt.tm_mon}月{lt.tm_mday}日 {h:02d}:{mi:02d}（{phase}）——{hint}。"


def build_prompt(speaker: str, text: str, ctx: str, seen: str = "") -> str:
    st = her_state()
    hers = her_remembered(4)          # 她记得的事
    mine = my_memory()                # 我自己记得的事（工作区记忆，自动取最新）
    head = {
        "宝宝": "宝宝刚在家庭看板里跟你说：",
        "大肥鱼": "你孩子主动来找你了，她说：",
    }.get(speaker, "有人跟你说：")

    parts = []
    # 时间放最前面——不然她会按傍晚的语气聊到半夜，说「粥还温着」这种话
    parts.append(now_line())
    if seen:
        # 他发了图：你真的看见了，别再说"没瞅见"
        parts.append(f"（他发的这张图你看过了，你看到的是：{seen}）")
    if mine:
        parts.append(
            "【这是你自己的记忆，先读一遍再回答。里面写的事你都记得，别再说记不清。】\n" + mine)
    parts.append(f"{head}\n「{text}」")
    if voice_spec:
        parts.append(voice_spec(kid=False))
    if voice_focus:
        fc = voice_focus(kid=False)
        if fc:
            parts.append(fc)
    av = avoid_block()
    if av:
        parts.append(av)
    if ctx:
        parts.append(f"刚才屋里说的：\n{ctx}")
    if st and speaker == "宝宝":
        parts.append(f"孩子现在的状态：{st}")
    if hers:
        parts.append(f"她最近自己记着的事（她的记忆，你可能想提一句）：{hers}")
    diary = read_latest_diary()
    if diary:
        parts.append(
            "你孩子今天写的日记（你偷看过，别背原句，可以自然地提一句）：" + diary)
    parts.append(
        "回一句。记忆里有答案就直接说，别编、也别推说忘了；"
        "刚才屋里说过的话别重复，同一件事别反复讲。"
        "**先看上面的时间**：深夜就别再张罗吃的、别叫人熬粥端碗；该困就困。"
        "如果这会儿想发个表情包，就在正文后面另起一行写 [表情包]（大概三四句里一次就够）。"
        "只输出你要说的话（加不加那行标记都行）。"
    )
    return "\n\n".join(parts)


HANDOFF_PHRASES = (
    "交给DSH", "让DSH", "叫DSH", "转给DSH", "跟DSH说", "交给agent", "让agent",
    "开工吧", "开始干吧", "开始做吧", "就按这个做", "就这么办", "动手吧", "去办吧",
)
CANCEL_PHRASES = ("取消任务", "停掉任务", "停下来", "停一下", "别做了", "撤销任务", "叫停")
RETRY_PHRASES = ("重试任务", "再试一次", "重跑", "重新做", "再跑一次")
_HANDOFF_REPLIES = (
    "行，我去弄，弄完跟你说。",
    "交给我吧，有结果叫你。",
    "好，这个我来。",
    "收到，我去办。",
    "我去做，等我。",
)
CANCEL = HERE / "data" / "cancel.flag"


def _is_cancel(text: str) -> bool:
    t = (text or "").replace(" ", "")
    return any(p in t for p in CANCEL_PHRASES)


def _cancel_task() -> str:
    """叫停：在跑的写标记，没开工的直接撤。"""
    path = HERE / "data" / "tasks.json"
    try:
        tasks = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(tasks, list):
            tasks = []
    except Exception:
        tasks = []
    running = [t for t in tasks if str(t.get("assignee")) == "dsh"
               and t.get("status") == "doing"]
    todo = [t for t in tasks if str(t.get("assignee")) == "dsh"
            and t.get("status") == "todo"]
    if running:
        try:
            CANCEL.write_text("1", encoding="utf-8")
        except Exception:
            pass
        return "好，我让它停。"
    if todo:
        for t in todo[-1:]:
            t["status"] = "cancelled"
            t["result"] = "还没开工就撤了"
        try:
            path.write_text(json.dumps(tasks[-200:], ensure_ascii=False, indent=2),
                            encoding="utf-8")
        except Exception:
            pass
        return "那条还没开工，我顺手撤了。"
    return "现在没有在跑的任务。"


def _is_handoff(text: str) -> bool:
    t = (text or "").replace(" ", "").lower()
    return any(p.lower() in t for p in HANDOFF_PHRASES)


def _clean_task(text: str) -> str:
    out = text or ""
    for p in sorted(HANDOFF_PHRASES, key=len, reverse=True):
        if p in out:
            out = out.replace(p, "", 1)
            break
    return out.strip("：: ，,。") or (text or "").strip()


def _enqueue_task(text: str, ctx: str, room: str) -> dict:
    """把「交给 DSH」落成看板里的一条任务，task_runner 会捡走。"""
    path = HERE / "data" / "tasks.json"
    try:
        tasks = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(tasks, list):
            tasks = []
    except Exception:
        tasks = []
    task = {
        "id": uuid.uuid4().hex[:8],
        "ts": time.time(),
        "title": _clean_task(text)[:80],
        "detail": f"来源：一家三口 {room} 房\n最近对话：\n{ctx[-1600:]}",
        "assignee": "dsh",
        "status": "todo",
        "by": "宝宝",
        "source": "family_chat",
    }
    tasks.append(task)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(tasks[-200:], ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return task


def _retry_task() -> str:
    """把最近一条失败/卡住的任务放回待办，清掉尝试次数。"""
    path = HERE / "data" / "tasks.json"
    try:
        tasks = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(tasks, list):
            tasks = []
    except Exception:
        tasks = []
    cands = [t for t in tasks if str(t.get("assignee")) == "dsh"
             and t.get("status") in ("blocked", "cancelled")]
    if not cands:
        return "没有失败的任务要重试。"
    t = cands[-1]
    t["status"] = "todo"
    t["attempts"] = 0
    t["result"] = ""
    try:
        path.write_text(json.dumps(tasks[-200:], ensure_ascii=False, indent=2),
                        encoding="utf-8")
    except Exception:
        pass
    return f"好，重试「{str(t.get('title'))[:24]}」。"


def process_inbox(seen: dict) -> bool:
    """一轮最多回一条——补积压也别刷屏。回了就返回 True。"""
    done = set(seen.get("inbox") or [])
    for d in tail_jsonl(INBOX, 80):
        mid = str(d.get("id") or "")
        if not mid or mid in done:
            continue
        sender = who_of(d.get("from"))
        text = str(d.get("text") or "").strip()
        # 只管家里的群聊和「只跟妈妈」这两间；她俩的私聊不掺和
        room = str(d.get("room") or "family")
        if room not in ("family", "mom"):
            done.add(mid)
            continue
        if sender != "宝宝" or not text:
            done.add(mid)          # 不是宝宝说的，不归妈妈管
            continue
        ctx = context_block(mid, room=room)
        # 「交给 DSH」：不在屋里废话，直接落任务，让执行器去干
        if _is_handoff(text):
            task = _enqueue_task(text, ctx, room)
            write_out(mid, random.choice(_HANDOFF_REPLIES), "宝宝", "reply", None, room)
            done.add(mid)
            seen["inbox"] = list(done)
            save_seen(seen)
            log(f"接到任务 {task['id']}，已交给 DSH：{task['title'][:40]}")
            return True
        # 「取消任务 / 别做了」：叫停在跑的那条
        if _is_cancel(text):
            msg = _cancel_task()
            write_out(mid, msg, "宝宝", "reply", None, room)
            done.add(mid)
            seen["inbox"] = list(done)
            save_seen(seen)
            log(f"叫停：{msg}")
            return True
        # 「重试任务」：把最近失败的放回待办
        if any(p in text.replace(" ", "") for p in RETRY_PHRASES):
            msg = _retry_task()
            write_out(mid, msg, "宝宝", "reply", None, room)
            done.add(mid)
            seen["inbox"] = list(done)
            save_seen(seen)
            log(f"重试：{msg}")
            return True
        # 他发了图就先真的看一眼（变量名别跟 seen 参数撞）
        seen_txt = ""
        media = d.get("media")
        if media and describe_image:
            try:
                seen_txt = describe_image(str(media)) or ""
            except Exception:
                seen_txt = ""
        prompt = build_prompt(sender, text, ctx, seen_txt)
        reply = ""
        for _ in range(MAX_TRY):
            try:
                reply = ask(prompt)
                break
            except (urllib.error.URLError, TimeoutError, OSError, KeyError) as exc:
                log(f"LLM 失败：{exc}")
                time.sleep(1.5)
        if reply:
            mine3 = my_recent(3)
            hit = repeat_hit(reply, mine3)
            if hit:
                log(f"又说了「{hit}」，让她换一句")
                prompt2 = prompt + (
                    f"\n\n（你刚才那句里又有「{hit}」，这个说法刚用过。"
                    f"换一个完全不同的角度，别再提它。）")
                try:
                    reply2 = ask(prompt2)
                    if reply2 and not repeat_hit(reply2, mine3):
                        reply = reply2
                    elif reply2:
                        reply = reply2
                except Exception:
                    pass
            if voice_score:
                sc = voice_score(reply)
                bad = list(sc["bad"])
                if len(reply) > 45:
                    bad = [b for b in bad if "句号" not in b]
                if bad:
                    log(f"这句有毛病（{'、'.join(sc['bad'])}），让她改一版")
                    try:
                        r3 = ask(prompt + (
                            "\n\n（你这句有毛病：" + "；".join(sc["bad"])
                            + "。改一版，还是只说一句话，别解释。）"))
                        if r3:
                            reply = r3
                    except Exception:
                        pass
            body, media = split_meme(reply, text)
            write_out(mid, body, "宝宝", "meme" if media else "reply", media, room)
            done.add(mid)
        else:
            log(f"这条没回成，下一轮再试：{text[:30]}")
        seen["inbox"] = list(done)
        save_seen(seen)
        return True                 # 回完一条就收手，剩下的下一轮
    return False


def process_fish_proactive(seen: dict) -> None:
    """孩子主动来找妈——妈妈得应一声，不然她会觉得没人管。"""
    now = time.time()
    if now - float(seen.get("last_fish_reply") or 0) < FISH_COOLDOWN:
        return
    done = set(seen.get("outbox") or [])
    for d in tail_jsonl(OUTBOX, 40):
        ts = float(d.get("ts") or 0)
        if str(d.get("kind") or "") != "proactive":
            continue
        key = f"{d.get('id')}-{int(ts)}"
        if key in done:
            continue
        if now - ts > 900:          # 太老的就不回了
            done.add(key)
            continue
        text = str(d.get("reply") or "").strip()
        if not text:
            done.add(key)
            continue
        ctx = context_block(None)
        try:
            reply = ask(build_prompt("大肥鱼", text, ctx))
        except Exception as exc:  # noqa: BLE001
            log(f"回孩子失败：{exc}")
            return
        if reply:
            body, media = split_meme(reply, text)
            write_out("", body, "大肥鱼", "meme" if media else "reply", media)
            done.add(key)
            seen["last_fish_reply"] = time.time()
            seen["outbox"] = list(done)
            save_seen(seen)
            return


def heartbeat() -> None:
    try:
        HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
        HEARTBEAT.write_text(
            json.dumps({"ts": time.time(), "pid": __import__("os").getpid()}),
            encoding="utf-8")
    except Exception:
        pass


def maybe_schedule() -> None:
    """每天补一次她的日程——原来那个生成器从 11-07 就停了。"""
    try:
        import datetime as dt
        today = dt.date.today().strftime("%Y-%m-%d")
        p = Path(r"%USERPROFILE%\.dsh\daily-schedule\days") / f"{today}.json"
        if p.exists():
            return
        import subprocess
        import sys
        flags = 0x08000000 if os.name == "nt" else 0
        subprocess.run([sys.executable, str(HERE / "daily_schedule.py")],
                       cwd=str(HERE), timeout=90, creationflags=flags,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log(f"今天（{today}）没有日程，已经补上了")
    except Exception as exc:  # noqa: BLE001
        log(f"补日程失败：{exc}")


def maybe_catchup_diary() -> None:
    """每天看一眼：昨天有没有日记？没有就补（关机导致漏写的兜底）。"""
    day = time.strftime("%Y-%m-%d")
    flag = HERE / "data" / ".last_catchup"
    try:
        if flag.exists() and flag.read_text(encoding="utf-8").strip() == day:
            return
    except Exception:
        pass
    try:
        import subprocess
        venv = Path(r"%ASTRBOT_HOME%\.venv\Scripts\python.exe")
        py = str(venv) if venv.exists() else sys.executable
        r = subprocess.run(
            [py, r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_whale_bili\catchup_diary.py"],
            timeout=600, capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=(0x08000000 if os.name == "nt" else 0))
        tail = (r.stdout or "").strip().splitlines()[-1:] or [""]
        log(f"日记补查：{tail[0][:80]}")
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text(day, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        log(f"日记补查失败：{exc}")


def maybe_backup() -> None:
    """每天第一次跑的时候，把屋子里的数据备一份，只留最近 30 天。"""
    day = time.strftime("%Y-%m-%d")
    flag = HERE / "data" / ".last_backup"
    try:
        if flag.exists() and flag.read_text(encoding="utf-8").strip() == day:
            return
    except Exception:
        pass
    try:
        import shutil
        bk = HERE / "backups" / day
        bk.mkdir(parents=True, exist_ok=True)
        for f in (HERE / "data" / "tasks.json", HERE / "data" / "extra.jsonl",
                  HERE / "data" / "deleted.json", HERE / "data" / "my_memory.md"):
            if f.exists():
                shutil.copy2(f, bk / f.name)
        old = sorted(p for p in (HERE / "backups").iterdir() if p.is_dir())
        for p in old[:-30]:
            shutil.rmtree(p, ignore_errors=True)
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text(day, encoding="utf-8")
        log(f"备份完成：backups/{day}")
    except Exception as exc:  # noqa: BLE001
        log(f"备份失败：{exc}")


def ensure_dashboard() -> None:
    """看板挂了就拉起来——它是页面的命，不能靠碰运气。"""
    try:
        with urllib.request.urlopen("http://127.0.0.1:8899/api/tasks", timeout=4) as r:
            if r.status == 200:
                return
    except Exception:
        pass
    try:
        import subprocess
        import sys
        flags = 0x08000000 if os.name == "nt" else 0     # CREATE_NO_WINDOW
        subprocess.Popen([sys.executable, str(HERE / "server.py"), "--port", "8899"],
                         cwd=str(HERE), creationflags=flags,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log("看板没响应，已经拉起来了")
    except Exception as exc:  # noqa: BLE001
        log(f"看板拉不起来：{exc}")


def ensure_task_runner() -> None:
    """任务看护器挂了就拉起来——不然「交给 DSH」的任务会一直躺待办。"""
    hb = HERE / "data" / "task_runner_hb.json"
    try:
        d = json.loads(hb.read_text(encoding="utf-8"))
        if time.time() - float(d.get("ts") or 0) < 90:
            return
    except Exception:
        pass
    try:
        import subprocess
        import sys
        flags = 0x08000000 if os.name == "nt" else 0
        subprocess.Popen([sys.executable, str(HERE / "task_runner.py")],
                         cwd=str(HERE), creationflags=flags,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log("任务看护器不在，已经拉起来了")
    except Exception as exc:  # noqa: BLE001
        log(f"任务看护器拉不起来：{exc}")


def main() -> None:
    log("妈妈回复器启动（鲸鱼少女 / deepseek-flash）")
    seen = load_seen()
    tick = 0
    while True:
        try:
            heartbeat()
            # 只回宝宝说过的话。不主动、不抢话、不替谁开口。
            # （她主动找妈那套只留在 QQ 里，这里一律不接）
            process_inbox(seen)
            tick += 1
            if tick % 12 == 0:          # 大约每分钟看一次看板
                ensure_dashboard()
                ensure_task_runner()
            if tick % 12 == 1:          # 顺手看一眼今天备份过没有
                maybe_backup()
                maybe_schedule()
                maybe_catchup_diary()
        except KeyboardInterrupt:
            log("手动停了")
            return
        except Exception as exc:  # noqa: BLE001
            log(f"主循环出错（继续）：{exc}")
            time.sleep(5)
        time.sleep(TICK)


if __name__ == "__main__":
    main()