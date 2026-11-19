import json

import httpx
import pytest

from hnradar.sources.ollama import DEFAULT_MODEL, MAX_REPLY_TOKENS, ModelError, OllamaClient

CHAT_URL = "http://localhost:11434/api/chat"


def test_sends_model_messages_schema_and_returns_the_reply_text(respx_mock):
    route = respx_mock.post(CHAT_URL).mock(
        return_value=httpx.Response(200, json={"message": {"role": "assistant", "content": '{"items": []}'}})
    )

    text = OllamaClient().complete("sys", "user", {"type": "object"})

    assert text == '{"items": []}'
    sent = json.loads(route.calls.last.request.content)
    assert sent["model"] == DEFAULT_MODEL == "qwen2.5:7b"
    assert sent["stream"] is False and sent["format"] == {"type": "object"}
    assert [m["role"] for m in sent["messages"]] == ["system", "user"]
    assert sent["options"] == {"temperature": 0, "num_predict": MAX_REPLY_TOKENS}


def test_model_name_is_configurable(respx_mock):
    route = respx_mock.post(CHAT_URL).mock(return_value=httpx.Response(200, json={"message": {"content": "x"}}))
    client = OllamaClient("qwen2.5:14b")
    client.complete("s", "u", {})
    assert client.name == "qwen2.5:14b"
    assert json.loads(route.calls.last.request.content)["model"] == "qwen2.5:14b"


def test_timeout_is_a_clear_error(respx_mock):
    respx_mock.post(CHAT_URL).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(ModelError, match="did not answer within 180s"):
        OllamaClient().complete("s", "u", {})


def test_server_not_running_is_a_clear_error(respx_mock):
    respx_mock.post(CHAT_URL).mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(ModelError, match="is it running"):
        OllamaClient().complete("s", "u", {})


@pytest.mark.parametrize("status", [404, 429, 500])
def test_http_error_is_a_clear_error_with_ollamas_reason(respx_mock, status):
    respx_mock.post(CHAT_URL).mock(return_value=httpx.Response(status, json={"error": "model not found"}))
    with pytest.raises(ModelError, match=f"HTTP {status} for model qwen2.5:7b: model not found"):
        OllamaClient().complete("s", "u", {})


@pytest.mark.parametrize("body", [{}, [], {"error": 5}, {"error": ""}])
def test_http_error_without_a_usable_reason_still_raises(respx_mock, body):
    respx_mock.post(CHAT_URL).mock(return_value=httpx.Response(500, json=body))
    with pytest.raises(ModelError, match="HTTP 500 for model qwen2.5:7b$"):
        OllamaClient().complete("s", "u", {})


def test_http_error_with_a_non_json_body_still_raises(respx_mock):
    respx_mock.post(CHAT_URL).mock(return_value=httpx.Response(502, text="<html>bad gateway</html>"))
    with pytest.raises(ModelError, match="HTTP 502"):
        OllamaClient().complete("s", "u", {})


def test_a_very_long_reason_is_cut(respx_mock):
    respx_mock.post(CHAT_URL).mock(return_value=httpx.Response(500, json={"error": "x" * 5000}))
    with pytest.raises(ModelError) as excinfo:
        OllamaClient().complete("s", "u", {})
    assert len(str(excinfo.value)) < 300


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={}),
        httpx.Response(200, json=[]),
        httpx.Response(200, json={"message": None}),
        httpx.Response(200, json={"message": {"content": 5}}),
    ],
)
def test_malformed_body_is_an_error_not_an_empty_reply(respx_mock, response):
    respx_mock.post(CHAT_URL).mock(return_value=response)
    with pytest.raises(ModelError, match="unexpected shape"):
        OllamaClient().complete("s", "u", {})


def test_other_transport_failures_are_wrapped(respx_mock):
    respx_mock.post(CHAT_URL).mock(side_effect=httpx.RemoteProtocolError("cut off"))
    with pytest.raises(ModelError, match="RemoteProtocolError"):
        OllamaClient().complete("s", "u", {})
