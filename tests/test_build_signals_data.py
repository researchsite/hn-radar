"""site/tools/build_signals_data.py: one public file with the Show HN and pulse result of every job (issue 56)."""

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from hnradar.models import Job, ShowHNCandidate, ShowHNResult
from hnradar.pulse.schema import PulseItem, PulseResult
from hnradar.showhn import build_result
from hnradar.sources import hn
from hnradar.steps import find_story, story_from_result

SCRIPT = Path(__file__).resolve().parent.parent / "site" / "tools" / "build_signals_data.py"
spec = importlib.util.spec_from_file_location("build_signals_data", SCRIPT)
bsd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bsd)

ESC, BELL, RLO = chr(27), chr(7), chr(0x202E)


def NOW():
    return datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)


def job(cid, company="Acme", url="https://acme.com/careers"):
    return Job(comment_id=cid, company=company, role="Dev", stack=["Python"], url=url, raw_text=company)


def cand(story_id=900, title="Show HN: Acme", num_comments=10, domain_match=True):
    return ShowHNCandidate(story_id=story_id, title=title, points=5, num_comments=num_comments, url="https://acme.com", domain_match=domain_match)


def showhn(cid, tmp_path, candidates, domain="acme.com", low_confidence=False, platform=False):
    d = tmp_path / "showhn"
    d.mkdir(exist_ok=True)
    result = ShowHNResult(query="Acme", domain=domain, candidates=candidates, total_hits=len(candidates), truncated=False,
                          low_confidence=low_confidence, platform_domain=platform)
    (d / f"{cid}.json").write_text(result.model_dump_json(), encoding="utf-8")
    return d


def pulse(story_id, tmp_path, items, file_story_id=None):
    d = tmp_path / "pulse"
    d.mkdir(exist_ok=True)
    result = PulseResult(story_id=file_story_id or story_id, title="t", model="m", comments_used=3, comments_total=3, items=items)
    (d / f"{story_id}.json").write_text(result.model_dump_json(), encoding="utf-8")
    return d


def thread(ids_texts):
    def fetch(sid, **_):
        return {"id": sid, "type": "story", "title": "t",
                "children": [{"id": i, "type": "comment", "text": t, "children": []} for i, t in ids_texts]}
    return fetch


BUG = PulseItem(kind="bug", summary="It crashes on start", comment_ids=[901, 902])


def sig(j, tmp_path, fetch=None):
    return bsd.signal_for(j, tmp_path / "showhn", tmp_path / "pulse", fetch or thread([(901, "It crashed for me"), (902, "Same here")]))


def test_a_job_without_a_link_or_on_a_job_board_has_no_story_and_a_reason(tmp_path):
    assert sig(job(1, url=None), tmp_path).note == "no domain in the job link"
    s = sig(job(2, url="https://www.linkedin.com/jobs/view/1"), tmp_path)
    assert s.status == "no_show_hn" and s.note.startswith("platform domain (linkedin.com)")
    assert sig(job(3, url="https://[bad"), tmp_path).status == "no_show_hn"


def test_a_job_without_a_current_show_hn_file_is_not_checked(tmp_path):
    assert sig(job(1), tmp_path) is None  # no file
    showhn(1, tmp_path, [cand()], domain="other.com")
    assert sig(job(1), tmp_path) is None  # made for another link: stale, not guessed


def test_no_story_that_links_to_the_domain(tmp_path):
    showhn(1, tmp_path, [cand(domain_match=False)])
    s = sig(job(1), tmp_path)
    assert s.status == "no_show_hn" and s.note == "no Show HN links to acme.com" and s.show_hn is None
    showhn(2, tmp_path, [cand()], low_confidence=True)
    assert sig(job(2), tmp_path).status == "no_show_hn"


def test_a_story_without_a_saved_pulse(tmp_path):
    showhn(1, tmp_path, [cand(num_comments=10)])
    s = sig(job(1), tmp_path)
    assert s.status == "no_pulse" and s.show_hn.story_id == 900 and s.show_hn.url.endswith("item?id=900") and s.problem is None
    showhn(2, tmp_path, [cand(num_comments=2)])
    assert sig(job(2), tmp_path).status == "not_enough_signal"


