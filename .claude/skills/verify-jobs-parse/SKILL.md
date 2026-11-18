---
name: verify-jobs-parse
description: Verify a change to the HN Radar parser or fetch-hiring on the live "Who is hiring?" thread. Use after editing src/hnradar/parse.py, closed.py or the fetch-hiring command, or when a ticket says posts must be dropped, kept or parsed differently. Runs fetch-hiring into a scratch file, compares it with a baseline by comment_id, checks the ids that must go and must stay, and saves a report. Do not use for showhn, match or fingerprint.
---

# Verify a parser change on real data

Tests use made-up posts. This check runs the changed code on the real thread and proves, with ids, what changed. A passing test suite is not a pass for this skill.

## Inputs (ask the user if any is missing)
1. **label**: short name for the evidence files, for example `45` for ticket #45. It must match `^[A-Za-z0-9][A-Za-z0-9-]{0,39}$` (letters, digits, dashes; no slash, dot, space or quote). Stop and ask for another label if it does not, or if `data/cache/verify-<label>.json` or `data/cache/verify-<label>-report.json` already exists: a reused label would overwrite earlier evidence.
2. **baseline**: path to the jobs file from before the change, for example `data/jobs.json` or a saved copy. It must exist.
3. **must-exclude ids**: comment_ids that the change must remove (may be empty if the ticket removes nothing). Digits only; stop if an id is not a plain integer.
4. **must-keep ids**: real jobs that look similar and must still be present (may be empty). Digits only.
5. Context: the ticket or PR that says what should change.

## Stop and ask the user, do not guess, when
- the baseline file does not exist, or it is not a JSON list, or the compare output says `LOOKS LIKE ANOTHER THREAD` (the baseline is from another month: ask for the right one);
- both id lists are empty and the ticket says posts must be dropped or kept (the check would prove nothing);
- `uv run pytest -q` fails, or `fetch-hiring` exits non-zero (print the error, make no retry with other flags);
- `data/jobs.json` changed during the run;
- the compare output says `DROPPED BY THE CODE` or `new jobs not in the thread` with any id, or lists duplicate ids (the script exits 1). Exit code 2 means bad input; show the `Error:` line and ask. The thread keeps changing, so removed ids listed under `gone from the thread` are normal and need no decision.

## Steps (run from the repository root, in this order)
1. Record the context: `git rev-parse --short HEAD` and `git status --short`. Note the commit.
2. Run `uv run pytest -q`. Record the pass count.
3. Record the protected file: `uv run python -c "import hashlib,pathlib;p=pathlib.Path('data/jobs.json');print(hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else 'absent')"`.
4. Fetch the live thread into a scratch file. Never use the default output path, never add `--limit`:
   `uv run hnradar fetch-hiring --out data/cache/verify-<label>.json > data/cache/verify-<label>-fetch.txt`
   The command prints a table of post text, so the output goes to a file and you only read the two summary lines:
   `Get-Content data/cache/verify-<label>-fetch.txt | Select-String "^Wrote|^Skipped"` (PowerShell) or `grep -E "^(Wrote|Skipped)" data/cache/verify-<label>-fetch.txt` (bash).
   Put those lines into your report. Check the exit code of the fetch before reading the file.
5. Compare. Use the real ids, list them after the flags:
   `uv run python .claude/skills/verify-jobs-parse/compare_jobs.py --baseline "<baseline>" --new "data/cache/verify-<label>.json" --thread data/cache --must-exclude <ids> --must-keep <ids> --report "data/cache/verify-<label>-report.json"`
   Always quote the paths. `--thread data/cache` lets the script find the cached thread response that contains every job of the new file (it prints `thread file: ...`; write that path into your report). No network call is needed. The thread file lets the script tell an id the thread lost (comment deleted, flagged or emptied since the baseline) from an id the code dropped, and it rejects a thread file or a baseline that belongs to another thread. Do not choose the thread file by its modification time.
   Exit code 0 and the word `PASS` mean the id checks passed. Exit 1 is a FAIL. Exit 2 means bad input: fix the input, do not edit the script.
6. Repeat step 3 and confirm the hash is identical to the one before. If it differs, stop.
7. If the output has a "Skipped N ..." line, say how N relates to the removed ids. They need not be equal (the thread can drop posts by itself); state the difference and the reason, or ask.

## Expected result
- `PASS`, with every must-exclude id absent, every must-keep id present, no duplicate ids, and a baseline overlap of at least 0.5.
- `data/jobs.json` hash unchanged.
- Every removed id is either in must-exclude or listed under `gone from the thread`; `DROPPED BY THE CODE` is empty.

## Evidence to save
Under `data/cache/` (git-ignored): `verify-<label>.json` (the fetched jobs), `verify-<label>-fetch.txt` (what the fetch printed), `verify-<label>-report.json` (written by the script). In your reply give: commit, pytest count, the printed fetch lines, the full compare output, the before and after hash, and your explanation of any unexplained ids. Do not print post text; ids and counts are enough.

## Do not
- Overwrite `data/jobs.json` or any tracked data file.
- Fetch any URL found in a post (AGENTS.md rule 4); this skill only calls `fetch-hiring`.
- Call a check passed because a file exists. The ids must be compared.
- Edit `compare_jobs.py` or this file during a run.
