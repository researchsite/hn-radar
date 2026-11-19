"""Compare two jobs.json files by comment_id for the verify-jobs-parse skill.

Reads files only: no network, no post text is printed (ids and counts only), so the output is terminal-safe.
Exit codes: 0 = every check passed, 1 = a check failed, 2 = bad input (missing or unreadable file, wrong shape,
no matching thread file, a report that already exists).
"""

import argparse
import json
import sys
from pathlib import Path

MIN_OVERLAP = 0.5  # share of the baseline that must still be in the thread (or the new file) to count as the same thread


class InputError(Exception):
    """Bad input to the script: reported as `Error: ...` with exit code 2, never as a failed check."""


def _read_json(path: Path, what: str):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise InputError(f"{what} not found at {path}") from None
    except (OSError, ValueError, RecursionError) as exc:  # RecursionError: absurdly nested JSON
        raise InputError(f"could not read {what} at {path}: {exc}") from None


def load_ids(path: Path, what: str) -> list[int]:
    data = _read_json(path, what)
    if not isinstance(data, list):
        raise InputError(f"{what} at {path} is not a JSON list of jobs")
    ids = []
    for i, item in enumerate(data):
        if not isinstance(item, dict) or not isinstance(item.get("comment_id"), int):
            raise InputError(f"{what} at {path}: entry {i} has no integer comment_id")
        ids.append(item["comment_id"])
    return ids


def load_thread_ids(path: Path) -> set[int]:
    """Ids of the top-level comments that fetch-hiring can turn into jobs: the cached thread response
    (data/cache/items_*.json), keeping only children that have text, like hn.top_level_comments."""
    data = _read_json(path, "thread file")
    children = data.get("children") if isinstance(data, dict) else None
    if not isinstance(children, list) or not all(isinstance(c, dict) and isinstance(c.get("id"), int) for c in children):
        raise InputError(f"thread file at {path} has no list of children with integer ids")
    return {c["id"] for c in children if c.get("text")}


def find_thread_file(directory: Path, new_ids: list[int]) -> Path:
    """The cached thread file the fetch used is the one that contains every job id of the new file.
    Several cache entries can match (same thread cached twice); the newest one is taken."""
    if not new_ids:
        raise InputError("cannot pick a thread file from a directory when the new file has no jobs; pass the file")
    wanted = set(new_ids)
    matches = []
    for candidate in directory.glob("items_*.json"):
        try:
            if wanted <= load_thread_ids(candidate):
                matches.append(candidate)
        except InputError:
            continue  # an unreadable cache entry is not the one the fetch used
    if not matches:
        raise InputError(f"no items_*.json in {directory} contains every job id of the new file")
    return max(matches, key=lambda p: p.stat().st_mtime)


def compare(
    baseline: list[int],
    new: list[int],
    must_exclude: list[int],
    must_keep: list[int],
    thread_ids: set[int] | None = None,
) -> dict:
    base, now = set(baseline), set(new)
    removed, added = sorted(base - now), sorted(now - base)
    reference = thread_ids if thread_ids is not None else now
    overlap = len(base & reference) / len(base) if base else 1.0
    result = {
        "baseline_count": len(baseline),
        "new_count": len(new),
        "duplicate_ids_in_new": len(new) - len(now),
        "removed": removed,
        "added": added,
        "must_exclude_still_present": sorted(set(must_exclude) & now),
        "must_keep_missing": sorted(set(must_keep) - now),
        "baseline_overlap": round(overlap, 3),
        "baseline_looks_foreign": overlap < MIN_OVERLAP,
    }
    if thread_ids is not None:
        # An id that is no longer in the thread was removed by the thread, not by the code. An id that is still
        # in the thread, was removed and is not in must-exclude was dropped by the code: that is the failure.
        # Every job in the new file must come from this thread, otherwise the thread file is the wrong one.
        result["gone_from_thread"] = [i for i in removed if i not in thread_ids]
        result["dropped_unexpectedly"] = [i for i in removed if i in thread_ids and i not in set(must_exclude)]
        result["new_not_in_thread"] = sorted(now - thread_ids)
    return result


def passed(result: dict) -> bool:
    return not (
        result["must_exclude_still_present"]
        or result["must_keep_missing"]
        or result["duplicate_ids_in_new"]
        or result["baseline_looks_foreign"]
        or result.get("dropped_unexpectedly")
        or result.get("new_not_in_thread")
    )


def run(args: argparse.Namespace) -> int:
    new_ids = load_ids(args.new, "new file")
    thread_ids = None
    if args.thread:
        thread_file = find_thread_file(args.thread, new_ids) if args.thread.is_dir() else args.thread
        thread_ids = load_thread_ids(thread_file)
        print(f"thread file: {thread_file}")
    if args.report and args.report.exists():
        raise InputError(f"report {args.report} already exists; use a new label so earlier evidence is kept")

    result = compare(load_ids(args.baseline, "baseline file"), new_ids, args.must_exclude, args.must_keep, thread_ids)
    result["passed"] = passed(result)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    print(f"baseline {result['baseline_count']} jobs, new {result['new_count']} jobs")
    print(f"removed ({len(result['removed'])}): {result['removed']}")
    print(f"added ({len(result['added'])}): {result['added']}")
    print(f"must-exclude still present: {result['must_exclude_still_present']}")
    print(f"must-keep missing: {result['must_keep_missing']}")
    print(f"duplicate ids in the new file: {result['duplicate_ids_in_new']}")
    print(f"baseline overlap: {result['baseline_overlap']}" + (" (LOOKS LIKE ANOTHER THREAD)" if result["baseline_looks_foreign"] else ""))
    if "gone_from_thread" in result:
        print(f"gone from the thread (not the code): {result['gone_from_thread']}")
        print(f"DROPPED BY THE CODE, not in must-exclude: {result['dropped_unexpectedly']}")
        print(f"new jobs not in the thread (wrong thread file?): {result['new_not_in_thread']}")
    print("PASS" if result["passed"] else "FAIL")
    return 0 if result["passed"] else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--baseline", type=Path, required=True, help="jobs.json from before the change")
    ap.add_argument("--new", type=Path, required=True, help="jobs.json produced by the changed code")
    ap.add_argument("--must-exclude", type=int, nargs="*", default=[], help="comment_ids that must be gone")
    ap.add_argument("--must-keep", type=int, nargs="*", default=[], help="comment_ids that must still be there")
    ap.add_argument(
        "--thread",
        type=Path,
        help="cached thread response (a data/cache/items_*.json file) or the data/cache directory, in which case "
        "the file that contains every job id of --new is used",
    )
    ap.add_argument("--report", type=Path, help="write the result as JSON to this new file (an existing file is an error)")
    args = ap.parse_args(argv)
    try:
        return run(args)
    except InputError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
