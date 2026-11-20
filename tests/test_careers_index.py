"""Tests for site/tools/build_careers_index.py. Pagefind itself is replaced by a fake runner: no network, no npx."""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "site" / "tools" / "build_careers_index.py"
spec = importlib.util.spec_from_file_location("build_careers_index", SCRIPT)
bci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bci)


def job(comment_id=1, **extra):
    record = {"comment_id": comment_id, "company": "Acme", "role": "Engineer", "location": "Berlin",
              "remote": True, "stack": ["Python", "Rust"], "url": None, "raw_text": "Acme | Engineer | Berlin"}
    record.update(extra)
    return record


def write_jobs(tmp_path, data):
    path = tmp_path / "jobs.json"
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return path


def fake_runner(pages_written: list):
    """Stands in for pagefind: records the pages it was given and writes a matching entry file."""

    def run(site_dir: Path, output_dir: Path) -> None:
        pages = sorted(p.name for p in site_dir.glob("*.html"))
        pages_written.extend(pages)
        output_dir.mkdir(parents=True)
        (output_dir / "pagefind-entry.json").write_text(
            json.dumps({"version": bci.PAGEFIND_VERSION, "languages": {"en": {"page_count": len(pages)}}}),
            encoding="utf-8",
        )

    return run


# ---- load_jobs: every input fails cleanly ----

def test_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(bci.IndexBuildError, match="cannot read"):
        bci.load_jobs(tmp_path / "nope.json")


def test_not_json_is_a_clear_error(tmp_path):
    with pytest.raises(bci.IndexBuildError, match="not valid JSON"):
        bci.load_jobs(write_jobs(tmp_path, "{not json"))


def test_deeply_nested_json_is_a_clear_error(tmp_path):
    with pytest.raises(bci.IndexBuildError, match="not valid JSON"):
        bci.load_jobs(write_jobs(tmp_path, "[" * 100000))


def test_not_a_list_is_a_clear_error(tmp_path):
    with pytest.raises(bci.IndexBuildError, match="must hold a list"):
        bci.load_jobs(write_jobs(tmp_path, {"jobs": []}))


def test_empty_list_refuses_to_build(tmp_path):
    with pytest.raises(bci.IndexBuildError, match="empty index"):
        bci.load_jobs(write_jobs(tmp_path, []))


def test_record_without_valid_comment_id_is_reported_not_dropped(tmp_path):
    path = write_jobs(tmp_path, [job(1), {"company": "No id", "raw_text": "x"}, job("abc")])
    with pytest.raises(bci.IndexBuildError, match=r"2 of 3 records .*\(positions: 1, 2\)"):
        bci.load_jobs(path)


def test_many_invalid_records_are_summarised(tmp_path):
    path = write_jobs(tmp_path, [{"x": 1}] * 12)
    with pytest.raises(bci.IndexBuildError, match="and 2 more"):
        bci.load_jobs(path)


def test_duplicate_comment_id_is_an_error(tmp_path):
    with pytest.raises(bci.IndexBuildError, match="duplicate comment_id 7"):
        bci.load_jobs(write_jobs(tmp_path, [job(7), job(7)]))


def test_valid_file_loads_all_jobs(tmp_path):
    assert [j.comment_id for j in bci.load_jobs(write_jobs(tmp_path, [job(1), job(2)]))] == [1, 2]


def test_older_file_without_closed_fields_loads(tmp_path):
    assert bci.load_jobs(write_jobs(tmp_path, [job(1)]))[0].closed is False


# ---- job_page: untrusted text ----

def test_html_in_a_post_is_escaped():
    page = bci.job_page(bci.Job.model_validate(job(raw_text='<script>alert(1)</script> <img src=x onerror=y>')))
    assert "<script>" not in page and "<img" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_company_and_role_are_escaped_in_title_and_heading():
    page = bci.job_page(bci.Job.model_validate(job(company='A"B<i>', role="x&y")))
    assert "<title>A&quot;B&lt;i&gt; | x&amp;y</title>" in page
    assert "<i>" not in page


def test_control_and_bidirectional_characters_are_removed():
    nasty = "pay\x1b[31m red\x07 ‮evil‬ zero​width \x9b"
    page = bci.job_page(bci.Job.model_validate(job(raw_text=nasty)))
    for char in ("\x1b", "\x07", "‮", "‬", "​", "\x9b"):
        assert char not in page


def test_very_long_post_is_indexed_in_full():
    text = "word " * 50_000 + "needle-at-the-end"
    assert "needle-at-the-end" in bci.job_page(bci.Job.model_validate(job(raw_text=text)))


def test_missing_company_role_location_still_gives_a_page():
    page = bci.job_page(bci.Job.model_validate(job(company="", role=None, location=None, stack=[])))
    assert "<title>Post 1</title>" in page


def test_comment_id_is_a_meta_value_not_indexed_text():
    page = bci.job_page(bci.Job.model_validate(job(comment_id=424242)))
    assert '<meta data-pagefind-meta="comment_id[content]" content="424242">' in page


# ---- build: never a half index ----

