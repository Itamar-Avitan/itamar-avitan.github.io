"""Make the research card's thumbnail from a source picture. Run by hand:

    python3 tools/make_paper_thumb.py <source image> <name>

Writes img/<name>.webp and img/<name>.jpg, square, 544px (twice the 272px the card lays out), with every
piece of metadata dropped: the image is repainted into a fresh canvas, so nothing from the source file's
EXIF, ICC or comment blocks can travel with it. Point site.yaml's figure entry at the pair afterwards.

The live thumbnail img/paper-lab.* was made this way from the Brains and Machines Lab's own illustration for
the NeurIPS 2025 paper, https://brainsandmachines.org/images/paper_figures/Model-Behavior_Alignment_under_Flexible_Evaluation.jpg
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SIZE = 544


def make(source: Path, name: str) -> list[Path]:
    with Image.open(source) as im:
        square = im.convert("RGB")
        side = min(square.size)
        left, top = (square.width - side) // 2, (square.height - side) // 2
        square = square.resize((SIZE, SIZE), Image.LANCZOS, box=(left, top, left + side, top + side))
    clean = Image.new("RGB", (SIZE, SIZE))          # a fresh canvas carries no info dict from the source
    clean.paste(square, (0, 0))
    webp, jpg = ROOT / "img" / f"{name}.webp", ROOT / "img" / f"{name}.jpg"
    clean.save(webp, "WEBP", quality=76, method=6)      # the browser asks for this one
    clean.save(jpg, "JPEG", quality=78, optimize=True, progressive=True)
    return [webp, jpg]


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    for path in make(Path(argv[0]).expanduser(), argv[1]):
        print(f"{path.relative_to(ROOT)}  {path.stat().st_size:,} B")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
