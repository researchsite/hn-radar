import json

import httpx
from typer.testing import CliRunner

from hnradar.cli import app
from hnradar.models import Job, Profile

from conftest import REPOS_URL, SEARCH_URL

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
