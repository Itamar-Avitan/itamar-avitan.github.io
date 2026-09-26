"""Write the icon files at the site root from img/mark.svg. Run once after the mark changes:

    python3 tools/make_favicon.py

  favicon.svg           the mark on a square canvas (viewBox 0 -16 42 42), its theme style kept, so a browser
                        that takes SVG icons draws it in the dark-theme colours on a dark system.
  favicon-96.png        96 by 96, the light-theme colours on a transparent ground. Google Search takes only a
                        square raster favicon (BMP, GIF, ICO, PNG, JPEG, PPM, TIFF; larger than 48px
                        recommended) that Googlebot-Image can crawl: an inline data: SVG is none of those.
  favicon.ico           16, 32 and 48, for clients that ask for /favicon.ico blindly.
  apple-touch-icon.png  180 by 180, the row set on the paper colour, for a phone's home screen.

The mark's arrangement does not change here (the odd-one-out row, kept by controller decision C5, 2026-09-25): a 42-unit row centred on a
42-unit square is small in a 16px tab, and that is the owner's choice to revisit, not this script's.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
INK, ACCENT, PAPER = "#14212B", "#0B5F73", "#F4F7F8"      # the light theme of style.css, as in make_og.py
ROW, SQUARE = 'viewBox="0 0 42 10"', 'viewBox="0 -16 42 42"'


def draw_row(draw: ImageDraw.ImageDraw, x: float, y: float, unit: float) -> None:
    """The same geometry as img/mark.svg: two circles of diameter 10 at 0 and 16, a square of side 10 at 32."""
    for cx in (0, 16):
        draw.ellipse((x + cx * unit, y, x + (cx + 10) * unit, y + 10 * unit), fill=INK)
    draw.rectangle((x + 32 * unit, y, x + 42 * unit, y + 10 * unit), fill=ACCENT)


def main() -> int:
    mark = (ROOT / "img" / "mark.svg").read_text(encoding="utf-8").strip()
    assert ROW in mark, "img/mark.svg is not the 42 by 10 row this script expects"
    (ROOT / "favicon.svg").write_text(mark.replace(ROW, SQUARE) + "\n", encoding="utf-8")

    s = 8                                                   # drawn large and scaled down, for smooth edges
    square = Image.new("RGBA", (42 * s, 42 * s), (0, 0, 0, 0))
    draw_row(ImageDraw.Draw(square), 0, 16 * s, s)
    square.resize((96, 96), Image.LANCZOS).save(ROOT / "favicon-96.png")
    square.resize((48, 48), Image.LANCZOS).save(ROOT / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])

    side, row = 180 * s, 140 * s                            # the row 140px wide on a 180px tile, 20px clear each side
    tile = Image.new("RGB", (side, side), PAPER)
    unit = row / 42
    draw_row(ImageDraw.Draw(tile), (side - row) / 2, (side - 10 * unit) / 2, unit)
    tile.resize((180, 180), Image.LANCZOS).save(ROOT / "apple-touch-icon.png")

    for name in ("favicon.svg", "favicon-96.png", "favicon.ico", "apple-touch-icon.png"):
        print(f"wrote {name} ({(ROOT / name).stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
