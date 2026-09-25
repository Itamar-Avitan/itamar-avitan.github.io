"""Build the static site: validate site.yaml, then render every page it lists.

The site is five pages (home, research, teaching, commonplace, accessibility). Each entry under `pages` in
site.yaml names its own template in templates/pages/ and the folder it is written into, so adding a page is
a site.yaml edit and one template -- never a change here. All text still lives in site.yaml; templates hold
markup only. One more file is written beside them: 404.html, from the `not_found` entry, which is not a page
of the site (no link leads to it, it is not in the sitemap, and it asks not to be indexed) but the answer
GitHub Pages serves for an address with nothing at it.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from markupsafe import Markup, escape

ROOT = Path(__file__).resolve().parent
REQUIRED = ["site_url", "name", "alternate_name", "identity_line", "pages", "about", "now", "links", "news",
            "research", "talks", "projects", "teaching", "accessibility", "footer", "portrait"]
PHONE = re.compile(r"(?:\+?972|\b0)[\s\-.]?5\d(?:[\s\-.]?\d){7}\b")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
UNPUBLISHED = re.compile(r"\b(in[\s-]prep(?:aration)?|under[\s-]review|submitted to)\b", re.I)
ABSOLUTE = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|//|#)")
# A quotation's permalink slug: lower-case ASCII words joined by single hyphens. It becomes an id and an
# address, and it is never changed once published (see the note over `quotes` in site.yaml).
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def opening(text: str, cap: int = 15) -> str:
    """The first clause of a quotation, for its permalink's accessible name: the words up to the first one
    that closes a sentence, or up to the first one that closes a clause with "…" in place of its comma, or
    the first `cap` words and "…" when neither comes sooner. The line's outer quotation marks are the page's
    house convention (see site.yaml), so they are dropped and the name puts its own around the clause. Four
    links that all said "Link to this quotation" were four identical entries in a screen reader's list of
    links (review of WP-S4, 2026-09-25); the speaker alone would not tell two Holmes lines apart."""
    words = text.strip("“”").split()
    taken: list[str] = []
    for word in words:
        taken.append(word)
        bare = word.rstrip("’”")
        if bare.endswith((".", "!", "?")):
            return " ".join(taken)
        if bare.endswith((",", ";", ":")):
            taken[-1] = bare.rstrip(",;:") + "…"
            return " ".join(taken)
        if len(taken) == cap and len(words) > cap:
            return " ".join(taken) + "…"
    return " ".join(taken)


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


def _terms_cut_by(text: str, phrase: str, terms: list[str]) -> list[str]:
    """The no-break terms an occurrence of which straddles either end of `phrase` (its first occurrence in
    `text`): a term wholly inside or wholly outside the phrase is fine, one that crosses its edge is cut."""
    start = text.index(phrase)
    end = start + len(phrase)
    cut = []
    for term in terms:
        at = text.find(term)
        while at != -1:
            overlaps = at < end and start < at + len(term)
            inside = start <= at and at + len(term) <= end
            if overlaps and not inside:
                cut.append(term)
                break
            at = text.find(term, at + 1)
    return cut


def validate(site: dict) -> list[str]:
    problems = [f"missing key: {k}" for k in REQUIRED if k not in site]
    for s in _strings(site):
        if PHONE.search(s):
            problems.append(f"phone number in content: {s[:60]!r}")
        if EMAIL.search(s):
            problems.append(f"plain email address in content (use 'name [at] host'): {s[:60]!r}")
        if UNPUBLISHED.search(s):
            problems.append(f"unpublished-work wording in content: {s[:60]!r}")
    # A news item may link one phrase of its sentence (`link`) to an address (`url`). The two go together, and
    # the phrase has to be in the sentence verbatim, or a rewrite of the sentence would quietly drop the link.
    for item in site.get("news") or []:
        if bool(item.get("link")) != bool(item.get("url")):
            problems.append(f"news item {item.get('date')!r} has only one of link/url")
        elif item.get("link") and item["link"] not in item["text"]:
            problems.append(f"news item {item.get('date')!r}: link {item['link']!r} is not in its text")
        elif item.get("link"):
            # The template splits the sentence at the phrase's first occurrence and wraps the no-break terms
            # in each piece by itself, so a phrase may hold whole terms but must never cut through one:
            # "NeurIPS" inside "(NeurIPS) 2025" would leave that term split across the pieces, without its
            # span and without anything to say so.
            for term in _terms_cut_by(item["text"], item["link"], site.get("nobreak") or []):
                problems.append(f"news item {item.get('date')!r}: link {item['link']!r} cuts through the nobreak term {term!r}")
    # Every quotation carries a slug, well formed and its own: the slug is the row's id and the address its
    # "§" permalink points at, so a missing one leaves an entry that cannot be shared and a repeated one
    # sends two links to the same place.
    seen = set()
    for quote in site.get("quotes") or []:
        slug = quote.get("slug")
        if not isinstance(slug, str) or not SLUG.fullmatch(slug):
            problems.append(f"quotation {quote.get('text', '')[:40]!r} has no well-formed slug (got {slug!r})")
        elif slug in seen:
            problems.append(f"quotation slug {slug!r} is used twice")
        seen.add(slug)
    # A paper card's `doi` feeds the structured data; its BibTeX block prints the same identifier for a reader.
    # Both come from one FACTS row (PUB-DOI), so they may not disagree.
    for card in site.get("research") or []:
        if card.get("doi") and card.get("cite") and card["doi"] not in card["cite"]:
            problems.append(f"research card {card.get('title', '')[:40]!r}: doi {card['doi']!r} is not in its cite block")
    for page in site.get("pages") or []:
        if not template_path(page).exists():
            problems.append(f"page {page['slug']!r} has no template at {template_path(page).relative_to(ROOT)}")
        # A page is linked from the navigation strip or from the colophon row, and never from both: with both
        # labels every page would print the same address twice. With neither, the page is built and published
        # and nothing on the site leads to it, which is the failure a reader cannot see.
        if bool(page.get("nav")) == bool(page.get("colophon")):
            both = "both a nav and a colophon label" if page.get("nav") else "neither a nav nor a colophon label"
            problems.append(f"page {page['slug']!r} has {both}: it needs exactly one")
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
    """The <title> and og:title: the page's own `title` when it sets one, else "<heading> — <name>", else the
    bare name. The home page sets one (see the note over `pages` in site.yaml): a search result that says only
    "Itamar Avitan" cannot be told from his namesake's."""
    return page.get("title") or (f'{page["heading"]} — {site["name"]}' if page.get("heading") else site["name"])


