# -*- coding: utf-8 -*-
"""给看板做一轮边界和异常测试，看还有哪里会炸。"""
import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8899"
issues = []
oks = []


def call(path, body=None, method="POST"):
    data = json.dumps(body or {}, ensure_ascii=False).encode("utf-8") if method != "GET" else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return 0, str(e)


def get(path):
    try:
        with urllib.request.urlopen(BASE + path, timeout=20) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception as e:  # noqa: BLE001
        return 0, str(e).encode()


# 1. 空消息
c, b = call("/api/say", {"text": "", "from": "宝宝"})
(oks if not json.loads(b).get("ok") else issues).append(f"空消息被拒（{c}）：{b[:60]}")

# 2. 超长消息
c, b = call("/api/say", {"text": "啊" * 700, "from": "宝宝"})
(oks if not json.loads(b).get("ok") else issues).append(f"超长被拒：{b[:60]}")

# 3. XSS 转义
c, b = call("/api/say", {"text": "<script>alert(1)</script>", "from": "宝宝"})
if json.loads(b).get("ok"):
    rid = json.loads(b)["row"]["id"]
    code, page = get("/api/data")
    j = json.loads(page)
    hit = [m for m in j["messages"] if m.get("id") == rid]
    if hit and "<script>" in hit[0]["text"]:
        oks.append("XSS 原文保留在数据里（前端 esc 转义）")
    ok = json.loads(b).get("ok")
    call("/api/delete", {"id": rid})
else:
    issues.append(f"XSS 消息没发出去：{b[:60]}")

# 4. 删不存在的 id
c, b = call("/api/delete", {"id": "不存在的id"})
(oks if c == 200 else issues).append(f"删不存在的 id：{c} {b[:40]}")

# 5. 路径穿越
c, b = get("/memes/../../../../Windows/win.ini")
(oks if c in (400, 403, 404) else issues).append(f"路径穿越 /memes/../../：返回 {c}")

c, b = get("/assets/../server.py")
(oks if c in (400, 403, 404) else issues).append(f"路径穿越 /assets/../：返回 {c}")

# 6. 非法 limit
c, b = get("/api/data?limit=abc")
(oks if c == 200 else issues).append(f"limit=abc：{c}")

c, b = get("/api/data?limit=-5")
(oks if c == 200 else issues).append(f"limit=-5：{c}")

c, b = get("/api/data?limit=999999")
(oks if c == 200 else issues).append(f"limit=999999：{c}")

# 7. 坏 JSON
req = urllib.request.Request(BASE + "/api/say", data=b"not json", method="POST")
req.add_header("Content-Type", "application/json")
try:
    with urllib.request.urlopen(req, timeout=10) as r:
        c = r.status
except urllib.error.HTTPError as e:
    c = e.code
(oks if c == 400 else issues).append(f"坏 JSON：返回 {c}")

# 8. 任务边界
c, b = call("/api/tasks", {"op": "add", "title": ""})
j = json.loads(b)
(oks if len([t for t in j.get("items", []) if not t.get("title")]) == 0 else issues).append("空标题任务没被拒")

# 9. 引用结构
c, b = call("/api/say", {"text": "引用测试2", "from": "宝宝",
                         "quote": {"who": "妈妈", "text": "被引用的内容"}})
if json.loads(b).get("ok"):
    rid = json.loads(b)["row"]["id"]
    code, page = get("/api/data")
    j = json.loads(page)
    hit = [m for m in j["messages"] if m.get("id") == rid]
    has_q = bool(hit and hit[0].get("quote"))
    (oks if has_q else issues).append("引用字段保存")
    call("/api/delete", {"id": rid})

print("=" * 56)
for o in oks:
    print("  ✓", o)
if issues:
    print("\n问题：")
    for i in issues:
        print("  ✗", i)
else:
    print("\n没有发现异常")
print("=" * 56)
