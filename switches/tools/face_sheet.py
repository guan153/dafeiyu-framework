# -*- coding: utf-8 -*-
"""把 MOOD_FACE 渲染成一张看得懂的「脸谱表」（PNG）。

用途：改表之前先看表——12 种心情各自长什么样、话多长、多久能发一次图。
跑法：
    python tools/face_sheet.py            # → docs/face_sheet.png
    python tools/face_sheet.py out.png
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from whale_core.face import MOOD_FACE, card_for  # noqa: E402

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
]

BG = (14, 16, 22)
CARD = (24, 27, 36)
CARD_EDGE = (38, 44, 58)
TXT = (232, 236, 245)
SUB = (147, 160, 184)
ACC = (78, 168, 255)
WARM = (255, 214, 120)
MUTED = (111, 127, 153)


def _font(size: int, bold: bool = False):
    order = [r"C:\Windows\Fonts\msyhbd.ttc"] if bold else []
    for p in order + FONT_CANDIDATES:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default()


def build(out: Path) -> Path:
    moods = list(MOOD_FACE)
    cols, rows = 4, 3
    cw, ch = 430, 300
    pad = 26
    header_h = 96
    W = pad + cols * (cw + pad)
    H = header_h + rows * (ch + pad) + 46

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    f_title = _font(34, bold=True)
    f_sub = _font(17)
    f_mood = _font(30, bold=True)
    f_expr = _font(21, bold=True)
    f_body = _font(16)
    f_small = _font(14)

    d.text((pad, 24), "她的脸 · 情绪外显表 v0", font=f_title, fill=TXT)
    d.text(
        (pad, 66),
        "12 种心情 → 表情 / 尾巴 / 语气 / 表情包权重 · 来源 whale_core/face.py MOOD_FACE · 强度按 0.70 取高强度档",
        font=f_sub,
        fill=SUB,
    )

    for i, mood in enumerate(moods):
        r, c = divmod(i, cols)
        x = pad + c * (cw + pad)
        y = header_h + r * (ch + pad)
        card = card_for(mood, 0.78)  # 高强度档：看「最足的那一面」
        spec = MOOD_FACE[mood]

        d.rounded_rectangle([x, y, x + cw, y + ch], radius=16, fill=CARD, outline=CARD_EDGE, width=1)
        # 顶条：情绪本色
        d.rounded_rectangle([x, y, x + cw, y + 6], radius=3, fill=ACC if card.allow_image else MUTED)

        d.text((x + 22, y + 20), mood, font=f_mood, fill=TXT)
        expr = card.exp3 or "（不做表情）"
        d.text((x + 22 + d.textlength(mood, font=f_mood) + 14, y + 30), f"『{expr}』", font=f_expr, fill=WARM)

        lines = [
            f"尾巴：{card.tail}",
            f"语气：{card.voice}",
            f"句长 ≤{card.sentence_max} 字 · 语气词 {card.particles}/3 · emoji {card.emoji}",
            f"延迟 ×{card.delay_x:.2f} · 撒娇档 {card.cute}/3 · 最多 {card.max_messages} 条",
            f"表情包：{card.meme_tag} ×{card.meme_boost:.2f}",
        ]
        ty = y + 78
        for ln in lines:
            d.text((x + 22, ty), ln, font=f_body, fill=SUB if ty < y + 200 else SUB)
            ty += 28

        flag = "可发图" if card.allow_image else "只发字"
        fw = d.textlength(flag, font=f_small)
        d.rounded_rectangle([x + cw - fw - 40, y + 20, x + cw - 18, y + 48], radius=14,
                            fill=(36, 60, 48) if card.allow_image else (56, 36, 40))
        d.text((x + cw - fw - 29, y + 26), flag, font=f_small,
               fill=(130, 220, 160) if card.allow_image else (235, 150, 150))

        # 低强度时是否还有脸
        soft = card_for(mood, 0.1).exp3 or "无"
        d.text((x + 22, y + ch - 34), f"低强度时：{soft}", font=f_small, fill=MUTED)

    note = "场景覆盖：work → 黑框眼镜/不撒娇/不发图 ｜ comfort → 收图+压句长 ｜ night → 延迟 ×1.25 ｜ 强度<0.25 → 不做鬼脸"
    d.text((pad, H - 34), note, font=f_small, fill=MUTED)

    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, quality=92)
    return out


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "face_sheet.png"
    p = build(target)
    print(f"saved {p} ({p.stat().st_size // 1024} KB)")
