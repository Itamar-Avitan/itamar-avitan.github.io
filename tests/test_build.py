import bisect
import copy
import json
import re
import shutil
import subprocess
from html import unescape
from pathlib import Path

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
# The two profiles the home page repeats beside the address (the `pick` of `contact`, templates/pages/home.html.j2).
PICK = ["GitHub", "Google Scholar"]


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


def _graph(slug: str, site: dict | None = None) -> dict:
    """The JSON-LD graph of a page, by node type."""
    site = site or SITE
    return {n["@type"]: n for n in json.loads(build.person_jsonld(site, page(slug, site)))["@graph"]}


def test_person_jsonld_is_one_entity_on_every_page_with_his_profile_and_his_paper():
    """One graph, one Person with one @id on every page, so a crawler merges the copies into one entity and
    tells it from his namesake's (deep review 2026-09-25, MR-06). The home page says it is his profile page,
    the research page files the paper under him by the DOI the CVs print (FACTS PUB-DOI) and the arXiv id,
    and no other page claims a paper. Every field is generated from site.yaml, never pasted."""
    for slug in SLUGS:
        data = _graph(slug)["Person"]
        assert data["@id"] == SITE["site_url"] + "#person", slug           # one entity, every page
        assert data["name"] == "Itamar Avitan"
        assert data["alternateName"] == "Itamar Amram Avitan" and data["url"] == SITE["site_url"]
        assert data["description"] == SITE["identity_line"]
        assert data["image"] == SITE["site_url"] + SITE["portrait"]["jpg"]
        assert all(u.startswith("https://") for u in data["sameAs"]) and "email" not in data
        assert len(data["sameAs"]) == 6 and "cv.pdf" not in data["sameAs"]
        assert not any("hl=" in u for u in data["sameAs"])                # a profile's address, no interface language
        assert data["jobTitle"] == "PhD candidate" and "NeuroAI" in data["knowsAbout"]
        assert data["affiliation"] == data["alumniOf"] == {
            "@type": "CollegeOrUniversity", "name": SITE["affiliation"], "url": SITE["affiliation_url"]}
        assert data["memberOf"] == {"@type": "ResearchOrganization", "name": "Brains and Machines Lab",
                                    "url": "https://brainsandmachines.org"}
    assert "same_as" not in SITE
    home = json.loads(build.person_jsonld(SITE, page("")))["@graph"]
    assert [n["@type"] for n in home] == ["ProfilePage", "Person"]
    assert home[0]["mainEntity"] == {"@id": SITE["site_url"] + "#person"} and home[0]["url"] == SITE["site_url"]
    art = _graph("research/")["ScholarlyArticle"]
    assert art["name"] == art["headline"] == SITE["research"][0]["title"]
    assert art["author"] == [{"@id": SITE["site_url"] + "#person"}, {"@type": "Person", "name": "Tal Golan"}]
    assert art["sameAs"] == ["https://doi.org/10.52202/085713-0404", "https://arxiv.org/abs/2510.23321"]
    assert art["url"].startswith("https://proceedings.neurips.cc/")
    assert art["datePublished"] == "2025" and art["isPartOf"]["volumeNumber"] == "38"
    assert art["isPartOf"]["isPartOf"] == {"@type": "Periodical", "name": "Advances in Neural Information Processing Systems"}
    for slug in SLUGS:
        assert ("ScholarlyArticle" in _graph(slug)) == (slug == "research/"), slug
    not_found = json.loads(build.person_jsonld(SITE, build.not_found_page(SITE)))["@graph"]
    assert [n["@type"] for n in not_found] == ["Person"]                   # the 404 page is nobody's profile


def test_structured_data_names_only_what_a_page_prints():
    """`knowsAbout` earns no rich result; what it may do is describe the visible page, so every term in it has
    to be printed somewhere on the site (deep review 2026-09-25, EM-14's rule narrowing MR-06). And the DOI the
    graph cites is the one the BibTeX block prints: both come from FACTS PUB-DOI, and the build refuses a
    disagreement."""
    body = " ".join(_visible_text(h.partition("<body")[2]) for h in every_page().values()).lower()
    for term in SITE["knows_about"]:
        assert term.lower() in body, term
    card = SITE["research"][0]
    assert card["doi"] in card["cite"] and "https://doi.org/" + card["doi"] in html_of("research/")
    bad = copy.deepcopy(SITE)
    bad["research"][0]["doi"] = "10.0000/not-the-one-in-the-cite-block"
    assert [p for p in build.validate(bad) if "doi" in p], build.validate(bad)


def test_jsonld_cannot_break_out_of_its_script_element():
    site = copy.deepcopy(SITE)
    site["identity_line"] = 'x</script><script>alert(1)</script>'
    assert _graph("", site)["Person"]["description"] == site["identity_line"]
    html = build.render(site)
    assert "</script><script>" not in html and "alert(1)</script>" not in html
    block = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S).group(1)
    assert {n["@type"]: n for n in json.loads(block)["@graph"]}["Person"]["description"] == site["identity_line"]


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
    assert f"<title>{build.page_title(SITE, page(''))}</title>" in (tmp_path / "index.html").read_text(encoding="utf-8")
    for slug in SLUGS:
        assert (tmp_path / slug / "index.html").exists(), slug
    sitemap = (tmp_path / "sitemap.xml").read_text(encoding="utf-8")
    assert [m for m in re.findall(r"<loc>([^<]+)</loc>", sitemap)] == [SITE["site_url"] + s for s in SLUGS]
    assert "Allow: /" in (tmp_path / "robots.txt").read_text(encoding="utf-8")


def test_the_404_page_is_built_beside_the_site_and_not_as_part_of_it(tmp_path, monkeypatch):
    """GitHub Pages serves /404.html, with status 404, for any address that has nothing at it, at any depth.
    So the page is written with root-absolute links, asks not to be indexed, has no canonical and no og:url
    (it has no address of its own), is in no link list and not in the sitemap, and says where everything
    else is (deep review 2026-09-25, MR-08 and PA-02)."""
    # the sentence is the last thing in main, and its margin must not fall out of main: the margin rule is
    # drawn to main's foot, and would otherwise break for that height above the colophon (measured, MR-08)
    assert ".page-intro:last-child { margin-bottom: 0; }" in _css()
    shutil.copytree(build.ROOT / "templates", tmp_path / "templates")
    shutil.copy(build.ROOT / "site.yaml", tmp_path / "site.yaml")
    monkeypatch.setattr(build, "ROOT", tmp_path)
    assert build.main([]) == 0
    html = (tmp_path / "404.html").read_text(encoding="utf-8")
    assert '<meta name="robots" content="noindex">' in html
    assert 'rel="canonical"' not in html and 'property="og:url"' not in html
    for target in re.findall(r'\s(?:href|src|srcset)="([^"]+)"', html):
        assert target.startswith(("/", "http", "data:", "#")), target
    assert "404" not in (tmp_path / "sitemap.xml").read_text(encoding="utf-8")
    assert build.validate(SITE) == []
    text = _visible_text(html.partition("<body")[2])
    assert SITE["not_found"]["heading"] in text and SITE["not_found"]["intro"] in text
    assert html.count("<h1") == 1 and 'class="opens-with-h1"' in html
    # the address may break after "//", before a dot of the host and after its slash, never at its hyphen
    assert ('The CV is at <a href="/cv.pdf">https://<wbr><span class="nb">itamar-avitan</span><wbr>.github<wbr>.io/'
            '<wbr>cv.pdf</a>.') in html
    strip = html.partition('<nav class="sitenav"')[2].partition("</nav>")[0]
    assert re.findall(r'href="([^"]*)"', strip) == ["/"] + [f"/{s}" for s in NAV_SLUGS[1:]] + ["/cv.pdf"]
    assert 'aria-current="page"' not in html                                  # it is nowhere in the site
    assert '<span id="email">' in html and 'href="/style.css"' in html
    without = copy.deepcopy(SITE)
    without.pop("not_found")
    monkeypatch.setattr(build, "load_site", lambda _p: without)
    (tmp_path / "404.html").unlink()
    assert build.main([]) == 0 and not (tmp_path / "404.html").exists()      # the entry is optional


def test_a_printed_address_breaks_only_where_a_reader_can_follow():
    """An address printed as text (the 404 page's CV sentence) may break after the scheme, before a dot of
    the host and after the slash that ends it, and never at a hyphen inside a host label: every engine
    broke "https://itamar-avitan.github.io/cv.pdf" after "itamar-" on a 320px screen, which reads as a
    hyphenation (deep review 2026-09-25, WP-S6 review). The visible text is the address, unchanged."""
    printed = build.printable_url("https://itamar-avitan.github.io/cv.pdf")
    assert printed == 'https://<wbr><span class="nb">itamar-avitan</span><wbr>.github<wbr>.io/<wbr>cv.pdf'
    assert build.printable_url("https://example.org/") == "https://<wbr>example<wbr>.org/"     # nothing after the slash
    assert build.printable_url("https://a-b.c-d.org/x") == ('https://<wbr><span class="nb">a-b</span>'
                                                            '<wbr>.<span class="nb">c-d</span><wbr>.org/<wbr>x')
    for url in ("https://itamar-avitan.github.io/cv.pdf", "https://example.org/", "https://a-b.c-d.org/x"):
        assert unescape(re.sub(r"<[^>]+>", "", build.printable_url(url))) == url
    assert "&lt;x&gt;" in build.printable_url("https://a-b.org/<x>") and "<x>" not in build.printable_url("https://a-b.org/<x>")


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
        title = page(slug).get("title") or (f"{heading} — Itamar Avitan" if heading else "Itamar Avitan")
        assert "Itamar Avitan" in title and "Amram" not in title, slug     # spec 4.5: the name exactly as printed
        assert f"<title>{title}</title>" in html and f'property="og:title" content="{title}"' in html, slug
        assert f'name="description" content="{build.page_description(SITE, page(slug))}"' in html, slug


