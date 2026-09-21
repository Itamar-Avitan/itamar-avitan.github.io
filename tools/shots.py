"""Screenshot every page of the built site: desktop light, desktop dark, phone light.

    python3 tools/shots.py

Writes shots/<page>-<view>.png (git-ignored) and prints each page's height at both widths, because the point
of the four-page split was that no page comes near the 4383px (laptop) and 6548px (phone) the single page
stood at. The site is rebuilt first, so the pictures always show the current site.yaml, templates and
style.css. Look at them.
"""
import subprocess
import sys
from pathlib import Path

import yaml
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "shots"
VIEWS = [("desktop-light", 1280, 900, "light"), ("desktop-dark", 1280, 900, "dark"), ("phone-light", 400, 850, "light")]


def main() -> int:
    subprocess.run([sys.executable, str(ROOT / "build.py")], check=True)
    OUT.mkdir(exist_ok=True)
    site = yaml.safe_load((ROOT / "site.yaml").read_text(encoding="utf-8"))
    with sync_playwright() as p:
        b = p.chromium.launch()
        for entry in site["pages"]:
            name = entry["slug"].strip("/") or "home"
            url = (ROOT / entry["slug"] / "index.html").as_uri()
            for view, w, h, scheme in VIEWS:
                ctx = b.new_context(viewport={"width": w, "height": h}, color_scheme=scheme, device_scale_factor=1)
                page = ctx.new_page()
                page.goto(url)
                page.wait_for_timeout(400)
                page.screenshot(path=str(OUT / f"{name}-{view}.png"), full_page=True)
                height = page.evaluate("Math.round(document.documentElement.scrollHeight)")
                overflow = page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth")
                print(f"shots/{name}-{view}.png: {w}px wide, {height}px tall, horizontal overflow={overflow}")
                ctx.close()
        b.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
