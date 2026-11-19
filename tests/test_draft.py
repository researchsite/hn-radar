import json

import pytest

from hnradar.draft.schema import MAX_LINE_CHARS
from hnradar.draft.write import MAX_EVIDENCE_CHARS, write_draft
from hnradar.pulse.cluster import PulseError
from hnradar.showhn import url_domain

REPO = {"name": "fastq", "language": "Python", "description": "A job queue"}
CITED = [{"id": 11, "text": "It crashes on start"}, {"id": 12, "text": "Same here on Linux"}]


class FakeClient:
    name = "fake:1b"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts = []

    def complete(self, system, user, schema):
        self.prompts.append(user)
        return self.replies.pop(0)


def line(text, ids=()):
    return {"text": text, "comment_ids": list(ids)}


def good_lines():
    return [
        line("Hello Acme, I am octo."),
        line("A Show HN that links to acme.com got reports of a crash on start.", [11, 12]),
        line("My repo fastq could help: it restarts jobs."),
        line("I would start with a reproduction."),
        line("Could we talk for ten minutes?"),
    ]


def draft(*replies, cited=CITED):
    client = FakeClient(*(json.dumps({"lines": r}) if not isinstance(r, str) else r for r in replies))
    result = write_draft(
        username="octo", company="Acme", domain="acme.com", story_id=7, repo=REPO,
        problem="It crashes on start", cited=cited, client=client,
    )
    return result, client


def test_a_good_draft_keeps_its_citations_and_gets_evidence_links():
    result, _ = draft(good_lines())
    assert [l.comment_ids for l in result.lines] == [[], [11, 12], [], [], []]
    assert [e.url for e in result.evidence] == [
        "https://news.ycombinator.com/item?id=11",
        "https://news.ycombinator.com/item?id=12",
    ]
    assert result.repo == "fastq" and result.model == "fake:1b"
    assert (result.repo_language, result.repo_description) == ("Python", "A job queue")


def test_evidence_text_is_cut():
    long = [{"id": 11, "text": "x" * 1000}]
    lines = good_lines()
    lines[1] = line("A Show HN that links to acme.com got a crash, fastq.", [11])
    result, _ = draft(lines, cited=long)
    assert len(result.evidence[0].text) == MAX_EVIDENCE_CHARS


def test_the_comments_reach_the_model_as_json_data():
    _, client = draft(good_lines())
    assert '"comments": [{"id": 11, "text": "It crashes on start"}' in client.prompts[0]


@pytest.mark.parametrize(
    "mutate, reason",
    [
        (lambda ls: ls[:4], "at least 5"),  # one line short
        (lambda ls: ls + [line("extra")], "at most 5"),  # one line over
        (lambda ls: [line("x" * (MAX_LINE_CHARS + 1))] + ls[1:], "at most"),  # exact limit + 1
        (lambda ls: [line("", [])] + ls[1:], "at least 1"),
        (lambda ls: [line(l["text"]) for l in ls], "no line cites"),  # no citation anywhere
        (lambda ls: [ls[0], line(ls[1]["text"], [11, 999]), *ls[2:]], "not in the cited"),
        (lambda ls: [*ls[:2], line("My code could help."), *ls[3:]], "does not name the repo"),
        (lambda ls: [line("Loved your Show HN!"), *ls[1:]], "your show hn"),
        (lambda ls: [line("I saw their Show HN"), *ls[1:]], "their show hn"),
        (lambda ls: [line("You launched it, fastq"), *ls[1:]], "you launched"),
    ],
)
def test_invalid_drafts_are_rejected_twice_then_fail(mutate, reason):
    bad = mutate(good_lines())
    with pytest.raises(PulseError) as err:
        draft(bad, bad)
    assert reason in str(err.value).lower()


def test_one_retry_recovers_and_the_reason_is_sent_back():
    bad = [line(l["text"]) for l in good_lines()]
    result, client = draft(bad, good_lines())
    assert result.lines[1].comment_ids == [11, 12]
    assert "rejected (no line cites a comment)" in client.prompts[1]


def test_not_json_is_rejected_without_echoing_it():
    with pytest.raises(PulseError) as err:
        draft("not json at all SECRET", "still not json SECRET")
    assert "SECRET" not in str(err.value)


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://acme.com/careers", "acme.com"),
        ("http://www.acme.com", "acme.com"),
        ("https://ACME.com./x", "acme.com"),
        ("https://jobs.acme.com/1", "jobs.acme.com"),
        ("https://evil.com\\@acme.com", None),
        ("javascript://acme.com", None),
        ("ftp://acme.com", None),
        ("https://localhost/x", None),
        ("https://[::1/x", None),
        ("", None),
        (None, None),
        (42, None),
    ],
)
def test_url_domain(url, expected):
    assert url_domain(url) == expected