def test_the_home_title_and_description_say_who_he_is():
    """A search result that says only "Itamar Avitan" cannot be told from his namesake's, which outranks this
    site for the bare name (deep review 2026-09-25, MR-03). The home title leads with the name exactly as the
    <h1> prints it and then says the field, the status and the university; the description says the same and
    ends on the identity line, which it follows when that line changes (owner ruling 17)."""
    home = page("")
    assert home["title"].startswith(SITE["name"] + " ") and "Amram" not in home["title"]
    assert home["description"].endswith(SITE["identity_line"])
    for words in ("NeuroAI", "PhD candidate", "Ben-Gurion University"):
        assert words in home["title"] and words in home["description"], words
    assert build.page_title(SITE, home) == home["title"]
    assert build.page_title(SITE, {"slug": "x/", "heading": "X"}) == "X — Itamar Avitan"
    assert build.page_title(SITE, {"slug": "x/"}) == "Itamar Avitan"


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
        labels = re.findall(r'<a [^>]*href="([^"]*)"([^>]*)>([^<]+)(?:<span class="vh">[^<]*</span>)?</a>', strip)
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


def _cv_pages() -> int:
    out = subprocess.run(["pdfinfo", str(build.ROOT / "cv.pdf")], capture_output=True, text=True)
    assert out.returncode == 0, "pdfinfo is needed to check the CV chip's label; install poppler"
    return int(re.search(r"^Pages:\s+(\d+)", out.stdout, re.M).group(1))


def test_the_cv_is_one_click_from_every_page():
    """The chip reads "CV" and its accessible name goes on "(PDF, 2 pages)": what opens, and how long it is,
    before it opens (deep review 2026-09-25, PA-07). The count is read off the published file, so a CV that
    grows to three pages fails here until site.yaml says so."""
    pages = _cv_pages()
    for slug, html in every_page().items():
        base = "../" if slug else ""
        chip = (f'<a class="btn btn--primary" href="{base}cv.pdf" type="application/pdf">CV'
                f'<span class="vh"> (PDF, {pages} pages)</span></a>')
        assert html.count(chip) == 1, slug


def test_every_page_says_whose_site_it_is_without_repeating_the_bio():
    """A visitor landing on a deep page sees the name, the role line and the portrait at the top and the
    address at the foot. The three paragraphs of "about" are said once, at home."""
    for slug, html in every_page().items():
        assert SITE["name"] in build._visible_text(html) if False else True
        assert html.count('class="role"') == 1, slug
        assert html.count('id="email"') == 1, slug
        assert html.count(SITE["links"][0]["text"]) == 1, slug
        # the portrait is there, and it is decorative: the name is the next word (deep review, PA-05)
        assert re.search(r'<img src="(?:\.\./)?img/portrait-800\.jpg" width="\d+" height="\d+" alt="">', html), slug
        if slug:
            assert '<div class="byline inset">' in html and "masthead" not in html, slug
            assert '<footer class="colophon">' in html and "links-nav" in html.partition("<footer")[2], slug
        else:
            assert '<div class="masthead">' in html and "byline" not in html
    home = html_of("")
    # the first sentence, its spaces collapsed as _visible_text collapses them: two paragraphs tie their em
    # dash to the word before it with a no-break space, which str.split() treats as a space
    first = [" ".join(paragraph.split(".")[0].split()) for paragraph in SITE["about"]]
    assert all(sentence in _visible_text(home) for sentence in first)
    assert sum(1 for h in every_page().values() if first[0] in _visible_text(h)) == 1


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
    # one way out of each row, plus the paper and its code under the Research row (2026-09: the two objects a
    # visitor came for). The extras are the first paper card's own: its first venue badge in the gutter under
    # the label -- the one outlined box outside the card -- and its Paper and Code addresses, so nothing here
    # can drift from the card (deep review 2026-09-25, DS-09 and AS-02).
    assert section.count("<a ") == 5
    card = SITE["research"][0]
    research_row = section.partition('<span class="tag">Research</span>')[2].partition("</li>")[0]
    venue = next(b for b in card["badges"] if " · " not in b)       # the card's own condition: a " · " is a presentation
    assert f'<p class="go-venue"><span class="tag tag--venue">{venue}</span></p>' in research_row
    assert section.count('class="tag tag--venue"') == 1
    paper = card["buttons"][0]
    code = next(b for b in card["buttons"] if b["label"] == "Code")
    assert paper["label"] == "Paper"
    assert (f'<p class="go-links"><a href="{paper["url"]}">Paper<span class="vh"> {paper["context"]}</span></a> · '
            f'<a href="{code["url"]}">Code<span class="vh"> {code["context"]}</span></a></p>') in unescape(research_row)
    assert re.findall(r'<a href="([^"]+)">', unescape(research_row))[1:] == [paper["url"], code["url"]]
    assert research_row.index('class="go-venue"') < research_row.index('class="what"') < research_row.index('class="go-links"')
    assert "go-links" not in research_row.partition('class="what"')[2].partition("</p>")[0]   # outside .what: not a title
    # the box goes to the first venue, not to the first badge: with the card's list turned round, the talk
    # ("CCN 2025 · Talk", a neutral tag on the card) stays off the row and the venue still takes the box
    talk = next(b for b in card["badges"] if " · " in b)
    turned = copy.deepcopy(SITE)
    turned["research"][0]["badges"] = list(reversed(card["badges"]))
    turned_section = html_of("", turned).partition('<section id="elsewhere"')[2].partition("</section>")[0]
    assert f'<p class="go-venue"><span class="tag tag--venue">{venue}</span></p>' in turned_section
    assert talk not in turned_section and turned_section.count('class="tag tag--venue"') == 1
    for slug in SLUGS[1:]:                            # and only the home page hands off; no page does it twice
        assert 'id="elsewhere"' not in html_of(slug), slug


def test_the_address_is_said_at_the_top_and_the_profiles_at_the_foot():
    """Six profile links between the lede and the first word of the home page cost about 120px of a phone
    screen, and they are not what a visitor comes to a front door for. The address keeps the masthead; the
    profiles stand in the colophon of every page. Each page still says each of them exactly once, except the
    two the front door repeats beside the address (GitHub and Google Scholar, the ones a recruiter and a peer
    open first: deep review 2026-09-25, AS-25) -- and only one element on any page carries id="email", which
    is the hook the script turns into a mailto."""
    home = html_of("")
    masthead = home.partition('<div class="masthead">')[2].partition("<main")[0]
    assert 'class="links links--reach"' in masthead and 'class="links-row"' not in masthead
    # the two picked profiles are items of the reach list itself, after the address, in the order picked:
    # GitHub with its mark first, so the marks stay together at the row's left as they do in the colophon
    assert re.findall(r'<span>([^<]+)</span></a>', masthead) == PICK
    for slug in SLUGS[1:]:
        assert 'rel="me"' not in html_of(slug).partition("<main")[0], slug     # only the front door does this
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
    # an address is not text: full ones start with http, and the "elsewhere" rows and the news links point at
    # this site's own slugs, with or without an anchor ("research/#papers"), which the template turns into a
    # relative link rather than printing
    addresses = set(SLUGS)
    missing = [s for s in _values(wanted)
               if not s.startswith("http") and s.split("#")[0] not in addresses and " ".join(s.split()) not in text]
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
    assert 'BCI4ALS</a>' not in html_of("research/")        # BCI4ALS is never linked (ruling)


def test_open_graph_points_at_the_site_and_its_card():
    """The home page is his profile (og:type profile, his first and last name); every other page is a page of
    the site. The card's alt text says what the card shows: the name, the role line, the identity line -- and
    on the research page, the paper (deep review 2026-09-25, MR-09, PA-08, DI-09)."""
    html = html_of("")
    role = " · ".join(r["label"] for r in SITE["role"])
    for needle in (f'property="og:title" content="{page("")["title"]}"', 'property="og:type" content="profile"',
                   'property="profile:first_name" content="Itamar"', 'property="profile:last_name" content="Avitan"',
                   'property="og:site_name" content="Itamar Avitan"', 'property="og:locale" content="en_US"',
                   'property="og:url" content="https://itamar-avitan.github.io/"',
                   'property="og:image" content="https://itamar-avitan.github.io/img/og.png"',
                   f'property="og:image:alt" content="Itamar Avitan. {role}. {SITE["identity_line"]}"',
                   f'property="og:description" content="{page("")["description"]}"'):
        assert needle in html, needle
    research = html_of("research/")
    assert 'property="og:type" content="website"' in research and "profile:" not in research
    assert f'property="og:image:alt" content="{page("research/")["og_image_alt"]}"' in research
    for slug, html in every_page().items():
        picture = page(slug).get("og_image", "img/og.png")
        assert picture == ("img/og-research.png" if slug == "research/" else "img/og.png"), slug
        assert f'property="og:image" content="{SITE["site_url"]}{picture}"' in html, slug
        assert html.count('property="og:image"') == 1 and html.count('property="og:image:alt"') == 1, slug
        assert 'property="og:site_name" content="Itamar Avitan"' in html, slug
        assert bool(page(slug).get("og_image")) == bool(page(slug).get("og_image_alt")), slug   # the two go together


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
             card["venue"], *card["badges"], *(b["label"] for b in card["buttons"]),
             *card["how"], card["found"], *card["open"]]
    assert [s for s in shown if " ".join(s.split()) not in text] == []
    # the depth blocks (deep review 2026-09-25, WP-S3): four stages under "How the test works", each the
    # caption of one mark of the diagram; what was found; the paper's open questions; the citation last
    assert len(card["how"]) == 4 and len(card["open"]) == 3
    assert '<ol class="recovery-diagram" role="list" aria-label="How the model-recovery test works">' in html
    for runin in ("Summary", "How the test works", "What we found", "Questions the paper leaves open"):
        assert f'<span class="runin">{runin}</span>' in html, runin
    body = html.partition('<div class="paper__body">')[2].partition("</article>")[0]
    assert (body.index('class="tldr"') < body.index("How the test works") < body.index('class="recovery-diagram"')
            < body.index("What we found") < body.index('class="open"') < body.index('<details class="cite">'))
    # unescaped, because an address with two query parameters carries "&", which a document writes "&amp;"
    assert all(f'href="{b["url"]}"' in unescape(html) for b in card["buttons"])
    assert f'<img src="../{card["figure"]["src"]}" alt="{card["figure"]["alt"]}"' in html
    assert '<span class="me">Itamar Avitan</span>, Tal Golan' in html
    assert html.count("btn btn--primary") == 2              # the paper's Paper button, and the CV in the strip
    # the title is the card's primary link, to the paper itself (the first button's address)
    assert f'<h3><a href="{SITE["research"][0]["buttons"][0]["url"]}">' in html
    assert '<p class="venue paper__venue">' in html
    # the links stand above the summary: they are why most readers arrive
    body = html.partition('<div class="paper__body">')[2].partition("</article>")[0]
    assert body.index('class="authors"') < body.index('class="venue paper__venue"') < body.index('class="buttons"') < body.index('class="tldr"')
    assert '<span class="tag tag--venue">NeurIPS 2025</span>' in html and '<span class="tag">CCN 2025 · Talk</span>' in html


