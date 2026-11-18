import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / ".claude" / "skills" / "verify-jobs-parse" / "compare_jobs.py"
spec = importlib.util.spec_from_file_location("compare_jobs", SCRIPT)
compare_jobs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compare_jobs)


def write_jobs(path, ids):
    path.write_text(json.dumps([{"comment_id": i, "company": "x"} for i in ids]), encoding="utf-8")
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


def test_reports_added_ids_and_duplicates(tmp_path):
    result = compare_jobs.compare([1, 2], [2, 2, 5], [], [])

    assert result["added"] == [5]
    assert result["removed"] == [1]
    assert result["duplicate_ids_in_new"] == 1


def test_empty_lists_pass_and_empty_files_are_fine(tmp_path, capsys):
    assert run(tmp_path, [], []) == 0
    assert "baseline 0 jobs, new 0 jobs" in capsys.readouterr().out


def test_report_file_is_written(tmp_path):
    report = tmp_path / "out" / "report.json"

    run(tmp_path, [1, 2], [1], "--must-exclude", "2", "--report", str(report))

    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["passed"] is True and data["removed"] == [2]


def test_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(SystemExit) as exc:
        compare_jobs.main(["--baseline", str(tmp_path / "nope.json"), "--new", write_jobs(tmp_path / "n.json", [1])])
    assert "baseline file not found" in str(exc.value)


@pytest.mark.parametrize("content", ['{"a": 1}', "not json", '[{"company": "x"}]', '["str"]'])
def test_bad_shapes_are_rejected(tmp_path, content):
    bad = tmp_path / "bad.json"
    bad.write_text(content, encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        compare_jobs.main(["--baseline", str(bad), "--new", write_jobs(tmp_path / "n.json", [1])])
    assert str(exc.value).startswith("Error:")


def write_thread(path, ids):
    path.write_text(json.dumps({"id": 99, "children": [{"id": i} for i in ids]}), encoding="utf-8")
    return str(path)


def test_ids_the_thread_lost_are_not_blamed_on_the_code(tmp_path, capsys):
    thread = write_thread(tmp_path / "t.json", [1, 4, 9])  # 3 left the thread, 9 is new and was kept

    code = run(tmp_path, [1, 3, 4], [1, 4, 9], "--thread", thread)

    out = capsys.readouterr().out
    assert code == 0
    assert "gone from the thread (not the code): [3]" in out
    assert "DROPPED BY THE CODE, not in must-exclude: []" in out


def test_a_post_the_code_dropped_without_being_listed_fails(tmp_path, capsys):
    thread = write_thread(tmp_path / "t.json", [1, 2, 3])  # 3 is still in the thread but missing from the output

    code = run(tmp_path, [1, 2, 3], [1, 2], "--thread", thread)

    assert code == 1
    assert "DROPPED BY THE CODE, not in must-exclude: [3]" in capsys.readouterr().out


def test_a_listed_exclusion_is_not_an_unexpected_drop(tmp_path):
    thread = write_thread(tmp_path / "t.json", [1, 2])

    assert run(tmp_path, [1, 2], [1], "--thread", thread, "--must-exclude", "2") == 0


def test_an_added_id_that_is_not_in_the_thread_fails(tmp_path):
    thread = write_thread(tmp_path / "t.json", [1])

    assert run(tmp_path, [1], [1, 7], "--thread", thread) == 1


@pytest.mark.parametrize("content", ["[]", '{"children": "x"}', '{"children": [{"id": "a"}]}', "nope"])
def test_bad_thread_file_is_rejected(tmp_path, content):
    bad = tmp_path / "bad_thread.json"
    bad.write_text(content, encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        run(tmp_path, [1], [1], "--thread", str(bad))
    assert str(exc.value).startswith("Error:")
