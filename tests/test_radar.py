"""Tests for `hnradar radar` (issue 15): the orchestration with fakes, then the command. No network, no model."""

import json

import pytest
from typer.testing import CliRunner

import hnradar.cli as cli
from hnradar.cli import app
from hnradar.models import Job
from hnradar.radar import STEP_NAMES, run_radar
from hnradar.sources import github, hn, ollama

runner = CliRunner()


def repo(name="fastq", language="Python", stars=5, topics=()):
    return {"name": name, "description": "A job queue", "stargazers_count": stars, "language": language, "fork": False,
            "size": 100, "topics": list(topics)}


def job(cid, company, stack=("Python",), url=None, role="Dev"):
    return {"comment_id": cid, "company": company, "role": role, "stack": list(stack),
            "url": url if url is not None else f"https://{company.lower()}.com/careers", "raw_text": company}


class FakeClient:
    """Routes by prompt: the pulse prompt starts with 'Comments:', the draft prompt with 'Facts:'."""

    name = "fake:1b"

    def __init__(self, fail_on=None):
        self.calls = {"pulse": 0, "draft": 0}
        self.fail_on = fail_on

    def complete(self, system, user, schema):
        kind = "pulse" if user.startswith("Comments:") else "draft"
        self.calls[kind] += 1
        if self.fail_on == kind:
            raise ollama.ModelError("model not found")
        if kind == "pulse":
            first = json.loads(user.removeprefix("Comments:\n"))[0]["id"]
            return json.dumps({"items": [{"kind": "bug", "summary": "It crashes on start", "comment_ids": [first]}]})
        facts = json.loads(user.removeprefix("Facts:\n"))
        cid = facts["comments"][0]["id"]
        return json.dumps({"lines": [
            {"text": f"Hello {facts['company']}, I am {facts['developer']}."},
            {"text": f"A Show HN that links to {facts['show_hn_domain']} got a crash report.", "comment_ids": [cid]},
            {"text": f"My repo {facts['repo']['name']} could help."},
            {"text": "I would start with a reproduction."},
            {"text": "Could we talk for ten minutes?"},
        ]})


class Hn:
    """Search finds one story per company on the job's own domain; story threads have `comments` comments."""

    def __init__(self, comments=5, no_story=(), fail_search_for=None, not_a_story=()):
        self.comments, self.no_story, self.fail_search_for, self.not_a_story = comments, set(no_story), fail_search_for, set(not_a_story)
        self.searches = []

    def search(self, company, *, domain=None, **_):
        self.searches.append(company)
        if company == self.fail_search_for:
            raise hn.HNError("search is down")
        if company in self.no_story:
            return {"total_hits": 0, "hits": []}
        sid = 1000 * (1 + sum(map(ord, company)) % 50)
        return {"total_hits": 1, "hits": [{"story_id": sid, "title": f"Show HN: {company}", "points": 9, "num_comments": 5, "url": f"https://{domain}/launch"}]}

    def item(self, story_id, **_):
        if story_id in self.not_a_story:
            return {"id": story_id, "type": "comment"}
        return {"id": story_id, "type": "story", "title": "t", "children": [
            {"id": story_id + 1 + i, "type": "comment", "text": f"Comment {i}", "children": []} for i in range(self.comments)]}


def go(tmp_path, jobs, *, repos=None, hn_fake=None, client=None, **kw):
    hn_fake = hn_fake or Hn()
    return run_radar(
        "octo", [Job.model_validate(j) for j in jobs], profiles_dir=tmp_path / "profiles", pulse_dir=tmp_path / "pulse",
        client=client or FakeClient(), fetch_repos=lambda u: repos if repos is not None else [repo()],
        search=hn_fake.search, fetch_item=hn_fake.item, **kw)


JOBS = [job(1, "Acme"), job(2, "Beta"), job(3, "Gamma")]


def test_happy_path_three_cited_drafts(tmp_path):
    result = go(tmp_path, JOBS)
    assert result.status == "ok" and result.failed_step is None
    assert [s.name for s in result.steps] == list(STEP_NAMES) and all(s.status == "ok" for s in result.steps)
    assert [c.rank for c in result.companies] == [1, 2, 3]
    for c in result.companies:
        assert c.draft_status == "written" and c.pulse_status == "ok"
        assert c.problem.comment_ids and c.show_hn.domain == f"{c.company.lower()}.com"
        assert c.draft.lines[1].comment_ids == c.problem.comment_ids
        assert c.fit_reasons and c.show_hn.url.endswith(str(c.show_hn.story_id))
    assert (tmp_path / "profiles" / "octo.json").exists()
    assert result.profile_summary.languages == ["Python"] and result.profile_summary.notable_repos == ["fastq"]