def test_the_card_figure_is_swapped_by_one_line_and_only_a_plot_is_marked_as_one():
    """site.yaml keeps both pictures; research[0].figure points at one of them by YAML alias. The owner chose
    the paper's own Figure 1D over the lab illustration on 2026-09-25 (deep review, ruling 3)."""
    lab, plot = SITE["figure_options"]["lab_illustration"], SITE["figure_options"]["recovery_matrix"]
    html = html_of("research/")
    assert SITE["research"][0]["figure"] is plot                             # the labelled matrix is live
    assert f'<img src="../{plot["src"]}" alt="{plot["alt"]}" width="272" height="272">' in html
    assert '<figure class="paper__fig paper__fig--plot">' in html            # a plot, whose colour is data
    assert "<figcaption>Adapted from Fig.\u00a01D" in html                # a no-break space ties the number
    assert "is a wrong\u00a0winner.</figcaption>" in html                 # and the last pair, in every engine
    assert "paper__fig--art" not in html and lab["webp"] not in html and lab["src"] not in html
    site = copy.deepcopy(SITE)
    site["research"][0]["figure"] = site["figure_options"]["lab_illustration"]
    other = html_of("research/", site)
    assert f'<picture><source srcset="../{lab["webp"]}" type="image/webp"><img src="../{lab["src"]}"' in other
    assert '<figure class="paper__fig paper__fig--art">' in other            # a picture, not a plot
    assert "paper__fig--plot" not in other and "<figcaption" not in other and plot["src"] not in other


def test_the_matrix_is_labelled_and_is_the_figure_its_description_says():
    """Figure 1D adapted: a picture inside <img> cannot use the page's webfont, so the axes are named in real
    text inside the SVG, small, and the caption carries the meaning. The alt text describes the drawing --
    twenty rows, most on the diagonal, seven attributed to the last column -- so the drawing is held to it."""
    svg = (build.ROOT / SITE["figure_options"]["recovery_matrix"]["src"]).read_text(encoding="utf-8")
    assert svg.count("<text") >= 4 and "adapted, axes labeled" in svg
    assert "recovered model" in svg and "generating model" in svg           # the axes, named
    assert re.search(r'font-size="\d+">1</text>', svg) and re.search(r'font-size="\d+">20</text>', svg)
    cells = [(int(x), int(y)) for x, y in re.findall(r'<rect x="(\d+)" y="(\d+)" width="10" height="10"', svg)]
    rows = sorted({y for _, y in cells})
    assert rows == list(range(0, 200, 10))                                    # twenty rows, one model each
    diagonal = {y for x, y in cells if x == y}
    last_column = {y for x, y in cells if x == 190 and y != 190}              # off the diagonal, in column 20
    assert len(diagonal - last_column) + len(last_column) == 20               # every row is one or the other
    assert len(last_column) == 7                                              # "seven ... in the last column"
    assert "seven are mostly or entirely attributed to one model in the last column" in SITE["figure_options"]["recovery_matrix"]["alt"]


def test_the_diagram_is_four_silent_marks_captioned_by_the_four_stages():
    """The owner's diagram (ruling 3, 2026-09-25): one mark per stage, no text and no numeral inside any of
    them -- the captions are the labels, with a visible counter -- and exactly one accent object in a stage,
    set in a style attribute (a var() in a presentation attribute is what the sanitizers drop). Stage 3 has
    none: there the comparison does not yet know which network generated the answers."""
    html = html_of("research/")
    strip = html.partition('<ol class="recovery-diagram"')[2].partition("</ol>")[0]
    marks = re.findall(r"<svg.*?</svg>", strip, flags=re.S)
    assert len(marks) == 4 and strip.count("<li>") == 4
    for mark in marks:
        assert 'aria-hidden="true" viewBox="0 0 120 64"' in mark and "<text" not in mark
        assert 'fill="var(' not in mark and 'stroke="var(' not in mark
        assert re.search(r'stroke-width="(\d+(?:\.\d+)?)"', mark) is None or all(
            float(w) >= 1.5 for w in re.findall(r'stroke-width="(\d+(?:\.\d+)?)"', mark))
    assert [mark.count("var(--accent)") for mark in marks] == [1, 1, 0, 1]
    captions = re.findall(r"</svg><p>(.*?)</p></li>", strip, flags=re.S)
    assert [" ".join(_visible_text(c).split()) for c in captions] == [" ".join(s.split()) for s in SITE["research"][0]["how"]]
    css = _css()
    assert "grid-template-columns: repeat(auto-fit, minmax(min(8.5rem, 100%), 1fr));" in css
    assert 'content: counter(step) ". ";' in css
    phone = _css().partition("Phone: the rule moves")[2].partition("Desktop: the gutter")[0]
    assert ".paper .recovery-diagram, .paper .open { order: 6; }" in phone and ".paper .cite { order: 7; }" in phone


def test_the_citation_is_the_registered_entry_behind_an_expander():
    """Every field is FACTS PUB-CITATION plus the DOI of PUB-DOI, verbatim, and no page range: the two
    published paginations disagree, and one of them carries a "12" the privacy guard forbids."""
    card, html = SITE["research"][0], html_of("research/")
    assert '<details class="cite"><summary>Cite (BibTeX)</summary><pre>' in html
    entry = unescape(html.partition('<details class="cite"><summary>Cite (BibTeX)</summary><pre>')[2].partition("</pre>")[0])
    assert entry == card["cite"]
    assert entry.startswith("@inproceedings{avitan2025modelbehavior,") and entry.endswith("}")
    assert "doi       = {10.52202/085713-0404}" in entry and "pages" not in entry
    assert "author    = {Avitan, Itamar and Golan, Tal}" in entry and "year      = {2025}" in entry
    assert "12" not in entry


def test_a_figure_whose_colour_is_data_is_never_hue_shifted():
    """A plot's colours are its data. No theme dims or filters a figure now (the next test pins `none` in
    every theme); this one keeps the older, weaker line as a floor: if a scale ever came back it could only be
    a brightness scale, because scaling the three channels together leaves every hue and saturation where it
    was."""
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
    """Exercised on a fixture, because the live list is shorter than `news_visible` (deep review 2026-09-25,
    FL-12: the expander hid the CCN 2025 talk and the PhD start, the start of the story the page tells)."""
    site = copy.deepcopy(SITE)
    site["news_visible"] = 2
    shown, older = build.split_news(site["news"], site["news_visible"])
    before, _, after = html_of("", site).partition('<details class="older">')
    assert older and all(n["text"] in _visible_text(before) for n in shown)
    inside = _visible_text(after.split("</details>")[0])
    assert all(n["text"] in inside for n in older) and f"Older news ({len(older)})" in inside
    site["news_visible"] = 99
    assert "<details" not in html_of("", site)
    # the live page carries the expander exactly when the list is longer than news_visible
    assert ('<details class="older">' in html_of("")) == (len(SITE["news"]) > SITE["news_visible"])


def _news_rows(html: str) -> list[str]:
    section = html.partition('<section id="news"')[2].partition("</section>")[0]
    return re.findall(r'<li class="row">(.*?)</li>', section, re.S)


def test_a_news_item_links_the_thing_it_names_and_nothing_else():
    """Four items name an artefact a reader can open -- the NEAT page, the paper's card, the code and data,
    the arXiv preprint -- and each links that one phrase; the other items carry no link at all. The phrase
    is the first occurrence of `link` in the sentence, and the address is `url` made relative to the page.
    The sentence has to survive the split: the pieces around the link go through nb() one by one."""
    rows = _news_rows(html_of(""))
    assert len(rows) == len(SITE["news"])
    assert {n["date"] for n in SITE["news"] if n.get("link")} == {"Sep 2026", "Dec 2025", "Nov 2025", "Oct 2025"}
    for item, row in zip(SITE["news"], rows):
        anchors = re.findall(r'<a href="([^"]*)">(.*?)</a>', row, re.S)
        if item.get("link"):
            assert len(anchors) == 1 and row.count("<a ") == 1, item["date"]
            href, label = anchors[0]
            assert href == build.local(item["url"], "") and _visible_text(label) == item["link"], item["date"]
            assert " ".join(item["text"].split()) in _visible_text(row), item["date"]
        else:
            assert "<a " not in row, item["date"]
    # the Sep 2026 phrase holds two no-break terms, and they are still wrapped inside the link
    neat = next(r for i, r in zip(SITE["news"], rows) if i["date"] == "Sep 2026")
    assert '<span class="nb">Neuro-AI-Talks</span> <span class="nb">(NEAT) 2026</span></a>' in neat


