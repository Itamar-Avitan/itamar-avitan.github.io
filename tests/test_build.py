import copy
import json
import re
import shutil
from pathlib import Path

import pytest

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
