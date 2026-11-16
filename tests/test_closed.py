import json

import httpx
import pytest
from typer.testing import CliRunner

from hnradar.cli import app
from hnradar.closed import closing_reply_id, says_closed
from hnradar.models import Job

from conftest import ITEM_URL, SEARCH_RESPONSE, SEARCH_URL, STORY_ID

runner = CliRunner()


def reply(reply_id, author, text, children=()):
    return {"id": reply_id, "author": author, "text": text, "children": list(children)}


def post(*replies, author="poster"):
    return {"id": 1, "author": author, "text": "Acme | Engineer | REMOTE", "children": list(replies)}


@pytest.mark.parametrize(
    "text",
    [
        "Please don't apply anymore. Applications are closed.",
        "UPDATE: we're no longer hiring for this role.",
        "Position filled, thanks everyone!",
        "The role has been filled.",
        "We've now filled the position.",
        "Not currently accepting applications.",
        "Please do not apply any more.",
        "Applications closed&#x2F;filled as of today.",  # HTML entity, read as text
        "The position isn't filled yet, but applications are closed.",  # earlier negation, later plain notice
        "We are not hiring anymore, but thanks for asking. The role is not closed to relocation, position filled.",
        "Hiring has stopped.",
        "Position filled, thanks everyone! We're looking forward to the next round.",
        "Applications are closed. We're looking forward to next year.",
        "Applications are closed. Thanks, we are looking at other options.",
        "Applications are closed. We're still looking forward to your feedback.",
        "We're no longer taking applications for these roles.",
        "We are no longer hiring for this position.",
        "Not accepting new applications at this time.",
        "Applications are closed for this role.",
        "Don't apply now, position filled.",
        "Please don't apply anymore, applications are closed.",
        "We are not hiring.",
        "We're not hiring right now.",
        "Applicants should not apply.",
        "Please don't apply.",
        "We filled the role.",
        "We filled the open position, thanks!",
    ],
)
def test_closing_wording_is_detected(text):
    assert says_closed(text.replace("&#x2F;", "/"))
    assert closing_reply_id(post(reply(2, "poster", text))) == 2


@pytest.mark.parametrize(
    "text",
    [
        "Remote, not closed to relocation.",
        "We are a closed-source company.",
        "The office is enclosed in glass.",
        "Applications are not closed yet.",
        "The position isn't filled.",
        "Is the position filled?",
        "If the role is filled we will tell you.",
        "Please don't apply if you need a visa.",  # conditional, not a closing notice
        "Some positions are filled, but we're still hiring for this role.",
        "Roles are filled, still hiring for backend.",
        "The role is filled but the search is still open for others.",
        'We got a question: "Is the position filled?"',
        "Someone asked (is the role filled?)",
        "We are not hiring for the data team.",  # qualified: says nothing about this job
        "We are not hiring juniors, only seniors.",
        "Applicants should not apply without a CV.",
        "Please don't apply if you need a visa.",
        "If we hire someone, the position is filled.",
        "The position isn't filled yet, but still open.",
        "We filled the role of CTO last year, and now we hire engineers.",
        "Don't apply, unless you're in Berlin.",
        "Please don't apply, we only want seniors.",
        "We are not hiring, unless you are a senior.",
        "Applicants should not apply; we only take referrals.",
        "Don't apply now, unless you're in Berlin.",
        "Please don't apply anymore, unless you are a senior.",
        "Don't apply now if you need a visa.",
        "We are no longer hiring juniors.",
        "No longer hiring in the US, only in Europe.",
        "Not accepting applications from recruiters.",
        "Applications are closed to candidates outside the EU.",
        "Position filled unless you are a senior.",
        "Our role is closed-source software.",
        "We are hiring closed-source engineers.",
        "The position is closed-loop control.",
        "We filled the backend role, we're hiring for frontend.",
        "We filled the backend role. We are also hiring for frontend, see below.",
        "Position filled. We're looking for a frontend engineer.",
        "We are hiring!",
        "",
    ],
)
def test_other_wording_is_not_closing(text):
    assert not says_closed(text)
    assert closing_reply_id(post(reply(2, "poster", text))) is None


def test_reply_by_someone_else_with_the_same_words_is_ignored():
    assert closing_reply_id(post(reply(2, "stranger", "Applications are closed."))) is None


def test_poster_name_is_compared_ignoring_case():
    assert closing_reply_id(post(reply(2, "POSTER", "Position filled."), author="Poster")) == 2


