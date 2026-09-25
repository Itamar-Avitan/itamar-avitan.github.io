"""Draw the two link-preview cards (1200x630) from site.yaml, in the site's own faces and colours.

  img/og.png           the name card: the mark, the name, the role line and the identity line. Every page
                       shares it unless it sets `og_image`.
  img/og-research.png  the paper card, for /research/: the mark, the first badge of the first research card,
                       the paper's short title (the part after the colon), the authors, and the figure the
                       card shows -- Figure 1D drawn from its own SVG (the matrix's cells and its grid, not
                       its axis labels), or the illustration's JPEG, whichever `research[0].figure` points
                       at -- so the preview always matches the page it opens. The first clause of the
                       figure's caption stands under it, split in two balanced lines when it needs two.

Run once after the name, the role line, the identity line, or the paper card's title, badge, authors or
figure change:  python3 tools/make_og.py
Fira Sans and Fira Mono are looked up with fc-match, then with kpsewhich (TeX Live ships both); without them
Pillow's default face is used. Nothing but pixels is written: no EXIF, no text chunks.
"""
from __future__ import annotations

import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
W, H, MARGIN = 1200, 630, 96
SCALE = 2                                   # drawn at twice the size and scaled down, for smooth edges
# the light theme of style.css: paper, ink, secondary ink, accent, the accent tint, the card border, the sheet
BG, INK, INK_2, ACCENT, ACCENT_TINT, RULE, SHEET = "#F4F7F8", "#14212B", "#41525D", "#0B5F73", "#E2EEF1", "#B7C4CC", "#FFFFFF"
MARK_UNIT = 4                               # img/mark.svg is 42 x 10 units
FIG, FIG_PAD = 300, 16                      # the figure on the paper card, and the sheet it sits on


def _run(*cmd: str) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def find_fira(family: str, style: str) -> str | None:
    """Path of an installed Fira font file ("Sans" or "Mono"; "Regular", "Medium", "SemiBold"), or None."""
    name = f"Fira {family}"
    found_family, _, rest = _run("fc-match", "--format=%{family}|%{style}|%{file}", f"{name}:style={style}").partition("|")
    found_style, _, path = rest.partition("|")
    if name in found_family and style.lower() in found_style.lower().replace(" ", "") and Path(path).is_file():
        return path                         # fc-match always answers, so check that it answered with Fira
    path = _run("kpsewhich", f"Fira{family}-{style}.otf")
    return path if path and Path(path).is_file() else None


def load_font(style: str, size: int, family: str = "Sans") -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = find_fira(family, style)
    return ImageFont.truetype(path, size) if path else ImageFont.load_default(size)


def wrap_in_two(text: str, font, draw: ImageDraw.ImageDraw) -> tuple[list[str], float]:
    """The two-line split with the shortest longer line, and that line's width; of two equally wide splits,
    the one with the fuller first line, so the short line is the last. Only a plain space is a place to
    break: a no-break space keeps its two words together, as it does on the page, and Pillow draws it as a
    space in both Fira faces."""
    words = text.split(" ")
    splits = [[" ".join(words[:i]), " ".join(words[i:])] for i in range(1, len(words))] or [[text]]
    widest = lambda lines: max(draw.textlength(line, font=font) for line in lines)
    best = min(splits, key=lambda lines: (widest(lines), -draw.textlength(lines[0], font=font)))
    return best, widest(best)


def wrap(text: str, font, draw: ImageDraw.ImageDraw, room: float) -> list[str]:
    """Greedy word wrap into lines no wider than `room`; a no-break space ties, as in wrap_in_two."""
    lines, line = [], ""
    for word in text.split(" "):
        trial = f"{line} {word}".strip()
        if line and draw.textlength(trial, font=font) > room:
            lines.append(line)
            line = word
        else:
            line = trial
    return lines + [line] if line else lines


def fit(text: str, draw: ImageDraw.ImageDraw, sizes, room: float, max_lines: int, style: str = "Regular",
        family: str = "Sans"):
    """The largest size in `sizes` at which `text` wraps into at most `max_lines` lines within `room`
    (the last size when none does): (size, font, lines)."""
    for size in sizes:
        font = load_font(style, size * SCALE, family)
        lines = wrap(text, font, draw, room * SCALE)
        if len(lines) <= max_lines:
            break
    return size, font, lines


def draw_mark(draw: ImageDraw.ImageDraw, x: int, y: int, s: int) -> None:
    """The same geometry as img/mark.svg, at MARK_UNIT px per unit."""
    u = MARK_UNIT
    for cx in (5, 21):
        draw.ellipse([(x + (cx - 5) * u) * s, y * s, (x + (cx + 5) * u) * s - 1, (y + 10 * u) * s - 1], fill=INK)
    draw.rectangle([(x + 32 * u) * s, y * s, (x + 42 * u) * s - 1, (y + 10 * u) * s - 1], fill=ACCENT)


