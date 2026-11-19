import json

import httpx
import pytest
from typer.testing import CliRunner

import hnradar.cli as cli
from hnradar.cli import app

runner = CliRunner()
SEARCH_URL = "https://hn.algolia.com/api/v1/search"
STORY_ID = 4242
ITEM_URL = f"https://hn.algolia.com/api/v1/items/{STORY_ID}"
SEARCH_RESPONSE = {
    "nbHits": 1,
    "hits": [{"objectID": str(STORY_ID), "title": "Show HN: Acme", "points": 10, "num_comments": 5, "url": "https://acme.com/launch"}],
}


class FakeClient:
    name = "fake:1b"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = 0

    def complete(self, system, user, schema):
        self.calls += 1
        return self.replies.pop(0)


def lines(extra=""):
    return json.dumps(
        {
            "lines": [
                {"text": "Hello Acme, I am octo."},
                {"text": f"A Show HN that links to acme.com got a crash report.{extra}", "comment_ids": [1000]},
                {"text": "My repo fastq could help."},
                {"text": "I would start with a reproduction."},
                {"text": "Could we talk for ten minutes?"},
            ]
        }
    )


def pulse_json(kind="bug", ids=(1000,)):
    return json.dumps({"items": [{"kind": kind, "summary": "It crashes on start", "comment_ids": list(ids)}]})


def story(n=5, texts=None):
    children = [
        {"id": 1000 + i, "type": "comment", "text": (texts or {}).get(i, f"Comment {i} about it"), "children": []}
        for i in range(n)
    ]
    return {"id": STORY_ID, "type": "story", "title": "Show HN: Acme", "children": children}


def job(company="Acme", url="https://acme.com/careers", stack=("Python",), cid=1):
    return {"comment_id": cid, "company": company, "role": "Dev", "stack": list(stack), "url": url, "raw_text": company}


PROFILE = {
    "username": "octo",
    "languages": [{"name": "Python", "size_kb": 100}],
    "topics": [],
    "notable_repos": [{"name": "fastq", "description": "A job queue", "stars": 5, "language": "Python"}],
}


def saved(ids=(1000,), kind="bug"):
    return json.dumps(
        {
            "story_id": STORY_ID,
            "title": "Show HN: Acme",
            "model": "m",
            "comments_used": 5,
            "comments_total": 5,
            "items": [{"kind": kind, "summary": "It crashes on start", "comment_ids": list(ids)}],
        }
    )


