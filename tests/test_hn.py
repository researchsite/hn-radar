import json
import os

import httpx
import pytest

from hnradar.sources import hn

from conftest import SEARCH_URL, SHOW_HN_URL, STORY_ID, make_show_hit, show_hn_route


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


def test_search_show_hn_maps_hits_and_sends_the_expected_query(respx_mock, tmp_path):
    route = show_hn_route(
        respx_mock, [make_show_hit(7, "Show HN: Acme", points=5, comments=2, url="https://acme.com")], nb_hits=1
    )

    found = hn.search_show_hn("Acme", cache_dir=tmp_path)

    assert found == {
        "total_hits": 1,
        "hits": [{"story_id": 7, "title": "Show HN: Acme", "points": 5, "num_comments": 2, "url": "https://acme.com"}],
    }
    params = route.calls.last.request.url.params
    assert (params["tags"], params["query"], params["hitsPerPage"]) == ("show_hn", "Acme", str(hn.SHOW_HN_PAGE))


def test_search_show_hn_is_cached_per_query(respx_mock, tmp_path):
    route = show_hn_route(respx_mock, [make_show_hit(7, "Show HN: Acme")])

    hn.search_show_hn("Acme", cache_dir=tmp_path)
    hn.search_show_hn("Acme", cache_dir=tmp_path)
    assert route.call_count == 1
    hn.search_show_hn("Other", cache_dir=tmp_path)  # new params, new cache entry
    assert route.call_count == 2


def test_search_show_hn_zero_hits(respx_mock, tmp_path):
    show_hn_route(respx_mock, [])

    assert hn.search_show_hn("Nobody", cache_dir=tmp_path) == {"total_hits": 0, "hits": []}


def test_search_show_hn_missing_numbers_and_url_get_defaults(respx_mock, tmp_path):
    respx_mock.get(SHOW_HN_URL).mock(
        return_value=httpx.Response(200, json={"hits": [{"objectID": "9", "title": None}], "nbHits": 1})
    )

    found = hn.search_show_hn("Acme", cache_dir=tmp_path)

    assert found["hits"] == [{"story_id": 9, "title": "", "points": 0, "num_comments": 0, "url": None}]


@pytest.mark.parametrize("hit", [{"objectID": "abc", "title": "x"}, {"title": "no id"}, {"objectID": None}])
def test_search_show_hn_hit_without_numeric_id_fails_loudly(respx_mock, tmp_path, hit):
    respx_mock.get(SHOW_HN_URL).mock(return_value=httpx.Response(200, json={"hits": [hit], "nbHits": 1}))

    with pytest.raises(hn.HNError):
        hn.search_show_hn("Acme", cache_dir=tmp_path)

    assert not list(tmp_path.iterdir())  # a bad hit is never cached


def test_search_show_hn_recovers_after_a_bad_response(respx_mock, tmp_path):
    route = respx_mock.get(SHOW_HN_URL)
    route.mock(return_value=httpx.Response(200, json={"hits": [{"objectID": "abc"}], "nbHits": 1}))
    with pytest.raises(hn.HNError):
        hn.search_show_hn("Acme", cache_dir=tmp_path)

    route.mock(return_value=httpx.Response(200, json={"hits": [{"objectID": "5", "title": "Show HN: Acme"}], "nbHits": 1}))

    assert hn.search_show_hn("Acme", cache_dir=tmp_path)["hits"][0]["story_id"] == 5  # not served from a bad cache entry


@pytest.mark.parametrize("object_id", ["²", "٣", "１２", "1 2", "-5", ""])
def test_search_show_hn_non_ascii_digit_id_fails_loudly_and_is_not_cached(respx_mock, tmp_path, object_id):
    respx_mock.get(SHOW_HN_URL).mock(
        return_value=httpx.Response(200, json={"hits": [{"objectID": object_id, "title": "x"}], "nbHits": 1})
    )

    with pytest.raises(hn.HNError):  # isdigit() accepts these, int() does not
        hn.search_show_hn("Acme", cache_dir=tmp_path)

    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("stale", [{"hits": []}, {"hits": [{"objectID": "x"}], "nbHits": 1}, [], "oops"])
def test_search_show_hn_malformed_cache_entry_is_a_miss_not_a_crash(respx_mock, tmp_path, stale):
    route = show_hn_route(respx_mock, [make_show_hit(7, "Show HN: Acme")])
    hn.search_show_hn("Acme", cache_dir=tmp_path)
    next(tmp_path.glob("*.json")).write_text(json.dumps(stale), encoding="utf-8")

    found = hn.search_show_hn("Acme", cache_dir=tmp_path)

    assert found["hits"][0]["story_id"] == 7
    assert route.call_count == 2  # refetched, and the entry is good again


