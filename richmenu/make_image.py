"""Draw richmenu/richmenu.png (2500x843, under 1 MB) with Pillow and a Thai font from macOS.
Run only when the design changes:  .venv/bin/python richmenu/make_image.py"""
import os
import sys

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(__file__))
from menu import COLS, H, LABELS, W

# Not Thonburi: Pillow's basic layout cannot stack Thai vowel/tone marks with it (dotted circles appear).
FONTS = ["/System/Library/Fonts/Supplemental/Krungthep.ttf", "/System/Library/Fonts/Supplemental/Sathu.ttf",
         "/System/Library/Fonts/Supplemental/Silom.ttf", "/System/Library/Fonts/Supplemental/Ayuthaya.ttf"]
BG, TILE, TILE2, INK, ACCENT = "#0e5a78", "#12708f", "#0b4c66", "#ffffff", "#7fd1ee"


def icon(d, i, cx, cy, r):
    """Simple white icons drawn from shapes (no emoji fonts needed)."""
    if i == 0:   # location pin
        d.ellipse([cx - r, cy - r * 1.1, cx + r, cy + r * 0.9], fill=INK)
        d.polygon([(cx - r * 0.75, cy + r * 0.45), (cx + r * 0.75, cy + r * 0.45), (cx, cy + r * 1.9)], fill=INK)
        d.ellipse([cx - r * 0.38, cy - r * 0.55, cx + r * 0.38, cy + r * 0.21], fill=BG)
    elif i == 1:  # water drop with level line
        d.polygon([(cx, cy - r * 1.5), (cx - r * 0.95, cy + r * 0.1), (cx + r * 0.95, cy + r * 0.1)], fill=INK)
        d.ellipse([cx - r * 0.95, cy - r * 0.55, cx + r * 0.95, cy + r * 1.35], fill=INK)
        d.arc([cx - r * 0.45, cy - r * 0.05, cx + r * 0.45, cy + r * 0.85], 20, 110, fill=BG, width=14)
    elif i == 2:  # clipboard / report
        d.rounded_rectangle([cx - r * 0.95, cy - r * 1.3, cx + r * 0.95, cy + r * 1.3], 22, fill=INK)
        for k in range(3):
            y = cy - r * 0.55 + k * r * 0.6
            d.rounded_rectangle([cx - r * 0.6, y, cx + r * 0.6, y + 16], 8, fill=BG)
        d.rounded_rectangle([cx - r * 0.4, cy - r * 1.5, cx + r * 0.4, cy - r * 1.05], 12, fill=ACCENT)
    else:         # sliders (settings)
        for k, off in enumerate((-0.7, 0, 0.7)):
            y = cy + off * r * 1.1
            d.rounded_rectangle([cx - r * 1.1, y - 8, cx + r * 1.1, y + 8], 8, fill=INK)
            x = cx + (-0.5 + 0.5 * k) * r
            d.ellipse([x - 26, y - 26, x + 26, y + 26], fill=ACCENT)


def main(out):
    path = next((p for p in FONTS if os.path.exists(p)), None)
    if path is None:
        sys.exit("no Thai font found; edit FONTS in this script")
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    col_w = W // COLS
    for i, label in enumerate(LABELS):
        x0 = i * col_w
        d.rectangle([x0 + 8, 8, x0 + col_w - 8, H - 8], fill=TILE if i % 2 == 0 else TILE2)
        icon(d, i, x0 + col_w / 2, 300, 120)
        size = 120
        while size > 50 and d.textlength(label, font=ImageFont.truetype(path, size)) > col_w - 80:
            size -= 4   # shrink long labels to fit their button
        font = ImageFont.truetype(path, size)
        w = d.textlength(label, font=font)
        d.text((x0 + (col_w - w) / 2, 585 - (size - 120) / 2), label, font=font, fill=INK)
    img.save(out, optimize=True)
    print(f"wrote {out} ({os.path.getsize(out) // 1024} KB)")


if __name__ == "__main__":
    main(os.path.join(os.path.dirname(__file__), "richmenu.png"))
