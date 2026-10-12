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
import random
import time
from dataclasses import dataclass
from typing import Any

from ..output_filter import SecretMask
from .adapter import (
    AdapterBadResponse,
    AdapterCaps,
    AdapterConnectionError,
    AdapterError,
    AdapterTimeout,
    InferenceAdapter,
    InferenceResult,
    ProvidersExhausted,
)

log = logging.getLogger("conscio.fallback")

_DEFAULT_RETRY = 2
_DEFAULT_BACKOFF_BASE = 1.0
_DEFAULT_BACKOFF_MAX = 10.0
_DEFAULT_TIMEOUT = 120.0

# Extra patterns for exception sanitization beyond SecretMask defaults
_EXCEPTION_EXTRA_PATTERNS = [
    r"(?i)\bBearer\s+\S+",
    r"(?i)\bAuthorization:\s*(?:(?:Bearer|Basic)\s+)?\S+",
    r"(?i)\bapi[_-]?key\s*[:=]\s*\S+",
    r"\bnvapi-[A-Za-z0-9_-]+\b",
    r"\bsk-[A-Za-z0-9_-]+\b",
    r"\bng-[A-Za-z0-9_-]+\b",
    r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b",
]

_EXCEPTION_MASK = SecretMask(
    replacement="[REDACTED]",
    extra_patterns=_EXCEPTION_EXTRA_PATTERNS,
)


def _sanitize_exc(exc: Exception) -> str:
    """Strip credentials from exception messages before logging."""
    return _EXCEPTION_MASK.apply(str(exc))


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
                    safe_msg = _sanitize_exc(exc)
                    from ..failure import FailureClass, FailureGovernor
                    cls = FailureGovernor.classify(exc)
                    if not FailureGovernor.should_retry(cls):
                        log.warning(
                            "provider %d (%s @ %s) failed with %s error: "
                            "%s — skipping retries",
                            idx, cfg.model, cfg.base_url, cls.value, safe_msg,
                        )
                        break

                    retry_after = (
                        getattr(exc, "retry_after", None)
                        if cls is FailureClass.RATE_LIMIT
                        else None
                    )
                    if retry_after is not None and retry_after > self._backoff_max:
                        log.warning(
                            "provider %d (%s @ %s) Retry-After %.1fs exceeds backoff_max %.1fs — advancing",
                            idx, cfg.model, cfg.base_url, retry_after, self._backoff_max,
                        )
                        break

                    wait = min(
                        self._backoff_base * (2 ** attempt),
                        self._backoff_max,
                    ) * (0.8 + 0.4 * random.random())
                    if retry_after is not None:
                        wait = max(wait, retry_after)
                    # Sanitize error message: strip any Authorization header
                    # or api_key that an API might echo in error responses
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
            raise ProvidersExhausted(
                f"all {len(self._configs)} providers exhausted"
            ) from last_exc
        raise AdapterError("fallback chain exhausted with no error")

    def capabilities(self) -> AdapterCaps:
        return self._adapters[self._current_index].capabilities()
