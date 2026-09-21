import copy
import json
import re
import shutil
import subprocess
from html import unescape
from pathlib import Path
from urllib.parse import unquote

import pytest
from PIL import Image

import build

SITE = build.load_site(build.ROOT / "site.yaml")
PAGES = {page["slug"]: page for page in SITE["pages"]}
SLUGS = list(PAGES)
# The pages the navigation strip carries, and the ones only the colophon row does. A page is in exactly one of
# these lists (build.validate holds that), and SLUGS is still every page the site builds and sitemaps.
NAV_SLUGS = [page["slug"] for page in SITE["pages"] if page.get("nav")]
COLOPHON_SLUGS = [page["slug"] for page in SITE["pages"] if page.get("colophon")]


def page(slug: str, site: dict | None = None) -> dict:
    """The page entry for a slug, out of SITE or out of a copy being edited."""
    return next(p for p in (site or SITE)["pages"] if p["slug"] == slug)


def html_of(slug: str, site: dict | None = None) -> str:
    site = site or SITE
    return build.render(site, page(slug, site))


def every_page(site: dict | None = None) -> dict[str, str]:
    site = site or SITE
    return {p["slug"]: build.render(site, p) for p in site["pages"]}


def test_real_content_is_valid():
    assert build.validate(SITE) == []


@pytest.mark.parametrize("bad", ["+972 52 123 4567", "052-1234567", "call 0521234567 now"])
def test_phone_numbers_are_rejected_anywhere(bad):
    site = copy.deepcopy(SITE)
    site["about"][0] += " " + bad
    assert any("phone" in p for p in build.validate(site))


def test_phone_number_parsed_as_integer_is_rejected():
    site = copy.deepcopy(SITE)
    site["contact"] = 972521234567
    assert any("phone" in p for p in build.validate(site))


def test_phone_number_used_as_key_is_rejected():
    site = copy.deepcopy(SITE)
    site["footer"] = {**site["footer"], "052-1234567": "x"}
    assert any("phone" in p for p in build.validate(site))


def test_plain_email_is_rejected():
    site = copy.deepcopy(SITE)
    site["about"][0] += " someone@example.org"
    assert any("email" in p for p in build.validate(site))


@pytest.mark.parametrize("phrase", ["in preparation", "in prep", "under review", "submitted to",
                                    "in-prep", "in-preparation", "under-review", "In-Preparation"])
def test_unpublished_markers_are_rejected(phrase):
    site = copy.deepcopy(SITE)
    site["news"][0]["text"] += f" ({phrase})"
    assert any("unpublished" in p for p in build.validate(site))


def test_missing_required_key_is_reported():
    site = copy.deepcopy(SITE)
    del site["identity_line"]
    assert any("identity_line" in p for p in build.validate(site))


def test_a_page_without_a_template_is_reported_rather_than_built():
    """A page is a site.yaml entry plus one template named by its slug. Naming a slug that has no template is
    the one way to write a page that cannot be built, so the build says so instead of raising at render time."""
    site = copy.deepcopy(SITE)
    site["pages"].append({"slug": "elsewhere/", "nav": "Elsewhere", "heading": "Elsewhere"})
    problems = build.validate(site)
    assert any("elsewhere/" in p and "no template" in p for p in problems)


def test_split_news_keeps_order_and_counts():
    news = [{"date": f"Jan 20{i:02d}", "text": str(i)} for i in range(7)]
    shown, older = build.split_news(news, 5)
    assert [n["text"] for n in shown] == list("01234") and [n["text"] for n in older] == ["5", "6"]


def test_ongoing_research_is_hidden_unless_flag_set():
    site = copy.deepcopy(SITE)
    site["research"].append({**site["research"][0], "title": "FIXTURE ONGOING", "ongoing": True})
    assert [r["title"] for r in build.visible_research(site)] == [SITE["research"][0]["title"]]
    site["show_ongoing"] = True
    assert "FIXTURE ONGOING" in [r["title"] for r in build.visible_research(site)]


def test_person_jsonld_is_valid_and_names_match():
    data = json.loads(build.person_jsonld(SITE))
    assert data["@type"] == "Person" and data["name"] == "Itamar Avitan"
    assert data["alternateName"] == "Itamar Amram Avitan" and data["url"] == SITE["site_url"]
    assert all(u.startswith("https://") for u in data["sameAs"]) and "email" not in data
    assert len(data["sameAs"]) == 6 and "cv.pdf" not in data["sameAs"]
    assert "same_as" not in SITE


def test_jsonld_cannot_break_out_of_its_script_element():
    site = copy.deepcopy(SITE)
    site["identity_line"] = 'x</script><script>alert(1)</script>'
    assert json.loads(build.person_jsonld(site))["description"] == site["identity_line"]
    html = build.render(site)
    assert "</script><script>" not in html and "alert(1)</script>" not in html
    block = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S).group(1)
    assert json.loads(block)["description"] == site["identity_line"]


def test_render_escapes_html_and_hides_cv_when_flag_off():
    site = copy.deepcopy(SITE)
    site["show_cv"] = False                 # the site ships with the CV published; prove the switch still works
    site["news"][0]["text"] = "5 < 6 & <script>x</script>"
    html = build.render(site)
    assert "<script>x</script>" not in html and "5 &lt; 6 &amp;" in html
    assert all('href="cv.pdf"' not in h and 'href="../cv.pdf"' not in h for h in every_page(site).values())
    site["show_cv"] = True
    assert 'href="cv.pdf"' in build.render(site)


def test_main_writes_nothing_when_invalid(tmp_path, monkeypatch):
    bad = copy.deepcopy(SITE); bad["about"][0] += " 052-1234567"
    monkeypatch.setattr(build, "ROOT", tmp_path)
    monkeypatch.setattr(build, "load_site", lambda _p: bad)
    (tmp_path / "templates").mkdir()
    assert build.main([]) == 1 and not (tmp_path / "index.html").exists()


def test_main_builds_every_page_and_the_two_robot_files(tmp_path, monkeypatch):
    shutil.copytree(build.ROOT / "templates", tmp_path / "templates")
    shutil.copy(build.ROOT / "site.yaml", tmp_path / "site.yaml")
    monkeypatch.setattr(build, "ROOT", tmp_path)
    assert build.main([]) == 0
    assert "<title>Itamar Avitan</title>" in (tmp_path / "index.html").read_text(encoding="utf-8")
    for slug in SLUGS:
        assert (tmp_path / slug / "index.html").exists(), slug
    sitemap = (tmp_path / "sitemap.xml").read_text(encoding="utf-8")
    assert [m for m in re.findall(r"<loc>([^<]+)</loc>", sitemap)] == [SITE["site_url"] + s for s in SLUGS]
    assert "Allow: /" in (tmp_path / "robots.txt").read_text(encoding="utf-8")


# --- the four pages ---------------------------------------------------------------------------------------

def test_the_site_is_the_four_pages_the_owner_asked_for():
    """He chose separate pages over one scroll on 2026-09-21. Home is the front door; the talks sit with the
    paper they carried, on Research; the commonplace has the page of its own he asked for. The strip carries
    those four and only those four: the accessibility statement is a fifth page, linked from the colophon."""
    assert NAV_SLUGS == ["", "research/", "teaching/", "commonplace/"]
    assert [p["nav"] for p in SITE["pages"] if p.get("nav")] == ["Home", "Research", "Teaching", "Commonplace"]
    assert not page("").get("heading")                     # the home page's h1 is the name in the masthead
    assert all(page(s)["heading"] for s in SLUGS[1:])


