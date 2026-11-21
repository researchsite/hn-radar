"""Tests for site/tools/build_pulse_data.py. The model and Hacker News are replaced by fakes: no network, no Ollama."""

import importlib.util
import json
from pathlib import Path

import httpx
import pytest

from hnradar.models import ShowHNCandidate, ShowHNResult
from hnradar.pulse.schema import PulseResult
from hnradar.sources import hn, ollama

SCRIPT = Path(__file__).resolve().parents[1] / "site" / "tools" / "build_pulse_data.py"
spec = importlib.util.spec_from_file_location("build_pulse_data", SCRIPT)
bpd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bpd)


def cand(story_id, comments=10, domain_match=True, title=None):
    return ShowHNCandidate(story_id=story_id, title=title or f"Show HN {story_id}", points=1, num_comments=comments,
                           domain_match=domain_match)


def write_showhn(folder, job_id, *cands, platform=False):
    folder.mkdir(exist_ok=True)
    result = ShowHNResult(query="Acme", domain="acme.com", candidates=list(cands), total_hits=len(cands), truncated=False,
                          low_confidence=not any(c.domain_match for c in cands), platform_domain=platform)
    (folder / f"{job_id}.json").write_text(result.model_dump_json(), encoding="utf-8")


class FakeClient:
    name = "fake:1b"

    def __init__(self, fail=False):
        self.fail = fail
        self.calls = 0

    def complete(self, system, user, schema):
        self.calls += 1
        if self.fail:
            raise ollama.ModelError("model not found")
        return json.dumps({"items": [{"kind": "pro", "summary": "People like it", "comment_ids": [100]}]})


def story(n_comments):
    return lambda story_id, **_: {"id": story_id, "type": "story", "title": f"T{story_id}",
                                   "children": [{"id": 100 + i, "type": "comment", "text": f"comment {i}", "children": []}
                                                for i in range(n_comments)]}


def run(stories, tmp_path, client=None, fetch=None, **kw):
    kw.setdefault("sleep", lambda _s: None)
    return bpd.build(stories, tmp_path / "pulse", client or FakeClient(), fetch=fetch or story(5), **kw)


def test_pick_domain_matched_stories_most_commented_first_each_once(tmp_path):
    d = tmp_path / "showhn"
    write_showhn(d, 1, cand(10, 50), cand(11, 5), cand(12, 30), cand(13, 99, domain_match=False))
    write_showhn(d, 2, cand(10, 50))  # the same story for another job
    assert bpd.pick_stories(d, per_company=2) == [(10, "Show HN 10"), (12, "Show HN 12")]


def test_pick_skips_platform_hosts_few_comments_and_broken_files(tmp_path):
    d = tmp_path / "showhn"
    write_showhn(d, 1, cand(10, 50), platform=True)
    write_showhn(d, 2, cand(11, 2))
    (d / "3.json").write_text("{", encoding="utf-8")
    (d / "index.json").write_text("{}", encoding="utf-8")
    assert bpd.pick_stories(d) == []
    assert bpd.pick_stories(d, min_comments=0) == [(11, "Show HN 11")]


def test_name_matches_are_opt_in(tmp_path):
    d = tmp_path / "showhn"
    write_showhn(d, 1, cand(10, 50, domain_match=False))
    assert bpd.pick_stories(d) == []
    assert bpd.pick_stories(d, name_matches=True) == [(10, "Show HN 10")]


def test_missing_folder_is_a_clear_error(tmp_path):
    with pytest.raises(bpd.PulseBuildError, match="build_showhn_data"):
        bpd.pick_stories(tmp_path / "nope")


def test_writes_a_valid_pulse_file_per_story(tmp_path):
    counts = run([(7, "T7")], tmp_path)
    assert counts["written"] == 1
    result = PulseResult.model_validate_json((tmp_path / "pulse" / "7.json").read_text(encoding="utf-8"))
    assert result.story_id == 7 and result.items[0].comment_ids == [100]


def test_too_few_comments_is_not_a_failure_and_writes_nothing(tmp_path):
    counts = run([(7, "T7")], tmp_path, fetch=story(2))
    assert counts["no_signal"] == 1 and counts["failed"] == [] and not (tmp_path / "pulse" / "7.json").exists()


def test_existing_file_is_skipped_unless_refresh(tmp_path):
    client = FakeClient()
    run([(7, "T7")], tmp_path, client)
    assert run([(7, "T7")], tmp_path, client)["skipped"] == 1 and client.calls == 1
    assert run([(7, "T7")], tmp_path, client, refresh=True)["written"] == 1 and client.calls == 2


def test_a_failed_story_writes_nothing_and_the_rest_continue(tmp_path):
    def fetch(story_id, **_):
        if story_id == 1:
            raise hn.HNError("down")
        return story(5)(story_id)

    counts = run([(1, "A"), (2, "B")], tmp_path, fetch=fetch)
    assert counts["written"] == 1 and [f[0] for f in counts["failed"]] == [1] and not counts["stopped"]


@pytest.mark.parametrize("error", [httpx.ConnectError("x"), ollama.ModelError("x"), OSError("x"), ValueError("not a story")])
def test_expected_errors_are_failures_not_crashes(tmp_path, error):
    def boom(story_id, **_):
        raise error

    assert len(run([(1, "A")], tmp_path, fetch=boom)["failed"]) == 1


def test_stops_after_three_failures_in_a_row(tmp_path):
    client = FakeClient(fail=True)
    counts = run([(i, f"T{i}") for i in range(1, 8)], tmp_path, client)
    assert counts["stopped"] and len(counts["failed"]) == 3 and client.calls == 3


