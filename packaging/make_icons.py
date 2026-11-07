"""Regenerate the macOS .icns and Linux 512px PNG from assets/moviq-icon.svg.

Run from the repo root:  python packaging/make_icons.py
Needs: Pillow, resvg-py (both already project dependencies).
"""
import io
from pathlib import Path

import resvg_py
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
SIZE, TILE, RADIUS = 1024, 824, 185


def build_tile():
    svg = (ASSETS / "moviq-icon.svg").read_text(encoding="utf-8")
    glyph = Image.open(io.BytesIO(bytes(
        resvg_py.svg_to_bytes(svg_string=svg, width=620),
    ))).convert("RGBA")
    off = (SIZE - TILE) // 2
    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    shadow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (off, off + 14, off + TILE, off + TILE + 14),
        radius=RADIUS, fill=(0, 0, 0, 70),
    )
    canvas = Image.alpha_composite(
        canvas, shadow.filter(ImageFilter.GaussianBlur(14)),
    )
    tile = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    ImageDraw.Draw(tile).rounded_rectangle(
        (off, off, off + TILE, off + TILE),
        radius=RADIUS, fill=(255, 255, 255, 255),
    )
    canvas = Image.alpha_composite(canvas, tile)
    canvas.alpha_composite(
        glyph, ((SIZE - glyph.width) // 2, (SIZE - glyph.height) // 2),
    )
    return canvas


if __name__ == "__main__":
    tile = build_tile()
    tile.save(
        ASSETS / "moviq-icon.icns", format="ICNS",
        sizes=[(16, 16), (32, 32), (64, 64), (128, 128),
               (256, 256), (512, 512), (1024, 1024)],
    )
    tile.resize((512, 512), Image.LANCZOS).save(ASSETS / "moviq-icon-512.png")
    print("Wrote assets/moviq-icon.icns and assets/moviq-icon-512.png")
