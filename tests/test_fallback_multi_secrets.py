"""Tests verifying secret sanitization union across fallback_multi and output_filter (Slice 2)."""
from __future__ import annotations

import pytest

from conscio.agency.fallback_multi import _sanitize_exc
from conscio.output_filter import SecretMask


# ---------------------------------------------------------------------------
# T1: _sanitize_exc redacts all occurrences (not just the 1st)
# ---------------------------------------------------------------------------
def test_sanitize_exc_redacts_multiple_occurrences() -> None:
    exc = Exception(
        "failed with sk-first-12345678901234567890 and sk-second-12345678901234567890"
    )
    sanitized = _sanitize_exc(exc)
    assert "sk-first-12345678901234567890" not in sanitized
    assert "sk-second-12345678901234567890" not in sanitized
    assert "[REDACTED]" in sanitized


# ---------------------------------------------------------------------------
# T2: _sanitize_exc does not leak value after colon in Authorization / api-key
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw_message", "forbidden_secret"),
    [
        ("HTTP 401 Authorization: secret-raw-token-xyz rejected", "secret-raw-token-xyz"),
        ("HTTP 401 Authorization: Bearer secret-bearer-abc rejected", "secret-bearer-abc"),
        ("HTTP 401 api-key: secret-api-key-123 rejected", "secret-api-key-123"),
        ("HTTP 401 api_key=secret-api-key-456 rejected", "secret-api-key-456"),
    ],
)
def test_sanitize_exc_header_credentials_redacted(
    raw_message: str, forbidden_secret: str
) -> None:
    exc = Exception(raw_message)
    sanitized = _sanitize_exc(exc)
    assert forbidden_secret not in sanitized
    assert "[REDACTED]" in sanitized


# ---------------------------------------------------------------------------
# T3: _sanitize_exc catches ghp_, AKIA, xox, and JWT
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw_message", "forbidden_secret"),
    [
        (
            "GitHub error with ghp_123456789012345678901234567890123456 token",
            "ghp_123456789012345678901234567890123456",
        ),
        (
            "AWS failure using key AKIAIOSFODNN7EXAMPLE in request",
            "AKIAIOSFODNN7EXAMPLE",
        ),
        (
            "Slack webhook xoxb-1234567890-1234567890-abcdef1234 failed",
            "xoxb-1234567890-1234567890-abcdef1234",
        ),
        (
            "JWT auth failed for eyJhbGciOi.eyJzdWIiOi.secret1234 token",
            "eyJhbGciOi.eyJzdWIiOi.secret1234",
        ),
    ],
)
def test_sanitize_exc_catches_standard_secret_patterns(
    raw_message: str, forbidden_secret: str
) -> None:
    exc = Exception(raw_message)
    sanitized = _sanitize_exc(exc)
    assert forbidden_secret not in sanitized
    assert "[REDACTED]" in sanitized


# ---------------------------------------------------------------------------
# T4: Short tokens, Anthropic sk-ant-, nvapi-, and standalone Bearer
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw_message", "forbidden_secret"),
    [
        ("HTTP 401 Bearer x failed", "x"),
        ("Failed with key sk-ant-api03-secret123 rejected", "sk-ant-api03-secret123"),
        ("Failed with key nvapi-short123 rejected", "nvapi-short123"),
        ("Failed with key ng-short456 rejected", "ng-short456"),
    ],
)
def test_sanitize_exc_catches_short_and_prefixed_tokens(
    raw_message: str, forbidden_secret: str
) -> None:
    exc = Exception(raw_message)
    sanitized = _sanitize_exc(exc)
    assert forbidden_secret not in sanitized
    assert "[REDACTED]" in sanitized


# ---------------------------------------------------------------------------
# T5: SecretMask in output_filter preserves legitimate code and prose
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "legitimate_text",
    [
        '<ng-container *ngIf="x"></ng-container>',
        "He was the bearer of bad news.",
        "auth: required for this route",
        "pip install sk-video",
        "ghp_x is a placeholder",
    ],
)
def test_secret_mask_preserves_legitimate_code_and_prose(legitimate_text: str) -> None:
    mask = SecretMask()
    assert mask.apply(legitimate_text) == legitimate_text


# ---------------------------------------------------------------------------
# T6: Legitimate case: normal error messages pass untouched character-by-character
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "clean_message",
    [
        "The read operation timed out",
        "Connection refused by 127.0.0.1:11434",
        "HTTP 500: Internal Server Error",
        "Model llama3:8b not found on host",
    ],
)
def test_sanitize_exc_preserves_clean_messages_exactly(clean_message: str) -> None:
    exc = Exception(clean_message)
    assert _sanitize_exc(exc) == clean_message
    assert SecretMask().apply(clean_message) == clean_message
