"""Combine the Show HN and pulse data of every job into one public file for the browser (issue 56).

The page that matches a visitor in the browser must not ask for one company's file: the request would tell the site
which companies matched. So this builds ONE file, `site/careers/data/signals.json`, with an entry for every job, and
the page downloads all of it whatever the visitor's profile is.

An entry holds what `hnradar radar` would find for that job, taken from the saved Show HN files (`showhn/<id>.json`,
made by build_showhn_data.py) and the saved pulses (`pulse/<story_id>.json`, made by build_pulse_data.py), with the
same rules: the story is the first one that links to the job link's own domain (`steps.story_from_result`), a job
board is skipped, and a problem is shown only if every comment it cites is in the thread (`steps.bug_from_pulse`).
No model runs here and nothing is invented: a job with a story but no saved pulse says so ("no_pulse").

    uv run python site/tools/build_signals_data.py

A job with no current Show HN file gets no entry (the page says it was not checked, never "no Show HN"). A thread that
cannot be fetched fails the run: nothing is written (a failed request is not a complete file).
"""

import argparse
import functools
import importlib.util
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from hnradar.cli import _terminal_safe, _write_text_atomic
from hnradar.models import Job, ShowHNResult
from hnradar.pulse.cluster import MIN_COMMENTS, PulseError
from hnradar.pulse.schema import PulseResult
from hnradar.showhn import is_platform_domain, url_domain
from hnradar.sources import hn
from hnradar.steps import bug_from_pulse, story_children, story_from_result

SITE = Path(__file__).resolve().parents[1]
DATA = SITE / "careers" / "data"
DEFAULT_JOBS = DATA / "jobs.json"
DEFAULT_SHOWHN = DATA / "showhn"
DEFAULT_PULSE = DATA / "pulse"
DEFAULT_OUT = DATA / "signals.json"
EVIDENCE_CHARS = 400  # a longer comment is cut to this many characters, the last one being the "…" that shows it was cut
CUT = "…"


class SignalsBuildError(Exception):
    pass


class SignalStory(BaseModel):
    model_config = ConfigDict(strict=True)  # no coercion: the browser checks this file with the same rules (hn-fit.js checkSignals)
    story_id: int
    title: str
    domain: str  # the domain the story links to (AGENTS.md rule 9: "links to", never "posted by the company")
    url: str


class SignalProblem(BaseModel):
    model_config = ConfigDict(strict=True)  # no coercion: the browser checks this file with the same rules (hn-fit.js checkSignals)
    summary: str
    comment_ids: list[int] = Field(min_length=1)


class SignalEvidence(BaseModel):
    model_config = ConfigDict(strict=True)  # no coercion: the browser checks this file with the same rules (hn-fit.js checkSignals)
    comment_id: int
    text: str
    url: str


class Signal(BaseModel):
    model_config = ConfigDict(strict=True)  # no coercion: the browser checks this file with the same rules (hn-fit.js checkSignals)
    status: Literal["no_show_hn", "no_pulse", "not_enough_signal", "no_cited_problem", "ok"]
    show_hn: SignalStory | None = None
    problem: SignalProblem | None = None
    evidence: list[SignalEvidence] = Field(default_factory=list)
    note: str | None = None

    @model_validator(mode="after")
    def _consistent(self):
        if self.status == "ok":
            if self.show_hn is None or self.problem is None:
                raise ValueError("status ok needs a story and a problem")
            if {e.comment_id for e in self.evidence} != set(self.problem.comment_ids):  # rule 1: every cited comment is shown
                raise ValueError("the evidence must be exactly the comments the problem cites")
        elif self.problem is not None or self.evidence:
            raise ValueError("only status ok has a problem and evidence")
        if self.status != "no_show_hn" and self.show_hn is None:
            raise ValueError("this status needs a story")
        return self


class SignalsFile(BaseModel):
    model_config = ConfigDict(strict=True)  # no coercion: the browser checks this file with the same rules (hn-fit.js checkSignals)
    generated_at: str
    jobs: dict[str, Signal]


