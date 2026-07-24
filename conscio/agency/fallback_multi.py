"""MultiProviderFallbackAdapter (v3.3): fallback chain across providers.

Extends FallbackAdapter to support multiple providers with different
base_urls and api_keys (NVIDIA, MiniMax proxy, Naga, OpenAI, etc).
Retries with exponential backoff before switching to the next provider.

Config example (config.json):

  {
    "adapter": {
      "type": "multi-fallback",
      "providers": [
        {
          "model": "nvidia/nemotron-3-ultra-550b-a55b",
          "base_url": "https://integrate.api.nvidia.com/v1",
          "api_key": "nvapi-xxx"
        },
        {
          "model": "minimaxai/minimax-m3",
          "base_url": "http://127.0.0.1:8777/v1",
          "api_key": "key-xxx"
        },
        {
          "model": "nemotron-3-ultra-550b-a55b:free",
          "base_url": "https://api.naga.ac/v1",
          "api_key": "ng-xxx"
        }
      ],
      "retry_per_provider": 2,
      "backoff_base": 1.0,
      "backoff_max": 10.0,
      "timeout": 120.0
    }
  }

The first provider that responds wins. On failure (timeout, 429, 5xx,
connection error), retries with backoff, then falls to the next provider.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from .adapter import (
    AdapterBadResponse,
    AdapterConnectionError,
    AdapterError,
    AdapterTimeout,
    AdapterCaps,
    InferenceAdapter,
    InferenceResult,
)

log = logging.getLogger("conscio.fallback")

_DEFAULT_RETRY = 2
_DEFAULT_BACKOFF_BASE = 1.0
_DEFAULT_BACKOFF_MAX = 10.0
_DEFAULT_TIMEOUT = 120.0

# Patterns to strip from error messages to prevent credential leakage
_SENSITIVE_PATTERNS = (
    "Bearer ", "Authorization:", "api_key=", "api-key:",
    "nvapi-", "sk-", "ng-",
)


def _sanitize_exc(exc: Exception) -> str:
    """Strip credentials from exception messages before logging."""
    msg = str(exc)
    for pat in _SENSITIVE_PATTERNS:
        idx = msg.find(pat)
        if idx >= 0:
            # Replace everything from the pattern to the next space or end
            end = msg.find(" ", idx + len(pat))
            if end < 0:
                end = len(msg)
            msg = msg[:idx] + pat + "[REDACTED]" + msg[end:]
    return msg


@dataclass
class ProviderConfig:
    """One provider in the fallback chain."""

    model: str
    base_url: str
    api_key: str = ""
    timeout: float = _DEFAULT_TIMEOUT


class MultiProviderFallbackAdapter(InferenceAdapter):
    """Adapter that falls back across providers with retry + backoff.

    Each provider has its own model, base_url, api_key, and timeout.
    On failure: retry up to ``retry_per_provider`` times with exponential
    backoff (``backoff_base * 2^attempt``, capped at ``backoff_max``),
    then advance to the next provider. If all providers are exhausted,
    raises the last error.
    """

    def __init__(
        self,
        *,
        providers: list[dict[str, Any]] | list[ProviderConfig],
        retry_per_provider: int = _DEFAULT_RETRY,
        backoff_base: float = _DEFAULT_BACKOFF_BASE,
        backoff_max: float = _DEFAULT_BACKOFF_MAX,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        from .adapters import OpenAICompatAdapter

        # Normalize provider configs
        self._configs: list[ProviderConfig] = []
        for p in providers:
            if isinstance(p, ProviderConfig):
                self._configs.append(p)
            else:
                self._configs.append(ProviderConfig(
                    model=p["model"],
                    base_url=p["base_url"],
                    api_key=p.get("api_key", ""),
                    timeout=p.get("timeout", timeout),
                ))

        if not self._configs:
            raise ValueError("multi-fallback requires at least 1 provider")

        self._retry_per_provider = retry_per_provider
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max
        self._current_index = 0

        # Build one OpenAICompatAdapter per provider
        self._adapters: list[OpenAICompatAdapter] = []
        for cfg in self._configs:
            self._adapters.append(OpenAICompatAdapter(
                model=cfg.model,
                base_url=cfg.base_url,
                api_key=cfg.api_key,
                timeout=cfg.timeout,
            ))

        log.info(
            "MultiProviderFallbackAdapter: %d providers, retry=%d, "
            "backoff=%.1f-%.1fs",
            len(self._configs), self._retry_per_provider,
            self._backoff_base, self._backoff_max,
        )

    # -- InferenceAdapter ------------------------------------------------

    @property
    def current_model(self) -> str:
        return self._configs[self._current_index].model

    @property
    def current_provider(self) -> str:
        return self._configs[self._current_index].base_url

    def generate(
        self,
        prompt,
        *,
        schema=None,
        grammar=None,
        max_tokens=512,
        temperature=0.2,
        stop=None,
    ) -> InferenceResult:
        last_exc: Exception | None = None

        for idx, (adapter, cfg) in enumerate(
            zip(self._adapters, self._configs)):
            self._current_index = idx
            for attempt in range(self._retry_per_provider):
                try:
                    result = adapter.generate(
                        prompt,
                        schema=schema,
                        grammar=grammar,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        stop=stop,
                    )
                    if idx > 0:
                        log.info(
                            "fallback recovered: provider %d (%s @ %s)",
                            idx, cfg.model, cfg.base_url,
                        )
                    return result
                except (
                    AdapterTimeout,
                    AdapterBadResponse,
                    AdapterConnectionError,
                    AdapterError,
                    TimeoutError,
                    ConnectionError,
                    OSError,
                ) as exc:
                    last_exc = exc
                    wait = min(
                        self._backoff_base * (2 ** attempt),
                        self._backoff_max,
                    )
                    # Sanitize error message: strip any Authorization header
                    # or api_key that an API might echo in error responses
                    safe_msg = _sanitize_exc(exc)
                    log.warning(
                        "provider %d (%s @ %s) failed on attempt %d/%d: "
                        "%s — retrying in %.1fs",
                        idx, cfg.model, cfg.base_url,
                        attempt + 1, self._retry_per_provider,
                        safe_msg, wait,
                    )
                    if attempt < self._retry_per_provider - 1:
                        time.sleep(wait)
                    # else: fall through to next provider

            log.warning(
                "provider %d (%s @ %s) exhausted — advancing",
                idx, cfg.model, cfg.base_url,
            )

        # All providers exhausted
        if last_exc:
            raise AdapterError(
                f"all {len(self._configs)} providers exhausted"
            ) from last_exc
        raise AdapterError("fallback chain exhausted with no error")

    def capabilities(self) -> AdapterCaps:
        return self._adapters[self._current_index].capabilities()
