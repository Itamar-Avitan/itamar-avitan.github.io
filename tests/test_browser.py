import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SITE = yaml.safe_load((ROOT / "site.yaml").read_text(encoding="utf-8"))
SLUGS = [page["slug"] for page in SITE["pages"]]                 # every page built, the colophon one included
NAV_SLUGS = [page["slug"] for page in SITE["pages"] if page.get("nav")]   # the four the strip carries
NAV = [page["nav"] for page in SITE["pages"] if page.get("nav")]
COLOPHON = [page["colophon"] for page in SITE["pages"] if page.get("colophon")]
WIDTHS = [320, 400, 768, 1280]                                   # 320px is the narrowest viewport supported
# The reader's own text size, as a root font size in px: 16 is the browser default, 32 is 200%. This axis is
# here because it was missing -- the overflow test below only ever ran at the default size, and so it passed
# on a site that ran 231px past a 320px viewport as soon as anyone enlarged the text. That is WCAG 1.4.4
# (Resize Text) and 1.4.10 (Reflow), two of the criteria /accessibility/ claims. iOS and Android "Larger
# Text" and Chrome's Medium/Large settings land inside this band: ordinary readers, not an edge case.
TEXT_SIZES = [16, 20, 24, 28, 32]


@pytest.fixture(scope="module")
def browser():
    assert subprocess.run([sys.executable, str(ROOT / "build.py")]).returncode == 0
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def _page(browser, width, scheme="light", slug=""):
    ctx = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
    page = ctx.new_page()
    requests, errors = [], []
    page.on("request", lambda r: requests.append(r.url))
    page.on("requestfailed", lambda r: errors.append(f"failed {r.url}"))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.goto((ROOT / slug / "index.html").as_uri())
    page.wait_for_timeout(300)
    return page, requests, errors


@pytest.mark.parametrize("slug", SLUGS)
@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("text", TEXT_SIZES)
def test_no_horizontal_overflow(browser, width, text, slug):
    page, _, _ = _page(browser, width, slug=slug)
    page.evaluate(f"document.documentElement.style.fontSize = '{text}px'")
    page.wait_for_timeout(50)
    over = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert over <= 0, f"{slug or 'home'} at {width}px, {text}px text: {over}px past the viewport"


@pytest.mark.parametrize("slug", SLUGS)
def test_only_local_requests_and_no_errors(browser, slug):
    _, requests, errors = _page(browser, 1280, slug=slug)
    assert [u for u in requests if not u.startswith("file://") and not u.startswith("data:")] == []
    assert errors == []


@pytest.mark.parametrize("slug", SLUGS)
def test_theme_toggle_switches_and_persists(browser, slug):
    page, _, _ = _page(browser, 1280, "light", slug)
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


@pytest.mark.parametrize("slug", SLUGS)
def test_email_link_is_built_in_the_browser(browser, slug):
    page, _, _ = _page(browser, 1280, slug=slug)
    assert page.get_attribute("a#email", "href").startswith("mailto:avitanit@")
    assert "[at]" in page.inner_text("a#email")


@pytest.mark.parametrize("slug", SLUGS)
def test_structure_and_alt_text(browser, slug):
    page, _, _ = _page(browser, 1280, slug=slug)
    heading = next(p.get("heading") for p in SITE["pages"] if p["slug"] == slug) or SITE["name"]
    assert page.locator("h1").count() == 1 and page.inner_text("h1").strip() == heading
    # every image loads and has an alt attribute; only the portrait's is empty -- it stands beside the name,
    # and a description would make a screen reader say the name twice (deep review 2026-09-25, PA-05)
    assert page.evaluate("[...document.images].every(i => i.hasAttribute('alt') && i.complete && i.naturalWidth > 0)")
    assert page.evaluate("[...document.images].filter(i => !i.alt).every(i => i.matches('.byline__who img, .masthead img'))")
    assert page.evaluate("[...document.images].filter(i => i.matches('.byline__who img, .masthead img')).map(i => i.alt)") == [""]
    assert page.evaluate("[...document.querySelectorAll('svg.ico')].every(s => s.getAttribute('aria-hidden') === 'true')")
    # Email, GitHub, Bluesky, and nothing else -- GitHub twice at home, beside the address and in the colophon
    assert page.locator("svg.ico").count() == (4 if slug == "" else 3)
    assert page.locator("main section").count() >= 1


