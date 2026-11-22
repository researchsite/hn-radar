# HN Radar

Turns the monthly Hacker News "Ask HN: Who is hiring?" thread into structured jobs, and matches them to a
GitHub user's public profile. It reads public data only and runs on your machine.

## Setup

Needs Python 3.13+ and [uv](https://docs.astral.sh/uv/).

```
uv sync
uv run hnradar --help
```

All commands below are run as `uv run hnradar <command>` from the repository root. Files are written under
`data/` (git-ignored where they contain per-user data).

Optional: set `GITHUB_TOKEN` (a token with no scopes) to raise GitHub's rate limit for `fingerprint`. Never
commit it.

## Commands

| Command | What it does | Writes |
|---|---|---|
| `fetch-hiring` | Fetches the latest "Who is hiring?" thread and parses each top-level post into a job (company, role, location, remote, stack, link). | `data/jobs.json` |
| `showhn <company>` | Finds up to 5 candidate Show HN stories for a company, domain matches first. | nothing |
| `fingerprint <user>` | Summarises a public GitHub user's languages, topics and notable repos. | `data/profiles/<user>.json` |
| `match <user>` | Scores the jobs against the user's profile and shows the top 3, each with reasons. | nothing |
| `draft <user>` | Writes a 5-line outreach note to the best-matched company, citing one user-reported problem. | nothing |

### fetch-hiring

```
uv run hnradar fetch-hiring                 # whole thread, writes data/jobs.json, prints a table
uv run hnradar fetch-hiring --limit 20      # first 20 posts only (quick test)
uv run hnradar fetch-hiring --out other.json
uv run hnradar fetch-hiring --json          # print JSON instead of a table (writes a file only with --out)
```

Run it once a month, or after a parser change, to refresh `data/jobs.json`. HN responses are cached in
`data/cache/`. If the cache cannot be written, the command still finishes and prints one
warning on stderr; a failed HN request is still an error.

### showhn

```
uv run hnradar showhn Acme
uv run hnradar showhn Acme --domain acme.com   # story URLs on that domain rank first
uv run hnradar showhn Acme --json
```

Prints a "low confidence" note when no story URL matches the domain. It also flags a platform domain
(github.com, notion.so, a job board, a form host): stories that link to it may belong to someone else, so
the result has `platform_domain: true` and `low_confidence: true`.

The check errs on the side of flagging. A company's own page on a platform (`acme.github.io`,
`acme.notion.site`) is flagged, and so is a company that is itself a listed platform (`substack.com`). The
list in `showhn.py` is a start: a platform that is not listed is treated as an ordinary domain.

### fingerprint

```
uv run hnradar fingerprint octocat
uv run hnradar fingerprint octocat --out-dir data/profiles
```

A user with no public repos gets an empty profile. Profiles are per-user data: keep them out of git and do
not publish them.

### match

```
uv run hnradar match octocat                  # needs data/jobs.json and data/profiles/octocat.json
uv run hnradar match octocat --json
uv run hnradar match octocat --jobs data/jobs.json --profiles-dir data/profiles
```

Run `fetch-hiring` and `fingerprint` first. Scoring is deterministic: language matches 20 points each, topic
matches 10, a remote bonus of 5 only when something else matched, capped at 100. Ties go to the newer post.
The command exits with an error if the profile is missing, malformed, belongs to another user, or if two
profile files differ only by letter case.

### draft

```
uv run hnradar draft octo                          # needs a profile and data/jobs.json; uses a saved pulse if there is one
uv run hnradar draft octo --model qwen2.5:7b --pulse-dir data/pulse
```

Tries the 10 best matches in order, and prints which line cites which comment. A match is used only if its job link has a domain that is not a platform
host, a Show HN links to that domain, and the thread has a `pulse` "bug" whose cited comments are all in the
thread. If none qualifies it prints `No cited problem found` with the reason per company and writes no draft
(exit 0). The note says "a Show HN that links to ..." because a link does not show the company posted it.
Under the note it prints the repo description, each cited comment (cleaned, cut to 300 characters) and its HN
link. Read them before you send anything: a small model can say more about the repo than its description
states. Nothing is sent. A careers subdomain (`jobs.acme.com`) is searched as given and may find no story. A company's own page on a
platform (`acme.github.io`) and a company that is itself a listed platform are skipped on purpose: the safe
side, not a solved case. If the 10 best matches give no draft, it says lower-ranked matches were not checked.

### radar

```
uv run hnradar radar octo                          # username only: fingerprint, match, Show HN, pulse, draft
uv run hnradar radar octo --json --out radar.json  # JSON to stdout and to a file (about one person: keep it private)
```

The end-to-end run, using the same functions as the single commands. It prints one progress line per step on stderr, then the top 3 companies: fit reasons, the Show HN story that links to the job link's domain, the problem users reported with its comment ids, and a 5-line draft with its evidence. A company with no such story, no cited problem or no matched repo is listed with the reason and gets no draft; the run is then `partial`. A user with no public repos or no strong match gets a friendly message (exit 0). A step that fails (GitHub, HN, the model, an unreadable file) stops the run with `Error: radar stopped at step '<name>': ...` and exit 1; no companies are shown, and `--out` records `status: failed` with the step. A saved pulse (`data/pulse/<story_id>.json`) is used instead of asking the model. A real run is in [docs/radar-example.md](docs/radar-example.md). Nothing is sent.

## Typical session

```
uv run hnradar fetch-hiring
uv run hnradar fingerprint octocat
uv run hnradar match octocat
```

## Tests

```
uv run pytest
```

## Contributing

See [AGENTS.md](AGENTS.md) for the quality gate and review rules.
