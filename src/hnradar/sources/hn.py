"""All HN network access (Algolia API) with a simple on-disk cache."""

import hashlib
import json
import os
import time
from pathlib import Path

import httpx

API = "https://hn.algolia.com/api/v1"
DEFAULT_CACHE_DIR = Path("data/cache")
SEARCH_TTL = 60 * 60
ITEM_TTL = 6 * 60 * 60
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
):
    cache_dir = cache_dir or DEFAULT_CACHE_DIR
    digest = hashlib.sha256(json.dumps([path, params], sort_keys=True).encode()).hexdigest()[:16]
    cache_file = cache_dir / f"{path.replace('/', '_')}_{digest}.json"

    if use_cache and cache_file.exists() and time.time() - cache_file.stat().st_mtime < ttl:
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass  # unreadable or truncated entry: treat as a miss and refetch

    response = httpx.get(f"{API}/{path}", params=params, timeout=30)
    response.raise_for_status()
    try:
        data = response.json()
    except ValueError as exc:
        raise HNError(f"Invalid JSON from {path}") from exc

    cache_dir.mkdir(parents=True, exist_ok=True)
    # Write then rename so an interrupted write can't leave a fresh, partial file.
    tmp_file = cache_file.with_suffix(".tmp")
    tmp_file.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp_file, cache_file)
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


def fetch_item(item_id: int, *, cache_dir: Path | None = None, use_cache: bool = True) -> dict:
    return _get_json(
        f"items/{item_id}", None, ttl=ITEM_TTL, cache_dir=cache_dir, use_cache=use_cache
    )


def top_level_comments(item: dict) -> list[dict]:
    """Direct replies to the story, skipping deleted/dead ones (no text)."""
    return [c for c in item.get("children", []) if c.get("text")]
