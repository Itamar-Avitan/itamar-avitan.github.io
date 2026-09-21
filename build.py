"""Build the static site: validate site.yaml, then render templates/index.html.j2."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

ROOT = Path(__file__).resolve().parent
REQUIRED = ["site_url", "name", "alternate_name", "identity_line", "bio", "links", "news",
            "research", "talks", "projects", "teaching", "footer", "portrait"]
PHONE = re.compile(r"(?:\+?972|\b0)[\s\-.]?5\d(?:[\s\-.]?\d){7}\b")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
UNPUBLISHED = re.compile(r"\b(in[\s-]prep(?:aration)?|under[\s-]review|submitted to)\b", re.I)


def load_site(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _strings(node):
    if isinstance(node, (str, int, float)):
        yield str(node)
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from _strings(k)
            yield from _strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _strings(v)


def validate(site: dict) -> list[str]:
    problems = [f"missing key: {k}" for k in REQUIRED if k not in site]
    for s in _strings(site):
        if PHONE.search(s):
            problems.append(f"phone number in content: {s[:60]!r}")
        if EMAIL.search(s):
            problems.append(f"plain email address in content (use 'name [at] host'): {s[:60]!r}")
        if UNPUBLISHED.search(s):
            problems.append(f"unpublished-work wording in content: {s[:60]!r}")
    return problems


def split_news(news: list[dict], visible: int = 5) -> tuple[list[dict], list[dict]]:
    """News runs newest first: the first `visible` items stay open, the older tail goes behind the expander."""
    return news[:visible], news[visible:]


def split_quotes(quotes: list[dict], visible: int = 2) -> tuple[list[dict], list[dict]]:
    """The commonplace runs the other way -- oldest line first, because its gutter is a historical axis -- so
    the older entries are its head, not its tail. Returns (older, shown) in the order they are printed."""
    return (quotes[:-visible], quotes[-visible:]) if 0 < visible < len(quotes) else ([], quotes)


def visible_research(site: dict) -> list[dict]:
    return [r for r in site["research"] if site.get("show_ongoing") or not r.get("ongoing")]


def person_jsonld(site: dict) -> str:
    return json.dumps({
        "@context": "https://schema.org", "@type": "Person",
        "name": site["name"], "alternateName": site["alternate_name"], "url": site["site_url"],
        "description": site["identity_line"],
        "affiliation": {"@type": "CollegeOrUniversity", "name": site["affiliation"]},
        "sameAs": [l["url"] for l in site["links"] if l.get("url") and l["label"] not in ("Email", "CV")],
    }, ensure_ascii=False, indent=2).replace("<", "\\u003c")


def render(site: dict) -> str:
    env = Environment(loader=FileSystemLoader(ROOT / "templates"), undefined=StrictUndefined,
                      autoescape=select_autoescape(["html", "j2"]), trim_blocks=True, lstrip_blocks=True)
    shown, older = split_news(site["news"], site.get("news_visible", 5))
    quotes_older, quotes_shown = split_quotes(site.get("quotes") or [], site.get("quotes_visible", 2))
    return env.get_template("index.html.j2").render(
        site=site, news_shown=shown, news_older=older,
        quotes_older=quotes_older, quotes_shown=quotes_shown,
        research=visible_research(site), jsonld=person_jsonld(site))


def main(argv: list[str]) -> int:
    site = load_site(ROOT / "site.yaml")
    problems = validate(site)
    if problems:
        print("site.yaml is not publishable:", *problems, sep="\n  - ")
        return 1
    (ROOT / "index.html").write_text(render(site), encoding="utf-8")
    (ROOT / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f'  <url><loc>{site["site_url"]}</loc></url>\n</urlset>\n', encoding="utf-8")
    (ROOT / "robots.txt").write_text(f'User-agent: *\nAllow: /\nSitemap: {site["site_url"]}sitemap.xml\n', encoding="utf-8")
    print("built index.html, sitemap.xml, robots.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
