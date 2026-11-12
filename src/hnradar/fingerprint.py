"""Turn a user's public GitHub repos into a Profile (pure functions, no network)."""

from collections import Counter

from hnradar.models import Language, Profile, Repo

TOP_LANGUAGES = 5
TOP_TOPICS = 10
TOP_REPOS = 5


def build_profile(username: str, repos: list[dict]) -> Profile:
    """Forks are excluded everywhere: they describe someone else's work."""
    own = [r for r in repos if not r.get("fork")]

    sizes: Counter[str] = Counter()
    for r in own:
        if r.get("language"):
            sizes[r["language"]] += r.get("size") or 0
    languages = sorted(sizes.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_LANGUAGES]

    topic_counts = Counter(t for r in own for t in r.get("topics") or [])
    topics = [t for t, _ in sorted(topic_counts.items(), key=lambda kv: (-kv[1], kv[0]))][:TOP_TOPICS]

    by_stars = sorted(own, key=lambda r: (-(r.get("stargazers_count") or 0), r.get("name", "")))
    notable = [
        Repo(
            name=r["name"],
            description=r.get("description"),
            stars=r.get("stargazers_count") or 0,
            language=r.get("language"),
        )
        for r in by_stars[:TOP_REPOS]
    ]

    return Profile(
        username=username,
        languages=[Language(name=n, size_kb=s) for n, s in languages],
        topics=topics,
        notable_repos=notable,
    )