@pytest.mark.parametrize("slug", NAV_SLUGS)
def test_the_navigation_reaches_every_page_without_javascript(browser, slug):
    """Ordinary links: a browser with scripting off follows them, and each one lands on a page whose own strip
    marks it as the one you are on."""
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, java_script_enabled=False)
    page = ctx.new_page()
    page.goto((ROOT / slug / "index.html").as_uri())
    assert page.locator(".sitenav a").count() == len(NAV) + 1           # the four pages and the CV
    assert [t.strip() for t in page.locator(".sitenav a").all_inner_texts()] == NAV + ["CV"]
    current = page.locator('.sitenav a[aria-current="page"]')
    assert current.count() == 1
    assert current.inner_text().strip() == next(p["nav"] for p in SITE["pages"] if p["slug"] == slug)
    for index, other in enumerate(NAV_SLUGS):
        # the address each link resolves to is the folder the page is served from; a server answers it with
        # that folder's index.html, which a file:// URL does not, so the page itself is opened directly after
        resolved = page.locator(".sitenav a").nth(index).evaluate("a => a.href")
        assert resolved == (ROOT / other).as_uri().rstrip("/") + "/", (slug, other)
        page.goto((ROOT / other / "index.html").as_uri())
        assert page.locator('.sitenav a[aria-current="page"]').inner_text().strip() == NAV[index]
        page.goto((ROOT / slug / "index.html").as_uri())
    ctx.close()


@pytest.mark.parametrize("slug", SLUGS)
def test_the_cv_link_resolves_to_the_file_that_is_published(browser, slug):
    page, _, _ = _page(browser, 1280, slug=slug)
    href = page.get_attribute(".sitenav__cv a", "href")
    assert href == ("cv.pdf" if slug == "" else "../cv.pdf")
    assert Path(page.evaluate("document.querySelector('.sitenav__cv a').href").removeprefix("file://")).exists()


@pytest.mark.parametrize("slug", SLUGS[1:])
def test_a_deep_landing_still_says_whose_site_it_is(browser, slug):
    """The name, the role line and the portrait at the top; the address and the profiles in the colophon."""
    page, _, _ = _page(browser, 1280, slug=slug)
    assert SITE["name"] in page.inner_text(".byline")
    assert "PhD candidate" in page.inner_text(".byline .role")
    assert page.locator(".byline picture img").count() == 1
    assert page.locator("footer .links-nav").count() == 1
    assert page.locator(".masthead").count() == 0                       # and the bio is not repeated here


def test_no_theme_filters_the_card_figure(browser):
    """A figure is printed as it is, in both themes. What answers the dark page is the mount: a light card
    behind the print, which has to stay light in either theme and has to be told apart from the sheet it is
    pasted on, or there is no mount to see. The light theme's mount used to be 1.18:1 against its white card:
    present in the stylesheet, invisible on the page, which left the figure a mounted print in one theme and
    a picture lying straight on the sheet in the other. Both themes mount it now."""
    for scheme in ("light", "dark"):
        page, _, _ = _page(browser, 1280, scheme, "research/")
        assert page.evaluate("getComputedStyle(document.querySelector('.paper__fig img')).filter") == "none"
        mat, card = page.evaluate("""() => [
            getComputedStyle(document.querySelector('.paper__plot')).backgroundColor,
            getComputedStyle(document.querySelector('.paper')).backgroundColor]""")
        assert _luminance(mat) > 0.45, (scheme, mat)             # a light mount, not a dark one
        assert _contrast(mat, card) >= 1.3, (scheme, mat, card)  # and seen, not merely declared


