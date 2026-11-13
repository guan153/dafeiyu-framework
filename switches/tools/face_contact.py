# -*- coding: utf-8 -*-
"""把 docs/face/ 的 12 张表情差分拼成一张对照表（PNG）。

跑法：
    python tools/face_contact.py
    python tools/face_contact.py docs/face_contact.png
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from whale_core.face import MOOD_FACE, card_for  # noqa: E402

FACE_DIR = ROOT / "docs" / "face"
FONT = r"C:\Windows\Fonts\msyh.ttc"
FONT_B = r"C:\Windows\Fonts\msyhbd.ttc"
BG = (12, 14, 19)
TXT = (235, 239, 247)
SUB = (150, 163, 186)
WARM = (255, 214, 120)


def _f(size: int, bold: bool = False):
    for p in ([FONT_B] if bold else []) + [FONT, FONT_B]:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default()


def main(out: Path) -> int:
    files = []
    for i, mood in enumerate(MOOD_FACE):
        p = FACE_DIR / f"{i:02d}_{mood}.png"
        if p.exists():
            files.append((i, mood, p))
    if not files:
        print("[fail] docs/face/ 里没有图，先跑 tools/face_gen.py")
        return 2

    manifest = {}
    mf = FACE_DIR / "manifest.json"
    if mf.exists():
        try:
            manifest = json.loads(mf.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            manifest = {}

    cols = 4
    rows = (len(files) + cols - 1) // cols
    tw, th = 460, 660          # 单格（图 + 两行字）
    gap, pad, head = 18, 24, 92
    W = pad * 2 + cols * tw + (cols - 1) * gap
    H = head + rows * th + (rows - 1) * gap + 40

    sheet = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(sheet)
    d.text((pad, 22), "她的 12 张脸", font=_f(36, True), fill=TXT)
    d.text((pad, 66), "同一 LoRA / 同一人物描述 / 只换表情段 · 强度档 0.70", font=_f(17), fill=SUB)

    for k, (i, mood, path) in enumerate(files):
        r, c = divmod(k, cols)
        x = pad + c * (tw + gap)
        y = head + r * (th + gap)

        card = card_for(mood, 0.78)
        im = Image.open(path).convert("RGB")
        im.thumbnail((tw, th - 96))
        sheet.paste(im, (x + (tw - im.width) // 2, y))

        label_y = y + th - 88
        d.text((x, label_y), mood, font=_f(28, True), fill=TXT)
        expr = card.exp3 or "（不做表情）"
        d.text((x + 92, label_y + 8), f"『{expr}』", font=_f(19), fill=WARM)
        d.text((x, label_y + 42), f"尾巴 {card.tail} · 表情包 {card.meme_tag} ×{card.meme_boost:.2f}", font=_f(15), fill=SUB)

    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, quality=90)
    print(f"saved {out} ({sheet.width}x{sheet.height}, {out.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "face_contact.png"
    raise SystemExit(main(target))