@functools.lru_cache(maxsize=1)
def _showhn_builder():
    """site/tools/build_showhn_data.py, loaded from its file: its job checks and its notion of a current file are used, not copied."""
    spec = importlib.util.spec_from_file_location("build_showhn_data", Path(__file__).with_name("build_showhn_data.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_jobs(path: Path) -> list[Job]:
    """The same checks as the Show HN builder (readable, valid, unique positive ids)."""
    module = _showhn_builder()
    try:
        return module.load_jobs(path)
    except module.ShowHNBuildError as exc:
        raise SignalsBuildError(str(exc)) from exc


def cut(text: str) -> str:
    return text if len(text) <= EVIDENCE_CHARS else text[: EVIDENCE_CHARS - 1] + CUT


def _read(path: Path, model, what: str):
    try:
        return model.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SignalsBuildError(f"cannot read the {what} {path}: {type(exc).__name__}") from exc


def signal_for(job: Job, showhn_dir: Path, pulse_dir: Path, fetch: Callable[..., dict] = hn.fetch_item) -> Signal | None:
    """None = not checked (no current Show HN file for this job). Raises SignalsBuildError for a broken saved file or a thread that cannot be read."""
    domain = url_domain(job.url)
    if domain is None:
        return Signal(status="no_show_hn", note="no domain in the job link")
    try:
        platform = is_platform_domain(domain)
    except ValueError:
        return Signal(status="no_show_hn", note="no domain in the job link")
    if platform:
        return Signal(status="no_show_hn", note=f"platform domain ({domain}); a story that links to it may belong to someone else")

    path = showhn_dir / f"{job.comment_id}.json"
    if not path.is_file():
        return None
    result = _read(path, ShowHNResult, "Show HN file")
    if result.domain != domain or result.query != _showhn_builder().job_query(job):  # made for another link or company: stale, so not checked rather than guessed
        return None
    look = story_from_result(domain, result)
    if look.story is None:
        return Signal(status="no_show_hn", note=look.skip)
    story = SignalStory(story_id=look.story.story_id, title=_terminal_safe(look.story.title), domain=domain,
                        url=f"https://news.ycombinator.com/item?id={look.story.story_id}")

    pulse_path = pulse_dir / f"{story.story_id}.json"
    if not pulse_path.is_file():
        if look.story.num_comments < MIN_COMMENTS:
            return Signal(status="not_enough_signal", show_hn=story)
        return Signal(status="no_pulse", show_hn=story)
    pulse = _read(pulse_path, PulseResult, "pulse")
    if pulse.story_id != story.story_id:
        raise SignalsBuildError(f"the pulse {pulse_path} is for story {pulse.story_id}, not {story.story_id}")
    try:
        children = story_children(story.story_id, fetch)
    except (hn.HNError, httpx.HTTPError, OSError, ValueError) as exc:  # a rejected or timed-out request is a controlled failure too
        raise SignalsBuildError(f"could not read the thread of story {story.story_id}: {_terminal_safe(str(exc))}") from exc
    if children is None:
        raise SignalsBuildError(f"{story.story_id} is not a story")
    bug = bug_from_pulse(pulse, children)
    if bug is None:
        return Signal(status="no_cited_problem", show_hn=story)
    summary, cited = bug
    evidence = [SignalEvidence(comment_id=c["id"], text=cut(_terminal_safe(c["text"])),
                               url=f"https://news.ycombinator.com/item?id={c['id']}") for c in cited]
    return Signal(status="ok", show_hn=story, problem=SignalProblem(summary=_terminal_safe(summary), comment_ids=[c["id"] for c in cited]),
                  evidence=evidence)


def build(jobs: list[Job], showhn_dir: Path, pulse_dir: Path, fetch: Callable[..., dict] = hn.fetch_item,
          clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> tuple[SignalsFile, int]:
    """(the file, how many jobs were not checked). A broken saved file or an unreadable thread raises."""
    entries: dict[str, Signal] = {}
    unchecked = 0
    for job in jobs:
        signal = signal_for(job, showhn_dir, pulse_dir, fetch)
        if signal is None:
            unchecked += 1
        else:
            entries[str(job.comment_id)] = signal
    return SignalsFile(generated_at=clock().isoformat(timespec="seconds"), jobs=entries), unchecked


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Combine the Show HN and pulse data of every job into one public file.")
    parser.add_argument("--jobs", type=Path, default=DEFAULT_JOBS)
    parser.add_argument("--showhn", type=Path, default=DEFAULT_SHOWHN)
    parser.add_argument("--pulse", type=Path, default=DEFAULT_PULSE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    try:
        jobs = load_jobs(args.jobs)
        signals, unchecked = build(jobs, args.showhn, args.pulse)
        _write_text_atomic(args.out, signals.model_dump_json(indent=1, exclude_none=True) + "\n")
    except (SignalsBuildError, ValidationError, OSError, PulseError) as exc:
        print(f"Error: {_terminal_safe(str(exc))}", file=sys.stderr)
        return 1
    counts: dict[str, int] = {}
    for signal in signals.jobs.values():
        counts[signal.status] = counts.get(signal.status, 0) + 1
    print(f"Wrote {args.out}: {len(signals.jobs)} of {len(jobs)} jobs ({', '.join(f'{n} {k}' for k, n in sorted(counts.items()))}); {unchecked} not checked.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
