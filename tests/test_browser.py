import re
import subprocess
import sys
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def browser():
    assert subprocess.run([sys.executable, str(ROOT / "build.py")]).returncode == 0
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def _page(browser, width, scheme="light"):
    ctx = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
    page = ctx.new_page()
    requests, errors = [], []
    page.on("request", lambda r: requests.append(r.url))
    page.on("requestfailed", lambda r: errors.append(f"failed {r.url}"))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.goto((ROOT / "index.html").as_uri())
    page.wait_for_timeout(300)
    return page, requests, errors


@pytest.mark.parametrize("width", [400, 768, 1280])
def test_no_horizontal_overflow(browser, width):
    page, _, _ = _page(browser, width)
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")


def test_only_local_requests_and_no_errors(browser):
    _, requests, errors = _page(browser, 1280)
    assert [u for u in requests if not u.startswith("file://") and not u.startswith("data:")] == []
    assert errors == []


def test_theme_toggle_switches_and_persists(browser):
    page, _, _ = _page(browser, 1280, "light")
    bg = lambda: page.evaluate("getComputedStyle(document.body).backgroundColor")
    before = bg()
    page.click("#theme-toggle")
    assert page.evaluate("document.documentElement.getAttribute('data-theme')") == "dark" and bg() != before
    page.reload()
    assert page.evaluate("document.documentElement.getAttribute('data-theme')") == "dark"


def test_dark_scheme_is_followed_without_a_choice(browser):
    light, _, _ = _page(browser, 1280, "light")
    dark, _, _ = _page(browser, 1280, "dark")
    get = "getComputedStyle(document.body).backgroundColor"
    assert light.evaluate(get) != dark.evaluate(get)


def test_email_link_is_built_in_the_browser(browser):
    page, _, _ = _page(browser, 1280)
    assert page.get_attribute("a#email", "href").startswith("mailto:avitanit@")
    assert "[at]" in page.inner_text("a#email")


def test_structure_and_alt_text(browser):
    page, _, _ = _page(browser, 1280)
    assert page.locator("h1").count() == 1 and page.inner_text("h1").strip() == "Itamar Avitan"
    assert page.evaluate("[...document.images].every(i => i.alt && i.complete && i.naturalWidth > 0)")
    for sec in ("research", "news", "talks", "projects", "quotes", "teaching"):
        assert page.locator(f"#{sec}").count() == 1
    assert page.evaluate("[...document.querySelectorAll('svg.ico')].every(s => s.getAttribute('aria-hidden') === 'true')")
    assert page.locator("svg.ico").count() == 3       # Email, GitHub, Bluesky, and nothing else


def test_no_theme_filters_the_card_figure(browser):
    """A figure is printed as it is, in both themes. What answers the dark page is the mount: a light card
    behind the print, which has to stay light in either theme and has to be told apart from the sheet it is
    pasted on, or there is no mount to see."""
    for scheme in ("light", "dark"):
        page, _, _ = _page(browser, 1280, scheme)
        assert page.evaluate("getComputedStyle(document.querySelector('.paper__fig img')).filter") == "none"
        mat, card = page.evaluate("""() => [
            getComputedStyle(document.querySelector('.paper__plot')).backgroundColor,
            getComputedStyle(document.querySelector('.paper')).backgroundColor]""")
        assert _luminance(mat) > 0.45, (scheme, mat)            # a light mount, not a dark one
        assert _contrast(mat, card) >= 1.1, (scheme, mat, card)  # and visibly not the sheet


def test_the_research_card_on_a_phone_puts_the_figure_under_the_title(browser):
    """Beside the title the figure cut the measure to about two dozen characters; above it, it opened the card
    with a picture. Below the byline the title gets the column back and the plate keeps its own width."""
    page, _, _ = _page(browser, 400)
    box = page.evaluate("""() => {
      const card = document.querySelector('.paper'), r = s => card.querySelector(s).getBoundingClientRect();
      const pad = parseFloat(getComputedStyle(card).paddingLeft);
      const measure = card.getBoundingClientRect().width - 2 * pad;
      return {title: r('h3'), fig: r('.paper__fig'), tldr: r('.tldr'), measure: measure};
    }""")
    assert box["title"]["bottom"] <= box["fig"]["top"]                  # the title is above it, not beside it
    assert box["fig"]["bottom"] <= box["tldr"]["top"]                   # and the summary below it
    assert box["title"]["width"] > 0.95 * box["measure"]                # the title has the full measure back
    assert 200 <= box["fig"]["width"] <= 216                            # a 13rem plate, not the whole column
    assert box["fig"]["left"] == pytest.approx(box["title"]["left"], abs=1)