def test_the_build_refuses_a_news_link_that_is_not_in_its_sentence():
    """A rewritten sentence must re-choose its phrase: a `link` the text no longer contains is a dead key,
    and a `link` without a `url`, or the reverse, is half an instruction."""
    site = copy.deepcopy(SITE)
    item = next(n for n in site["news"] if n.get("link"))
    item["link"] = "a phrase the sentence does not contain"
    assert any(item["date"] in p and "is not in its text" in p for p in build.validate(site))
    site = copy.deepcopy(SITE)
    item = next(n for n in site["news"] if n.get("link"))
    del item["url"]
    assert any(item["date"] in p and "only one of link/url" in p for p in build.validate(site))
    site = copy.deepcopy(SITE)
    item = next(n for n in site["news"] if not n.get("link"))
    item["url"] = "research/"
    assert any(item["date"] in p and "only one of link/url" in p for p in build.validate(site))


def test_the_build_refuses_a_news_link_that_cuts_through_a_nobreak_term():
    """The template wraps the no-break terms in each piece around the phrase, so a phrase may hold whole
    terms (the Sep 2026 one holds two) but never part of one: "NeurIPS" inside "(NeurIPS) 2025" would leave
    that term split across the pieces and silently without its span."""
    assert build.validate(SITE) == []
    site = copy.deepcopy(SITE)
    item = next(n for n in site["news"] if "(NeurIPS) 2025" in n["text"])
    item["link"], item["url"] = "NeurIPS", "research/#papers"
    problems = build.validate(site)
    assert any(item["date"] in p and "cuts through" in p and "(NeurIPS) 2025" in p for p in problems), problems
    item["link"] = "(NeurIPS) 2025"                                   # the whole term inside the phrase is fine
    assert build.validate(site) == []
    item["link"] = "Systems (NeurIPS) 2025 in"                        # and so is a phrase around it
    assert build.validate(site) == []
    assert build._terms_cut_by("a b-c d", "b", ["b-c"]) == ["b-c"] and build._terms_cut_by("a b-c d", "a b-c", ["b-c"]) == []


def test_every_internal_news_link_lands_on_a_built_page_at_the_id_it_names():
    pages = every_page()
    internal = [n for n in SITE["news"] if n.get("url") and not n["url"].startswith("http")]
    assert internal                                   # the paper's card is reached from the news at least once
    for item in internal:
        slug, _, fragment = item["url"].partition("#")
        assert slug in pages and fragment, item["date"]
        assert f'id="{fragment}"' in pages[slug], (item["date"], item["url"])


def test_talks_link_the_venue_and_show_the_note():
    html = html_of("research/")
    assert '<p class="venue"><a href="https://2025.ccneuro.org/contributed-talk/?id=65">' in html
    assert ('<p class="note">Preliminary version of the NeurIPS 2025 paper above, where linear probing is the '
            '“flexible evaluation” of the title.</p>') in html
    assert '<p class="note">The poster for the paper above.</p>' in html
    assert html.count('<p class="note">') == 2
    assert '<h2 id="talks-h" class="inset">Talks and presentations</h2>' in html   # the CVs' heading: one row is a poster


def test_project_titles_link_only_when_a_url_is_given():
    """The title links the programme's own page, as a talk's venue links the venue's; the team's repository
    is the row's "Code" link, the CVs' label. BCI4ALS stays unlinked (ruling)."""
    html = html_of("research/")
    assert ('<h3><a href="https://sites.google.com/brown.edu/ebt/2026-practicum">'
            'Embodied Brain Technology Practicum</a></h3>') in html
    assert "<h3>BCI4ALS</h3>" in html
    projects = html.partition('<section id="projects"')[2]
    assert projects.count('class="buttons"') == 1
    assert '<li><a class="btn" href="https://github.com/Itamar-Avitan/earbetter-ebt-practicum">Code</a></li>' in projects


def test_the_practicum_leads_with_the_programme_and_ends_on_his_one_clause():
    """The owner's ruling of 2026-09-21 supersedes every earlier framing of this entry, his own product-story
    correction of the same day included: the entry describes the programme rather than his part in it, and he
    chose "One entry, programme-first" (owner ruling, 2026-09-21; record kept privately) -- the PROGRAMME leads
    and EarBetter is named in a clause. On 2026-09-25 (rulings 4 and 7, OWNER-RULINGS) he allowed one clause on
    his own part back, in the ruled wording "I led the system integration", and ruling 8 gave BCI4ALS its
    clause (the three areas he ticked, not leadership); this test used to be named
    "...says_nothing_about_who_did_what" and forbade the first person in the blurb, which those rulings
    supersede. The sensor inventory and the business-plan detail stay deleted. See the note over `projects`
    in site.yaml, and shared/projects.tex in the CV repository, which carries the same shape."""
    entry = next(p for p in SITE["projects"] if "Practicum" in p["title"])
    bci = next(p for p in SITE["projects"] if p["title"] == "BCI4ALS")
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

    # DELETED AT HIS REQUEST, from the whole site: the retired clause's wording, the sensors, the business-plan detail
    everywhere = " ".join(_visible_text(h) for h in every_page().values())
    for gone in ("control software", "put the system together",
                 "Muse", "heart-rate variability", "skin conductance",
                 "go-to-market", "business plan"):
        assert gone not in everywhere, gone
    # one clause on his own part, in the ruled wording, at the end (rulings 4 and 7): not a list of what he did
    assert entry["blurb"].endswith(" I led the system integration.") and entry["blurb"].count("I ") == 1
    # and the clause stays out of News and About (the rulings allow it on the project row alone)
    assert "system integration" not in news["text"] and not any("system integration" in p for p in SITE["about"])
    assert "pitched" in entry["blurb"]              # the single word that survives of the pitch and the plan
    # BCI4ALS: the three areas he ticked (ruling 8), after the team sentence, and no leadership claimed
    assert bci["blurb"].endswith("the real-time system.") and "led" not in bci["blurb"]
    assert bci["blurb"].index("team of five") < bci["blurb"].index("I worked on")

    # the older paper-language framing stays gone too
    for phrase in ("explores whether", "physiological signals", "selective audio attenuation",
                   "remain experimental", "clinically evaluated"):
        assert phrase not in entry["blurb"] + " " + news["text"], phrase

    # the facts it may state, and the two it may not
    assert entry["period"] == "24 Jul–6 Aug 2026" and "Two weeks" in entry["blurb"]   # derived, not asserted
    assert "competitive" in entry["blurb"] and "five-person" in entry["blurb"]
    assert "12" not in _visible_text(html)          # a private number attached to this entry stays off the page
    assert "award" not in everywhere.lower()

    # the rulings are recorded where the next editor will see them, as the active decision and not as the
    # owner's own messages, and the retired wording is not kept in this public file (external review 2 §16:
    # the history lives in FACTS PROJ-EBT-DESC and the private ledgers)
    yaml_text = (build.ROOT / "site.yaml").read_text(encoding="utf-8")
    assert "PROGRAMME-FIRST" in yaml_text and "record kept privately" in yaml_text
    assert "programme-first" in yaml_text and "I led the system integration" in yaml_text and "control software" not in yaml_text


def test_the_public_source_records_decisions_and_not_the_owners_messages():
    """This repository is public. The rulings behind the site are recorded in its comments and docstrings as
    the active decision, dated, with "record kept privately" pointing at where the reasoning lives; the
    owner's own messages, once pasted in word for word, are not (external review 2 §16; WP-S16, 2026-09-26).
    What the scan looks for are the marks of a pasted message, none of them reproduces his message: two pieces
    of chat shorthand, the phrase that used to introduce a pasted message, a quoted string that opens with a
    lower-case "i" and a space -- the site's own strings open with a capital -- and a lower-case quotation
    that follows the owner as its subject (he, his, the owner) and an attribution word (asked, chose, said,
    wrote, answer, words) within a few words, which is the shape a pasted message takes in a note; the label
    of an option he picked opens with a capital and passes. A comment or a docstring wraps, and a quotation
    wraps with it, so each file is read with its lines joined, comment marks and indentation stripped, and a
    hit is reported on the line it starts on (review of WP-S16: the per-line scan let three wrapped
    quotations through). The scan covers every hand-written source file, this one included; only the two
    lines that name the marks are skipped."""
    root = build.ROOT
    files = [root / "site.yaml", root / "README.md", root / "style.css", root / "build.py",
             *sorted((root / "templates").rglob("*.j2")), *sorted((root / "tools").glob("*.py")),
             *sorted((root / "tests").glob("*.py"))]
    marks = ("btw", "blah blah", "words verbatim")
    opener = re.compile(r"""["'“‘]i\s""")
    attributed = re.compile(r"""\b(?:[Hh]e|[Hh]is|owner(?:'s)?)\b[^"“]{0,40}?\b(?:ask(?:ed)?|chose(?:n)?|said|wrote|answer|words)\b[^"“]{0,24}?["“][a-z]""")
    assert len(files) >= 14
    for path in files:
        lines = [(number, line.strip().lstrip("#").strip())
                 for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
                 if "marks = (" not in line and "attributed = re.compile(" not in line]
        text = " ".join(line for _, line in lines)
        starts = [0]
        for _, line in lines:
            starts.append(starts[-1] + len(line) + 1)
        def at(offset):                                   # the (file, line number, line) a hit starts on
            number, line = lines[bisect.bisect_right(starts, offset) - 1]
            return path.name, number, line
        for mark in marks:
            assert text.lower().find(mark) < 0, at(text.lower().find(mark))
        for pattern in (opener, attributed):
            hit = pattern.search(text)
            assert hit is None, at(hit.start())
    # the phrase itself, where the six rulings this package restated live (a comment may wrap inside it)
    joined = re.sub(r"\n#\s*", " ", (root / "site.yaml").read_text(encoding="utf-8"))
    assert joined.count("record kept privately") >= 6