def test_progress_is_reported_once_per_step_in_order(tmp_path):
    seen = []
    go(tmp_path, JOBS, progress=seen.append)
    assert [s.name for s in seen] == list(STEP_NAMES)


def test_only_the_top_three_companies_are_used(tmp_path):
    jobs = [job(i, f"Co{i}") for i in range(1, 8)]
    assert len(go(tmp_path, jobs).companies) == 3


def test_a_company_without_a_show_hn_is_listed_with_the_reason_and_the_run_is_partial(tmp_path):
    jobs = [job(1, "Acme"), job(2, "Beta", url="https://jobs.ashbyhq.com/beta"), job(3, "Gamma", url=" ")]
    jobs[2]["url"] = None
    result = go(tmp_path, jobs, hn_fake=Hn(no_story={"Gamma"}))
    assert result.status == "partial" and "no draft" in result.warnings[0]
    by_name = {c.company: c for c in result.companies}
    assert by_name["Acme"].draft_status == "written"
    assert "platform domain" in by_name["Beta"].note and by_name["Beta"].show_hn is None and by_name["Beta"].pulse_status == "no_show_hn"
    assert "no domain" in by_name["Gamma"].note and by_name["Gamma"].draft is None


def test_too_few_comments_is_not_enough_signal_not_a_failure(tmp_path):
    result = go(tmp_path, JOBS[:1], hn_fake=Hn(comments=2))
    c = result.companies[0]
    assert result.status == "partial" and c.pulse_status == "not_enough_signal" and c.draft is None and c.problem is None


def test_a_story_id_that_is_not_a_story_is_reported(tmp_path):
    fake = Hn()
    sid = fake.search("Acme", domain="acme.com")["hits"][0]["story_id"]
    fake.not_a_story.add(sid)
    c = go(tmp_path, JOBS[:1], hn_fake=fake).companies[0]
    assert c.pulse_status == "not_enough_signal" and "not a story" in c.note


def test_a_saved_pulse_is_used_without_the_model(tmp_path):
    fake = Hn()
    sid = fake.search("Acme", domain="acme.com")["hits"][0]["story_id"]
    (tmp_path / "pulse").mkdir()
    (tmp_path / "pulse" / f"{sid}.json").write_text(json.dumps({
        "story_id": sid, "title": "t", "model": "m", "comments_used": 5, "comments_total": 5,
        "items": [{"kind": "bug", "summary": "Saved bug", "comment_ids": [sid + 1]}]}), encoding="utf-8")
    client = FakeClient()
    c = go(tmp_path, JOBS[:1], hn_fake=fake, client=client).companies[0]
    assert client.calls == {"pulse": 0, "draft": 1} and c.problem.summary == "Saved bug"


def test_a_pulse_with_only_uncited_or_unknown_comments_gives_no_cited_problem(tmp_path):
    fake = Hn()
    sid = fake.search("Acme", domain="acme.com")["hits"][0]["story_id"]
    (tmp_path / "pulse").mkdir()
    (tmp_path / "pulse" / f"{sid}.json").write_text(json.dumps({
        "story_id": sid, "title": "t", "model": "m", "comments_used": 5, "comments_total": 5,
        "items": [{"kind": "pro", "summary": "Nice", "comment_ids": [sid + 1]},
                  {"kind": "bug", "summary": "Cites a comment that is not in the thread", "comment_ids": [999999]}]}), encoding="utf-8")
    c = go(tmp_path, JOBS[:1], hn_fake=fake).companies[0]
    assert c.pulse_status == "no_cited_problem" and c.draft is None and "no problem" in c.note


def test_a_match_on_a_topic_only_has_no_repo_to_offer_so_no_draft(tmp_path):
    jobs = [job(1, "Acme", stack=("Docker",))]
    result = go(tmp_path, jobs, repos=[repo(language="Rust", topics=["docker"])])
    c = result.companies[0]
    assert c.pulse_status == "ok" and c.draft_status == "skipped" and "no repo" in c.note and result.status == "partial"


def test_no_public_repos_is_a_friendly_result_and_nothing_is_fetched(tmp_path):
    fake = Hn()
    result = go(tmp_path, JOBS, repos=[], hn_fake=fake)
    assert result.status == "no_matches" and "no public repos" in result.warnings[0] and fake.searches == []
    assert [s.status for s in result.steps] == ["ok", "skipped", "skipped", "skipped", "skipped"]


