"""Make the shared cases for the browser port of fingerprint and match (AGENTS.md site rule S6).

The expected answers come from the Python reference (`hnradar.fingerprint.build_profile`, `hnradar.match`), never
from the JavaScript. `site/careers/hn-fit.js` must give the same answers (`site/tests/hn-fit.test.js`), and
`tests/test_fit_golden.py` fails when the committed file no longer matches what this script makes. After a change to
either side:

    uv run python site/tools/make_fit_golden.py
"""

import json
import sys
from pathlib import Path

from hnradar.fingerprint import build_profile
from hnradar.match import score_job, top_matches
from hnradar.models import Job

OUT = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "fit_golden.json"


def repo(name, language=None, stars=0, topics=(), size=100, fork=False, description=None):
    return {"name": name, "language": language, "stargazers_count": stars, "topics": list(topics), "size": size,
            "fork": fork, "description": description}


def job(cid, company, stack=(), remote=None, role="Dev", url=None):
    return Job(comment_id=cid, company=company, role=role, remote=remote, stack=list(stack), url=url, raw_text=company).model_dump(mode="json")


def profile_cases() -> list[dict]:
    many_languages = [repo(f"r{i}", language=f"Lang{i}", size=100 - i) for i in range(8)]  # 8 languages, only 5 kept
    many_topics = [repo("t", topics=[f"topic{i:02d}" for i in range(14)])]
    many_repos = [repo(f"repo{i:02d}", language="Python", stars=i % 3) for i in range(9)]  # stars tie, name decides
    inputs = {
        "empty": [],
        "forks_only": [repo("f", "Python", 99, ["x"], fork=True)],
        "forks_are_excluded_everywhere": [repo("mine", "Go", 1, ["cli"]), repo("theirs", "Rust", 500, ["cli", "rust"], fork=True)],
        "language_size_is_summed_and_ties_go_by_name": [repo("a", "Python", size=50), repo("b", "Python", size=50), repo("c", "Go", size=100), repo("d", "Ada", size=100)],
        "only_five_languages": many_languages,
        "only_ten_topics_count_then_name": many_topics + [repo("u", topics=["topic13", "topic12"])],
        "only_five_notable_repos_stars_then_name": many_repos,
        "missing_fields": [{"name": "bare"}, {"name": "nulls", "language": None, "size": None, "topics": None, "stargazers_count": None, "description": None}],
        "description_and_null_language": [repo("d", None, 3, description="A job queue"), repo("e", "Python", 3, description=None)],
        "no_language_means_no_size": [repo("z", None, size=9999), repo("y", "Zig", size=1)],
    }
    return [{"name": name, "username": "octo", "repos": repos, "profile": build_profile("octo", repos).model_dump(mode="json")} for name, repos in inputs.items()]


def _profile(languages=(), topics=(), repos=()):
    return build_profile("octo", [repo(n, lang, 1) for n, lang in repos] + [repo(f"t{i}", None, 0, [t]) for i, t in enumerate(topics)] + [repo(f"l{i}", lang, 0, size=1000 - i) for i, lang in enumerate(languages)])


def score_cases() -> list[dict]:
    py_go = _profile(languages=["Python", "Go"], repos=[("fastq", "Python")], topics=["docker", "cli"])
    rusty = build_profile("octo", [repo(f"p{i}", "Python", 10 - i) for i in range(6)] + [repo("tiny", "Rust", 0)])  # Rust is not in the top 5 repos
    five = _profile(languages=["Python", "Go", "Rust", "Java", "Kotlin"], topics=["docker"])
    jobs = {
        "no_overlap": (py_go, job(1, "A", ["COBOL"])),
        "empty_stack": (py_go, job(1, "A", [])),
        "language_and_repo_reason": (py_go, job(2, "B", ["Python"])),
        "language_beats_topic_of_the_same_name": (_profile(languages=["Go"], topics=["go"]), job(3, "C", ["Go"])),
        "topic_only": (py_go, job(4, "D", ["Docker"])),
        "aliases_golang_k8s_postgres": (_profile(languages=["Go"], topics=["kubernetes", "postgresql"]), job(5, "E", ["golang", "k8s", "Postgres"])),
        "next_js_and_symbols": (_profile(languages=["C++", "C#"], topics=["nextjs"]), job(6, "F", ["C++", "C#", "Next.js"])),
        "duplicate_terms_count_once": (_profile(languages=["JavaScript"], topics=["react"]), job(7, "G", ["JS", "javascript", "React", "ReactJS"])),
        "remote_bonus_and_suffix": (py_go, job(8, "H", ["Python", "Docker"], remote=True)),
        "remote_false_and_none_give_no_bonus": (py_go, job(9, "I", ["Python"], remote=False)),
        "score_is_capped_at_100": (five, job(10, "J", ["Python", "Go", "Rust", "Java", "Kotlin", "Docker"], remote=True)),
        "a_matched_language_without_a_notable_repo_has_no_repo_reason": (rusty, job(11, "K", ["Rust"])),
        "blank_terms_are_ignored": (py_go, job(12, "L", ["", "!!!", "Python"])),
        "two_languages_two_reasons": (py_go, job(13, "M", ["Go", "Python"])),
    }
    cases = []
    for name, (profile, j) in jobs.items():
        match = score_job(profile, Job.model_validate(j))
        cases.append({"name": name, "profile": profile.model_dump(mode="json"), "job": j, "expected": match.model_dump(mode="json") if match else None})
    return cases


def top_cases() -> list[dict]:
    profile = _profile(languages=["Python", "Go"], topics=["docker"], repos=[("fastq", "Python")])
    sets = {
        "empty_jobs": ([], 3),
        "no_strong_matches": ([job(1, "A", ["COBOL"]), job(2, "B", [])], 3),
        "best_first_and_ranks": ([job(1, "Low", ["Docker"]), job(2, "High", ["Python", "Go"]), job(3, "Mid", ["Python"])], 3),
        "ties_go_to_the_newer_post": ([job(10, "Old", ["Python"]), job(30, "New", ["Python"]), job(20, "Mid", ["Python"])], 3),
        "limit_cuts_the_list": ([job(i, f"Co{i}", ["Python"]) for i in range(1, 8)], 3),
        "limit_larger_than_matches": ([job(1, "A", ["Python"]), job(2, "B", ["COBOL"])], 10),
        "limit_zero": ([job(1, "A", ["Python"])], 0),
        "remote_changes_the_order": ([job(5, "Onsite", ["Python"], remote=False), job(4, "Remote", ["Python"], remote=True)], 3),
    }
    cases = []
    for name, (jobs, limit) in sets.items():
        expected = top_matches(profile, [Job.model_validate(j) for j in jobs], limit)
        cases.append({"name": name, "profile": profile.model_dump(mode="json"), "jobs": jobs, "limit": limit,
                      "expected": [m.model_dump(mode="json") for m in expected]})
    return cases


def make() -> dict:
    return {"profile_cases": profile_cases(), "score_cases": score_cases(), "top_cases": top_cases()}


def render() -> str:
    return json.dumps(make(), indent=1, ensure_ascii=True) + "\n"


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(), encoding="utf-8", newline="\n")
    print(f"Wrote {OUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
