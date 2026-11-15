# AGENTS.md — HN Radar

## Project

HN Radar turns the HN "Who is Hiring" thread into cited warm intros:
GitHub profile → top 3 companies → their HN pulse → outreach draft.

Python 3.13, `uv`, typer, httpx, pydantic. Source in `src/hnradar/`, tests in `tests/`.
Generated data lives in `data/` (`data/jobs.json` is committed because the static site reads it;
`data/cache/` is git-ignored).

## Setup

```powershell
uv sync
```

## Verify (run before every PR)

```powershell
uv run pytest -q
uv run hnradar fetch-hiring --limit 20 --out data/cache/verify.json
```

Always pass `--out` for experiments. The default output is `data/jobs.json`, and a limited run
would overwrite the full committed file.

## Rules

1. **Every claim is cited.** Any pulse item or outreach sentence that states what users said MUST
   carry the HN `comment_id` it came from.
   Applies to: `src/hnradar/pulse/`, `src/hnradar/outreach/`.
   Why: the product promise is "arrive with a real fix". One invented complaint destroys trust and
   could embarrass the user in front of an employer.

2. **LLM output is schema-validated.** Parse model responses only through pydantic models; never
   regex free text.
   Applies to: any module calling Ollama.
   Why: small local models drift in format; validation turns silent garbage into a visible error.

3. **Network goes through `sources/`.** All HN/GitHub HTTP calls live in `src/hnradar/sources/`
   with on-disk caching. Tests never hit the network (mock with respx).
   Applies to: all of `src/` and `tests/`.
   Why: reproducible tests and not abusing free public APIs.

4. **Untrusted URLs only in the sandbox layer.** Never fetch a URL taken from a post, comment or
   user outside `src/hnradar/sandbox/`.
   Applies to: everything.
   Why: HN posts contain arbitrary links; later this layer becomes OpenSandbox.

5. **Minimal personal data.** Store only the GitHub username and derived public facts. Never
   scrape LinkedIn or store resumes.
   Applies to: everything.
   Why: privacy and terms of service.

6. **Limits fail loudly.** Any loop, page cap, retry cap, size limit or timeout must either finish
   or raise a clear error. Never return a truncated result as if it were complete.
   Applies to: all of `src/`.
   Why: a pagination cap once silently produced partial profiles; a partial result looks
   exactly like a complete one.

7. **Every change ships edge-case tests.** Before writing the tests, list the worst cases for the
   code you touched and add a test for each: empty input, exactly-at-the-limit input,
   over-the-limit input, upstream error (404, 403/429, malformed JSON), and two writers or
   callers touching the same file or cache key.
   Applies to: `tests/`, every PR.
   Why: tests written only from the ticket's happy path missed a pagination bug and a cache-write
   collision that review later found.

8. **Untrusted post text is cleaned before it reaches a terminal.** Text taken from an HN post,
   comment or GitHub profile (company, role, titles, descriptions) must have control characters
   and bidirectional or other invisible format characters removed before it is printed to the
   terminal. JSON printed to stdout is allowed only if it is serialized with `ensure_ascii=True`,
   so every non-ASCII control character is escaped; files may keep the raw text.
   Applies to: every `typer.echo` / `print` in `src/hnradar/` that outputs post or profile text.
   Why: the author of a post controls that text, and an escape sequence in it can rewrite or hide
   what the user sees; a review found this in the `fetch-hiring` table.

## Quality gate (definition of done)

A coding task is done only when all of these hold. Findings come from any reviewer (a person or an
automated review); each has a type (bug, rule violation, test gap, maintainability) and a severity.

1. **High and Medium findings are fixed.** Every valid High or Medium finding is fixed, including
   Medium bugs. A fix comes with a test or a reproduction that fails before the fix and passes after.
2. **Low findings are reported, not silently fixed or dropped.** Leave Low findings (including Low
   rule violations) unchanged and list each one in the final report for the human to decide.
3. **Disagreements need evidence.** To call a finding invalid, cite the code, a test or a
   reproduction. Only the human dismisses or closes a finding; an agent never does.
4. **Checks pass.** `uv run pytest -q` and the smoke check under "Verify" pass in this run, and
   the edge cases required by rule 7 are tested.
5. **The review covers the latest commit.** After fixes, review again. For a pull request, the
   latest completed review must be of the current head commit; a review of an older commit does not
   count, and a review still running does not count.
6. **Stop when stalled.** If a High or Medium finding is still open after 3 fix-and-review rounds,
   or the same finding comes back unchanged, stop.
7. **Final report.** State each finding with its type, severity, decision and evidence; the checks
   run and their results; the reviewed commit and the current head; and the Low findings flagged.
   If anything at or above Medium is unresolved, a check failed or was not run, or the review is
   incomplete or stale, report **NOT DONE** with the blocker and what you need from the human.
   A run that cannot review (for example an autonomous worker that only opens a PR) reports
   "NOT DONE: review pending" with the PR link; it never reports done.