def test_a_cited_problem_carries_its_comments(tmp_path):
    showhn(1, tmp_path, [cand()])
    pulse(900, tmp_path, [BUG])
    s = sig(job(1), tmp_path)
    assert s.status == "ok" and s.problem.summary == "It crashes on start" and s.problem.comment_ids == [901, 902]
    assert [(e.comment_id, e.text, e.url) for e in s.evidence] == [
        (901, "It crashed for me", "https://news.ycombinator.com/item?id=901"), (902, "Same here", "https://news.ycombinator.com/item?id=902")]


def test_a_problem_citing_a_comment_that_is_not_in_the_thread_is_not_shown(tmp_path):
    showhn(1, tmp_path, [cand()])
    pulse(900, tmp_path, [PulseItem(kind="bug", summary="Made up", comment_ids=[901, 999])])
    s = sig(job(1), tmp_path)
    assert s.status == "no_cited_problem" and s.problem is None and s.evidence == [] and s.show_hn.story_id == 900


def test_only_bug_items_count(tmp_path):
    showhn(1, tmp_path, [cand()])
    pulse(900, tmp_path, [PulseItem(kind="pro", summary="Nice", comment_ids=[901])])
    assert sig(job(1), tmp_path).status == "no_cited_problem"


def test_evidence_and_titles_are_cleaned_and_cut(tmp_path):
    showhn(1, tmp_path, [cand(title=f"Show HN: Acme{ESC}[31m{RLO}")])
    pulse(900, tmp_path, [PulseItem(kind="bug", summary=f"Bad{BELL} thing", comment_ids=[901])])
    s = sig(job(1), tmp_path, thread([(901, "x" * 900 + ESC + "[31m")]))
    assert ESC not in s.show_hn.title and RLO not in s.show_hn.title and BELL not in s.problem.summary
    assert len(s.evidence[0].text) <= bsd.EVIDENCE_CHARS and ESC not in s.evidence[0].text


def test_a_pulse_file_for_another_story_fails(tmp_path):
    showhn(1, tmp_path, [cand()])
    pulse(900, tmp_path, [BUG], file_story_id=123)
    with pytest.raises(bsd.SignalsBuildError, match="is for story 123, not 900"):
        sig(job(1), tmp_path)


@pytest.mark.parametrize("breaker", ["bad_showhn", "bad_pulse"])
def test_a_broken_saved_file_fails_instead_of_being_skipped(tmp_path, breaker):
    showhn(1, tmp_path, [cand()])
    if breaker == "bad_showhn":
        (tmp_path / "showhn" / "1.json").write_text("{", encoding="utf-8")
    else:
        pulse(900, tmp_path, [BUG])
        (tmp_path / "pulse" / "900.json").write_text('{"story_id": "x"}', encoding="utf-8")
    with pytest.raises(bsd.SignalsBuildError, match="cannot read the"):
        sig(job(1), tmp_path)


def test_an_unreadable_thread_fails_the_run(tmp_path):
    showhn(1, tmp_path, [cand()])
    pulse(900, tmp_path, [BUG])

    def down(sid, **_):
        raise hn.HNError("HN is down")

    with pytest.raises(bsd.SignalsBuildError, match="could not read the thread of story 900: HN is down"):
        sig(job(1), tmp_path, down)
    with pytest.raises(bsd.SignalsBuildError, match="is not a story"):
        sig(job(1), tmp_path, lambda sid, **_: {"id": sid, "type": "comment"})


def test_the_builder_picks_the_same_story_as_the_radar():
    """find_story (live search) and the offline builder share story_from_result: same result, same story."""
    hits = [{"story_id": 7, "title": "Show HN: Other", "points": 1, "num_comments": 1, "url": "https://elsewhere.com/x"},
            {"story_id": 8, "title": "Show HN: Acme", "points": 9, "num_comments": 9, "url": "https://acme.com/x"}]
    live = find_story("Acme", "https://acme.com/careers", lambda company, domain: {"hits": hits, "total_hits": 2})
    offline = story_from_result("acme.com", build_result("Acme", "acme.com", hits, 2))
    assert live.story == offline.story and live.story.story_id == 8 and live.skip is None


def test_build_counts_unchecked_jobs_and_keys_entries_by_comment_id(tmp_path):
    showhn(1, tmp_path, [cand()])
    jobs = [job(1), job(2), job(3, url=None)]
    file, unchecked = bsd.build(jobs, tmp_path / "showhn", tmp_path / "pulse", thread([]), NOW)
    assert unchecked == 1 and sorted(file.jobs) == ["1", "3"] and file.generated_at == "2026-11-22T12:00:00+00:00"
    assert file.jobs["1"].status == "no_pulse" and file.jobs["3"].status == "no_show_hn"