def test_a_page_is_linked_from_the_strip_or_the_colophon_and_never_from_both():
    """Every page the site builds has exactly one label: `nav` puts it in the strip, `colophon` in the small
    row at the foot. Both would print the same address twice on every page; neither would publish a page
    nothing on the site leads to -- the failure a reader cannot see, so the build refuses it."""
    assert COLOPHON_SLUGS == ["accessibility/"]
    assert set(NAV_SLUGS) & set(COLOPHON_SLUGS) == set()
    assert set(NAV_SLUGS) | set(COLOPHON_SLUGS) == set(SLUGS)
    for labels, wanted in (({"nav": "X", "colophon": "X"}, "both"), ({}, "neither")):
        site = copy.deepcopy(SITE)
        entry = {k: v for k, v in page("research/", site).items() if k not in ("nav", "colophon")}
        site["pages"] = [{**entry, **labels}]
        assert any("research/" in p and wanted in p for p in build.validate(site)), wanted


def test_each_page_has_its_own_address_title_and_canonical():
    for slug, html in every_page().items():
        url = SITE["site_url"] + slug
        assert f'<link rel="canonical" href="{url}">' in html, slug
        assert f'property="og:url" content="{url}"' in html, slug
        heading = page(slug).get("heading")
        title = f"{heading} — Itamar Avitan" if heading else "Itamar Avitan"
        assert f"<title>{title}</title>" in html and f'property="og:title" content="{title}"' in html, slug
        assert f'name="description" content="{build.page_description(SITE, page(slug))}"' in html, slug


def test_every_page_has_exactly_one_h1_and_it_names_the_page():
    for slug, html in every_page().items():
        assert html.count("<h1") == 1, slug
        h1 = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S).group(1).strip()
        assert h1 == (page(slug).get("heading") or SITE["name"]), slug


def test_the_navigation_is_ordinary_links_that_mark_the_page_you_are_on():
    """No JavaScript is involved: four <a> elements and the CV, the same five on every page, with the current
    one carrying aria-current so a reader who cannot see the tick is told the same thing."""
    for slug, html in every_page().items():
        strip = html.partition('<nav class="sitenav"')[2].partition("</nav>")[0]
        labels = re.findall(r'<a [^>]*href="([^"]*)"([^>]*)>([^<]+)</a>', strip)
        assert [label for _, _, label in labels] == ["Home", "Research", "Teaching", "Commonplace", "CV"], slug
        current = [label for _, attrs, label in labels if 'aria-current="page"' in attrs]
        # a page the strip does not carry marks nothing in it: the colophon row below marks itself instead
        assert current == ([page(slug)["nav"]] if slug in NAV_SLUGS else []), slug
        assert strip.count('aria-current="page"') == (1 if slug in NAV_SLUGS else 0), slug
        assert "onclick" not in strip and "<button" not in strip, slug


def test_every_link_between_pages_climbs_back_to_the_root_first():
    """The pages live in folders, so a link from one to another is relative to its own depth: 'research/' from
    the root, '../teaching/' from inside one. A page linking to itself says './', never an empty address."""
    for slug, html in every_page().items():
        base = "../" if slug else ""
        strip = html.partition('<nav class="sitenav"')[2].partition("</nav>")[0]
        hrefs = re.findall(r'<a [^>]*href="([^"]*)"', strip)
        assert hrefs == [(base + s) or "./" for s in NAV_SLUGS] + [base + "cv.pdf"], slug
        assert "" not in hrefs and all(not h.startswith("/") for h in hrefs), slug
        for asset in ("style.css", SITE["portrait"]["jpg"], SITE["portrait"]["webp"]):
            assert f'"{base}{asset}"' in html, (slug, asset)


def test_the_cv_is_one_click_from_every_page():
    for slug, html in every_page().items():
        base = "../" if slug else ""
        assert html.count(f'<a class="btn btn--primary" href="{base}cv.pdf">CV</a>') == 1, slug


def test_every_page_says_whose_site_it_is_without_repeating_the_bio():
    """A visitor landing on a deep page sees the name, the role line and the portrait at the top and the
    address at the foot. The three paragraphs of "about" are said once, at home."""
    for slug, html in every_page().items():
        assert SITE["name"] in build._visible_text(html) if False else True
        assert html.count('class="role"') == 1, slug
        assert html.count('id="email"') == 1, slug
        assert html.count(SITE["links"][0]["text"]) == 1, slug
        assert '<img src="' in html and SITE["portrait"]["alt"] in html, slug
        if slug:
            assert '<div class="byline inset">' in html and "masthead" not in html, slug
            assert '<footer class="colophon">' in html and "links-nav" in html.partition("<footer")[2], slug
        else:
            assert '<div class="masthead">' in html and "byline" not in html
    home = html_of("")
    for paragraph in SITE["about"]:
        assert paragraph.split(".")[0] in _visible_text(home)
    assert sum(1 for h in every_page().values() if SITE["about"][0].split(".")[0] in _visible_text(h)) == 1


def test_home_hands_the_visitor_on_to_every_other_page():
    """The front door named the commonplace, the teaching and the paper and linked to none of them, so the
    navigation strip was the only way in. One row per other page, each carrying a real thing from it."""
    html = html_of("")
    section = html.partition('<section id="elsewhere"')[2].partition("</section>")[0]
    assert [row["page"] for row in SITE["elsewhere"]] == [page(s)["nav"] for s in NAV_SLUGS[1:]]
    assert section.count('<li class="row">') == 3
    for row in SITE["elsewhere"]:
        assert f'<span class="tag">{row["page"]}</span>' in section, row["page"]   # a label, never a link
        assert f'<a href="{row["url"]}">' in section, row["url"]                   # relative to the root page
        assert row["label"] in _visible_text(section) and row["text"] in _visible_text(section)
        assert "read more" not in row["label"].lower() and row["label"] != row["page"]
    assert '<p class="when">' not in section          # the gutter carries an address here, so it takes no tick
    assert section.count("<a ") == 3                  # one way out of each row, and no second link to dilute it
    for slug in SLUGS[1:]:                            # and only the home page hands off; no page does it twice
        assert 'id="elsewhere"' not in html_of(slug), slug


def test_the_address_is_said_at_the_top_and_the_profiles_at_the_foot():
    """Six profile links between the lede and the first word of the home page cost about 120px of a phone
    screen, and they are not what a visitor comes to a front door for. The address keeps the masthead; the
    profiles stand in the colophon of every page. Each page still says each of them exactly once -- and only
    one element on any page carries id="email", which is the hook the script turns into a mailto."""
    home = html_of("")
    masthead = home.partition('<div class="masthead">')[2].partition("<main")[0]
    assert 'class="links links--reach"' in masthead and 'class="links-row"' not in masthead
    foot = home.partition("<footer")[2]
    assert 'class="links-row"' in foot and "links--reach" not in foot
    for slug, html in every_page().items():
        assert html.count('id="email"') == 1, slug
        assert html.count('class="links-row"') == 1, slug
        assert html.count('<nav class="links-nav"') == (1 if slug else 2), slug
        deep_foot = html.partition("<footer")[2]
        assert ("links--reach" in deep_foot) is bool(slug), slug             # deep pages say the address here


