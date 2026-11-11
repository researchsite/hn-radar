\# AGENTS.md — HN Radar



\## Project

HN Radar turns HN "Who is Hiring" into cited warm intros: GitHub profile → top 3 companies → their HN pulse → outreach draft.



\## Setup

uv sync



\## Verify (run before every PR)

uv run pytest -q

uv run hnradar fetch-hiring --limit 20



\## Rules

1\. \*\*Every claim is cited.\*\* Any pulse item or outreach sentence that states what users said MUST carry the HN comment\_id it came from.

&#x20;  Applies to: src/hnradar/pulse/, src/hnradar/outreach/. Why: the product promise is "arrive with a real fix" — one invented complaint destroys trust and could embarrass the user in front of an employer.

2\. \*\*LLM output is schema-validated.\*\* Parse model responses only through pydantic models; never regex free text.

&#x20;  Applies to: any module calling Ollama. Why: small local models drift in format; validation turns silent garbage into a visible error.

3\. \*\*Network goes through sources/.\*\* All HN/GitHub HTTP calls live in src/hnradar/sources/ with caching and polite rate limits; tests never hit the network.

&#x20;  Applies to: all of src/ and tests/. Why: reproducible tests and not abusing free public APIs.

4\. \*\*Untrusted URLs only in the sandbox layer.\*\* Never fetch a URL taken from a post/comment/user outside src/hnradar/sandbox/.

&#x20;  Applies to: everything. Why: HN posts contain arbitrary links; later this layer becomes OpenSandbox.

5\. \*\*Minimal personal data.\*\* Store only the GitHub username and derived public facts. Never scrape LinkedIn or store resumes.

&#x20;  Applies to: everything. Why: privacy + ToS.



