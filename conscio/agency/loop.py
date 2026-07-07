# conscio/agency/loop.py
"""
GoalArbiter + AutonomyLoop (spec section 5.11).

The arbiter picks the cycle's goal deterministically (no LLM): the
GoalGenerator's priority order x alignment with the dominant dissonance
(P4) x out of quarantine. The loop is the L3 heartbeat —
reflect -> arbiter/act -> ledger -> (dream when recommended) — repeated
until the metabolic ActBudget is exhausted. The budget is a binding
execution gate (P3), not advisory.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from conscio.metabolic import MetabolicContext, MetabolicState

from .act import ActReport, ActStatus
from . import goal_fingerprint

# Lexical hints linking a coherence dimension to goal verbs (P4).
DISSONANCE_HINTS: dict[str, tuple[str, ...]] = {
    "epistemic": ("investigate", "verify", "learn", "understand",
                  "confidence"),
    "reality": ("check", "monitor", "observe", "perceive", "status"),
    "ontological": ("reconcile", "contradiction", "consistency", "conflict"),
    "temporal": ("stale", "prune", "refresh", "update", "expire"),
}
ALIGNMENT_BONUS = 2.0


class GoalArbiter:
    """Deterministic goal selection for one act() cycle."""

    def __init__(self, breaker: Any, executable_fn: Any = None):
        self.breaker = breaker
        # #7 provenance gate: a predicate (description -> bool). When wired by
        # the engine it denies diagnostic-origin goals (self_prompt, meta_error,
        # compaction) so they are never auto-executed. Default None -> no gate
        # (back-compat: the bare arbiter executes everything).
        self.executable_fn = executable_fn

    def _executable(self, description: str) -> bool:
        return self.executable_fn(description) if self.executable_fn else True

    def choose(self, state: Any) -> str | None:
        self.breaker.review_quarantine()
        goals = [g for g in state.active_goals
                 if not self.breaker.is_quarantined(goal_fingerprint(g))
                 and self._executable(g)]
        if not goals:
            return None
        hints = DISSONANCE_HINTS.get((state.coherence_note or "").lower(), ())

        def score(item: tuple[int, str]) -> float:
            index, goal = item
            base = float(len(goals) - index)     # generator priority order
            aligned = any(h in goal.lower() for h in hints)
            return base + (ALIGNMENT_BONUS if aligned else 0.0)

        return max(enumerate(goals), key=score)[1]


# Act outcomes that count as a failed cycle for the aggregate brake. REJECTED
# is included: a repeatedly skeptic-rejected proposal ("skeptic_fail") is the
# field flailing mode, even though no tool ran. LOCKED stops the loop already.
_FAILURE_STATUSES = frozenset({ActStatus.FAILED, ActStatus.REJECTED})


@dataclass
class ActBudget:
    """Binding metabolic budget for engine.run() (P3)."""
    max_cycles: int = 10
    max_llm_calls: int = 100
    max_tokens: int = 200_000
    max_wall_s: float = 600.0
    # v1.5.1 (#8): aggregate failure-rate brake. Once at least `min_attempts`
    # cycles have run, stop the heartbeat if the share of failed cycles reaches
    # `max_failure_rate`. Complements (does not replace) the per-goal breaker:
    # it catches broad flailing across many distinct goals/tools. Set
    # max_failure_rate >= 1.0 to disable.
    max_failure_rate: float = 0.5
    min_attempts: int = 4


@dataclass
class RunReport:
    cycles: int = 0
    reports: list[ActReport] = field(default_factory=list)
    llm_calls: int = 0
    tokens: int = 0
    wall_s: float = 0.0
    failures: int = 0
    stopped: str = ""


class AutonomyLoop:
    """engine.run(budget) heartbeat: reflect -> arbiter/act -> dream.

    MetabolicContext stops being advisory here: FATIGUE halves the cycle
    budget, CRITICAL forces the cap down to L1 PROPOSE for the cycle.
    LLM calls and tokens are measured as deltas on the shared Meter, so
    run() budgets only its own consumption.
    """

    def __init__(self, engine: Any, pipeline: Any, meter: Any):
        self.engine = engine
        self.pipeline = pipeline
        self.meter = meter

    def run(self, budget: ActBudget, *, world_state: str = "") -> RunReport:
        report = RunReport()
        start = time.monotonic()
        calls0, tokens0 = self.meter.calls, self.meter.tokens
        max_cycles = budget.max_cycles
        cap0 = self.pipeline.autonomy_cap
        try:
            while True:
                report.wall_s = time.monotonic() - start
                report.llm_calls = self.meter.calls - calls0
                report.tokens = self.meter.tokens - tokens0
                stopped = self._budget_stop(report, budget, max_cycles)
                if stopped:
                    report.stopped = stopped
                    if stopped == "failure_rate":
                        self._emit_failure_brake(report)
                    break
                self.engine.reflect(world_state=world_state)
                state = self.engine.state
                tier = MetabolicContext.assess(
                    self.engine.session_tokens_used
                    if self.engine.session_tokens_used is not None
                    else state.total_tokens_approx(),
                    self.engine.model_info.context_window)
                if tier is MetabolicState.FATIGUE:
                    max_cycles = min(max_cycles,
                                     max(1, budget.max_cycles // 2))
                self.pipeline.autonomy_cap = (
                    1 if tier is MetabolicState.CRITICAL else cap0)
                act_report = self.engine.act()
                report.reports.append(act_report)
                report.cycles += 1
                if act_report.status in _FAILURE_STATUSES:
                    report.failures += 1
                if (act_report.lockdown or state.action_lockdown
                        or act_report.status is ActStatus.LOCKED):
                    report.stopped = "lockdown"
                    break
                if self.engine.dream_recommended.recommended:
                    self.engine.dream()
        finally:
            self.pipeline.autonomy_cap = cap0
        report.wall_s = time.monotonic() - start
        report.llm_calls = self.meter.calls - calls0
        report.tokens = self.meter.tokens - tokens0
        return report

    @staticmethod
    def _budget_stop(report: RunReport, budget: ActBudget,
                     max_cycles: int) -> str:
        # Aggregate failure-rate brake (#8) — checked first so broad flailing is
        # reported as such, not as a generic budget exhaustion.
        if (budget.max_failure_rate < 1.0
                and report.cycles >= budget.min_attempts
                and report.cycles > 0
                and report.failures / report.cycles >= budget.max_failure_rate):
            return "failure_rate"
        if report.cycles >= max_cycles:
            return "max_cycles"
        if report.llm_calls >= budget.max_llm_calls:
            return "max_llm_calls"
        if report.tokens >= budget.max_tokens:
            return "max_tokens"
        if report.wall_s >= budget.max_wall_s:
            return "max_wall_s"
        return ""

    def _emit_failure_brake(self, report: RunReport) -> None:
        """Surface the failure-rate trip so an operator/host sees why the
        awake loop stopped (visible in the event log and heartbeat)."""
        bus = getattr(self.engine, "event_bus", None)
        if bus is None:
            return
        rate = report.failures / report.cycles if report.cycles else 0.0
        try:
            bus.emit(
                type="system", category="system",
                data={"message": "failure-rate brake: autonomous loop stopped",
                      "failures": report.failures,
                      "cycles": report.cycles,
                      "failure_rate": round(rate, 3)},
                priority=8,
            )
        except Exception:                       # a strict bus must not crash run()
            pass
