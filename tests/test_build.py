import copy
import json
import re
import shutil
from html import unescape
from pathlib import Path
from urllib.parse import unquote

import pytest
from PIL import Image

import build

SITE = build.load_site(build.ROOT / "site.yaml")


def test_real_content_is_valid():
    assert build.validate(SITE) == []


@pytest.mark.parametrize("bad", ["+972 52 123 4567", "052-1234567", "call 0521234567 now"])
def test_phone_numbers_are_rejected_anywhere(bad):
    site = copy.deepcopy(SITE)
    site["bio"][0] += " " + bad
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
    site["bio"][0] += " someone@example.org"
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
    site["news"][0]["text"] = "5 < 6 & <script>x</script>"
    html = build.render(site)
    assert "<script>x</script>" not in html and "5 &lt; 6 &amp;" in html
    assert 'href="cv.pdf"' not in html
    site["show_cv"] = True
    assert 'href="cv.pdf"' in build.render(site)


def test_main_writes_nothing_when_invalid(tmp_path, monkeypatch):
    bad = copy.deepcopy(SITE); bad["bio"][0] += " 052-1234567"
    monkeypatch.setattr(build, "ROOT", tmp_path)
    monkeypatch.setattr(build, "load_site", lambda _p: bad)
    (tmp_path / "templates").mkdir()
    assert build.main([]) == 1 and not (tmp_path / "index.html").exists()


def test_main_builds_the_three_files(tmp_path, monkeypatch):
    shutil.copytree(build.ROOT / "templates", tmp_path / "templates")
    shutil.copy(build.ROOT / "site.yaml", tmp_path / "site.yaml")
    monkeypatch.setattr(build, "ROOT", tmp_path)
    assert build.main([]) == 0
    assert "<title>Itamar Avitan</title>" in (tmp_path / "index.html").read_text(encoding="utf-8")
    assert f'<loc>{SITE["site_url"]}</loc>' in (tmp_path / "sitemap.xml").read_text(encoding="utf-8")
    assert "Allow: /" in (tmp_path / "robots.txt").read_text(encoding="utf-8")


# --- Task 2: the D2 template ---

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


def test_every_content_string_is_rendered():
    text = _visible_text(build.render(SITE))
    missing = [s for s in _values({k: SITE[k] for k in ("identity_line", "bio", "news", "talks", "projects", "teaching")})
               if not s.startswith("http") and " ".join(s.split()) not in text]
    assert missing == []


def test_head_metadata():
    html = build.render(SITE)
    assert "<title>Itamar Avitan</title>" in html and html.count("<h1") == 1
    for needle in ('rel="canonical" href="https://itamar-avitan.github.io/"', 'property="og:image"',
                   'name="twitter:card" content="summary_large_image"', 'application/ld+json', 'name="description"'):
        assert needle in html


def test_no_mailto_no_plain_address_no_third_party_hosts():
    html = build.render(SITE)
    assert "mailto:" not in html.replace('"mailto:" +', "") and "@post.bgu.ac.il" not in html
    hosts = set(re.findall(r'(?:src|href)="https?://([^/"]+)', html.split("</head>")[0]))
    assert hosts <= {"itamar-avitan.github.io"}, hosts      # head may only reference the site itself


def test_removed_items_stay_removed():
    text = _visible_text(build.render(SITE))
    assert "Curved Spaces" not in text and "Feb 2026" not in text
    assert 'BCI4ALS</a>' not in build.render(SITE)          # card title is not a link


def test_open_graph_points_at_the_site_and_its_card():
    html = build.render(SITE)
    for needle in ('property="og:title" content="Itamar Avitan"', 'property="og:type" content="website"',
                   'property="og:url" content="https://itamar-avitan.github.io/"',
                   'property="og:image" content="https://itamar-avitan.github.io/img/og.png"',
                   f'property="og:description" content="{SITE["identity_line"]}"'):
        assert needle in html