def draw_card(name: str, line: str, role: str = "") -> Image.Image:
    """The name card: the mark, the name, the role line under it (one line, sized to fit the measure, in the
    page's own role-line face) and the identity line in two lines."""
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
    for rsize in range(32, 23, -1):         # the largest size at which the role line fits on one line
        role_font = load_font("Regular", rsize * s)
        if draw.textlength(role, font=role_font) <= (W - 2 * MARGIN) * s:
            break
    role_h = (rsize + 20) if role else 0    # the role line under the name, and the gap below it

    u = MARK_UNIT
    block = 10 * u + 64 + 104 + role_h + 44 + leading * len(lines)      # mark, gap, name, role, gap, lines
    x, y = MARGIN, (H - block) // 2
    draw_mark(draw, x, y, s)
    baseline = y + 10 * u + 64 + 84          # the name's baseline: about a cap height below the gap
    draw.text((x * s, baseline * s), name, font=name_font, fill=INK, anchor="ls")
    if role:
        baseline += 20 + rsize + 14          # close under the name, like the page's own role line
        draw.text((x * s, baseline * s), role, font=role_font, fill=INK_2, anchor="ls")
        assert draw.textlength(role, font=role_font) <= (W - 2 * MARGIN) * s, "role line wider than the card"
    baseline += 44 + size
    for text in lines:
        draw.text((x * s, baseline * s), text, font=line_font, fill=INK_2, anchor="ls")
        baseline += leading
    return img.resize((W, H), Image.LANCZOS)


def svg_figure(path: Path) -> tuple[list[tuple[float, float, float, float, str]],
                                    list[tuple[float, float, float, float, str]]]:
    """What the card redraws of the figure's SVG, in the SVG's own units: the rectangles inside a translated
    group -- for img/model-recovery.svg the matrix's ground and every cell -- as (x, y, width, height,
    fill), and the straight strokes of a <path> in the same group -- the matrix's grid -- as (x1, y1, x2,
    y2, stroke). A path is read as absolute M, H and V commands, which is all the grid uses; any other
    command is refused rather than drawn wrong. The text labels and the root-level ground are left out: a
    card cannot set the page's webfont, and the sheet the figure is drawn on here stands in for that
    ground."""
    ns = "{http://www.w3.org/2000/svg}"
    rects, lines = [], []
    for group in ET.parse(path).getroot().iter(f"{ns}g"):
        move = re.fullmatch(r"translate\(\s*([-\d.]+)[\s,]+([-\d.]+)\s*\)", group.get("transform", ""))
        if not move:
            continue
        tx, ty = float(move.group(1)), float(move.group(2))
        for rect in group.iter(f"{ns}rect"):
            rects.append((tx + float(rect.get("x", 0)), ty + float(rect.get("y", 0)),
                          float(rect.get("width")), float(rect.get("height")), rect.get("fill", INK)))
        for shape in group.iter(f"{ns}path"):
            d, stroke = shape.get("d", ""), shape.get("stroke", "none")
            if stroke == "none":
                continue
            if re.sub(r"[MHV\d\s.,-]", "", d):
                raise ValueError(f"{path.name}: a path with commands other than M, H and V cannot be redrawn")
            cx = cy = 0.0
            for command, a, b in re.findall(r"([MHV])\s*([-\d.]+)(?:[\s,]+([-\d.]+))?", d):
                if command == "M":
                    cx, cy = float(a), float(b)
                elif command == "H":
                    lines.append((tx + cx, ty + cy, tx + float(a), ty + cy, stroke))
                    cx = float(a)
                else:
                    lines.append((tx + cx, ty + cy, tx + cx, ty + float(a), stroke))
                    cy = float(a)
    return rects, lines


def figure_image(fig: dict, s: int) -> Image.Image:
    """The card's picture of `research[0].figure`, FIG px square on a sheet with FIG_PAD around it: a plot is
    redrawn from its SVG's rectangles and then its grid, one final pixel wide in the path's own stroke
    colour (the SVG's hairline would vanish at this size, and without the grid the matrix read as dots on a
    pale square rather than the 20 by 20 grid the page shows); an illustration is its JPEG resized."""
    side = (FIG + 2 * FIG_PAD) * s
    sheet = Image.new("RGB", (side, side), SHEET)
    draw = ImageDraw.Draw(sheet)
    draw.rectangle([0, 0, side - 1, side - 1], outline=RULE, width=s)
    src = ROOT / fig["src"]
    if src.suffix == ".svg":
        rects, lines = svg_figure(src)
        x0, y0 = min(r[0] for r in rects), min(r[1] for r in rects)
        x1, y1 = max(r[0] + r[2] for r in rects), max(r[1] + r[3] for r in rects)
        k = FIG * s / max(x1 - x0, y1 - y0)
        at = lambda x, y: (FIG_PAD * s + (x - x0) * k, FIG_PAD * s + (y - y0) * k)     # SVG units to sheet px
        for x, y, w, h, fill in rects:
            left, top = at(x, y)
            draw.rectangle([round(left), round(top), round(left + w * k) - 1, round(top + h * k) - 1], fill=fill)
        last = FIG_PAD * s + FIG * s - 1                    # the figure's last pixel: the outermost lines stay inside it
        for xa, ya, xb, yb, stroke in lines:
            (left, top), (right, bottom) = at(min(xa, xb), min(ya, yb)), at(max(xa, xb), max(ya, yb))
            if xa == xb:                                    # a vertical line, s px wide (one final pixel)
                px = min(round(left), last - s + 1)
                draw.rectangle([px, round(top), px + s - 1, min(round(bottom), last)], fill=stroke)
            else:
                py = min(round(top), last - s + 1)
                draw.rectangle([round(left), py, min(round(right), last), py + s - 1], fill=stroke)
    else:
        with Image.open(src) as im:
            sheet.paste(im.convert("RGB").resize((FIG * s, FIG * s), Image.LANCZOS), (FIG_PAD * s, FIG_PAD * s))
    return sheet


