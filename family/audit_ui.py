# -*- coding: utf-8 -*-
"""对照成熟聊天工具，审一遍自家页面。"""
import json
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8899"
HERE = Path(r"%PROJECT_HOME%\母女聊天室")
html = (HERE / "index.html").read_text(encoding="utf-8")
js = html.split("<script>")[-1] if "<script>" in html else ""
css = html.split("<style>")[1].split("</style>")[0] if "<style>" in html else ""


def has(*words):
    return all(w in html for w in words)


d = json.loads(urllib.request.urlopen(BASE + "/api/data", timeout=20).read())
m = json.loads(urllib.request.urlopen(BASE + "/api/memes", timeout=25).read())

print("现状")
print(f"  消息 {len(d['messages'])} 条（一次全量返回，{len(json.dumps(d, ensure_ascii=False))//1024} KB）")
print(f"  表情 {len(m['items'])} 张")
print(f"  index.html {len(html)//1024} KB，CSS {len(css)//1024} KB，JS {len(js)//1024} KB")
print()

checks = [
    ("消息引用/回复", has("quote")),
    ("消息搜索", has("search") or has("搜索")),
    ("复制消息", has("clipboard")),
    ("撤回/删除消息", has("撤回") or has("delete")),
    ("@提及", has("mention")),
    ("滚动到最新按钮", has("scrollBottom") or has("跳到最新")),
    ("消息分组（同人合并）", has("grouped") or has("连续")),
    ("时间悬停看完整", has("title=") and has("toLocaleString")),
    ("草稿保存", has("localStorage")),
    ("深色模式", has("dark") and has("prefers-color-scheme")),
    ("响应式（窄屏）", has("@media")),
    ("快捷键", has("Escape") or has("metaKey")),
    ("未读/标题闪烁", "document.title" in html),
    ("分页/加载更早", has("loadMore") or has("更早")),
    ("消息状态（发送中/失败）", has("sending") or has("失败")),
    ("输入中提示", has("正在输入") or has("typing")),
    ("消息通栏时间分隔", has("dayLab")),
    ("图片放大查看", has("lightbox") or has("放大查看")),
    ("右键菜单", has("contextmenu")),
    ("aria 无障碍", has("aria-")),
]

print("成熟聊天工具有、我们有的：")
for name, ok in checks:
    if ok:
        print(f"  ✓ {name}")
print("\n还缺的：")
for name, ok in checks:
    if not ok:
        print(f"  ✗ {name}")
