import copy
import http.server
import io
import math
import re
import subprocess
import sys
import threading
from pathlib import Path

import pytest
import yaml
from PIL import Image
from playwright.sync_api import sync_playwright

try:
    import pypdf                       # only for counting the pages of a print; the print itself needs nothing
except ImportError:                    # pragma: no cover
    pypdf = None

import build

ROOT = Path(__file__).resolve().parents[1]
SITE = yaml.safe_load((ROOT / "site.yaml").read_text(encoding="utf-8"))
SLUGS = [page["slug"] for page in SITE["pages"]]                 # every page built, the colophon one included
NAV_SLUGS = [page["slug"] for page in SITE["pages"] if page.get("nav")]   # the four the strip carries
NAV = [page["nav"] for page in SITE["pages"] if page.get("nav")]
COLOPHON = [page["colophon"] for page in SITE["pages"] if page.get("colophon")]
# The CV chip's accessible name: the visible "CV" and then, hidden, what opens -- "CV (PDF, 2 pages)".
CV_LINK = next(l for l in SITE["links"] if l["label"] == "CV")
CV_NAME = f'{CV_LINK["label"]} ({CV_LINK["format"]})'
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
    # innerText puts a line break before the chip's hidden span, so the names are compared whitespace-normalised
    assert [" ".join(t.split()) for t in page.locator(".sitenav a").all_inner_texts()] == NAV + [CV_NAME]
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


class _LikeGitHubPages(http.server.SimpleHTTPRequestHandler):
    """Serves the built site the way the host does: an address with nothing at it gets 404.html, status 404."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, *args):
        pass

    def send_error(self, code, message=None, explain=None):
        page = ROOT / "404.html"
        if code != 404 or not page.exists():
            return super().send_error(code, message, explain)
        body = page.read_bytes()
        self.send_response(404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def served():
    """The built site on a local port, for the one page that cannot be opened off the disk (its links are
    root-absolute, because it is served at every depth)."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _LikeGitHubPages)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


# The lines an element's text is set in, read off the client rects of each character.
_LINES_OF = """
(el) => {
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  const lines = []; let node, prevTop = null, line = '';
  while ((node = walker.nextNode())) {
    for (let i = 0; i < node.length; i++) {
      const r = document.createRange(); r.setStart(node, i); r.setEnd(node, i + 1);
      const rects = r.getClientRects(); if (!rects.length) continue;
      const top = Math.round(rects[0].top);
      if (prevTop !== null && Math.abs(top - prevTop) > 2) { lines.push(line); line = ''; }
      line += node.data[i]; prevTop = top;
    }
  }
  return line ? [...lines, line] : lines;
}
"""


