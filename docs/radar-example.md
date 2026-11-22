# Example: `hnradar radar researchsite`

A real run on 2026-11-21 for the public GitHub account `researchsite` (this project's own account), against the committed `data/jobs.json` (231 posts of the November 2026 thread). Only public data is shown. Progress goes to stderr, the result to stdout.

This account matches no company with a Show HN story that links to the domain of its job link: the three best matches link to a job board or an application system (`ycombinator.com`, `linkedin.com`) or to a domain with no Show HN story. So the run is `partial`: every company is listed with its fit reasons and the reason it has no pulse or draft, and no draft is invented (AGENTS.md rules 1 and 9). For a user whose top matches have a Show HN thread with a cited problem, each company also gets a "Users reported" line with its comment ids and a 5-line draft with its evidence.

```
[1/5] fingerprint: ok (0.0s) - 44 public repos
[2/5] match: ok (0.01s) - 3 of 231 jobs
[3/5] showhn: skipped (0.05s) - 0 of 3 companies have a Show HN that links to their domain
[4/5] pulse: skipped (0.0s) - 0 of 0 threads have a cited problem
[5/5] draft: skipped (0.0s) - 0 of 0 drafts written
Radar for researchsite: top 3 companies (partial).

1. MixRank (YC S11) | Software Engineers | fit 65/100
   - Your Python work matches this job's stack (the post says remote)
   - Your JavaScript work matches this job's stack
   - Your TypeScript work matches this job's stack
   - Your repo ContextCal is written in Python
   https://www.ycombinator.com/companies/mixrank/jobs/Fnwsojk-j
   Note: platform domain (ycombinator.com); a story that links to it may belong to someone else

2. RAIDS AI | Senior Technical Project Manager (Full Stack) | fit 45/100
   - Your Python work matches this job's stack (the post says remote)
   - Your TypeScript work matches this job's stack
   - Your repo ContextCal is written in Python
   https://www.linkedin.com/jobs/view/4483409995/
   Note: platform domain (linkedin.com); a story that links to it may belong to someone else

3. OpenTrain AI | Backend Engineer, AI Coding Task Design | fit 45/100
   - Your Python work matches this job's stack (the post says remote)
   - Your TypeScript work matches this job's stack
   - Your repo ContextCal is written in Python
   https://app.opentrain.ai/api/out/...
   Note: no Show HN links to app.opentrain.ai

3 of 3 companies have no draft: see each company's note.
```

The full result as JSON: `uv run hnradar radar researchsite --json` (or `--out radar.json`). It is about one person: keep that file private.
