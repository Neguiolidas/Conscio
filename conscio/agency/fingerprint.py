# conscio/agency/fingerprint.py
"""goal_fingerprint — leaf module with no agency imports, so consumers
outside the act pipeline (e.g. the noosphere) can fingerprint a goal without
pulling adapters/gateway/skeptic/trust/breaker/ledger."""
from __future__ import annotations

import hashlib


def goal_fingerprint(goal_text: str) -> str:
    return hashlib.sha256(goal_text.encode("utf-8")).hexdigest()[:16]
