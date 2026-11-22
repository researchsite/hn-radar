"""`hnradar radar USERNAME`: fingerprint, match, Show HN, pulse and draft in order for the top 3 companies (issue 15).

The steps are the same functions the single commands use (`fetch_repos`/`build_profile`, `top_matches`, `find_story`,
`get_pulse`, `write_draft`); this module only runs them in order, times them and records what each company got.
A step that fails (network, model, unreadable file) stops the run: the result then has status "failed", names the
step and holds no companies, so a half-finished run is never shown as a complete one (rule 6, rule 10). "No Show HN
story", "not enough signal" and "no cited problem" are normal results, listed per company with the reason.
"""

import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from hnradar.draft.schema import DraftResult
from hnradar.draft.write import write_draft
from hnradar.match import TOP_MATCHES, top_matches
from hnradar.models import Job
from hnradar.pulse.cluster import PulseError
from hnradar.sources import github, hn, ollama
from hnradar.steps import bug_from_pulse, find_story, get_pulse, repo_for_match, save_profile, story_children

DEFAULT_DEPTH = 10  # how many of the best matches the search may look at to find `limit` drafts
STEP_NAMES = ("fingerprint", "match", "showhn", "pulse", "draft")
STEP_ERRORS = (ValueError, PulseError, ollama.ModelError, hn.HNError, github.GitHubError, httpx.HTTPError, OSError)


class RadarStep(BaseModel):
    name: Literal["fingerprint", "match", "showhn", "pulse", "draft"]
    status: Literal["ok", "skipped", "failed"]
    seconds: float = 0.0
    message: str | None = None


class RadarStory(BaseModel):
    story_id: int
    title: str
    domain: str  # the domain the story links to (rule 9: "links to", never "posted by the company")
    url: str  # https://news.ycombinator.com/item?id=<story_id>


class RadarProblem(BaseModel):
    summary: str
    comment_ids: list[int] = Field(min_length=1)  # rule 1: no problem without the comments it came from


class RadarCompany(BaseModel):
    rank: int
    company: str
    role: str | None = None
    job_comment_id: int
    job_url: str | None = None
    fit_score: int
    fit_reasons: list[str]
    show_hn: RadarStory | None = None
    pulse_status: Literal["ok", "no_show_hn", "not_enough_signal", "no_cited_problem"]
    problem: RadarProblem | None = None
    draft_status: Literal["written", "skipped"]
    draft: DraftResult | None = None
    note: str | None = None  # why there is no Show HN story, pulse problem or draft


class RadarProfile(BaseModel):
    languages: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    notable_repos: list[str] = Field(default_factory=list)


class RadarResult(BaseModel):
    username: str
    generated_at: str
    status: Literal["ok", "partial", "no_matches", "failed"]
    failed_step: str | None = None
    error: str | None = None
    steps: list[RadarStep] = Field(default_factory=list)
    profile_summary: RadarProfile | None = None
    companies: list[RadarCompany] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def failed_result(username: str, step: str, message: str) -> "RadarResult":
    """A failed run that never reached `run_radar` (for example an unreadable jobs file): same shape, same rule (no companies)."""
    return RadarResult(username=username, generated_at=_now(), status="failed", failed_step=step, error=message,
                       steps=[RadarStep(name=step, status="failed", message=message)])


