# -*- coding: utf-8 -*-
"""把全家福做成 .ico，再建一个桌面快捷方式。"""
from pathlib import Path
from PIL import Image

HERE = Path(r"%PROJECT_HOME%\母女聊天室")
ASSETS = HERE / "assets"
src = ASSETS / "family.png"
ico = ASSETS / "family.ico"

im = Image.open(src).convert("RGBA")
# 图标：正方形，圆角外留白，稍微裁一点让脸更突出
w, h = im.size
side = min(w, h)
im = im.crop(((w - side) // 2, (h - side) // 2, (w + side) // 2, (h + side) // 2))
sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
im.save(ico, format="ICO", sizes=sizes)
print(f"图标: {ico}  {ico.stat().st_size} bytes")

# 预览：256 的 png，方便看
im.resize((256, 256), Image.LANCZOS).save(ASSETS / "family_256.png")
print("预览: assets/family_256.png")
