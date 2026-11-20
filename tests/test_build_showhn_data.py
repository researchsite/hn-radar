"""Tests for site/tools/build_showhn_data.py. The HN search is replaced by a fake: no network."""

import importlib.util
import json
from pathlib import Path

import httpx
import pytest

from hnradar.models import Job, ShowHNResult
from hnradar.sources import hn

SCRIPT = Path(__file__).resolve().parents[1] / "site" / "tools" / "build_showhn_data.py"
spec = importlib.util.spec_from_file_location("build_showhn_data", SCRIPT)
bsd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bsd)

HIT = {"story_id": 7, "title": "Show HN: Acme", "points": 3, "num_comments": 1, "url": "https://acme.com/x"}


def job(comment_id=1, company="Acme", url="https://acme.com/careers", **extra):
    return Job(comment_id=comment_id, company=company, url=url, raw_text="x", **extra)


def fake_search(calls, hits=(HIT,), fail_for=()):
    def search(query, *, domain=None, **_):
        calls.append((query, domain))
        if query in fail_for:
            raise hn.HNError("down")
        return {"total_hits": len(hits), "hits": list(hits)}
    return search


def run(jobs, tmp_path, **kw):
    kw.setdefault("sleep", lambda _s: None)
    return bsd.build(jobs, tmp_path / "showhn", **kw)


def test_writes_one_file_per_job_with_the_job_domain(tmp_path):
    calls = []
    counts = run([job(1), job(2, company="Beta", url="https://www.beta.io/jobs")], tmp_path, search=fake_search(calls))
    assert counts["written"] == 2 and counts["failed"] == []
    assert calls == [("Acme", "acme.com"), ("Beta", "beta.io")]
    result = ShowHNResult.model_validate_json((tmp_path / "showhn" / "1.json").read_text(encoding="utf-8"))
    assert result.query == "Acme" and result.domain == "acme.com" and result.candidates[0].domain_match is True


def test_job_board_link_is_a_platform_domain_not_a_domain_match(tmp_path):
    run([job(1, url="https://jobs.ashbyhq.com/acme")], tmp_path, search=fake_search([]))
    data = json.loads((tmp_path / "showhn" / "1.json").read_text(encoding="utf-8"))
    assert data["platform_domain"] is True and data["low_confidence"] is True


@pytest.mark.parametrize("url", [None, "", "mailto:jobs@acme.com", "https://[bad", "https://localhost/x", "ftp://acme.com"])
def test_a_link_that_is_not_a_plain_web_host_means_name_only(tmp_path, url):
    calls = []
    run([job(1, url=url)], tmp_path, search=fake_search(calls))
    assert calls == [("Acme", None)]


def test_post_without_company_is_counted_and_skipped(tmp_path):
    calls = []
    counts = run([job(1, company=""), job(2, company="  ‮ ")], tmp_path, search=fake_search(calls))
    assert counts["no_company"] == 2 and counts["written"] == 0 and calls == []


def test_query_is_one_clean_line_so_the_page_can_compare_it(tmp_path):
    run([job(1, company="Acme‮   Corp\n(YC S11)")], tmp_path, search=fake_search([]))
    data = json.loads((tmp_path / "showhn" / "1.json").read_text(encoding="utf-8"))
    assert data["query"] == "Acme Corp (YC S11)"


def test_existing_files_are_skipped_unless_refresh(tmp_path):
    calls = []
    run([job(1)], tmp_path, search=fake_search(calls))
    counts = run([job(1)], tmp_path, search=fake_search(calls))
    assert counts["skipped"] == 1 and counts["written"] == 0 and len(calls) == 1
    counts = run([job(1)], tmp_path, search=fake_search(calls), refresh=True)
    assert counts["written"] == 1 and len(calls) == 2


def test_failed_lookup_writes_nothing_and_the_rest_continue(tmp_path):
    calls = []
    counts = run([job(1, company="Down"), job(2)], tmp_path, search=fake_search(calls, fail_for={"Down"}))
    assert counts["written"] == 1 and [f[0] for f in counts["failed"]] == [1]
    assert not (tmp_path / "showhn" / "1.json").exists() and (tmp_path / "showhn" / "2.json").exists()


def test_network_error_and_write_error_are_failures_not_crashes(tmp_path):
    def boom(query, **_):
        raise httpx.ConnectError("no network")

    def cannot_write(path, text):
        raise OSError("disk full")

    assert len(run([job(1)], tmp_path, search=boom)["failed"]) == 1
    assert len(run([job(2)], tmp_path, search=fake_search([]), write=cannot_write)["failed"]) == 1


def test_limit_only_looks_at_the_first_jobs(tmp_path):
    calls = []
    counts = run([job(1), job(2), job(3)], tmp_path, search=fake_search(calls), limit=2)
    assert counts["written"] == 2 and len(calls) == 2


