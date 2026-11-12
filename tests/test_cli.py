import json

import httpx
from typer.testing import CliRunner

from hnradar.cli import app
from hnradar.models import Job

from conftest import SEARCH_URL

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