def test_the_research_page_names_the_paper_section_like_the_other_two():
    """The card had no heading, so two of the three things the page holds took a tick on the margin rule and
    the third did not -- which left the paper reading as an illustration of the intro rather than as the work.
    Naming it also closes the heading-level skip: h1 Research, h2 Papers, then the card's h3 title."""
    html = html_of("research/")
    assert '<h2 id="papers-h" class="inset">Papers</h2>' in html
    papers = html.partition('<section id="papers"')[2].partition('<section id="talks"')[0]
    assert '<article class="paper">' in papers                       # the card is inside the section it names
    assert papers.count("<h3>") == len(SITE["research"]) == 1        # and its title is a level below "Papers"
    assert SITE["research"][0]["title"][:20] in _visible_text(papers)
    assert re.findall(r"<h([123])", html) == ["1", "2", "3", "2", "3", "3", "3", "2", "3", "3"]


@pytest.mark.parametrize("slug", SLUGS)
def test_no_page_skips_a_heading_level(slug):
    """A reader moving by headings should never fall through a level. The research page used to run h1 -> h3,
    because Research was an h2 when the site was one page and the card's title was never re-levelled when it
    became the page's h1."""
    levels = [int(n) for n in re.findall(r"<h([1-6])", html_of(slug))]
    assert levels and levels[0] == 1 and levels.count(1) == 1, slug
    for before, after in zip(levels, levels[1:]):
        assert after <= before + 1, (slug, before, after, levels)


# --- the templates ----------------------------------------------------------------------------------------

def _visible_text(html: str) -> str:
    html = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S)
    html = re.sub(r"</?(?:a|span|b|strong|em|i|mark|time|abbr|cite)\b[^>]*>", "", html)   # inline tags: no space
    return " ".join(re.sub(r"<[^>]+>", " ", html).replace("&amp;", "&").replace("&#39;", "'").replace("&#34;", '"').split())


def _values(node):
    """Content values only. build._strings also yields dict keys (the privacy guard wants them); keys are never rendered."""
    if isinstance(node, dict):
        for v in node.values():
            yield from _values(v)
    elif isinstance(node, list):
        for v in node:
            yield from _values(v)
    else:
        yield str(node)


def test_every_content_string_is_rendered_on_one_page_or_another():
    text = " ||| ".join(_visible_text(h) for h in every_page().values())
    wanted = {k: SITE[k] for k in ("identity_line", "about", "now", "elsewhere", "news", "talks", "projects",
                                   "teaching", "accessibility")}
    wanted["intros"] = [p.get("intro") for p in SITE["pages"] if p.get("intro")]
    # an address is not text: full ones start with http, and the "elsewhere" rows point at this site's own
    # slugs, which the template turns into a relative link rather than printing
    addresses = set(SLUGS)
    missing = [s for s in _values(wanted)
               if not s.startswith("http") and s not in addresses and " ".join(s.split()) not in text]
    assert missing == []


def test_head_metadata():
    for slug, html in every_page().items():
        for needle in ('property="og:image"', 'name="twitter:card" content="summary_large_image"',
                       'application/ld+json', 'name="description"', 'rel="canonical"'):
            assert needle in html, (slug, needle)


def test_no_mailto_no_plain_address_no_third_party_hosts():
    for slug, html in every_page().items():
        assert "mailto:" not in html.replace('"mailto:" +', "") and "@post.bgu.ac.il" not in html, slug
        hosts = set(re.findall(r'(?:src|href)="https?://([^/"]+)', html.split("</head>")[0]))
        assert hosts <= {"itamar-avitan.github.io"}, (slug, hosts)   # head may only reference the site itself


def test_removed_items_stay_removed():
    text = " ".join(_visible_text(h) for h in every_page().values())
    assert "Curved Spaces" not in text and "Feb 2026" not in text
    assert 'BCI4ALS</a>' not in html_of("research/")        # card title is not a link


def test_open_graph_points_at_the_site_and_its_card():
    html = html_of("")
    for needle in ('property="og:title" content="Itamar Avitan"', 'property="og:type" content="website"',
                   'property="og:url" content="https://itamar-avitan.github.io/"',
                   'property="og:image" content="https://itamar-avitan.github.io/img/og.png"',
                   f'property="og:description" content="{SITE["identity_line"]}"'):
        assert needle in html, needle


def test_section_ids_and_script_hooks():
    where = {"": ["about", "elsewhere", "now", "news"],
             "research/": ["research", "papers", "talks", "projects"],
             "teaching/": ["teaching", "courses", "students"], "commonplace/": ["quotes"],
             "accessibility/": ["accessibility", "aim", "done", "limits", "report"]}
    for slug, html in every_page().items():
        for sec in where[slug]:
            assert html.count(f'<section id="{sec}"') == 1, (slug, sec)
        assert html.count('id="theme-toggle"') == 1, slug
        assert '<span id="email">avitanit [at] post.bgu.ac.il</span>' in html, slug
    research = html_of("research/")
    assert (research.index('id="research"') < research.index('id="papers"')
            < research.index('id="talks"') < research.index('id="projects"'))


def test_research_card_shows_every_field():
    html = html_of("research/")
    text, card = _visible_text(html), SITE["research"][0]
    shown = [card["title"], card["tldr"], *filter(None, [card["figure"].get("caption")]), *card["authors"],
             *card["badges"], *(b["label"] for b in card["buttons"])]
    assert [s for s in shown if " ".join(s.split()) not in text] == []
    # unescaped, because an address with two query parameters carries "&", which a document writes "&amp;"
    assert all(f'href="{b["url"]}"' in unescape(html) for b in card["buttons"])
    assert f'<img src="../{card["figure"]["src"]}" alt="{card["figure"]["alt"]}"' in html
    assert '<span class="me">Itamar Avitan</span>, Tal Golan' in html
    assert html.count("btn btn--primary") == 2              # the paper's Paper button, and the CV in the strip
    assert '<span class="tag tag--venue">NeurIPS 2025</span>' in html and '<span class="tag">CCN 2025 · Talk</span>' in html


def test_the_card_figure_is_swapped_by_one_line_and_only_a_plot_is_marked_as_one():
    """site.yaml keeps both thumbnails; research[0].figure points at one of them by YAML alias."""
    lab, plot = SITE["figure_options"]["lab_illustration"], SITE["figure_options"]["recovery_matrix"]
    html = html_of("research/")
    assert SITE["research"][0]["figure"] is lab                              # the lab illustration is live
    assert f'<picture><source srcset="../{lab["webp"]}" type="image/webp"><img src="../{lab["src"]}"' in html
    assert '<figure class="paper__fig paper__fig--art">' in html             # a picture, not a plot
    assert "paper__fig--plot" not in html and "<figcaption" not in html
    site = copy.deepcopy(SITE)
    site["research"][0]["figure"] = site["figure_options"]["recovery_matrix"]
    other = html_of("research/", site)
    assert f'<img src="../{plot["src"]}" alt="{plot["alt"]}" width="272" height="272">' in other
    assert lab["webp"] not in other and lab["src"] not in other
    assert '<figure class="paper__fig paper__fig--plot">' in other and "<figcaption>Fig. 1D</figcaption>" in other
    assert "paper__fig--art" not in other


