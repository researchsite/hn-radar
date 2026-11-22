import pytest

from hnradar.match import MAX_SCORE, TOP_MATCHES, score_job, top_matches
from hnradar.models import Job, Language, Profile, Repo


def job(comment_id, stack, *, remote=None, company="Acme"):
    return Job(comment_id=comment_id, company=company, role="Engineer", remote=remote, stack=stack, raw_text="x")


PROFILE = Profile(
    username="octo",
    languages=[Language(name="Python", size_kb=900), Language(name="TypeScript", size_kb=300)],
    topics=["nextjs", "typescript", "graph-database"],
    notable_repos=[
        Repo(name="docs-site", stars=9, language="TypeScript"),
        Repo(name="radar", stars=5, language="Python"),
    ],
)


def test_language_match_names_the_language_and_a_concrete_repo():
    m = score_job(PROFILE, job(1, ["Python", "Go"]))

    kinds = {(r.kind, r.evidence) for r in m.reasons}
    assert ("language", "Python") in kinds
    assert ("repo", "radar") in kinds  # the notable repo written in the matched language
    assert m.score == 20


def test_topic_match_and_normalised_names():
    m = score_job(PROFILE, job(1, ["Next.js"]))  # topic "nextjs" is the same term as "Next.js"

    assert [(r.kind, r.evidence) for r in m.reasons] == [("topic", "nextjs")]
    assert m.score == 10


def test_a_stack_term_counts_once_and_a_language_wins_over_a_topic():
    m = score_job(PROFILE, job(1, ["TypeScript", "typescript", "TYPESCRIPT"]))

    assert [(r.kind, r.evidence) for r in m.reasons] == [("language", "TypeScript"), ("repo", "docs-site")]
    assert m.score == 20


def test_remote_is_only_a_bonus_after_an_overlap():
    assert score_job(PROFILE, job(1, ["Go"], remote=True)) is None
    assert score_job(PROFILE, job(2, ["Python"], remote=True)).score == 25
    assert score_job(PROFILE, job(3, ["Python"], remote=False)).score == 20
    assert score_job(PROFILE, job(4, ["Python"], remote=None)).score == 20


def test_remote_bonus_is_not_a_reason_of_its_own():
    m = score_job(PROFILE, job(1, ["Python"], remote=True))

    assert all(r.kind != "remote" for r in m.reasons)  # every reason names a language, repo or topic
    assert "remote" in m.reasons[0].text
    assert m.reasons[0].evidence == "Python"


def test_score_is_capped():
    many = Profile(username="x", languages=[Language(name=f"L{i}", size_kb=1) for i in range(5)])

    m = score_job(many, job(1, [f"L{i}" for i in range(5)], remote=True))

    assert m.score == MAX_SCORE


def test_symbols_in_names_are_kept():
    p = Profile(username="x", languages=[Language(name="C++", size_kb=1), Language(name="C#", size_kb=1)])

    assert score_job(p, job(1, ["C"])) is None
    assert score_job(p, job(2, ["c++"])).reasons[0].evidence == "C++"
    assert score_job(p, job(3, ["C#"])).reasons[0].evidence == "C#"


def test_top_three_best_first_and_ranked():
    jobs = [job(i, ["Python"] * (1 if i < 4 else 1) + (["TypeScript"] if i in (2, 5) else [])) for i in range(1, 7)]

    result = top_matches(PROFILE, jobs)

    assert len(result) == TOP_MATCHES == 3
    assert [m.rank for m in result] == [1, 2, 3]
    assert [m.job.comment_id for m in result] == [5, 2, 6]  # 40 points, 40 points, then newest of the 20s


def test_exactly_three_matches_gives_three_ranked_1_to_3():
    result = top_matches(PROFILE, [job(1, ["Python"]), job(2, ["Python"]), job(3, ["Python"]), job(4, ["Go"])])

    assert [m.rank for m in result] == [1, 2, 3]
    assert [m.job.comment_id for m in result] == [3, 2, 1]


def test_ties_go_to_the_newer_post():
    result = top_matches(PROFILE, [job(10, ["Python"]), job(30, ["Python"]), job(20, ["Python"])])

    assert [m.job.comment_id for m in result] == [30, 20, 10]


def test_fewer_than_three_matches_is_fine_and_no_filler():
    result = top_matches(PROFILE, [job(1, ["Python"]), job(2, ["Go"]), job(3, ["Rust"])])

    assert [m.job.comment_id for m in result] == [1]


@pytest.mark.parametrize(
    "profile, jobs",
    [
        (PROFILE, []),  # empty jobs
        (PROFILE, [job(1, [])]),  # a job with no stack
        (PROFILE, [job(1, ["Go", "Rust"], remote=True)]),  # no overlap, even though remote
        (Profile(username="empty"), [job(1, ["Python"], remote=True)]),  # no languages, no topics
    ],
)
def test_no_overlap_is_an_empty_result(profile, jobs):
    assert top_matches(profile, jobs) == []


def test_a_profile_with_topics_but_no_languages_still_matches_on_topics():
    topics_only = Profile(username="x", topics=["nextjs"])

    result = top_matches(topics_only, [job(1, ["Next.js"]), job(2, ["Go"])])

    assert [m.job.comment_id for m in result] == [1]
    assert [(r.kind, r.evidence) for r in result[0].reasons] == [("topic", "nextjs")]


@pytest.mark.parametrize(
    "job_term, profile_term",
    [("PostgreSQL", "postgres"), ("Kubernetes", "k8s"), ("Go", "golang"), ("Node.js", "node"), ("TypeScript", "ts")],
)
def test_common_aliases_match_in_both_directions(job_term, profile_term):
    as_topic = Profile(username="x", topics=[profile_term])
    as_language = Profile(username="x", languages=[Language(name=profile_term, size_kb=1)])

    assert top_matches(as_topic, [job(1, [job_term])])
    assert top_matches(as_language, [job(1, [job_term])])
    assert top_matches(Profile(username="x", topics=[job_term]), [job(1, [profile_term])])


def test_scoring_streams_jobs_instead_of_holding_a_list(monkeypatch):
    from hnradar import match

    seen = []
    real = match.heapq.nlargest

    def spy(n, iterable, key=None):
        seen.append(iterable)
        return real(n, iterable, key=key)

    monkeypatch.setattr(match.heapq, "nlargest", spy)

    top_matches(PROFILE, (job(i, ["Python"]) for i in range(1, 2000)))  # a generator in, no list of 2000 matches

    assert len(seen) == 1 and not isinstance(seen[0], (list, tuple))  # a list-based version never reaches here


def test_blank_terms_never_match():
    p = Profile(username="x", languages=[Language(name="  ", size_kb=1)], topics=["", "!!"])

    assert top_matches(p, [job(1, ["", "  ", "!!"])]) == []


def test_a_persons_looking_for_work_post_is_never_matched_as_a_job():
    person = Job(comment_id=9, company="I'm Sam Jones", stack=["Python"], remote=True,
                 raw_text="I'm Sam Jones, a full-stack engineer.\nLocation: Berlin\nRemote: Yes\nWilling to relocate: No\nTechnologies: Python")
    opening = job(1, ["Python"])
    assert [m.job.comment_id for m in top_matches(PROFILE, [person, opening])] == [1]
    assert top_matches(PROFILE, [person]) == []