@pytest.mark.parametrize("width, scheme", [(320, "light"), (390, "dark"), (1280, "light")])
def test_a_wrong_address_gets_the_sites_own_404_page(browser, served, width, scheme):
    """GitHub Pages answers a missing address with /404.html and status 404, at any depth, and used to answer
    with its own page: 980px wide, no viewport, no way back (deep review 2026-09-25, PA-02, MR-08). The
    site's own page is served here the way the host serves it and asked for two folders deep: the status
    stays 404, the stylesheet and the fonts arrive, nothing overflows at 100% or 200% text, and every link on
    it leads back to the root."""
    ctx = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
    page = ctx.new_page()
    requests, failed = [], []
    page.on("request", lambda r: requests.append(r.url))
    page.on("requestfailed", lambda r: failed.append(r.url))
    response = page.goto(f"{served}/nonexistent/deeper/")
    page.wait_for_timeout(300)
    assert response.status == 404
    assert failed == [] and all(u.startswith(served + "/") for u in requests), requests
    assert page.inner_text("h1").strip() == SITE["not_found"]["heading"]
    assert SITE["not_found"]["intro"] in page.inner_text("main")
    loaded = page.evaluate("[...document.fonts].filter(f => f.status === 'loaded').map(f => f.family.replace(/\"/g, ''))")
    assert "Fira Sans" in loaded and "Fira Mono" in loaded, loaded
    assert "Fira Sans" in page.evaluate("getComputedStyle(document.querySelector('h1')).fontFamily")
    for text in (16, 32):
        page.evaluate(f"document.documentElement.style.fontSize = '{text}px'")
        page.wait_for_timeout(50)
        over = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert over <= 0, f"404 page at {width}px, {text}px text: {over}px past the viewport"
        # the printed CV address breaks only where the template lets it: after "//", before a dot of the
        # host or after its slash -- never at "itamar-", which read as a hyphenation, and never mid-word
        lines = page.evaluate(_LINES_OF, page.query_selector("#not-found a"))
        for end, start in zip(lines, lines[1:]):
            assert end.endswith("/") or start.startswith("."), (width, text, lines)
    hrefs = page.evaluate("[...document.querySelectorAll('.sitenav a')].map(a => a.getAttribute('href'))")
    assert hrefs == ["/"] + [f"/{s}" for s in NAV_SLUGS[1:]] + ["/cv.pdf"]
    assert page.locator('.sitenav a[aria-current="page"]').count() == 0     # it is nowhere in the site
    assert page.get_attribute("#not-found a", "href") == "/cv.pdf"
    assert page.request.get(f"{served}/cv.pdf").status == 200               # and the CV is really there
    assert page.request.get(f"{served}/research/").status == 200
    assert page.request.get(f"{served}/favicon.ico").status == 200
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
    assert [l["label"] for l in links] == NAV + [CV_NAME]
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
    page.set_viewport_size({"width": 400, "height": 900})      # nor is the phone's, in the colophon
    assert page.locator(".colophon-nav .toggle--foot").count() == 1
    assert not page.locator(".colophon-nav .toggle--foot").is_visible()
    ctx.close()
    page, _, _ = _page(browser, 1280)                          # with the script, the control arrives
    assert page.locator("#theme-toggle").is_visible()


@pytest.mark.parametrize("slug", NAV_SLUGS)
@pytest.mark.parametrize("width", WIDTHS)
def test_the_current_page_tick_sits_on_the_strip_rule(browser, width, slug):
    """The tick that marks the page you are on is the section tick of the margin rule, one floor up: 2px of
    ink lying on the hairline under the strip. That is exactly true while the strip is one line, which it is
    from 356px up now that a phone's theme button stands in the colophon and the five items have the line
    to themselves (deep review 2026-09-25, PA-04). Below that the CV chip drops to a second line, the tick
    can no longer reach the hairline, and what it must do instead is stay an underline under its own link
    and keep off the line beneath it."""
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
    # The navigation strip takes the same one-pixel step as the gutter dates it is meant to match (13px beside
    # an 18px body was the one mono label left behind; deep review 2026-09-25, AS-19). The tick that marks the
    # current page is placed from its own link's box by the strip's padding, and the link is the strip's
    # tallest item at either size, so the tick's foot still lands on the strip's hairline: measured here at
    # both widths, because that is what used to be the reason for keeping the strip at 13px.
    nav = "parseFloat(getComputedStyle(document.querySelector('.sitenav a')).fontSize)"
    assert (narrow.evaluate(nav), wide.evaluate(nav)) == (13, 14)
    tick_foot = """() => {
      const a = document.querySelector('.sitenav a[aria-current="page"]');
      const t = getComputedStyle(a, '::before');
      const foot = a.getBoundingClientRect().bottom - parseFloat(t.bottom);   /* the tick's bottom edge */
      return foot - document.querySelector('.topstrip').getBoundingClientRect().bottom;
    }"""
    assert abs(narrow.evaluate(tick_foot)) <= 0.5 and abs(wide.evaluate(tick_foot)) <= 0.5


