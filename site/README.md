# HN Radar site

The web pages for HN Radar. Plain HTML, CSS and JavaScript, no build step and no server. This folder is kept separate from the hackathon work on purpose: it is built here so every change goes through the normal pull request and review flow, then it is **copied into the RawWeights site as one folder** (`/careers/`).

Only public build-time data is shown (`data/jobs.json`, later pulse and Show HN data). Per-user data (GitHub profile, match, draft) is never published or stored here: see [AGENTS.md](../AGENTS.md) rule 5.

## Layout

```
site/
  README.md              this file (committed)
  careers/               THE deployable folder; copied to the site root as /careers/
    index.html           job explorer (search, filters, detail view)
    insights.html        top technologies, remote share, what posts leave out
    company.html         placeholder: coming soon (issue 31)
    journey.html         placeholder: coming soon (issue 31)
    check.html           placeholder: coming soon (issue 31)
    hn.css, hn.js        shared styles and helpers (text cleaning, safe links, designed states)
    data/jobs.json       dev copy of ../../data/jobs.json (git-ignored, refreshed by the sync script)
  includes/              dev copy of the live site's design system, nav and footer (git-ignored)
  fonts/                 dev copy of the live site's fonts (git-ignored)
  _private/              local-only notes, templates and prompts (git-ignored; never committed)
```

Pages live in `careers/` and use the **same relative paths as on the live site** (`../includes/brand.css`, `../includes/rw-nav.js`, `../includes/rw-footer.js`, `./data/...`). That is why `includes/` and `fonts/` sit next to `careers/` here: the page works unchanged in both places.

## Run it locally

From the repo root:

```powershell
powershell -File site/_private/sync-dev-assets.ps1    # copy includes, fonts and jobs.json (first time, and after data or design changes)
python -m http.server 8000 --directory site
```

Open http://localhost:8000/careers/. Use a server, not a `file://` URL, because the page loads JSON with `fetch`.

## Rules for every page

1. **Design:** use the site's design tokens from `includes/brand.css`: the `--rw-*` variables (`--rw-text`, `--rw-text-muted`, `--rw-card`, `--rw-border`, `--rw-orange`, `--rw-link`, `--rw-font-display`, `--rw-r-md` ...). The design guide mentions `--md-sys-color-*` names, but the live `brand.css` defines only `--rw-*`, the two fonts already loaded there (Space Grotesk for headings, Inter for body) and the `rw-` class prefix (`rw-hn-` for new HN Radar components). Never hard-code colours. Light and dark themes both work: keep the inline theme script from the template at the top of `<head>`.
2. **Start from a template:** copy `_private/templates/base.html` for a plain page, `tool.html` for an interactive one, and read `_private/reference/unslop.html` for a page that loads a JSON file (the pattern `fetch('./data/x.json')` is the same here). `_private/templates/components.html` shows every UI component.
3. **Untrusted text:** post text comes from Hacker News and can contain anything. Write it with `textContent` only, never `innerHTML`; remove control and bidirectional characters first (see `clean()` in `careers/index.html`).
4. **Citations:** any statement about what users said shows its HN comment link. No uncited claims.
5. **Never a blank screen:** a missing or invalid JSON file shows a visible message; an empty list shows an empty state.
6. **Statuses are designed states, not errors:** `low_confidence`, `truncated`, `no_strong_matches` and the others in the schemas (`_private/schema/`).
7. **Accessible and small:** keyboard friendly, visible focus, contrast AA, works from 360 px wide, no frameworks unless a page truly needs one.
8. **Page area:** wrap the page content in a `<div id="content" class="rw-hn-wrap">`, not `<main>`. The page owns this area; the nav and footer are the site's components and must not be touched by page styles.
9. **Paths:** relative only (`./data/`, `../includes/`). No absolute `/careers/...` paths, so the folder can be moved or renamed.

## Adding a page

1. Copy a template into `site/careers/` (for example `careers/insights.html`).
2. Replace the SEO placeholders (title, description, canonical `https://rawweights.com/careers/<page>.html`, Open Graph).
3. Keep the nav and footer blocks and the theme script as they are.
4. Read data from `./data/<file>.json` and add that file to the sync script if it is new.
5. Open the page locally in light and dark mode and at 360 px wide.

## Moving the folder into the RawWeights site

Do this when the pages are done, in a separate branch of the site project, and review that pull request there.

1. Run the sync script, then copy the **contents of `site/careers/`** (not `includes/`, `fonts/` or `_private/`) to `careers/` in the site project root. Copy `data/jobs.json` too: it is public data. Do not copy anything from `data/profiles/`.
2. In the site project add the pages to `includes/page-meta.json`, add a nav entry in `includes/nav-data.json` **and** in the `NAV_DATA` object inside `includes/rw-nav.js`, then bump the `?v=` on every page's `rw-nav.js` script tag.
3. Regenerate the sitemap and search index (`generate-sitemap.py`, `npm run build`, and the `.md` companions with `generate-markdown.js` if the site keeps them).
4. Open `/careers/` locally and check the nav, footer, dark mode and the JSON paths.
5. Refresh `careers/data/jobs.json` whenever `hnradar fetch-hiring` produces a new month.

The full checklist, with the exact commands, is in `site/_private/MOVE_TO_SITE.md`.

## Building with an agent

`site/_private/BUILD_PROMPT.md` is a ready prompt for a design and front-end thread. It points to the data schemas and examples, the design guide and the templates, and states the constraints above.