@pytest.fixture
def env(respx_mock, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    class Env:
        def write(self, jobs=None, profile=PROFILE, saved_pulse=None):
            (tmp_path / "data" / "profiles").mkdir(parents=True, exist_ok=True)
            (tmp_path / "data" / "profiles" / "octo.json").write_text(json.dumps(profile), encoding="utf-8")
            (tmp_path / "data" / "jobs.json").write_text(json.dumps(jobs if jobs is not None else [job()]), encoding="utf-8")
            if saved_pulse is not None:
                (tmp_path / "data" / "pulse").mkdir(parents=True, exist_ok=True)
                (tmp_path / "data" / "pulse" / f"{STORY_ID}.json").write_text(saved_pulse, encoding="utf-8")

        def hn(self, search=SEARCH_RESPONSE, item=None, search_status=200):
            respx_mock.get(SEARCH_URL).mock(return_value=httpx.Response(search_status, json=search))
            respx_mock.get(ITEM_URL).mock(return_value=httpx.Response(200, json=item if item is not None else story()))

        def client(self, *replies):
            c = FakeClient(*replies)
            monkeypatch.setattr(cli, "_model_client", lambda model: c)
            return c

    return Env()


def test_draft_prints_five_lines_and_the_cited_evidence(env):
    env.write(saved_pulse=saved())
    env.hn()
    client = env.client(lines())

    result = runner.invoke(app, ["draft", "octo"])

    assert result.exit_code == 0, result.output
    assert client.calls == 1  # the saved pulse was used: only the draft needed the model
    out = result.output.splitlines()
    start = out.index("Hello Acme, I am octo.")
    assert out[start + 4] == "Could we talk for ten minutes?"
    assert "repo fastq | Python | A job queue" in result.output
    assert "comment 1000 | Comment 0 about it | https://news.ycombinator.com/item?id=1000" in result.output
    assert f"Show HN: https://news.ycombinator.com/item?id={STORY_ID} (links to acme.com)" in result.output
    assert "your Show HN" not in result.output


def test_draft_runs_pulse_when_none_is_saved(env):
    env.write()
    env.hn()
    client = env.client(pulse_json(), lines())

    result = runner.invoke(app, ["draft", "octo"])

    assert result.exit_code == 0, result.output
    assert client.calls == 2 and "Hello Acme" in result.output


def test_a_platform_domain_job_is_skipped_and_the_next_match_is_used(env):
    env.write(jobs=[job("Gh", "https://github.com/x", cid=5), job("Acme", cid=1)], saved_pulse=saved())
    env.hn()
    env.client(lines())

    result = runner.invoke(app, ["draft", "octo"])

    assert result.exit_code == 0, result.output
    assert "Draft for Acme" in result.output


def test_only_platform_domains_means_no_draft_and_no_model_call(env):
    env.write(jobs=[job("Gh", "https://github.com/x")])
    env.hn()
    client = env.client()

    result = runner.invoke(app, ["draft", "octo"])

    assert result.exit_code == 0
    assert "No cited problem found" in result.output and "Gh: platform domain" in result.output
    assert "Draft for" not in result.output and client.calls == 0


@pytest.mark.parametrize(
    "jobs, reason",
    [
        ([job(url=None)], "no domain in the job link"),
        ([job(stack=("Rust",))], None),  # no stack overlap: not a match at all
    ],
)
def test_jobs_without_a_usable_match_give_no_draft(env, jobs, reason):
    env.write(jobs=jobs)
    env.hn()
    env.client()

    result = runner.invoke(app, ["draft", "octo"])

    assert result.exit_code == 0 and "No draft written" in result.output
    if reason:
        assert reason in result.output


def test_a_match_without_a_repo_is_skipped(env):
    profile = {**PROFILE, "notable_repos": [], "topics": ["python"]}
    env.write(jobs=[job(stack=("python",))], profile=profile)
    env.hn()
    env.client()

    result = runner.invoke(app, ["draft", "octo"])

    assert result.exit_code == 0 and "no matched repo" in result.output


def test_no_show_hn_on_the_domain_is_skipped(env):
    other = {"nbHits": 1, "hits": [{**SEARCH_RESPONSE["hits"][0], "url": "https://elsewhere.org/x"}]}
    env.write(saved_pulse=saved())
    env.hn(search=other)
    env.client()

    result = runner.invoke(app, ["draft", "octo"])

    assert result.exit_code == 0 and "no Show HN links to acme.com" in result.output


@pytest.mark.parametrize(
    "pulse",
    [saved(kind="pro"), saved(kind="ask"), saved(ids=(9999,)), json.dumps({**json.loads(saved()), "items": []})],
)
def test_no_cited_bug_means_no_draft_not_an_invented_one(env, pulse):
    env.write(saved_pulse=pulse)
    env.hn()
    client = env.client()

    result = runner.invoke(app, ["draft", "octo"])

    assert result.exit_code == 0, result.output
    assert "No cited problem found" in result.output and "Draft for" not in result.output
    assert client.calls == 0


def test_a_thread_with_too_few_comments_gives_no_draft(env):
    env.write()
    env.hn(item=story(2))
    client = env.client()

    result = runner.invoke(app, ["draft", "octo"])

    assert result.exit_code == 0 and "No draft written" in result.output and client.calls == 0


def test_a_model_that_cites_an_unsent_comment_fails_loudly(env):
    env.write(saved_pulse=saved())
    env.hn()
    bad = lines().replace("1000", "7777")
    env.client(bad, bad)

    result = runner.invoke(app, ["draft", "octo"])

    assert result.exit_code == 1
    assert "Error:" in result.output and "Draft for" not in result.output


def test_a_draft_that_calls_the_show_hn_theirs_is_refused(env):
    env.write(saved_pulse=saved())
    env.hn()
    bad = lines(" I loved their Show HN.")
    env.client(bad, bad)

    result = runner.invoke(app, ["draft", "octo"])

    assert result.exit_code == 1 and "Draft for" not in result.output


@pytest.mark.parametrize("status", [403, 429, 500])
def test_hn_errors_exit_1(env, status):
    env.write(saved_pulse=saved())
    env.hn(search_status=status)
    env.client()

    result = runner.invoke(app, ["draft", "octo"])

    assert result.exit_code == 1 and "Error:" in result.output and "Draft for" not in result.output


def test_an_unreadable_saved_pulse_is_an_error_not_a_silent_rerun(env):
    env.write(saved_pulse="{not json")
    env.hn()
    client = env.client()

    result = runner.invoke(app, ["draft", "octo"])

    assert result.exit_code == 1 and "could not read the saved pulse" in result.output and client.calls == 0


def test_cited_text_is_made_terminal_safe(env):
    env.write(saved_pulse=saved())
    env.hn(item=story(texts={0: "boom \x1b[31mred\x1b[0m ‮evil"}))
    env.client(lines())

    result = runner.invoke(app, ["draft", "octo"])

    assert result.exit_code == 0 and "\x1b" not in result.output and "‮" not in result.output
    assert "boom [31mred [0m evil" in result.output


def test_missing_profile_and_bad_username_exit_1(env):
    env.client()
    assert runner.invoke(app, ["draft", "octo"]).exit_code == 1
    assert runner.invoke(app, ["draft", "bad name!"]).exit_code == 1


def test_help_says_nothing_is_sent():
    assert "Nothing is sent" in runner.invoke(app, ["draft", "--help"]).output.replace("\n", " ")


def test_a_story_on_a_platform_host_is_not_used_even_though_its_domain_matches(env):
    gh = {"nbHits": 1, "hits": [{**SEARCH_RESPONSE["hits"][0], "url": "https://github.com/stranger/tool"}]}
    env.write(jobs=[job("Gh", "https://github.com/jobs")], saved_pulse=saved())
    env.hn(search=gh)
    client = env.client()

    result = runner.invoke(app, ["draft", "octo"])

    assert result.exit_code == 0 and "Gh: platform domain" in result.output
    assert "Draft for" not in result.output and client.calls == 0


@pytest.mark.parametrize("platform_jobs, drafted", [(9, True), (10, False)])
def test_only_the_ten_best_matches_are_tried(env, platform_jobs, drafted):
    jobs = [job(f"P{i}", "https://github.com/x", cid=100 + i) for i in range(platform_jobs)] + [job("Acme", cid=1)]
    env.write(jobs=jobs, saved_pulse=saved())
    env.hn()
    env.client(lines())

    result = runner.invoke(app, ["draft", "octo"])

    assert result.exit_code == 0, result.output
    assert ("Draft for Acme" in result.output) is drafted
