import json
import os
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import httpx
import typer

from hnradar.closed import closing_reply_id
from hnradar.draft.write import write_draft
from hnradar.match import top_matches
from hnradar.models import Job, MatchResult, Profile
from hnradar.parse import is_candidate_profile, parse_comment
from hnradar import radar as radar_mod
from hnradar.pulse.cluster import MIN_COMMENTS, PulseError, cluster
from hnradar.showhn import build_result, normalize_domain
from hnradar.steps import save_profile, cited_bug, find_story, repo_for_match, story_children
from hnradar.sources import github, hn, ollama

DRAFT_MATCHES = 10  # best matches tried, in order, until one has a Show HN with a cited bug
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
    skipped = 0
    for c in comments:
        job = parse_comment(int(c["id"]), c["text"])
        if is_candidate_profile(job.raw_text):  # a person offering themselves, not a job
            skipped += 1
            continue
        closed_id = closing_reply_id(c)
        if closed_id is not None:
            job.closed, job.closed_comment_id = True, closed_id
        jobs.append(job)

    records = [j.model_dump() for j in jobs]
    target = out if out is not None else (None if json_output else Path("data/jobs.json"))
    if target is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    skipped_note = f"Skipped {skipped} candidate {'profile' if skipped == 1 else 'profiles'}"
    if json_output:
        # ensure_ascii escapes every non-ASCII character (incl. C1 controls such as U+009B) on stdout.
        typer.echo(json.dumps(records, indent=2, ensure_ascii=True))
        if skipped:
            typer.echo(skipped_note, err=True)  # stderr keeps stdout valid JSON
        return

    typer.echo(f'Wrote {len(jobs)} jobs from "{_terminal_safe(story["title"])}" to {target}')
    if skipped:
        typer.echo(skipped_note)
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
    A platform domain (github.com, a job board, ...) is flagged as low confidence.
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
    if result.platform_domain:
        typer.echo(
            "Low confidence: platform domain. A story that links to it may belong to someone else, "
            "so a match does not show that the company posted it."
        )
    if result.low_confidence and not any(c.domain_match for c in result.candidates):
        typer.echo("Low confidence: no story URL matches the domain" + ("" if domain is not None else " (no --domain given)") + ".")


