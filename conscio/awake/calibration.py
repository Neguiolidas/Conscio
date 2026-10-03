"""Batch H (v4.8.1) — calibration helpers for Awake Mode LLM volume.

Retworked after the hostile review (Claude fbb0ceed, 2026-10-02): the first
cut read a hand-copied ledger key that never matched what act.py records,
counted maintenance cycles by watching goal status that act() does not
change, and froze its rolling window at cache time. This module now has
one source of truth for the ledger key — ``maintenance_goal_fingerprint``
derives it from the same constant the engine regenerates — and the window
is recomputed on every check.

The three levers (measured burn: 267 costed actions/day on the muse
ledger, 2026-10-01):

1. ``MaintenanceCooldown`` — reflect() does not regenerate the
   ``daemon_check`` goal while the last ATTEMPT of that exact ledger key
   (executed, failed or rejected — each burned provider quota) is inside
   the window.

2. Run-scoped maintenance cap (``AutonomyLoop``) — at most one
   ``daemon_check`` cycle per run, counted by ledger rows of the goal
   fingerprint with id greater than the run's baseline. Failures count;
   a failing maintenance loop never earns a second proposal in one run.

3. ``DailyAttemptCeiling`` — a rolling 24h budget of awake-loop ACT
   ATTEMPTS (every non-host ledger row, whatever its outcome or token
   count; see the class docstring). The window moves: every check recomputes
   ``now - 24h``, so a cached ceiling ages old rows out instead of
   freezing (the daemon restarts every 6h on the muse, but a long-lived
   daemon must not degrade permanently). When the ceiling trips, run()
   degrades to perceive+reflect and REPORTS the trip.
"""
from __future__ import annotations

import time
from typing import Any

_MAINTENANCE_CHECK = "daemon_check"


def maintenance_goal_fingerprint() -> str:
    """Ledger key of the daemon_check maintenance goal.

    Single source of truth: the description constant the engine passes to
    ``GoalGenerator.generate_from_maintenance``, fingerprinted exactly the
    way act.py fingerprints the goal it runs
    (``goal_fingerprint(goal.description)``, where the description is
    ``"Maintenance: " + target``). Deriving — not hand-copying — is what
    keeps this in step with the engine text (hostile review, lever 1).
    """
    from ..agency.fingerprint import goal_fingerprint
    from ..goal_generator import Goal

    return goal_fingerprint(Goal.MAINTENANCE_DAEMON_CHECK_DESCRIPTION)


class MaintenanceCooldown:
    """Gate for regenerating the daemon_check maintenance goal.

    ``should_generate`` consults the ledger through the PUBLIC
    ``last_attempt_ts`` (any status: executed, failed, rejected — a failed
    attempt burned provider quota exactly like a successful one, and
    re-proposing into a rate limit every 15 min was the measured waste).
    ``None`` (never attempted) always allows — the very first check must
    happen.
    """

    def __init__(self, minutes: int = 60, *,
                 now_fn: Any = time.time) -> None:
        self.window_s = max(0, int(minutes)) * 60
        self._now_fn = now_fn

    def should_generate(self, *, last_attempt_ts: float | None) -> bool:
        if last_attempt_ts is None:
            return True
        return (float(self._now_fn()) - float(last_attempt_ts)) >= self.window_s


class DailyAttemptCeiling:
    """Rolling 24h budget of ACT ATTEMPTS for the awake loop.

    v4.8.1 (batch H round 5, #866): counts ATTEMPTS (every actions row in
    the window), not cost-carrying rows — a failed attempt paid for its
    LLM requests exactly like a successful one, and a 429 storm burns
    RPM while writing tokens=0 rows. The first cut (DailyCostCeiling,
    filtering on tokens>0) was blind to the failure storm it existed to
    cap.

    The count is a ledger query (``count_attempts_since``), so the budget
    survives any process restart: the ledger IS the state. The window is
    recomputed on every ``allows_more``/``report`` call — ``now`` is
    evaluated per check (injectable only for tests), never captured at
    construction, so a cached ceiling ages old rows out of the window
    instead of tripping forever (hostile review, lever 3).
    """

    def __init__(self, *, max_attempts_per_day: int,
                 ledger: Any,
                 now_fn: Any = time.time,
                 window_s: float = 24 * 3600.0) -> None:
        self.max_attempts = int(max_attempts_per_day)
        self._ledger = ledger
        self._now_fn = now_fn
        self.window_s = float(window_s)

    def _spent(self) -> int:
        now = float(self._now_fn())
        return self._ledger.count_attempts_since(now - self.window_s, now=now)

    def allows_more(self) -> bool:
        return self._spent() < self.max_attempts

    def report(self) -> str:
        spent = self._spent()
        if spent < self.max_attempts:
            return ""
        return (f"daily attempt ceiling reached: {spent} act attempts"
                f" in the last 24h (ceiling {self.max_attempts});"
                " awake loop skipping act(), perceive+reflect continue")
