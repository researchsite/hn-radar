import httpx
import pytest

SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"
STORY_ID = 99
ITEM_URL = f"https://hn.algolia.com/api/v1/items/{STORY_ID}"

SEARCH_RESPONSE = {
    "hits": [
        {"objectID": "100", "title": "Ask HN: Who wants to be hired? (November 2026)", "created_at_i": 1793000000},
        {"objectID": "99", "title": "Ask HN: Who is hiring? (November 2026)", "created_at_i": 1792900000},
        {"objectID": "50", "title": "Ask HN: Who is hiring? (October 2026)", "created_at_i": 1790000000},
    ]
}

PIPE_COMMENT = (
    "Acme | Senior Backend Engineer | Berlin, Germany | REMOTE | Python, Postgres"
    "<p>We build widgets. Apply at "
    '<a href="https:&#x2F;&#x2F;acme.example&#x2F;jobs" rel="nofollow">https:&#x2F;&#x2F;acme.example&#x2F;jobs</a>'
)
FREEFORM_COMMENT = (
    "We&#x27;re Initech, a small team building TPS report software. "
    "Looking for engineers in Austin, TX or remote across the US.<p>Email jobs@initech.example"
)
ONSITE_COMMENT = "Globex | Data Analyst | New York, NY | ONSITE only<p>SQL and dashboards."

ITEM_RESPONSE = {
    "id": STORY_ID,
    "type": "story",
    "title": "Ask HN: Who is hiring? (November 2026)",
    "children": [
        {"id": 101, "type": "comment", "text": PIPE_COMMENT,
         "children": [{"id": 201, "type": "comment", "text": "Open to Canada?", "children": []}]},
        {"id": 102, "type": "comment", "text": FREEFORM_COMMENT, "children": []},
        {"id": 103, "type": "comment", "text": None, "children": []},
        {"id": 104, "type": "comment", "text": ONSITE_COMMENT, "children": []},
    ],
}


@pytest.fixture
def mock_hn(respx_mock):
    search = respx_mock.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=SEARCH_RESPONSE))
    item = respx_mock.get(ITEM_URL).mock(return_value=httpx.Response(200, json=ITEM_RESPONSE))
    return search, item


REPOS_URL = "https://api.github.com/users/octo/repos"


def make_repo(name, *, language="Python", size=10, stars=0, fork=False, topics=(), description=None):
    return {
        "name": name,
        "description": description,
        "language": language,
        "size": size,
        "stargazers_count": stars,
        "fork": fork,
        "topics": list(topics),
    }


@pytest.fixture
def mock_github(respx_mock):
    repos = [
        make_repo("big", language="Python", size=300, stars=5, topics=["cli"]),
        make_repo("fork", language="Rust", size=5000, stars=900, fork=True),
        make_repo("small", language="Go", size=20),
    ]
    return respx_mock.get(REPOS_URL).mock(return_value=httpx.Response(200, json=repos))