def test_no_strong_matches_is_a_friendly_result(tmp_path):
    result = go(tmp_path, [job(1, "Acme", stack=("COBOL",))])
    assert result.status == "no_matches" and "No strong matches" in result.warnings[0] and result.companies == []
    assert [s.status for s in result.steps] == ["ok", "ok", "skipped", "skipped", "skipped"]


@pytest.mark.parametrize("step, kwargs, error", [
    ("fingerprint", {"fetch": github.GitHubError("rate limited")}, "rate limited"),
    ("showhn", {"hn_fake": Hn(fail_search_for="Beta")}, "search is down"),
    ("pulse", {"client": FakeClient(fail_on="pulse")}, "model not found"),
    ("draft", {"client": FakeClient(fail_on="draft")}, "model not found"),
])
def test_a_failing_step_stops_the_run_names_the_step_and_shows_no_companies(tmp_path, step, kwargs, error):
    fetch = kwargs.pop("fetch", None)
    if fetch is not None:
        def broken(username):
            raise fetch
        result = run_radar("octo", [Job.model_validate(j) for j in JOBS], profiles_dir=tmp_path / "p", pulse_dir=tmp_path / "pu",
                           client=FakeClient(), fetch_repos=broken)
    else:
        result = go(tmp_path, JOBS, **kwargs)
    assert result.status == "failed" and result.failed_step == step and error in result.error
    assert result.companies == [] and result.steps[-1].name == step and result.steps[-1].status == "failed"
    assert [s.name for s in result.steps] == list(STEP_NAMES[: STEP_NAMES.index(step) + 1])  # later steps never ran


def test_a_saved_pulse_for_another_story_fails_the_pulse_step(tmp_path):
    fake = Hn()
    sid = fake.search("Acme", domain="acme.com")["hits"][0]["story_id"]
    (tmp_path / "pulse").mkdir()
    (tmp_path / "pulse" / f"{sid}.json").write_text(json.dumps({
        "story_id": sid + 7, "title": "t", "model": "m", "comments_used": 5, "comments_total": 5, "items": []}), encoding="utf-8")
    result = go(tmp_path, JOBS[:1], hn_fake=fake)
    assert result.status == "failed" and result.failed_step == "pulse" and "not" in result.error


def test_an_unwritable_profile_folder_fails_the_fingerprint_step(tmp_path):
    blocker = tmp_path / "profiles"
    blocker.write_text("a file, not a folder", encoding="utf-8")
    result = go(tmp_path, JOBS)
    assert result.status == "failed" and result.failed_step == "fingerprint"


# ---- the command ----

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fake = Hn()
    client = FakeClient()
    monkeypatch.setattr(cli, "_model_client", lambda model: client)
    monkeypatch.setattr(cli.radar_mod, "run_radar", lambda *a, **k: run_radar(
        *a, **{**k, "fetch_repos": lambda u: [repo()], "search": fake.search, "fetch_item": fake.item}))
    (tmp_path / "data").mkdir()

    class Env:
        def jobs(self, jobs=JOBS):
            (tmp_path / "data" / "jobs.json").write_text(json.dumps(jobs), encoding="utf-8")

        hn, client_, path = fake, client, tmp_path

    return Env()


def test_command_prints_progress_and_three_cited_drafts(env):
    env.jobs()
    result = runner.invoke(app, ["radar", "octo"])
    assert result.exit_code == 0, result.output
    for n, name in enumerate(STEP_NAMES, start=1):
        assert f"[{n}/5] {name}: ok" in result.output
    assert result.output.count("Draft for") == 3 and result.output.count("Users reported: It crashes on start (comments") == 3
    assert "\n1. " in result.output and "\n3. " in result.output and "Evidence (read it before you send anything" in result.output
    assert "Show HN that links to acme.com" in result.output


def test_command_writes_the_full_result_to_out_and_json_is_ascii(env):
    env.jobs([job(1, "Acéme", url="https://acme.com/careers")])
    result = runner.invoke(app, ["radar", "octo", "--json", "--out", "radar.json"])
    assert result.exit_code == 0, result.output
    saved = json.loads((env.path / "radar.json").read_text(encoding="utf-8"))
    assert saved["status"] == "ok" and saved["companies"][0]["company"] == "Acéme"
    body = result.output[result.output.index("{"):]
    assert "\\u00e9" in body and "é" not in body


