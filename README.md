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

### fetch-hiring

```
uv run hnradar fetch-hiring                 # whole thread, writes data/jobs.json, prints a table
uv run hnradar fetch-hiring --limit 20      # first 20 posts only (quick test)
uv run hnradar fetch-hiring --out other.json
uv run hnradar fetch-hiring --json          # print JSON instead of a table (writes a file only with --out)
```

Run it once a month, or after a parser change, to refresh `data/jobs.json`. HN responses are cached in
`data/cache/`.

### showhn

```
uv run hnradar showhn Acme
uv run hnradar showhn Acme --domain acme.com   # story URLs on that domain rank first
uv run hnradar showhn Acme --json
```

Prints a "low confidence" note when no story URL matches the domain.

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
