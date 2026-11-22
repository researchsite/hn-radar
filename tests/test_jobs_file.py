"""The committed jobs file holds openings only: the browser match (site/careers/hn-fit.js) does not filter profiles."""

import json
from pathlib import Path

from hnradar.models import Job
from hnradar.parse import is_candidate_profile

JOBS = Path(__file__).resolve().parent.parent / "data" / "jobs.json"


def test_the_committed_jobs_file_has_no_candidate_profile_posts():
    jobs = [Job.model_validate(j) for j in json.loads(JOBS.read_text(encoding="utf-8"))]
    assert [j.comment_id for j in jobs if is_candidate_profile(j.raw_text)] == []
