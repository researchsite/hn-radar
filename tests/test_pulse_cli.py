import json
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

import hnradar.cli as cli
from hnradar.cli import app
from hnradar.pulse.cluster import MAX_COMMENTS

runner = CliRunner()
SHOW_ID = 4242
SHOW_URL = f"https://hn.algolia.com/api/v1/items/{SHOW_ID}"
CHAT_URL = "http://localhost:11434/api/chat"


class FakeClient:
    name = "fake:1b"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = 0

    def complete(self, system, user, schema):
        self.calls += 1
        return self.replies.pop(0)


def show_story(n, *, type="story", title="Show HN: Acme"):
    children = [{"id": 1000 + i, "type": "comment", "text": f"Comment {i} about it", "children": []} for i in range(n)]
    return {"id": SHOW_ID, "type": type, "title": title, "children": children}


def reply(summary="It crashes on start", ids=(1000,), kind="bug"):
    return json.dumps({"items": [{"kind": kind, "summary": summary, "comment_ids": list(ids)}]})


@pytest.fixture
def story(respx_mock, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def serve(item, status=200):
        respx_mock.get(SHOW_URL).mock(return_value=httpx.Response(status, json=item))

    return serve


def use_client(monkeypatch, client):
    monkeypatch.setattr(cli, "_model_client", lambda model: client)
    return client


def test_pulse_writes_the_file_and_prints_cited_items(story, monkeypatch, tmp_path):
    story(show_story(5))
    use_client(monkeypatch, FakeClient(reply(ids=[1000, 1001])))

    result = runner.invoke(app, ["pulse", str(SHOW_ID)])

    assert result.exit_code == 0, result.output
    assert "bug | It crashes on start | comments 1000, 1001" in result.output
    data = json.loads((tmp_path / "data" / "pulse" / f"{SHOW_ID}.json").read_text(encoding="utf-8"))
    assert data["story_id"] == SHOW_ID and data["model"] == "fake:1b"
    assert data["items"][0]["comment_ids"] == [1000, 1001]
    assert (data["comments_used"], data["comments_total"]) == (5, 5)
    assert "Used" not in result.output  # nothing was cut


def test_pulse_out_writes_to_the_given_path(story, monkeypatch, tmp_path):
    story(show_story(3))
    use_client(monkeypatch, FakeClient(reply()))
    target = tmp_path / "x" / "p.json"

    result = runner.invoke(app, ["pulse", str(SHOW_ID), "--out", str(target)])

    assert result.exit_code == 0, result.output
    assert target.exists() and not (tmp_path / "data" / "pulse").exists()


def test_pulse_passes_the_model_option(story, monkeypatch):
    story(show_story(3))
    seen = []
    client = FakeClient(reply())
    monkeypatch.setattr(cli, "_model_client", lambda model: seen.append(model) or client)

    runner.invoke(app, ["pulse", str(SHOW_ID), "--model", "qwen2.5:14b"])
    runner.invoke(app, ["pulse", str(SHOW_ID)])

    assert seen == ["qwen2.5:14b", "qwen2.5:7b"]


def test_not_enough_signal_writes_nothing_and_exits_0(story, monkeypatch, tmp_path):
    story(show_story(2))
    client = use_client(monkeypatch, FakeClient())

    result = runner.invoke(app, ["pulse", str(SHOW_ID)])

    assert result.exit_code == 0
    assert "Not enough signal" in result.output
    assert client.calls == 0
    assert not (tmp_path / "data" / "pulse").exists()


def test_over_the_cap_says_how_many_were_used(story, monkeypatch):
    story(show_story(MAX_COMMENTS + 1))
    use_client(monkeypatch, FakeClient(reply()))

    result = runner.invoke(app, ["pulse", str(SHOW_ID)])

    assert f"Used {MAX_COMMENTS} of {MAX_COMMENTS + 1} comments" in result.output


def test_exactly_the_cap_prints_no_cut_note(story, monkeypatch):
    story(show_story(MAX_COMMENTS))
    use_client(monkeypatch, FakeClient(reply()))
    assert "Used" not in runner.invoke(app, ["pulse", str(SHOW_ID)]).output


def test_unknown_comment_id_is_an_error_and_writes_nothing(story, monkeypatch, tmp_path):
    story(show_story(4))
    use_client(monkeypatch, FakeClient(reply(ids=[9]), reply(ids=[9])))

    result = runner.invoke(app, ["pulse", str(SHOW_ID)])

    assert result.exit_code == 1
    assert "Error:" in result.stderr and "9" in result.stderr
    assert not (tmp_path / "data" / "pulse").exists()


def test_invalid_output_twice_is_an_error(story, monkeypatch):
    story(show_story(4))
    client = use_client(monkeypatch, FakeClient("nope", "still nope"))

    result = runner.invoke(app, ["pulse", str(SHOW_ID)])

    assert result.exit_code == 1 and "after 2 attempts" in result.stderr
    assert client.calls == 2


def test_model_summary_is_cleaned_before_printing(story, monkeypatch):
    story(show_story(4))
    use_client(monkeypatch, FakeClient(reply(summary="bad\x1b[2Jtext‮")))

    result = runner.invoke(app, ["pulse", str(SHOW_ID)])

    assert "\x1b" not in result.output and "‮" not in result.output and "bad [2Jtext" in result.output


def test_story_title_is_cleaned_before_printing(story, monkeypatch):
    story(show_story(4, title="Show HN: \x1b]0;pwn\x07Acme"))
    use_client(monkeypatch, FakeClient(reply()))
    assert "\x1b" not in runner.invoke(app, ["pulse", str(SHOW_ID)]).output


def test_model_timeout_is_a_clear_error_exit_1(story, respx_mock):
    story(show_story(4))
    respx_mock.post(CHAT_URL).mock(side_effect=httpx.ReadTimeout("slow"))

    result = runner.invoke(app, ["pulse", str(SHOW_ID)])

    assert result.exit_code == 1 and "did not answer" in result.stderr


def test_end_to_end_through_the_ollama_http_api(story, respx_mock, tmp_path):
    story(show_story(4))
    respx_mock.post(CHAT_URL).mock(return_value=httpx.Response(200, json={"message": {"content": reply()}}))

    result = runner.invoke(app, ["pulse", str(SHOW_ID)])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "data" / "pulse" / f"{SHOW_ID}.json").exists()


