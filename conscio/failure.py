"""
FailureClass enum + FailureGovernor — Conscio v3.1 Harness Efficiency Layer.

Mechanism 5 (failure-spend governance) from The Harness Effect paper.
"""
from __future__ import annotations

import enum
from typing import Dict

from conscio.agency.adapter import AdapterTimeout, AdapterConnectionError, AdapterBadResponse


class FailureClass(enum.Enum):
    """Taxonomy of failure modes for adapter / inference errors."""

    RATE_LIMIT = "rate_limit"        # HTTP 429, rate limit exceeded
    STALL = "stall"  # model produced no useful output, empty response
    TIMEOUT = "timeout"               # connection or generation timed out
    MALFORMED_STREAM = "malformed_stream"  # truncated or corrupted response
    PROVIDER_OUTAGE = "provider_outage"    # 5xx server error, service unavailable
    PERMANENT = "permanent"           # unrecoverable error (auth, invalid request, content filter)


class FailureGovernor:
    """Per-tool circuit breaker that classifies failures and governs retries."""

    def __init__(self, max_consecutive: int = 3) -> None:
        self._failures: Dict[str, int] = {}
        self._permanent: Dict[str, bool] = {}
        self._max_consecutive = max_consecutive

    # ── classification ────────────────────────────────────────────────

    @staticmethod
    def classify(exc: Exception) -> FailureClass:
        """Inspect exception type and message to classify the failure."""
        # Adapter-typed exceptions first (most specific)
        if isinstance(exc, AdapterTimeout):
            return FailureClass.TIMEOUT
        if isinstance(exc, AdapterConnectionError):
            return FailureClass.PROVIDER_OUTAGE
        if isinstance(exc, AdapterBadResponse):
            return FailureClass.MALFORMED_STREAM

        # Generic Exception — inspect message
        msg = str(exc).lower()

        if "429" in msg or "rate limit" in msg:
            return FailureClass.RATE_LIMIT
        if "empty" in msg or "no content" in msg:
            return FailureClass.STALL
        if "auth" in msg or "unauthorized" in msg or "forbidden" in msg:
            return FailureClass.PERMANENT
        if "5xx" in msg or "503" in msg or "502" in msg or "unavailable" in msg:
            return FailureClass.PROVIDER_OUTAGE

        # Default — safest assumption
        return FailureClass.PERMANENT

    # ── retry decision ─────────────────────────────────────────────────

    @staticmethod
    def should_retry(cls: FailureClass) -> bool:
        """Whether the failure class warrants a retry."""
        return cls is not FailureClass.PERMANENT

    # ── circuit breaker ────────────────────────────────────────────────

    def record(self, cls: FailureClass, tool: str) -> None:
        """Record a failure for *tool*, incrementing the consecutive counter."""
        if cls is FailureClass.PERMANENT:
            self._permanent[tool] = True
            return

        self._failures[tool] = self._failures.get(tool, 0) + 1

    def is_open(self, tool: str) -> bool:
        """Breaker is open when consecutive ≥ max OR any PERMANENT recorded."""
        if self._permanent.get(tool, False):
            return True
        return self._failures.get(tool, 0) >= self._max_consecutive

    def reset(self, tool: str) -> None:
        """Clear failure state for *tool*."""
        self._failures.pop(tool, None)
        self._permanent.pop(tool, None)
