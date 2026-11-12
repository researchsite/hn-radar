import httpx
import pytest

from hnradar.fingerprint import build_profile
from hnradar.sources import github

from conftest import REPOS_URL, make_repo


def test_fetch_repos_returns_repos(mock_github, tmp_path):
    assert [r["name"] for r in github.fetch_repos("octo", cache_dir=tmp_path)] == ["big", "fork", "small"]


def test_fetch_repos_unknown_user(respx_mock, tmp_path):
    respx_mock.get(REPOS_URL).mock(return_value=httpx.Response(404, json={"message": "Not Found"}))
    with pytest.raises(github.UserNotFoundError, match="octo"):
        github.fetch_repos("octo", cache_dir=tmp_path)


def test_fetch_repos_rate_limited(respx_mock, tmp_path):
    respx_mock.get(REPOS_URL).mock(
        return_value=httpx.Response(403, headers={"x-ratelimit-remaining": "0"}, json={})
    )
    with pytest.raises(github.GitHubError, match="GITHUB_TOKEN"):
        github.fetch_repos("octo", cache_dir=tmp_path)


@pytest.mark.parametrize("name", ["", "../etc", "a/b", "-lead", "trail-", "a--b", "x" * 40, "sp ace"])
def test_invalid_username_never_hits_network(respx_mock, tmp_path, name):
    with pytest.raises(github.GitHubError, match="Invalid"):
        github.fetch_repos(name, cache_dir=tmp_path)
    assert not respx_mock.calls


def test_fetch_repos_paginates(respx_mock, tmp_path):
    full_page = [make_repo(f"r{i}") for i in range(github.PER_PAGE)]
    route = respx_mock.get(REPOS_URL).mock(
        side_effect=[httpx.Response(200, json=full_page), httpx.Response(200, json=[make_repo("last")])]
    )
    repos = github.fetch_repos("octo", cache_dir=tmp_path)
    assert len(repos) == github.PER_PAGE + 1
    assert route.call_count == 2


def test_responses_are_cached_on_disk(mock_github, tmp_path):
    for _ in range(2):
        github.fetch_repos("octo", cache_dir=tmp_path)
    assert mock_github.call_count == 1


def test_token_from_env_is_sent_but_not_required(mock_github, tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    github.fetch_repos("octo", cache_dir=tmp_path / "a")
    assert "authorization" not in mock_github.calls.last.request.headers

    monkeypatch.setenv("GITHUB_TOKEN", "t0ken")
    github.fetch_repos("octo", cache_dir=tmp_path / "b")
    assert mock_github.calls.last.request.headers["authorization"] == "Bearer t0ken"


def test_languages_weighted_by_size_and_forks_excluded():
    repos = [
        make_repo("a", language="Python", size=100),
        make_repo("b", language="Python", size=50),
        make_repo("c", language="Go", size=120),
        make_repo("d", language="Rust", size=9999, fork=True),  # fork: must not count
        make_repo("e", language=None, size=500),  # no language: ignored
    ]
    profile = build_profile("octo", repos)
    assert [(l.name, l.size_kb) for l in profile.languages] == [("Python", 150), ("Go", 120)]


def test_only_top_five_languages():
    repos = [make_repo(f"r{i}", language=f"L{i}", size=100 - i) for i in range(7)]
    assert [l.name for l in build_profile("octo", repos).languages] == ["L0", "L1", "L2", "L3", "L4"]


def test_notable_repos_by_stars_and_topics_by_frequency():
    repos = [
        make_repo("low", stars=1, topics=["cli", "python"]),
        make_repo("high", stars=50, description="the good one", topics=["python"]),
        make_repo("forked", stars=999, fork=True, topics=["ignored"]),
    ]
    profile = build_profile("octo", repos)
    assert [(r.name, r.stars) for r in profile.notable_repos] == [("high", 50), ("low", 1)]
    assert profile.notable_repos[0].description == "the good one"
    assert profile.topics == ["python", "cli"]


def test_zero_repos_gives_empty_profile():
    profile = build_profile("octo", [])
    assert profile.model_dump() == {"username": "octo", "languages": [], "topics": [], "notable_repos": []}


def test_more_repos_than_the_page_cap_fails_instead_of_truncating(respx_mock, tmp_path, monkeypatch):
    monkeypatch.setattr(github, "MAX_PAGES", 2)
    full_page = [make_repo(f"r{i}") for i in range(github.PER_PAGE)]
    route = respx_mock.get(REPOS_URL).mock(return_value=httpx.Response(200, json=full_page))
    with pytest.raises(github.GitHubError, match="partial profile"):
        github.fetch_repos("octo", cache_dir=tmp_path)
    assert route.call_count == 2


def test_exactly_a_full_last_page_keeps_paginating(respx_mock, tmp_path):
    full_page = [make_repo(f"r{i}") for i in range(github.PER_PAGE)]
    route = respx_mock.get(REPOS_URL).mock(
        side_effect=[httpx.Response(200, json=full_page)] * 5 + [httpx.Response(200, json=[make_repo("x")])]
    )
    assert len(github.fetch_repos("octo", cache_dir=tmp_path)) == 5 * github.PER_PAGE + 1
    assert route.call_count == 6


def test_cache_write_leaves_no_temp_files(mock_github, tmp_path):
    github.fetch_repos("octo", cache_dir=tmp_path)
    assert not list(tmp_path.glob("*.tmp"))
    assert len(list(tmp_path.glob("github_*.json"))) == 1


def test_failed_cache_write_cleans_up_its_temp_file(mock_github, tmp_path, monkeypatch):
    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(github.os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        github.fetch_repos("octo", cache_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []
