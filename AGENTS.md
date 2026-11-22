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
node --test site/tests/hn-fit.test.js site/tests/hn-github.test.js   # the site's JavaScript; pytest also runs it when node is installed
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

9. **A domain match is never presented as company ownership.** `domain_match` means a Show HN
   story's URL host is the requested domain or a subdomain of it. It does not show that the company
   posted the story: platform domains such as notion.so or supabase.com host other people's stories.
   Labels, command help, docstrings, field comments and docs must say that the story "links to" the
   domain, and must not call it "official", "the company's own" or "posted by the company".
   Applies to: `src/hnradar/showhn.py`, `src/hnradar/cli.py`, `src/hnradar/models.py` and any text
   that describes `showhn` output.
   Why: a false "official story" claim reads as confident as a true one, and the outreach draft built
   on it would repeat the claim to a person at that company.

10. **A failed upstream request is never returned as a complete result.** When a request to an
    outside service (HN Algolia, GitHub, a model server) fails, is rejected, is rate limited, times
    out or returns malformed data, the code must raise a clear error (the CLI then prints
    `Error: ...` and exits 1) or say in the result that data is missing. It must not catch the
    failure and return what it has as if it were complete. A deliberate fallback is allowed only when
    the output says it was used and the ticket asks for it. A cache entry that cannot be read is a
    miss: the data is fetched again and nothing is lost, so that case is fine.
    Applies to: `src/hnradar/sources/` and every command in `src/hnradar/cli.py` that calls it.
    Why: a swallowed failure looks exactly like a complete answer; a Show HN lookup that silently
    dropped its second search would hide the company's own story from the user.

## Site rules (JavaScript in `site/`)

The rules above are written for the Python code. The pages in `site/careers/` have their own, from the mistakes
reviews found in `check.html` (PR 59). `AGENTS.md` rules 1, 6, 9 and 10 apply to the pages too.

S1. **Text from a post, a comment or a person's GitHub profile reaches a page only as text.** Use `textContent` or
    `createTextNode` (the `H.el` helper does); never `innerHTML`, `insertAdjacentHTML`, `document.write` or `eval`.
    A link from data is used only after `H.safeUrl` or `H.hnItemUrl` accepts it. Why: the author controls that text.
    (Issue 47 asks for the same as a review standard.)

S2. **A page that handles a person's data keeps it in memory only, and says what it sends.** No `localStorage`,
    `sessionStorage`, cookie, IndexedDB or analytics script on such a page (`check.html` leaves `analytics.js` out).
    The only requests allowed are same-origin static files and the public GitHub API, and the page says so in words.
    Why: a username or a repo list is personal data; a privacy promise that code can break must be tested.

S3. **Limits and failures are named, never blank.** A rate limit, an unknown user, more repos than the cap, a
    timeout, an offline browser or a malformed response ends in a visible message that says what happened and what to
    do, and no partial result is shown as complete. Why: rule 6 and rule 10, for the browser.

S4. **Async work can be cancelled and cannot render late.** Every fetch or file read uses an `AbortController` or a
    generation token that Clear and a newer start both change, and the result is checked against it before it is
    drawn. Why: a result the person cleared came back after Clear (PR 59).

S5. **What is copied is what is shown.** Build the copied text from the same validated data the page renders, with
    the same citations. Why: the Copy button once dropped the comment links shown beside the claim (PR 59).

S6. **One idea, one implementation.** The match logic lives in `site/careers/hn-fit.js` and nowhere else on the
    site; Python (`fingerprint.py`, `match.py`) is the reference. A change to either side regenerates the shared
    cases (`site/tools/make_fit_golden.py`) in the same commit, and both test suites must pass. Why: two copies drift.

S7. **A fixture, an example and a sample are validated by the real code.** Anything shown to a person as an example
    must pass the Python models or the matcher through a test (`tests/test_check_example.py` does this).

S8. **State what is only checked by hand.** There is no browser test yet (issue 56): list the behaviours checked
    by hand in the PR description, and move them into tests when the harness exists.

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

## Before asking for review (checklist)

Run through this before every review, so the review finds new problems and not the ones we already know. Each item comes from a finding that earlier reviews raised.

1. **Ticket, line by line.** Map every acceptance criterion and edge case to a test or a written decision. If two lines of the ticket disagree, decide, edit the ticket, and say so in the PR; a stale ticket produces stale findings.
2. **Every input fails cleanly.** For each file, API response, cache entry and CLI argument the change reads, list the failures: missing, unreadable, not JSON, nested too deeply, wrong shape or types, empty, huge, a negative or out-of-range number. Each one ends in `Error: ...` and exit 1, never a traceback and never a half result.
3. **Ambiguity is an error, not a guess.** If more than one candidate fits (two files that differ only by case, several matching records), stop and say which; never pick the first.
4. **Identity is checked.** A name taken from the command line that selects a file must match what is inside the file, ignoring case where the source does.
5. **One source of truth.** Search the code for an existing regex, constant, alias table or helper before writing a new one. Names that must match across two systems (job stack versus GitHub topics, `Go` versus `golang`, `k8s`, `postgres`) go through one shared normaliser.
6. **Every fix has a test that fails without it.** A claim in the PR description ("fixed X") must point to its test. At-the-limit cases (exactly the cap) are tested, not only over and under.
7. **Ask what else a fix touches.** After each change, grep for other callers and other copies of the same idea before asking for the next review.
8. **Run it on real data** and read the output, not only the tests: the real output is where a missing `Go` or a wrong ranking shows up.
9. **State what the PR does not check** (skipped reviewers, untested paths, known limits) in the description.