@pytest.mark.parametrize("status", [403, 404, 429, 500])
def test_upstream_hn_error_is_exit_1(story, status):
    story({"error": "x"}, status=status)
    assert runner.invoke(app, ["pulse", str(SHOW_ID)]).exit_code == 1


@pytest.mark.parametrize("item", [[], "x", {"id": 1, "type": "comment", "text": "hi"}, show_story(4, type="job")])
def test_item_that_is_not_a_story_is_an_error(story, item):
    story(item)
    result = runner.invoke(app, ["pulse", str(SHOW_ID)])
    assert result.exit_code == 1 and "is not a story" in result.stderr


def test_story_without_children_is_no_signal(story, monkeypatch):
    story({"id": SHOW_ID, "type": "story", "title": "t"})
    use_client(monkeypatch, FakeClient())
    result = runner.invoke(app, ["pulse", str(SHOW_ID)])
    assert result.exit_code == 0 and "Not enough signal" in result.output


@pytest.mark.parametrize("bad", ["0", "-5", "abc"])
def test_bad_story_id_is_rejected(bad):
    assert runner.invoke(app, ["pulse", bad]).exit_code == 2


def test_out_that_cannot_be_written_is_an_error_not_a_traceback(story, monkeypatch, tmp_path):
    story(show_story(4))
    use_client(monkeypatch, FakeClient(reply()))

    result = runner.invoke(app, ["pulse", str(SHOW_ID), "--out", str(tmp_path)])  # a directory

    assert result.exit_code == 1 and "could not write" in result.stderr
    assert not isinstance(result.exception, OSError)


