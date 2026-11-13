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