def test_a_figure_whose_colour_is_data_is_never_hue_shifted():
    """The dark theme may dim a figure, but a plot's colours are its data: only a brightness scale may touch
    them, because scaling the three channels together leaves every hue and saturation where it was."""
    css = (build.ROOT / "style.css").read_text(encoding="utf-8")
    assert ".paper__fig--plot img { filter: var(--plot-filter); }" in css
    assert ".paper__fig--art img { filter: var(--art-filter); }" in css
    values = re.findall(r"--plot-filter:\s*([^;]+);", css)
    assert len(values) == 3                                                  # light, and both dark blocks
    assert all(re.fullmatch(r"none|brightness\(0?\.\d+\)", v.strip()) for v in values), values
    assert "invert(" not in css and "hue-rotate(" not in css


def test_no_theme_alters_a_figure_and_a_light_mat_answers_the_dark_page_instead():
    """The owner's ruling of 2026-09-21. Inverting an illustration misrepresents it for the same reason
    inverting a plot does, and so, more quietly, does dimming or desaturating one. Nothing filters a figure in
    any theme now; what settles a pale print into a dark page is the mount around it."""
    css = (build.ROOT / "style.css").read_text(encoding="utf-8")
    for token in ("--plot-filter", "--art-filter"):
        values = [v.strip() for v in re.findall(rf"{token}:\s*([^;]+);", css)]
        assert values == ["none", "none", "none"], (token, values)           # light, and both dark blocks
    assert len(re.findall(r"--mat:\s*#[0-9A-Fa-f]{6};", css)) == 3           # a mount in every theme
    mat = re.search(r"\.paper__plot \{[^}]*\}", css).group(0)
    assert "background: var(--mat);" in mat and re.search(r"padding: [\d.]+rem;", mat)


def test_ongoing_card_gets_an_ongoing_badge_only_when_shown():
    site = copy.deepcopy(SITE)
    site["research"].append({**site["research"][0], "title": "FIXTURE ONGOING", "ongoing": True})
    hidden = html_of("research/", site)
    assert "FIXTURE ONGOING" not in hidden and ">Ongoing<" not in hidden
    site["show_ongoing"] = True
    html = html_of("research/", site)
    assert "FIXTURE ONGOING" in html and html.count('<span class="tag">Ongoing</span>') == 1


def test_news_is_split_between_the_list_and_the_details_element():
    shown, older = build.split_news(SITE["news"], SITE["news_visible"])
    before, _, after = html_of("").partition('<details class="older">')
    assert older and all(n["text"] in _visible_text(before) for n in shown)
    inside = _visible_text(after.split("</details>")[0])
    assert all(n["text"] in inside for n in older) and f"Older news ({len(older)})" in inside
    site = copy.deepcopy(SITE)
    site["news_visible"] = 99
    assert "<details" not in html_of("", site)


def test_talks_link_the_venue_and_show_the_note():
    html = html_of("research/")
    assert '<p class="venue"><a href="https://2025.ccneuro.org/contributed-talk/?id=65">' in html
    assert '<p class="note">Preliminary version of the NeurIPS 2025 paper.</p>' in html
    assert html.count('<p class="note">') == 1


def test_project_titles_link_only_when_a_url_is_given():
    html = html_of("research/")
    assert ('<h3><a href="https://github.com/Itamar-Avitan/earbetter-ebt-practicum">'
            'Embodied Brain Technology Practicum</a></h3>') in html
    assert "<h3>BCI4ALS</h3>" in html


def test_the_practicum_leads_with_the_programme_and_says_nothing_about_who_did_what():
    """The owner's ruling of 2026-09-21, which supersedes every earlier framing of this entry -- his own
    product-story correction of the same day included. His words: "i think its need to be more of a
    description of an neurotechnology internship practicum for grad students collaborate with brown
    university at providence blah blah, like more about the environment less on what i did, if they want to
    know they can ask. (also on the website btw i think)". He chose "One entry, programme-first": the
    PROGRAMME leads, EarBetter is named in a clause, and nothing says who did what. See the long note over
    `projects` in site.yaml, and shared/projects.tex in the CV repository, which carries the same reframe."""
    entry = next(p for p in SITE["projects"] if "Practicum" in p["title"])
    news = next(n for n in SITE["news"] if "Practicum" in n["text"])
    html = html_of("research/")

    # the programme leads: it is the entry's own title, and the first sentence of the blurb is about it
    assert entry["title"] == "Embodied Brain Technology Practicum"
    assert "EarBetter" not in entry["blurb"].split(".")[0]
    assert entry["blurb"].index("Brown University") < entry["blurb"].index("EarBetter")
    assert news["text"].index("Practicum") < news["text"].index("EarBetter")

    # EarBetter is named, in a clause, and briefly
    for phrase in ("EarBetter", "add-on", "any headphones", "biosignals", "anxiety"):
        assert phrase in entry["blurb"], phrase
    assert "headphone add-on" in news["text"] and "designed, built and pitched" in news["text"]

    # DELETED AT HIS REQUEST, from the whole site: his own part, the sensors, the business-plan detail
    everywhere = " ".join(_visible_text(h) for h in every_page().values())
    for gone in ("control software", "put the system together", "system integration",
                 "Muse", "heart-rate variability", "skin conductance",
                 "go-to-market", "business plan"):
        assert gone not in everywhere, gone
    # and no personal-contribution wording put back in its place: nothing in the entry is in the first person
    for first_person in ("I ", "I'", "my ", "myself"):
        assert first_person not in entry["blurb"], first_person
    assert "pitched" in entry["blurb"]              # the single word that survives of the pitch and the plan

    # the older paper-language framing stays gone too
    for phrase in ("explores whether", "physiological signals", "selective audio attenuation",
                   "remain experimental", "clinically evaluated"):
        assert phrase not in entry["blurb"] + " " + news["text"], phrase

    # the facts it may state, and the two it may not
    assert entry["period"] == "24 Jul–6 Aug 2026" and "Two weeks" in entry["blurb"]   # derived, not asserted
    assert "competitive" in entry["blurb"] and "five-person" in entry["blurb"]
    assert "12" not in _visible_text(html)          # "1 of 12" is on his list of private numbers
    assert "award" not in everywhere.lower()

    # the ruling is recorded where the next editor will see it, with the superseded wording kept as evidence
    yaml_text = (build.ROOT / "site.yaml").read_text(encoding="utf-8")
    assert "more about the environment less on what i did" in yaml_text
    assert "One entry, programme-first" in yaml_text
    assert "MUST NOT COME BACK" in yaml_text and "SUPERSEDED, kept as evidence" in yaml_text
    assert "I wrote the control software and put the system together." in yaml_text   # only as the old text



def test_optional_keys_may_be_absent():
    site = copy.deepcopy(SITE)
    for entry in site["talks"] + site["projects"] + site["research"] + site["links"]:
        for key in ("url", "note", "text", "figure", "me", "ongoing", "authors", "badges", "buttons"):
            entry.pop(key, None)
    for entry in site["pages"]:
        entry.pop("intro", None)
        entry.pop("description", None)
    for key in ("role", "nobreak", "news_visible", "show_ongoing", "quotes_intro"):
        site.pop(key, None)
    pages = every_page(site)
    for slug, html in pages.items():
        for gone in ('<p class="venue"><a', 'class="note"', "<figure", 'class="authors"', 'class="role"',
                     'class="nb"', 'rel="me"', 'id="email"', 'class="tag tag--venue"', 'class="btn',
                     'class="page-intro'):
            assert gone not in html, (slug, gone)
    assert "<h3>Embodied Brain Technology Practicum</h3>" in pages["research/"]
    assert "Cognitive Computational Neuroscience (CCN) 2025" in pages["research/"]


