"""Score jobs against a Profile (pure functions, no network, no model)."""

import heapq
import re
from collections.abc import Iterable

from hnradar.models import Job, Match, MatchJob, MatchReason, Profile

TOP_MATCHES = 3
LANGUAGE_POINTS = 20
TOPIC_POINTS = 10
REMOTE_POINTS = 5  # a small bonus; it never makes a job without any stack overlap a match
MAX_SCORE = 100


def _norm(term: str) -> str:
    """'Next.js' and 'nextjs' are the same term; 'C++' and 'C#' keep their symbol."""
    return re.sub(r"[^a-z0-9+#]", "", term.lower())


def score_job(profile: Profile, job: Job) -> Match | None:
    """None when the job's stack shares nothing with the profile. Each stack term counts once
    (a language wins over a topic of the same name)."""
    languages = {_norm(lang.name): lang.name for lang in profile.languages if _norm(lang.name)}
    topics = {_norm(topic): topic for topic in profile.topics if _norm(topic)}

    seen: set[str] = set()
    matched_languages: list[str] = []
    matched_topics: list[str] = []
    for term in job.stack:
        key = _norm(term)
        if not key or key in seen:
            continue
        seen.add(key)
        if key in languages:
            matched_languages.append(languages[key])
        elif key in topics:
            matched_topics.append(topics[key])
    if not matched_languages and not matched_topics:
        return None

    reasons = [
        MatchReason(kind="language", text=f"Your {name} work matches this job's stack", evidence=name)
        for name in matched_languages
    ]
    repo = next(
        (r for r in profile.notable_repos if r.language and r.language in matched_languages), None
    )
    if repo:
        reasons.append(MatchReason(kind="repo", text=f"Your repo {repo.name} is written in {repo.language}", evidence=repo.name))
    reasons += [
        MatchReason(kind="topic", text=f"Your repos use the topic {name}", evidence=name) for name in matched_topics
    ]

    score = LANGUAGE_POINTS * len(matched_languages) + TOPIC_POINTS * len(matched_topics)
    if job.remote:
        score += REMOTE_POINTS
        # Not a reason of its own: every reason must name something from the profile, and "remote" names nothing.
        reasons[0] = reasons[0].model_copy(update={"text": reasons[0].text + " (the post says remote)"})

    return Match(
        rank=0,
        score=min(score, MAX_SCORE),
        job=MatchJob(comment_id=job.comment_id, company=job.company, role=job.role, remote=job.remote, url=job.url),
        reasons=reasons,
    )


def top_matches(profile: Profile, jobs: Iterable[Job], limit: int = TOP_MATCHES) -> list[Match]:
    """Best first; ties go to the newer post (higher comment_id). Fewer than `limit` is fine, and an
    empty list means "no strong matches" - never a filler top 3."""
    scored = (m for m in (score_job(profile, job) for job in jobs) if m)  # a stream: only `limit` matches are kept
    best = heapq.nlargest(limit, scored, key=lambda m: (m.score, m.job.comment_id))
    return [m.model_copy(update={"rank": i}) for i, m in enumerate(best, start=1)]
