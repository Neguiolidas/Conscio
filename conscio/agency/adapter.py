# conscio/agency/adapter.py
"""
InferenceAdapter — decouples the agentic core from any inference backend
(spec section 5.1 / blueprint section 7).

The engine never does HTTP itself; it talks to this interface. MockAdapter
is the deterministic backend used by the entire test suite.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class AdapterError(Exception):
    """Base error for inference failures."""


class AdapterTimeout(AdapterError):
    pass


class AdapterConnectionError(AdapterError):
    pass


class AdapterBadResponse(AdapterError):
    pass


@dataclass
class InferenceResult:
    text: str
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0


@dataclass
class AdapterCaps:
    model_name: str = "mock"
    json_mode: bool = True
    grammar: bool = False


class InferenceAdapter(ABC):
    @abstractmethod
    def generate(self, prompt: str, *, schema: dict | None = None,
                 grammar: str | None = None, max_tokens: int = 512,
                 temperature: float = 0.2,
                 stop: list[str] | None = None) -> InferenceResult: ...

    @abstractmethod
    def capabilities(self) -> AdapterCaps: ...


class MockAdapter(InferenceAdapter):
    """Scriptable adapter: returns queued responses, records every call."""

    def __init__(self, script: list[str] | None = None,
                 caps: AdapterCaps | None = None):
        self._script = list(script or [])
        self._caps = caps or AdapterCaps()
        self.calls: list[dict[str, Any]] = []

    def generate(self, prompt: str, *, schema: dict | None = None,
                 grammar: str | None = None, max_tokens: int = 512,
                 temperature: float = 0.2,
                 stop: list[str] | None = None) -> InferenceResult:
        self.calls.append({"prompt": prompt, "schema": schema,
                           "grammar": grammar, "max_tokens": max_tokens,
                           "temperature": temperature, "stop": stop})
        if not self._script:
            raise AdapterError("MockAdapter script exhausted")
        text = self._script.pop(0)
        return InferenceResult(text=text, tokens_in=len(prompt) // 4,
                               tokens_out=len(text) // 4)

    def capabilities(self) -> AdapterCaps:
        return self._caps
