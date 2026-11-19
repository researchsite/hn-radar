"""All HN network access (Algolia API) with a simple on-disk cache."""

import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path

import httpx

API = "https://hn.algolia.com/api/v1"
DEFAULT_CACHE_DIR = Path("data/cache")
SEARCH_TTL = 60 * 60
ITEM_TTL = 6 * 60 * 60
SHOW_HN_TTL = 60 * 60
SHOW_HN_PAGE = 50  # hits requested per search; the caller reports when total_hits is larger
TITLE_PREFIX = "ask hn: who is hiring"


class HNError(RuntimeError):
    pass


def _get_json(
    path: str,
    params: dict[str, str] | None,
    *,
    ttl: int,
    cache_dir: Path | None,
    use_cache: bool,
    validate=None,
):
    cache_dir = cache_dir or DEFAULT_CACHE_DIR
    digest = hashlib.sha256(json.dumps([path, params], sort_keys=True).encode()).hexdigest()[:16]
    cache_file = cache_dir / f"{path.replace('/', '_')}_{digest}.json"

    if use_cache and cache_file.exists() and time.time() - cache_file.stat().st_mtime < ttl:
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            if validate:
                validate(cached)  # an entry of the wrong shape (old or hand-edited) is a miss, not a crash
            return cached
        except (OSError, ValueError, HNError):
            pass  # unreadable, truncated or malformed entry: treat as a miss and refetch

    response = httpx.get(f"{API}/{path}", params=params, timeout=30)
    response.raise_for_status()
    try:
        data = response.json()
    except ValueError as exc:
        raise HNError(f"Invalid JSON from {path}") from exc
    if validate:
        validate(data)  # before the cache write: a response of the wrong shape is never cached

    cache_dir.mkdir(parents=True, exist_ok=True)
    # Write to a unique temp file, then rename, so an interrupted or concurrent write can't leave
    # a fresh, partial cache file or clobber another writer's temp file.
    fd, tmp_name = tempfile.mkstemp(dir=cache_dir, prefix=cache_file.stem, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            json.dump(data, tmp_file)
        os.replace(tmp_name, cache_file)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return data


def find_latest_hiring_story(*, cache_dir: Path | None = None, use_cache: bool = True) -> dict:
    """Return {"id": int, "title": str} of the newest "Ask HN: Who is hiring?" story."""
    data = _get_json(
        "search_by_date",
        {"tags": "story,author_whoishiring", "query": "who is hiring"},
        ttl=SEARCH_TTL,
        cache_dir=cache_dir,
        use_cache=use_cache,
    )
    # The whoishiring account also posts "Who wants to be hired?" and "Freelancer?" threads.
    hits = [h for h in data.get("hits", []) if (h.get("title") or "").lower().startswith(TITLE_PREFIX)]
    if not hits:
        raise HNError("No 'Ask HN: Who is hiring?' story found")
    latest = max(hits, key=lambda h: h.get("created_at_i", 0))
    return {"id": int(latest["objectID"]), "title": latest["title"]}


def _check_search_shape(data) -> None:
    if not isinstance(data, dict) or not isinstance(data.get("hits"), list):
        raise HNError("Unexpected search response: no 'hits' list")
    # nbHits is required: without it a cut result could not be told apart from a complete one (rule 6).
    nb_hits = data.get("nbHits")
    if isinstance(nb_hits, bool) or not isinstance(nb_hits, int) or nb_hits < 0:
        raise HNError("Unexpected search response: 'nbHits' is missing or not a count")
    if nb_hits < len(data["hits"]):
        raise HNError("Unexpected search response: 'nbHits' is smaller than the hits returned")
    for hit in data["hits"]:  # checked here, before the cache write, so a bad hit is never cached
        if not isinstance(hit, dict) or not re.fullmatch(r"[0-9]+", str(hit.get("objectID", ""))):  # not isdigit(): "²" passes it, int() rejects it
            raise HNError(f"Unexpected Show HN hit without a numeric id: {hit!r}")
        for key in ("title", "url"):
            if not isinstance(hit.get(key), (str, type(None))):
                raise HNError(f"Unexpected Show HN hit: {key!r} is not text: {hit!r}")
        for key in ("points", "num_comments"):
            value = hit.get(key)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise HNError(f"Unexpected Show HN hit: {key!r} is not a count: {hit!r}")


def _show_hn_search(query: str, *, url_only: bool, cache_dir: Path | None, use_cache: bool) -> dict:
    params = {"tags": "show_hn", "query": query, "hitsPerPage": str(SHOW_HN_PAGE)}
    if url_only:
        params["restrictSearchableAttributes"] = "url"  # undocumented for HN: it only widens the candidate pool
    data = _get_json(
        "search",
        params,
        ttl=SHOW_HN_TTL,
        cache_dir=cache_dir,
        use_cache=use_cache,
        validate=_check_search_shape,
    )
    hits = []
    for h in data["hits"]:
        hits.append(
            {
                "story_id": int(h["objectID"]),
                "title": h.get("title") or "",
                "points": h.get("points") or 0,
                "num_comments": h.get("num_comments") or 0,
                "url": h.get("url"),
            }
        )
    return {"total_hits": data["nbHits"], "hits": hits}


def search_show_hn(
    company: str, *, domain: str | None = None, cache_dir: Path | None = None, use_cache: bool = True
) -> dict:
    """Show HN stories matching `company`, in Algolia relevance order.

    With `domain`, a second search for stories whose URL contains it is merged in (de-duplicated by
    story id, name-search copy first). A failure of either request raises; there is no partial result.

    Returns {"total_hits": int, "hits": [{"story_id", "title", "points", "num_comments", "url"}]},
    with at most SHOW_HN_PAGE hits per request. total_hits lets callers tell when the list is only
    part of the matches: with a domain it is exact when both responses held all their matches,
    otherwise a lower bound (the larger Algolia count), because the two result sets can overlap.
    """
    if domain is not None and not domain.strip():
        raise ValueError("domain must not be empty")  # checked before any request

    found = _show_hn_search(company, url_only=False, cache_dir=cache_dir, use_cache=use_cache)
    if domain is None:
        return found

    by_url = _show_hn_search(domain.strip(), url_only=True, cache_dir=cache_dir, use_cache=use_cache)
    hits = list(found["hits"])
    seen = {h["story_id"] for h in hits}
    for hit in by_url["hits"]:
        if hit["story_id"] not in seen:
            seen.add(hit["story_id"])
            hits.append(hit)
    total_hits = max(found["total_hits"], by_url["total_hits"], len(hits))
    return {"total_hits": total_hits, "hits": hits}


def fetch_item(item_id: int, *, cache_dir: Path | None = None, use_cache: bool = True) -> dict:
    return _get_json(
        f"items/{item_id}", None, ttl=ITEM_TTL, cache_dir=cache_dir, use_cache=use_cache
    )


def top_level_comments(item: dict) -> list[dict]:
    """Direct replies to the story, skipping deleted/dead ones (no text)."""
    return [c for c in item.get("children", []) if c.get("text")]
