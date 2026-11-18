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


def load_thread_ids(path: Path) -> set[int]:
    """Top-level comment ids of the cached thread response that the fetch used (data/cache/items_*.json)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"Error: thread file not found at {path}")
    except (OSError, ValueError) as exc:
        raise SystemExit(f"Error: could not read thread file at {path}: {exc}")
    children = data.get("children") if isinstance(data, dict) else None
    if not isinstance(children, list) or not all(isinstance(c, dict) and isinstance(c.get("id"), int) for c in children):
        raise SystemExit(f"Error: thread file at {path} has no list of children with integer ids")
    return {c["id"] for c in children}


def compare(
    baseline: list[int],
    new: list[int],
    must_exclude: list[int],
    must_keep: list[int],
    thread_ids: set[int] | None = None,
) -> dict:
    base, now = set(baseline), set(new)
    removed, added = sorted(base - now), sorted(now - base)
    result = {
        "baseline_count": len(baseline),
        "new_count": len(new),
        "duplicate_ids_in_new": len(new) - len(now),
        "removed": removed,
        "added": added,
        "must_exclude_still_present": sorted(set(must_exclude) & now),
        "must_keep_missing": sorted(set(must_keep) - now),
    }
    if thread_ids is not None:
        # An id that is no longer in the thread was removed by the thread, not by the code. An id that is still
        # in the thread, was removed and is not in must-exclude was dropped by the code: that is the failure.
        result["gone_from_thread"] = [i for i in removed if i not in thread_ids]
        result["dropped_unexpectedly"] = [i for i in removed if i in thread_ids and i not in set(must_exclude)]
        result["added_not_in_thread"] = [i for i in added if i not in thread_ids]
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--baseline", type=Path, required=True, help="jobs.json from before the change")
    ap.add_argument("--new", type=Path, required=True, help="jobs.json produced by the changed code")
    ap.add_argument("--must-exclude", type=int, nargs="*", default=[], help="comment_ids that must be gone")
    ap.add_argument("--must-keep", type=int, nargs="*", default=[], help="comment_ids that must still be there")
    ap.add_argument("--thread", type=Path, help="cached thread response of the run (data/cache/items_*.json); sorts removed and added ids into thread drift and code drops")
    ap.add_argument("--report", type=Path, help="write the result as JSON to this file")
    args = ap.parse_args(argv)

    result = compare(
        load_ids(args.baseline, "baseline file"),
        load_ids(args.new, "new file"),
        args.must_exclude,
        args.must_keep,
        load_thread_ids(args.thread) if args.thread else None,
    )
    result["passed"] = (
        not result["must_exclude_still_present"]
        and not result["must_keep_missing"]
        and not result.get("dropped_unexpectedly")
        and not result.get("added_not_in_thread")
    )
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    print(f"baseline {result['baseline_count']} jobs, new {result['new_count']} jobs")
    print(f"removed ({len(result['removed'])}): {result['removed']}")
    print(f"added ({len(result['added'])}): {result['added']}")
    print(f"must-exclude still present: {result['must_exclude_still_present']}")
    print(f"must-keep missing: {result['must_keep_missing']}")
    if "gone_from_thread" in result:
        print(f"gone from the thread (not the code): {result['gone_from_thread']}")
        print(f"DROPPED BY THE CODE, not in must-exclude: {result['dropped_unexpectedly']}")
        print(f"added but not in the thread: {result['added_not_in_thread']}")
    print("PASS" if result["passed"] else "FAIL")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