HIT_TEST_JS = """
(selector) => [...document.querySelectorAll(selector)].map(a => {
  // elementFromPoint answers for the viewport only, and the profile links stand in the colophon at the foot
  // of the page, so each target is brought into view before it is pointed at. "instant" because the
  // stylesheet asks for smooth scrolling and a rect read mid-animation is a rect of nowhere in particular.
  a.scrollIntoView({block: "center", inline: "center", behavior: "instant"});
  const r = a.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2;
  const hit = (dx, dy) => { const el = document.elementFromPoint(x + dx, y + dy);
                            return !!(el && el.closest('a') === a); };
  return {label: a.textContent.trim(), width: +r.width.toFixed(1), height: +r.height.toFixed(1),
          covered: [[-11.5, -11.5], [11.5, -11.5], [-11.5, 11.5], [11.5, 11.5]].every(c => hit(...c))};
})
"""


# The profile links in document order: at home the reach line under the name (the address, then the two
# picked profiles) and the colophon row; on every other page the colophon alone, address first.
HOME_TARGETS = ["avitanit [at] post.bgu.ac.il", "GitHub", "Google Scholar",
                "GitHub", "Bluesky", "Google Scholar", "LinkedIn", "X", "ORCID"]
DEEP_TARGETS = ["avitanit [at] post.bgu.ac.il", "GitHub", "Bluesky", "Google Scholar", "LinkedIn", "X", "ORCID"]


@pytest.mark.parametrize("slug", SLUGS)
@pytest.mark.parametrize("width", WIDTHS)
def test_every_profile_link_is_at_least_a_24px_target(browser, width, slug):
    """WCAG 2.2 SC 2.5.8, at every viewport and not only on a phone: the desktop "X" was an 8.6 by 32 px
    target. A 23px box centred on each link has to land on that link at all four of its corners, which is
    what the overlay gives it without widening its box and opening a hole in the row. The two profiles the
    home page repeats beside the address are held to the same rule there."""
    page, _, _ = _page(browser, width, slug=slug)
    links = page.evaluate(HIT_TEST_JS, ".links a")
    assert [l["label"] for l in links] == (HOME_TARGETS if slug == "" else DEEP_TARGETS)
    assert [l for l in links if l["height"] < 24 or not l["covered"]] == []


@pytest.mark.parametrize("slug", SLUGS)
@pytest.mark.parametrize("width", WIDTHS)
def test_every_navigation_link_is_at_least_a_24px_target(browser, width, slug):
    """The strip is on every page and is how the site is used, so it is held to the same rule as the profile
    row: 24 by 24 CSS px, at every width, the CV chip included."""
    page, _, _ = _page(browser, width, slug=slug)
    links = page.evaluate(HIT_TEST_JS, ".sitenav a")
    assert [l["label"] for l in links] == NAV + ["CV"]
    assert [l for l in links if l["height"] < 24 or not l["covered"]] == []


def test_the_theme_toggle_is_invisible_until_the_script_unhides_it(browser):
    """A visible control that does nothing is worse than no control. The button is written hidden and the
    script unhides it, so a page whose script never ran simply does not offer it."""
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, java_script_enabled=False)
    page = ctx.new_page()
    page.goto((ROOT / "index.html").as_uri())
    assert page.locator("#theme-toggle").count() == 1
    assert not page.locator("#theme-toggle").is_visible()      # in the markup, not on the page
    assert page.locator("span#email").is_visible()             # and the address is still readable as text
    ctx.close()
    page, _, _ = _page(browser, 1280)                          # with the script, the control arrives
    assert page.locator("#theme-toggle").is_visible()


