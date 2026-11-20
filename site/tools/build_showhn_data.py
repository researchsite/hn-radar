"""Build the Show HN file of every job for the company page (issues 32 and 41).

For each post in site/careers/data/jobs.json it runs the same lookup as `hnradar showhn` and writes the result to
site/careers/data/showhn/<comment_id>.json, the file company.html reads, and a one-file summary
site/careers/data/showhn-index.json that the company directory uses to put companies with Show HN posts first. The company domain is the host of the
job's own link; a job board or other shared host is flagged by `hnradar` as a platform domain (low confidence),
and a post without a link is looked up by name only.

    uv run python site/tools/build_showhn_data.py                 # jobs without a file yet
    uv run python site/tools/build_showhn_data.py --refresh       # redo every job
    uv run python site/tools/build_showhn_data.py --limit 20      # first 20 jobs, to try it

A failed lookup writes nothing for that job (a failed request is never saved as "no Show HN post"): it is listed at
the end and the exit code is 1. Run it again; jobs that have a file are skipped. Needs the network (HN search).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

import httpx

from hnradar.cli import _terminal_safe, _write_text_atomic
from hnradar.models import Job, ShowHNResult
from hnradar.showhn import build_result, normalize_domain
from hnradar.sources import hn

SITE = Path(__file__).resolve().parents[1]
DEFAULT_JOBS = SITE / "careers" / "data" / "jobs.json"
DEFAULT_OUT = SITE / "careers" / "data" / "showhn"
INDEX_NAME = "showhn-index.json"  # written next to the showhn folder


class ShowHNBuildError(Exception):
    """A clear, user-facing failure. Printed as 'Error: ...' with exit code 1."""


def load_jobs(path: Path) -> list[Job]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ShowHNBuildError(f"cannot read {path}: {exc.strerror or exc}. Run `hnradar fetch-hiring` and the sync script first.") from exc
    except json.JSONDecodeError as exc:
        raise ShowHNBuildError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, list) or not data:
        raise ShowHNBuildError(f"{path} must be a non-empty list of jobs")
    try:
        return [Job.model_validate(record) for record in data]
    except ValueError as exc:
        raise ShowHNBuildError(f"{path} has an invalid job record: {exc}") from exc


def job_query(job: Job) -> str | None:
    """The company name as company.html compares it (one line, single spaces); None when the post names no company."""
    name = " ".join(_terminal_safe(job.company).split())
    return name or None


def job_domain(job: Job) -> str | None:
    """The host of the job's own link as a bare domain, or None (no link, or not a plain http(s) host)."""
    try:
        parsed = urlparse(job.url or "")
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return None
        return normalize_domain(parsed.hostname)
    except ValueError:  # malformed URL or a host that is not a bare ASCII domain
        return None


def build(
    jobs: list[Job],
    out_dir: Path,
    *,
    refresh: bool = False,
    limit: int | None = None,
    pause: float = 0.3,
    search: Callable[..., dict] = hn.search_show_hn,
    write: Callable[[Path, str], None] = _write_text_atomic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Write one file per job. Returns counts and the list of failures: {"written", "skipped", "no_company", "failed"}."""
    counts = {"written": 0, "skipped": 0, "no_company": 0, "failed": []}
    for job in jobs[:limit] if limit is not None else jobs:
        target = out_dir / f"{job.comment_id}.json"
        query = job_query(job)
        if query is None:
            counts["no_company"] += 1
            continue
        if target.exists() and not refresh:
            counts["skipped"] += 1
            continue
        try:
            domain = job_domain(job)
            found = search(query, domain=domain)
            result = build_result(query, domain, found["hits"], found["total_hits"])
            write(target, result.model_dump_json(indent=2) + "\n")
        except (ValueError, hn.HNError, httpx.HTTPError, OSError) as exc:
            counts["failed"].append((job.comment_id, query, _terminal_safe(str(exc))))
            continue
        counts["written"] += 1
        sleep(pause)
    return counts


def write_index(out_dir: Path, index_path: Path, write: Callable[[Path, str], None] = _write_text_atomic) -> int:
    """Summarise every Show HN file in `out_dir` into one small file, so the company directory can rank companies
    without opening 200+ files: {"<comment_id>": {"strong": stories that link to the company domain, "found": stories shown}}."""
    summary = {}
    for file in sorted(out_dir.glob("*.json")):
        if not file.stem.isdigit():
            continue
        try:
            result = ShowHNResult.model_validate_json(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # a broken file is left out of the index; the company page shows it as invalid
        summary[file.stem] = {
            "strong": sum(1 for c in result.candidates if c.domain_match) if not result.platform_domain else 0,
            "found": len(result.candidates),
        }
    write(index_path, json.dumps(summary, indent=1, sort_keys=True) + "\n")
    return len(summary)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write the Show HN file of every job for the company page.")
    parser.add_argument("--jobs", type=Path, default=DEFAULT_JOBS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--refresh", action="store_true", help="redo jobs that already have a file")
    parser.add_argument("--limit", type=int, default=None, help="only the first N jobs")
    parser.add_argument("--pause", type=float, default=0.3, help="seconds between requests (default 0.3)")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        print("Error: --limit must be at least 1", file=sys.stderr)
        return 1
    try:
        jobs = load_jobs(args.jobs)
    except ShowHNBuildError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    counts = build(jobs, args.out, refresh=args.refresh, limit=args.limit, pause=args.pause)
    indexed = write_index(args.out, args.out.parent / INDEX_NAME)
    print(f"Show HN files in {args.out}: {counts['written']} written, {counts['skipped']} already there, "
          f"{counts['no_company']} posts without a company name, {len(counts['failed'])} failed. "
          f"{INDEX_NAME}: {indexed} entries.")
    for comment_id, query, reason in counts["failed"]:
        print(f"  failed: {comment_id} ({query}): {reason}", file=sys.stderr)
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
