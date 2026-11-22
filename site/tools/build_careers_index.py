"""Build the careers-only Pagefind index (issue 40).

Reads site/careers/data/jobs.json, writes one small HTML page per job to a temporary folder and runs the same
Pagefind CLI and version as the RawWeights site search on that folder. The result is a self-contained index in
site/careers/pagefind/ that holds careers jobs only: it never touches the site's own pagefind/ folder or pagefind.yml.

    uv run python site/tools/build_careers_index.py
    uv run python site/tools/build_careers_index.py --jobs path/to/jobs.json --out path/to/pagefind

Rebuild after every refresh of jobs.json. Exit code 1 with "Error: ..." on any failure; the old index is replaced
only after the new one is complete and has one page per job (never a half index).
"""

from __future__ import annotations

import argparse
import html
import json
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from pydantic import ValidationError

from hnradar.cli import _terminal_safe  # one cleaner for untrusted post text (control and format characters)
from hnradar.models import Job
from hnradar.parse import is_candidate_profile

# Must match the version of the site's committed index (rawweightfinal/pagefind/pagefind-entry.json, "version").
PAGEFIND_VERSION = "1.6.0"

SITE = Path(__file__).resolve().parents[1]
DEFAULT_JOBS = SITE / "careers" / "data" / "jobs.json"
DEFAULT_OUT = SITE / "careers" / "pagefind"


class IndexBuildError(Exception):
    """A clear, user-facing failure. The CLI prints it as 'Error: ...' and exits 1."""


def load_jobs(path: Path) -> list[Job]:
    """Read and validate jobs.json. Every record must be a valid job and every comment_id unique."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise IndexBuildError(
            f"cannot read {path}: {exc.strerror or exc}. Run `hnradar fetch-hiring` and the sync script first."
        ) from exc
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise IndexBuildError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise IndexBuildError(f"{path} must hold a list of jobs, found {type(data).__name__}")

    jobs: list[Job] = []
    bad: list[str] = []
    for position, record in enumerate(data):
        try:
            jobs.append(Job.model_validate(record))
        except ValidationError:
            bad.append(str(position))
    if bad:
        shown = ", ".join(bad[:10]) + (f" and {len(bad) - 10} more" if len(bad) > 10 else "")
        raise IndexBuildError(f"{len(bad)} of {len(data)} records in {path} are not valid jobs (positions: {shown})")

    seen: set[int] = set()
    for job in jobs:
        if job.comment_id <= 0:  # the page ignores such a record, so a search hit on it would show nothing
            raise IndexBuildError(f"comment_id {job.comment_id} in {path} is not a positive number")
        if job.comment_id in seen:
            raise IndexBuildError(f"duplicate comment_id {job.comment_id} in {path}")
        seen.add(job.comment_id)
    # The pages (and the browser match in hn-fit.js, which does not repeat this check) assume the file holds openings only.
    people = [job.comment_id for job in jobs if is_candidate_profile(job.raw_text)]
    if people:
        shown = ", ".join(str(i) for i in people[:10]) + (f" and {len(people) - 10} more" if len(people) > 10 else "")
        raise IndexBuildError(f"{path} holds {len(people)} posts that are a person's own profile, not a job (comment ids: {shown}). Run `hnradar fetch-hiring` again.")
    if not jobs:
        raise IndexBuildError(f"{path} has no jobs: refusing to build an empty index")
    return jobs


def job_page(job: Job) -> str:
    """One indexable page for a job. Every field is cleaned of control/format characters and HTML-escaped."""
    esc = html.escape
    company = _terminal_safe(job.company)
    role = _terminal_safe(job.role)
    location = _terminal_safe(job.location)
    stack = " ".join(_terminal_safe(s) for s in job.stack)
    text = _terminal_safe(job.raw_text)
    title = " | ".join(part for part in (company, role) if part) or f"Post {job.comment_id}"
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f"<title>{esc(title)}</title>"
        # the page reads this id from a result and links to the HN comment; it is not indexed as text
        f'<meta data-pagefind-meta="comment_id[content]" content="{job.comment_id}">'
        "</head><body data-pagefind-body>"
        f'<h1 data-pagefind-weight="10">{esc(company)}</h1>'
        f'<p data-pagefind-weight="5">{esc(role)} {esc(location)}</p>'
        f'<p data-pagefind-weight="3">{esc(stack)}</p>'
        f"<p>{esc(text)}</p>"
        "</body></html>"
    )


def write_pages(jobs: list[Job], directory: Path) -> None:
    for job in jobs:
        (directory / f"{job.comment_id}.html").write_text(job_page(job), encoding="utf-8")


def run_pagefind(site_dir: Path, output_dir: Path) -> None:
    """Run the Pagefind CLI (same one as the site's `npm run build`), pinned to the site's version."""
    npx = shutil.which("npx")
    if npx is None:
        raise IndexBuildError("npx not found: install Node.js (the RawWeights site uses it for its own search index)")
    command = [npx, "--yes", f"pagefind@{PAGEFIND_VERSION}", "--site", str(site_dir), "--output-path", str(output_dir)]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[-800:]
        raise IndexBuildError(f"pagefind exited with {result.returncode}: {detail}")


def indexed_page_count(output_dir: Path) -> int:
    """Pages Pagefind reports in its entry file, so a short index is detected."""
    try:
        entry = json.loads((output_dir / "pagefind-entry.json").read_text(encoding="utf-8"))
        return sum(int(language["page_count"]) for language in entry["languages"].values())
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise IndexBuildError(f"pagefind wrote no readable pagefind-entry.json in {output_dir}: {exc}") from exc


def build(
    jobs_path: Path = DEFAULT_JOBS,
    out_dir: Path = DEFAULT_OUT,
    runner: Callable[[Path, Path], None] = run_pagefind,
) -> int:
    """Build the index and return the number of pages. The old index is replaced only when the new one is complete."""
    jobs = load_jobs(jobs_path)
    out_dir = out_dir.resolve()
    staging = out_dir.with_name(out_dir.name + ".new")
    shutil.rmtree(staging, ignore_errors=True)
    try:
        with tempfile.TemporaryDirectory(prefix="careers-index-") as tmp:
            pages = Path(tmp)
            write_pages(jobs, pages)
            runner(pages, staging)
        count = indexed_page_count(staging)
        if count != len(jobs):
            raise IndexBuildError(f"pagefind indexed {count} pages for {len(jobs)} jobs: index not replaced")
        # keep the working index until the new one is in place; put it back if the swap fails
        backup = out_dir.with_name(out_dir.name + ".old")
        shutil.rmtree(backup, ignore_errors=True)
        if out_dir.exists():
            out_dir.replace(backup)
        try:
            staging.replace(out_dir)
        except OSError:
            if backup.exists() and not out_dir.exists():
                backup.replace(out_dir)
            raise
        shutil.rmtree(backup, ignore_errors=True)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the careers-only Pagefind index.")
    parser.add_argument("--jobs", type=Path, default=DEFAULT_JOBS, help="jobs.json to index")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="folder for the Pagefind index")
    args = parser.parse_args(argv)
    try:
        count = build(args.jobs, args.out)
    except IndexBuildError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Indexed {count} careers jobs into {args.out} (pagefind {PAGEFIND_VERSION})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