@pytest.mark.parametrize("nb_hits", [None, "many", -1, True, 1.5])
def test_search_show_hn_missing_or_invalid_nbhits_fails_loudly(respx_mock, tmp_path, nb_hits):
    body = {"hits": [{"objectID": "5", "title": "x"}]}
    if nb_hits is not None:
        body["nbHits"] = nb_hits
    respx_mock.get(SHOW_HN_URL).mock(return_value=httpx.Response(200, json=body))

    with pytest.raises(hn.HNError):
        hn.search_show_hn("Acme", cache_dir=tmp_path)

    assert not list(tmp_path.iterdir())


def test_search_show_hn_total_hits_is_the_full_count_not_the_page(respx_mock, tmp_path):
    show_hn_route(respx_mock, [make_show_hit(1, "Show HN: x"), make_show_hit(2, "Show HN: y")], nb_hits=813)

    found = hn.search_show_hn("Supabase", cache_dir=tmp_path)

    assert (found["total_hits"], len(found["hits"])) == (813, 2)


def test_search_show_hn_http_error_and_bad_json(respx_mock, tmp_path):
    respx_mock.get(SHOW_HN_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        hn.search_show_hn("Acme", cache_dir=tmp_path)

    respx_mock.get(SHOW_HN_URL).mock(return_value=httpx.Response(200, content=b"<html>oops"))
    with pytest.raises(hn.HNError):
        hn.search_show_hn("Acme", cache_dir=tmp_path, use_cache=False)
    assert not list(tmp_path.iterdir())


def test_two_writers_on_one_cache_key_both_succeed(respx_mock, tmp_path, monkeypatch):
    show_hn_route(respx_mock, [make_show_hit(7, "Show HN: Acme")])
    real_replace = os.replace
    nested = []

    def replace(src, dst):
        if not nested:
            nested.append(True)  # a second writer finishes the same key while the first is mid-write
            hn.search_show_hn("Acme", cache_dir=tmp_path, use_cache=False)
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", replace)

    found = hn.search_show_hn("Acme", cache_dir=tmp_path, use_cache=False)

    assert found["hits"][0]["story_id"] == 7
    assert not list(tmp_path.glob("*.tmp"))
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_failed_cache_write_leaves_no_temp_file(respx_mock, tmp_path, monkeypatch):
    show_hn_route(respx_mock, [make_show_hit(7, "Show HN: Acme")])

    def broken_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", broken_replace)

    with pytest.raises(OSError):
        hn.search_show_hn("Acme", cache_dir=tmp_path)

    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("status", [403, 404, 429, 500, 503])
def test_search_show_hn_upstream_errors_raise_and_are_not_cached(respx_mock, tmp_path, status):
    respx_mock.get(SHOW_HN_URL).mock(return_value=httpx.Response(status))

    with pytest.raises(httpx.HTTPStatusError):
        hn.search_show_hn("Acme", cache_dir=tmp_path)

    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "body",
    [
        {"hits": None, "nbHits": 0},
        {"nbHits": 3},
        {"hits": "none", "nbHits": 1},
        {"hits": [], "nbHits": "many"},
        [],
        "oops",
        None,
    ],
)
def test_search_show_hn_wrong_shape_raises_hn_error_and_is_not_cached(respx_mock, tmp_path, body):
    respx_mock.get(SHOW_HN_URL).mock(return_value=httpx.Response(200, json=body))

    with pytest.raises(hn.HNError):
        hn.search_show_hn("Acme", cache_dir=tmp_path)

    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "hit",
    [
        {"objectID": "5", "url": ["https://acme.com"]},
        {"objectID": "5", "url": 5},
        {"objectID": "5", "title": {"a": 1}},
        {"objectID": "5", "points": "many"},
        {"objectID": "5", "num_comments": True},
        {"objectID": "5", "points": 1.5},
        {"objectID": "5", "points": -3},
        {"objectID": "5", "num_comments": -1},
    ],
)
def test_search_show_hn_hit_field_of_wrong_type_fails_loudly(respx_mock, tmp_path, hit):
    respx_mock.get(SHOW_HN_URL).mock(return_value=httpx.Response(200, json={"hits": [hit], "nbHits": 1}))

    with pytest.raises(hn.HNError):
        hn.search_show_hn("Acme", cache_dir=tmp_path)

    assert not list(tmp_path.iterdir())


def test_search_show_hn_nbhits_smaller_than_the_page_fails_loudly(respx_mock, tmp_path):
    respx_mock.get(SHOW_HN_URL).mock(
        return_value=httpx.Response(200, json={"hits": [{"objectID": "5", "title": "x"}], "nbHits": 0})
    )

    with pytest.raises(hn.HNError):
        hn.search_show_hn("Acme", cache_dir=tmp_path)

    assert not list(tmp_path.iterdir())


def test_search_show_hn_hit_that_is_not_an_object_fails_loudly(respx_mock, tmp_path):
    respx_mock.get(SHOW_HN_URL).mock(return_value=httpx.Response(200, json={"hits": ["oops"], "nbHits": 1}))

    with pytest.raises(hn.HNError):
        hn.search_show_hn("Acme", cache_dir=tmp_path)
