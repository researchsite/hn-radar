"""Build the pulse file of the Show HN stories of every company for the company page (issues 13 and 41).

Reads the Show HN files written by build_showhn_data.py, picks the stories worth summarising and writes
site/careers/data/pulse/<story_id>.json for each, the same result as `hnradar pulse <story_id>`.

    uv run python site/tools/build_pulse_data.py                    # stories that link to the company's own domain
    uv run python site/tools/build_pulse_data.py --limit 5          # first 5 stories, to try it
    uv run python site/tools/build_pulse_data.py --name-matches     # also stories found by name only (many more, more noise)
    uv run python site/tools/build_pulse_data.py --model qwen2.5:14b --refresh

A story is picked when it links to the company's own domain (not a shared host such as GitHub), has at least
--min-comments comments, and is among the company's --per-company most commented ones. A story shared by several
jobs is summarised once. Several hiring posts of one company count as one company. Only the stories saved in the Show HN
files are considered (at most 5 per company: domain matches first, then points), so a busy story ranked lower is not
summarised. Stories that already have a file are skipped unless --refresh. A thread with too few
comments with text writes nothing (that is a normal result, not a failure). A failed story is listed at the end and
the exit code is 1; after 3 failures in a row the run stops (the model is probably not running).
Needs a running Ollama with the model, and the network (HN).
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable
from pathlib import Path

import httpx
from pydantic import ValidationError

from hnradar.cli import _terminal_safe, _write_text_atomic
from hnradar.models import ShowHNCandidate, ShowHNResult
from hnradar.pulse.cluster import PulseError, cluster
from hnradar.sources import hn, ollama

SITE = Path(__file__).resolve().parents[1]
DEFAULT_SHOWHN = SITE / "careers" / "data" / "showhn"
DEFAULT_OUT = SITE / "careers" / "data" / "pulse"
MAX_FAILURES_IN_A_ROW = 3


class PulseBuildError(Exception):
    """A clear, user-facing failure. Printed as 'Error: ...' with exit code 1."""


def pick_stories(
    showhn_dir: Path,
    *,
    per_company: int = 2,
    min_comments: int = 3,
    name_matches: bool = False,
    problems: list[str] | None = None,
) -> list[tuple[int, str]]:
    """(story_id, title) for the stories to summarise, most commented first, each story once.
    A Show HN file that cannot be read is skipped and its name is appended to `problems` (the caller reports it)."""
    if not showhn_dir.is_dir():
        raise PulseBuildError(f"{showhn_dir} is not a folder. Run build_showhn_data.py first.")
    # one company can have several hiring posts: group the files by company name so the limit is per company
    by_company: dict[tuple[str, str | None], dict[int, ShowHNCandidate]] = {}
    for file in sorted(showhn_dir.glob("*.json")):
        if not file.stem.isdigit():
            continue
        try:
            result = ShowHNResult.model_validate_json(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            if problems is not None:
                problems.append(file.name)
            continue
        # same name and same domain = the same company; two employers that share a name keep separate limits
        stories = by_company.setdefault((result.query.casefold(), result.domain), {})
        for c in result.candidates:
            if c.num_comments >= min_comments and ((c.domain_match and not result.platform_domain) or name_matches):
                stories.setdefault(c.story_id, c)
    picked: dict[int, tuple[int, str]] = {}  # story_id -> (num_comments, title)
    for stories in by_company.values():
        usable = sorted(stories.values(), key=lambda c: (not c.domain_match, -c.num_comments, -c.story_id))
        for c in usable[:per_company]:
            picked.setdefault(c.story_id, (c.num_comments, c.title))
    return [(sid, title) for sid, (_n, title) in sorted(picked.items(), key=lambda kv: (-kv[1][0], kv[0]))]


def prune_stale(showhn_dir: Path, out_dir: Path, min_comments: int) -> int:
    """Delete pulse files of stories that the Show HN files now list with fewer than `min_comments` comments: the
    thread no longer has enough to summarise, so an earlier summary must not keep being shown."""
    removed = 0
    for file in showhn_dir.glob("*.json"):
        if not file.stem.isdigit():
            continue
        try:
            result = ShowHNResult.model_validate_json(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for c in result.candidates:
            old = out_dir / f"{c.story_id}.json"
            if c.num_comments < min_comments and old.exists():
                old.unlink()
                removed += 1
    return removed


def make_pulse(story_id: int, client, fetch: Callable[..., dict] = hn.fetch_item):
    """The same steps as `hnradar pulse`: fetch the story, then group its comments. None = not enough signal."""
    item = fetch(story_id)
    if not isinstance(item, dict) or item.get("type") != "story" or not isinstance(item.get("children", []), list):
        raise ValueError(f"{story_id} is not a story")
    title = item.get("title") if isinstance(item.get("title"), str) else ""
    children = item.get("children") or []
    return cluster(story_id, title, [c for c in children if not isinstance(c, dict) or c.get("text")], client)


def build(
    stories: list[tuple[int, str]],
    out_dir: Path,
    client,
    *,
    refresh: bool = False,
    limit: int | None = None,
    pause: float = 0.5,
    fetch: Callable[..., dict] = hn.fetch_item,
    write: Callable[[Path, str], None] = _write_text_atomic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Write one file per story. Returns {"written", "skipped", "no_signal", "failed": [(story_id, title, reason)], "stopped"}."""
    counts = {"written": 0, "skipped": 0, "no_signal": 0, "failed": [], "stopped": False}
    in_a_row = 0
    for story_id, title in stories[:limit] if limit is not None else stories:
        target = out_dir / f"{story_id}.json"
        if target.exists() and not refresh:
            counts["skipped"] += 1
            continue
        try:
            result = make_pulse(story_id, client, fetch)
            if result is None:
                counts["no_signal"] += 1
                in_a_row = 0
                target.unlink(missing_ok=True)  # a summary from an earlier run no longer holds: do not keep showing it
                continue
            write(target, result.model_dump_json(indent=2) + "\n")
        except (ValueError, PulseError, ollama.ModelError, hn.HNError, httpx.HTTPError, ValidationError, OSError) as exc:
            counts["failed"].append((story_id, title, _terminal_safe(str(exc))))
            in_a_row += 1
            if in_a_row >= MAX_FAILURES_IN_A_ROW:
                counts["stopped"] = True
                break
            continue
        in_a_row = 0
        counts["written"] += 1
        sleep(pause)
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write the pulse file of the Show HN stories of every company.")
    parser.add_argument("--showhn", type=Path, default=DEFAULT_SHOWHN, help="folder of Show HN files")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--model", default=ollama.DEFAULT_MODEL, help=f"Ollama model (default {ollama.DEFAULT_MODEL})")
    parser.add_argument("--per-company", type=int, default=2, help="most commented stories per company (default 2)")
    parser.add_argument("--min-comments", type=int, default=3, help="skip stories with fewer comments (default 3)")
    parser.add_argument("--name-matches", action="store_true", help="also stories that match by name only")
    parser.add_argument("--refresh", action="store_true", help="redo stories that already have a file")
    parser.add_argument("--limit", type=int, default=None, help="only the first N stories")
    parser.add_argument("--pause", type=float, default=0.5, help="seconds between stories (default 0.5)")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1 or args.per_company < 1 or args.min_comments < 0:
        print("Error: --limit and --per-company must be at least 1, --min-comments at least 0", file=sys.stderr)
        return 1
    problems: list[str] = []
    try:
        stories = pick_stories(args.showhn, per_company=args.per_company, min_comments=args.min_comments,
                               name_matches=args.name_matches, problems=problems)
    except PulseBuildError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if problems:
        shown = ", ".join(problems[:5]) + (f" and {len(problems) - 5} more" if len(problems) > 5 else "")
        print(f"Warning: {len(problems)} Show HN file(s) could not be read and were skipped ({shown}). "
              "Run build_showhn_data.py --refresh to rebuild them.", file=sys.stderr)
    stale = prune_stale(args.showhn, args.out, args.min_comments) if args.out.is_dir() else 0
    if stale:
        print(f"Removed {stale} pulse file(s) of stories that now have too few comments.")
    if not stories:
        print("No story to summarise (no company has a Show HN story that links to its own domain).")
        return 0
    counts = build(stories, args.out, ollama.OllamaClient(args.model), refresh=args.refresh, limit=args.limit, pause=args.pause)
    print(f"Pulse files in {args.out}: {counts['written']} written, {counts['skipped']} already there, "
          f"{counts['no_signal']} with too few comments, {len(counts['failed'])} failed (of {len(stories)} stories).")
    for story_id, title, reason in counts["failed"]:
        print(f"  failed: {story_id} ({_terminal_safe(title)[:60]}): {reason}", file=sys.stderr)
    if counts["stopped"]:
        print(f"Stopped after {MAX_FAILURES_IN_A_ROW} failures in a row: is Ollama running with {args.model}?", file=sys.stderr)
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