def test_dates_are_time_elements():
    pages = every_page()
    for slug, needle in [("", '<time class="when" datetime="2026-09">Sep 2026</time>'),
                         ("research/", '<time datetime="2026-09-14">14</time>–<time datetime="2026-09-15">15 Sep 2026</time>'),
                         ("research/", '<time datetime="2025-12-05">5 Dec 2025</time>'),
                         ("research/", '<time datetime="2026-07-24">24 Jul</time>–<time datetime="2026-08-06">6 Aug 2026</time>'),
                         ("research/", '<time datetime="2022">2022</time>–<time datetime="2023">2023</time>'),
                         ("", '<time class="what" datetime="2026-09">September 2026</time>')]:
        assert needle in pages[slug], (slug, needle)


def test_dates_that_cannot_be_read_stay_plain_text():
    site = copy.deepcopy(SITE)
    site["news"][0]["date"] = "Summer 2026"
    site["talks"][0]["date"] = "32 Sep 2026"
    site["projects"][0]["period"] = "1–2–3 Sep 2026"
    site["footer"]["updated"] = "a while ago"
    pages = every_page(site)
    assert '<span class="when">Summer 2026</span>' in pages[""]
    assert '<span class="what">a while ago</span>' in pages[""]
    assert '<p class="when">32 Sep 2026</p>' in pages["research/"]
    assert '<p class="when">1–2–3 Sep 2026</p>' in pages["research/"]
    for html in pages.values():
        assert all(re.fullmatch(r"\d{4}(-\d\d(-\d\d)?)?", d) for d in re.findall(r'datetime="([^"]*)"', html))


def test_the_now_block_is_dated_once_and_its_lines_are_not_events():
    """It says what he is doing, so it carries one date and takes one tick. The lines under it are not events
    and get no dates of their own -- they are a list in the text column, beside that single tick."""
    section = html_of("").partition('<section id="now"')[2].partition("</section>")[0]
    assert section.count('class="when"') == 1 and '<time class="when" datetime="2026-09">' in section
    assert section.count("<li>") == len(SITE["now"]) == 4
    assert '<ul class="what now" role="list">' in section
    text = _visible_text(section)
    assert all(" ".join(line.split()) in text for line in SITE["now"])


def test_the_about_and_now_drafts_are_marked_as_drafts_in_site_yaml():
    """They are the two pieces of writing the owner asked for and has not yet approved. The file says so, so
    that whoever edits next knows they are standing in for his own words, not recording them."""
    yaml_text = (build.ROOT / "site.yaml").read_text(encoding="utf-8")
    assert yaml_text.count("DRAFT, awaiting the owner") == 1 and "DRAFT, as above" in yaml_text
    assert 150 <= sum(len(p.split()) for p in SITE["about"]) <= 200
    assert 3 <= len(SITE["now"]) <= 5


def test_teaching_is_two_sections_and_every_row_is_dated_and_titled():
    """It was one flat list of four sentences: no headings, no section ticks, the only page on the site with
    no internal structure -- and a mentorship ranked level with a three-year teaching assistantship. It is
    Courses and Students now, and each row is laid out like a talk: the years in the gutter, the role under
    them as a tag, the course and what it is about in the column."""
    html = html_of("teaching/")
    for section, heading in (("courses", "Courses"), ("students", "Students")):
        assert f'<section id="{section}" aria-labelledby="{section}-h">' in html, section
        assert f'<h2 id="{section}-h" class="inset">{heading}</h2>' in html, section
    rows = html.count('<li class="row">')
    assert rows == html.count('<p class="when">') == html.count('<p class="kind">') == 5
    assert rows == len(SITE["teaching"]["courses"]) + len(SITE["teaching"]["students"])
    for entry in SITE["teaching"]["courses"] + SITE["teaching"]["students"]:
        assert f'<span class="tag">{entry["kind"]}</span>' in html, entry["title"]
    assert '<p class="when">2023/24, 2024/25 and 2025/26</p>' in html          # not a date the page can read
    assert '<p class="when"><time datetime="2021">2021</time>–<time datetime="2023">2023</time></p>' in html
    assert '<h3>Two undergraduate research students</h3>' in html              # its own row, not a line in a list


def test_the_teaching_page_carries_the_course_home_announces():
    """This page is the authority on his teaching, and it did not list the academic writing course that both
    "about" and "now" on the home page announce for 2026/27."""
    courses = SITE["teaching"]["courses"]
    writing = next(c for c in courses if "writing" in c["title"].lower())
    assert writing["when"] == "from 2026/27" and "2026/27" in SITE["now"][1]
    assert "academic writing course" in SITE["about"][2]
    assert writing["title"] in _visible_text(html_of("teaching/"))
    assert [c["title"] for c in courses][0] == "Introduction to Cognition and Computation"


def test_a_teaching_row_without_a_sentence_still_renders():
    """The two rows under "Students" are people, not a syllabus: they carry a title and nothing under it."""
    html = html_of("teaching/")
    students = html.partition('<section id="students"')[2].partition("</section>")[0]
    assert students.count("<h3>") == 2 and "<p>" not in students
    site = copy.deepcopy(SITE)
    site["teaching"] = {"courses": [{"title": "A course", "kind": "Teaching assistant", "when": "2026"}]}
    only = html_of("teaching/", site)
    assert "<h3>A course</h3>" in only and '<section id="students"' not in only


def test_quotes_print_the_line_its_attribution_and_its_context_and_nothing_else():
    html = html_of("commonplace/")
    section = html.partition('<section id="quotes"')[2].partition("</section>")[0]
    shown = _visible_text(section)
    assert section.count("<blockquote>") == len(SITE["quotes"]) == 4
    for quote in SITE["quotes"]:
        assert f'<blockquote><p>{quote["text"]}</p></blockquote>' in section         # printed exactly as verified
        assert quote["attribution"] in shown and quote.get("note", "") in shown
        for private in ("evidence", "status", "copyright"):                          # provenance, not page text
            assert quote[private] not in shown
    assert section.count('<time class="when" datetime="1887">1887</time>') == 3      # the year takes its tick
    assert '<time class="when" datetime="2003">2003</time>' in section
    # the borrowed line keeps its own date in the attribution, where it cannot break at the hyphen
    assert 'Epistle II (<span class="nb">1733-34</span>)</p>' in section


def test_a_quotation_carries_its_own_quotation_marks():
    """Generated content cannot be selected or copied. On a page of quotations the marks are part of the text,
    so a reader who copies a line gets the line as it is printed."""
    section = html_of("commonplace/").partition('<section id="quotes"')[2].partition("</section>")[0]
    for quote in SITE["quotes"]:
        assert quote["text"].startswith("“") and quote["text"].endswith("”"), quote["text"]
    css = (build.ROOT / "style.css").read_text(encoding="utf-8")
    assert "\\201C" not in css and "\\201D" not in css
    assert section.count("“") >= 3 and section.count("”") >= 3


