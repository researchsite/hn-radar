"""Rank Show HN search hits for a company (pure functions, no network)."""

import re
from urllib.parse import urlparse

from hnradar.models import ShowHNCandidate, ShowHNResult

MAX_CANDIDATES = 5
_LABEL = r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
_DOMAIN_RE = re.compile(rf"{_LABEL}(\.{_LABEL})+")  # at least two labels, ASCII (punycode for IDNs)


def normalize_domain(domain: str) -> str:
    """A bare lowercase host such as acme.com. Raises ValueError for anything else (scheme, path,
    port, query, user info, empty or malformed labels)."""
    value = domain.strip().lower().removeprefix("www.")
    if not _DOMAIN_RE.fullmatch(value):
        raise ValueError(f"Expected a bare domain like acme.com, got {domain!r}")
    return value


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


def build_result(company: str, domain: str | None, hits: list[dict], total_hits: int) -> ShowHNResult:
    """Domain matches first, then points (highest first), then newest story. Never silent about the cut."""
    wanted = normalize_domain(domain) if domain is not None else None
    candidates = [
        ShowHNCandidate(**hit, domain_match=bool(wanted) and host_matches(hit["url"], wanted))
        for hit in hits
    ]
    candidates.sort(key=lambda c: (not c.domain_match, -c.points, -c.story_id))
    kept = candidates[:MAX_CANDIDATES]
    return ShowHNResult(
        query=company,
        domain=wanted,
        candidates=kept,
        total_hits=total_hits,
        truncated=total_hits > len(kept),
        low_confidence=not any(c.domain_match for c in kept),
    )
