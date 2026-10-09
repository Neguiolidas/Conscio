"""Higiene de segredos em logs, mensagens e diagnósticos de erro.

Origem:
- OpenDots: src/server/slack-channel.ts:7-26, src/server/computer-service.ts:90-116
- OpenTag: app/channel-helpers.ts:74-93
- openmuse: apps/server/src/log.ts:1-29
Stdlib apenas.
"""

from __future__ import annotations

import re
from typing import Any

# Allowed exception/failure class names in public logs
_SAFE_EXCEPTION_NAMES: frozenset[str] = frozenset({
    "ValueError",
    "TypeError",
    "KeyError",
    "TimeoutError",
    "RuntimeError",
    "PermissionError",
    "FileNotFoundError",
    "ConnectionError",
    "HTTPError",
    "OSError",
    "IndexError",
    "AttributeError",
    "NotImplementedError",
})

_CODE_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

# Regular expressions detecting common API key / secret patterns
_TOKEN_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),                          # OpenAI / Generic sk-
    re.compile(r"sk-ant-[A-Za-z0-9_-]+"),                         # Anthropic
    re.compile(r"xox[baprs]-[A-Za-z0-9-]+"),                      # Slack tokens
    re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"),                    # GitHub tokens
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+"), # JWT
    re.compile(r"(?i)bearer\s+[A-Za-z0-9_\-\.]{20,}"),            # Bearer tokens
]


def safe_failure(error: Exception | str, status: int | None = None) -> str:
    """Format safe failure representation: 'Name (HTTP status)' or 'Name'.
    
    Names outside the whitelist collapse to 'Error' - never leaking message/stack.
    """
    if isinstance(error, Exception):
        name = type(error).__name__
    else:
        name = str(error).strip()

    safe_name = name if name in _SAFE_EXCEPTION_NAMES else "Error"
    if status is not None:
        return f"{safe_name} (HTTP {status})"
    return safe_name


def background_failure(phase: str, error: Exception | str, code: str | None = None) -> str:
    """Format failure in background tasks. code is only included if it matches ^[A-Za-z0-9_.-]{1,64}$."""
    err_str = safe_failure(error)
    if code and _CODE_PATTERN.match(code):
        return f"[{phase}] {err_str} ({code})"
    return f"[{phase}] {err_str}"


def provider_failure(phase: str, status: int, request_id: str | None = None) -> str:
    """Format external provider failure exposing only phase, HTTP status, and sanitized request ID."""
    req_part = f" (request_id: {request_id})" if request_id else ""
    return f"Provider failure in {phase}: status {status}{req_part}"


def redact_secrets(text: str, secrets: list[str] | set[str] | None = None) -> str:
    """Redact known secret strings from text prior to parsing or emitting."""
    if not text or not secrets:
        return text
    out = text
    for sec in secrets:
        if sec:
            out = out.replace(sec, "[redacted]")
    return out


def redact_tokens(text: str) -> str:
    """Redact common credential patterns (OpenAI, Anthropic, Slack, GitHub, JWT, Bearer)."""
    if not text:
        return ""
    out = text
    for pattern in _TOKEN_PATTERNS:
        out = pattern.sub("[redacted]", out)
    return out


def sanitize_reason(text: str) -> str:
    """Extract first line, redact credentials, and cap at 180 characters."""
    if not text:
        return ""
    first_line = text.splitlines()[0] if "\n" in text or "\r" in text else text
    redacted = redact_tokens(first_line)
    return redacted[:180]


def user_facing_error(error: Any) -> str:
    """Return user-friendly message for known failures without leaking internals."""
    err_text = str(error).lower()

    if isinstance(error, TimeoutError) or "timeout" in err_text:
        return "The request timed out. Please try again."
    if "rate limit" in err_text or "429" in err_text:
        return "Rate limit reached. Please wait before retrying."
    if isinstance(error, PermissionError) or "401" in err_text or "403" in err_text or "access denied" in err_text:
        return "Authentication failed or access denied."
    if isinstance(error, FileNotFoundError) or "404" in err_text or "not found" in err_text:
        return "Resource not found."
    return "An unexpected error occurred."