def test_optional_keys_may_be_absent():
    site = copy.deepcopy(SITE)
    for entry in site["talks"] + site["projects"] + site["research"] + site["links"] + site["news"]:
        for key in ("url", "link", "note", "text", "figure", "me", "ongoing", "authors", "badges", "buttons",
                    "links"):
            if key == "text" and "date" in entry:
                continue                                # a news item's sentence is required; its link is not
            entry.pop(key, None)
    for entry in site["research"]:
        for key in ("venue", "how", "found", "open", "cite", "published", "doi"):    # the card's citation line,
            entry.pop(key, None)                                # depth blocks and record are optional; a talk's venue is not
    for entry in site["links"]:
        entry.pop("format", None)
    for entry in site["pages"]:
        for key in ("intro", "description", "title", "og_image", "og_image_alt"):
            entry.pop(key, None)
    for key in ("role", "nobreak", "works", "news_visible", "show_ongoing", "quotes_intro", "knows_about",
                "affiliation_url", "not_found"):
        site.pop(key, None)
    pages = every_page(site)
    person = _graph("research/", site)["Person"]
    assert not {"jobTitle", "memberOf", "knowsAbout"} & set(person) and "url" not in person["affiliation"]
    assert "ScholarlyArticle" not in _graph("research/", site)
    for html in pages.values():
        assert 'class="vh"' not in html.partition('<nav class="sitenav"')[2].partition("</nav>")[0]
    for slug, html in pages.items():
        for gone in ('<p class="venue"><a', 'class="venue paper__venue"', 'class="note"', "<figure",
                     'class="authors"', 'class="role"', 'class="nb"', 'rel="me"', 'id="email"',
                     'class="tag tag--venue"', 'class="btn', 'class="page-intro', 'class="recovery-diagram"',
                     'class="open"', 'class="cite"', 'class="work"', 'class="go-venue"', 'class="go-links"'):
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
    assert section.count("<li>") == len(SITE["now"]) == 2
    assert '<ul class="what now" role="list">' in section
    text = _visible_text(section)
    assert all(" ".join(line.split()) in text for line in SITE["now"])


def test_the_about_and_now_drafts_are_marked_as_drafts_in_site_yaml():
    """They are the two pieces of writing the owner asked for and has not yet approved. The file says so, so
    that whoever edits next knows they are standing in for his own words, not recording them."""
    yaml_text = (build.ROOT / "site.yaml").read_text(encoding="utf-8")
    assert yaml_text.count("DRAFT, awaiting the owner") == 1 and "DRAFT, as above" in yaml_text
    assert 150 <= sum(len(p.split()) for p in SITE["about"]) <= 200
    assert 2 <= len(SITE["now"]) <= 5


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
    # The literal is deliberate and has to be raised by hand when a row is added: the assertion under it
    # derives the same number from site.yaml, so on its own it would wave through a row silently deleted.
    # 6 = four courses (the fourth, Computational Approaches to Neuroimaging, added so this page matches the
    # CV the nav strip opens) + the two student rows.
    assert rows == html.count('<p class="when">') == html.count('<p class="kind">') == 6
    assert rows == len(SITE["teaching"]["courses"]) + len(SITE["teaching"]["students"])
    for entry in SITE["teaching"]["courses"] + SITE["teaching"]["students"]:
        assert f'<span class="tag">{entry["kind"]}</span>' in html, entry["title"]
    assert '<p class="when">2023/24, 2024/25 and 2025/26</p>' in html          # not a date the page can read
    assert '<p class="when"><time datetime="2021">2021</time>–<time datetime="2023">2023</time></p>' in html
    assert '<h3>Two undergraduate research students</h3>' in html              # its own row, not a line in a list


def test_the_teaching_page_carries_the_course_home_announces():
    """This page is the authority on his teaching, and it did not list the academic writing course that "now"
    on the home page announces for 2026/27. The course is described, not titled: its title is not printed
    yet (deep review 2026-09-25, AK-03 and CS-17; WP-S12 prints the one ruling 13 gave), and the list runs
    newest first like every list on the site (FL-08): it was the one list that ran the other way, so the
    gutter changed direction between Courses and Students. The order is the CV's."""
    courses = SITE["teaching"]["courses"]
    writing = next(c for c in courses if "writing" in c["title"].lower())
    assert writing["when"] == "from 2026/27" and "2026/27" in SITE["now"][1]
    assert "academic writing course" in SITE["now"][-1]
    assert writing["title"] in _visible_text(html_of("teaching/"))
    assert "what" not in writing and writing["kind"] == "Teaching team"      # a description, and no invented sentence
    assert [c["title"] for c in courses] == ["An academic writing course", "Introduction to Cognition and Computation",
                                             "Deep Learning for Neuroscience and Cognition",
                                             "Computational Approaches to Neuroimaging"]
    # the gutter reads one direction down the whole page: the years of the six rows, in page order
    html = html_of("teaching/")
    whens = re.findall(r'<p class="when">(.*?)</p>', html)
    assert [_visible_text(w) for w in whens] == ["from 2026/27", "2023/24, 2024/25 and 2025/26", "spring 2026", "2024",
                                                 "2025–2026", "2021–2023"]


def test_a_teaching_row_without_a_sentence_still_renders():
    """The rows under "Students" carry the register's own sentence under the title (the mentoring topics and
    the tutoring subjects, which cv.pdf already prints); the one row with nothing under its title is now the
    writing course under "Courses", so the no-sentence path is still exercised by a real row (deep review
    2026-09-25, AC-12 and CS-17). The synthetic site below proves it on its own as well."""
    html = html_of("teaching/")
    students = html.partition('<section id="students"')[2].partition("</section>")[0]
    assert students.count("<h3>") == 2 and students.count("<p>") == 2
    assert [k["kind"] for k in SITE["teaching"]["students"]] == ["Mentor", "Tutor"]     # role nouns, not gerunds
    assert "<h3>Academic tutoring, Dean of Students Office</h3>" in students            # a post, not a course
    assert "<p>Calculus, linear algebra and statistics.</p>" in students
    assert "statistical modeling of neural spiking data" in students                   # US spelling, FACTS TEACH-MENT-DESC
    courses = html.partition('<section id="courses"')[2].partition("</section>")[0]
    writing = re.search(r'<li class="row">(?:(?!</li>).)*An academic writing course(?:(?!</li>).)*</li>', courses, re.S).group(0)
    assert "<h3>An academic writing course</h3>" in writing and "<p>" not in writing
    assert 'log--tight' not in html                          # both lists take the ordinary step: every row may carry a sentence
    site = copy.deepcopy(SITE)
    site["teaching"] = {"courses": [{"title": "A course", "kind": "Teaching assistant", "when": "2026"}]}
    only = html_of("teaching/", site)
    assert "<h3>A course</h3>" in only and '<section id="students"' not in only


def test_quotes_print_the_line_its_attribution_and_its_context_and_nothing_else():
    html = html_of("commonplace/")
    section = html.partition('<section id="quotes"')[2].partition("</section>")[0]
    shown = _visible_text(section)
    assert section.count("<blockquote>") == len(SITE["quotes"]) == 4
    assert "four" in SITE["elsewhere"][2]["text"] and len(SITE["quotes"]) == 4      # the home page's teaser counts them
    for quote in SITE["quotes"]:
        assert f'<blockquote><p>{quote["text"]}</p></blockquote>' in section         # printed exactly as verified
        # rendered text, with the attribution's U+00A0 ties read as the spaces they print as
        assert " ".join(quote["attribution"].split()) in shown and quote.get("note", "") in shown
        for private in ("evidence", "status", "copyright"):                          # provenance, not page text
            assert quote[private] not in shown
    assert section.count('<time class="when" datetime="1887">1887</time>') == 3      # the year takes its tick
    assert '<time class="when" datetime="2003">2003</time>' in section
    # the borrowed line keeps its own date in the attribution, where it cannot break at the hyphen -- after the
    # poem's title, as the novel's year follows the novel's (AK-16); the permalink follows inside the same <p>.
    # The title, its span and the epistle's numeral are tied with U+00A0 (see the numeral test below); the
    # title is set in italic (`works`, DS-07), so the tie inside it is inside the <i> and the one after it is not.
    assert '<i class="work">An\u00a0Essay\u00a0on\u00a0Man</i>\u00a0(<span class="nb">1733–34</span>), Epistle\u00a0II' in section
    assert 'Epistle\u00a0II (<span class="nb">1733–34</span>)' not in section


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
    assert quote["attribution"] == ("Sherlock Holmes, in Arthur Conan Doyle, A Study in Scarlet\u00a0(1887), "
                                    "Part\u00a0I, Chapter\u00a0II, “The Science of Deduction”")   # year and numerals tied
    assert quote["year"] == "1887" and quote["status"] == "verified" and quote["copyright"] == "public-domain"


def test_the_commonplace_runs_in_the_owners_order_with_snape_first():
    """It used to run oldest first, so that the years in the gutter only went forward. The owner ruled on
    2026-09-21 that the Snape line comes first (record kept privately), so that rule is gone: a commonplace book
    is a person's own order, not a chronology, and the gutter may now carry 2003 above 1887. Nothing else moved
    -- the three A Study in Scarlet lines keep the order they were in."""
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
                                    "Phoenix\u00a0(2003), Chapter\u00a024, “Occlumency”")           # year and numeral tied
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


