import json

import httpx
import pytest
from typer.testing import CliRunner

from hnradar.cli import _terminal_safe, app
from hnradar.models import Job, Profile

from conftest import ITEM_URL, REPOS_URL, SEARCH_RESPONSE, SEARCH_URL, STORY_ID

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