@pytest.mark.parametrize("slug", NAV_SLUGS)
@pytest.mark.parametrize("width", WIDTHS)
def test_the_current_page_tick_sits_on_the_strip_rule(browser, width, slug):
    """The tick that marks the page you are on is the section tick of the margin rule, one floor up: 2px of
    ink lying on the hairline under the strip. That is exactly true while the strip is one line, which it is
    from about 360px up -- and it is why the strip stacks the toggle above the links rather than beside them.
    Below that the five items wrap, the tick can no longer reach the hairline, and what it must do instead is
    stay an underline under its own link and keep off the line beneath it."""
    page, _, _ = _page(browser, width, slug=slug)
    measured = page.evaluate("""() => {
      const a = document.querySelector('.sitenav a[aria-current="page"]');
      const links = [...document.querySelectorAll('.sitenav a')];
      const t = getComputedStyle(a, '::before');
      const r = a.getBoundingClientRect();
      const bottom = r.bottom - parseFloat(t.bottom) + parseFloat(t.height);
      const lines = new Set(links.map(l => Math.round(l.getBoundingClientRect().top))).size;
      const below = links.filter(l => l !== a && l.getBoundingClientRect().top > r.bottom)
                         .map(l => l.getBoundingClientRect().top);
      return {bottom: bottom, rule: document.querySelector('.topstrip').getBoundingClientRect().bottom,
              lines: lines, nextLine: below.length ? Math.min(...below) : null};
    }""")
    assert parse_px(page.evaluate("getComputedStyle(document.querySelector('.sitenav a[aria-current=\\'page\\']'), '::before').height")) == 2
    if measured["lines"] == 1:
        assert abs(measured["bottom"] - measured["rule"]) <= 1.5, (width, slug, measured)
    else:
        assert measured["nextLine"] is None or measured["bottom"] < measured["nextLine"], (width, slug, measured)


def test_the_research_card_on_a_phone_puts_the_figure_under_the_title(browser):
    """Beside the title the figure cut the measure to about two dozen characters; above it, it opened the card
    with a picture. Below the byline the title gets the column back and the plate keeps its own width."""
    page, _, _ = _page(browser, 400, slug="research/")
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


def test_the_laptop_gets_a_composition_of_its_own(browser):
    """Above 45rem the page used to be finished: an 824px block centred on every wider screen, so the only
    thing 600px of laptop did was push the same column further from both edges -- 308px of empty paper on
    each side at 1440. The page and the type step up together at 64rem, so the measure stays near the same
    number of characters rather than the same number of pixels."""
    narrow, _, _ = _page(browser, 1023)
    wide, _, _ = _page(browser, 1280)
    block = "document.querySelector('.page').getBoundingClientRect().width"
    size = "parseFloat(getComputedStyle(document.body).fontSize)"
    assert narrow.evaluate(block) == 824 and narrow.evaluate(size) == 17
    assert wide.evaluate(block) == 976 and wide.evaluate(size) == 18
    # the measure in ems, which is what a reader actually feels, moves by less than a tenth
    ems = lambda p: p.evaluate("document.querySelector('.prose p').getBoundingClientRect().width") / p.evaluate(size)
    assert abs(ems(wide) - ems(narrow)) < 3, (ems(narrow), ems(wide))
    # and the navigation strip keeps its own scale, because the tick that marks the current page is placed
    # by the strip's padding and lands on its hairline: a taller strip link moves the tick off the rule
    nav = "parseFloat(getComputedStyle(document.querySelector('.sitenav a')).fontSize)"
    assert wide.evaluate(nav) == narrow.evaluate(nav) == 13


@pytest.mark.parametrize("width", [320, 400])
def test_a_phone_meets_the_name_before_the_preferences(browser, width):
    """The strip used to stack, which put the theme toggle on the first line of the page -- above the
    navigation and above his name. The strip is one row now: the nav keeps the line and wraps inside it, and
    the toggle is a 44px square holding only its mark. The label stays in the DOM, because it is the button's
    accessible name and the script writes to it.

    What this buys is order at every width, and height where it was worst: the strip measures 110.6px at 320
    against the stacked 156.6, and 108.8 against 105.8 at 400, where the five items used to fit on one line
    precisely because the toggle had taken a row of its own above them. Three pixels there for forty-six
    here, and on both the first thing met is the way around the site rather than a preference."""
    page, _, _ = _page(browser, width)
    box = page.evaluate("""() => {
      const t = document.querySelector('#theme-toggle').getBoundingClientRect();
      const nav = document.querySelector('.sitenav a').getBoundingClientRect();
      const label = document.querySelector('#theme-toggle span');
      return {toggleTop: t.top, toggleW: t.width, toggleH: t.height, navTop: nav.top,
              strip: document.querySelector('.topstrip').getBoundingClientRect().height,
              painted: label.getBoundingClientRect().width, text: label.textContent};
    }""")
    assert abs(box["toggleTop"] - box["navTop"]) <= 1             # beside the navigation, not above it
    assert box["toggleW"] >= 44 and box["toggleH"] >= 44          # still a finger-sized target
    assert box["painted"] <= 1 and box["text"] == "Use dark theme"
    assert page.get_attribute("#theme-toggle", "aria-label") is None      # the span is the accessible name
    assert box["strip"] <= 112, box["strip"]


