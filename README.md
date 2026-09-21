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

One-time setup: `python3 -m pip install -r requirements.txt`.

## Never add private information: this repository is public

That covers the files and the git history alike: no phone number, no plain-text email address
(write `name [at] host`), no referee contacts, no unpublished or in-preparation work (not even hidden
or commented out), no private numbers, no citation counts. `build.py` refuses to build when
`site.yaml` contains a phone number, a plain-text email address or unpublished-work wording, but
that check is a safety net, not a substitute for care.