def test_nested_reply_is_read_only_when_written_by_the_poster():
    nested_by_poster = reply(2, "stranger", "Is this open?", [reply(3, "poster", "Applications are closed.")])
    nested_by_other = reply(2, "poster", "Hi!", [reply(3, "stranger", "Applications are closed.")])
    assert closing_reply_id(post(nested_by_poster)) == 3
    assert closing_reply_id(post(nested_by_other)) is None


def test_poster_replying_twice_gives_the_first_closing_reply():
    comment = post(
        reply(2, "poster", "Thanks for the interest, we reply to everyone."),
        reply(3, "poster", "Position filled."),
        reply(4, "poster", "No longer hiring."),
    )
    assert closing_reply_id(comment) == 3


def test_no_replies_or_no_children_key():
    assert closing_reply_id(post()) is None
    assert closing_reply_id({"id": 1, "author": "poster", "text": "x"}) is None


def test_deleted_reply_with_no_text_is_skipped():
    comment = post(reply(2, "poster", None), reply(3, "poster", ""), reply(4, "poster", "Position filled."))
    assert closing_reply_id(comment) == 4


@pytest.mark.parametrize("author", [None, "", 5, ["poster"]])
def test_post_without_a_usable_author_is_never_closed(author):
    assert closing_reply_id(post(reply(2, "poster", "Position filled."), author=author)) is None
    assert closing_reply_id({"id": 1, "children": [reply(2, None, "Position filled.")]}) is None


@pytest.mark.parametrize(
    "children",
    [
        "not a list",
        None,
        [None, 5, "x", [], {"id": 2, "author": "poster", "text": 7}],
        [{"author": "poster", "text": "Position filled."}],  # no id: nothing to cite
        [{"id": True, "author": "poster", "text": "Position filled."}],
        [{"id": "2", "author": "poster", "text": "Position filled."}],
    ],
)
def test_malformed_replies_are_skipped_not_a_crash(children):
    assert closing_reply_id({"id": 1, "author": "poster", "children": children}) is None


def test_deeply_nested_thread_does_not_overflow_the_stack():
    node = reply(5000, "poster", "Position filled.")
    for i in range(4999, 1, -1):  # 5000 levels: recursion would raise RecursionError
        node = reply(i, "stranger", "x", [node])
    assert closing_reply_id(post(node)) == 5000


def test_very_long_reply_without_a_match_is_fast_and_negative():
    assert closing_reply_id(post(reply(2, "poster", "applications " * 50000))) is None


def test_job_defaults_so_older_jobs_json_still_loads():
    old = {"comment_id": 1, "company": "Acme", "raw_text": "x"}
    job = Job.model_validate(old)
    assert job.closed is False and job.closed_comment_id is None


def mock_thread(respx_mock, children):
    item = {"id": STORY_ID, "type": "story", "title": "Ask HN: Who is hiring? (November 2026)", "children": children}
    respx_mock.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=SEARCH_RESPONSE))
    respx_mock.get(ITEM_URL).mock(return_value=httpx.Response(200, json=item))


def test_fetch_hiring_flags_closed_jobs_with_the_citing_reply(respx_mock, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mock_thread(
        respx_mock,
        [
            {**post(reply(11, "poster", "Applications are closed."), author="poster"), "id": 101},
            {**post(reply(12, "stranger", "Applications are closed.")), "id": 102},
            {"id": 103, "text": "Initech | Dev | REMOTE", "children": [reply(13, "x", "Position filled.")]},  # no author
        ],
    )
    out = tmp_path / "jobs.json"

    result = runner.invoke(app, ["fetch-hiring", "--out", str(out)])

    assert result.exit_code == 0, result.output
    jobs = {j["comment_id"]: j for j in json.loads(out.read_text(encoding="utf-8"))}
    assert (jobs[101]["closed"], jobs[101]["closed_comment_id"]) == (True, 11)
    assert (jobs[102]["closed"], jobs[102]["closed_comment_id"]) == (False, None)
    assert (jobs[103]["closed"], jobs[103]["closed_comment_id"]) == (False, None)


def test_fetch_hiring_still_works_when_the_thread_has_no_authors_at_all(mock_hn, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "jobs.json"

    result = runner.invoke(app, ["fetch-hiring", "--out", str(out)])

    assert result.exit_code == 0, result.output
    assert not any(j["closed"] for j in json.loads(out.read_text(encoding="utf-8")))