@pytest.mark.parametrize("width", [768, 1280])
def test_the_portrait_fills_the_gutter_it_sits_in(browser, width):
    """It was 120px right-aligned inside a 136px gutter, so its left edge stood 16px inside the page's own
    edge and read as a wobble against the first word of the navigation above it. It fills the gutter now:
    left edge on the page edge, right edge on the margin rule, like a plate pasted into the margin."""
    page, _, _ = _page(browser, width)
    left = page.evaluate("""() => [document.querySelector('.masthead picture').getBoundingClientRect().left,
                                   document.querySelector('.sitenav a').getBoundingClientRect().left,
                                   document.querySelector('.masthead picture').getBoundingClientRect().width]""")
    assert left[0] == pytest.approx(left[1], abs=0.5), left
    assert left[2] == (136 if width < 1024 else 192), left


def test_the_commonplace_sets_its_quotations_above_the_prose(browser):
    """The page exists for the quotations, and they were set at body size -- barely larger than the note
    under them, which left the loudest thing on the page the word "Commonplace". One step up, still well
    below the page's own title: a quotation is not a heading. The phone keeps body size; the measure there
    cannot afford the step."""
    sizes = {}
    for width in (400, 800, 1280):
        page, _, _ = _page(browser, width, slug="commonplace/")
        sizes[width] = page.evaluate("""() => [
            parseFloat(getComputedStyle(document.querySelector('.quote blockquote')).fontSize),
            parseFloat(getComputedStyle(document.body).fontSize),
            parseFloat(getComputedStyle(document.querySelector('h1')).fontSize),
            parseFloat(getComputedStyle(document.querySelector('.quote__note')).fontSize)]""")
    quote, body, h1, note = sizes[1280]
    assert sizes[400][0] == sizes[400][1] == 17          # the phone is as it was
    assert sizes[800][0] == 20 and quote == 22
    assert body < quote < h1 and note < body


@pytest.mark.parametrize("slug", SLUGS)
def test_body_text_is_at_least_16px(browser, slug):
    page, _, _ = _page(browser, 400, slug=slug)
    assert page.evaluate("parseFloat(getComputedStyle(document.querySelector('main p')).fontSize)") >= 16


CEILINGS = {"research/": ((1280, 3600), (400, 5200))}
DEFAULT_CEILINGS = ((1280, 3000), (400, 4000))


@pytest.mark.parametrize("slug", SLUGS)
def test_no_page_is_anywhere_near_as_long_as_the_old_single_page(browser, slug):
    """The single page stood at 4383px on a laptop and 6548px on a phone, which is what the owner was
    objecting to. Splitting it is only worth doing if the pieces stay short. The research page is the one
    page allowed to run long: it exists to go deep, which is the owner's first priority for the site, and
    even so it stays 18-21% under the single page he objected to. Measured after the 2026-09 polish
    (WP-S3, with the diagram and Figure 1D on the card): 3370px at 1280 and 4822px at 400."""
    for width, ceiling in CEILINGS.get(slug, DEFAULT_CEILINGS):
        page, _, _ = _page(browser, width, slug=slug)
        height = page.evaluate("document.documentElement.scrollHeight")
        assert height < ceiling, (slug, width, height)


# --- Contrast: WCAG AA in both themes ---

