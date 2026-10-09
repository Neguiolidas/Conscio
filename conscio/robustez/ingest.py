"""Ingestão defensiva de dados e respostas de serviços externos.

Origem:
- openmuse: apps/server/src/search.ts:40-169
Stdlib apenas.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any


class IngestTimeout(Exception):
    """Excedido tempo limite de ingestão."""


class IngestTooLarge(Exception):
    """Resposta excedeu o tamanho máximo configurado."""


def fetch_text(
    url: str,
    timeout_s: float = 45.0,
    max_bytes: int = 1024 * 1024,
) -> str:
    """Download de texto via HTTP com streaming, timeout e limite estrito de bytes."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Conscio-Ingest/4.9"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as response:
            chunks: list[bytes] = []
            total_bytes = 0
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > max_bytes:
                    raise IngestTooLarge(f"Payload exceeded max_bytes={max_bytes}")
                chunks.append(chunk)

            raw_bytes = b"".join(chunks)
            return raw_bytes.decode("utf-8", errors="replace")
    except TimeoutError as e:
        raise IngestTimeout(f"Request to {url} timed out after {timeout_s}s") from e
    except urllib.error.URLError as e:
        if isinstance(e.reason, TimeoutError):
            raise IngestTimeout(f"Request to {url} timed out after {timeout_s}s") from e
        raise RuntimeError(f"Ingest failed for {url}: {str(e)[:150]}") from e
    except IngestTooLarge:
        raise
    except Exception as e:
        raise RuntimeError(f"Unexpected ingest error: {str(e)[:150]}") from e


def validate_items(
    items: list[Any],
    validator: Callable[[Any], bool],
) -> tuple[list[Any], int, list[str]]:
    """Valida lista de itens descartando inválidos e gerando relatório honesto de descarte."""
    valid: list[Any] = []
    dropped = 0
    warnings: list[str] = []

    for i, item in enumerate(items):
        try:
            if validator(item):
                valid.append(item)
            else:
                dropped += 1
                warnings.append(f"Item at index {i} failed validation: {str(item)[:80]}")
        except Exception as e:
            dropped += 1
            warnings.append(f"Item at index {i} raised during validation: {str(e)[:80]}")

    return valid, dropped, warnings


def apply_char_budget(
    texts: list[str],
    budget: int = 30000,
) -> tuple[list[str], bool]:
    """Aplica limite estrito de caracteres cumulativos sobre uma lista de textos."""
    bounded: list[str] = []
    current_chars = 0
    truncated = False

    for t in texts:
        t_len = len(t)
        if current_chars + t_len <= budget:
            bounded.append(t)
            current_chars += t_len
        else:
            remaining = budget - current_chars
            if remaining > 0:
                bounded.append(t[:remaining])
            truncated = True
            break

    return bounded, truncated
