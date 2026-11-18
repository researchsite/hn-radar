"""Compare two jobs.json files by comment_id for the verify-jobs-parse skill.

Reads files only: no network, no post text is printed (ids and counts only), so the output is terminal-safe.
Exit codes: 0 = every must-exclude id is gone and every must-keep id is present, 1 = a check failed,
2 = bad input (missing file, not a JSON list, an entry without a comment_id).
"""

import argparse
import json
import sys
from pathlib import Path


def load_ids(path: Path, what: str) -> list[int]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"Error: {what} not found at {path}")
    except (OSError, ValueError) as exc:
        raise SystemExit(f"Error: could not read {what} at {path}: {exc}")
    if not isinstance(data, list):
        raise SystemExit(f"Error: {what} at {path} is not a JSON list of jobs")
    ids = []
    for i, item in enumerate(data):
        if not isinstance(item, dict) or not isinstance(item.get("comment_id"), int):
            raise SystemExit(f"Error: {what} at {path}: entry {i} has no integer comment_id")
        ids.append(item["comment_id"])
    return ids


def compare(baseline: list[int], new: list[int], must_exclude: list[int], must_keep: list[int]) -> dict:
    base, now = set(baseline), set(new)
    return {
        "baseline_count": len(baseline),
        "new_count": len(new),
        "duplicate_ids_in_new": len(new) - len(now),
        "removed": sorted(base - now),
        "added": sorted(now - base),
        "must_exclude_still_present": sorted(set(must_exclude) & now),
        "must_keep_missing": sorted(set(must_keep) - now),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--baseline", type=Path, required=True, help="jobs.json from before the change")
    ap.add_argument("--new", type=Path, required=True, help="jobs.json produced by the changed code")
    ap.add_argument("--must-exclude", type=int, nargs="*", default=[], help="comment_ids that must be gone")
    ap.add_argument("--must-keep", type=int, nargs="*", default=[], help="comment_ids that must still be there")
    ap.add_argument("--report", type=Path, help="write the result as JSON to this file")
    args = ap.parse_args(argv)

    result = compare(
        load_ids(args.baseline, "baseline file"),
        load_ids(args.new, "new file"),
        args.must_exclude,
        args.must_keep,
    )
    result["passed"] = not result["must_exclude_still_present"] and not result["must_keep_missing"]
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    print(f"baseline {result['baseline_count']} jobs, new {result['new_count']} jobs")
    print(f"removed ({len(result['removed'])}): {result['removed']}")
    print(f"added ({len(result['added'])}): {result['added']}")
    print(f"must-exclude still present: {result['must_exclude_still_present']}")
    print(f"must-keep missing: {result['must_keep_missing']}")
    print("PASS" if result["passed"] else "FAIL")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