@pytest.mark.parametrize("width", WIDTHS)
def test_the_featured_papers_links_are_24px_targets_and_its_strip_keeps_its_order(browser, width):
    """The front door's "Paper · Code · Video · More on the research page" is set like the card's mono labels
    in a line of prose, and each link's box is padded to the 24px a target needs (WCAG 2.2 SC 2.5.8) without
    moving the line (WP-S17). The four marks under the paragraph read in order: one row of four from 45rem
    up, two rows of two below it -- never three and a stranded fourth."""
    page, _, _ = _page(browser, width)
    links = page.evaluate(HIT_TEST_JS, ".feature__links a")
    assert [l["label"].split(" ")[0] for l in links] == ["Paper", "Code", "Video", "More"]
    assert [l for l in links if l["height"] < 24 or not l["covered"]] == []
    tops = page.evaluate("[...document.querySelectorAll('.recovery-diagram--small li')].map(l => Math.round(l.getBoundingClientRect().top))")
    rows = sorted(set(tops))
    assert [tops.count(r) for r in rows] == ([4] if width >= 720 else [2, 2]), (width, tops)


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_first_screen_says_who_he_is_and_what_question_animates_his_work(browser, scheme):
    """At 1280 by 800, the commonest laptop screen, the front door shows the name under the mark, the role
    line, both sentences of the identity line -- the second set one step under the first, at the size the
    other pages' intros take -- the address, and the About heading with its whole first paragraph, which
    closes on the broader question (WP-S17: the page's one promise, external review 3 §21 and §28)."""
    ctx = browser.new_context(viewport={"width": 1280, "height": 800}, color_scheme=scheme)
    page = ctx.new_page()
    page.goto((ROOT / "index.html").as_uri())
    page.wait_for_timeout(300)
    box = page.evaluate("""() => { const r = s => document.querySelector(s).getBoundingClientRect().toJSON();
      return {mark: r('.mark--mast'), name: r('.masthead h1'), more: r('.lede__more'), email: r('#email'),
              about: r('#about-h'), first: r('#about .prose p')}; }""")
    assert box["first"]["bottom"] <= 800 and box["about"]["top"] < box["first"]["top"] < box["first"]["bottom"]
    assert box["more"]["bottom"] < box["email"]["top"] < box["about"]["top"]
    assert box["mark"]["bottom"] <= box["name"]["top"] and abs(box["mark"]["x"] - box["name"]["x"]) <= 3
    sizes = page.evaluate("""['.lede', '.lede__more'].map(s => parseFloat(getComputedStyle(document.querySelector(s)).fontSize))""")
    assert sizes == [24, 20]
    ctx.close()


@pytest.mark.parametrize("width", [320, 400])
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_a_phone_meets_the_name_before_the_preferences(browser, width, scheme):
    """On a phone the strip carries only the way around the site; the theme button stands in the colophon,
    labelled, beside "Accessibility" (deep review 2026-09-25, PA-04). The system setting is followed until a
    visitor chooses, so the one control a phone visitor rarely needs is not the first thing on the page. The
    button there is a real one: its label is painted, its box clears the 24px a target needs, its text keeps
    4.5:1 on the page in both themes, and a tap flips the theme as the strip's button does on a laptop."""
    page, _, _ = _page(browser, width, scheme)
    assert not page.locator("#theme-toggle").is_visible()
    foot = page.locator(".colophon-nav .toggle--foot")
    assert foot.is_visible() and foot.inner_text().strip() == ("Use dark theme" if scheme == "light" else "Use light theme")
    box = foot.bounding_box()
    assert box["height"] >= 24 and box["width"] >= 24, box
    assert page.evaluate("document.querySelector('.toggle--foot span').getBoundingClientRect().width") > 40   # painted, not clipped
    colour = page.evaluate("""() => { const b = document.querySelector('.toggle--foot');
      return [getComputedStyle(b).color, getComputedStyle(document.body).backgroundColor]; }""")
    assert _contrast(*colour) >= 4.5, colour
    # after the row's link, on its line: the row reads "Accessibility · Use dark theme"
    link = page.locator(".colophon-nav a").first.bounding_box()
    assert box["x"] > link["x"] and abs((box["y"] + box["height"] / 2) - (link["y"] + link["height"] / 2)) <= 2, (box, link)
    foot.click()
    assert page.evaluate("document.documentElement.getAttribute('data-theme')") == ("dark" if scheme == "light" else "light")
    assert foot.inner_text().strip() == ("Use light theme" if scheme == "light" else "Use dark theme")


