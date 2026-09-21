"""Build the static site: validate site.yaml, then render every page it lists.

The site is four pages (home, research, teaching, commonplace). Each entry under `pages` in site.yaml names
its own template in templates/pages/ and the folder it is written into, so adding a page is a site.yaml edit
and one template -- never a change here. All text still lives in site.yaml; templates hold markup only.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

ROOT = Path(__file__).resolve().parent
REQUIRED = ["site_url", "name", "alternate_name", "identity_line", "pages", "about", "now", "links", "news",
            "research", "talks", "projects", "teaching", "footer", "portrait"]
PHONE = re.compile(r"(?:\+?972|\b0)[\s\-.]?5\d(?:[\s\-.]?\d){7}\b")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
UNPUBLISHED = re.compile(r"\b(in[\s-]prep(?:aration)?|under[\s-]review|submitted to)\b", re.I)
ABSOLUTE = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|//|#)")


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
    for page in site.get("pages") or []:
        if not template_path(page).exists():
            problems.append(f"page {page['slug']!r} has no template at {template_path(page).relative_to(ROOT)}")
    return problems


def split_news(news: list[dict], visible: int = 5) -> tuple[list[dict], list[dict]]:
    """News runs newest first: the first `visible` items stay open, the older tail goes behind the expander."""
    return news[:visible], news[visible:]


def visible_research(site: dict) -> list[dict]:
    return [r for r in site["research"] if site.get("show_ongoing") or not r.get("ongoing")]


# --- pages -----------------------------------------------------------------------------------------------
# A page's slug does all the work: '' or 'research/'. It is the address under site_url, the folder the file is
# written into, the name of its template, and -- counted in slashes -- how far the page sits below the root,
# which is what every relative link and asset on it has to climb back.

def template_name(page: dict) -> str:
    return f"pages/{page['slug'].strip('/') or 'home'}.html.j2"


def template_path(page: dict) -> Path:
    return ROOT / "templates" / template_name(page)


def base_of(page: dict) -> str:
    """The climb from this page back to the site root: '' at the root, '../' one folder down."""
    return "../" * len([part for part in page["slug"].split("/") if part])


def page_url(site: dict, page: dict) -> str:
    return site["site_url"] + page["slug"]


def page_title(site: dict, page: dict) -> str:
    return f'{page["heading"]} — {site["name"]}' if page.get("heading") else site["name"]


def page_description(site: dict, page: dict) -> str:
    return page.get("description") or site["identity_line"]


def local(url: str, base: str) -> str:
    """Prefix a path of this site with the climb back to its root; leave a full address alone."""
    return url if ABSOLUTE.match(url or "") else base + url


def person_jsonld(site: dict) -> str:
    return json.dumps({
        "@context": "https://schema.org", "@type": "Person",
        "name": site["name"], "alternateName": site["alternate_name"], "url": site["site_url"],
        "description": site["identity_line"],
        "affiliation": {"@type": "CollegeOrUniversity", "name": site["affiliation"]},
        "sameAs": [l["url"] for l in site["links"] if l.get("url") and l["label"] not in ("Email", "CV")],
    }, ensure_ascii=False, indent=2).replace("<", "\\u003c")


def environment() -> Environment:
    env = Environment(loader=FileSystemLoader(ROOT / "templates"), undefined=StrictUndefined,
                      autoescape=select_autoescape(["html", "j2"]), trim_blocks=True, lstrip_blocks=True)
    env.filters["local"] = local
    return env


def render(site: dict, page: dict | None = None) -> str:
    """One page of the site. Without an argument: the home page, which is the first entry under `pages`."""
    page = page or site["pages"][0]
    base = base_of(page)
    shown, older = split_news(site["news"], site.get("news_visible", 5))
    nav = [{"label": p["nav"], "href": local(p["slug"], base) or "./", "current": p is page}
           for p in site["pages"]]
    return environment().get_template(template_name(page)).render(
        site=site, page=page, base=base, nav=nav,
        page_url=page_url(site, page), page_title=page_title(site, page),
        page_description=page_description(site, page),
        news_shown=shown, news_older=older,
        research=visible_research(site), jsonld=person_jsonld(site))


def main(argv: list[str]) -> int:
    site = load_site(ROOT / "site.yaml")
    problems = validate(site)
    if problems:
        print("site.yaml is not publishable:", *problems, sep="\n  - ")
        return 1
    written = []
    for page in site["pages"]:
        out = ROOT / page["slug"] / "index.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render(site, page), encoding="utf-8")
        written.append(str(out.relative_to(ROOT)))
    locs = "".join(f"  <url><loc>{page_url(site, p)}</loc></url>\n" for p in site["pages"])
    (ROOT / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f'{locs}</urlset>\n', encoding="utf-8")
    (ROOT / "robots.txt").write_text(f'User-agent: *\nAllow: /\nSitemap: {site["site_url"]}sitemap.xml\n', encoding="utf-8")
    print("built", ", ".join(written + ["sitemap.xml", "robots.txt"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
