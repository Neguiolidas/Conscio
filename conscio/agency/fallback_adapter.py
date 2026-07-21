"""FallbackAdapter (v3.1): model-agnostic adapter with automatic fallback.

Wraps an OpenAI-compatible adapter. If the primary model fails with a
PERMANENT error (or after max retries), it switches to the next model
in the chain. The chain is discovered from the /v1/models endpoint.

Usage:
    adapter = FallbackAdapter(
        base_url="http://localhost:1234/v1",
        models=["liquid/lfm2.5-1.2b", "qwen3.5-0.8b", "bonsai-4b"],
    )
    # If lfm2.5 fails permanently, switches to qwen3.5, then bonsai-4b.
"""
from __future__ import annotations


from .adapter import (
    AdapterCaps,
    AdapterError,
    AdapterBadResponse,
    InferenceAdapter,
    InferenceResult,
)


class FallbackAdapter(InferenceAdapter):
    """Adapter that falls back to the next model when the current one fails.

    Failure handling:
    - AdapterBadResponse / timeout -> switch to next model, retry
    - Any PERMANENT error -> switch to next model
    - If all models exhausted -> raise the last error
    """

    def __init__(self, *, base_url: str, models: list[str],
                 api_key: str = "", timeout: float = 120.0):
        from .adapters import OpenAICompatAdapter
        self.base_url = base_url.rstrip("/")
        self.models = models
        self.current_index = 0
        self.timeout = timeout
        # Create one adapter per model (lightweight — just stores config)
        self._adapters: list[OpenAICompatAdapter] = []
        for m in models:
            self._adapters.append(OpenAICompatAdapter(
                model=m, base_url=base_url, api_key=api_key, timeout=timeout,
            ))

    @property
    def current_model(self) -> str:
        return self.models[self.current_index] if self.models else ""

    def _current(self) -> InferenceAdapter:
        return self._adapters[self.current_index]

    def _advance(self) -> bool:
        """Switch to next model. Returns True if there's a next model."""
        if self.current_index < len(self.models) - 1:
            self.current_index += 1
            return True
        return False

    def generate(self, prompt, *, schema=None, grammar=None, max_tokens=512,
                 temperature=0.2, stop=None) -> InferenceResult:
        last_exc: Exception | None = None
        attempts = 0
        max_total_attempts = len(self.models) * 2  # 2 tries per model

        while attempts < max_total_attempts:
            attempts += 1
            adapter = self._current()
            try:
                return adapter.generate(
                    prompt, schema=schema, grammar=grammar,
                    max_tokens=max_tokens, temperature=temperature, stop=stop,
                )
            except (AdapterBadResponse, AdapterError) as exc:
                last_exc = exc
                # Try switching to next model
                if self._advance():
                    continue
                # All models exhausted
                raise
            except Exception as exc:
                last_exc = exc
                if self._advance():
                    continue
                raise

        if last_exc:
            raise last_exc
        raise AdapterError("fallback chain exhausted with no error")

    def capabilities(self) -> AdapterCaps:
        return self._current().capabilities()