@pytest.mark.parametrize("width", [360, 390, 414, 430])
@pytest.mark.parametrize("slug", NAV_SLUGS)
def test_the_strip_is_one_line_on_a_phone(browser, width, slug):
    """With the theme button out of the strip the five items fit one line from 356px up -- every common
    phone width -- where they wrapped to two at all of them before (deep review 2026-09-25, PA-04, measured
    in Chromium and WebKit). The line is about 60px tall; the strip used to be 109-111px."""
    page, _, _ = _page(browser, width, slug=slug)
    tops = page.evaluate("[...document.querySelectorAll('.sitenav li')].map(l => Math.round(l.getBoundingClientRect().top))")
    assert max(tops) - min(tops) <= 2, (width, slug, tops)
    strip = page.evaluate("document.querySelector('.topstrip').getBoundingClientRect().height")
    assert strip <= 64, (width, slug, strip)
    assert page.evaluate("document.querySelector('.topstrip .toggle').checkVisibility()") is False


@pytest.mark.parametrize("width", [480, 600, 719])
def test_the_strip_keeps_the_laptops_gap_between_a_phone_and_a_laptop(browser, width):
    """The spread that fits the five items on a phone's line was first applied up to 720px, so from 480 to
    719px the items were strewn across the whole line with gaps of 39 to 99px and snapped back to 18px at
    720 -- a stretched tab bar the site uses nowhere else, seen in a split-screen laptop window (WP-S9
    review). The spread stops at 30rem now: from 480px the strip is one line at the laptop's 1.125rem gap,
    still without the theme button, which returns at 720."""
    page, _, _ = _page(browser, width)
    boxes = page.evaluate("[...document.querySelectorAll('.sitenav li')].map(l => l.getBoundingClientRect().toJSON())")
    assert max(b["top"] for b in boxes) - min(b["top"] for b in boxes) <= 2, (width, boxes)
    gaps = [boxes[i + 1]["left"] - boxes[i]["right"] for i in range(len(boxes) - 1)]
    assert all(abs(gap - 18) <= 1 for gap in gaps), (width, gaps)
    assert page.evaluate("document.querySelector('.topstrip .toggle').checkVisibility()") is False


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
    """Keyboard use has to be seen. Every link and the toggle draw the same 2px accent ring when focused --
    every one that is on the page: the colophon's theme button is display: none from 45rem up and cannot take
    focus there, so it is not a control at this width."""
    page, _, _ = _page(browser, 1280, slug=slug)
    outlines = page.evaluate("""() => {
      const out = [];
      for (const el of [...document.querySelectorAll('a, button')].filter(el => el.checkVisibility())) {
        el.focus();
        const s = getComputedStyle(el);
        out.push({what: el.textContent.trim().slice(0, 24), width: s.outlineWidth, style: s.outlineStyle});
      }
      return out;
    }""")
    assert outlines and [o for o in outlines if o["style"] == "none" or parse_px(o["width"]) < 2] == []


def parse_px(value):
    return float(value.replace("px", "") or 0)