class _StepFailed(Exception):
    def __init__(self, step: str, cause: BaseException, started: float):
        super().__init__(f"{step}: {cause}")
        self.step = step
        self.cause = cause
        self.started = started


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run_radar(
    username: str,
    jobs: list[Job],
    *,
    profiles_dir: Path,
    pulse_dir: Path,
    client,
    fetch_repos: Callable[..., list[dict]] = github.fetch_repos,
    search: Callable[..., dict] = hn.search_show_hn,
    fetch_item: Callable[..., dict] = hn.fetch_item,
    progress: Callable[[RadarStep], None] = lambda step: None,
    clock: Callable[[], float] = time.monotonic,
    limit: int = TOP_MATCHES,
    depth: int = DEFAULT_DEPTH,
) -> RadarResult:
    steps: list[RadarStep] = []

    def record(name: str, status: str, started: float, message: str | None = None) -> None:
        step = RadarStep(name=name, status=status, seconds=round(clock() - started, 2), message=message)
        steps.append(step)
        progress(step)

    def failed(name: str, exc: BaseException, started: float) -> RadarResult:
        record(name, "failed", started, str(exc))
        return RadarResult(username=username, generated_at=_now(), status="failed", failed_step=name, error=str(exc), steps=steps)

    def skip_rest(from_name: str, why: str) -> None:
        for name in STEP_NAMES[STEP_NAMES.index(from_name):]:
            record(name, "skipped", clock(), why)

    # 1. fingerprint
    t = clock()
    try:
        profile, _path, n_repos = save_profile(username, profiles_dir, fetch_repos)
    except STEP_ERRORS as exc:
        return failed("fingerprint", exc, t)
    summary = RadarProfile(
        languages=[x.name for x in profile.languages], topics=list(profile.topics), notable_repos=[r.name for r in profile.notable_repos]
    )
    record("fingerprint", "ok", t, f"{n_repos} public repos")
    if not n_repos:
        skip_rest("match", "no public repos")
        return RadarResult(username=profile.username, generated_at=_now(), status="no_matches", steps=steps, profile_summary=summary,
                           warnings=[f"{profile.username} has no public repos, so there is nothing to match."])

    # 2. match: as many matches as the search may look at (depth); the stages below take them in rank order
    t = clock()
    try:
        matches = top_matches(profile, jobs, depth)  # depth is a hard cap: a smaller depth than limit means fewer drafts, not more lookups
    except STEP_ERRORS as exc:
        return failed("match", exc, t)
    record("match", "ok", t, f"{len(matches)} of {len(jobs)} jobs")
    if not matches:
        skip_rest("showhn", "no strong matches")
        return RadarResult(username=profile.username, generated_at=_now(), status="no_matches", steps=steps, profile_summary=summary,
                           warnings=[f"No strong matches for {profile.username} in {len(jobs)} jobs."])

    # 3-5. Show HN, pulse and draft, a batch of matches at a time: the next batch is only looked at while fewer than
    # `limit` drafts exist, so a run that finds its drafts in the top matches does no extra work.
    companies: list[RadarCompany] = []
    spent = {"showhn": 0.0, "pulse": 0.0, "draft": 0.0}
    counts = {"story": 0, "bug": 0, "draft": 0}

    def run_batch(batch: list) -> None:
        by_rank = {m.rank: (RadarCompany(
            rank=m.rank, company=m.job.company, role=m.job.role, job_comment_id=m.job.comment_id, job_url=m.job.url,
            fit_score=m.score, fit_reasons=[r.text for r in m.reasons], pulse_status="no_show_hn", draft_status="skipped"), m) for m in batch}
        companies.extend(c for c, _m in by_rank.values())

        # Show HN: the story that links to each job link's own domain
        t0 = clock()
        lookups = {}
        try:
            for rank, (c, m) in by_rank.items():
                lookups[rank] = find_story(m.job.company, m.job.url, search)
        except STEP_ERRORS as exc:
            raise _StepFailed("showhn", exc, t0) from exc
        for rank, (c, m) in by_rank.items():
            look = lookups[rank]
            if look.story is None:
                c.note = look.skip
            else:
                c.show_hn = RadarStory(story_id=look.story.story_id, title=look.story.title, domain=look.domain,
                                       url=f"https://news.ycombinator.com/item?id={look.story.story_id}")
        with_story = [r for r in by_rank if by_rank[r][0].show_hn is not None]
        counts["story"] += len(with_story)
        spent["showhn"] += clock() - t0

        # pulse: the saved pulse of the story, or a fresh one; the first bug whose comments are all in the thread
        t0 = clock()
        bugs: dict[int, tuple[str, list[dict]]] = {}
        try:
            for rank in with_story:
                c = by_rank[rank][0]
                children = story_children(c.show_hn.story_id, fetch_item)
                if children is None:
                    c.pulse_status, c.note = "not_enough_signal", f"{c.show_hn.story_id} is not a story"
                    continue
                result = get_pulse(c.show_hn.story_id, children, pulse_dir, client)
                if result is None:
                    c.pulse_status, c.note = "not_enough_signal", "too few comments with text to say what people think"
                    continue
                bug = bug_from_pulse(result, children)
                if bug is None:
                    c.pulse_status, c.note = "no_cited_problem", "no problem the users reported is backed by comments in the thread"
                    continue
                c.pulse_status = "ok"
                c.problem = RadarProblem(summary=bug[0], comment_ids=[x["id"] for x in bug[1]])
                bugs[rank] = bug
        except STEP_ERRORS as exc:
            raise _StepFailed("pulse", exc, t0) from exc
        counts["bug"] += len(bugs)
        spent["pulse"] += clock() - t0

        # draft: one note per company that has a cited problem and a repo the match names
        t0 = clock()
        try:
            for rank, bug in bugs.items():
                c, m = by_rank[rank]
                repo = repo_for_match(profile, m)
                if repo is None:
                    c.note = "no repo of yours is named by the match, so there is nothing to offer"
                    continue
                drafted = write_draft(
                    username=profile.username, company=c.company, domain=c.show_hn.domain, story_id=c.show_hn.story_id,
                    repo={"name": repo.name, "language": repo.language, "description": repo.description},
                    problem=bug[0], cited=bug[1], client=client,
                )
                c.draft, c.draft_status = drafted, "written"
                counts["draft"] += 1
        except STEP_ERRORS as exc:
            raise _StepFailed("draft", exc, t0) from exc
        spent["draft"] += clock() - t0

    def record_total(name: str) -> None:
        text = {
            "showhn": f"{counts['story']} of {len(companies)} companies have a Show HN that links to their domain",
            "pulse": f"{counts['bug']} of {counts['story']} threads have a cited problem",
            "draft": f"{counts['draft']} of {counts['bug']} drafts written",
        }[name]
        ok = {"showhn": counts["story"], "pulse": counts["bug"], "draft": counts["draft"]}[name] > 0
        step = RadarStep(name=name, status="ok" if ok else "skipped", seconds=round(spent[name], 2), message=text)
        steps.append(step)
        progress(step)

    examined = 0
    try:
        while examined < len(matches) and counts["draft"] < limit:
            batch = matches[examined:examined + (limit - counts["draft"])]
            examined += len(batch)
            run_batch(batch)
    except _StepFailed as exc:
        for name in ("showhn", "pulse", "draft")[: ("showhn", "pulse", "draft").index(exc.step)]:  # the stages that finished
            record_total(name)
        return failed(exc.step, exc.cause, exc.started - spent[exc.step])  # the failed step's time covers its whole stage

    for name in ("showhn", "pulse", "draft"):
        record_total(name)

    written = counts["draft"]
    status = "ok" if written == len(companies) else "partial"
    warnings = [] if status == "ok" else [f"{len(companies) - written} of {len(companies)} companies have no draft: see each company's note."]
    if written < limit and len(matches) == depth:  # rule 6: a cut search is not a complete one
        warnings.append(f"Only the {len(matches)} best matches were checked (depth {depth}); lower-ranked matches were not.")
    return RadarResult(username=profile.username, generated_at=_now(), status=status, steps=steps, profile_summary=summary,
                       companies=companies, warnings=warnings)
