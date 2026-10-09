"""Retry em 3 camadas com budgets por item da cadeia de provedores LLM.

Origem:
- foreman: foreman/llm.py:137-210 (create_with_fallback)
Budgets por item:
1. Erro transitório (5xx, timeouts, connection resets): mesmo item com backoff 1/2/4s (até 3x).
2. 429 com hint curto ('retry in Ns'): espera o tempo indicado até 6x.
3. Quota / 403 / esgotamento de retries: fallback para o próximo item da cadeia com budgets zerados.
4. Erros não-recuperáveis (400 Bad Request, validação de prompt): levanta imediatamente sem mascaramento.
Stdlib apenas (re, time).
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any

_HINT_PATTERN = re.compile(r"retry\s+(?:in|after)\s+(\d+(?:\.\d+)?)\s*s?", re.IGNORECASE)


def _get_status_code(exc: Exception) -> int | None:
    for attr in ("status_code", "http_status", "status", "code"):
        val = getattr(exc, attr, None)
        if isinstance(val, int):
            return val
    return None


def _default_is_transient(exc: Exception) -> bool:
    code = _get_status_code(exc)
    if code is not None and code in (500, 502, 503, 504):
        return True
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    msg = str(exc).lower()
    return any(term in msg for term in ("timeout", "connection reset", "temporarily unavailable", "overloaded"))


def _default_is_rate_limit(exc: Exception) -> tuple[bool, float | None]:
    code = _get_status_code(exc)
    msg = str(exc)
    is_429 = code == 429 or "rate limit" in msg.lower() or "too many requests" in msg.lower()
    if not is_429:
        return False, None

    # Check for hint
    m = _HINT_PATTERN.search(msg)
    if m:
        try:
            hint = float(m.group(1))
            return True, hint
        except Exception:
            pass
    return True, None


def _default_is_quota(exc: Exception) -> bool:
    code = _get_status_code(exc)
    if code in (402, 403):
        return True
    msg = str(exc).lower()
    return any(term in msg for term in ("insufficient_quota", "quota exceeded", "billing", "credit exhausted"))


def call_with_fallback(
    call: Callable[..., Any],
    chain: list[Any],
    *,
    is_transient: Callable[[Exception], bool] | None = None,
    is_rate_limit: Callable[[Exception], tuple[bool, float | None]] | None = None,
    is_quota: Callable[[Exception], bool] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    on_rate_limit_wait: Callable[[float], None] | None = None,
    on_fallback: Callable[[Any, Any, Exception], None] | None = None,
    max_transient_retries: int = 3,
    max_rate_limit_retries: int = 6,
    **kwargs: Any,
) -> Any:
    """Executa chamada com fallback por item da cadeia, orquestrando budgets independentes por provider."""
    if not chain:
        raise ValueError("Provider chain must contain at least one item")

    fn_is_transient = is_transient or _default_is_transient
    fn_is_rate_limit = is_rate_limit or _default_is_rate_limit
    fn_is_quota = is_quota or _default_is_quota

    last_exc: Exception | None = None

    for i, provider_item in enumerate(chain):
        transient_attempts = 0
        rate_limit_attempts = 0

        while True:
            try:
                return call(provider_item, **kwargs)
            except Exception as e:
                code = _get_status_code(e)
                # 400 Bad Request or unrecoverable client errors: raise immediately
                if code == 400:
                    raise

                # Check quota exhaustion -> immediate fallback
                if fn_is_quota(e):
                    last_exc = e
                    if i + 1 < len(chain) and on_fallback:
                        on_fallback(provider_item, chain[i + 1], e)
                    break

                # Check rate limit (429)
                is_rl, hint = fn_is_rate_limit(e)
                if is_rl:
                    rate_limit_attempts += 1
                    if rate_limit_attempts <= max_rate_limit_retries:
                        wait_s = hint if hint is not None and hint > 0 else 2.0
                        wait_s = min(wait_s, 60.0)
                        if on_rate_limit_wait:
                            on_rate_limit_wait(wait_s)
                        sleep(wait_s)
                        continue
                    else:
                        # Exceeded rate limit budget for this item -> fallback
                        last_exc = e
                        if i + 1 < len(chain) and on_fallback:
                            on_fallback(provider_item, chain[i + 1], e)
                        break

                # Check transient errors
                if fn_is_transient(e):
                    transient_attempts += 1
                    if transient_attempts <= max_transient_retries:
                        backoff = 2 ** (transient_attempts - 1)  # 1s, 2s, 4s...
                        sleep(backoff)
                        continue
                    else:
                        # Exceeded transient budget -> fallback
                        last_exc = e
                        if i + 1 < len(chain) and on_fallback:
                            on_fallback(provider_item, chain[i + 1], e)
                        break

                # Any other unclassified error raises immediately
                raise

    if last_exc:
        raise last_exc
    raise RuntimeError("All providers in fallback chain exhausted")
