import importlib.util
import json
import os
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / ".claude" / "skills" / "verify-jobs-parse" / "compare_jobs.py"
spec = importlib.util.spec_from_file_location("compare_jobs", SCRIPT)
compare_jobs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compare_jobs)


def write_jobs(path, ids):
    path.write_text(json.dumps([{"comment_id": i, "company": "x"} for i in ids]), encoding="utf-8")
    return str(path)


def write_thread(path, ids, *, empty=()):
    """A cached thread response; `empty` ids are deleted or dead comments (no text)."""
    children = [{"id": i, "text": "post"} for i in ids] + [{"id": i, "text": None} for i in empty]
    path.write_text(json.dumps({"id": 99, "children": children}), encoding="utf-8")
    return str(path)


def run(tmp_path, baseline, new, *extra):
    return compare_jobs.main(["--baseline", write_jobs(tmp_path / "b.json", baseline), "--new", write_jobs(tmp_path / "n.json", new), *extra])


def test_passes_when_excluded_ids_are_gone_and_kept_ids_stay(tmp_path, capsys):
    code = run(tmp_path, [1, 2, 3, 4], [1, 4], "--must-exclude", "2", "3", "--must-keep", "1", "4")

    out = capsys.readouterr().out
    assert code == 0
    assert "PASS" in out and "removed (2): [2, 3]" in out


def test_fails_when_an_excluded_id_is_still_present(tmp_path, capsys):
    code = run(tmp_path, [1, 2], [1, 2], "--must-exclude", "2")

    assert code == 1
    assert "must-exclude still present: [2]" in capsys.readouterr().out


def test_fails_when_a_kept_id_is_missing(tmp_path, capsys):
    code = run(tmp_path, [1, 2], [1], "--must-keep", "2")

    assert code == 1
    assert "must-keep missing: [2]" in capsys.readouterr().out


def test_reports_added_ids_and_duplicates():
    result = compare_jobs.compare([1, 2], [2, 2, 5], [], [])

    assert result["added"] == [5]
    assert result["removed"] == [1]
    assert result["duplicate_ids_in_new"] == 1


def test_duplicate_ids_in_the_new_file_fail_the_check(tmp_path, capsys):
    code = run(tmp_path, [1, 2], [1, 1, 2])

    assert code == 1
    assert "duplicate ids in the new file: 1" in capsys.readouterr().out


def test_empty_lists_pass_and_empty_files_are_fine(tmp_path, capsys):
    assert run(tmp_path, [], []) == 0
    assert "baseline 0 jobs, new 0 jobs" in capsys.readouterr().out


def test_report_file_is_written(tmp_path):
    report = tmp_path / "out" / "report.json"

    run(tmp_path, [1, 2], [1], "--must-exclude", "2", "--report", str(report))

    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["passed"] is True and data["removed"] == [2]


def test_an_existing_report_is_never_overwritten(tmp_path, capsys):
    report = tmp_path / "report.json"
    report.write_text("earlier evidence", encoding="utf-8")

    code = run(tmp_path, [1], [1], "--report", str(report))

    assert code == 2
    assert "already exists" in capsys.readouterr().err
    assert report.read_text(encoding="utf-8") == "earlier evidence"


def test_missing_file_is_a_clear_error_with_exit_code_2(tmp_path, capsys):
    code = compare_jobs.main(["--baseline", str(tmp_path / "nope.json"), "--new", write_jobs(tmp_path / "n.json", [1])])

    assert code == 2
    assert "Error: baseline file not found" in capsys.readouterr().err


@pytest.mark.parametrize("content", ['{"a": 1}', "not json", '[{"company": "x"}]', '["str"]', "[" * 200000])
def test_bad_shapes_and_absurdly_nested_json_are_exit_code_2_not_a_traceback(tmp_path, capsys, content):
    bad = tmp_path / "bad.json"
    bad.write_text(content, encoding="utf-8")

    code = compare_jobs.main(["--baseline", str(bad), "--new", write_jobs(tmp_path / "n.json", [1])])

    assert code == 2
    assert capsys.readouterr().err.startswith("Error:")


# --- the thread file ---


