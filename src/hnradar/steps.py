"""Steps shared by `draft` and `radar`, so neither copies the other's logic (issue 15, rule: one source of truth)."""

from dataclasses import dataclass
from pathlib import Path

from hnradar.fingerprint import build_profile
from hnradar.models import Match, Profile, ShowHNCandidate, ShowHNResult
from hnradar.pulse.cluster import cluster, select_comments
from hnradar.pulse.schema import PulseResult
from hnradar.showhn import build_result, is_platform_domain, url_domain
from hnradar.sources import github, hn


def save_profile(username: str, out_dir: Path, fetch_repos=github.fetch_repos) -> tuple[Profile, Path, int]:
    """Fetch the user's public repos, build the profile and save it as <out_dir>/<username>.json.
    Returns (profile, path, number of public repos). Shared by `fingerprint` and `radar`."""
    repos = fetch_repos(username)
    profile = build_profile(username, repos)
    out = out_dir / f"{username}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(profile.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return profile, out, len(repos)


@dataclass
class StoryLookup:
    domain: str | None
    story: ShowHNCandidate | None  # the first story that links to the domain, or None
    skip: str | None  # why there is no story, in words for the person (None when there is one)
    result: ShowHNResult | None = None


def repo_for_match(profile: Profile, match: Match):
    """The profile repo that the match reasons name, or None."""
    return next((r for r in profile.notable_repos if any(x.kind == "repo" and x.evidence == r.name for x in match.reasons)), None)


def story_from_result(domain: str, result: ShowHNResult) -> StoryLookup:
    """The first story of a Show HN result that links to `domain`, or why there is none. Pure: shared by `find_story`
    (live search) and the site's offline builder (saved results), so both pick the same story."""
    story = next((c for c in result.candidates if c.domain_match), None)
    if result.low_confidence or story is None:
        return StoryLookup(domain, None, f"no Show HN links to {domain}", result)
    return StoryLookup(domain, story, None, result)


def find_story(company: str, job_url: str | None, search=hn.search_show_hn) -> StoryLookup:
    """The Show HN story that links to the job link's own domain. A platform domain is skipped before any request."""
    domain = url_domain(job_url)
    if domain is None:
        return StoryLookup(None, None, "no domain in the job link")
    if is_platform_domain(domain):
        return StoryLookup(domain, None, f"platform domain ({domain}); a story that links to it may belong to someone else")
    found = search(company, domain=domain)
    return story_from_result(domain, build_result(company, domain, found["hits"], found["total_hits"]))


def story_children(story_id: int, fetch=hn.fetch_item) -> list[dict] | None:
    """The top-level comments of a Show HN story, or None when the id is not a story."""
    item = fetch(story_id)
    if not isinstance(item, dict) or item.get("type") != "story" or not isinstance(item.get("children", []), list):
        return None
    return item.get("children") or []


def saved_pulse(pulse_dir: Path, story_id: int) -> PulseResult | None:
    path = pulse_dir / f"{story_id}.json"
    if not path.exists():
        return None
    try:
        return PulseResult.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:  # a saved pulse that cannot be read is not silently re-made
        raise ValueError(f"could not read the saved pulse {path}: {type(exc).__name__}") from exc


def get_pulse(story_id: int, children: list[dict], pulse_dir: Path, client) -> PulseResult | None:
    """The saved pulse of the story, or a fresh one from the model. None = too few comments (not enough signal)."""
    result = saved_pulse(pulse_dir, story_id)
    if result is not None and result.story_id != story_id:  # a pulse copied or renamed from another story
        raise ValueError(f"the saved pulse {pulse_dir / f'{story_id}.json'} is for story {result.story_id}, not {story_id}")
    if result is None:
        result = cluster(story_id, "", [c for c in children if not isinstance(c, dict) or c.get("text")], client)
    return result


def bug_from_pulse(result: PulseResult, children: list[dict]) -> tuple[str, list[dict]] | None:
    """The first pulse bug whose cited comments are all in the thread, as (summary, [{"id", "text"}])."""
    by_id = {c["id"]: c for c in select_comments(children)[0]}
    for item in (i for i in result.items if i.kind == "bug"):
        if all(cid in by_id for cid in item.comment_ids):
            return item.summary, [by_id[cid] for cid in item.comment_ids]
    return None


def cited_bug(story_id: int, children: list[dict], pulse_dir: Path, client) -> tuple[str, list[dict]] | None:
    result = get_pulse(story_id, children, pulse_dir, client)
    return None if result is None else bug_from_pulse(result, children)