@pytest.mark.parametrize("slug", SLUGS)
def test_the_first_tab_stop_skips_to_the_content(browser, slug):
    """A keyboard reader pressed Tab eight times on the home page before reaching a word of it (deep review
    2026-09-25, PA-06). Now the first Tab lands on "Skip to content", painted inside the viewport at the top
    left only while it holds focus; Enter puts the focus on <main>, which draws no ring of its own; and the
    next Tab goes on from there, past the strip and the identity, never back into the header."""
    page, _, _ = _page(browser, 1280, slug=slug)
    before = page.evaluate("document.querySelector('.skip').getBoundingClientRect().bottom")
    assert before <= 0, before                                                   # above the viewport until then
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.className") == "skip"
    box = page.evaluate("document.querySelector('.skip').getBoundingClientRect().toJSON()")
    assert 0 <= box["y"] <= 24 and box["x"] <= 24 and box["height"] >= 24, box    # painted, at the top left
    ring = page.evaluate("getComputedStyle(document.activeElement).outlineWidth")
    assert parse_px(ring) >= 2
    page.keyboard.press("Enter")
    assert page.evaluate("document.activeElement.id") == "content"
    assert page.evaluate("getComputedStyle(document.activeElement).outlineStyle") == "none"
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.closest('header') === null")
    assert page.evaluate("document.activeElement.className") != "skip"


@pytest.mark.parametrize("scheme, by_toggle", [("dark", False), ("light", True)])
def test_paper_is_the_light_palette_whatever_the_screen_showed(browser, scheme, by_toggle):
    """The dark theme -- chosen with the toggle, or taken from the system -- used to print its name and lede
    in rgb(148,152,155), 2.91:1 on white; the light theme printed the CV and Paper chips as faint boxes and
    no address anywhere (deep review 2026-09-25, PA-01). On paper: black ink, the light colour scheme, the
    strip and the skip link gone, the Paper button a plain link, each button's address after it, and
    /research/ within its measured sheets at Chrome's default margins."""
    page, _, _ = _page(browser, 1280, scheme, "research/")
    if by_toggle:
        page.click("#theme-toggle")
        page.wait_for_timeout(300)
    assert page.evaluate("document.documentElement.getAttribute('data-theme')") == ("dark" if by_toggle else None)
    page.emulate_media(media="print")
    page.wait_for_timeout(300)                                                  # colour transitions take 120ms
    inks = page.evaluate("""() => ({
      body: getComputedStyle(document.body).color, h1: getComputedStyle(document.querySelector('h1')).color,
      h3: getComputedStyle(document.querySelector('.paper h3')).color,
      scheme: getComputedStyle(document.documentElement).colorScheme,
      ground: getComputedStyle(document.body).backgroundColor,
      strip: getComputedStyle(document.querySelector('.topstrip')).display,
      skip: getComputedStyle(document.querySelector('.skip')).display,
      foot: getComputedStyle(document.querySelector('.colophon-nav')).display,
      mark: getComputedStyle(document.querySelector('.mark-row')).display,
      root: getComputedStyle(document.documentElement).fontSize,
      chip: getComputedStyle(document.querySelector('.buttons .btn--primary')).backgroundColor,
      chipInk: getComputedStyle(document.querySelector('.buttons .btn--primary')).color,
      after: getComputedStyle(document.querySelector('.buttons a[href^="http"]'), '::after').content,
      href: document.querySelector('.buttons a[href^="http"]').getAttribute('href'),
      portrait: getComputedStyle(document.querySelector('.byline__who img')).filter,
      figure: getComputedStyle(document.querySelector('.paper__fig img')).filter,
    })""")
    assert inks["body"] == inks["h1"] == inks["h3"] == "rgb(0, 0, 0)", inks
    assert inks["scheme"] == "light" and inks["ground"] == "rgb(255, 255, 255)", inks
    assert inks["strip"] == inks["skip"] == inks["foot"] == inks["mark"] == "none", inks
    assert parse_px(inks["root"]) == 14                                          # 87.5% of the 16px default
    assert inks["chip"] == "rgba(0, 0, 0, 0)" and inks["chipInk"] == "rgb(11, 95, 115)", inks   # a plain link, in the accent
    assert inks["after"] == f'" <{inks["href"]}>"', inks["after"]              # the address, after the label
    assert inks["portrait"] == "none" and inks["figure"] == "none", inks
    page.evaluate("window.dispatchEvent(new Event('beforeprint'))")             # page.pdf() does not fire it
    sheet = page.pdf(format="A4", margin={"top": "0.4in", "right": "0.4in", "bottom": "0.4in", "left": "0.4in"})
    if pypdf is not None:
        assert len(pypdf.PdfReader(io.BytesIO(sheet)).pages) <= 3, "the research page grew past its measured sheets"


