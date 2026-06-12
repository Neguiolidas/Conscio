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


@dataclass
class Meter:
    """Shared inference odometer — the binding budget reads this (P3)."""
    calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    latencies_ms: list[int] = field(default_factory=list)

    @property
    def tokens(self) -> int:
        return self.tokens_in + self.tokens_out


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


class MeteredAdapter(InferenceAdapter):
    """Transparent wrapper counting calls/tokens/latency on a Meter.

    A failed call still debits one call: the budget pays for attempts.
    `wrapped_name` keeps the real adapter class visible to the ledger.
    """

    def __init__(self, inner: InferenceAdapter, meter: Meter):
        self.inner = inner
        self.meter = meter
        self.wrapped_name = type(inner).__name__

    def generate(self, prompt: str, *, schema: dict | None = None,
                 grammar: str | None = None, max_tokens: int = 512,
                 temperature: float = 0.2,
                 stop: list[str] | None = None) -> InferenceResult:
        self.meter.calls += 1
        result = self.inner.generate(
            prompt, schema=schema, grammar=grammar, max_tokens=max_tokens,
            temperature=temperature, stop=stop)
        self.meter.tokens_in += result.tokens_in
        self.meter.tokens_out += result.tokens_out
        self.meter.latencies_ms.append(result.latency_ms)
        return result

    def capabilities(self) -> AdapterCaps:
        return self.inner.capabilities()