def test_build_writes_one_page_per_job_and_replaces_the_old_index(tmp_path):
    out = tmp_path / "pagefind"
    out.mkdir()
    (out / "stale.txt").write_text("old")
    written: list = []
    count = bci.build(write_jobs(tmp_path, [job(1), job(2)]), out, runner=fake_runner(written))
    assert count == 2 and written == ["1.html", "2.html"]
    assert (out / "pagefind-entry.json").exists() and not (out / "stale.txt").exists()
    assert not (tmp_path / "pagefind.new").exists()


def test_build_at_the_smallest_size_one_job(tmp_path):
    assert bci.build(write_jobs(tmp_path, [job(1)]), tmp_path / "pagefind", runner=fake_runner([])) == 1


def test_failing_pagefind_keeps_the_old_index_and_leaves_no_staging_folder(tmp_path):
    out = tmp_path / "pagefind"
    out.mkdir()
    (out / "keep.txt").write_text("old index")

    def boom(site_dir, output_dir):
        output_dir.mkdir()
        raise bci.IndexBuildError("pagefind exited with 1: boom")

    with pytest.raises(bci.IndexBuildError, match="boom"):
        bci.build(write_jobs(tmp_path, [job(1)]), out, runner=boom)
    assert (out / "keep.txt").read_text() == "old index"
    assert not (tmp_path / "pagefind.new").exists()


def test_short_index_is_rejected_and_old_index_kept(tmp_path):
    out = tmp_path / "pagefind"
    out.mkdir()
    (out / "keep.txt").write_text("old index")

    def short(site_dir, output_dir):
        output_dir.mkdir()
        (output_dir / "pagefind-entry.json").write_text(json.dumps({"languages": {"en": {"page_count": 1}}}))

    with pytest.raises(bci.IndexBuildError, match="indexed 1 pages for 2 jobs"):
        bci.build(write_jobs(tmp_path, [job(1), job(2)]), out, runner=short)
    assert (out / "keep.txt").exists()


def test_missing_entry_file_is_a_clear_error(tmp_path):
    def silent(site_dir, output_dir):
        output_dir.mkdir()

    with pytest.raises(bci.IndexBuildError, match="no readable pagefind-entry.json"):
        bci.build(write_jobs(tmp_path, [job(1)]), tmp_path / "pagefind", runner=silent)


def test_two_builds_in_a_row_leave_a_single_clean_index(tmp_path):
    jobs_path = write_jobs(tmp_path, [job(1), job(2)])
    out = tmp_path / "pagefind"
    bci.build(jobs_path, out, runner=fake_runner([]))
    bci.build(jobs_path, out, runner=fake_runner([]))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["jobs.json", "pagefind"]


# ---- command line ----

def test_cli_prints_error_and_exits_1(tmp_path, capsys):
    assert bci.main(["--jobs", str(tmp_path / "missing.json"), "--out", str(tmp_path / "out")]) == 1
    assert capsys.readouterr().err.startswith("Error: cannot read")


def test_missing_npx_is_a_clear_error(monkeypatch, tmp_path):
    monkeypatch.setattr(bci.shutil, "which", lambda name: None)
    with pytest.raises(bci.IndexBuildError, match="npx not found"):
        bci.run_pagefind(tmp_path, tmp_path / "out")


def test_pagefind_failure_exit_code_is_reported(monkeypatch, tmp_path):
    class Result:
        returncode = 3
        stderr = "kaboom"
        stdout = ""

    monkeypatch.setattr(bci.shutil, "which", lambda name: "/usr/bin/npx")
    monkeypatch.setattr(bci.subprocess, "run", lambda *a, **k: Result())
    with pytest.raises(bci.IndexBuildError, match="pagefind exited with 3: kaboom"):
        bci.run_pagefind(tmp_path, tmp_path / "out")


def test_pagefind_command_is_pinned_to_the_site_version(monkeypatch, tmp_path):
    seen = {}

    class Result:
        returncode = 0
        stderr = stdout = ""

    def fake_run(command, **kwargs):
        seen["command"] = command
        return Result()

    monkeypatch.setattr(bci.shutil, "which", lambda name: "/usr/bin/npx")
    monkeypatch.setattr(bci.subprocess, "run", fake_run)
    bci.run_pagefind(tmp_path, tmp_path / "out")
    assert f"pagefind@{bci.PAGEFIND_VERSION}" in seen["command"] and bci.PAGEFIND_VERSION == "1.6.0"


def test_failed_swap_keeps_the_working_index(tmp_path, monkeypatch):
    jobs = write_jobs(tmp_path, [job(1)])
    out = tmp_path / "pagefind"
    out.mkdir()
    (out / "old.txt").write_text("working index", encoding="utf-8")
    real_replace = Path.replace

    def failing_replace(self, target):
        if self.name == "pagefind.new":
            raise OSError("locked")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", failing_replace)
    with pytest.raises(OSError):
        bci.build(jobs, out, runner=fake_runner([]))
    assert (out / "old.txt").read_text(encoding="utf-8") == "working index"
