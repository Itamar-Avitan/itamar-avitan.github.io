# itamar-avitan.github.io

Source of Itamar Avitan's personal academic website, served by GitHub Pages at
<https://itamar-avitan.github.io/>. It is a single static page. All content lives in `site.yaml`;
`build.py` validates it and renders `templates/index.html.j2` into `index.html`, `sitemap.xml` and
`robots.txt`. The built files are committed, so no continuous-integration build is needed.

## Updating the site

1. Edit `site.yaml`.
2. Run `python3 build.py`.
3. Run `python3 -m pytest -q`.
4. Commit the rebuilt files together with `site.yaml`.

If the name or the identity line changed, also run `python3 tools/make_og.py`: it redraws the social-media
card `img/og.png` from `site.yaml`.

The research card's thumbnail is chosen by one line. `site.yaml` holds both pictures under `figure_options`,
and the card points at one of them:

```yaml
  figure: *lab_illustration     # the lab's illustration for the paper (live)
  figure: *recovery_matrix      # the paper's own Figure 1D
```

Change that line, run `python3 build.py`, and the card swaps.
To prepare another one, run `python3 tools/make_paper_thumb.py <image> <name>`: it writes `img/<name>.webp`
and a `.jpg` fallback, square and stripped of metadata, and prints their sizes.

**No theme alters a figure.** Nothing inverts, dims or desaturates one, in either theme: an illustration
misread is as wrong as a plot misread. What settles a pale print into the dark page is the mount — the light
card behind it, `--mat` in `style.css`. A figure whose colour is the data still carries `plot: true`, because
the distinction is real and the `--plot-filter` hook is what a future change has to go through in the open.

## The link marks: three, and only three

Email, GitHub and Bluesky carry a mark; Google Scholar, LinkedIn, X and ORCID carry their text label alone.
That is the owner's ruling of 2026-09-21 and it is not a gap to fill: LinkedIn's policy forbids third-party
use of its logo, ORCID's terms forbid altering the iD icon, the Google Scholar stand-in was illegible at the
size it was used, and X's terms could not be established. The long form is in the comment above `ICONS` in
`templates/index.html.j2`; `tests/test_build.py` fails if the set changes. Because three marks among seven
labels in one row would read as four icons that failed to load, the template renders the marked links and the
plain ones as two groups, and the stylesheet steps further between the groups than inside them.

Two more checks, run by hand:

- `python3 tools/shots.py` rebuilds the page and writes `shots/desktop-light.png`, `shots/desktop-dark.png`
  and `shots/phone-light.png` (not committed). Look at them after any change to the template or `style.css`.
- `python3 tools/check_links.py` requests every web address in `site.yaml`, the site's own included, and exits
  with 1 if one is broken. It needs the network, so it is not part of the tests. A line that says `blocked` is
  a host that turns away every client that is not a browser: open that address in a browser. A `200` means
  that the server answered, not that the page still says what it should.

One-time setup: `python3 -m pip install -r requirements.txt`, then `python3 -m playwright install chromium`
(the browser tests in `tests/test_browser.py` and `tools/shots.py` drive a headless Chromium; they load only
local files).

## Never add private information: this repository is public

That covers the files and the git history alike: no phone number, no plain-text email address
(write `name [at] host`), no referee contacts, no unpublished or in-preparation work (not even hidden
or commented out), no private numbers, no citation counts. `build.py` refuses to build when
`site.yaml` contains a phone number, a plain-text email address or unpublished-work wording, but
that check is a safety net, not a substitute for care.
