import json

import httpx
import pytest
from typer.testing import CliRunner

from hnradar.cli import _terminal_safe, app
from hnradar.models import Job, Profile

from conftest import (
    ITEM_URL,
    REPOS_URL,
    SEARCH_RESPONSE,
    SEARCH_URL,
    SHOW_HN_URL,
    STORY_ID,
    make_show_hit,
    show_hn_route,
    show_hn_routes,
)

runner = CliRunner()


def test_fetch_hiring_writes_jobs(mock_hn, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # keeps data/cache/ out of the repo
    out = tmp_path / "out" / "jobs.json"

    result = runner.invoke(app, ["fetch-hiring", "--out", str(out)])

    assert result.exit_code == 0, result.output
    assert "Wrote 3 jobs" in result.output
    jobs = [Job.model_validate(j) for j in json.loads(out.read_text(encoding="utf-8"))]
    assert [j.comment_id for j in jobs] == [101, 102, 104]
    assert jobs[1].role is None  # non-pipe post


def test_fetch_hiring_limit(mock_hn, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "jobs.json"

    result = runner.invoke(app, ["fetch-hiring", "--limit", "2", "--out", str(out)])

    assert result.exit_code == 0, result.output
    assert len(json.loads(out.read_text(encoding="utf-8"))) == 2


def test_fetch_hiring_network_error_exits_nonzero(respx_mock, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    respx_mock.get(SEARCH_URL).mock(return_value=httpx.Response(503))

    result = runner.invoke(app, ["fetch-hiring", "--out", str(tmp_path / "jobs.json")])

    assert result.exit_code == 1
    assert not (tmp_path / "jobs.json").exists()


def test_fingerprint_writes_profile(mock_github, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["fingerprint", "octo"])

    assert result.exit_code == 0, result.output
    profile = Profile.model_validate_json((tmp_path / "data" / "profiles" / "octo.json").read_text(encoding="utf-8"))
    assert profile.username == "octo"
    assert [l.name for l in profile.languages] == ["Python", "Go"]  # fork's Rust excluded
    assert [r.name for r in profile.notable_repos] == ["big", "small"]


def test_fingerprint_no_repos_is_empty_profile_exit_0(respx_mock, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    respx_mock.get(REPOS_URL).mock(return_value=httpx.Response(200, json=[]))

    result = runner.invoke(app, ["fingerprint", "octo", "--out-dir", str(tmp_path / "p")])

    assert result.exit_code == 0, result.output
    assert "no public repos" in result.output
    profile = Profile.model_validate_json((tmp_path / "p" / "octo.json").read_text(encoding="utf-8"))
    assert profile.languages == [] and profile.notable_repos == []


def test_fingerprint_unknown_user_exits_1(respx_mock, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    respx_mock.get(REPOS_URL).mock(return_value=httpx.Response(404, json={"message": "Not Found"}))

    result = runner.invoke(app, ["fingerprint", "octo", "--out-dir", str(tmp_path / "p")])

    assert result.exit_code == 1
    assert "not found" in result.output.lower()
    assert not (tmp_path / "p").exists()


def test_fingerprint_invalid_username_exits_1(respx_mock, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["fingerprint", "../evil", "--out-dir", str(tmp_path / "p")])

    assert result.exit_code == 1
    assert not respx_mock.calls
    assert not (tmp_path / "p").exists()


def test_fetch_hiring_default_prints_table(mock_hn, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["fetch-hiring", "--out", str(tmp_path / "jobs.json")])

    assert result.exit_code == 0, result.output
    assert "Acme | Senior Backend Engineer | yes" in result.output
    assert "Globex | Data Analyst | no" in result.output
    assert (tmp_path / "jobs.json").exists()  # --out behavior unchanged


def test_fetch_hiring_json_alone_writes_no_file(mock_hn, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["fetch-hiring", "--json"])

    assert result.exit_code == 0, result.output
    assert [j["comment_id"] for j in json.loads(result.stdout)] == [101, 102, 104]
    assert not (tmp_path / "data" / "jobs.json").exists()


def test_fetch_hiring_json_with_out_prints_and_writes(mock_hn, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "jobs.json"

    result = runner.invoke(app, ["fetch-hiring", "--json", "--out", str(out)])

    assert result.exit_code == 0, result.output
    printed = json.loads(result.stdout)
    assert printed == json.loads(out.read_text(encoding="utf-8"))
    assert len(printed) == 3


def test_fetch_hiring_json_respects_limit(mock_hn, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["fetch-hiring", "--json", "--limit", "1"])

    assert result.exit_code == 0, result.output
    assert len(json.loads(result.stdout)) == 1


def test_fetch_hiring_limit_zero_is_empty_and_exit_0(mock_hn, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    table = runner.invoke(app, ["fetch-hiring", "--limit", "0", "--out", str(tmp_path / "jobs.json")])
    as_json = runner.invoke(app, ["fetch-hiring", "--limit", "0", "--json"])

    assert table.exit_code == 0, table.output
    assert "|" not in table.output  # no table rows
    assert as_json.exit_code == 0, as_json.output
    assert json.loads(as_json.stdout) == []


def test_fetch_hiring_json_network_error_exits_nonzero(respx_mock, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    respx_mock.get(SEARCH_URL).mock(return_value=httpx.Response(503))

    result = runner.invoke(app, ["fetch-hiring", "--json"])

    assert result.exit_code == 1


@pytest.mark.parametrize("limit", [3, 10])  # exactly the job count, and above it
def test_fetch_hiring_limit_at_or_above_job_count_returns_all(mock_hn, tmp_path, monkeypatch, limit):
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["fetch-hiring", "--json", "--limit", str(limit)])

    assert result.exit_code == 0, result.output
    assert len(json.loads(result.stdout)) == 3


def mock_hostile_post(respx_mock, text):
    """One job whose text carries literal control characters, as the API's JSON can."""
    item = {
        "id": STORY_ID,
        "type": "story",
        "title": "Ask HN: Who is hiring? (November 2026)",
        "children": [{"id": 301, "type": "comment", "text": text, "children": []}],
    }
    respx_mock.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=SEARCH_RESPONSE))
    respx_mock.get(ITEM_URL).mock(return_value=httpx.Response(200, json=item))


def test_fetch_hiring_table_strips_control_characters(respx_mock, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mock_hostile_post(respx_mock, "Evil" + chr(27) + "[2J" + chr(7) + " Corp | DevOps | REMOTE")

    result = runner.invoke(app, ["fetch-hiring", "--out", str(tmp_path / "jobs.json")])

    assert result.exit_code == 0, result.output
    assert not any(ord(c) < 32 and c != chr(10) for c in result.output)  # no ESC, BEL or CR reaches the terminal
    assert "Evil [2J Corp | DevOps | yes" in result.output


def test_fetch_hiring_table_strips_bidi_overrides(respx_mock, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mock_hostile_post(respx_mock, "Acme" + chr(0x202E) + "evil | DevOps | REMOTE")

    result = runner.invoke(app, ["fetch-hiring", "--out", str(tmp_path / "jobs.json")])

    assert result.exit_code == 0, result.output
    assert chr(0x202E) not in result.output
    assert "Acmeevil | DevOps | yes" in result.output


def test_fetch_hiring_json_stdout_escapes_c1_controls(respx_mock, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mock_hostile_post(respx_mock, "Acme" + chr(0x9B) + "2J | DevOps | REMOTE")

    result = runner.invoke(app, ["fetch-hiring", "--json"])

    assert result.exit_code == 0, result.output
    assert all(ord(c) < 128 for c in result.stdout)  # nothing a terminal could act on
    assert json.loads(result.stdout)[0]["company"] == "Acme" + chr(0x9B) + "2J"  # still round-trips


@pytest.mark.parametrize("text", [None, "", chr(27) + chr(7), chr(0x202E) + chr(0x200B), "plain"])
def test_terminal_safe_edge_cases(text):
    cleaned = _terminal_safe(text)
    assert all(ord(c) >= 32 for c in cleaned)
    assert cleaned == ("plain" if text == "plain" else "")


def test_showhn_prints_ranked_table_with_domain_match_first(respx_mock, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    show_hn_route(
        respx_mock,
        [
            make_show_hit(1, "Show HN: Popular but not Acme", points=900, url="https://other.com"),
            make_show_hit(2, "Show HN: Acme", points=5, comments=3, url="https://acme.com"),
        ],
    )

    result = runner.invoke(app, ["showhn", "Acme", "--domain", "acme.com"])

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0] == 'Show HN candidates for "Acme": 2 of 2 hits'
    assert lines[1] == "2 | 5 pts | 3 comments | official Show HN | Show HN: Acme | https://acme.com"
    assert lines[2].startswith("1 | 900 pts")
    assert "Low confidence" not in result.output


def test_showhn_without_domain_is_flagged_not_refused(respx_mock, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    show_hn_route(respx_mock, [make_show_hit(1, "Show HN: non-linear UI", url="https://example.org")])

    result = runner.invoke(app, ["showhn", "Linear"])

    assert result.exit_code == 0, result.output
    assert "Low confidence: no story URL matches the domain (no --domain given)." in result.output


def test_showhn_reports_when_hits_were_cut(respx_mock, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    show_hn_route(respx_mock, [make_show_hit(i, "Show HN: x", points=i) for i in range(1, 9)], nb_hits=813)

    result = runner.invoke(app, ["showhn", "Supabase", "--json"])

    data = json.loads(result.stdout)
    assert result.exit_code == 0, result.output
    assert (len(data["candidates"]), data["total_hits"], data["truncated"]) == (5, 813, True)


def test_showhn_json_shape(respx_mock, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    show_hn_route(respx_mock, [make_show_hit(2, "Show HN: Acme", points=5, comments=3, url="https://acme.com")])

    result = runner.invoke(app, ["showhn", "Acme", "--domain", "www.ACME.com", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "query": "Acme",
        "domain": "acme.com",
        "candidates": [
            {"story_id": 2, "title": "Show HN: Acme", "points": 5, "num_comments": 3,
             "url": "https://acme.com", "domain_match": True}
        ],
        "total_hits": 1,
        "truncated": False,
        "low_confidence": False,
    }


def test_showhn_zero_hits_is_not_an_error(respx_mock, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    show_hn_route(respx_mock, [])

    table = runner.invoke(app, ["showhn", "Nobody"])
    as_json = runner.invoke(app, ["showhn", "Nobody", "--json"])

    assert table.exit_code == 0, table.output
    assert "0 of 0 hits" in table.output
    assert as_json.exit_code == 0
    assert json.loads(as_json.stdout)["candidates"] == []


def test_showhn_network_error_exits_1(respx_mock, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    respx_mock.get("https://hn.algolia.com/api/v1/search").mock(return_value=httpx.Response(503))

    result = runner.invoke(app, ["showhn", "Acme"])

    assert result.exit_code == 1


@pytest.mark.parametrize(
    "args",
    [
        ["showhn", "   "],
        ["showhn", "Acme", "--domain", "https://acme.com"],
        ["showhn", "Acme", "--domain", "acme.com/path"],
        ["showhn", "Acme", "--domain", "acme.com:443"],
        ["showhn", "Acme", "--domain", "user@acme.com"],
        ["showhn", "Acme", "--domain", ""],  # an empty value is invalid, not "no domain"
    ],
)
def test_showhn_bad_input_exits_1_without_touching_the_network(respx_mock, tmp_path, monkeypatch, args):
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, args)

    assert result.exit_code == 1
    assert not respx_mock.calls


def test_showhn_table_cleans_hostile_titles_and_json_escapes_c1(respx_mock, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    title = "Show HN: Evil" + chr(27) + "[2J" + chr(0x202E) + " Corp" + chr(0x9B)
    show_hn_route(respx_mock, [make_show_hit(1, title, url="https://evil.example/" + chr(27))])

    table = runner.invoke(app, ["showhn", "Evil"])
    as_json = runner.invoke(app, ["showhn", "Evil", "--json"])

    assert table.exit_code == 0, table.output
    assert not any(ord(c) < 32 and c != chr(10) for c in table.output)
    assert chr(0x202E) not in table.output and chr(0x9B) not in table.output
    assert all(ord(c) < 128 for c in as_json.stdout)
    assert json.loads(as_json.stdout)["candidates"][0]["title"] == title  # still round-trips


@pytest.mark.parametrize("status", [403, 404, 429, 500, 503])
def test_showhn_upstream_errors_exit_1_with_a_message(respx_mock, tmp_path, monkeypatch, status):
    monkeypatch.chdir(tmp_path)
    respx_mock.get("https://hn.algolia.com/api/v1/search").mock(return_value=httpx.Response(status))

    result = runner.invoke(app, ["showhn", "Acme"])

    assert result.exit_code == 1
    assert "Error:" in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)  # no traceback


@pytest.mark.parametrize("body", [{"hits": None, "nbHits": 0}, {"nbHits": 3}, []])
def test_showhn_wrong_shaped_response_exits_1_with_a_message(respx_mock, tmp_path, monkeypatch, body):
    monkeypatch.chdir(tmp_path)
    respx_mock.get("https://hn.algolia.com/api/v1/search").mock(return_value=httpx.Response(200, json=body))

    result = runner.invoke(app, ["showhn", "Acme"])

    assert result.exit_code == 1
    assert "Error:" in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)


def _match_files(tmp_path, jobs, profile):
    jobs_file = tmp_path / "jobs.json"
    jobs_file.write_text(json.dumps(jobs), encoding="utf-8")
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    if profile is not None:
        (profiles / "octo.json").write_text(json.dumps(profile), encoding="utf-8")
    return ["match", "octo", "--jobs", str(jobs_file), "--profiles-dir", str(profiles)]


MATCH_PROFILE = {
    "username": "octo",
    "languages": [{"name": "Python", "size_kb": 10}],
    "topics": [],
    "notable_repos": [{"name": "radar", "stars": 1, "language": "Python"}],
}


def _job(comment_id, stack, **extra):
    return {"comment_id": comment_id, "company": f"Co{comment_id}", "stack": stack, "raw_text": "x", **extra}


def test_match_prints_top_matches_with_reasons(tmp_path):
    args = _match_files(tmp_path, [_job(1, ["Python"], remote=True, role="Dev"), _job(2, ["Go"])], MATCH_PROFILE)

    result = runner.invoke(app, args)

    assert result.exit_code == 0, result.output
    assert "Top 1 of 2 jobs for octo" in result.output
    assert "1. Co1 | Dev | yes | score 25" in result.output
    assert "Your Python work matches this job's stack" in result.output
    assert "Your repo radar is written in Python" in result.output
    assert "Co2" not in result.output


def test_match_json_shape(tmp_path):
    result = runner.invoke(app, _match_files(tmp_path, [_job(1, ["Python"])], MATCH_PROFILE) + ["--json"])

    data = json.loads(result.output)
    assert (data["username"], data["status"]) == ("octo", "ok")
    assert data["matches"][0]["rank"] == 1
    assert data["matches"][0]["job"]["company"] == "Co1"
    assert data["matches"][0]["reasons"][0] == {
        "kind": "language", "text": "Your Python work matches this job's stack", "evidence": "Python"
    }


@pytest.mark.parametrize("jobs", [[], [_job(1, ["Go"])]])
def test_match_with_no_overlap_says_no_strong_matches_and_exits_0(tmp_path, jobs):
    args = _match_files(tmp_path, jobs, MATCH_PROFILE)

    table = runner.invoke(app, args)
    as_json = runner.invoke(app, args + ["--json"])

    assert table.exit_code == 0
    assert "No strong matches" in table.output
    assert json.loads(as_json.output)["status"] == "no_strong_matches"
    assert json.loads(as_json.output)["matches"] == []


def test_match_profile_with_no_languages_is_no_strong_matches(tmp_path):
    empty = {"username": "octo"}

    result = runner.invoke(app, _match_files(tmp_path, [_job(1, ["Python"])], empty))

    assert result.exit_code == 0
    assert "No strong matches" in result.output


def test_match_missing_profile_tells_the_user_to_run_fingerprint(tmp_path):
    result = runner.invoke(app, _match_files(tmp_path, [_job(1, ["Python"])], None))

    assert result.exit_code == 1
    assert "hnradar fingerprint octo" in result.output


def test_match_missing_jobs_file_tells_the_user_to_run_fetch_hiring(tmp_path):
    args = _match_files(tmp_path, [], MATCH_PROFILE)
    args[args.index("--jobs") + 1] = str(tmp_path / "nope.json")

    result = runner.invoke(app, args)

    assert result.exit_code == 1
    assert "hnradar fetch-hiring" in result.output


@pytest.mark.parametrize("username", ["../octo", "octo/x", "a b", "", "x" * 40, "octo.json", "foo-", "a--b"])
def test_match_rejects_usernames_that_are_not_github_logins(tmp_path, username):
    args = _match_files(tmp_path, [], MATCH_PROFILE)
    args[1] = username

    result = runner.invoke(app, args)

    assert result.exit_code == 1
    assert "valid GitHub username" in result.output


def test_match_profile_saved_under_another_name_is_an_error(tmp_path):
    args = _match_files(tmp_path, [_job(1, ["Python"])], dict(MATCH_PROFILE, username="someone-else"))

    result = runner.invoke(app, args)

    assert result.exit_code == 1
    assert "someone-else" in result.output and "octo" in result.output


def test_match_finds_a_profile_saved_with_another_capitalisation(tmp_path):
    args = _match_files(tmp_path, [_job(1, ["Python"])], None)
    (tmp_path / "profiles" / "OCTO.json").write_text(json.dumps(MATCH_PROFILE), encoding="utf-8")  # as `fingerprint OCTO` writes it

    result = runner.invoke(app, args)  # `match octo`

    assert result.exit_code == 0, result.output
    assert "Top 1 of 1 jobs for octo" in result.output


def test_match_unreadable_profiles_dir_is_a_clean_error(tmp_path, monkeypatch):
    args = _match_files(tmp_path, [_job(1, ["Python"])], None)

    def boom(self):
        raise PermissionError("denied")

    monkeypatch.setattr("pathlib.Path.iterdir", boom)
    result = runner.invoke(app, args)

    assert result.exit_code == 1
    assert "Error:" in result.output and "Traceback" not in result.output


def test_match_two_profiles_differing_only_by_case_is_an_error(tmp_path, monkeypatch):
    args = _match_files(tmp_path, [_job(1, ["Python"])], None)
    profiles = tmp_path / "profiles"
    monkeypatch.setattr("pathlib.Path.iterdir", lambda self: iter([profiles / "OCTO.json", profiles / "Octo.json"]))

    result = runner.invoke(app, args)  # `match octo`: neither file is the exact spelling

    assert result.exit_code == 1
    assert "differ only by case" in result.output and "OCTO.json" in result.output


def test_match_exact_spelling_does_not_hide_a_case_variant(tmp_path, monkeypatch):
    args = _match_files(tmp_path, [_job(1, ["Python"])], dict(MATCH_PROFILE, username="Octo"))
    profiles = tmp_path / "profiles"
    (profiles / "Octo.json").write_text(json.dumps(dict(MATCH_PROFILE, username="Octo")), encoding="utf-8")
    monkeypatch.setattr("pathlib.Path.iterdir", lambda self: iter([profiles / "Octo.json", profiles / "OCTO.json"]))
    args[1] = "Octo"  # the exact spelling exists, but so does OCTO.json: still ambiguous

    result = runner.invoke(app, args)

    assert result.exit_code == 1
    assert "differ only by case" in result.output


@pytest.mark.parametrize("target", ["jobs", "profile"])
def test_match_deeply_nested_json_is_a_clean_error(tmp_path, target):
    args = _match_files(tmp_path, [], MATCH_PROFILE)
    path = tmp_path / "jobs.json" if target == "jobs" else tmp_path / "profiles" / "octo.json"
    path.write_text("[" * 200000 + "]" * 200000, encoding="utf-8")

    result = runner.invoke(app, args)

    assert result.exit_code == 1
    assert "Error:" in result.output and "Traceback" not in result.output


def test_match_username_case_is_not_significant(tmp_path):
    args = _match_files(tmp_path, [_job(1, ["Python"])], dict(MATCH_PROFILE, username="Octo"))

    assert runner.invoke(app, args).exit_code == 0  # GitHub logins are case-insensitive


@pytest.mark.parametrize("content", ["not json", "{}", '{"username": 5}', "[]"])
def test_match_malformed_profile_exits_1_with_error(tmp_path, content):
    args = _match_files(tmp_path, [], MATCH_PROFILE)
    (tmp_path / "profiles" / "octo.json").write_text(content, encoding="utf-8")

    result = runner.invoke(app, args)

    assert result.exit_code == 1
    assert "Error:" in result.output


@pytest.mark.parametrize("content", ["not json", "{}", '[{"comment_id": "x"}]', '["oops"]'])
def test_match_malformed_jobs_file_exits_1_with_error(tmp_path, content):
    args = _match_files(tmp_path, [], MATCH_PROFILE)
    (tmp_path / "jobs.json").write_text(content, encoding="utf-8")

    result = runner.invoke(app, args)

    assert result.exit_code == 1
    assert "Error:" in result.output


def test_match_hostile_post_text_is_cleaned_in_the_table_and_escaped_in_json(tmp_path):
    esc = chr(27)
    hostile = _job(1, ["Python"], company=f"Ev{esc}[2Jil‮Co", role="Dev\x85X", url="https://x.example/\x1b]0;t\x07")
    args = _match_files(tmp_path, [hostile], MATCH_PROFILE)

    table = runner.invoke(app, args)
    as_json = runner.invoke(app, args + ["--json"])

    assert esc not in table.output and "‮" not in table.output and "\x85" not in table.output
    assert esc not in as_json.output and "‮" not in as_json.output
    assert json.loads(as_json.output)["matches"][0]["job"]["company"] == hostile["company"]


# --- #20: URL-restricted domain query ---------------------------------------------------------------


def test_showhn_with_domain_merges_the_url_search_and_ranks_its_story_first(respx_mock, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    route = show_hn_routes(
        respx_mock,
        [make_show_hit(1, "Show HN: Popular but not Acme", points=900, url="https://other.com")],
        [make_show_hit(2, "Show HN: Acme launch", points=5, url="https://acme.com/launch")],
    )

    result = runner.invoke(app, ["showhn", "Acme", "--domain", "acme.com", "--json"])

    data = json.loads(result.stdout)
    assert result.exit_code == 0, result.output
    assert [c["story_id"] for c in data["candidates"]] == [2, 1]
    assert data["candidates"][0]["domain_match"] is True
    assert (data["total_hits"], data["truncated"], data["low_confidence"]) == (2, False, False)
    assert route.call_count == 2


def test_showhn_without_domain_makes_exactly_one_request(respx_mock, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    route = show_hn_routes(respx_mock, [make_show_hit(1, "Show HN: Acme")], [])

    result = runner.invoke(app, ["showhn", "Acme"])

    assert result.exit_code == 0, result.output
    assert route.call_count == 1


def test_showhn_merged_total_is_a_lower_bound_and_reported_as_cut(respx_mock, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    show_hn_routes(
        respx_mock,
        [make_show_hit(i, "Show HN: x", points=i) for i in range(1, 9)],
        [make_show_hit(99, "Show HN: Acme", url="https://acme.com")],
        name_nb=813,
    )

    result = runner.invoke(app, ["showhn", "Acme", "--domain", "acme.com", "--json"])

    data = json.loads(result.stdout)
    assert result.exit_code == 0, result.output
    assert (len(data["candidates"]), data["total_hits"], data["truncated"]) == (5, 813, True)
    assert data["candidates"][0]["story_id"] == 99  # the domain match is kept even though 8 other hits outrank it by points


@pytest.mark.parametrize("status", [403, 404, 429, 503])
def test_showhn_domain_request_failure_still_prints_the_name_results(respx_mock, tmp_path, monkeypatch, status):
    monkeypatch.chdir(tmp_path)

    def respond(request):
        if request.url.params.get("restrictSearchableAttributes"):
            return httpx.Response(status, json={"message": "nope"})
        return httpx.Response(200, json={"hits": [make_show_hit(1, "Show HN: Acme")], "nbHits": 1})

    respx_mock.get(SHOW_HN_URL).mock(side_effect=respond)

    result = runner.invoke(app, ["showhn", "Acme", "--domain", "acme.com"])

    assert result.exit_code == 0, result.output
    assert "Show HN candidates" in result.output
