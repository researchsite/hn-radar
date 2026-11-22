# HN Radar site

The web pages for HN Radar. Plain HTML, CSS and JavaScript, no build step and no server. This folder is kept separate from the hackathon work on purpose: it is built here so every change goes through the normal pull request and review flow, then it is **copied into the RawWeights site** (the pages in `/careers/`; the CSS and JS go to the site's `/includes/`, data, pictures and videos to its shared `/data/` and `/images/` folders; the copy script does the rewiring, see below).

Only public build-time data is shown (`data/jobs.json`, later pulse and Show HN data). Per-user data (GitHub profile, match, draft) is never published or stored here: see [AGENTS.md](../AGENTS.md) rule 5.

## Layout

```
site/
  README.md              this file (committed)
  careers/               THE deployable folder
    index.html           job explorer (search, filters, detail view)
    insights.html        top technologies, remote share, what posts leave out
    company.html         without ?job=: directory of every company with a teaser and a search box;
                         with ?job=<comment_id>: that company's hiring post, Show HN posts, pulse (issue 41)
    journey.html         placeholder: coming soon (issue 31)
    check.html           Check yours: type a GitHub username to see the fit in the browser, or load the radar.json that `hnradar radar USER --out radar.json` made on your own computer (issues 31, 56)
    hn.css, hn.js        shared styles and helpers (text cleaning, safe links, designed states); hn.js holds PATHS
    hn-fit.js            profile and match logic for the browser: a port of fingerprint.py and match.py (no DOM, no network); the Python code is the reference (issue 56)
    hn-github.js         the one request the page makes for a person: their public repos from api.github.com (no storage; fetch is passed in, so Node tests drive every failure)
    data/jobs.json       dev copy of ../../data/jobs.json (git-ignored, refreshed by the sync script)
    images/, videos/     pictures and videos used by the pages (committed; video names start with careers-)
    pagefind/            careers-only search index, built by tools/build_careers_index.py (git-ignored)
  tools/                 build_careers_index.py: builds that index (tested in tests/test_careers_index.py)
                         make_fit_golden.py: makes the shared test cases for hn-fit.js from the Python reference
  tests/                 hn-fit.test.js, hn-github.test.js: Node tests (node --test site/tests/*.test.js); also run by uv run pytest when node is installed
  includes/, fonts/      dev copies of the live site's design system, nav, footer and fonts (git-ignored)
  favicon.svg, site.webmanifest, images/favicon-*.png   dev copies of the site's icons (git-ignored)
  _private/              local-only notes, templates, prompts and scripts (git-ignored; never committed)
```

Pages use the **same relative paths as on the live site** for the site's own files (`../includes/brand.css`, `../includes/rw-nav.js`, `../includes/rw-footer.js`). That is why `includes/` and `fonts/` sit next to `careers/` here: the page works unchanged in both places. Nothing from them is tracked in git.

### Where data, pictures and videos live: one variable
Pages never write a data, picture or video path themselves. They use `HN.paths` from `careers/hn.js`:

```js
var PATHS = { data: './data/', images: './images/', videos: './videos/', search: './pagefind/' };   // dev: inside careers/
```
```js
H.loadJSON(H.paths.data + 'jobs.json', ...)        // a picture: H.paths.images + 'diagram.png'
```

On the live site these are shared folders at the site root, so the move changes that one line to
`{ data: '../data/', images: '../images/careers/', videos: '../images/videos/', search: './pagefind/' }` and moves the files to match.
`site/_private/publish-to-site.ps1` does both (see "Moving"). To change the live layout later, change that line (and the table at the top of the script), nothing else.

| Kind | Dev (here) | Live site |
|---|---|---|
| Pages | `careers/` | `careers/` |
| `hn.css`, `hn.js`, `hn-fit.js`, `hn-github.js` | `careers/` | `includes/rw-hn.css`, `includes/rw-hn.js`, `includes/rw-hn-fit.js`, `includes/rw-hn-github.js` |
| Data (JSON) | `careers/data/` | `data/` |
| Pictures | `careers/images/` | `images/careers/` |
| Videos | `careers/videos/careers-<name>.mp4` | `images/videos/careers-<name>.mp4` |
| Search index (careers only) | `careers/pagefind/` | `careers/pagefind/` (replaced on every publish) |

The CSS and JS are loaded by plain `<link>` and `<script>` tags, which cannot read a variable. So in the dev layout they stay in `careers/` (the folder is self-contained and nothing of ours sits in the git-ignored `includes/`), and the copy script moves them to the site's `includes/` as `rw-hn.css` / `rw-hn.js` and rewrites those two tags in the copied pages (`./hn.css` becomes `../includes/rw-hn.css`). It stops with an error if a page has no such tag.

### Company page: data it reads and how to test it
`company.html` with no `?job=` is the **company directory**: every post from `jobs.json` as a card (company, role, place, technologies, a teaser taken from the text after the post's first line), the best-documented first (a score of stated facts: technologies, role, place, remote, job link, post length; a sort option gives A to Z), 50 at a time, with a search box for name, role, place or technology (`?q=` presets it). It is not meant to repeat the job explorer: the explorer filters posts, this page is the entry to one company. Picking a card opens `company.html?job=<comment_id>`; an invalid or unknown id shows a note above the directory instead of a dead end.

`company.html?job=<comment_id>` reads three files, all through `H.paths.data` (so they move with the one variable):

| File | What | Produced by | Today |
|---|---|---|---|
| `jobs.json` | the hiring post | `hnradar fetch-hiring` | exists |
| `showhn/<comment_id>.json` | Show HN candidates for that job (`hnradar showhn --json` shape, `built/showhn_result.schema.json`) | not saved anywhere yet (gap 3 in issue 32) | missing: the page shows "not produced yet" |
| `pulse/<story_id>.json` | pros, bugs and asks for one Show HN thread (`planned/pulse.schema.json`) | `hnradar pulse` (issue 13, not built) | missing: the page shows "not produced yet" |

Jobs, Insights and Companies show "Job posts from Hacker News, data last updated: <date>", taken from the `Last-Modified` header of `jobs.json` (the day the file was copied after a crawl); with no header the note is hidden.

Every state is designed: missing file ("not produced yet"), invalid file, no Show HN post (`no_show_hn`), low confidence, "5 of 813" when truncated, `not_enough_signal`. A pulse point without a valid HN comment id is never shown, and the page says how many were hidden (AGENTS.md rule 1).

Real Show HN data: run `uv run python site/tools/build_showhn_data.py` (see the build table below). To test pulse or odd cases with fixtures, copy files from `_private/schema/examples/` into `careers/data/pulse/` (or `showhn/`). `publish-to-site.ps1` publishes only an allowlist: `jobs.json`, `showhn-index.json` and `showhn/<number>.json`. `pulse/<number>.json` is published too, so keep fixtures out of `careers/data/pulse/` and `careers/data/showhn/` (keep them elsewhere, or delete them before publishing).

### Building every data file, in order
Run from the repo root, in this order. Steps 2 to 5 read the dev copy `site/careers/data/jobs.json`, so the sync comes right after the fetch.

| # | Data | Command | Output | Notes |
|---|---|---|---|---|
| 1 | Hiring posts | `uv run hnradar fetch-hiring` | `data/jobs.json` | monthly; network |
| 2 | Dev copies | `powershell -File site/_private/sync-dev-assets.ps1` | `site/careers/data/jobs.json` and the site's includes and fonts | after every step 1 |
| 3 | Show HN, one file per job | `uv run python site/tools/build_showhn_data.py` | `site/careers/data/showhn/<comment_id>.json`, `showhn-index.json` | about 2 minutes, network. Skips jobs that already have a current file; `--limit 20` to try, `--refresh` to redo all. A failed lookup is listed and writes nothing |
| 4 | Pulse, one file per Show HN story | `uv run python site/tools/build_pulse_data.py` | `site/careers/data/pulse/<story_id>.json` | needs a local Ollama model (default `qwen2.5:7b`; `--model qwen2.5:14b` is slower and better) and the network. Picks, for each company, its 2 most commented Show HN stories that link to the company's own domain (`--per-company`, `--name-matches` adds name-only matches), summarises each once, skips stories that already have a file (`--refresh` redoes them, `--limit 5` to try). Several hiring posts of one company count as one company. Only stories saved in the Show HN files are considered (at most 5 per company), so a busy story ranked lower is not summarised. A thread with too few comments writes nothing (a refresh removes the old file); a failed story is listed; it stops after 3 failures in a row. One story by hand: `uv run hnradar pulse <id> --out site/careers/data/pulse/<id>.json` |
| 5 | Show HN and pulse of every job in one file, for the browser match | `uv run python site/tools/build_signals_data.py` | `site/careers/data/signals.json` | after steps 3 and 4 (and every refresh of them); no model, no search: it reads the saved files and fetches the threads of stories that have a pulse (cached). Fails without writing if a saved file is broken or a thread cannot be read. A job with no current Show HN file gets no entry (the page says "not checked") |
| 6 | Careers search index | `uv run python site/tools/build_careers_index.py` | `site/careers/pagefind/` | after every step 2 (the publish script refuses an older index) |
| 7 | Publish to the live site | `powershell -File site/_private/publish-to-site.ps1` (dry run), then `-Apply` | the site project | see "Moving the folder" below |

`signals.json` is what the username box on `check.html` reads for Show HN threads and what users said (one file for every job, so the request never names a company; it is rebuilt after the Show HN and pulse files, and `hn-fit.js` refuses a file that does not follow the same rules as `build_signals_data.py`, which the golden cases check). The search index covers hiring posts only. Show HN and pulse files are read per company by `company.html`; the directory uses `showhn-index.json` only to put companies with a Show HN post first. The generated folders are git-ignored; keep test fixtures out of `careers/data/showhn/` and `careers/data/pulse/` because the publish script copies everything there that looks like a real file.

### Careers search: its own Pagefind index
The job explorer searches the full text of every post with [Pagefind](https://pagefind.app), the same library and version (1.6.0) as the RawWeights site search, but with a **separate index that holds careers jobs only**.

```powershell
uv run python site/tools/build_careers_index.py     # after every refresh of careers/data/jobs.json
```

- It writes one small page per job to a temporary folder, runs `npx pagefind@1.6.0` on it (the same CLI as the site's `npm run build`; needs Node) and writes the index to `careers/pagefind/`. The old index is replaced only after the new one is complete and has exactly one page per job.
- **No conflict with the site search:** the index lives in `careers/pagefind/`, the site's own is `/pagefind/`. They share no files and no state. The site's `pagefind.yml` is not changed (do not add `careers/*.html` to its glob), and the careers pages are not part of the site index.
- The page (`HN.search` in `hn.js`) loads `careers/pagefind/pagefind.js` with the JS API and reads **only the comment id** of each result: no excerpt or other index text is ever put in the page.
- If the index cannot be loaded or queried, the box falls back to the old match on company, role, location and technologies and says so on the page.
- Pagefind matches word starts (`rus` finds `rust`), not letters inside a word (the old box did). Several words must all appear.
- `publish-to-site.ps1` refuses to publish an index that is older than `careers/data/jobs.json`.

### Check yours: how to try it without committing anything
`check.html` has two inputs. **A username**: the browser asks `api.github.com` for that user's public repos (`hn-github.js`, no cookies, no referrer, nothing stored), matches them with `jobs.json` (`hn-fit.js`) and adds, for each match, the Show HN thread that links to the company's domain and the problem users reported with its comments, from `signals.json`. It shows no draft (a model is needed) and says so on each card; if `signals.json` cannot be loaded the fit is still shown with a note, and a job missing from the file reads "not checked", never "no Show HN". The requests the page makes are the two static data files (the same for every visitor) and that one GitHub call; GitHub allows 60 requests an hour per network address, and the page says so when the limit is used up. **A result file** made by `hnradar radar`: read with `FileReader` in the browser tab, no request carries it. The page deliberately does **not** load `includes/analytics.js` (Microsoft Clarity records what is on screen). Every text from the file is written with `textContent`; a draft is shown only if its claim line cites a comment; a point about what users said with no valid comment id is dropped with a note; a `failed` result shows only the failed step.

```powershell
powershell -File site/_private/sync-dev-assets.ps1        # once: dev copies of the site's includes and fonts
python -m http.server 8000 --directory site               # then open http://localhost:8000/careers/check.html
uv run hnradar radar YOUR-GITHUB-USERNAME --out radar.json   # your own result; then drop radar.json on the page
```

The **Try an example** button shows a made-up developer (`example-developer`) matched against three real jobs, with the real SadServers Show HN comments. The data sits in the page as `<script type="application/json" id="ck-example-data">` (no request), and a warning note above the result says it is an example. The draft in it was written by hand, never by a model and never sent; it is the only draft in the repository.

Nothing needs to be committed: the page is a plain file under `site/`, the server only serves your working copy. For every state without running the model, use the local, git-ignored samples in `site/_private/samples/` (`SYNTHETIC` ones come from the test fakes, not from real people): three drafts, partial, no matches, failed, hostile text with invalid parts, and a file that is not a result.

### The site's own icon and manifest files
The shared nav script (`includes/rw-nav.js`) adds the favicon, `apple-touch-icon` and `site.webmanifest` links to the `<head>` of every page. The pages here do not reference them and do not need them. They are copied into the dev folder only so the preview server does not log 404s for them.

## Run it locally

From the repo root:

```powershell
powershell -File site/_private/sync-dev-assets.ps1    # copy includes, fonts, icons and data/jobs.json (first time, and after data or design changes)
python -m http.server 8000 --directory site
```

Open http://localhost:8000/careers/. Use a server, not a `file://` URL, because the page loads JSON with `fetch`.

## Rules for every page

1. **Design:** use the site's design tokens from `includes/brand.css`: the `--rw-*` variables (`--rw-text`, `--rw-text-muted`, `--rw-card`, `--rw-border`, `--rw-orange`, `--rw-link`, `--rw-font-display`, `--rw-r-md` ...). The design guide mentions `--md-sys-color-*` names, but the live `brand.css` defines only `--rw-*`, the two fonts already loaded there (Space Grotesk for headings, Inter for body) and the `rw-` class prefix (`rw-hn-` for new HN Radar components). Never hard-code colours. Light and dark themes both work: keep the inline theme script from the template at the top of `<head>`.
2. **Start from a template:** copy `_private/templates/base.html` for a plain page, `tool.html` for an interactive one, and read `_private/reference/unslop.html` for a page that loads a JSON file (the pattern is the same here, but the path comes from `H.paths.data`). `_private/templates/components.html` shows every UI component.
3. **Untrusted text:** post text comes from Hacker News and can contain anything. Write it with `textContent` only, never `innerHTML`; remove control and bidirectional characters first (see `clean()` in `careers/hn.js`).
4. **Citations:** any statement about what users said shows its HN comment link. No uncited claims.
5. **Never a blank screen:** a missing or invalid JSON file shows a visible message; an empty list shows an empty state.
6. **Statuses are designed states, not errors:** `low_confidence`, `truncated`, `no_strong_matches` and the others in the schemas (`_private/schema/`).
7. **Accessible and small:** keyboard friendly, visible focus, contrast AA, works from 360 px wide, no frameworks unless a page truly needs one.
8. **Page area:** wrap the page content in a `<div id="content" class="rw-hn-wrap">`, not `<main>`. The page owns this area; the nav and footer are the site's components and must not be touched by page styles.
9. **Paths:** relative only. The site's own files via `../includes/`; data, pictures and videos only through `H.paths` (never a literal path in a page). No absolute `/careers/...` paths, so the folder can be moved or renamed.

## Adding a page

1. Copy a template into `site/careers/` (for example `careers/insights.html`).
2. Replace the SEO placeholders (title, description, canonical `https://rawweights.com/careers/<page>.html`, Open Graph).
3. Keep the nav and footer blocks and the theme script as they are.
4. Read data with `H.paths.data + '<file>.json'` (add the file to the sync script if it is new); use `H.paths.images` / `H.paths.videos` for media. Shared CSS or JS goes in `hn.css` / `hn.js`.
5. Open the page locally in light and dark mode and at 360 px wide.

## Moving the folder into the RawWeights site

Do this when the pages are done, in a separate branch of the site project, and review that pull request there. The site project deploys from its **`gh-pages`** branch (there is no `main`): branch from it and open the pull request against it.

1. Run the sync script, then `powershell -File site/_private/publish-to-site.ps1` (a dry run that lists the files) and again with `-Apply`. It copies the pages to `careers/` (with the two tags rewritten), `hn.css`, `hn.js`, `hn-fit.js` and `hn-github.js` to `includes/rw-hn.css`, `includes/rw-hn.js`, `includes/rw-hn-fit.js` and `includes/rw-hn-github.js`, `careers/data/*` to the site's `data/`, `careers/images/*` to `images/careers/` and `careers/videos/*` to `images/videos/` and the search index `careers/pagefind/` to the same place (replacing it), and rewrites the `PATHS` line in the copied `rw-hn.js` to those places. Nothing else is copied: not the dev copies of includes and fonts, not `data/profiles/`. It also lists leftovers from the earlier layout (`careers/data/`, `careers/hn.css`, `careers/hn.js` in the site project) to delete there.
   By hand instead: copy the pages; copy `hn.css`, `hn.js`, `hn-fit.js` and `hn-github.js` to `includes/rw-hn.css`, `includes/rw-hn.js`, `includes/rw-hn-fit.js` and `includes/rw-hn-github.js` and change the `./hn*.css` and `./hn*.js` tags in each page that uses them (`check.html` loads all four scripts; every other page loads `hn.css` and `hn.js`); move the data, pictures and videos as in the table above; set `PATHS` in `rw-hn.js` to `{ data: '../data/', images: '../images/careers/', videos: '../images/videos/', search: './pagefind/' }`.
2. Registering the pages (nav entry, `?v=` bump, `sitemap.xml`, `llms.txt`) is done by the site's own job: do not do it by hand here. The site's `pagefind.yml` and `pagefind/` are not touched either: careers has its own index (see above).
3. Do not `git add -A` in the site project (it has unrelated daily changes); add explicit paths.
4. Open `/careers/` locally and check the nav, footer, dark mode, search and the JSON paths.
5. What changes when the company page goes live: `company.html` is copied like the other pages and needs no edit, but (a) `publish-to-site.ps1` publishes `data/` only from its allowlist, so only `jobs.json`, `showhn-index.json`, `signals.json`, `showhn/<number>.json` and `pulse/<number>.json` go out (never schema examples: delete test fixtures from those folders first); (b) its `<meta name="robots" content="noindex">` should be removed when it is ready to be found, and the page added to the site's sitemap by the site's own job; (c) the "data last updated" date comes from the `Last-Modified` header: on GitHub Pages that is the deploy day of `data/jobs.json`, so deploy it on the day of the crawl or the date will be later than the crawl.
6. Monthly, in this order (the table in "Building every data file, in order" has the commands): `hnradar fetch-hiring`, the sync script, `build_showhn_data.py`, `build_pulse_data.py`, `build_signals_data.py`, `build_careers_index.py`, then the publish script. `data/jobs.json`, the search index and `signals.json` always travel together: a `signals.json` older than `jobs.json` leaves new jobs marked "not checked" and shows old signals for the rest. The allowlist that decides which data files go out lives in the local, git-ignored `site/_private/publish-to-site.ps1` (it must list `signals.json`); the dry run lists every file it would copy, so check that `data\signals.json` is in it.

The full checklist, with the exact commands and the open decisions (placeholder pages, nav placement, which account pushes), is in `site/_private/MOVE_TO_SITE.md`.

## Building with an agent

`site/_private/BUILD_PROMPT.md` is a ready prompt for a design and front-end thread. It points to the data schemas and examples, the design guide and the templates, and states the constraints above.

### Match logic in the browser (`hn-fit.js`)
`hn-fit.js` turns a list of public repos into a profile and ranks jobs the same way `hnradar match` does (issue 56). It has no DOM, network or storage code, and a test fails if it gains any. The Python code (`fingerprint.py`, `match.py`) is the reference: `site/tools/make_fit_golden.py` makes `tests/fixtures/fit_golden.json` from it (32 cases: edge cases and real behaviours such as aliases, ties and the 100 cap), `tests/test_fit_golden.py` fails when that file is out of date, and `site/tests/hn-fit.test.js` fails when the JavaScript gives a different answer. Change either side, run `uv run python site/tools/make_fit_golden.py`, and commit both. The rules for site code are in AGENTS.md ("Site rules").