@app.command("fingerprint")
def fingerprint(
    username: Annotated[str, typer.Argument(help="Public GitHub username.")],
    out_dir: Annotated[Path, typer.Option(help="Directory for <user>.json.")] = Path("data/profiles"),
) -> None:
    """Summarize a GitHub user's languages, topics and notable repos from public data."""
    try:
        profile, out, n_repos = save_profile(username, out_dir)
    except (github.GitHubError, httpx.HTTPError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    if not n_repos:
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


def _parse_jobs(raw_jobs) -> list[Job]:
    if not isinstance(raw_jobs, list):
        raise ValueError("expected a list of jobs")
    return [Job.model_validate(item) for item in raw_jobs]


def _load_profile_and_jobs(username: str, jobs_file: Path, profiles_dir: Path) -> tuple[Profile, list[Job]]:
    """Shared by `match` and `draft`: the saved profile and jobs, or an `Error:` line and exit 1."""
    if not github.USERNAME_RE.fullmatch(username):  # also keeps the path inside the profiles dir
        typer.echo(f"Error: {username!r} is not a valid GitHub username", err=True)
        raise typer.Exit(1)

    profile_path = _profile_path(profiles_dir, username)
    raw_profile = _read_json_file(profile_path, "profile", f"Run `hnradar fingerprint {username}` first.")
    raw_jobs = _read_json_file(jobs_file, "jobs file", "Run `hnradar fetch-hiring` first.")
    try:
        profile = Profile.model_validate(raw_profile)
        jobs = _parse_jobs(raw_jobs)
    except ValueError as exc:  # pydantic's ValidationError is a ValueError
        typer.echo(f"Error: {profile_path} or {jobs_file} has an unexpected shape: {exc}", err=True)
        raise typer.Exit(1) from exc

    if profile.username.lower() != username.lower():  # a copied or renamed profile would be shown under the wrong name
        typer.echo(f"Error: {profile_path} is the profile of {profile.username!r}, not {username!r}", err=True)
        raise typer.Exit(1)
    return profile, jobs


@app.command("match")
def match(
    username: Annotated[str, typer.Argument(help="GitHub username with a profile from `hnradar fingerprint`.")],
    jobs_file: Annotated[Path, typer.Option("--jobs", help="Jobs file from `hnradar fetch-hiring`.")] = Path("data/jobs.json"),
    profiles_dir: Annotated[Path, typer.Option(help="Directory holding <user>.json.")] = Path("data/profiles"),
    as_json: Annotated[bool, typer.Option("--json", help="Print the result as JSON instead of a table.")] = False,
) -> None:
    """Show the top 3 jobs for a user, scored deterministically from their profile."""
    profile, jobs = _load_profile_and_jobs(username, jobs_file, profiles_dir)
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


def _write_text_atomic(path: Path, text: str) -> None:
    """Write to a temp file, then rename, so a failed write never destroys a saved result."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _model_client(model: str) -> ollama.OllamaClient:
    return ollama.OllamaClient(model)


@app.command("pulse")
def pulse(
    story_id: Annotated[int, typer.Argument(min=1, help="HN id of a Show HN story (see `hnradar showhn`).")],
    model: Annotated[str, typer.Option(help="Ollama model that groups the comments.")] = ollama.DEFAULT_MODEL,
    out: Annotated[Path | None, typer.Option(help="Output file (default data/pulse/STORY_ID.json).")] = None,
) -> None:
    """Group a story's top-level comments into pros, bugs and asks; every item cites its comment ids."""
    try:
        item = hn.fetch_item(story_id)
        if not isinstance(item, dict) or item.get("type") != "story" or not isinstance(item.get("children", []), list):
            raise ValueError(f"{story_id} is not a story")
        title = item.get("title") if isinstance(item.get("title"), str) else ""
        children = item.get("children") or []
        result = cluster(story_id, title, [c for c in children if not isinstance(c, dict) or c.get("text")], _model_client(model))
    except (ValueError, PulseError, ollama.ModelError, hn.HNError, httpx.HTTPError) as exc:
        typer.echo(f"Error: {_terminal_safe(str(exc))}", err=True)
        raise typer.Exit(1) from exc

    if result is None:
        typer.echo(f"Not enough signal: fewer than {MIN_COMMENTS} comments with text. Nothing written.")
        return

    target = out if out is not None else Path("data/pulse") / f"{story_id}.json"
    try:
        _write_text_atomic(target, result.model_dump_json(indent=2) + "\n")
    except OSError as exc:  # out is a directory, read-only, or its parent is a file
        typer.echo(f"Error: could not write {target}: {_terminal_safe(str(exc))}", err=True)
        raise typer.Exit(1) from exc

    typer.echo(f'Pulse for "{_terminal_safe(result.title)}" ({result.model}): {len(result.items)} items, written to {target}')
    if result.comments_used < result.comments_total:
        typer.echo(f"Used {result.comments_used} of {result.comments_total} comments (longest first); the rest were not sent.")
    for kind in ("bug", "ask", "pro"):
        for it in (i for i in result.items if i.kind == kind):
            ids = ", ".join(str(c) for c in it.comment_ids)
            typer.echo(f"{kind:<3} | {_terminal_safe(it.summary)} | comments {ids}")


def _echo_draft(drafted) -> None:
    """The draft, then the evidence under it. Shared by `draft` and `radar`."""
    typer.echo(f"Draft for {_terminal_safe(drafted.company)} ({drafted.model}), using your repo {_terminal_safe(drafted.repo)}:")
    typer.echo("")
    for line in drafted.lines:
        typer.echo(_terminal_safe(line.text))
    typer.echo("")
    typer.echo("Evidence (read it before you send anything; nothing is sent):")
    for n, line in enumerate(drafted.lines, start=1):
        if line.comment_ids:
            typer.echo(f"line {n} cites comment {', '.join(str(i) for i in line.comment_ids)}")
    typer.echo(f"repo {_terminal_safe(drafted.repo)} | {_terminal_safe(drafted.repo_language) or '-'} | {_terminal_safe(drafted.repo_description) or 'no description'}")
    for e in drafted.evidence:
        typer.echo(f"comment {e.comment_id} | {_terminal_safe(e.text)} | {e.url}")
    typer.echo(f"Show HN: https://news.ycombinator.com/item?id={drafted.story_id} (links to {drafted.domain})")


@app.command("draft")
def draft(
    username: Annotated[str, typer.Argument(help="GitHub username with a profile from `hnradar fingerprint`.")],
    model: Annotated[str, typer.Option(help="Ollama model that writes the draft.")] = ollama.DEFAULT_MODEL,
    jobs_file: Annotated[Path, typer.Option("--jobs", help="Jobs file from `hnradar fetch-hiring`.")] = Path("data/jobs.json"),
    profiles_dir: Annotated[Path, typer.Option(help="Directory holding <user>.json.")] = Path("data/profiles"),
    pulse_dir: Annotated[Path, typer.Option(help="Directory holding saved pulses (`hnradar pulse`).")] = Path("data/pulse"),
) -> None:
    """Write a 5-line outreach note to the best-matched company, citing one user-reported problem.

    Only a company with a Show HN that links to its own domain is used (a platform domain is skipped), and
    the note says "a Show HN that links to ..." because the company may not have posted it. The cited
    comments are printed under the note: read them before you send anything. Nothing is sent.
    """
    profile, jobs = _load_profile_and_jobs(username, jobs_file, profiles_dir)
    skipped = []
    cut = False
    try:
        client = _model_client(model)
        matches = top_matches(profile, jobs, limit=DRAFT_MATCHES + 1)  # one extra tells "10 tried" from "all tried"
        cut = len(matches) > DRAFT_MATCHES
        for m in matches[:DRAFT_MATCHES]:
            company = m.job.company
            repo = repo_for_match(profile, m)
            if repo is None:
                skipped.append(f"{company}: no matched repo")
                continue
            lookup = find_story(company, m.job.url)  # a platform domain is skipped before any request
            if lookup.story is None:
                skipped.append(f"{company}: {lookup.skip}")
                continue
            story, domain = lookup.story, lookup.domain
            children = story_children(story.story_id)
            if children is None:
                skipped.append(f"{company}: {story.story_id} is not a story")
                continue
            bug = cited_bug(story.story_id, children, pulse_dir, client)
            if bug is None:
                skipped.append(f"{company}: no cited bug in the Show HN comments")
                continue
            summary, cited = bug
            drafted = write_draft(
                username=profile.username, company=company, domain=domain, story_id=story.story_id,
                repo={"name": repo.name, "language": repo.language, "description": repo.description},
                problem=summary, cited=cited, client=client,
            )
            break
        else:
            drafted = None
    except (ValueError, PulseError, ollama.ModelError, hn.HNError, httpx.HTTPError, OSError) as exc:
        typer.echo(f"Error: {_terminal_safe(str(exc))}", err=True)
        raise typer.Exit(1) from exc

    if drafted is None:
        typer.echo(f"No cited problem found for {_terminal_safe(profile.username)}. No draft written.")
        for line in skipped:
            typer.echo(f"- {_terminal_safe(line)}")
        if cut:  # rule 6: a cut search is not a complete one
            typer.echo(f"Only the {DRAFT_MATCHES} best matches were checked; lower-ranked matches were not.")
        return

    _echo_draft(drafted)


@app.command("radar")
def radar(
    username: Annotated[str, typer.Argument(help="Public GitHub username.")],
    model: Annotated[str, typer.Option(help="Ollama model for the pulse and the drafts.")] = ollama.DEFAULT_MODEL,
    jobs_file: Annotated[Path, typer.Option("--jobs", help="Jobs file from `hnradar fetch-hiring`.")] = Path("data/jobs.json"),
    profiles_dir: Annotated[Path, typer.Option(help="Directory for <user>.json (the saved profile).")] = Path("data/profiles"),
    pulse_dir: Annotated[Path, typer.Option(help="Directory holding saved pulses (`hnradar pulse`).")] = Path("data/pulse"),
    out: Annotated[Path | None, typer.Option(help="Also write the full result (JSON) to this file. It is about one person: keep it private.")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print the result as JSON instead of text.")] = False,
) -> None:
    """From a GitHub username to the top 3 companies, each with fit reasons, a cited problem and a draft.

    Runs fingerprint, match, Show HN, pulse and draft in order, one progress line per step on stderr. A step that
    fails stops the run with an error naming it; nothing partial is shown as complete. A company with no Show HN
    that links to its domain, or no cited problem, is listed with the reason and gets no draft. Nothing is sent.
    """
    if not github.USERNAME_RE.fullmatch(username):
        typer.echo(f"Error: {username!r} is not a valid GitHub username", err=True)
        raise typer.Exit(1)
    raw_jobs = _read_json_file(jobs_file, "jobs file", "Run `hnradar fetch-hiring` first.")
    try:
        jobs = _parse_jobs(raw_jobs)
    except ValueError as exc:
        typer.echo(f"Error: {jobs_file} has an unexpected shape: {exc}", err=True)
        raise typer.Exit(1) from exc

    counter = {"n": 0}

    def progress(step) -> None:
        counter["n"] += 1
        line = f"[{counter['n']}/{len(radar_mod.STEP_NAMES)}] {step.name}: {step.status} ({step.seconds}s)"
        typer.echo(line + (f" - {_terminal_safe(step.message)}" if step.message else ""), err=True)

    result = radar_mod.run_radar(
        username, jobs, profiles_dir=profiles_dir, pulse_dir=pulse_dir, client=_model_client(model), progress=progress
    )

    if out is not None:
        try:
            _write_text_atomic(out, result.model_dump_json(indent=2) + "\n")
        except OSError as exc:
            typer.echo(f"Error: could not write {out}: {_terminal_safe(str(exc))}", err=True)
            raise typer.Exit(1) from exc
    if result.status == "failed":
        typer.echo(f"Error: radar stopped at step '{result.failed_step}': {_terminal_safe(result.error or '')}. No result is shown.", err=True)
        raise typer.Exit(1)
    if as_json:
        typer.echo(json.dumps(result.model_dump(), indent=2, ensure_ascii=True))
        return
    _echo_radar(result)


def _echo_radar(result) -> None:
    if result.status == "no_matches":
        for line in result.warnings:
            typer.echo(_terminal_safe(line))
        return
    typer.echo(f"Radar for {_terminal_safe(result.username)}: top {len(result.companies)} companies ({result.status}).")
    for c in result.companies:
        typer.echo("")
        typer.echo(f"{c.rank}. {_terminal_safe(c.company)} | {_terminal_safe(c.role) or '-'} | fit {c.fit_score}/100")
        for reason in c.fit_reasons:
            typer.echo(f"   - {_terminal_safe(reason)}")
        if c.job_url:
            typer.echo(f"   {_terminal_safe(c.job_url)}")
        if c.show_hn is not None:
            typer.echo(f"   Show HN that links to {c.show_hn.domain}: {_terminal_safe(c.show_hn.title)} ({c.show_hn.url})")
        if c.problem is not None:
            ids = ", ".join(str(i) for i in c.problem.comment_ids)
            typer.echo(f"   Users reported: {_terminal_safe(c.problem.summary)} (comments {ids})")
        if c.note:
            typer.echo(f"   Note: {_terminal_safe(c.note)}")
        if c.draft is not None:
            typer.echo("")
            _echo_draft(c.draft)
    for line in result.warnings:
        typer.echo("")
        typer.echo(_terminal_safe(line))
