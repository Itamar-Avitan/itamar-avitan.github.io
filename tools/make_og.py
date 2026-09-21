"""Draw the social-media card img/og.png (1200x630): the mark, the name and the identity line from site.yaml.

Run once after the name or the identity line changes:  python3 tools/make_og.py
Fira Sans is looked up with fc-match, then with kpsewhich (TeX Live ships it); without it Pillow's default face is used.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import yaml
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
W, H, MARGIN = 1200, 630, 96
SCALE = 2                                   # drawn at twice the size and scaled down, for smooth edges
BG, INK, INK_2, ACCENT = "#F4F7F8", "#14212B", "#41525D", "#0B5F73"     # the light theme of style.css
MARK_UNIT = 4                               # img/mark.svg is 42 x 10 units


def _run(*cmd: str) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def find_fira_sans(style: str) -> str | None:
    """Path of an installed Fira Sans font file in this style ("Regular", "SemiBold"), or None."""
    family, _, rest = _run("fc-match", "--format=%{family}|%{style}|%{file}", f"Fira Sans:style={style}").partition("|")
    found_style, _, path = rest.partition("|")
    if "Fira Sans" in family and style.lower() in found_style.lower().replace(" ", "") and Path(path).is_file():
        return path                         # fc-match always answers, so check that it answered with Fira Sans
    path = _run("kpsewhich", f"FiraSans-{style}.otf")
    return path if path and Path(path).is_file() else None


def load_font(style: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = find_fira_sans(style)
    return ImageFont.truetype(path, size) if path else ImageFont.load_default(size)


def wrap_in_two(text: str, font, draw: ImageDraw.ImageDraw) -> tuple[list[str], float]:
    """The two-line split with the shortest longer line, and that line's width."""
    words = text.split()
    splits = [[" ".join(words[:i]), " ".join(words[i:])] for i in range(1, len(words))] or [[text]]
    widest = lambda lines: max(draw.textlength(line, font=font) for line in lines)
    best = min(splits, key=widest)
    return best, widest(best)


def draw_card(name: str, line: str) -> Image.Image:
    s = SCALE
    img = Image.new("RGB", (W * s, H * s), BG)
    draw = ImageDraw.Draw(img)
    name_font = load_font("SemiBold", 104 * s)
    for size in range(44, 23, -2):          # the largest size at which two lines fit between the margins
        line_font = load_font("Regular", size * s)
        lines, width = wrap_in_two(line, line_font, draw)
        if width <= (W - 2 * MARGIN) * s:
            break
    leading = round(size * 1.4)

    u = MARK_UNIT
    block = 10 * u + 64 + 104 + 44 + leading * len(lines)      # mark, gap, name, gap, lines
    x, y = MARGIN, (H - block) // 2
    for cx in (5, 21):                       # the same geometry as img/mark.svg
        draw.ellipse([(x + (cx - 5) * u) * s, y * s, (x + (cx + 5) * u) * s - 1, (y + 10 * u) * s - 1], fill=INK)
    draw.rectangle([(x + 32 * u) * s, y * s, (x + 42 * u) * s - 1, (y + 10 * u) * s - 1], fill=ACCENT)

    baseline = y + 10 * u + 64 + 84          # the name's baseline: about a cap height below the gap
    draw.text((x * s, baseline * s), name, font=name_font, fill=INK, anchor="ls")
    baseline += 44 + size
    for text in lines:
        draw.text((x * s, baseline * s), text, font=line_font, fill=INK_2, anchor="ls")
        baseline += leading
    return img.resize((W, H), Image.LANCZOS)


def main() -> int:
    site = yaml.safe_load((ROOT / "site.yaml").read_text(encoding="utf-8"))
    out = ROOT / "img" / "og.png"
    draw_card(site["name"], site["identity_line"]).save(out, optimize=True)     # no metadata is written
    faces = {style: Path(find_fira_sans(style) or "Pillow default").name for style in ("SemiBold", "Regular")}
    print(f"wrote {out.relative_to(ROOT)} ({out.stat().st_size} bytes); fonts: {faces}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