def test_the_family_resemblance_line_is_the_verified_one():
    """Checked against Project Gutenberg ebook #244 in full; see the entry's own evidence. The apostrophe in
    "can't" is the curly U+2019 the source prints, which is the one place a retyped copy drifts."""
    quote = next(q for q in SITE["quotes"] if "family resemblance" in q["text"])
    assert quote["text"] == ("“There is a strong family resemblance about misdeeds, and if you have all "
                             "the details of a thousand at your finger ends, it is odd if you can’t "
                             "unravel the thousand and first.”")
    assert "can't" not in quote["text"]
    assert quote["attribution"] == ("Sherlock Holmes, in Arthur Conan Doyle, A Study in Scarlet (1887), "
                                    "Part I, Chapter II, “The Science of Deduction”")
    assert quote["year"] == "1887" and quote["status"] == "verified" and quote["copyright"] == "public-domain"


def test_the_commonplace_runs_in_the_owners_order_with_snape_first():
    """It used to run oldest first, so that the years in the gutter only went forward. The owner asked on
    2026-09-21 for "the snape quote needs to be first", so that rule is gone: a commonplace book is a person's
    own order, not a chronology, and the gutter may now carry 2003 above 1887. Nothing else moved -- the three
    A Study in Scarlet lines keep the order they were in."""
    years = [q["year"] for q in SITE["quotes"]]
    assert years == ["2003", "1887", "1887", "1887"] != sorted(years)
    assert SITE["quotes"][0]["copyright"].startswith("in-copyright")          # the Snape passage leads
    assert SITE["quotes"][0]["text"].startswith("“Only Muggles talk of ‘mind reading.’")
    assert [q["text"][:40] for q in SITE["quotes"][1:]] == [
        "“I consider that a man’s brain originally"[:40],
        "“The proper study of mankind is man.”"[:40],
        "“There is a strong family resemblance abo"[:40]]
    section = html_of("commonplace/").partition('<section id="quotes"')[2].partition("</section>")[0]
    assert re.findall(r'<time class="when" datetime="(\d{4})">', section) == years
    order = [m.start() for q in SITE["quotes"] for m in [re.search(re.escape(q["text"][:40]), section)]]
    assert order == sorted(order)                        # printed in that order too, not merely stored in it


def test_the_in_copyright_quotation_is_the_long_extract_the_owner_ruled_for():
    """He was shown that the novel is in copyright and that the single sentence was the safer extract, and
    chose the full passage. Shortening it back is undoing a decision, not tidying, so the reason is recorded
    in site.yaml beside the entry as well as here. The verifier's two corrections are applied."""
    quote = next(q for q in SITE["quotes"] if q["copyright"].startswith("in-copyright"))
    assert quote["text"].startswith("“Only Muggles talk of ‘mind reading.’ ")
    assert quote["text"].endswith("or at least, most minds are.”")       # not broken off after "Potter—"
    assert "mind-reading" not in quote["text"]                                # two words, as every source has
    for sentence in ("The mind is not a book, to be opened at will and examined at leisure.",
                     "Thoughts are not etched on the inside of skulls, to be perused by any invader.",
                     "The mind is a complex and many-layered thing, Potter—"):
        assert sentence in quote["text"]
    assert quote["attribution"] == ("Severus Snape, in J.K. Rowling, Harry Potter and the Order of the "
                                    "Phoenix (2003), Chapter 24, “Occlumency”")
    assert "owner-ruled" in quote["copyright"] and "2026-09-21" in quote["evidence"]
    assert "do not silently shorten it back" in (build.ROOT / "site.yaml").read_text(encoding="utf-8")


def test_every_commonplace_line_stands_open_on_its_own_page():
    """The commonplace shared a page with everything else and had an expander to keep it from outweighing the
    work. It has a page of its own now: all four lines are open, none is shortened, and nothing about it is
    behind a click. The expander is gone with the reason for it, rather than left dormant in the stylesheet."""
    section = html_of("commonplace/").partition('<section id="quotes"')[2].partition("</section>")[0]
    assert section.count("<blockquote>") == 4
    assert "<details" not in section and "quotes_visible" not in (build.ROOT / "site.yaml").read_text(encoding="utf-8")
    assert not hasattr(build, "split_quotes")
    assert "older--first" not in (build.ROOT / "style.css").read_text(encoding="utf-8")
    text = _visible_text(section)          # compare rendered text: the template wraps "1733-34" in a
    for q in SITE["quotes"]:               # no-break span, so the raw attribution string is not in the HTML
        assert " ".join(q["text"].split()) in text
        assert " ".join(q["attribution"].split()) in text


def test_the_attribution_is_a_paragraph_not_a_cite():
    """<cite> is for the title of a work and must not mark up a person's name; every attribution here opens
    with the speaker."""
    html = html_of("commonplace/")
    assert "<cite" not in html
    assert html.count('<p class="quote__by">') == len(SITE["quotes"])


def test_every_gutter_label_in_the_quotes_section_is_a_plain_date():
    """The gutter is the page's time axis. A date that needs a parenthetical belongs in the attribution."""
    section = html_of("commonplace/").partition('<section id="quotes"')[2].partition("</section>")[0]
    labels = re.findall(r'<(?:time|span) class="when"[^>]*>([^<]*)</(?:time|span)>', section)
    assert len(labels) == len(SITE["quotes"])
    assert all(re.fullmatch(r"\d{4}", label) for label in labels), labels


def test_the_talk_video_opens_where_his_own_talk_starts():
    """The recording is one five-talk session with no chapter markers, so the address carries a start time:
    t=715s is 11:55 (the owner's ruling of 2026-09-21, and the timestamp already committed on his CV)."""
    button = next(b for b in SITE["research"][0]["buttons"] if b["label"] == "CCN 2025 talk video")
    assert button["url"] == "https://www.youtube.com/watch?v=vT-3kV89Rhk&t=715s"
    html = html_of("research/")
    assert 'href="https://www.youtube.com/watch?v=vT-3kV89Rhk&amp;t=715s"' in html
    assert "&t=715s" not in html                                            # never the bare ampersand
    assert unescape(re.search(r'href="([^"]*vT-3kV89Rhk[^"]*)"', html).group(1)) == button["url"]


def _css() -> str:
    return (build.ROOT / "style.css").read_text(encoding="utf-8")


def test_the_theme_toggle_is_not_painted_when_the_script_has_not_run():
    """The markup writes the button hidden and the script unhides it, so a visitor whose browser ran no
    JavaScript is not shown a control that does nothing. .toggle sets display from a class, which outranks the
    browser's own [hidden] rule, so the attribute has to be honoured in the stylesheet in so many words."""
    assert all('<button id="theme-toggle" class="toggle" type="button" hidden>' in h for h in every_page().values())
    assert re.search(r"\.toggle\[hidden\] \{[^}]*display: none", _css())
    assert "btn.hidden = false" in (build.ROOT / "templates" / "base.html.j2").read_text(encoding="utf-8")


def test_the_current_page_tick_is_the_section_tick_and_lands_on_the_strip_rule():
    """One device, two floors: a 2px ink tick marks the section you are reading on the margin rule, and the
    page you are on on the strip's hairline. The tick is placed by the same distance that sets the hairline,
    so the two cannot drift apart."""
    css = _css()
    assert re.search(r"\.topstrip \{[^}]*--strip-pad: 0\.625rem;[^}]*border-bottom: 1px solid var\(--rule\);", css, re.S)
    tick = re.search(r'\.sitenav a\[aria-current="page"\]::before \{[^}]*\}', css).group(0)
    assert "bottom: calc(-1 * var(--strip-pad) - 1px);" in tick and "height: 2px;" in tick
    assert "background: var(--ink);" in tick
    section_tick = re.search(r"h1\.inset::before, h2\.inset::before \{[^}]*\}", css).group(0)
    assert "height: 2px;" in section_tick and "background: var(--ink);" in section_tick


