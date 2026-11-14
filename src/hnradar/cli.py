import json
import unicodedata
from pathlib import Path
from typing import Annotated

import httpx
import typer

from hnradar.fingerprint import build_profile
from hnradar.parse import parse_comment
from hnradar.sources import github, hn

app = typer.Typer(help="HN Radar", no_args_is_help=True)


@app.callback()
def _root() -> None:
    """HN Radar."""


@app.command("fetch-hiring")
def fetch_hiring(
    limit: Annotated[int | None, typer.Option(min=0, help="Max number of jobs to keep.")] = None,
    out: Annotated[
        Path | None,
        typer.Option(help="Output JSON file (default data/jobs.json; with --json only written if given)."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print jobs as JSON to stdout instead of a table.")] = False,
) -> None:
    """Fetch the latest 'Ask HN: Who is hiring?' thread, write parsed jobs as JSON and print a table."""
    try:
        story = hn.find_latest_hiring_story()
        comments = hn.top_level_comments(hn.fetch_item(story["id"]))
    except (hn.HNError, httpx.HTTPError) as exc:  # HNError covers non-JSON responses
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    if limit is not None:
        comments = comments[:limit]
    jobs = [parse_comment(int(c["id"]), c["text"]) for c in comments]

    records = [j.model_dump() for j in jobs]
    target = out if out is not None else (None if json_output else Path("data/jobs.json"))
    if target is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if json_output:
        # ensure_ascii escapes every non-ASCII character (incl. C1 controls such as U+009B) on stdout.
        typer.echo(json.dumps(records, indent=2, ensure_ascii=True))
        return

    typer.echo(f'Wrote {len(jobs)} jobs from "{_terminal_safe(story["title"])}" to {target}')
    for j in jobs:
        typer.echo(f"{_terminal_safe(j.company)} | {_terminal_safe(j.role) or '-'} | {_remote_label(j.remote)}")


def _terminal_safe(text: str | None) -> str:
    """Post text is attacker-controlled: replace control characters (ESC, BEL, CR, C1...) with spaces and
    drop format characters (bidirectional overrides like U+202E, zero-width) before printing."""
    if not text:
        return ""
    kept = (" " if unicodedata.category(c) == "Cc" else c for c in text if unicodedata.category(c) != "Cf")
    return " ".join("".join(kept).split())


def _remote_label(remote: bool | None) -> str:
    return "?" if remote is None else ("yes" if remote else "no")


@app.command("fingerprint")
def fingerprint(
    username: Annotated[str, typer.Argument(help="Public GitHub username.")],
    out_dir: Annotated[Path, typer.Option(help="Directory for <user>.json.")] = Path("data/profiles"),
) -> None:
    """Summarize a GitHub user's languages, topics and notable repos from public data."""
    try:
        repos = github.fetch_repos(username)
    except (github.GitHubError, httpx.HTTPError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    profile = build_profile(username, repos)
    out = out_dir / f"{username}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(profile.model_dump_json(indent=2) + "\n", encoding="utf-8")
    if not repos:
        typer.echo(f"{username} has no public repos; wrote an empty profile to {out}")
    else:
        typer.echo(f"Wrote profile for {username} ({len(profile.languages)} languages) to {out}")