def test_ids_the_thread_lost_are_not_blamed_on_the_code(tmp_path, capsys):
    thread = write_thread(tmp_path / "t.json", [1, 4, 9])  # 3 left the thread, 9 is new and was kept

    code = run(tmp_path, [1, 3, 4], [1, 4, 9], "--thread", thread)

    out = capsys.readouterr().out
    assert code == 0
    assert "gone from the thread (not the code): [3]" in out
    assert "DROPPED BY THE CODE, not in must-exclude: []" in out


def test_a_deleted_post_is_gone_from_the_thread_not_dropped_by_the_code(tmp_path, capsys):
    thread = write_thread(tmp_path / "t.json", [1, 2], empty=[3])  # 3 is still listed but has no text now

    code = run(tmp_path, [1, 2, 3], [1, 2], "--thread", thread)

    assert code == 0
    assert "gone from the thread (not the code): [3]" in capsys.readouterr().out


def test_a_post_the_code_dropped_without_being_listed_fails(tmp_path, capsys):
    thread = write_thread(tmp_path / "t.json", [1, 2, 3])  # 3 is still in the thread but missing from the output

    code = run(tmp_path, [1, 2, 3], [1, 2], "--thread", thread)

    assert code == 1
    assert "DROPPED BY THE CODE, not in must-exclude: [3]" in capsys.readouterr().out


def test_a_listed_exclusion_is_not_an_unexpected_drop(tmp_path):
    thread = write_thread(tmp_path / "t.json", [1, 2])

    assert run(tmp_path, [1, 2], [1], "--thread", thread, "--must-exclude", "2") == 0


def test_a_new_job_that_is_not_in_the_thread_fails(tmp_path, capsys):
    thread = write_thread(tmp_path / "t.json", [1])

    code = run(tmp_path, [1], [1, 7], "--thread", thread)

    assert code == 1
    assert "new jobs not in the thread (wrong thread file?): [7]" in capsys.readouterr().out


def test_the_wrong_thread_file_cannot_turn_real_drops_into_a_pass(tmp_path):
    other_story = write_thread(tmp_path / "other.json", [100, 101])  # a different story: none of our ids

    assert run(tmp_path, [1, 2, 3], [1, 2], "--thread", other_story) == 1


def test_a_baseline_from_another_thread_fails(tmp_path, capsys):
    thread = write_thread(tmp_path / "t.json", [10, 11, 12])

    code = run(tmp_path, [1, 2, 3], [10, 11, 12], "--thread", thread)  # last month's baseline: no overlap

    assert code == 1
    assert "LOOKS LIKE ANOTHER THREAD" in capsys.readouterr().out


def test_baseline_overlap_is_checked_without_a_thread_file_too(tmp_path):
    assert run(tmp_path, [1, 2, 3, 4], [9]) == 1


@pytest.mark.parametrize("content", ["[]", '{"children": "x"}', '{"children": [{"id": "a"}]}', "nope"])
def test_bad_thread_file_is_exit_code_2(tmp_path, capsys, content):
    bad = tmp_path / "bad_thread.json"
    bad.write_text(content, encoding="utf-8")

    code = run(tmp_path, [1], [1], "--thread", str(bad))

    assert code == 2
    assert capsys.readouterr().err.startswith("Error:")


# --- picking the thread file from the cache directory ---


def test_directory_picks_the_file_that_contains_every_new_job(tmp_path, capsys):
    cache = tmp_path / "cache"
    cache.mkdir()
    write_thread(cache / "items_1_aaaa.json", [100, 101])  # another story, newer
    right = write_thread(cache / "items_2_bbbb.json", [1, 2, 3])
    os.utime(cache / "items_1_aaaa.json", (9999999999, 9999999999))

    code = run(tmp_path, [1, 2, 3], [1, 2, 3], "--thread", str(cache))

    assert code == 0
    assert f"thread file: {Path(right)}" in capsys.readouterr().out


def test_directory_with_no_matching_thread_file_is_exit_code_2(tmp_path, capsys):
    cache = tmp_path / "cache"
    cache.mkdir()
    write_thread(cache / "items_1_aaaa.json", [100])

    code = run(tmp_path, [1, 2], [1, 2], "--thread", str(cache))

    assert code == 2
    assert "no items_*.json" in capsys.readouterr().err


def test_directory_needs_at_least_one_new_job(tmp_path, capsys):
    cache = tmp_path / "cache"
    cache.mkdir()

    code = run(tmp_path, [1], [], "--thread", str(cache))

    assert code == 2
    assert "pass the file" in capsys.readouterr().err