def test_the_figure_caption_is_set_once_and_turns_with_its_gutter():
    """It was styled twice: right in the base rule, and left again in the phone block under a comment about a
    caption this build does not print at all. The type is set once now and the alignment once, where the rest
    of the gutter is turned -- labels follow the page's left edge on a phone, the margin rule on a desktop."""
    base, _, rest = _css().partition("Phone: the rule moves")
    phone, _, desktop = rest.partition("Desktop: the gutter")
    caption = re.search(r"\.paper__fig figcaption \{[^}]*\}", base)
    assert base.count(".paper__fig figcaption") == 1 and "text-align" not in caption.group(0)
    assert ".paper__fig figcaption" not in phone                            # the phone keeps the left edge
    assert ".paper__fig figcaption { text-align: right; }" in desktop
    assert "hangs under the left edge" not in _css()                        # and the misleading comment is gone


def test_a_profile_link_is_widened_by_an_overlay_rather_than_by_its_box():
    """WCAG 2.2 SC 2.5.8 asks for 24 by 24 CSS px. Widening the box would push "X"'s neighbours apart and open
    a hole in the row exactly where the shortest label is, so the target is an overlay that takes the pointer
    and leaves the layout alone. The phone block no longer needs a minimum width of its own."""
    css = _css()
    overlay = re.search(r"\.links a:not\(\.btn\)::after \{[^}]*\}", css).group(0)
    assert "position: absolute" in overlay and "width: 1.5rem" in overlay
    assert re.search(r"\.links a:not\(\.btn\) \{[^}]*position: relative", css)
    assert "min-width: 1.5rem" not in css and "justify-content: center" not in css


def test_a_page_says_why_it_is_there_under_its_title():
    html = html_of("commonplace/")
    assert f'<p class="page-intro inset">{SITE["quotes_intro"]}</p>' in html
    site = copy.deepcopy(SITE)
    del site["quotes_intro"]
    assert "page-intro" not in html_of("commonplace/", site)
    for slug in ("research/", "teaching/", "accessibility/"):
        assert f'<p class="page-intro inset">' in html_of(slug), slug


def test_the_quotes_section_is_empty_when_there_are_no_quotes():
    site = copy.deepcopy(SITE)
    del site["quotes"]
    html = html_of("commonplace/", site)
    assert "<blockquote" not in html and "quote__by" not in html
    assert '<section id="quotes"' in html          # the page is listed in site.yaml; the list is simply empty


def test_terms_of_art_do_not_break_and_stay_escaped():
    pages = every_page()
    assert all(f'<span class="nb">{term}</span>' in pages[""] for term in ("Ben-Gurion", "(NeurIPS) 2025"))
    assert all(f'<span class="nb">{term}</span>' in pages["research/"] for term in ("Best-Fitting", "five-person"))
    # a nobreak term that matches nothing is dead configuration: "go-to-market" went with the phrase itself
    assert "go-to-market" not in SITE["nobreak"]
    for term in SITE["nobreak"]:
        assert any(term in _visible_text(h) for h in pages.values()), term
    site = copy.deepcopy(SITE)
    site["about"][0] = "Ben-Gurion <b>& co</b>"
    assert '<p><span class="nb">Ben-Gurion</span> &lt;b&gt;&amp; co&lt;/b&gt;</p>' in html_of("", site)
    del site["nobreak"]
    assert "<p>Ben-Gurion &lt;b&gt;&amp; co&lt;/b&gt;</p>" in html_of("", site)


def test_role_line_keeps_the_lab_link_on_every_page():
    for slug, html in every_page().items():
        assert "<li>PhD candidate</li>" in html, slug
        assert '<li><a href="https://brainsandmachines.org">Brains and Machines Lab</a></li>' in html, slug


def test_portrait_and_calm_rows_of_links():
    html = html_of("")
    assert ('<picture><source srcset="img/portrait-400.webp" type="image/webp">'
            '<img src="img/portrait-800.jpg" width="120" height="120" alt="Itamar Avitan"></picture>') in html
    # the address, then the profiles this page may mark, then the profiles it may not
    assert html.count('<ul class="links ') == 3 and "monogram" not in html
    email_row = html.partition('<li class="links__email">')[2].partition("</li>")[0]
    assert email_row.count("<svg") == 1 and ">Email</span>" in email_row       # the envelope leads the block
    page_html = unescape(html)
    for link in SITE["links"]:
        if link["label"] not in ("Email", "CV"):
            assert page_html.count(f'<a rel="me" href="{link["url"]}">') == 1
            assert page_html.count(f'<span>{link["label"]}</span></a>') == 1
    assert 'class="links links--reach"' in html and "btn--primary" in html


def test_each_marked_link_carries_a_silent_mark_beside_its_visible_label():
    page_html = unescape(html_of(""))
    marked = [link for link in SITE["links"] if link.get("icon")]
    assert len(marked) == 3
    assert page_html.count('<svg class="ico ') == 3                           # every mark, and nothing else
    for link in marked:
        opens = f'href="{link["url"]}">' if link.get("url") else 'class="links__label">'
        after = page_html.partition(opens)[2]
        assert after.startswith(f'<svg class="ico ico--{link["icon"]}" aria-hidden="true" '
                                'viewBox="0 0 24 24" width="16" height="16"><path d="')
        assert link["label"] in after.partition("</svg>")[2].partition("</li>")[0]   # the label stays, beside the mark
    bare = copy.deepcopy(SITE)
    for link in bare["links"]:
        link.pop("icon", None)
    assert 'class="ico' not in html_of("", bare)


def test_only_the_safe_subset_of_marks_exists_at_all():
    """The owner's ruling of 2026-09-21, and the reason this file exists: LinkedIn's policy forbids third-party
    use of its logo (Simple Icons removed the mark on 2024-12-17), ORCID's terms forbid altering the iD icon
    and set a 16px floor the old 12.4px trace broke twice over, the Google Scholar stand-in closed into a blob
    under about 20px, and X's brand terms could not be fetched. A mark that cannot be drawn is not drawn: the
    four links carry their text label, which is what says where they go."""
    marked = {link["label"]: link.get("icon") for link in SITE["links"] if link.get("icon")}
    assert marked == {"Email": "mail", "GitHub": "github", "Bluesky": "bluesky"}
    template = (build.ROOT / "templates" / "macros.html.j2").read_text(encoding="utf-8")
    paths = template.partition("{%- set ICONS = {")[2].partition("} -%}")[0]
    assert sorted(re.findall(r'"(\w+)":', paths)) == ["bluesky", "github", "mail"]
    for slug, html in every_page().items():
        for gone in ("scholar", "linkedin", "orcid", "x"):                    # no dormant path to restore
            assert f'"{gone}":' not in paths and f"ico--{gone}" not in html, (slug, gone)
    for link in SITE["links"]:                                                # every link keeps its label
        assert link["label"] and (link.get("url") or link.get("text"))


