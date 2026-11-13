# -*- coding: utf-8 -*-
"""把现成的图裁成页面用的头像。"""
from pathlib import Path
from PIL import Image

OUT = Path(r"%PROJECT_HOME%\母女聊天室\assets")
OUT.mkdir(parents=True, exist_ok=True)

# 妈的立绘 -> 头部特写
src = Path(r"%PROJECT_HOME%\_live2d\gen\front_closed.png")
im = Image.open(src).convert("RGBA")
W, H = im.size
print("立绘尺寸", W, H)
# 头部大概在中间偏上：整张 832x1216，头在 y 60~520、x 230~640
box = (int(W * 0.26), int(H * 0.045), int(W * 0.78), int(H * 0.44))
head = im.crop(box).resize((256, 256), Image.LANCZOS)
head.save(OUT / "mom.png")
print("妈妈头像 ->", OUT / "mom.png", head.size)

# 大肥鱼的现成头像
fish_src = Path(r"%PROJECT_HOME%\_bili_qq_p1")
for name in ("smile", "cheer", "heart", "point"):
    p = fish_src / f"avatar_{name}.png"
    if p.exists():
        img = Image.open(p).convert("RGBA").resize((256, 256), Image.LANCZOS)
        img.save(OUT / f"fish_{name}.png")
        print("女儿头像 ->", OUT / f"fish_{name}.png")

# 宝宝：暂时用符号，不做图
print("完成")
