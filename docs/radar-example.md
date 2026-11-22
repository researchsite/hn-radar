# Example: `hnradar radar researchsite`

A real run on 2026-11-22 for the public GitHub account `researchsite` (this project's own account), against the committed `data/jobs.json` (233 posts of the November 2026 thread). Only public data is shown. Progress goes to stderr, the result to stdout.

This account matches no company with a Show HN story that links to the domain of its job link: the matches link to a job board or an application system (`ycombinator.com`, `ashbyhq.com`, `linkedin.com`) or to a domain with no Show HN story. The run goes down the best matches (`--limit` 3 drafts, `--depth` 10 matches), finds no draft in all 10, and says so: every company is listed with its fit reasons and the reason it has no pulse or draft, and no draft is invented (AGENTS.md rules 1 and 9). For a user whose matches include a Show HN thread with a cited problem, the company also gets a "Users reported" line with its comment ids and a 5-line draft with its evidence.

```
[1/5] fingerprint: ok (0.07s) - 44 public repos
[2/5] match: ok (0.01s) - 10 of 233 jobs
[3/5] showhn: skipped (0.26s) - 0 of 10 companies have a Show HN that links to their domain
[4/5] pulse: skipped (0.0s) - 0 of 0 threads have a cited problem
[5/5] draft: skipped (0.0s) - 0 of 0 drafts written
Radar for researchsite: 10 companies checked, 0 drafts (partial).

1. MixRank (YC S11) | Software Engineers | fit 65/100
   - Your Python work matches this job's stack (the post says remote)
   - Your JavaScript work matches this job's stack
   - Your TypeScript work matches this job's stack
   - Your repo ContextCal is written in Python
   https://www.ycombinator.com/companies/mixrank/jobs/Fnwsojk-j
   Note: platform domain (ycombinator.com); a story that links to it may belong to someone else

2. Alternative Payments | Senior Software Engineer, Alti (AI agents) | fit 45/100
   - Your TypeScript work matches this job's stack (the post says remote)
   - Your Python work matches this job's stack
   - Your repo ContextCal is written in Python
   https://jobs.ashbyhq.com/alternativepayments/76a7a829-d08d-4a0f-9a88...
   Note: platform domain (jobs.ashbyhq.com); a story that links to it may belong to someone else

3. RAIDS AI | Senior Technical Project Manager (Full Stack) | fit 45/100
   - Your Python work matches this job's stack (the post says remote)
   - Your TypeScript work matches this job's stack
   - Your repo ContextCal is written in Python
   https://www.linkedin.com/jobs/view/4483409995/
   Note: platform domain (linkedin.com); a story that links to it may belong to someone else

(companies 4 to 10 left out here)

10 of 10 companies have no draft: see each company's note.

Only the 10 best matches were checked (depth 10); lower-ranked matches were not.
```

The full result as JSON: `uv run hnradar radar researchsite --json` (or `--out radar.json`). It is about one person: keep that file private.
