# conscio/agency/breaker.py
"""
CircuitBreaker — the paralysis instinct, F1 minimal version
(spec section 5.8 / blueprint section 5).

Consecutive failures are derived from the ActionLedger (single source of
truth — no second table). The threshold is a fixed constant in F1; the
TrustMatrix (F2) replaces it with a dynamic value computed from
MetaCognition. The pipeline owns the lockdown flag mutation; the breaker
only detects and announces.
"""
from __future__ import annotations

from typing import Any

from .ledger import ActionLedger

DEFAULT_MAX_RETRIES = 3  # F2: TrustMatrix.max_action_retries() replaces this


class CircuitBreaker:
    def __init__(self, ledger: ActionLedger, event_bus: Any,
                 max_retries: int = DEFAULT_MAX_RETRIES):
        self.ledger = ledger
        self.event_bus = event_bus
        self.max_retries = max_retries

    def should_trip(self, goal_fp: str) -> bool:
        return self.ledger.consecutive_failures(goal_fp) >= self.max_retries

    def trip(self, goal_fp: str, *, detail: str = "") -> None:
        """Announce intentional collapse on the native EventBus.

        emit() takes a dict payload (serialized with json.dumps); the
        blueprint's message text lives under the "message" key.
        """
        self.event_bus.emit(
            type="error", category="system",
            data={"message": (f"Intractable dissonance: action thread "
                              f"'{goal_fp}' collapsed after "
                              f"{self.max_retries} consecutive failures. "
                              f"{detail}").strip(),
                  "goal_fp": goal_fp,
                  "failures": self.max_retries})