def test_body_text_is_at_least_16px(browser):
    page, _, _ = _page(browser, 400)
    assert page.evaluate("parseFloat(getComputedStyle(document.querySelector('main p')).fontSize)") >= 16


# --- Contrast: WCAG AA in both themes ---

# The kinds of text the plan names. Every painted text on the page is measured; each of these must be among them.
CONTRAST_TARGETS = {
    "body paragraph": ".bio p",
    "link": ".links a",
    "muted date": ".log .when, .log .when time",
    "badge": ".tag",
    "filled button": ".btn--primary",
}

# Every element of the body that holds text of its own: its colour, the first background that is not transparent
# on the way up from it, its font size, and which of the targets (name -> selector) it matches.
PAINTED_TEXT_JS = """
(targets) => {
  const painted = [];
  for (const el of document.body.querySelectorAll('*')) {
    if (el.closest('script, style, svg')) continue;
    const text = [...el.childNodes].filter(n => n.nodeType === Node.TEXT_NODE).map(n => n.textContent).join(' ').trim();
    if (!text) continue;
    let background = null;
    for (let n = el; n && !background; n = n.parentElement) {
      const c = getComputedStyle(n).backgroundColor;
      if (c !== 'rgba(0, 0, 0, 0)' && c !== 'transparent') background = c;
    }
    const style = getComputedStyle(el);
    painted.push({
      what: '<' + el.tagName.toLowerCase() + (el.className ? ' class="' + el.className + '"' : '') + '> ' + text.slice(0, 40),
      color: style.color, background: background, size: parseFloat(style.fontSize),
      kinds: Object.keys(targets).filter(kind => el.matches(targets[kind])),
    });
  }
  return painted;
}
"""


def _rgb(css):
    match = re.fullmatch(r"rgba?\((\d+), (\d+), (\d+)(?:, ([\d.]+))?\)", css or "")
    if not match or float(match.group(4) or 1) != 1:
        raise ValueError(f"expected an opaque rgb() colour, got {css!r}: translucent colours would have to be blended first")
    return tuple(int(v) for v in match.groups()[:3])


def _luminance(css):
    """Relative luminance, WCAG 2: https://www.w3.org/TR/WCAG22/#dfn-relative-luminance"""
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in (v / 255 for v in _rgb(css))]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(colour, background):
    lighter, darker = sorted((_luminance(colour), _luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def test_contrast_ratio_formula():
    white = "rgb(255, 255, 255)"
    assert _contrast("rgb(0, 0, 0)", white) == pytest.approx(21.0)
    assert _contrast(white, "rgb(0, 0, 0)") == pytest.approx(21.0)             # the order does not matter
    assert _contrast(white, white) == pytest.approx(1.0)
    assert _contrast("rgb(118, 118, 118)", white) == pytest.approx(4.54, abs=0.005)   # #767676: the lightest grey that passes on white
    assert _contrast("rgb(119, 119, 119)", white) < 4.5                         # #777777 just misses
    assert _contrast("rgba(0, 0, 0, 1)", white) == pytest.approx(21.0)


@pytest.mark.parametrize("colour", ["rgba(0, 0, 0, 0.5)", "color(srgb 0.1 0.2 0.3)", "transparent", None])
def test_contrast_refuses_colours_it_cannot_measure(colour):
    with pytest.raises(ValueError):
        _contrast(colour, "rgb(255, 255, 255)")


@pytest.mark.parametrize("theme, by_toggle", [("light", False), ("dark", False), ("light", True), ("dark", True)])
def test_text_contrast_meets_wcag_aa(browser, theme, by_toggle):
    """Each theme is reached both ways: from the system setting, and by the toggle from the opposite system setting."""
    other = "dark" if theme == "light" else "light"
    page, _, _ = _page(browser, 1280, other if by_toggle else theme)
    if by_toggle:
        page.click("#theme-toggle")
        page.wait_for_timeout(300)                                              # colour transitions take 120ms
    chosen = page.evaluate("document.documentElement.getAttribute('data-theme')")
    assert chosen == (theme if by_toggle else None)                             # no choice is left over from another test
    assert page.evaluate("getComputedStyle(document.documentElement).colorScheme") == theme   # the theme being measured
    painted = page.evaluate(PAINTED_TEXT_JS, CONTRAST_TARGETS)
    too_faint = []
    for t in painted:
        ratio, needed = _contrast(t["color"], t["background"]), 4.5 if t["size"] < 24 else 3.0
        if ratio < needed:
            too_faint.append(f'{ratio:.2f} < {needed}: {t["what"]} ({t["color"]} on {t["background"]}, {t["size"]}px)')
    assert too_faint == []
    assert {kind for t in painted for kind in t["kinds"]} == set(CONTRAST_TARGETS)