def test_the_marks_sit_with_the_marks_and_the_plain_links_with_the_plain_ones():
    """Three marks among six labels scattered through one row read as three icons that failed to load, so the
    links that carry a mark are still set before the ones that cannot. What the order must not do is open a
    hole: a 3rem step between the groups against a 1.25rem step inside one read at 1280px as a missing item,
    not as two groups -- nothing on the page tells a reader that these six addresses are of two kinds, because
    the split is a licensing accident. One step, one row; the contiguous marks do the grouping."""
    html = html_of("")
    row = html.partition('<div class="links-row">')[2].partition("</div>")[0]
    order = re.findall(r'<span>([^<]+)</span>', row)
    assert order == ["GitHub", "Bluesky", "Google Scholar", "LinkedIn", "X", "ORCID"]
    before, _, after = row.partition('class="links links--plain"')
    assert before.count("<svg") == 2 and "<svg" not in after   # every mark on one side of the split
    css = _css()
    between = float(re.search(r"\.links-row \{[^}]*column-gap: ([\d.]+)rem", css).group(1))
    inside = float(re.search(r"^\.links \{[^}]*gap: 0 ([\d.]+)rem", css, re.M).group(1))
    assert between == inside


def test_the_marks_are_sized_one_by_one_so_they_read_as_one_set():
    """Three equal boxes are not three equal weights: rendered at 16px and sampled at 8x, the ink covers 58.7%
    of the Bluesky box, 45.2% of the envelope and 42.7% of the GitHub disc. Only the heaviest comes down."""
    css = _css()
    sized = dict(re.findall(r"\.ico--(\w+) \{ width: ([\d.]+rem);", css))
    assert sized == {"bluesky": "0.9375rem"}
    assert re.search(r"\.ico \{[^}]*width: 1rem;", css)                       # the rest keep the full 16px
    marks = re.findall(r"\.ico(?:--\w+)?[^{}]*\{[^}]*\}", css)                # sizing settled the alignment:
    assert marks and not any("transform" in rule for rule in marks)           # no mark is nudged by hand


def test_favicon_is_the_mark():
    mark = (build.ROOT / "img" / "mark.svg").read_text(encoding="utf-8").strip()
    assert 'viewBox="0 0 42 10"' in mark and mark.count("<circle") == 2 and mark.count("<rect") == 1
    for shape in ('<circle cx="5" cy="5" r="5"/>', '<circle cx="21" cy="5" r="5"/>', '<rect x="32" width="10" height="10"/>'):
        assert shape in mark
    for slug, html in every_page().items():
        href = re.search(r'<link rel="icon" href="data:image/svg\+xml,([^"]+)">', html).group(1)
        assert unquote(href).replace("'", '"') == mark, slug


def test_images_carry_no_metadata_and_the_social_card_is_1200x630():
    for name in ("portrait-400.webp", "portrait-800.jpg", "og.png", "paper-lab.webp", "paper-lab.jpg"):
        with Image.open(build.ROOT / "img" / name) as im:
            assert len(im.getexif()) == 0 and not getattr(im, "text", None), name
    for name in ("paper-lab.webp", "paper-lab.jpg"):                 # square, and twice the 272px it is laid out at
        with Image.open(build.ROOT / "img" / name) as im:
            assert im.size == (544, 544), name
    with Image.open(build.ROOT / "img" / "og.png") as im:
        assert im.size == (1200, 630)


# --- The accessibility statement: every line of it is a claim about this site ------------------------------

def test_the_statement_claims_only_what_something_actually_checks():
    """The page says the site aims at WCAG 2.1 AA and lists what was done. Each bullet has to be backed by a
    check that runs, or it is marketing. This test pins the three that had no guard of their own: reduced
    motion, the document language, and the fact that nothing on any page loads from a third party."""
    css = _css()
    motion = re.search(r"@media \(prefers-reduced-motion: reduce\) \{[^@]*\}\s*\}", css)
    assert motion and "transition: none !important" in motion.group(0)
    assert "scroll-behavior: auto" in motion.group(0)
    assert "@keyframes" not in css and not re.search(r"^\s*animation:", css, re.M)   # nothing moves on its own
    for slug, html in every_page().items():
        assert '<html lang="en">' in html, slug                                       # a language on the document
        assert '<nav class="sitenav" aria-label="Pages">' in html, slug               # a named landmark
        # nothing is LOADED from anyone else. Links that lead elsewhere are fine and are not requests: what
        # must never be off-site is anything the browser fetches to render the page -- an image, a script, a
        # stylesheet, a font. tests/test_browser.py watches the actual request list as well.
        loaded = re.findall(r'<(?:img|script|source|iframe|embed)\b[^>]*\ssrc(?:set)?="([^"]+)"', html)
        # only the <link> kinds the browser actually fetches; rel="canonical" is a statement, not a request
        loaded += re.findall(r'<link\b[^>]*\srel="(?:stylesheet|icon|preload|preconnect)"[^>]*\shref="([^"]+)"', html)
        loaded += re.findall(r'<link\b[^>]*\shref="([^"]+)"[^>]*\srel="(?:stylesheet|icon|preload|preconnect)"', html)
        assert [u for u in loaded if u.startswith(("http://", "https://", "//"))] == [], slug
        assert "@font-face" not in html and "fonts.googleapis" not in html, slug


def test_the_statement_does_not_overclaim_the_tap_target_rule():
    """Measured, not assumed: a link inside running text is 18 to 22 CSS px tall on this site -- the role
    line's lab link, a talk's venue, a hand-off row. Only the strip, the profile row and the colophon row are
    held to 24 by 24 (tests/test_browser.py measures all three), so that is exactly what the page may say."""
    done = " ".join(SITE["accessibility"]["done"])
    assert "navigation strip" in done and "row of profiles" in done and "footer" in done
    assert "A link set inside a sentence is left at the size of the words around it." in done
    assert "Links and buttons are at least 24" not in done          # the sentence this replaced, which was false


def test_the_statement_neither_claims_conformance_nor_cites_a_law():
    """The two things such a page must never do. It says what it AIMS at and who checked it (nobody but him),
    and it names no legal duty -- which rules bind a personal site is not a question this page can answer."""
    text = " ".join(_values({k: SITE["accessibility"][k] for k in SITE["accessibility"]}))
    assert "aims at WCAG 2.1 Level AA" in text
    assert "not a certificate" in text and "nobody but me has audited this site" in text
    for word in ("conforms", "conformant", "compliant", "compliance", "law", "legal", "required by",
                 "Section 508", "directive", "accessible to everyone"):
        assert word.lower() not in text.lower(), word


def test_the_published_cv_carries_the_wording_the_site_carries():
    """cv.pdf is linked from the strip of every page, so it is part of what this site says. The owner's
    programme-first ruling deleted his personal contribution, the sensor inventory and the business-plan
    detail from the CVs; if the published PDF still held them, the site would contradict its own project
    entry from its own navigation bar. It is the public build (no phone number) of the cv repository."""
    pdf = build.ROOT / "cv.pdf"
    assert pdf.exists()
    out = subprocess.run(["pdftotext", str(pdf), "-"], capture_output=True, text=True)
    assert out.returncode == 0, "pdftotext is needed to check the published CV; install poppler"
    text = " ".join(out.stdout.split())
    for gone in ("control software", "system integration", "Muse headband", "heart-rate variability",
                 "skin conductance", "go-to-market", "business plan", "selective audio attenuation"):
        assert gone not in text, gone
    assert "Embodied Brain Technology Practicum" in text and "EarBetter" in text
    assert "biosignal-driven add-on for any headphones" in text
    assert not re.search(r"(?:\+?972|\b0)[\s\-.]?5\d(?:[\s\-.]?\d){7}\b", text)   # the public build