def test_every_quotation_has_a_permalink_of_its_own():
    """One line can be shared (deep review 2026-09-25, AS-52): each quotation's slug is the id of its row and
    the address of the small "§" at the end of its attribution. The slug is ASCII, unique, and never changed
    once published; the link is always painted, since a keyboard or a finger cannot hover; each link's
    accessible name names its own line, so a list of links does not hear four identical entries; and the
    entry a link lands on stands on a panel of the accent tint (measured in tests/test_browser.py, which is
    where the plan's ring was found to run through the hanging mark and the date)."""
    slugs = [q["slug"] for q in SITE["quotes"]]
    assert slugs == ["snape-mind-not-a-book", "holmes-brain-attic", "watson-proper-study", "holmes-thousand-and-first"]
    assert len(set(slugs)) == len(slugs) and all(build.SLUG.fullmatch(s) for s in slugs)
    html = html_of("commonplace/")
    section = html.partition('<section id="quotes"')[2].partition("</section>")[0]
    rows = re.findall(r'<li class="row" id="([^"]+)">', section)
    assert rows == slugs
    names = []
    for q in SITE["quotes"]:
        assert section.count(f'id="{q["slug"]}"') == 1
        speaker = q["attribution"].split(", in ")[0]
        name = f'Link to this quotation ({speaker}: “{build.opening(q["text"])}”)'
        assert f'<a class="quote__link" href="#{q["slug"]}" aria-label="{name}">§</a></p>' in section
        names.append(name)
    assert len(set(names)) == len(names) and section.count('class="quote__link"') == len(slugs)
    # inside the attribution's own paragraph, at its end, after the source
    for q in SITE["quotes"]:
        by = re.search(r'<p class="quote__by">(.*?)</p>', section[section.index(f'id="{q["slug"]}"'):], re.S).group(1)
        assert by.endswith("§</a>") and _visible_text(by).startswith(q["attribution"][:20])
    css = _css()
    link = re.search(r"\.quote__link \{[^}]*\}", css).group(0)
    assert "padding: 0.5rem 0.5625rem" in link and "var(--mono)" in link and "text-decoration: none" in link
    for hidden in ("display: none", "opacity: 0", "visibility: hidden"):
        assert hidden not in link                                              # never hover-only
    assert ".quote__link:hover, .quote__link:focus-visible {" in css
    mark = re.search(r"\.row:target \{[^}]*\}", css).group(0)
    assert "background: var(--target-tint)" in mark and "box-shadow: 0 0 0 0.625rem var(--target-tint)" in mark
    assert ".row:target > .what" not in css                                # the ring that collided is gone
    assert css.count("--target-tint: #") == 3                             # a token in the light and both dark blocks


def test_a_permalinks_name_opens_with_the_lines_first_clause():
    """The accessible name ends on the line's first clause: a whole sentence when the first one is short,
    a cut clause with "…" for its comma, or the first fifteen words when neither comes sooner. The page's
    own quotation marks around the line are dropped, so the name can put its own around the clause."""
    assert build.opening(SITE["quotes"][0]["text"]) == "Only Muggles talk of ‘mind reading.’"
    assert build.opening(SITE["quotes"][1]["text"]) == "I consider that a man’s brain originally is like a little empty attic…"
    assert build.opening(SITE["quotes"][2]["text"]) == "The proper study of mankind is man."
    assert build.opening(SITE["quotes"][3]["text"]) == "There is a strong family resemblance about misdeeds…"
    assert build.opening("“Know then thyself”") == "Know then thyself"          # no punctuation: the whole line
    assert build.opening("“one two three four”", cap=3) == "one two three…"      # the cap, when nothing closes sooner
    assert build.opening("“one two three”", cap=3) == "one two three"           # the cap is not a cut when the line ends there
    assert build.opening("“Wait; then go.”") == "Wait…"                          # a semicolon closes a clause too


def test_the_build_refuses_a_quotation_without_a_good_slug():
    """A missing slug is an entry that cannot be shared; a repeated one sends two links to one place; a slug
    with a space or a capital is not an address anyone can type."""
    site = copy.deepcopy(SITE)
    del site["quotes"][1]["slug"]
    assert any("has no well-formed slug" in p and "I consider that a man" in p for p in build.validate(site))
    site = copy.deepcopy(SITE)
    site["quotes"][2]["slug"] = site["quotes"][1]["slug"]
    assert any("used twice" in p and "holmes-brain-attic" in p for p in build.validate(site))
    for bad in ("Holmes Brain Attic", "holmes_brain", "-leading", "trailing-", "double--hyphen", "ünïcode", 7):
        site = copy.deepcopy(SITE)
        site["quotes"][0]["slug"] = bad
        assert any("has no well-formed slug" in p for p in build.validate(site)), bad
    assert build.validate(SITE) == []


def test_the_statement_reports_before_it_lists():
    """Intro, aim, report, done, limits: the GOV.UK model's order (deep review 2026-09-25, FL-09), so the way
    to report a problem is on the first screen and not 2.8 screens down. The intro names the four in the same
    order, and the report sentence points at the address at the foot of this page first, because the colophon
    is no longer in view when it is read."""
    html = html_of("accessibility/")
    assert (html.index('<section id="aim"') < html.index('<section id="report"')
            < html.index('<section id="done"') < html.index('<section id="limits"'))
    intro = page("accessibility/")["intro"]
    assert intro.index("aims at") < intro.index("tell me") < intro.index("actually done") < intro.index("not claimed")
    assert "My address is at the foot of this page, and at the top of the home page." in SITE["accessibility"]["report"]


def test_every_gutter_label_in_the_quotes_section_is_a_plain_date():
    """The gutter is the page's time axis. A date that needs a parenthetical belongs in the attribution."""
    section = html_of("commonplace/").partition('<section id="quotes"')[2].partition("</section>")[0]
    labels = re.findall(r'<(?:time|span) class="when"[^>]*>([^<]*)</(?:time|span)>', section)
    assert len(labels) == len(SITE["quotes"])
    assert all(re.fullmatch(r"\d{4}", label) for label in labels), labels


def test_the_talk_video_opens_where_his_own_talk_starts():
    """The recording is one five-talk session with no chapter markers, so the address carries a start time:
    t=715s is 11:55 (the owner's ruling of 2026-09-21, and the timestamp already committed on his CV)."""
    ccn = next(t for t in SITE["talks"] if "(CCN) 2025" in t["venue"])
    link = ccn["links"][0]                                     # the session recording sits on the CCN row, as on the CV
    assert link["url"] == "https://www.youtube.com/watch?v=vT-3kV89Rhk&t=715s"
    assert link["label"] == "Recording (starts at 11:55)"
    html = html_of("research/")
    assert 'href="https://www.youtube.com/watch?v=vT-3kV89Rhk&amp;t=715s"' in html
    assert "&t=715s" not in html                                            # never the bare ampersand
    assert unescape(re.search(r'href="([^"]*vT-3kV89Rhk[^"]*)"', html).group(1)) == link["url"]
    # and the card carries the paper's own NeurIPS video, the one cv.pdf prints (FACTS PUB-LINK-TALK)
    video = SITE["research"][0]["buttons"][-1]
    assert (video["label"], video["url"]) == ("NeurIPS 2025 video", "https://slideslive.com/39047290")
    assert "talk" not in video["context"]                                   # a poster video, not a talk
    assert "CCN 2025 talk video" not in html


def _css() -> str:
    return (build.ROOT / "style.css").read_text(encoding="utf-8")


def test_the_theme_toggle_is_not_painted_when_the_script_has_not_run():
    """The markup writes both buttons hidden -- the strip's, and the colophon's that a phone shows in its
    place (deep review 2026-09-25, PA-04) -- and the script unhides them, so a visitor whose browser ran no
    JavaScript is not shown a control that does nothing. .toggle sets display from a class, which outranks the
    browser's own [hidden] rule, so the attribute has to be honoured in the stylesheet in so many words."""
    for slug, html in every_page().items():
        assert '<button id="theme-toggle" class="toggle" type="button" data-theme-toggle hidden>' in html, slug
        assert '<button class="toggle toggle--foot" type="button" data-theme-toggle hidden>' in html, slug
        assert html.count("data-theme-toggle hidden>") == 2 and html.count('id="theme-toggle"') == 1, slug
        # the colophon's is an item of the row about the site, after its links, so it goes with the row
        row = html.partition('<nav class="colophon-nav"')[2].partition("</nav>")[0]
        assert row.index("Accessibility</a>") < row.index('<li class="colophon-nav__theme"><button'), slug
    css = _css()
    assert re.search(r"\.toggle\[hidden\] \{[^}]*display: none", css)
    assert "btns[i].hidden = false" in (build.ROOT / "templates" / "base.html.j2").read_text(encoding="utf-8")
    # one button at a time: the colophon's does not exist for layout from 45rem up, the strip's below it
    base, _, rest = css.partition("Phone: the rule moves")
    phone = rest.partition("Desktop: the gutter")[0]
    assert ".colophon-nav__theme { display: none; }" in base
    assert ".topstrip > .toggle { display: none; }" in phone and ".colophon-nav__theme { display: list-item; }" in phone
    # the five items spread across the line only where it is tight (a phone, below 30rem); from 480px the
    # laptop's 18px gap fits on one line, and a split-screen laptop window keeps the strip's own rhythm
    spread = ".sitenav ul { justify-content: space-between; column-gap: 0.5rem; }"
    assert spread in phone.partition("@media (max-width: 29.99rem) {")[2].partition("\n}")[0]
    assert spread not in phone.partition("@media (max-width: 29.99rem) {")[0]
    assert css.count("justify-content: space-between") == 1