def test_pause_between_requests(tmp_path):
    slept = []
    run([job(1), job(2)], tmp_path, search=fake_search([]), pause=0.5, sleep=slept.append)
    assert slept == [0.5, 0.5]


def test_index_counts_domain_matches_and_ignores_platform_and_broken_files(tmp_path):
    out = tmp_path / "showhn"
    run([job(1), job(2, url="https://jobs.ashbyhq.com/b"), job(3, company="Gamma", url=None)], tmp_path, search=fake_search([]))
    (out / "4.json").write_text("{", encoding="utf-8")
    (out / "notes.json").write_text("{}", encoding="utf-8")
    index = tmp_path / "idx.json"
    assert bsd.write_index(out, index) == 3
    data = json.loads(index.read_text(encoding="utf-8"))
    assert data == {"1": {"strong": 1, "found": 1}, "2": {"strong": 0, "found": 1}, "3": {"strong": 0, "found": 1}}


def test_load_jobs_errors_are_clear(tmp_path):
    for content, text in [(None, "cannot read"), ("{", "not valid JSON"), ("{}", "non-empty list"), ("[]", "non-empty list"), ('[{"x": 1}]', "invalid job")]:
        path = tmp_path / "jobs.json"
        path.unlink(missing_ok=True)
        if content is not None:
            path.write_text(content, encoding="utf-8")
        with pytest.raises(bsd.ShowHNBuildError, match=text):
            bsd.load_jobs(path)


def test_main_exit_codes_and_summary(tmp_path, monkeypatch, capsys):
    jobs_file = tmp_path / "jobs.json"
    jobs_file.write_text(json.dumps([job(1).model_dump(), job(2, company="Down").model_dump()]), encoding="utf-8")
    real_build = bsd.build
    monkeypatch.setattr(bsd, "build", lambda *a, **k: real_build(*a, search=fake_search([], fail_for={"Down"}), **k))
    code = bsd.main(["--jobs", str(jobs_file), "--out", str(tmp_path / "out"), "--pause", "0"])
    out = capsys.readouterr()
    assert code == 1 and "1 written" in out.out and "1 failed" in out.out and "failed: 2 (Down)" in out.err
    assert bsd.main(["--jobs", str(tmp_path / "missing.json")]) == 1
    assert bsd.main(["--jobs", str(jobs_file), "--limit", "0"]) == 1


def test_a_backslash_link_is_never_a_domain(tmp_path):
    calls = []
    run([job(1, url="https://evil.example\\@acme.com/x")], tmp_path, search=fake_search(calls))
    assert calls == [("Acme", None)]


def test_a_changed_company_or_link_is_looked_up_again_and_removed_jobs_are_pruned(tmp_path):
    calls = []
    run([job(1), job(2)], tmp_path, search=fake_search(calls))
    counts = run([job(1, url="https://acme.io/careers"), job(2)], tmp_path, search=fake_search(calls))
    assert counts["written"] == 1 and counts["skipped"] == 1
    assert json.loads((tmp_path / "showhn" / "1.json").read_text(encoding="utf-8"))["domain"] == "acme.io"
    assert bsd.prune(tmp_path / "showhn", [job(2)]) == 1
    assert not (tmp_path / "showhn" / "1.json").exists() and (tmp_path / "showhn" / "2.json").exists()


@pytest.mark.parametrize("status", [403, 404, 429, 500])
def test_http_error_statuses_are_failures_not_files(tmp_path, status):
    def refused(query, **_):
        request = httpx.Request("GET", "https://hn.algolia.com/api/v1/search")
        raise httpx.HTTPStatusError("bad", request=request, response=httpx.Response(status, request=request))

    counts = run([job(1)], tmp_path, search=refused)
    assert len(counts["failed"]) == 1 and not (tmp_path / "showhn" / "1.json").exists()


def test_limit_equal_to_the_job_count_looks_at_every_job(tmp_path):
    calls = []
    run([job(1), job(2)], tmp_path, search=fake_search(calls), limit=2)
    assert len(calls) == 2


def test_load_jobs_rejects_undecodable_deeply_nested_and_non_positive_ids(tmp_path):
    path = tmp_path / "jobs.json"
    path.write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(bsd.ShowHNBuildError):
        bsd.load_jobs(path)
    path.write_text("[" * 100000, encoding="utf-8")
    with pytest.raises(bsd.ShowHNBuildError):
        bsd.load_jobs(path)
    for ids in ([0], [-5], [3, 3]):
        path.write_text(json.dumps([job(i).model_dump() for i in ids]), encoding="utf-8")
        with pytest.raises(bsd.ShowHNBuildError, match="positive"):
            bsd.load_jobs(path)
