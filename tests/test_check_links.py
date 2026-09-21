"""Offline tests of tools/check_links.py: which addresses it would ask for and how it judges the answers.
No request is made here; the tool itself is a network tool and is run by hand."""
import importlib.util
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("check_links", ROOT / "tools" / "check_links.py")
check_links = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_links)

SITE = yaml.safe_load((ROOT / "site.yaml").read_text(encoding="utf-8"))
OWN = "https://itamar-avitan.github.io/"


def test_collects_every_web_address_once_in_order():
    node = {"a": "https://one.example/x", "b": [{"url": "http://two.example"}, {"url": "cv.pdf"}, {"url": None}],
            "c": "see https://three.example/page, and https://one.example/x again.", "d": "name [at] host", "e": 5}
    assert check_links.collect_urls(node) == ["https://one.example/x", "http://two.example", "https://three.example/page"]


def test_the_real_site_yields_only_https_addresses_without_private_data():
    urls = check_links.collect_urls(SITE)
    assert len(urls) == len(set(urls)) >= 15 and all(u.startswith("https://") for u in urls)
    assert OWN in urls and "https://doi.org/10.6084/m9.figshare.30542690.v1" in urls
    assert [u for u in urls if check_links.private_data(u)] == []


@pytest.mark.parametrize("url", ["https://example.org/?to=someone@example.org", "https://example.org/?to=someone%40example.org",
                                 "https://someone@example.org/", "https://example.org/call/052-1234567",
                                 "https://example.org/?tel=%2B972521234567"])
def test_addresses_carrying_an_email_or_a_phone_number_are_recognised(url):
    assert check_links.private_data(url)


@pytest.mark.parametrize("status, urls, expected", [
    (200, ["https://github.com/Itamar-Avitan"], "ok"),
    (204, ["https://example.org/"], "ok"),
    (404, ["https://github.com/Itamar-Avitan"], "broken"),
    (500, ["https://example.org/"], "broken"),
    (None, ["https://example.org/"], "broken"),                      # no answer at all
    (999, ["https://www.linkedin.com/in/itamar-avitan/"], "blocked"),
    (404, ["https://www.linkedin.com/in/itamar-avitan/"], "broken"),   # only the documented status is excused
    (403, ["https://doi.org/10.6084/m9.figshare.30542690.v1"], "blocked"),
    (202, ["https://doi.org/10.6084/m9.figshare.30542690.v1", "https://figshare.com/articles/dataset/x/1"], "blocked"),
    (403, ["https://doi.org/10.1000/x", "https://publisher.example/x"], "blocked"),
    (404, ["https://bsky.app/profile/avitanit.bsky.social"], "blocked"),
    (403, ["https://scholar.google.com/citations?user=x"], "blocked"),
    (429, ["https://scholar.google.com/citations?user=x"], "blocked"),
    (403, ["https://www.google.com/"], "broken"),                     # scholar.google.com only, not all of google.com
    (403, ["https://x.com/avitanit"], "blocked"),
    (403, ["https://dropbox.com/x"], "broken"),                       # "dropbox.com" ends with "x.com" but is another host
    (403, ["https://notx.com/"], "broken"),
])
def test_verdicts(status, urls, expected):
    assert check_links.verdict(status, *urls) == expected


def _run(monkeypatch, capsys, answers, own=OWN):
    asked = []

    def fake_fetch(url):
        asked.append(url)
        return answers[url]

    monkeypatch.setattr(check_links, "fetch", fake_fetch)
    code = check_links.check(list(answers), own)
    return code, asked, capsys.readouterr().out


def test_run_passes_when_every_link_answers_or_is_a_documented_bot_wall(monkeypatch, capsys):
    code, asked, out = _run(monkeypatch, capsys, {
        "https://github.com/Itamar-Avitan": (200, "https://github.com/Itamar-Avitan", ""),
        "https://x.com/avitanit": (403, "https://x.com/avitanit", "")})
    assert code == 0 and len(asked) == 2
    assert "200" in out and "blocked" in out and "https://x.com/avitanit" in out


def test_run_fails_on_a_broken_link_and_on_no_answer(monkeypatch, capsys):
    code, _, out = _run(monkeypatch, capsys, {"https://example.org/gone": (404, "https://example.org/gone", "")})
    assert code == 1 and "404" in out
    code, _, out = _run(monkeypatch, capsys, {"https://example.org/slow": (None, "https://example.org/slow", "timed out")})
    assert code == 1 and "timed out" in out


def test_the_sites_own_address_is_reported_but_cannot_fail_the_run(monkeypatch, capsys):
    code, asked, out = _run(monkeypatch, capsys, {OWN: (404, OWN, "")})
    assert code == 0 and asked == [OWN] and "404" in out


def test_no_request_is_made_for_an_address_with_private_data(monkeypatch, capsys):
    bad = "https://example.org/?to=someone@example.org"
    asked = []
    monkeypatch.setattr(check_links, "fetch", lambda url: asked.append(url) or (200, url, ""))
    assert check_links.check([bad, "https://example.org/fine"], OWN) == 1
    assert asked == ["https://example.org/fine"]
    assert "someone@example.org" not in capsys.readouterr().out     # and the private part is not echoed either


def test_the_request_looks_like_a_browser_and_names_nobody():
    headers = " ".join(f"{k}: {v}" for k, v in check_links.HEADERS.items())
    assert headers.startswith("User-Agent: Mozilla/5.0") and "@" not in headers and "From" not in check_links.HEADERS
    assert check_links.TIMEOUT == 20