def test_the_model_refuses_an_ok_entry_that_is_not_backed_by_its_comments():
    story = bsd.SignalStory(story_id=1, title="t", domain="a.com", url="u")
    problem = bsd.SignalProblem(summary="s", comment_ids=[5])
    with pytest.raises(ValidationError):
        bsd.Signal(status="ok", show_hn=story, problem=problem, evidence=[])
    with pytest.raises(ValidationError):
        bsd.Signal(status="ok", show_hn=story, problem=problem, evidence=[bsd.SignalEvidence(comment_id=6, text="t", url="u")])
    with pytest.raises(ValidationError):
        bsd.Signal(status="no_pulse", show_hn=story, problem=problem)
    with pytest.raises(ValidationError):
        bsd.Signal(status="no_pulse")  # a status that names a story needs the story
    with pytest.raises(ValidationError):
        bsd.SignalProblem(summary="s", comment_ids=[])


def run_main(tmp_path, jobs):
    jobs_file = tmp_path / "jobs.json"
    jobs_file.write_text(json.dumps([j.model_dump(mode="json") for j in jobs]), encoding="utf-8")
    out = tmp_path / "out" / "signals.json"
    code = bsd.main(["--jobs", str(jobs_file), "--showhn", str(tmp_path / "showhn"), "--pulse", str(tmp_path / "pulse"), "--out", str(out)])
    return code, out


def test_main_writes_a_file_the_model_can_read_back(tmp_path, capsys):
    showhn(1, tmp_path, [cand()])
    code, out = run_main(tmp_path, [job(1), job(2, url=None)])
    assert code == 0 and "Wrote" in capsys.readouterr().out
    read = bsd.SignalsFile.model_validate_json(out.read_text(encoding="utf-8"))
    assert set(read.jobs) == {"1", "2"}
    raw = json.loads(out.read_text(encoding="utf-8"))
    assert "problem" not in raw["jobs"]["1"] and "note" not in raw["jobs"]["1"]  # empty fields are left out


def test_main_writes_nothing_when_a_file_is_broken(tmp_path, capsys):
    showhn(1, tmp_path, [cand()])
    (tmp_path / "showhn" / "1.json").write_text("{", encoding="utf-8")
    code, out = run_main(tmp_path, [job(1)])
    assert code == 1 and not out.exists() and "Error: cannot read the Show HN file" in capsys.readouterr().err


def test_main_rejects_a_bad_jobs_file(tmp_path, capsys):
    code = bsd.main(["--jobs", str(tmp_path / "missing.json"), "--out", str(tmp_path / "o.json")])
    assert code == 1 and "Error:" in capsys.readouterr().err


def test_evidence_is_cut_at_exactly_the_cap_and_the_cut_is_visible(tmp_path):
    showhn(1, tmp_path, [cand()])
    pulse(900, tmp_path, [PulseItem(kind="bug", summary="s", comment_ids=[901, 902, 903])])
    n = bsd.EVIDENCE_CHARS
    s = sig(job(1), tmp_path, thread([(901, "a" * (n - 1)), (902, "b" * n), (903, "c" * (n + 1))]))
    exact_minus_one, exact, over = (e.text for e in s.evidence)
    assert exact_minus_one == "a" * (n - 1) and exact == "b" * n  # at the cap: untouched
    assert over == "c" * (n - 1) + "…" and len(over) == n  # over it: cut, ending in the marker


def test_a_saved_result_for_another_company_name_is_not_used(tmp_path):
    showhn(1, tmp_path, [cand()])  # saved for the query "Acme"
    assert sig(job(1, company="Acme"), tmp_path) is not None
    assert sig(job(1, company="Acme Robotics"), tmp_path) is None  # the post was corrected: stale, not checked


def test_a_rejected_thread_request_is_a_controlled_failure(tmp_path, capsys):
    import httpx

    showhn(1, tmp_path, [cand()])
    pulse(900, tmp_path, [BUG])

    def refused(sid, **_):
        raise httpx.ConnectError("connection refused")

    with pytest.raises(bsd.SignalsBuildError, match="could not read the thread of story 900"):
        sig(job(1), tmp_path, refused)
    for error in (httpx.ReadTimeout("slow"), httpx.HTTPStatusError("503", request=httpx.Request("GET", "https://x"), response=httpx.Response(503))):
        def failing(sid, _e=error, **_):
            raise _e
        with pytest.raises(bsd.SignalsBuildError):
            sig(job(1), tmp_path, failing)
