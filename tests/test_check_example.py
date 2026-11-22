"""The example in site/careers/check.html must be something hnradar itself could produce (Qodo review of PR 59)."""

import json
import re
from pathlib import Path

from hnradar.draft.schema import ModelDraft
from hnradar.draft.write import _check
from hnradar.match import top_matches
from hnradar.models import Job, Language, Profile, Repo
from hnradar.radar import RadarResult

PAGE = Path(__file__).resolve().parent.parent / "site" / "careers" / "check.html"


def example() -> dict:
    html = PAGE.read_text(encoding="utf-8")
    block = re.search(r'<script type="application/json" id="ck-example-data">(.*?)</script>', html, re.S)
    assert block, "the example data block is missing from check.html"
    return json.loads(block.group(1))


def test_example_is_a_valid_radar_result():
    result = RadarResult.model_validate(example())
    assert [c.rank for c in result.companies] == [1, 2, 3]
    assert [c.draft_status for c in result.companies].count("written") == 1


def test_example_draft_passes_the_tools_own_draft_check():
    drafted = next(c.draft for c in RadarResult.model_validate(example()).companies if c.draft)
    cited = {e.comment_id for e in drafted.evidence}
    _check(ModelDraft.model_validate({"lines": [line.model_dump() for line in drafted.lines]}), cited, drafted.repo, drafted.domain)


# the three real posts the example uses (stack and remote as hnradar parsed them), and the made-up developer
JOBS = [
    Job(comment_id=50341345, company="SadServers", role="Software Engineer", remote=True, stack=["Django", "Python", "React"],
        url="https://sadservers.com", raw_text="SadServers"),
    Job(comment_id=50365279, company="Search Atlas", role="Product Engineer (PM)", remote=True,
        stack=["Python", "SQL", "Django", "React", "Next.js"], url="https://careers.searchatlas.com/jobs/599248-product-engineer-pm", raw_text="Search Atlas"),
    Job(comment_id=50451710, company="RAIDS AI", role="Senior Technical Project Manager (Full Stack)", remote=True,
        stack=["Python", "Django", "FastAPI", "React", "TypeScript"], url="https://www.linkedin.com/jobs/view/4483409995/", raw_text="RAIDS AI"),
]


def test_example_scores_ranks_and_reasons_are_what_the_matcher_produces():
    result = RadarResult.model_validate(example())
    summary = result.profile_summary
    drafted = next(c.draft for c in result.companies if c.draft)
    assert summary.notable_repos == [drafted.repo]
    profile = Profile(
        username=result.username,
        languages=[Language(name=name, size_kb=100) for name in summary.languages],
        topics=summary.topics,
        notable_repos=[Repo(name=drafted.repo, stars=1, language=drafted.repo_language)],  # the only repo the example gives a language for
    )
    expected = top_matches(profile, JOBS)
    shown = [(c.rank, c.job_comment_id, c.fit_score, c.fit_reasons) for c in result.companies]
    assert shown == [(m.rank, m.job.comment_id, m.score, [r.text for r in m.reasons]) for m in expected]
