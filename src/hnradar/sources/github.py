"""All GitHub network access (public REST API) with a simple on-disk cache."""

import hashlib
import json
import os
import re
import time
from pathlib import Path

import httpx

API = "https://api.github.com"
DEFAULT_CACHE_DIR = Path("data/cache")
REPOS_TTL = 60 * 60
PER_PAGE = 100
MAX_PAGES = 5
# GitHub usernames: 1-39 chars, alphanumeric or single hyphens, no leading/trailing hyphen.
USERNAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")


class GitHubError(RuntimeError):
    pass


class UserNotFoundError(GitHubError):
    pass


def _headers() -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _get_json(
    path: str,
    params: dict[str, str | int],
    *,
    ttl: int,
    cache_dir: Path | None,
    use_cache: bool,
):
    cache_dir = cache_dir or DEFAULT_CACHE_DIR
    digest = hashlib.sha256(json.dumps([path, params], sort_keys=True).encode()).hexdigest()[:16]
    cache_file = cache_dir / f"github_{path.replace('/', '_')}_{digest}.json"

    if use_cache and cache_file.exists() and time.time() - cache_file.stat().st_mtime < ttl:
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass  # unreadable or truncated entry: treat as a miss and refetch

    response = httpx.get(f"{API}/{path}", params=params, headers=_headers(), timeout=30)
    if response.status_code == 404:
        raise UserNotFoundError(f"Not found: {path}")
    if response.status_code in (403, 429) and response.headers.get("x-ratelimit-remaining") == "0":
        raise GitHubError("GitHub rate limit reached. Set GITHUB_TOKEN to raise the limit.")
    response.raise_for_status()
    try:
        data = response.json()
    except ValueError as exc:
        raise GitHubError(f"Invalid JSON from {path}") from exc

    cache_dir.mkdir(parents=True, exist_ok=True)
    # Write then rename so an interrupted write can't leave a fresh, partial file.
    tmp_file = cache_file.with_suffix(".tmp")
    tmp_file.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp_file, cache_file)
    return data


def fetch_repos(username: str, *, cache_dir: Path | None = None, use_cache: bool = True) -> list[dict]:
    """Public repos owned by `username` (forks included; callers filter), newest push first."""
    if not USERNAME_RE.fullmatch(username):
        raise GitHubError(f"Invalid GitHub username: {username!r}")
    repos: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        try:
            batch = _get_json(
                f"users/{username}/repos",
                {"type": "owner", "sort": "pushed", "per_page": PER_PAGE, "page": page},
                ttl=REPOS_TTL,
                cache_dir=cache_dir,
                use_cache=use_cache,
            )
        except UserNotFoundError as exc:
            raise UserNotFoundError(f"GitHub user not found: {username}") from exc
        repos.extend(batch)
        if len(batch) < PER_PAGE:
            break
    return repos