@pytest.mark.parametrize(
    "children",
    [
        ["text"],
        [{"id": "x", "text": "hello there"}],
        [{"text": "no id here"}],
        [{"id": True, "text": "bool id"}],
        [{"id": 5, "text": 12}],
    ],
)
def test_malformed_comments_are_an_error_not_a_traceback(story, monkeypatch, children):
    story({"id": SHOW_ID, "type": "story", "title": "t", "children": children})
    use_client(monkeypatch, FakeClient())

    result = runner.invoke(app, ["pulse", str(SHOW_ID)])

    assert result.exit_code == 1 and "Error:" in result.stderr
    assert not isinstance(result.exception, (AttributeError, TypeError, KeyError))


def test_children_that_is_not_a_list_is_an_error(story):
    story({"id": SHOW_ID, "type": "story", "title": "t", "children": "oops"})
    result = runner.invoke(app, ["pulse", str(SHOW_ID)])
    assert result.exit_code == 1 and "is not a story" in result.stderr


FIXTURE = Path(__file__).parent / "fixtures" / "show_hn_30100077.json"  # a trimmed real Algolia item, authors removed


def test_recorded_hn_item_goes_through_the_whole_command(respx_mock, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    recorded = json.loads(FIXTURE.read_text(encoding="utf-8"))
    respx_mock.get("https://hn.algolia.com/api/v1/items/30100077").mock(return_value=httpx.Response(200, json=recorded))
    ids = [c["id"] for c in recorded["children"]]
    client = use_client(monkeypatch, FakeClient(reply(summary="Asks about Heroku", ids=[ids[1], ids[3]], kind="ask")))

    result = runner.invoke(app, ["pulse", "30100077"])

    assert result.exit_code == 0, result.output
    assert f"ask | Asks about Heroku | comments {ids[1]}, {ids[3]}" in result.output
    saved = json.loads((tmp_path / "data" / "pulse" / "30100077.json").read_text(encoding="utf-8"))
    assert saved["title"] == recorded["title"] and saved["comments_total"] == len(ids)
    assert client.calls == 1


def test_failed_write_keeps_the_previous_pulse(story, monkeypatch, tmp_path):
    story(show_story(4))
    use_client(monkeypatch, FakeClient(reply()))
    target = tmp_path / "pulse.json"
    target.write_text("previous result", encoding="utf-8")
    real_replace = cli.os.replace

    def replace(src, dst):
        if Path(dst) == target:  # only the pulse file; the HN cache write must still work
            raise OSError("disk full")
        real_replace(src, dst)

    monkeypatch.setattr(cli.os, "replace", replace)

    result = runner.invoke(app, ["pulse", str(SHOW_ID), "--out", str(target)])

    assert result.exit_code == 1 and "could not write" in result.stderr
    assert target.read_text(encoding="utf-8") == "previous result"
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []


def test_new_pulse_replaces_the_old_one_completely(story, monkeypatch, tmp_path):
    story(show_story(4))
    use_client(monkeypatch, FakeClient(reply()))
    target = tmp_path / "pulse.json"
    target.write_text("x" * 100_000, encoding="utf-8")

    assert runner.invoke(app, ["pulse", str(SHOW_ID), "--out", str(target)]).exit_code == 0

    assert json.loads(target.read_text(encoding="utf-8"))["story_id"] == SHOW_ID


def test_unwritable_hn_cache_warns_and_pulse_still_succeeds(story, monkeypatch, tmp_path):
    story(show_story(4))
    use_client(monkeypatch, FakeClient(reply()))
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "cache").write_text("a file where the cache directory should be", encoding="utf-8")

    result = runner.invoke(app, ["pulse", str(SHOW_ID)])

    assert result.exit_code == 0, result.output
    assert result.stderr.count("Warning: could not write the HN cache") == 1
    assert (tmp_path / "data" / "pulse" / f"{SHOW_ID}.json").exists()
