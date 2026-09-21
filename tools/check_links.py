"""Check every web address in site.yaml. This is a network tool: run it by hand; it is not part of pytest.

    python3 tools/check_links.py

Prints one line per address, "status url", and exits with 1 when an address is broken (no answer, or a status
other than 2xx). Some hosts turn away every client that is not a browser although the page exists; their known
answers are printed as "blocked" and do not fail the run. A blocked address is unverified, not verified: open it
in a browser. The site's own address is reported but cannot fail the run, because it answers 404 until the site
is published. An address that carries an email address or a phone number is never requested.
"""
from __future__ import annotations

import http.client
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import unquote, urlsplit

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from build import EMAIL, PHONE  # noqa: E402  (the same privacy patterns that guard site.yaml)

TIMEOUT = 20    # seconds
HEADERS = {     # browser-like, and nothing that names a person: no From header, no contact address
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en",
}
URL = re.compile(r"""https?://[^\s<>"']+""")
# Host (and its subdomains) -> the statuses it gives to clients that are not browsers, whether or not the page exists.
BOT_WALLS = {
    "linkedin.com": {999},
    "doi.org": {202, 403},          # the publisher behind the DOI refuses; doi.org itself only redirects
    "figshare.com": {202, 403},
    "bsky.app": {404},
    "scholar.google.com": {403, 429},
    "x.com": {403},
}


def collect_urls(node) -> list[str]:
    """Every http(s) address in the strings of a YAML structure: in order of appearance, each one once."""
    found: list[str] = []

    def walk(n) -> None:
        if isinstance(n, str):
            found.extend(u.rstrip(".,;:") for u in URL.findall(n))
        elif isinstance(n, dict):
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)

    walk(node)
    return list(dict.fromkeys(found))


def private_data(url: str) -> bool:
    """True when the address carries an email address or a phone number, plain or percent-encoded."""
    text = unquote(url)
    return bool(EMAIL.search(text) or PHONE.search(text))


def _on_host(url: str, domain: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host == domain or host.endswith("." + domain)


def verdict(status: int | None, *urls: str) -> str:
    """"ok", "blocked" or "broken". urls: the address asked for and the one that answered after redirects."""
    if status is None:
        return "broken"
    if any(status in statuses and _on_host(u, domain) for u in urls for domain, statuses in BOT_WALLS.items()):
        return "blocked"
    return "ok" if 200 <= status < 300 else "broken"


def fetch(url: str) -> tuple[int | None, str, str]:
    """GET the address, following redirects. Returns (status or None, the address that answered, error text)."""
    try:
        request = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status, response.url, ""
    except urllib.error.HTTPError as e:                                # an answer, only not 2xx
        e.close()
        return e.code, e.filename or url, ""                           # filename: the address that answered
    except (OSError, http.client.HTTPException, ValueError) as e:      # no connection, timeout, TLS, malformed answer or address
        return None, url, str(getattr(e, "reason", "") or e) or type(e).__name__


def check(urls: list[str], own: str | None = None) -> int:
    """Request every address and print a line for each. Returns the exit status: 1 if any is broken, else 0."""
    counts = {"ok": 0, "blocked": 0, "broken": 0}
    for url in urls:
        if private_data(url):
            counts["broken"] += 1
            print(f"refused  an address on {urlsplit(url).hostname} carries an email address or a phone number: not requested")
            continue
        status, final, error = fetch(url)
        result = verdict(status, url, final)
        code = "error" if status is None else str(status)
        if url == own and result == "broken":
            print(f"{code:<8} {url}  (the site's own address: not published yet? not counted)")
            continue
        counts[result] += 1
        if result == "blocked":
            print(f"{'blocked':<8} {url}  ({code}: this host turns away clients that are not browsers; open it in a browser)")
        else:
            print(f"{code:<8} {url}" + ("  BROKEN" if result == "broken" else "") + (f"  ({error})" if error else ""))
    print(f"{sum(counts.values())} addresses: {counts['ok']} ok, {counts['blocked']} blocked, {counts['broken']} broken")
    return 1 if counts["broken"] else 0


def main() -> int:
    site = yaml.safe_load((ROOT / "site.yaml").read_text(encoding="utf-8"))
    return check(collect_urls(site), site.get("site_url"))


if __name__ == "__main__":
    raise SystemExit(main())
