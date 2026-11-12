import httpx
import pytest

from hnradar.sources import hn

from conftest import SEARCH_URL, STORY_ID


def test_find_latest_skips_other_threads(mock_hn, tmp_path):
    story = hn.find_latest_hiring_story(cache_dir=tmp_path)
    assert story == {"id": STORY_ID, "title": "Ask HN: Who is hiring? (November 2026)"}


def test_no_hiring_story_raises(respx_mock, tmp_path):
    respx_mock.get(SEARCH_URL).mock(return_value=httpx.Response(200, json={"hits": []}))
    with pytest.raises(hn.HNError):
        hn.find_latest_hiring_story(cache_dir=tmp_path)


def test_top_level_comments_skip_deleted_and_replies(mock_hn, tmp_path):
    comments = hn.top_level_comments(hn.fetch_item(STORY_ID, cache_dir=tmp_path))
    assert [c["id"] for c in comments] == [101, 102, 104]


def test_responses_are_cached_on_disk(mock_hn, tmp_path):
    search, item = mock_hn
    for _ in range(2):
        hn.find_latest_hiring_story(cache_dir=tmp_path)
        hn.fetch_item(STORY_ID, cache_dir=tmp_path)
    assert search.call_count == 1
    assert item.call_count == 1
    assert len(list(tmp_path.glob("*.json"))) == 2


def test_use_cache_false_refetches(mock_hn, tmp_path):
    search, _ = mock_hn
    hn.find_latest_hiring_story(cache_dir=tmp_path)
    hn.find_latest_hiring_story(cache_dir=tmp_path, use_cache=False)
    assert search.call_count == 2


def test_corrupt_cache_entry_is_refetched(mock_hn, tmp_path):
    search, _ = mock_hn
    hn.find_latest_hiring_story(cache_dir=tmp_path)
    next(tmp_path.glob("*.json")).write_text('{"hits": [', encoding="utf-8")  # truncated write

    story = hn.find_latest_hiring_story(cache_dir=tmp_path)

    assert story["id"] == STORY_ID
    assert search.call_count == 2
    assert not list(tmp_path.glob("*.tmp"))


def test_invalid_json_response_raises_hn_error(respx_mock, tmp_path):
    respx_mock.get(SEARCH_URL).mock(return_value=httpx.Response(200, content=b"<html>oops"))
    with pytest.raises(hn.HNError):
        hn.find_latest_hiring_story(cache_dir=tmp_path)
    assert not list(tmp_path.iterdir())


def test_http_error_propagates(respx_mock, tmp_path):
    respx_mock.get(SEARCH_URL).mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        hn.find_latest_hiring_story(cache_dir=tmp_path)