def test_a_link_says_where_it_leads_to_a_reader_who_cannot_see_the_card():
    """A screen reader's list of links read "Paper, arXiv, Code, Data" on the research page and three bare
    titles on the home page's hand-off (deep review 2026-09-25, PA-05). Each paper button carries a `context`
    in site.yaml -- per button, never one suffix for all, which would have called a CCN recording part of
    the NeurIPS paper -- printed after the label in a hidden span; the hand-off link names its page the same
    way; the home Research row's Paper and Code carry the card's own contexts. The two generated glyphs the
    stylesheet draws, the role line's dots and the attribution's dash, carry empty alternative text so they
    are not read either."""
    card, research = SITE["research"][0], html_of("research/")
    contexts = {b["label"]: b.get("context") for b in card["buttons"]}
    assert contexts == {"Paper": "(NeurIPS 2025)", "arXiv": "preprint of the NeurIPS 2025 paper",
                        "Code": "for the NeurIPS 2025 paper", "Data": "for the NeurIPS 2025 paper",
                        "NeurIPS 2025 video": "of the poster presentation of the paper"}
    for label, context in contexts.items():
        assert f'>{label}<span class="vh"> {context}</span></a></li>' in research, label
    assert "CCN" not in " ".join(contexts.values())                       # a preliminary version, never the paper
    buttons = research.partition('<ul class="buttons" role="list">')[2].partition("</ul>")[0]
    assert buttons.count('<span class="vh">') == len(card["buttons"])
    site = copy.deepcopy(SITE)
    for button in site["research"][0]["buttons"]:
        button.pop("context", None)
    bare = html_of("research/", site).partition('<ul class="buttons" role="list">')[2].partition("</ul>")[0]
    assert 'class="vh"' not in bare                                        # the key is optional
    home = html_of("")
    section = home.partition('<section id="elsewhere"')[2].partition("</section>")[0]
    for row in SITE["elsewhere"]:
        # the page's name closes the link, inside it, before the dash that opens the clause
        assert section.count(f'<span class="vh"> ({row["page"]} page)</span></a>&nbsp;—') == 1, row["page"]
        assert re.search(rf'<a href="{row["url"]}">(?:(?!</a>).)*{re.escape(row["label"][-12:])}<span class="vh"> \({row["page"]} page\)</span></a>', section), row["page"]
    assert section.count('class="vh"') == len(SITE["elsewhere"]) + 2          # the three pages, then Paper and Code
    css = _css()
    assert re.search(r'\.role li::before \{[^}]*content: "\\00B7" / "";', css)
    assert '.quote__by::before { content: "\\2014\\00A0" / ""; }' in css


def test_the_first_thing_on_every_page_is_a_way_past_the_strip():
    """A keyboard reader pressed Tab eight times on the home page before reaching a word of it (deep review
    2026-09-25, PA-06). The skip link is the first element in <body>, leads to <main id="content">, which
    can take focus (tabindex -1: a link can land on it, Tab does not stop on it) and draws no ring of its
    own; the link is kept above the viewport until it holds focus. tests/test_browser.py presses the keys."""
    for slug, html in every_page().items():
        body = html.partition("<body>")[2]
        assert body.lstrip().startswith('<a class="skip" href="#content">Skip to content</a>'), slug
        assert html.count('<a class="skip"') == 1 and html.count('id="content"') == 1, slug
        assert re.search(r'<main id="content" tabindex="-1"( class="opens-with-h1")?>', html), slug
    not_found = (build.ROOT / "404.html").read_text(encoding="utf-8")
    assert '<a class="skip" href="#content">Skip to content</a>' in not_found       # the 404 page inherits it
    css = _css()
    skip = re.search(r"\.skip \{[^}]*\}", css).group(0)
    assert "position: absolute" in skip and "top: -3rem" in skip and "z-index: 10" in skip
    assert ".skip:focus { top: 0.75rem; }" in css and "main:focus { outline: none; }" in css
    assert "a “Skip to content” link is the first stop on every page" in SITE["accessibility"]["done"][2]


def test_paper_gets_the_light_palette_and_the_addresses_whatever_the_screen_showed():
    """The print block (deep review 2026-09-25, PA-01): the light palette set on the three root selectors,
    the system-dark block's own among them, because a bare `:root` loses to it on specificity and a
    dark-theme page then printed its name in a 2.91:1 grey; one scale step down on the root, not body, so
    the rem-set lines shrink with the text; the screen furniture off the paper, the skip link, the phone's
    theme button and the mark's caption included; the filled button a plain link; addresses after the links
    that lead off the site; the older news opened by the script on beforeprint and closed after. The
    portrait's dark-theme dimming goes with the palette. tests/test_browser.py measures the inks and the
    page count."""
    css = _css()
    block = css.partition("@media print {")[2]
    assert block.count("@media") == 0                                       # the last block in the file
    assert ':root, :root:not([data-theme="light"]), :root[data-theme="dark"] {' in block
    root = block.partition(':root[data-theme="dark"] {')[2].partition("}")[0]
    for token in ("color-scheme: light;", "--bg: #fff;", "--ink: #000;", "--accent: #0B5F73;",
                  "--accent-tint: transparent;", "--mat: transparent;", "--target-tint: transparent;",
                  "--portrait-filter: none;"):
        assert token in root, token
    assert "html { font-size: 87.5%; }" in block and "body { background: #fff; }" in block
    hidden = re.search(r"\n  (\.skip,[^{]*)\{ display: none !important; \}", block).group(1)
    for furniture in (".skip", ".topstrip", ".colophon-nav", ".mark", ".mark-row", "main::before",
                      ".colophon .links-nav::before", ".colophon .row::before", "h1.inset::before",
                      "h2.inset::before", ".row > .when::after"):
        assert furniture in [s.strip() for s in hidden.split(",")], furniture
    assert ".btn--primary { color: var(--accent); background: none; border: 0; padding-inline: 0; text-decoration: underline; }" in block
    addresses = re.search(r"(\.buttons a\[href\^=\"http\"\]::after[^{]*)\{([^}]*)\}", block)
    assert addresses and 'content: " <" attr(href) ">";' in addresses.group(2)
    for where in ('.venue a[href^="http"]::after', 'h3 a[href^="http"]::after', '.go-links a[href^="http"]::after'):
        assert where in addresses.group(1), where
    # the card is taller than a sheet, so its pieces keep whole rather than the card (which only emptied sheet 1)
    assert ".paper__aside, .paper .tldr, .recovery-diagram li, .paper .open, .log > .row, .quote { break-inside: avoid; }" in block
    assert ".paper .recovery-diagram, .paper .open { break-before: avoid; }" in block and "h2, h3 { break-after: avoid; }" in block
    assert not re.search(r"\n  \.paper,", block)
    assert "--plot-filter" not in block and "--art-filter" not in block   # a figure is printed as it is, on paper too
    script = (build.ROOT / "templates" / "base.html.j2").read_text(encoding="utf-8")
    assert 'window.addEventListener("beforeprint"' in script and 'window.addEventListener("afterprint"' in script
    assert 'querySelectorAll("details.older:not([open])")' in script and 'd.setAttribute("data-print-opened", "")' in script
    assert 'querySelectorAll("details[data-print-opened]")' in script


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
    assert all(f'<span class="nb">{term}</span>' in pages["research/"] for term in ("Best-Fitting", "five-person", "real-time"))
    # a nobreak term that matches nothing is dead configuration: "go-to-market" went with the phrase itself
    assert "go-to-market" not in SITE["nobreak"]
    for term in SITE["nobreak"]:
        assert any(term in _visible_text(h) for h in pages.values()), term
    site = copy.deepcopy(SITE)
    site["about"][0] = "Ben-Gurion <b>& co</b>"
    assert '<p><span class="nb">Ben-Gurion</span> &lt;b&gt;&amp; co&lt;/b&gt;</p>' in html_of("", site)
    del site["nobreak"]
    assert "<p>Ben-Gurion &lt;b&gt;&amp; co&lt;/b&gt;</p>" in html_of("", site)


def test_every_work_title_is_set_in_italic_and_matches_the_content():
    """The titles of works (`works` in site.yaml) are set in <i class="work"> wherever the text names one, in
    a self-hosted italic face (deep review 2026-09-25, DS-07): a book or a long poem is italic in prose, and
    the chapter titles beside them keep their quotation marks. Like a `nobreak` term, a title that matches
    nothing is dead configuration. <i>, not <cite>: the attributions open with a speaker, and the test above
    forbids <cite> on that page. The face is a local file, declared once, so the "nothing loads from anyone
    else" bullet of the accessibility statement stays true."""
    pages = every_page()
    assert SITE["works"] and len(set(SITE["works"])) == len(SITE["works"])
    for work in SITE["works"]:
        assert any(f'<i class="work">{work}</i>' in h for h in pages.values()), work
        assert not any(work in re.sub(r'<i class="work">.*?</i>', "", h) for h in pages.values()), work   # none left roman
    assert "<cite" not in pages["commonplace/"]
    css = _css()
    face = re.search(r'@font-face \{ font-family: "Fira Sans"; font-style: italic; font-weight: 400;[^}]*\}', css)
    assert face and 'src: url("fonts/fira-sans-latin-400-italic.woff2") format("woff2")' in face.group(0)
    assert (build.ROOT / "fonts" / "fira-sans-latin-400-italic.woff2").read_bytes()[:4] == b"wOF2"
    assert "font-synthesis: none" in css                       # so a face that fails to load falls back to roman, not faux italic
    site = copy.deepcopy(SITE)
    del site["works"]
    assert 'class="work"' not in html_of("commonplace/", site)