def test_section_ids_and_script_hooks():
    html = build.render(SITE)
    for sec in ("research", "news", "talks", "projects", "teaching"):
        assert html.count(f'<section id="{sec}"') == 1
    assert html.count('id="theme-toggle"') == 1
    assert '<span id="email">avitanit [at] post.bgu.ac.il</span>' in html


def test_research_card_shows_every_field():
    html = build.render(SITE)
    text, card = _visible_text(html), SITE["research"][0]
    shown = [card["title"], card["tldr"], card["figure"]["caption"], *card["authors"], *card["badges"],
             *(b["label"] for b in card["buttons"])]
    assert [s for s in shown if " ".join(s.split()) not in text] == []
    assert all(f'href="{b["url"]}"' in html for b in card["buttons"])
    assert f'<img src="{card["figure"]["src"]}" alt="{card["figure"]["alt"]}"' in html
    assert '<span class="me">Itamar Avitan</span>, Tal Golan' in html
    assert html.count("btn btn--primary") == 1              # Paper; the CV button is hidden for now
    assert '<span class="tag tag--venue">NeurIPS 2025</span>' in html and '<span class="tag">CCN 2025 · Talk</span>' in html


def test_ongoing_card_gets_an_ongoing_badge_only_when_shown():
    site = copy.deepcopy(SITE)
    site["research"].append({**site["research"][0], "title": "FIXTURE ONGOING", "ongoing": True})
    hidden = build.render(site)
    assert "FIXTURE ONGOING" not in hidden and ">Ongoing<" not in hidden
    site["show_ongoing"] = True
    html = build.render(site)
    assert "FIXTURE ONGOING" in html and html.count('<span class="tag">Ongoing</span>') == 1


def test_news_is_split_between_the_list_and_the_details_element():
    shown, older = build.split_news(SITE["news"], SITE["news_visible"])
    before, _, after = build.render(SITE).partition('<details class="older">')
    assert older and all(n["text"] in _visible_text(before) for n in shown)
    inside = _visible_text(after.split("</details>")[0])
    assert all(n["text"] in inside for n in older) and f"Older news ({len(older)})" in inside
    site = copy.deepcopy(SITE)
    site["news_visible"] = 99
    assert "<details" not in build.render(site)


def test_talks_link_the_venue_and_show_the_note():
    html = build.render(SITE)
    assert '<p class="venue"><a href="https://2025.ccneuro.org/contributed-talk/?id=65">' in html
    assert '<p class="note">Preliminary version of the NeurIPS 2025 paper.</p>' in html
    assert html.count('<p class="note">') == 1


def test_project_titles_link_only_when_a_url_is_given():
    html = build.render(SITE)
    assert '<h3><a href="https://github.com/Itamar-Avitan/earbetter-ebt-practicum">EarBetter</a></h3>' in html
    assert "<h3>BCI4ALS</h3>" in html


def test_optional_keys_may_be_absent():
    site = copy.deepcopy(SITE)
    for entry in site["talks"] + site["projects"] + site["research"] + site["links"]:
        for key in ("url", "note", "text", "figure", "me", "ongoing", "authors", "badges", "buttons"):
            entry.pop(key, None)
    for key in ("role", "nobreak", "news_visible", "show_ongoing"):
        site.pop(key, None)
    html = build.render(site)
    for gone in ('<p class="venue"><a', 'class="note"', "<figure", 'class="authors"', 'class="role"', 'class="nb"',
                 'rel="me"', 'id="email"', 'class="tag tag--venue"', 'class="btn'):
        assert gone not in html, gone
    assert "<h3>EarBetter</h3>" in html and "Cognitive Computational Neuroscience (CCN) 2025" in html


def test_dates_are_time_elements():
    html = build.render(SITE)
    for needle in ('<time class="when" datetime="2026-09">Sep 2026</time>',
                   '<time datetime="2026-09-14">14</time>–<time datetime="2026-09-15">15 Sep 2026</time>',
                   '<time datetime="2025-12-05">5 Dec 2025</time>',
                   '<time datetime="2026-07-24">24 Jul</time>–<time datetime="2026-08-06">6 Aug 2026</time>',
                   '<time datetime="2022">2022</time>–<time datetime="2023">2023</time>',
                   '<time class="what" datetime="2026-09">September 2026</time>'):
        assert needle in html, needle