def test_the_older_news_prints_open_and_closes_again_afterwards(browser, tmp_path):
    """A closed <details> hides its content in the browser itself, not through a rule a stylesheet could
    override, so the printed home page used to leave out whatever stood behind "Older news" (deep review
    2026-09-25, PA-01). The script opens it on beforeprint and closes it again on afterprint -- and only what
    it opened: a list the reader had opened stays open. The live list is shorter than `news_visible`, so the
    expander is exercised on a page built from a copy of site.yaml that shows two items."""
    site = copy.deepcopy(SITE)
    site["news_visible"] = 2
    fixture = tmp_path / "index.html"
    fixture.write_text(build.render(site, site["pages"][0]), encoding="utf-8")
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    page.goto(fixture.as_uri())
    page.wait_for_timeout(300)
    assert page.locator("details.older").count() == 1
    assert page.evaluate("document.querySelector('details.older').open") is False
    page.evaluate("window.dispatchEvent(new Event('beforeprint'))")
    assert page.evaluate("document.querySelector('details.older').open") is True
    assert page.evaluate("document.querySelector('details.older').hasAttribute('data-print-opened')")
    assert page.locator("details.older .row").first.is_visible()                  # the older items are on the page
    page.evaluate("window.dispatchEvent(new Event('afterprint'))")
    assert page.evaluate("document.querySelector('details.older').open") is False
    assert not page.evaluate("document.querySelector('details.older').hasAttribute('data-print-opened')")
    page.evaluate("document.querySelector('details.older').open = true")           # the reader's own choice
    page.evaluate("window.dispatchEvent(new Event('beforeprint'))")
    page.evaluate("window.dispatchEvent(new Event('afterprint'))")
    assert page.evaluate("document.querySelector('details.older').open") is True    # is left as it was
    ctx.close()


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


@pytest.mark.parametrize("width", [360, 412])
def test_real_time_stays_on_one_line_on_the_bci4als_row(browser, width):
    """Chromium set the BCI4ALS row as "...the real- / time system." at 36 of 225 widths from 320 to 1440, 360
    and 412 (the commonest Android widths) among them, because a hyphen is a break opportunity and the
    site's nobreak list did not yet hold "real-time" (review of WP-S10). It does now, and the span it makes
    sits on one line: one client rect. (WebKit never split it at 16px; this pins the engine that did.)"""
    page, _, _ = _page(browser, width, slug="research/")
    rects = page.evaluate("""() => { const s = [...document.querySelectorAll('span.nb')].find(s => s.textContent === 'real-time');
                                   return s ? s.getClientRects().length : 0; }""")
    assert rects == 1, (width, rects)


# Every kind row on a phone: the date's box, the label's box and whether it clips sideways, where the label's
# first glyph is, and the gutter's inner edge (the right side of the spine), which bounds the strip of paper the
# dot's pulled-back block lands in when the label has dropped under its date. Page coordinates.
KIND_PAIRS_JS = r"""
() => [...document.querySelectorAll('.log--kind .row')].map(row => {
  const when = row.querySelector('.when'), kind = row.querySelector('.kind'), tag = row.querySelector('.kind .tag');
  const log = row.closest('.log'), y = window.scrollY;
  const text = [...tag.childNodes].find(n => n.nodeType === 3 && n.data.trim());
  const r = document.createRange(); r.setStart(text, 0); r.setEnd(text, 1);
  const glyph = r.getBoundingClientRect(), date = when.getBoundingClientRect(), box = kind.getBoundingClientRect();
  return {date: when.textContent.trim(), label: tag.textContent.trim(),
          rem: parseFloat(getComputedStyle(document.documentElement).fontSize),
          dot: getComputedStyle(tag, '::before').content, clip: getComputedStyle(kind).overflowX,
          glyphLeft: glyph.left, glyphTop: glyph.top + y, glyphBottom: glyph.bottom + y,
          dateRight: date.right, dateBottom: date.bottom + y, boxLeft: box.left,
          gutterLeft: log.getBoundingClientRect().left + parseFloat(getComputedStyle(log).borderLeftWidth)};
})
"""


