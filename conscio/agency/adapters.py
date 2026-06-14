"""
Concrete inference adapters (spec section 5.1). stdlib urllib only —
no `requests`. All defaults point at localhost: independence means the
framework runs fully local; pointing at a cloud endpoint is the
operator's choice, never a requirement.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from .adapter import (
    AdapterBadResponse,
    AdapterCaps,
    AdapterConnectionError,
    AdapterTimeout,
    InferenceAdapter,
    InferenceResult,
)


def _post_json(url: str, payload: dict, timeout: float,
               headers: dict | None = None) -> dict:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except TimeoutError as exc:
        raise AdapterTimeout(str(exc)) from exc
    except urllib.error.HTTPError as exc:        # server responded 4xx/5xx
        raise AdapterBadResponse(f"HTTP {exc.code}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        if isinstance(getattr(exc, "reason", None), TimeoutError):
            raise AdapterTimeout(str(exc)) from exc
        raise AdapterConnectionError(str(exc)) from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise AdapterBadResponse(str(exc)) from exc


class OllamaAdapter(InferenceAdapter):
    def __init__(self, *, model: str,
                 base_url: str = "http://localhost:11434",
                 timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def generate(self, prompt, *, schema=None, grammar=None, max_tokens=512,
                 temperature=0.2, stop=None) -> InferenceResult:
        payload = {"model": self.model, "prompt": prompt, "stream": False,
                   "options": {"temperature": temperature,
                               "num_predict": max_tokens}}
        if schema is not None:
            payload["format"] = "json"
        if stop:
            payload["options"]["stop"] = stop
        start = time.monotonic()
        data = _post_json(f"{self.base_url}/api/generate", payload,
                          self.timeout)
        return InferenceResult(
            text=str(data.get("response", "")),
            tokens_in=int(data.get("prompt_eval_count", 0)),
            tokens_out=int(data.get("eval_count", 0)),
            latency_ms=int((time.monotonic() - start) * 1000))

    def capabilities(self) -> AdapterCaps:
        return AdapterCaps(model_name=self.model, json_mode=True,
                           grammar=False)


class LlamaCppAdapter(InferenceAdapter):
    """llama.cpp server — native GBNF grammar support (tier 1, F3)."""

    def __init__(self, *, base_url: str = "http://localhost:8080",
                 timeout: float = 120.0, model_name: str = "llama.cpp"):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.model_name = model_name

    def generate(self, prompt, *, schema=None, grammar=None, max_tokens=512,
                 temperature=0.2, stop=None) -> InferenceResult:
        payload = {"prompt": prompt, "n_predict": max_tokens,
                   "temperature": temperature}
        if grammar is not None:
            payload["grammar"] = grammar
        if stop:
            payload["stop"] = stop
        start = time.monotonic()
        data = _post_json(f"{self.base_url}/completion", payload, self.timeout)
        return InferenceResult(
            text=str(data.get("content", "")),
            tokens_in=int(data.get("tokens_evaluated", 0)),
            tokens_out=int(data.get("tokens_predicted", 0)),
            latency_ms=int((time.monotonic() - start) * 1000))

    def capabilities(self) -> AdapterCaps:
        return AdapterCaps(model_name=self.model_name, json_mode=False,
                           grammar=True)


class OpenAICompatAdapter(InferenceAdapter):
    def __init__(self, *, model: str,
                 base_url: str = "http://localhost:8000/v1",
                 api_key: str = "", timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    def generate(self, prompt, *, schema=None, grammar=None, max_tokens=512,
                 temperature=0.2, stop=None) -> InferenceResult:
        payload = {"model": self.model,
                   "messages": [{"role": "user", "content": prompt}],
                   "max_tokens": max_tokens, "temperature": temperature}
        if schema is not None:
            payload["response_format"] = {"type": "json_object"}
        if stop:
            payload["stop"] = stop
        headers = ({"Authorization": f"Bearer {self.api_key}"}
                   if self.api_key else {})
        start = time.monotonic()
        data = _post_json(f"{self.base_url}/chat/completions", payload,
                          self.timeout, headers)
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AdapterBadResponse(f"unexpected payload: {exc}") from exc
        usage = data.get("usage", {})
        return InferenceResult(
            text=str(text),
            tokens_in=int(usage.get("prompt_tokens", 0)),
            tokens_out=int(usage.get("completion_tokens", 0)),
            latency_ms=int((time.monotonic() - start) * 1000))

    def capabilities(self) -> AdapterCaps:
        return AdapterCaps(model_name=self.model, json_mode=True,
                           grammar=False)
