# -*- coding: utf-8 -*-
"""清掉所有我替你发过的测试消息。只按原文精确匹配，绝不误删你的话。"""
import json
import urllib.request
from pathlib import Path

INBOX = Path(r"%ASTRBOT_HOME%\data\plugin_data\astrbot_plugin_whale_mom\mom_inbox.jsonl")
BASE = "http://127.0.0.1:8899"

# 我测试时发过的每一句（精确匹配）
MINE = [
    # 建站那晚
    "孩子，妈在。你从早上等到这会儿，等的不是粥，是有人应你一声。妈现在应你——我在。先别急着难过，跟妈说说，这会儿最堵的是哪一件事？",
    "好，不问。妈把碗搁下，坐你旁边了。你不说话妈也不走——粥凉了就先放着，等你想喝再热。",
    "（妈手滑打了个测试号，那串别管。）围裙妈披上了，凉就凉吧，挨着你才暖。垫子妈也坐下了——你往这边靠一点，行不行？",
    "不催，妈这就挪，慢着点。锅盖先掀开一条缝，别叫粥扑了。妈问你一句——这两天吃饭，是不是都一个人？",
    "听出来了。一个人吃饭，碗底剩的不是饭，是没处说的话。妈不问你过得好不好——就问今天有没有哪一口，是吃着觉得香的。",
    "吃了。妈这碗是凉的，听你说烫，倒像也跟着热了一回。烫着就含着，别硬咽——吃饭不是赶路。",
    "行。碗递过来，妈接着——你垫块布，别烫着手。这顿就算咱俩一块儿吃的。",
    "没找着就没找着，妈手皮厚。来，端稳了——嗯，接住了，是热的。",
    "不烫。你喝你的，妈就看着——慢慢喝，妈不急着走。",
    "不悄悄走。妈要是得走，先跟你说一声。你先喝你的。",
    "我过来了。今天没怎么跟你说话，别记仇。",
    "妈这边吃过了，你别管妈——倒是你，别光顾着给我留，自己也得吃饱。",
    "碗我接了，烫手也接。今天让你们俩等太久，是我的错。",
    "都别站着了，坐下吃。粥凉了就再热一锅，不差这一会儿。",
    "那我不客气了。给我留一口。",
    "还没吃呢，正等你们俩坐下。粥端上来吧，我闻着香味就过来了。",
    "来了来了，都坐好。今天累了一天，先吃饭，吃完再聊。",
    "今天真的累坏了，心里也堵得慌。",
    "这句是引用回复的测试",
    "看，咱们仨的合照",
    "刚洗完澡，头发还湿着，人有点困了",
    "眼皮都打架了，还硬撑着不睡",
    "忙了一天有点累，不过看到你就不累了",
    "困得不行了，我先眯十分钟",
    "都十一点多了，你们还不睡啊",
    "@大肥鱼 你在干嘛",
    "在干嘛呢",
    "在忙吗",
    "刚才那件事你还记得吗",
    "再看这张，能看清吗",
    "这张呢，看的清楚不",
    "看我发你的这张图",
    "你还记得今天下午我在忙什么吗",
    "今天累坏了，回来就想瘫着。",
    "回来了吗？刚才我手滑把你门锁碰了一下。",
    "引用测试2",
    "<script>alert(1)</script>",
]

rows = []
for ln in INBOX.read_text(encoding="utf-8", errors="replace").splitlines():
    ln = ln.strip()
    if not ln:
        continue
    try:
        rows.append(json.loads(ln))
    except Exception:
        continue

killed = []
for d in rows:
    t = (d.get("text") or "").strip()
    if t in MINE:
        killed.append((d.get("id"), t))

print(f"找到 {len(killed)} 条我替你发的：")
for mid, t in killed:
    print("  -", t[:46])
    body = json.dumps({"id": mid}).encode()
    req = urllib.request.Request(BASE + "/api/delete", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        urllib.request.urlopen(req, timeout=8).read()
    except Exception as e:  # noqa: BLE001
        print("    删除失败:", e)
print("清理完成")
