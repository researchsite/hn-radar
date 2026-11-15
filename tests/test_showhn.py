import pytest

from hnradar.showhn import MAX_CANDIDATES, build_result, host_matches, normalize_domain


def hit(story_id, *, points=1, url=None, title="Show HN: thing"):
    return {"story_id": story_id, "title": title, "points": points, "num_comments": 0, "url": url}


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://acme.com/launch", True),
        ("https://www.acme.com/", True),
        ("https://blog.acme.com/post", True),
        ("https://ACME.com", True),
        ("https://acme.com./launch", True),  # a trailing dot is the same host
        ("https://www.acme.com.", True),
        ("https://acme.com..", False),
        ("https://evilacme.com", False),  # suffix without a dot boundary
        ("https://acme.com.evil.io", False),  # domain only as a prefix of another host
        ("https://github.com/acme/acme.com", False),  # domain only in the path
        ("https://example.org/?next=acme.com", False),  # domain only in the query
        ("https://evil.com\\@acme.com", False),  # parsers disagree on the host: never a match
        ("https://acme.com\\@evil.com", False),
        ("https://attacker|.acme.com", False),  # malformed host: allowlist, not suffix match
        ("https://att acker.acme.com", False),
        ("https://a_b.acme.com", False),
        ("https://exämple.acme.com", False),  # non-ASCII: only punycode can match
        ("javascript://acme.com/%0aalert(1)", False),  # only http(s) can match
        ("ftp://acme.com", False),
        ("", False),
        (None, False),
        (["https://acme.com"], False),  # not text: must not raise
        ({"u": 1}, False),
        (5, False),
        ("http://[bad", False),  # malformed URL must not raise
        ("not a url", False),
    ],
)
def test_host_matches(url, expected):
    assert host_matches(url, "acme.com") is expected


@pytest.mark.parametrize("raw, expected", [("acme.com", "acme.com"), ("  WWW.Acme.COM ", "acme.com")])
def test_normalize_domain_accepts_bare_hosts(raw, expected):
    assert normalize_domain(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "", "   ", "https://acme.com", "acme.com/path", "acme .com",
        "acme.com:443", "acme.com?x=1", "acme.com#top", "user@acme.com",  # port, query, fragment, user info
        "acme", "acme..com", "-acme.com", "acme-.com", ".acme.com", "acme.com.", "www.",
    ],
)
def test_normalize_domain_rejects_everything_else(raw):
    with pytest.raises(ValueError):
        normalize_domain(raw)


def test_domain_matches_come_first_then_points_then_newest():
    hits = [
        hit(1, points=500, url="https://other.com"),
        hit(2, points=10, url="https://acme.com"),
        hit(3, points=10, url="https://other.org"),
        hit(4, points=10, url="https://www.acme.com"),  # ties with 2 on points: newer story first
    ]

    result = build_result("Acme", "acme.com", hits, total_hits=4)

    assert [c.story_id for c in result.candidates] == [4, 2, 1, 3]
    assert [c.domain_match for c in result.candidates] == [True, True, False, False]
    assert result.low_confidence is False
    assert result.truncated is False


def test_cut_to_five_is_reported_not_silent():
    hits = [hit(i, points=i) for i in range(1, 9)]

    result = build_result("Acme", None, hits, total_hits=120)

    assert len(result.candidates) == MAX_CANDIDATES == 5
    assert result.total_hits == 120
    assert result.truncated is True


def test_exactly_five_hits_is_not_truncated():
    hits = [hit(i) for i in range(1, 6)]

    result = build_result("Acme", None, hits, total_hits=5)

    assert len(result.candidates) == 5
    assert result.truncated is False


def test_without_domain_every_result_is_low_confidence():
    result = build_result("Linear", None, [hit(1, url="https://linear.app")], total_hits=1)

    assert result.low_confidence is True
    assert result.candidates[0].domain_match is False


def test_domain_with_no_matching_host_is_low_confidence():
    result = build_result("Linear", "linear.app", [hit(1, url="https://example.org")], total_hits=1)

    assert result.low_confidence is True
    assert result.domain == "linear.app"


def test_zero_hits_is_an_empty_low_confidence_result():
    result = build_result("Nobody", "nobody.dev", [], total_hits=0)

    assert result.candidates == []
    assert result.low_confidence is True
    assert result.truncated is False


def test_bad_domain_raises_before_ranking():
    with pytest.raises(ValueError):
        build_result("Acme", "https://acme.com", [hit(1)], total_hits=1)