def test_dates_that_cannot_be_read_stay_plain_text():
    site = copy.deepcopy(SITE)
    site["news"][0]["date"] = "Summer 2026"
    site["talks"][0]["date"] = "32 Sep 2026"
    site["projects"][0]["period"] = "1–2–3 Sep 2026"
    site["footer"]["updated"] = "a while ago"
    html = build.render(site)
    for needle in ('<span class="when">Summer 2026</span>', '<p class="when">32 Sep 2026</p>',
                   '<p class="when">1–2–3 Sep 2026</p>', '<span class="what">a while ago</span>'):
        assert needle in html, needle
    assert all(re.fullmatch(r"\d{4}(-\d\d(-\d\d)?)?", d) for d in re.findall(r'datetime="([^"]*)"', html))


def test_teaching_sentences_stay_whole_and_lift_their_dates():
    html = build.render(SITE)
    assert ('University of the Negev<span class="vh">: </span></p>' in html
            and '<p class="when">spring 2026<span class="vh">.</span></p>' in html)
    assert '<p class="when"><time datetime="2021">2021</time>–<time datetime="2023">2023</time><span class="vh">.</span></p>' in html
    assert '<p class="what">Mentoring: two undergraduate research students.</p>' in html


def test_terms_of_art_do_not_break_and_stay_escaped():
    html = build.render(SITE)
    assert all(f'<span class="nb">{term}</span>' in html for term in ("Ben-Gurion", "Best-Fitting", "(NeurIPS) 2025"))
    site = copy.deepcopy(SITE)
    site["bio"][0] = "Ben-Gurion <b>& co</b>"
    assert '<p><span class="nb">Ben-Gurion</span> &lt;b&gt;&amp; co&lt;/b&gt;</p>' in build.render(site)
    del site["nobreak"]
    assert "<p>Ben-Gurion &lt;b&gt;&amp; co&lt;/b&gt;</p>" in build.render(site)


def test_role_line_keeps_the_lab_link():
    html = build.render(SITE)
    assert "<li>PhD candidate</li>" in html
    assert '<li><a href="https://brainsandmachines.org">Brains and Machines Lab</a></li>' in html


def test_portrait_and_one_calm_row_of_links():
    html = build.render(SITE)
    assert ('<picture><source srcset="img/portrait-400.webp" type="image/webp">'
            '<img src="img/portrait-800.jpg" width="120" height="120" alt="Itamar Avitan"></picture>') in html
    assert html.count('<ul class="links"') == 1 and "monogram" not in html
    page = unescape(html)
    for link in SITE["links"]:
        if link["label"] not in ("Email", "CV"):
            assert page.count(f'<a rel="me" href="{link["url"]}">{link["label"]}</a>') == 1
    site = copy.deepcopy(SITE)
    site["show_cv"] = True
    assert build.render(site).count('<a class="btn btn--primary" href="cv.pdf">CV</a>') == 1


def test_favicon_is_the_mark():
    mark = (build.ROOT / "img" / "mark.svg").read_text(encoding="utf-8").strip()
    assert 'viewBox="0 0 42 10"' in mark and mark.count("<circle") == 2 and mark.count("<rect") == 1
    for shape in ('<circle cx="5" cy="5" r="5"/>', '<circle cx="21" cy="5" r="5"/>', '<rect x="32" width="10" height="10"/>'):
        assert shape in mark
    href = re.search(r'<link rel="icon" href="data:image/svg\+xml,([^"]+)">', build.render(SITE)).group(1)
    assert unquote(href).replace("'", '"') == mark


def test_images_carry_no_metadata_and_the_social_card_is_1200x630():
    for name in ("portrait-400.webp", "portrait-800.jpg", "og.png"):
        with Image.open(build.ROOT / "img" / name) as im:
            assert len(im.getexif()) == 0 and not getattr(im, "text", None), name
    with Image.open(build.ROOT / "img" / "og.png") as im:
        assert im.size == (1200, 630)
