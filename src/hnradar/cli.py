import json
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import httpx
import typer

from hnradar.closed import closing_reply_id
from hnradar.fingerprint import build_profile
from hnradar.match import top_matches
from hnradar.models import Job, MatchResult, Profile
from hnradar.parse import parse_comment
from hnradar.showhn import build_result, normalize_domain
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
    jobs = []
    for c in comments:
        job = parse_comment(int(c["id"]), c["text"])
        closed_id = closing_reply_id(c)
        if closed_id is not None:
            job.closed, job.closed_comment_id = True, closed_id
        jobs.append(job)

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


@app.command("showhn")
def showhn(
    company: Annotated[str, typer.Argument(help="Company name to look up.")],
    domain: Annotated[
        str | None,
        typer.Option(help="Company domain, e.g. acme.com. Also searches stories whose URL contains it."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print the result as JSON.")] = False,
) -> None:
    """Find candidate Show HN stories for a company (at most 5, domain matches first).

    A domain match means the story links to that domain, not that the company posted it.
    """
    company = company.strip()
    try:
        if not company:
            raise ValueError("Company name must not be empty")
        wanted = normalize_domain(domain) if domain is not None else None  # validate before any network call
        found = hn.search_show_hn(company, domain=wanted)
        result = build_result(company, wanted, found["hits"], found["total_hits"])
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    except (hn.HNError, httpx.HTTPError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    if json_output:
        # ensure_ascii escapes every non-ASCII character (incl. C1 controls such as U+009B) on stdout.
        typer.echo(json.dumps(result.model_dump(), indent=2, ensure_ascii=True))
        return

    typer.echo(f'Show HN candidates for "{_terminal_safe(company)}": {len(result.candidates)} of {result.total_hits} hits')
    for c in result.candidates:
        match = "domain match" if c.domain_match else "-"
        typer.echo(
            f"{c.story_id} | {c.points} pts | {c.num_comments} comments | {match} | "
            f"{_terminal_safe(c.title)} | {_terminal_safe(c.url) or '-'}"
        )
    if result.truncated:
        advice = "refine the name" if domain is not None else "refine the name or add --domain"
        typer.echo(f"Showing {len(result.candidates)} of {result.total_hits} hits; {advice}.")
    if result.low_confidence:
        typer.echo("Low confidence: no story URL matches the domain" + ("" if domain is not None else " (no --domain given)") + ".")


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


def _read_json_file(path: Path, what: str, hint: str):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        typer.echo(f"Error: {what} not found at {path}. {hint}", err=True)
        raise typer.Exit(1) from exc
    except (OSError, ValueError, RecursionError) as exc:  # unreadable, not JSON, or nested too deeply
        typer.echo(f"Error: could not read {what} at {path}: {exc}", err=True)
        raise typer.Exit(1) from exc


def _profile_path(profiles_dir: Path, username: str) -> Path:
    """`fingerprint Octo` writes Octo.json; GitHub logins are case-insensitive, so `match octo` finds it too."""
    exact = profiles_dir / f"{username}.json"
    if not profiles_dir.is_dir():
        return exact
    wanted = f"{username}.json".lower()
    try:
        found = sorted(p for p in profiles_dir.iterdir() if p.name.lower() == wanted)
    except OSError:  # unreadable or vanished directory: let the normal read report the real error
        return exact
    if len(found) > 1:  # which one is current? Guessing could rank jobs from a stale profile
        names = ", ".join(p.name for p in found)
        typer.echo(f"Error: several profiles differ only by case ({names}). Keep one and remove the others.", err=True)
        raise typer.Exit(1)
    if exact in found or exact.exists():
        return exact
    return found[0] if found else exact


@app.command("match")
def match(
    username: Annotated[str, typer.Argument(help="GitHub username with a profile from `hnradar fingerprint`.")],
    jobs_file: Annotated[Path, typer.Option("--jobs", help="Jobs file from `hnradar fetch-hiring`.")] = Path("data/jobs.json"),
    profiles_dir: Annotated[Path, typer.Option(help="Directory holding <user>.json.")] = Path("data/profiles"),
    as_json: Annotated[bool, typer.Option("--json", help="Print the result as JSON instead of a table.")] = False,
) -> None:
    """Show the top 3 jobs for a user, scored deterministically from their profile."""
    if not github.USERNAME_RE.fullmatch(username):  # also keeps the path inside the profiles dir
        typer.echo(f"Error: {username!r} is not a valid GitHub username", err=True)
        raise typer.Exit(1)

    profile_path = _profile_path(profiles_dir, username)
    raw_profile = _read_json_file(profile_path, "profile", f"Run `hnradar fingerprint {username}` first.")
    raw_jobs = _read_json_file(jobs_file, "jobs file", "Run `hnradar fetch-hiring` first.")
    try:
        profile = Profile.model_validate(raw_profile)
        if not isinstance(raw_jobs, list):
            raise ValueError("expected a list of jobs")
        jobs = [Job.model_validate(item) for item in raw_jobs]
    except ValueError as exc:  # pydantic's ValidationError is a ValueError
        typer.echo(f"Error: {profile_path} or {jobs_file} has an unexpected shape: {exc}", err=True)
        raise typer.Exit(1) from exc

    if profile.username.lower() != username.lower():  # a copied or renamed profile would be shown under the wrong name
        typer.echo(f"Error: {profile_path} is the profile of {profile.username!r}, not {username!r}", err=True)
        raise typer.Exit(1)

    matches = top_matches(profile, jobs)
    result = MatchResult(
        username=profile.username,
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        status="ok" if matches else "no_strong_matches",
        matches=matches,
    )
    if as_json:
        typer.echo(json.dumps(result.model_dump(), indent=2, ensure_ascii=True))
        return
    if not matches:
        typer.echo(f"No strong matches for {_terminal_safe(profile.username)} in {len(jobs)} jobs.")
        return
    typer.echo(f"Top {len(matches)} of {len(jobs)} jobs for {_terminal_safe(profile.username)}:")
    for m in matches:
        j = m.job
        typer.echo(f"{m.rank}. {_terminal_safe(j.company)} | {_terminal_safe(j.role) or '-'} | {_remote_label(j.remote)} | score {m.score}")
        for reason in m.reasons:
            typer.echo(f"   - {_terminal_safe(reason.text)}")
        if j.url:
            typer.echo(f"   {_terminal_safe(j.url)}")