def page_description(site: dict, page: dict) -> str:
    return page.get("description") or site["identity_line"]


def local(url: str, base: str) -> str:
    """Prefix a path of this site with the climb back to its root; leave a full address alone."""
    return url if ABSOLUTE.match(url or "") else base + url


def printable_url(url: str) -> Markup:
    """An address printed as text, with its line breaks chosen rather than left to the browser: after the
    scheme's "//", before each dot of the host, and after the slash that ends the host -- never at a hyphen
    inside a host label, which is wrapped in the no-break span. Left alone, all three engines break
    "https://itamar-avitan.github.io/cv.pdf" after "itamar-" on a 320px screen (and mid-word at 200% text),
    and a hyphen at a line end reads as a hyphenation, so a reader retyping the address drops it. The labels
    are kept separate rather than the whole host tied, so at 200% text on a 320px screen, where the host is
    wider than the measure, the address still has a place of its own to break before overflow-wrap breaks
    it anywhere (deep review 2026-09-25, WP-S6 review). The text is escaped here; the result is Markup."""
    scheme, sep, rest = url.partition("://")
    host, slash, path = rest.partition("/")
    labels = [Markup('<span class="nb">{}</span>').format(label) if "-" in label else escape(label)
              for label in host.split(".")]
    return Markup("").join([escape(scheme + sep), Markup("<wbr>"), Markup("<wbr>.").join(labels),
                            escape(slash), Markup("<wbr>") if path else Markup(""), escape(path)])


def _person(site: dict) -> dict:
    """The one Person every page describes. The @id is what lets a crawler merge the copies on every page into
    one entity, and tell that entity from the other Itamar Avitan. Everything past the name is optional: a key
    missing from site.yaml leaves its field out, never a blank. `knows_about` holds only terms a page of the
    site prints (tests/test_build.py checks that): structured data describes the visible page, it does not
    add to it."""
    role = site.get("role") or []
    lab = next((r for r in role if r.get("url")), None)
    university = {"@type": "CollegeOrUniversity", "name": site["affiliation"],
                  **({"url": site["affiliation_url"]} if site.get("affiliation_url") else {})}
    person = {
        "@type": "Person", "@id": site["site_url"] + "#person",
        "name": site["name"], "alternateName": site["alternate_name"], "url": site["site_url"],
        "image": site["site_url"] + site["portrait"]["jpg"],
        "description": site["identity_line"], "affiliation": university, "alumniOf": university,
        # a profile's canonical address carries no interface language
        "sameAs": [l["url"].replace("&hl=en", "") for l in site["links"]
                   if l.get("url") and l["label"] not in ("Email", "CV")],
    }
    if role:
        person["jobTitle"] = role[0]["label"]
    if lab:
        person["memberOf"] = {"@type": "ResearchOrganization", "name": lab["label"], "url": lab["url"]}
    if site.get("knows_about"):
        person["knowsAbout"] = site["knows_about"]
    return person


