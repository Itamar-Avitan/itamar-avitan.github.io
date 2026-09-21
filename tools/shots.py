"""Screenshot the built page: desktop light, desktop dark, phone light. Writes shots/*.png (git-ignored).

    python3 tools/shots.py

The page is rebuilt first, so the pictures always show the current site.yaml, template and style.css. Look at them.
"""
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "shots"
VIEWS = [("desktop-light", 1280, 900, "light"), ("desktop-dark", 1280, 900, "dark"), ("phone-light", 400, 850, "light")]


def main() -> int:
    subprocess.run([sys.executable, str(ROOT / "build.py")], check=True)
    OUT.mkdir(exist_ok=True)
    url = (ROOT / "index.html").as_uri()
    with sync_playwright() as p:
        b = p.chromium.launch()
        for name, w, h, scheme in VIEWS:
            ctx = b.new_context(viewport={"width": w, "height": h}, color_scheme=scheme, device_scale_factor=1)
            page = ctx.new_page()
            page.goto(url)
            page.wait_for_timeout(400)
            page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)
            overflow = page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth")
            print(f"shots/{name}.png: {w}px wide, horizontal overflow={overflow}")
            ctx.close()
        b.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
