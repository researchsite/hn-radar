import pytest

from hnradar.showhn import MAX_CANDIDATES, PLATFORM_DOMAINS, build_result, host_matches, is_platform_domain, normalize_domain


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


@pytest.mark.parametrize(
    "domain",
    [
        "notion.so", "github.com", "medium.com", "arxiv.org", "ycombinator.com",  # exact
        "team.notion.so", "jobs.ashbyhq.com", "gist.github.com", "acme.github.io",  # subdomain of a listed host
        "a.b.c.herokuapp.com",
        "NOTION.SO", "www.github.com", "  Jobs.Lever.co ",  # case, www. and spaces are normalised
        "x.com", "youtu.be", "grnh.se", "forms.gle",
    ],
)
def test_platform_domains_are_flagged(domain):
    assert is_platform_domain(domain) is True


@pytest.mark.parametrize(
    "domain",
    [
        "supabase.com", "posthog.com", "fly.io", "openrent.co.uk",  # ordinary companies
        "mynotion.so", "notnotion.so", "notion.so.evil.com", "github.com.evil.io",  # look-alikes
        "box.com", "max.com", "linkedin.com.au", "githubusercontent.org",  # suffix without a dot boundary
        "so", "com", "io",  # a bare TLD is never a platform
    ],
)
def test_ordinary_domains_and_lookalikes_are_not_platforms(domain):
    if "." not in domain:
        with pytest.raises(ValueError):  # not a valid domain at all: rejected, same as before
            is_platform_domain(domain)
    else:
        assert is_platform_domain(domain) is False


@pytest.mark.parametrize("raw", ["", "   ", "https://github.com", "github.com/acme", "git hub.com", "a..com"])
def test_invalid_domain_is_rejected_not_treated_as_platform(raw):
    with pytest.raises(ValueError):
        is_platform_domain(raw)


def test_every_listed_platform_is_a_normalised_domain():
    # a typo ("Github.com", "www.x.com", a path) would silently never match
    assert PLATFORM_DOMAINS
    assert all(normalize_domain(d) == d for d in PLATFORM_DOMAINS)


def test_platform_domain_is_low_confidence_even_when_a_story_links_to_it():
    result = build_result("Notion", "notion.so", [hit(1, points=9, url="https://notion.so/blog/x")], 1)

    assert result.candidates[0].domain_match is True  # still shown: the person decides
    assert result.platform_domain is True
    assert result.low_confidence is True


def test_platform_subdomain_is_flagged_too():
    assert build_result("Acme", "acme.github.io", [hit(1, url="https://acme.github.io/")], 1).platform_domain is True


def test_ordinary_domain_is_unchanged():
    result = build_result("Supabase", "supabase.com", [hit(1, url="https://supabase.com/launch")], 1)

    assert result.platform_domain is False
    assert result.low_confidence is False


def test_no_domain_is_not_a_platform_domain():
    result = build_result("Acme", None, [hit(1, url="https://github.com/acme")], 1)

    assert result.platform_domain is False
    assert result.low_confidence is True  # unchanged: no domain given


def test_platform_domain_with_no_hits_is_still_flagged():
    result = build_result("Notion", "www.NOTION.so", [], 0)

    assert (result.domain, result.platform_domain, result.low_confidence) == ("notion.so", True, True)
