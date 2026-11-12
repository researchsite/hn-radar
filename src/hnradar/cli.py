import json
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
    limit: Annotated[int | None, typer.Option(min=1, help="Max number of jobs to keep.")] = None,
    out: Annotated[Path, typer.Option(help="Output JSON file.")] = Path("data/jobs.json"),
) -> None:
    """Fetch the latest 'Ask HN: Who is hiring?' thread and write parsed jobs as JSON."""
    try:
        story = hn.find_latest_hiring_story()
        comments = hn.top_level_comments(hn.fetch_item(story["id"]))
    except (hn.HNError, httpx.HTTPError) as exc:  # HNError covers non-JSON responses
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    if limit is not None:
        comments = comments[:limit]
    jobs = [parse_comment(int(c["id"]), c["text"]) for c in comments]

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps([j.model_dump() for j in jobs], indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    typer.echo(f'Wrote {len(jobs)} jobs from "{story["title"]}" to {out}')


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