def test_command_failure_names_the_step_exits_1_and_shows_no_result(env):
    env.jobs()
    env.hn.fail_search_for = "Acme"
    result = runner.invoke(app, ["radar", "octo", "--out", "radar.json"])
    assert result.exit_code == 1
    assert "Error: radar stopped at step 'showhn': search is down. No result is shown." in result.output
    assert "Draft for" not in result.output and "Radar for" not in result.output
    assert json.loads((env.path / "radar.json").read_text(encoding="utf-8"))["status"] == "failed"


def test_command_no_matches_is_friendly_and_exits_0(env):
    env.jobs([job(1, "Acme", stack=("COBOL",))])
    result = runner.invoke(app, ["radar", "octo"])
    assert result.exit_code == 0 and "No strong matches for octo in 1 jobs." in result.output


@pytest.mark.parametrize("name", ["", "bad-", "a--b", "a" * 40, "x/../y", "a b"])
def test_command_rejects_an_invalid_username(env, name):
    env.jobs()
    result = runner.invoke(app, ["radar", name])
    assert result.exit_code == 1 and "not a valid GitHub username" in result.output


@pytest.mark.parametrize("content, text", [(None, "not found. Run `hnradar fetch-hiring`"), ("{", "could not be read"), ('{"a": 1}', "expected a list of jobs"), ('[{"x": 1}]', "validation error")])
def test_command_turns_a_missing_or_bad_jobs_file_into_a_failed_result(env, content, text):
    if content is not None:
        (env.path / "data" / "jobs.json").write_text(content, encoding="utf-8")
    result = runner.invoke(app, ["radar", "octo", "--out", "radar.json"])
    assert result.exit_code == 1 and "radar stopped at step 'match'" in result.output and text in result.output
    saved = json.loads((env.path / "radar.json").read_text(encoding="utf-8"))
    assert saved["status"] == "failed" and saved["failed_step"] == "match" and saved["companies"] == [] and text in saved["error"]


def test_an_empty_jobs_list_is_no_matches(tmp_path):
    result = go(tmp_path, [])
    assert result.status == "no_matches" and result.companies == []
    assert result.steps[1].name == "match" and result.steps[1].status == "ok" and result.steps[1].message == "0 of 0 jobs"
    assert "No strong matches for octo in 0 jobs." in result.warnings[0]


def test_command_with_an_empty_jobs_file_is_friendly_and_exits_0(env):
    env.jobs([])
    result = runner.invoke(app, ["radar", "octo"])
    assert result.exit_code == 0 and "No strong matches for octo in 0 jobs." in result.output


def test_limit_follows_more_than_three_matches(tmp_path):
    jobs = [job(i, f"Co{i}") for i in range(1, 8)]
    assert len(go(tmp_path, jobs, limit=5).companies) == 5


@pytest.mark.parametrize("value", ["0", "11", "x"])
def test_command_rejects_a_bad_limit(env, value):
    env.jobs()
    assert runner.invoke(app, ["radar", "octo", "--limit", value]).exit_code != 0


def test_command_limit_option_is_passed_on(env):
    env.jobs([job(i, f"Co{i}") for i in range(1, 8)])
    result = runner.invoke(app, ["radar", "octo", "--limit", "4"])
    assert result.exit_code == 0 and "top 4 companies" in result.output


def test_text_output_cleans_the_domain_and_the_model_name(tmp_path, monkeypatch):
    esc, bell, rlo = chr(27), chr(7), chr(0x202E)
    result = go(tmp_path, JOBS[:1])
    result.companies[0].show_hn.domain = f"acme.com{esc}[31m{rlo}"
    result.companies[0].draft.domain = f"acme.com{esc}[31m{rlo}"
    result.companies[0].draft.model = f"m{bell}"
    out = []
    monkeypatch.setattr(cli.typer, "echo", lambda msg="", **k: out.append(str(msg)))
    cli._echo_radar(result)
    text = chr(10).join(out)
    assert esc not in text and rlo not in text and bell not in text and "acme.com" in text



def test_command_cleans_hostile_post_text_before_printing(env):
    env.jobs([job(1, "Acme\x1b[31mRED‮", role="Dev\x07")])
    result = runner.invoke(app, ["radar", "octo"])
    assert "\x1b" not in result.output and "‮" not in result.output and "\x07" not in result.output


def test_command_reports_an_unwritable_out_file(env):
    env.jobs()
    (env.path / "blocked").mkdir()
    result = runner.invoke(app, ["radar", "octo", "--out", str(env.path / "blocked")])
    assert result.exit_code == 1 and "could not write" in result.output
