# itamar-avitan.github.io

Source of Itamar Avitan's personal academic website, served by GitHub Pages at
<https://itamar-avitan.github.io/>. All content lives in `site.yaml`; `build.py` validates it and renders one
template per page into `index.html`, `research/index.html`, `teaching/index.html`,
`commonplace/index.html`, `accessibility/index.html`, plus `sitemap.xml` and `robots.txt`. The built files
are committed, so no continuous-integration build is needed.

## Updating the site

1. Edit `site.yaml`.
2. Run `python3 build.py`.
3. Run `python3 -m pytest -q`.
4. Commit the rebuilt files together with `site.yaml`.
5. If the CV changed, run `make publish` in the cv repository and commit the refreshed `cv.pdf` with the rest.

Step 5 names the command rather than the copy on purpose, so this file and the cv repository's `Makefile`
cannot drift apart. `cv.pdf` here is `build/cv-public.pdf` there; this repository can neither build it nor
tell whether it has gone stale, so that alarm lives in the cv repository, where `make check-published` (run
as part of its `make test`) compares the published PDF's text against a fresh build and fails when they
differ. The test here, `test_the_published_cv_is_the_public_build_and_obeys_the_programme_ruling`, checks
only what this repository can check: that the file published is the phone-free public variant and that it
obeys the owner's wording rulings.

If the name or the identity line changed, also run `python3 tools/make_og.py`: it redraws the social-media
card `img/og.png` from `site.yaml`.

## Four pages, one site.yaml

The site was one long page until 2026-09-21, when the owner asked for separate pages: "the phone one column
is not fun", "might want to go more tabs", "not make it an online cv but more hey this is me website and some
things about me and updates and stuff". Nothing was dropped in the split.

| Page | Address | What is on it |
| --- | --- | --- |
| Home | `/` | the masthead, `about`, `elsewhere`, `now`, and the news feed |
| Research | `/research/` | Papers, Talks, Projects |
| Teaching | `/teaching/` | Courses and Students |
| Commonplace | `/commonplace/` | the quotations, in the owner's order |

A fifth page, the **accessibility statement** at `/accessibility/`, is not in that strip. It is about the
site rather than about him, so it is linked from a small row in the colophon of every page — the convention
for this, and where a reader who needs it looks. `build.py` writes it into `sitemap.xml` with the other four.
A page entry carries `nav` (the strip) or `colophon` (that row), exactly one of the two: with both, every
page would print the same address twice; with neither, the page would be published with nothing leading to
it. `build.py` refuses either case. The statement's own rules for editing are written over `accessibility`
in `site.yaml` — the short version is that every line of it is a claim about this site, each bullet names a
check that actually runs, and it must never claim conformance nobody tested or a legal duty nobody checked.

**Every page names its sections**, and every section takes a 2px ink tick on the margin rule. `elsewhere`
on the home page is the hand-off: one row per other page, each carrying a real line from it. Its gutter
holds the page's name as an outlined tag rather than a date, because these rows are addresses and not points
in time — the margin rule stays a time axis, and a tag takes no tick.

The list under `pages` in `site.yaml` **is** the site. Each entry's `slug` is three things at once: the
address under `site_url`, the folder the file is written into, and the name of its template in
`templates/pages/` (`''` means `home.html.j2`). Adding a page is one `site.yaml` entry and one template;
`build.py` never learns about it. `templates/base.html.j2` holds the frame every page shares — head, the
navigation strip, the identity block, the colophon — and `templates/macros.html.j2` the pieces they reuse.
Every relative link and asset is written with the climb back to the site root in front of it (`../` on a page
one folder down), so the built site can be opened straight off the disk.

**The navigation strip** is ordinary links, and works with JavaScript switched off. The page you are on is
the one set in ink rather than accent; it carries `aria-current="page"` and a 2px ink tick on the strip's
hairline — the same tick the margin rule uses to mark a section, one floor up. Below about 360px the five
items wrap and the tick becomes a plain underline under its own link; the row gap is wider than the tick is
long, so it never lands on the line below. The CV is the strip's one filled element, by the rule the site has
kept throughout: a filled box is the single primary action in its group.

**The `about` and `now` blocks are drafts** written on 2026-09-21 and marked as such in `site.yaml`. They
stand in for the owner's own words until he replaces them. Every clause in them comes from a fact already on
the site; do not add anything to them that he has not said. The one-sentence descriptions under the courses
in `teaching` are drafts of the same kind: they say what each course is about, drawn from its own name, and
say nothing about what he does inside it beyond the role in its `kind` tag, because nothing on record does.

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
`templates/macros.html.j2`; `tests/test_build.py` fails if the set changes. Because three marks among six
labels in one row would read as three icons that failed to load, the template renders the marked links and
the plain ones as two groups. The profile row stands in the colophon of every page, the home page included;
the address stands in the masthead at home and in the colophon everywhere else. So a visitor who lands deep
has the name at the top and an address at the foot, the front door is not six links deep before its first
word, and no page says either of them twice.

## Three widths, not two

The page is a single column under 45rem, gains its gutter and margin rule above it, and steps up again at
64rem: `--col`, `--gutter`, `--pad` and the body size all grow together, so a laptop gets a composition of
its own rather than the phone column centred in more paper, and the measure stays near the same number of
characters. The navigation strip is the one thing that does not scale with it — the tick that marks the
current page is placed by the strip's own padding and has to land on its hairline.

Two more checks, run by hand:

- `python3 tools/shots.py` rebuilds the site and writes `shots/<page>-<view>.png` for every page (not
  committed) at 1280, 1440 and 400, in both themes, printing each page's height. Look at them after any
  change to a template or to `style.css`.
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