def test_the_colophon_mark_carries_its_caption_on_every_page():
    """The mark had stood at the foot of every page unexplained (deep review 2026-09-25, DI-01); the caption
    beside it is one line from site.yaml, and the mark keeps the owner's row of three shapes at one and a
    half times its old size (DS-10) -- the same drawing the favicon is made from."""
    note = SITE["footer"]["mark_note"]
    assert "our NeurIPS\u00a02025 paper" in note and "odd one out" in note  # every word is already on the site;
    assert "NeurIPS 2025" not in note      # the venue and its year are tied with a no-break space: the caption
    # is printed raw, so the nobreak list cannot reach it, and untied it broke "NeurIPS / 2025" at 760-1023px
    # and on phones (WP-S8 review). "odd one out" is not tied: at 320px that would make the caption four lines.
    for slug, html in every_page().items():
        foot = html.partition('<footer class="colophon">')[2]
        assert foot.count('<p class="mark-row inset">') == 1, slug
        assert f'<span class="mark-note">{note}</span></p>' in foot, slug
        mark = foot.partition('<svg class="mark"')[2].partition("</svg>")[0]
        assert 'viewBox="0 0 42 10" width="63" height="15"' in mark and 'aria-hidden="true"' in mark, slug
        assert mark.count("<circle") == 2 and mark.count("<rect") == 1, slug
    assert (build.ROOT / "404.html").read_text(encoding="utf-8").count(f'<span class="mark-note">{note}</span>') == 1


def test_role_line_keeps_the_lab_link_on_every_page():
    for slug, html in every_page().items():
        assert "<li>PhD candidate</li>" in html, slug
        assert '<li><a href="https://brainsandmachines.org">Brains and Machines Lab</a></li>' in html, slug


def test_portrait_and_calm_rows_of_links():
    html = html_of("")
    assert ('<picture><source srcset="img/portrait-400.webp" type="image/webp">'
            '<img src="img/portrait-800.jpg" width="120" height="120" alt=""></picture>') in html
    # the address, then the profiles this page may mark, then the profiles it may not
    assert html.count('<ul class="links ') == 3 and "monogram" not in html
    email_row = html.partition('<li class="links__email">')[2].partition("</li>")[0]
    assert email_row.count("<svg") == 1 and ">Email</span>" in email_row       # the envelope leads the block
    # each profile once per page -- except the two the home page also sets beside the address (PICK), twice
    for slug, page_html in ((s, unescape(h)) for s, h in every_page().items()):
        for link in SITE["links"]:
            if link["label"] not in ("Email", "CV"):
                expected = 2 if (slug == "" and link["label"] in PICK) else 1
                assert page_html.count(f'<a rel="me" href="{link["url"]}">') == expected, (slug, link["label"])
                assert page_html.count(f'<span>{link["label"]}</span></a>') == expected, (slug, link["label"])
    assert 'class="links links--reach"' in html and "btn--primary" in html


def test_each_marked_link_carries_a_silent_mark_beside_its_visible_label():
    page_html = unescape(html_of(""))
    marked = [link for link in SITE["links"] if link.get("icon")]
    assert len(marked) == 3
    # every mark, and nothing else: three per page, four at home, where GitHub's link is set beside the
    # address as well as in the colophon and draws its mark both times
    for slug, html in every_page().items():
        assert unescape(html).count('<svg class="ico ') == (4 if slug == "" else 3), slug
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
    # The icon files at the root, written by tools/make_favicon.py from that mark (deep review 2026-09-25,
    # MR-07): the row, centred on a square, in an SVG that keeps the theme style, a 96px PNG (Google takes only
    # a square raster it can crawl, larger than 48px), an ICO for clients that ask for one blindly, and a
    # home-screen tile. The arrangement of the mark is the owner's (Q-L A) and does not change here.
    square = (build.ROOT / "favicon.svg").read_text(encoding="utf-8").strip()
    assert square == mark.replace('viewBox="0 0 42 10"', 'viewBox="0 -16 42 42"')
    with Image.open(build.ROOT / "favicon-96.png") as im:
        assert im.size == (96, 96) and im.mode == "RGBA"
        assert im.getpixel((11, 48))[:3] == (0x14, 0x21, 0x2B) and im.getpixel((84, 48))[:3] == (0x0B, 0x5F, 0x73)
        assert im.getpixel((1, 1))[3] == 0 and im.getpixel((30, 48))[3] == 0     # transparent ground, a row
    with Image.open(build.ROOT / "favicon.ico") as im:
        assert im.format == "ICO" and im.info["sizes"] == {(16, 16), (32, 32), (48, 48)}
    with Image.open(build.ROOT / "apple-touch-icon.png") as im:
        assert im.size == (180, 180) and im.getpixel((2, 2)) == (0xF4, 0xF7, 0xF8)
    for slug, html in every_page().items():
        base = build.base_of(page(slug))
        assert f'<link rel="icon" href="{base}favicon-96.png" sizes="96x96" type="image/png">' in html, slug
        assert f'<link rel="icon" href="{base}favicon.svg" type="image/svg+xml">' in html, slug
        assert f'<link rel="apple-touch-icon" href="{base}apple-touch-icon.png">' in html, slug
        assert "data:image/svg+xml" not in html, slug                      # the inline icon, which no crawler took


def test_images_carry_no_metadata_and_the_social_cards_are_1200x630():
    for name in ("portrait-400.webp", "portrait-800.jpg", "og.png", "og-research.png", "paper-lab.webp", "paper-lab.jpg"):
        with Image.open(build.ROOT / "img" / name) as im:
            assert len(im.getexif()) == 0 and not getattr(im, "text", None), name
    for name in ("paper-lab.webp", "paper-lab.jpg"):                 # square, and twice the 272px it is laid out at
        with Image.open(build.ROOT / "img" / name) as im:
            assert im.size == (544, 544), name
    for name in ("og.png", "og-research.png"):
        with Image.open(build.ROOT / "img" / name) as im:
            assert im.size == (1200, 630), name


# --- The accessibility statement: every line of it is a claim about this site ------------------------------

def test_the_statement_claims_only_what_something_actually_checks():
    """The page says the site aims at WCAG 2.1 AA and lists what was done. Each bullet has to be backed by a
    check that runs, or it is marketing. This test pins the three that had no guard of their own: reduced
    motion, the document language, and the fact that nothing on any page loads from a third party -- and the
    three sentences the deep review of 2026-09-25 found saying more than was true (CS-10, MR-12, PA-05)."""
    done = SITE["accessibility"]["done"]
    assert "tells no one but the host, GitHub Pages, that you were here" in done[0]     # the host sees every visit
    assert "tells nobody else" not in done[0]
    assert "checked by an automated test that I run before I publish a change" in SITE["accessibility"]["done_intro"]
    assert "automatically before the site is built" not in SITE["accessibility"]["done_intro"]   # by hand, after the build
    assert done[6].startswith("Every picture that carries information has a text alternative")
    assert "The portrait beside my name, and the small marks beside Email, GitHub and Bluesky, are hidden from screen readers" in done[6]
    assert "Every image carries a description" not in " ".join(done)                   # false once the portrait is decorative
    assert done[7].endswith("made into a link in your browser.") and "harvest" not in done[7]   # cv.pdf prints both addresses
    assert "the middle of the three levels" in SITE["accessibility"]["aim"]
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


def test_the_published_cv_is_the_public_build_and_obeys_the_programme_ruling():
    """cv.pdf is linked from the strip of every page, so it is part of what this site says. The owner's
    programme-first ruling deleted the retired role clause, the sensor inventory and the business-plan
    detail from the CVs; if the published PDF still held them, the site would contradict its own project
    entry from its own navigation bar. It is the public build (no phone number) of the cv repository.
    Since 2026-09-25 (rulings 4, 7 and 8) one role clause per project is printed again -- "Led system
    integration." and "Worked on data collection ..." -- so "system integration" left the list below; the
    assertion that the PDF carries those clauses is added by the commit that republishes it (WP-C8's
    publish), because until then the served file is the older build.

    This checks what the file says, not how fresh it is: a cv.pdf ten commits stale would pass every line
    below, because none of these phrases would have moved. Freshness is guarded where the drift is caused
    and where both sides can be built -- `make check-published` in the cv repository, which is part of its
    `make test`. Do not turn this into an agreement check against site.yaml: it would need an allow-list
    covering nearly half the claims and would still miss the drift that guard catches."""
    pdf = build.ROOT / "cv.pdf"
    assert pdf.exists()
    out = subprocess.run(["pdftotext", str(pdf), "-"], capture_output=True, text=True)
    assert out.returncode == 0, "pdftotext is needed to check the published CV; install poppler"
    text = " ".join(out.stdout.split())
    for gone in ("control software", "Muse headband", "heart-rate variability",
                 "skin conductance", "go-to-market", "business plan", "selective audio attenuation"):
        assert gone not in text, gone
    assert "Embodied Brain Technology Practicum" in text and "EarBetter" in text
    assert "biosignal-driven add-on for any headphones" in text
    assert not re.search(r"(?:\+?972|\b0)[\s\-.]?5\d(?:[\s\-.]?\d){7}\b", text)   # the public build

def test_the_accessibility_page_tells_the_truth_about_where_the_address_is():
    """The statement's job is to be accurate, and the sentence a reporter acts on named the wrong place: it
    said the address is in the footer of every page, but the home page carries it in the header. Every bullet
    on that page names a check that runs, so this one gets a check too: the claim is asserted against the
    rendered markup of every page, and it fails if either the sentence or the layout moves."""
    placement = {}
    for page in SITE["pages"]:
        html = build.render(SITE, page)
        head = html.partition("</header>")[0]
        slug = page["slug"].strip("/") or "home"
        placement[slug] = "header" if 'id="email"' in head else ("footer" if 'id="email"' in html else "absent")
        if slug == "accessibility":
            claim = "My address is at the foot of this page, and at the top of the home page."
            assert claim in _visible_text(html), "the statement no longer matches the markup"
    assert "absent" not in placement.values(), placement          # reachable from every page
    assert placement["home"] == "header", placement               # the home page puts it under the name
    assert {v for k, v in placement.items() if k != "home"} == {"footer"}, placement
