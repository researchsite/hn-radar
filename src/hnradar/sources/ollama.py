"""Local model access (Ollama). The only place that talks to the model server (rules 3 and 10)."""

import httpx

DEFAULT_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen2.5:7b"
TIMEOUT = 180  # seconds; a 7b model on a CPU can take minutes for a long thread
MAX_REPLY_TOKENS = 2048


class ModelError(RuntimeError):
    pass


def _reason(response: httpx.Response) -> str:
    """Ollama explains a failure in {"error": "..."} (model not found, not enough memory)."""
    try:
        reason = response.json().get("error")
    except (ValueError, AttributeError):
        return ""
    return f": {reason[:200]}" if isinstance(reason, str) and reason else ""


class OllamaClient:
    def __init__(self, model: str = DEFAULT_MODEL, *, base_url: str = DEFAULT_URL, timeout: float = TIMEOUT):
        self.name = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def complete(self, system: str, user: str, schema: dict) -> str:
        body = {
            "model": self.name,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": False,
            "format": schema,  # constrains the reply; it is still validated by the caller
            # num_predict bounds the reply so a looping model stops; a cut-off reply is invalid JSON, so the
            # caller rejects it (rule 6) instead of using part of it.
            "options": {"temperature": 0, "num_predict": MAX_REPLY_TOKENS},
        }
        try:
            response = httpx.post(f"{self.base_url}/api/chat", json=body, timeout=self.timeout)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise ModelError(f"{self.name} did not answer within {self.timeout:g}s") from exc
        except httpx.ConnectError as exc:
            raise ModelError(f"cannot reach Ollama at {self.base_url}; is it running?") from exc
        except httpx.HTTPStatusError as exc:
            raise ModelError(
                f"Ollama returned HTTP {exc.response.status_code} for model {self.name}{_reason(exc.response)}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ModelError(f"request to Ollama failed: {type(exc).__name__}") from exc

        try:
            content = response.json()["message"]["content"]
        except (ValueError, KeyError, TypeError) as exc:
            raise ModelError("Ollama sent a reply in an unexpected shape") from exc
        if not isinstance(content, str):
            raise ModelError("Ollama sent a reply in an unexpected shape")
        return content
