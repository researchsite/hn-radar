"""Rank Show HN search hits for a company (pure functions, no network)."""

import re
from urllib.parse import urlparse

from hnradar.models import ShowHNCandidate, ShowHNResult

MAX_CANDIDATES = 5
_LABEL = r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
_DOMAIN_RE = re.compile(rf"{_LABEL}(\.{_LABEL})+")  # at least two labels, ASCII (punycode for IDNs)


# Hosts that many unrelated people publish on: code hosts, blogs, page builders, job boards and application
# forms, video and file hosts. A story that links to one of these says little about any one company
# (rule 9). A subdomain of a listed host counts as listed, which also flags a company's own page on a
# platform (acme.github.io): the safe side. Start list from the ticket (#38) plus the hosts real job posts
# link to (measured on the November 2026 thread).
PLATFORM_DOMAINS = frozenset(
    {
        "github.com", "github.io", "gitlab.com", "medium.com", "substack.com", "notion.so", "notion.site",
        "docs.google.com", "drive.google.com", "forms.gle", "youtube.com", "youtu.be", "linkedin.com",
        "twitter.com", "x.com", "arxiv.org", "ycombinator.com",
        "vercel.app", "netlify.app", "pages.dev", "herokuapp.com",
        "ashbyhq.com", "greenhouse.io", "grnh.se", "lever.co", "workable.com", "smartrecruiters.com",
        "oraclecloud.com", "tally.so", "typeform.com",
    }
)


def normalize_domain(domain: str) -> str:
    """A bare lowercase host such as acme.com. Raises ValueError for anything else (scheme, path,
    port, query, user info, empty or malformed labels)."""
    value = domain.strip().lower().removeprefix("www.")
    if not _DOMAIN_RE.fullmatch(value):
        raise ValueError(f"Expected a bare domain like acme.com, got {domain!r}")
    return value


def is_platform_domain(domain: str) -> bool:
    """True if `domain` is a listed platform host or a subdomain of one. Raises ValueError like normalize_domain."""
    labels = normalize_domain(domain).split(".")
    return any(".".join(labels[i:]) in PLATFORM_DOMAINS for i in range(len(labels) - 1))


def host_matches(url: str | None, domain: str) -> bool:
    """True if the URL's host is `domain` or a subdomain of it. The URL is only parsed, never fetched."""
    if not isinstance(url, str) or not url:
        return False
    if "\\" in url:  # urlparse and browsers read "https://evil.com\@acme.com" as different hosts: never trust it
        return False
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().removesuffix(".").removeprefix("www.")  # "acme.com." is acme.com
    except ValueError:  # malformed URL such as an unclosed IPv6 bracket
        return False
    # Allowlist, not a blocklist of bad characters: only http(s) and a well-formed ASCII hostname can match.
    if parsed.scheme not in ("http", "https") or not _DOMAIN_RE.fullmatch(host):
        return False
    return host == domain or host.endswith("." + domain)


def url_domain(url: str | None) -> str | None:
    """The bare host of an http(s) URL (a job post's link), or None when it has no usable host. Only parsed."""
    if not isinstance(url, str) or not url or "\\" in url:
        return None
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().removesuffix(".")
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    try:
        return normalize_domain(host)
    except ValueError:
        return None


def build_result(company: str, domain: str | None, hits: list[dict], total_hits: int) -> ShowHNResult:
    """Domain matches first, then points (highest first), then newest story. Never silent about the cut."""
    wanted = normalize_domain(domain) if domain is not None else None
    candidates = [
        ShowHNCandidate(**hit, domain_match=bool(wanted) and host_matches(hit["url"], wanted))
        for hit in hits
    ]
    candidates.sort(key=lambda c: (not c.domain_match, -c.points, -c.story_id))
    kept = candidates[:MAX_CANDIDATES]
    platform = wanted is not None and is_platform_domain(wanted)
    return ShowHNResult(
        query=company,
        domain=wanted,
        candidates=kept,
        total_hits=total_hits,
        truncated=total_hits > len(kept),
        low_confidence=platform or not any(c.domain_match for c in kept),
        platform_domain=platform,
    )