def test_a_success_resets_the_failure_count(tmp_path):
    def fetch(story_id, **_):
        if story_id in (1, 2, 4, 5):
            raise hn.HNError("down")
        return story(5)(story_id)

    counts = run([(i, f"T{i}") for i in range(1, 7)], tmp_path, fetch=fetch)
    assert not counts["stopped"] and counts["written"] == 2 and len(counts["failed"]) == 4


def test_limit_and_pause(tmp_path):
    slept = []
    counts = run([(1, "A"), (2, "B"), (3, "C")], tmp_path, limit=2, pause=0.5, sleep=slept.append)
    assert counts["written"] == 2 and slept == [0.5, 0.5]


def test_not_a_story_is_an_error(tmp_path):
    counts = run([(1, "A")], tmp_path, fetch=lambda story_id, **_: {"id": 1, "type": "comment"})
    assert len(counts["failed"]) == 1


def test_main_summary_and_exit_codes(tmp_path, monkeypatch, capsys):
    d = tmp_path / "showhn"
    write_showhn(d, 1, cand(10, 50))
    real_build = bpd.build
    monkeypatch.setattr(bpd, "build", lambda stories, out, client, **k: real_build(stories, out, FakeClient(), fetch=story(5), **k))
    code = bpd.main(["--showhn", str(d), "--out", str(tmp_path / "pulse"), "--pause", "0"])
    assert code == 0 and "1 written" in capsys.readouterr().out
    assert bpd.main(["--showhn", str(tmp_path / "missing")]) == 1
    assert bpd.main(["--showhn", str(d), "--limit", "0"]) == 1
    assert bpd.main(["--showhn", str(d), "--per-company", "0"]) == 1


def test_several_posts_of_one_company_share_the_limit(tmp_path):
    d = tmp_path / "showhn"
    write_showhn(d, 1, cand(10, 50), cand(11, 40))
    write_showhn(d, 2, cand(12, 45), cand(13, 5))  # same company name ("Acme"), another hiring post
    assert [s for s, _ in bpd.pick_stories(d, per_company=2)] == [10, 12]
    assert [s for s, _ in bpd.pick_stories(d, per_company=3)] == [10, 12, 11]


def test_refresh_that_finds_too_few_comments_removes_the_old_summary(tmp_path):
    run([(7, "T7")], tmp_path)
    assert (tmp_path / "pulse" / "7.json").exists()
    counts = run([(7, "T7")], tmp_path, fetch=story(1), refresh=True)
    assert counts["no_signal"] == 1 and not (tmp_path / "pulse" / "7.json").exists()


def test_unreadable_show_hn_files_are_reported_not_hidden(tmp_path, capsys):
    d = tmp_path / "showhn"
    write_showhn(d, 1, cand(10, 50))
    (d / "2.json").write_text("{", encoding="utf-8")
    problems = []
    assert bpd.pick_stories(d, problems=problems) == [(10, "Show HN 10")]
    assert problems == ["2.json"]
    code = bpd.main(["--showhn", str(d), "--out", str(tmp_path / "pulse"), "--limit", "1", "--pause", "0"])
    assert "1 Show HN file(s) could not be read" in capsys.readouterr().err


def test_empty_folder_and_empty_list(tmp_path, capsys):
    d = tmp_path / "showhn"
    d.mkdir()
    assert bpd.pick_stories(d) == []
    assert bpd.main(["--showhn", str(d), "--out", str(tmp_path / "pulse")]) == 0
    assert "No story to summarise" in capsys.readouterr().out
    counts = run([], tmp_path)
    assert counts["written"] == 0 and counts["failed"] == []


def test_limits_are_inclusive_at_the_edge(tmp_path):
    d = tmp_path / "showhn"
    write_showhn(d, 1, cand(10, 3), cand(11, 2), cand(12, 9), cand(13, 8))
    assert [s for s, _ in bpd.pick_stories(d, min_comments=3, per_company=10)] == [12, 13, 10]  # 3 is enough, 2 is not
    assert [s for s, _ in bpd.pick_stories(d, min_comments=3, per_company=3)] == [12, 13, 10]    # exactly per_company eligible
    assert [s for s, _ in bpd.pick_stories(d, min_comments=3, per_company=2)] == [12, 13]


def test_two_employers_with_the_same_name_and_different_domains_do_not_share_a_limit(tmp_path):
    d = tmp_path / "showhn"
    d.mkdir()
    for job_id, domain, story_id in ((1, "acme.com", 10), (2, "acme.io", 20)):
        result = ShowHNResult(query="Acme", domain=domain, candidates=[cand(story_id, 50)], total_hits=1, truncated=False, low_confidence=False)
        (d / f"{job_id}.json").write_text(result.model_dump_json(), encoding="utf-8")
    assert [s for s, _ in bpd.pick_stories(d, per_company=1)] == [10, 20]


def test_prune_stale_removes_summaries_of_threads_that_no_longer_have_enough_comments(tmp_path):
    d = tmp_path / "showhn"
    out = tmp_path / "pulse"
    out.mkdir()
    write_showhn(d, 1, cand(10, 2), cand(11, 20))
    (out / "10.json").write_text("{}", encoding="utf-8")
    (out / "11.json").write_text("{}", encoding="utf-8")
    (out / "99.json").write_text("{}", encoding="utf-8")  # a story no Show HN file lists: left alone
    assert bpd.prune_stale(d, out, 3) == 1
    assert not (out / "10.json").exists() and (out / "11.json").exists() and (out / "99.json").exists()
