# -*- coding: utf-8 -*-
"""把可爱层（CUTE_KIT）渲染成一张能看懂的「嘴谱表」（PNG）。

跑法：
    python tools/cute_sheet.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from whale_core.face import CUTE_KIT, MOOD_FACE, card_for, cute_kit  # noqa: E402

BG = (14, 16, 22)
CARD = (24, 27, 36)
EDGE = (38, 44, 58)
TXT = (232, 236, 245)
SUB = (150, 163, 186)
ACC = (255, 168, 120)   # 暖色：这一层是「嘴」，跟蓝色的「脸」区分
WARM = (255, 214, 120)
MUTED = (111, 127, 153)
FONT = r"C:\Windows\Fonts\msyh.ttc"
FONT_B = r"C:\Windows\Fonts\msyhbd.ttc"


def _f(size: int, bold: bool = False):
    for p in ([FONT_B] if bold else []) + [FONT, FONT_B]:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default()


def build(out: Path) -> Path:
    moods = list(MOOD_FACE)
    cols, rows = 3, 4
    cw, ch = 560, 300
    pad, head = 26, 104
    W = pad * 2 + cols * cw + (cols - 1) * pad
    H = head + rows * ch + (rows - 1) * 18 + 46

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    f_title = _f(34, True)
    f_sub = _f(16)
    f_mood = _f(26, True)
    f_body = _f(16)
    f_k = _f(15)
    f_kao = _f(19)

    d.text((pad, 22), "她的嘴 · 可爱配方 v1", font=f_title, fill=TXT)
    d.text((pad, 66), "证据：B 站同题材 14 条高播放视频的 1420 条真实评论 · 来源 whale_core/face.py CUTE_KIT",
           font=f_sub, fill=SUB)

    for i, mood in enumerate(moods):
        r, c = divmod(i, cols)
        x = pad + c * (cw + pad)
        y = head + r * (ch + 18)
        card = card_for(mood, 0.78)
        kit = cute_kit(card, salt=11)
        spec = CUTE_KIT[mood]

        d.rounded_rectangle([x, y, x + cw, y + ch], radius=14, fill=CARD, outline=EDGE, width=1)
        d.rounded_rectangle([x, y, x + 6, y + ch], radius=3, fill=ACC)

        d.text((x + 24, y + 18), mood, font=f_mood, fill=TXT)
        d.text((x + 24 + d.textlength(mood, font=f_mood) + 12, y + 26),
               f"自称「{kit.self_ref}」", font=f_body, fill=WARM)

        def line(label, value, yy, color=SUB, font=f_k):
            d.text((x + 24, yy), label, font=font, fill=MUTED)
            d.text((x + 92, yy), value, font=font, fill=color)

        line("口癖", " / ".join(spec.words) if spec.words else "（不说话，只说事）", y + 62, font=f_body)
        line("颜文字", kit.kaomoji or "（不出）", y + 94, color=(190, 200, 230), font=f_kao)
        line("傲娇尾", "、".join([kit.tsundere_tail]) if kit.tsundere_tail else "（不傲娇）", y + 128)
        line("贴贴", kit.touch_line or "（不要贴贴）", y + 158)
        line("配图台词", f"「{spec.sticker}」" if spec.sticker else "（不发图）", y + 188)

        # 示例拼装：只展示零件怎么摆，不假装是一句真实回复
        parts = []
        if kit.words:
            parts.append(kit.words[0])
        if kit.kaomoji:
            parts.append(kit.kaomoji)
        if kit.tsundere_tail:
            parts.append(kit.tsundere_tail)
        demo = "  ".join(parts) if parts else "——"
        d.text((x + 24, y + 226), "零件预览", font=f_k, fill=MUTED)
        d.text((x + 24, y + 248), demo[:34], font=f_body, fill=(200, 210, 240))

    note = "硬规则：正事档全关（口癖/颜文字/傲娇）｜安慰档不许傲娇｜强度<0.25 不卖萌｜不许自我贬低（「我真是好鱼」才对）"
    d.text((pad, H - 34), note, font=_f(14), fill=MUTED)

    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, quality=92)
    return out


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "cute_sheet.png"
    p = build(target)
    print(f"saved {p} ({p.stat().st_size // 1024} KB)")