def _article(site: dict, card: dict) -> dict:
    """A ScholarlyArticle for a paper card that carries `published`: the record a scholarly crawler reconciles
    on -- the DOI first (FACTS PUB-DOI, the identifier the CVs print), then the arXiv page -- with him as the
    author the graph already knows by @id."""
    by = {b["label"]: b["url"] for b in card.get("buttons") or []}
    pub = card["published"]
    article = {
        "@type": "ScholarlyArticle", "name": card["title"], "headline": card["title"],
        "author": [{"@id": site["site_url"] + "#person"} if a == card.get("me") else {"@type": "Person", "name": a}
                   for a in card.get("authors") or []],
        "datePublished": pub["year"],
        "isPartOf": {"@type": "PublicationVolume", "volumeNumber": pub["volume"],
                     "isPartOf": {"@type": "Periodical", "name": pub["periodical"]}},
    }
    if "Paper" in by:
        article["url"] = by["Paper"]
    same_as = ([f'https://doi.org/{card["doi"]}'] if card.get("doi") else []) + ([by["arXiv"]] if "arXiv" in by else [])
    if same_as:
        article["sameAs"] = same_as
    return article


def person_jsonld(site: dict, page: dict | None = None) -> str:
    """The head's JSON-LD, one graph: the Person on every page; on the home page a ProfilePage whose main
    entity he is, first; on the research page one ScholarlyArticle per shown paper card that carries
    `published`. "<" is escaped so no content string can close the script element it is written into."""
    page = page or site["pages"][0]
    graph = [_person(site)]
    if not page["slug"]:
        graph.insert(0, {"@type": "ProfilePage", "@id": site["site_url"] + "#page", "url": site["site_url"],
                         "mainEntity": {"@id": site["site_url"] + "#person"}})
    if page["slug"] == "research/":
        graph += [_article(site, card) for card in visible_research(site) if card.get("published")]
    return json.dumps({"@context": "https://schema.org", "@graph": graph},
                      ensure_ascii=False, indent=2).replace("<", "\\u003c")


def environment() -> Environment:
    env = Environment(loader=FileSystemLoader(ROOT / "templates"), undefined=StrictUndefined,
                      autoescape=select_autoescape(["html", "j2"]), trim_blocks=True, lstrip_blocks=True)
    env.filters["local"] = local
    env.filters["opening"] = opening
    env.filters["printable_url"] = printable_url
    return env


def links_to(site: dict, page: dict, base: str, label_key: str) -> list[dict]:
    """The rows of one link list: the strip (`nav`) or the colophon row (`colophon`).

    Two lists, one shape, because they are the same thing at two volumes: the strip carries the pages a
    visitor came for, the colophon the page about the site itself. A page carries one label or the other
    (validate() holds that), so neither list can say an address the other already says.
    """
    return [{"label": p[label_key], "href": local(p["slug"], base) or "./", "current": p is page}
            for p in site["pages"] if p.get(label_key)]


def not_found_page(site: dict) -> dict:
    """The 404 page: not a member of `pages`, so it is in no link list and not in the sitemap. `noindex` is
    the switch render() and the base template read: root-absolute links, a robots line, no canonical."""
    return {**site["not_found"], "slug": "404", "noindex": True}


def render(site: dict, page: dict | None = None) -> str:
    """One page of the site. Without an argument: the home page, which is the first entry under `pages`."""
    page = page or site["pages"][0]
    base = "/" if page.get("noindex") else base_of(page)     # the 404 page is served at every depth
    shown, older = split_news(site["news"], site.get("news_visible", 5))
    nav = links_to(site, page, base, "nav")
    colophon_nav = links_to(site, page, base, "colophon")
    return environment().get_template(template_name(page)).render(
        site=site, page=page, base=base, nav=nav, colophon_nav=colophon_nav,
        page_url=page_url(site, page), page_title=page_title(site, page),
        page_description=page_description(site, page),
        news_shown=shown, news_older=older,
        research=visible_research(site), jsonld=person_jsonld(site, page))


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
    if site.get("not_found"):
        (ROOT / "404.html").write_text(render(site, not_found_page(site)), encoding="utf-8")
        written.append("404.html")
    locs = "".join(f"  <url><loc>{page_url(site, p)}</loc></url>\n" for p in site["pages"])
    (ROOT / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f'{locs}</urlset>\n', encoding="utf-8")
    (ROOT / "robots.txt").write_text(f'User-agent: *\nAllow: /\nSitemap: {site["site_url"]}sitemap.xml\n', encoding="utf-8")
    print("built", ", ".join(written + ["sitemap.xml", "robots.txt"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