# The kinds of text the plan names. Every painted text on the page is measured; each of these must be among them.
CONTRAST_TARGETS = {
    "body paragraph": ".prose p, .page-intro",
    "link": ".links a",
    "navigation link": ".sitenav a:not(.btn)",
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
    """Each theme is reached both ways: from the system setting, and by the toggle from the opposite system
    setting -- and on every page, because each page paints a different part of the palette."""
    other = "dark" if theme == "light" else "light"
    seen = set()
    for slug in SLUGS:
        page, _, _ = _page(browser, 1280, other if by_toggle else theme, slug)
        if by_toggle:
            page.click("#theme-toggle")
            page.wait_for_timeout(300)                                          # colour transitions take 120ms
        chosen = page.evaluate("document.documentElement.getAttribute('data-theme')")
        assert chosen == (theme if by_toggle else None), slug                   # no choice left over from another test
        assert page.evaluate("getComputedStyle(document.documentElement).colorScheme") == theme
        painted = page.evaluate(PAINTED_TEXT_JS, CONTRAST_TARGETS)
        too_faint = []
        for t in painted:
            ratio, needed = _contrast(t["color"], t["background"]), 4.5 if t["size"] < 24 else 3.0
            if ratio < needed:
                too_faint.append(f'{slug} {ratio:.2f} < {needed}: {t["what"]} ({t["color"]} on {t["background"]}, {t["size"]}px)')
        assert too_faint == []
        seen |= {kind for t in painted for kind in t["kinds"]}
    assert seen == set(CONTRAST_TARGETS)            # every kind of text the plan names was actually measured


@pytest.mark.parametrize("slug", SLUGS)
def test_focus_is_visible_on_every_control(browser, slug):
    """Keyboard use has to be seen. Every link and the toggle draw the same 2px accent ring when focused."""
    page, _, _ = _page(browser, 1280, slug=slug)
    outlines = page.evaluate("""() => {
      const out = [];
      for (const el of document.querySelectorAll('a, button')) {
        el.focus();
        const s = getComputedStyle(el);
        out.push({what: el.textContent.trim().slice(0, 24), width: s.outlineWidth, style: s.outlineStyle});
      }
      return out;
    }""")
    assert outlines and [o for o in outlines if o["style"] == "none" or parse_px(o["width"]) < 2] == []


def parse_px(value):
    return float(value.replace("px", "") or 0)


# --- The accessibility statement: its own page, linked from the colophon of every page ---------------------

@pytest.mark.parametrize("slug", SLUGS)
def test_the_accessibility_statement_is_one_click_from_every_page(browser, slug):
    """The owner asked for a statement reachable from every page. It is a page of its own and the link to it
    stands in the colophon, not in the navigation strip -- see the note over `pages` in site.yaml. Here that
    is checked where it matters: the link is on the page, it resolves to a file that exists, and following it
    lands on the statement."""
    page, _, _ = _page(browser, 1280, slug=slug)
    links = page.locator(".colophon-nav a")
    assert links.count() == len(COLOPHON) == 1
    assert [t.strip() for t in links.all_inner_texts()] == COLOPHON
    assert page.locator('.sitenav a:text-is("Accessibility")').count() == 0     # and not in the strip
    href = page.get_attribute(".colophon-nav a", "href")
    assert href == ("accessibility/" if slug == "" else "../accessibility/")
    target = Path(page.evaluate("document.querySelector('.colophon-nav a').href").removeprefix("file://"))
    assert (target / "index.html").exists() or target.exists(), (slug, target)


@pytest.mark.parametrize("width", WIDTHS)
def test_the_colophon_link_is_at_least_a_24px_target(browser, width):
    """WCAG 2.2 SC 2.5.8, and the page itself says links are held to it, so this one is too. Its box is only
    about 21px tall at this type size, so it gets the overlay the profile row gets."""
    page, _, _ = _page(browser, width)
    links = page.evaluate(HIT_TEST_JS, ".colophon-nav a")
    assert [l["label"] for l in links] == COLOPHON
    assert [l for l in links if l["height"] < 24 or not l["covered"]] == []


def test_the_statement_marks_itself_as_the_page_you_are_on(browser):
    """A page the strip does not carry marks nothing there, so the colophon row has to say where you are."""
    page, _, _ = _page(browser, 1280, slug="accessibility/")
    assert page.locator('.sitenav a[aria-current="page"]').count() == 0
    assert page.locator('.colophon-nav a[aria-current="page"]').inner_text().strip() == "Accessibility"
    other, _, _ = _page(browser, 1280, slug="research/")
    assert other.locator('.colophon-nav a[aria-current="page"]').count() == 0


def test_the_statement_says_the_aim_the_checks_the_limits_and_how_to_report(browser):
    """The four things the owner asked it to state. The address is deliberately NOT repeated on this page:
    the reporting section points at the colophon, which carries it on every page as "name [at] host"."""
    page, _, _ = _page(browser, 1280, slug="accessibility/")
    text = page.inner_text("main")
    assert "WCAG 2.1 Level AA" in text
    assert "not a certificate" in text and "nobody but me has audited" in text
    assert "I write it and keep it working" in text            # a personal site, maintained by one person,
                                                               # said in the first person like the rest of the page
    assert "has not been tested with a screen reader" in text  # and what is therefore not claimed
    assert "at the top of the home page" in text                       # how to report, without printing an address
    # the statement itself carries no address; the colophon below it does, as it does on every page
    main_html = page.eval_on_selector("main", "el => el.outerHTML")
    assert "@" not in text and "mailto:" not in main_html
    assert page.locator("main #email").count() == 0 and page.locator("footer a#email").count() == 1
    assert [h.strip() for h in page.locator("main h2").all_inner_texts()] == [
        "The aim", "Reporting a problem", "What was done", "What is not claimed"]
    # no legal claim of any kind: this page states no obligation and cites no law
    for word in ("law", "legal", "legally", "required by", "Section 508", "directive", "compliance"):
        assert word.lower() not in text.lower(), word


def test_the_way_to_report_a_problem_is_on_the_first_screen(browser):
    """The GOV.UK model's order puts feedback and contact directly after the opening statement of how
    accessible the site is; here that brings "Reporting a problem" from 2.8 screens down to the first one
    (deep review 2026-09-25, FL-09: heading bottom 2270 -> 668 at 1280x800)."""
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    page = ctx.new_page()
    page.goto((ROOT / "accessibility" / "index.html").as_uri())
    bottom = page.evaluate("document.querySelector('#report-h').getBoundingClientRect().bottom")
    assert bottom <= 700, bottom
    ctx.close()


# The entry a permalink landed on: the targeted row's box, its panel (the background plus the box-shadow's
# spread, which is the panel's padding), the boxes the panel has to enclose (the date, and the first glyph of
# the quotation, which hangs into the margin) and the ones it has to keep out of (the rows either side and
# the viewport's edges), and the colours painted on it.
TARGET_MARK_JS = r"""
() => {
  const rows = [...document.querySelectorAll('.row:target')];
  if (rows.length !== 1) return {count: rows.length};
  const row = rows[0], p = row.querySelector('blockquote p');
  const text = document.createTreeWalker(p, NodeFilter.SHOW_TEXT).nextNode();
  const glyph = document.createRange(); glyph.setStart(text, 0); glyph.setEnd(text, 1);
  const box = el => { const b = el.getBoundingClientRect(); return {left: b.left, top: b.top, right: b.right, bottom: b.bottom}; };
  const style = getComputedStyle(row);
  const spread = parseFloat((style.boxShadow.match(/(-?[\d.]+)px\s*$/) || [0, 0])[1]);
  const color = sel => getComputedStyle(row.querySelector(sel)).color;
  return {count: 1, row: box(row), when: box(row.querySelector('.when')), glyph: box(glyph), spread: spread,
          background: style.backgroundColor, page: getComputedStyle(document.body).backgroundColor,
          neighbours: [row.previousElementSibling, row.nextElementSibling].filter(Boolean).map(box),
          viewport: document.documentElement.clientWidth,
          inks: {quotation: color('blockquote p'), source: color('.quote__by'), note: color('.quote__note'),
                 date: color('.when'), link: color('.quote__link')}};
}
"""


@pytest.mark.parametrize("width", WIDTHS)
def test_every_quotation_permalink_is_a_24px_target_and_marks_its_entry(browser, width):
    """The "§" at the end of each attribution is not a link inside a sentence, so the 24px rule applies to it
    (WCAG 2.2 SC 2.5.8); and following one lands on the entry it names, which stands on a panel of the accent
    tint. The panel is measured, not read off the stylesheet, because the plan's 2px ring around .what was
    found running through the quotation's hanging opening mark on a laptop and across the date on a phone
    (review of WP-S4): the mark has to enclose the date and the first glyph, stay inside the row's own air
    and inside the viewport, be a colour of its own, and keep every text on it at 4.5:1, in both themes."""
    page, _, _ = _page(browser, width, slug="commonplace/")
    links = page.evaluate(HIT_TEST_JS, ".quote__link")
    assert [l["label"] for l in links] == ["§"] * len(SITE["quotes"])
    assert [l for l in links if l["height"] < 24 or l["width"] < 24 or not l["covered"]] == []
    for scheme in ("light", "dark"):
        for q in SITE["quotes"]:
            page, _, _ = _page(browser, width, scheme, "commonplace/")
            page.goto((ROOT / "commonplace" / "index.html").as_uri() + "#" + q["slug"])
            page.wait_for_timeout(100)
            m = page.evaluate(TARGET_MARK_JS)
            where = (width, scheme, q["slug"])
            assert m["count"] == 1, where
            assert 8 <= m["spread"] <= 12, where                              # 0.625rem: a panel, not a hairline
            panel = {"left": m["row"]["left"] - m["spread"], "top": m["row"]["top"] - m["spread"],
                     "right": m["row"]["right"] + m["spread"], "bottom": m["row"]["bottom"] + m["spread"]}
            for name in ("when", "glyph"):                                  # enclosed, with 2px to spare
                inner = m[name]
                assert (panel["left"] + 2 <= inner["left"] and inner["right"] <= panel["right"] - 2
                        and panel["top"] + 2 <= inner["top"] and inner["bottom"] <= panel["bottom"] - 2), (where, name, panel, inner)
            for other in m["neighbours"]:                                    # in its own air: clear of the rows either side
                assert other["bottom"] <= panel["top"] or panel["bottom"] <= other["top"], (where, panel, other)
            assert panel["left"] >= 4 and panel["right"] <= m["viewport"] - 4, (where, panel)
            assert m["background"] != m["page"] and _contrast(m["background"], m["page"]) >= 1.15, where   # seen
            for kind, ink in m["inks"].items():
                assert _contrast(ink, m["background"]) >= 4.5, (where, kind, ink, m["background"])


# The first word of each line of every attribution, at the widths the layout and the type change at and the
# ones between them, where the wrap moves one word at a time.
LINE_STARTS_JS = r"""
() => [...document.querySelectorAll('.quote__by')].map(p => {
  const walker = document.createTreeWalker(p, NodeFilter.SHOW_TEXT), words = [];
  for (let node; (node = walker.nextNode());) {
    for (let m, re = /\S+/g; (m = re.exec(node.data));) {
      const r = document.createRange(); r.setStart(node, m.index); r.setEnd(node, m.index + m[0].length);
      const rect = r.getClientRects()[0];
      if (rect) words.push({word: m[0], top: Math.round(rect.top)});
    }
  }
  const lines = [];
  for (const w of words) {
    const last = lines[lines.length - 1];
    if (last && Math.abs(last.top - w.top) < 4) last.words.push(w.word); else lines.push({top: w.top, words: [w.word]});
  }
  return lines.map(l => l.words);
})
"""


@pytest.mark.parametrize("width", sorted(set(WIDTHS) | {360, 390, 430, 1024, 1440}))
def test_no_attribution_line_opens_on_a_numeral_a_date_or_a_dash(browser, width):
    """"Part I, Chapter I" split as "Chapter | I," on a laptop once the permalink made the Watson attribution
    three lines, and the lone "I" read as the pronoun (review of WP-S4). The numerals are tied to their nouns
    with U+00A0, each work's year to its title (the Snape line opened one on "(2003)," at 320 and 768), Pope's
    title to its span, and the dash to the word before it, so no line of any attribution opens on a Roman
    numeral, on a year, on a piece of the Essay's title, or on an em dash."""
    page, _, _ = _page(browser, width, slug="commonplace/")
    for lines in page.evaluate(LINE_STARTS_JS):
        for line in lines[1:]:
            first = line[0]
            assert not re.fullmatch(r"[IVXLC]+[,.;:]?", first), (width, line)
            assert not re.match(r"\(\d", first) and first not in ("Essay", "on", "Man", "—"), (width, line)
