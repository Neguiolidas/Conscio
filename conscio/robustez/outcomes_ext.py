"""Classificação estrita de outcomes (failed vs unknown), retentabilidade e sanitização de URLs.

Origem:
- OpenTag: agent/arcade_tools/outcomes.py:1-199
Distinção fundamental entre FAILED (seguro para retry com confirmação do provider) e UNKNOWN (onde retry arrisca duplicar escrita).
Stdlib apenas.
"""

from __future__ import annotations

import enum
import re


class Outcome(enum.Enum):
    """Categorização de resultado de ações e ferramentas."""
    SUCCESS = "success"
    FAILED = "failed"
    UNKNOWN = "unknown"
    RATE_LIMITED = "rate_limited"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    PERMISSION_DENIED = "permission_denied"
    INVALID_REQUEST = "invalid_request"


RETRYABLE: frozenset[Outcome] = frozenset({
    Outcome.FAILED,
    Outcome.RATE_LIMITED,
})

_URL_PATTERN = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^\s]+")

SENTENCES: dict[Outcome, str] = {
    Outcome.SUCCESS: "Operation completed successfully.",
    Outcome.FAILED: "Operation failed and may be retried.",
    Outcome.UNKNOWN: "Operation result is unknown; do not retry without verifying state.",
    Outcome.RATE_LIMITED: "Rate limit encountered; back off before retrying.",
    Outcome.CANCELLED: "Operation was cancelled.",
    Outcome.TIMEOUT: "Operation timed out.",
    Outcome.PERMISSION_DENIED: "Permission denied; action cannot be performed.",
    Outcome.INVALID_REQUEST: "Request was invalid and cannot be completed as specified.",
}


def safe_to_retry(outcome: Outcome, can_retry_provider: bool = False) -> bool:
    """Verifica se a operação é segura para retentativa.
    
    True somente se o outcome for intrinsecamente retentável E o provedor permitir.
    UNKNOWN nunca é retentado diretamente para evitar duplicação de escrita.
    """
    if outcome not in RETRYABLE:
        return False
    return can_retry_provider


def scrub_text(text: str) -> str:
    """Remove trechos contendo URLs completas (://) antes de exibir ou logar externamente."""
    if not text:
        return ""
    return _URL_PATTERN.sub("[url-removed]", text)


def describe_outcome(outcome: Outcome, action: str = "", message: str = "") -> str:
    """Retorna frase amigável contextualizada para quem decide o próximo passo."""
    base_sentence = SENTENCES.get(outcome, "Operation ended with status: " + outcome.value)
    prefix = f"[{action}] " if action else ""
    suffix = f" Details: {scrub_text(message)}" if message else ""
    return f"{prefix}{base_sentence}{suffix}"
