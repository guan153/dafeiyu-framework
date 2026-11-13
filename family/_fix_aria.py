# -*- coding: utf-8 -*-
"""给页面补无障碍标签（PowerShell 引号打架，用 Python 改）。"""
from pathlib import Path

p = Path(r"%PROJECT_HOME%\母女聊天室\index.html")
s = p.read_text(encoding="utf-8")

pairs = [
    ('<div id="feed">', '<div id="feed" role="log" aria-live="polite" aria-label="家庭对话">'),
    ('id="btnSearch" title="搜索（Ctrl+K）">', 'id="btnSearch" title="搜索（Ctrl+K）" aria-label="搜索消息">'),
    ('id="btnTheme" title="深色/浅色">', 'id="btnTheme" title="深色/浅色" aria-label="切换深色模式">'),
    ('id="btnSide">记忆</button>', 'id="btnSide" aria-label="打开记忆面板">记忆</button>'),
    ('id="btnQQ">QQ 同步：关</button>', 'id="btnQQ" aria-label="切换 QQ 同步">QQ 同步：关</button>'),
    ('id="memeBtn" title="表情包">', 'id="memeBtn" title="表情包" aria-label="打开表情包">'),
    ('id="atBtn" title="@ 她俩">', 'id="atBtn" title="@ 她俩" aria-label="提到某人">'),
    ('placeholder="说点什么……"></textarea>', 'placeholder="说点什么……" aria-label="输入消息"></textarea>'),
    ('class="jump" id="jumpNew" title="回到最新">', 'class="jump" id="jumpNew" title="回到最新" aria-label="回到最新消息">'),
    ('class="side" id="side">', 'class="side" id="side" aria-label="记忆与任务面板">'),
    ('id="memes">', 'id="memes" aria-label="表情包面板">'),
]
n = 0
for a, b in pairs:
    if a in s:
        s = s.replace(a, b, 1)
        n += 1
    else:
        print("没找到:", a[:50])

p.write_text(s, encoding="utf-8")
print(f"改了 {n}/{len(pairs)} 处，aria-label 现在有 {s.count('aria-label')} 个")