def _colours(page, x0, y0, x1, y1):
    """The distinct colours painted inside a rectangle of the page (page coordinates, shrunk to whole pixels)."""
    clip = {"x": math.ceil(x0), "y": math.ceil(y0), "width": math.floor(x1) - math.ceil(x0), "height": math.floor(y1) - math.ceil(y0)}
    return set(Image.open(io.BytesIO(page.screenshot(clip=clip, full_page=True))).convert("RGB").getdata())


@pytest.mark.parametrize("text", [16, 32])
@pytest.mark.parametrize("width", [320, 390, 430, 480])
@pytest.mark.parametrize("slug", ["teaching/", "research/"])
def test_the_hung_dot_never_opens_a_kind_label_that_drops_under_its_date(browser, slug, width, text):
    """On a phone the kind label follows its date on one line, "Spring 2026 · TEACHING ASSISTANT", and the
    middle dot between them is hung in the date's right margin rather than typed into the label (style.css,
    the phone block). When the date is too long for the label to sit beside it, the label drops a line, and
    the dot has to go with the pair, not with the label: a line that opened "· TEACHING ASSISTANT" was the
    regression the review of WP-S7 caught. The course he has helped teach longest carries four academic
    years since WP-S12 (owner ruling 11, 2026-09-25), the longest date in any gutter, and its label drops
    under it at every phone width up to 480px, so this now happens on every phone. Each pair therefore has
    to be one of two things: the label beside its date, 1.25rem to the right of it with the dot in that gap;
    or the label on a line of its own, opening on a letter at the left edge of its own box, with the box set
    to clip what overflows it sideways (clip by name: hidden would push the label out from beside the floated
    date) and the strip of gutter beside that line, where the dot's block lands, bare paper. The strip is
    read from the pixels, because the letter opens at the box's edge whether or not the block is cut off:
    with the clip removed the label still measured in place and the dot was painted in the gutter (review of
    WP-S12). Both pages with kind rows, at the default text size and at 200%."""
    page, _, _ = _page(browser, width, slug=slug)
    page.evaluate(f"document.documentElement.style.fontSize = '{text}px'")
    page.wait_for_timeout(50)
    pairs = page.evaluate(KIND_PAIRS_JS)
    assert pairs, slug
    dropped = []
    for p in pairs:
        where = (slug, width, text, p["date"], p["label"])
        assert "·" in p["dot"], where                                      # the dot is the label's ::before, still
        if p["glyphTop"] >= p["dateBottom"] - 1:                           # the label dropped under its date
            dropped.append(p["date"])
            assert abs(p["glyphLeft"] - p["boxLeft"]) < 0.5, where         # opens at its box's edge
            assert p["clip"] == "clip", where                              # which cuts the dot's block off, without a new formatting context
            paper = _colours(page, p["gutterLeft"] + 1, p["glyphTop"] + 1, p["boxLeft"] - 1, p["glyphBottom"] - 1)
            assert len(paper) == 1, (where, paper)                         # and no dot is painted in the gutter beside the line
        else:
            assert abs(p["glyphLeft"] - p["dateRight"] - 1.25 * p["rem"]) < 0.5, where   # beside the date, the dot between
    if slug == "teaching/":
        assert "2023/24, 2024/25, 2025/26 and 2026/27" in dropped, (width, text)    # the case this test exists for