def draw_research_card(site: dict) -> Image.Image:
    """The paper card: the mark, the venue badge, the short title, the authors, and the figure with the first
    clause of its caption under it."""
    s = SCALE
    card = site["research"][0]
    img = Image.new("RGB", (W * s, H * s), BG)
    draw = ImageDraw.Draw(img)
    sheet = figure_image(card["figure"], s)
    sheet_w = sheet.width // s
    room = W - 2 * MARGIN - sheet_w - 56                    # the text column, clear of the sheet

    badge = (card.get("badges") or [""])[0]
    badge_font = load_font("Medium", 24 * s, "Mono")
    title = card["title"].split(":", 1)[-1].strip()
    tsize, title_font, title_lines = fit(title, draw, range(56, 35, -2), room, 2, "SemiBold")
    leading = round(tsize * 1.15)
    authors = " and ".join(card.get("authors") or [])
    authors_font = load_font("Regular", 30 * s)
    # the caption's first clause ("Adapted from Fig. 1D of the paper"); the no-break space that ties "Fig."
    # to its number stays, so neither wrap can part them
    caption = (card["figure"].get("caption") or "").split(". ")[0]
    caption_font = load_font("Regular", 22 * s, "Mono")
    caption_lines = wrap(caption, caption_font, draw, sheet_w * s) if caption else []
    if len(caption_lines) == 2:             # two lines: the balanced split, not the greedy one that leaves a runt
        caption_lines, _ = wrap_in_two(caption, caption_font, draw)     # its longer line is never wider than greedy's

    u = MARK_UNIT
    badge_h = 24 + 16 if badge else 0
    block = 10 * u + 56 + badge_h + 28 + leading * len(title_lines) + 28 + 30    # mark, badge, title, authors
    x, y = MARGIN, (H - block) // 2
    draw_mark(draw, x, y, s)
    y += 10 * u + 56
    if badge:
        pad_x, pad_y = 14, 8
        bw = draw.textlength(badge, font=badge_font) / s + 2 * pad_x
        draw.rounded_rectangle([x * s, y * s, (x + bw) * s, (y + badge_h) * s], radius=4 * s,
                               fill=ACCENT_TINT, outline=ACCENT, width=s)
        draw.text(((x + pad_x) * s, (y + pad_y) * s), badge, font=badge_font, fill=ACCENT, anchor="la")
        y += badge_h
    baseline = y + 28 + tsize                                # the first title line's baseline
    for text in title_lines:
        draw.text((x * s, baseline * s), text, font=title_font, fill=INK, anchor="ls")
        baseline += leading
    baseline += 28 + 30 - leading                            # one gap under the last title line, then the authors
    draw.text((x * s, baseline * s), authors, font=authors_font, fill=INK_2, anchor="ls")

    fig_block = sheet_w + (12 + 30 * len(caption_lines) if caption_lines else 0)
    fx, fy = W - MARGIN - sheet_w, (H - fig_block) // 2
    img.paste(sheet, (fx * s, fy * s))
    cap_base = fy + sheet_w + 12 + 22
    for text in caption_lines:
        draw.text((fx * s, cap_base * s), text, font=caption_font, fill=INK_2, anchor="ls")
        cap_base += 30
    return img.resize((W, H), Image.LANCZOS)


def main() -> int:
    site = yaml.safe_load((ROOT / "site.yaml").read_text(encoding="utf-8"))
    role = " · ".join(r["label"] for r in site.get("role") or []).replace(" ", " ")
    cards = {"og.png": draw_card(site["name"], site["identity_line"], role),
             "og-research.png": draw_research_card(site)}
    for name, image in cards.items():
        out = ROOT / "img" / name
        image.save(out, optimize=True)                                      # no metadata is written
        print(f"wrote {out.relative_to(ROOT)} ({out.stat().st_size} bytes)")
    faces = {f"{family} {style}": Path(find_fira(family, style) or "Pillow default").name
             for family, style in (("Sans", "SemiBold"), ("Sans", "Regular"), ("Mono", "Medium"), ("Mono", "Regular"))}
    print(f"fonts: {faces}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
