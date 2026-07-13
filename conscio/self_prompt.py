# conscio/self_prompt.py
"""
Self-Prompting — internal hypothesis generation (v0.7).

The agent introspects its own state (coherence dissonances, blind spots, stale
entities) and produces ranked SelfPrompts — questions it would ask itself. PURE:
no side effects, no network, deterministic for a given state. The caller
(engine.reflect) surfaces the prompts and may spawn ONE bounded goal per cycle.

Theory: Claude_Sentience (Dave Shapiro) — "consciousness emerges when coherence
examines itself." Coherence examination becomes self-directed questions here.
"""
from __future__ import annotations

from dataclasses import dataclass

# source_signal → GoalGenerator drive value (strings match Drive.value)
_SIGNAL_DRIVE = {
    "epistemic":   "evolution",    # miscalibration → self-improve
    "reality":     "curiosity",    # predictions vs observation → investigate
    "ontological": "curiosity",    # contradictions → investigate
    "temporal":    "maintenance",  # mode flapping → stabilize
    "blind_spot":  "evolution",
    "stale":       "maintenance",
}

# Question templates per coherence dimension.
_QUESTION = {
    "epistemic":   "why is my confidence diverging from my accuracy?",
    "reality":     "why are my predictions diverging from observations?",
    "ontological": "why do I hold contradictory world-model assertions?",
    "temporal":    "why is my cognitive mode flapping?",
}

_BLIND_SPOT_SEVERITY = 0.5
_STALE_SEVERITY = 0.5

# Deterministic tiebreak when severities are equal (lower = earlier).
_SIGNAL_ORDER = {
    "ontological": 0, "reality": 1, "epistemic": 2, "temporal": 3,
    "blind_spot": 4, "stale": 5,
}


@dataclass(frozen=True)
class SelfPrompt:
    question: str
    drive: str          # "curiosity" | "maintenance" | "evolution"
    target: str         # dimension name / entity / blind-spot label
    source_signal: str  # key into _SIGNAL_DRIVE
    severity: float     # 0..1, higher = more urgent

    def marker(self) -> str:
        return self.question


def generate_self_prompts(meta, world, coherence_report, recent_events=None) -> list["SelfPrompt"]:
    """
    Introspect internal state → SelfPrompts ranked by severity (desc).

    PURE / deterministic. Sources:
      - coherence_report.dissonances  → one prompt per dimension below threshold
      - meta.blind_spots()            → evolution prompts (top 2)
      - world.stale_entities()        → maintenance prompts (top 2)

    `recent_events` is accepted for signature symmetry; v0.7 derives temporal from
    coherence_report (which already consumed the event window).
    """
    prompts: list[SelfPrompt] = []

    # 1. Coherence dissonances — the primary self-examination signal.
    for d in getattr(coherence_report, "dissonances", []) or []:
        dim = d.dimension
        prompts.append(SelfPrompt(
            question=_QUESTION.get(dim, f"why is my {dim} coherence low?"),
            drive=_SIGNAL_DRIVE.get(dim, "curiosity"),
            target=dim,
            source_signal=dim,
            severity=float(getattr(d, "severity", 1.0 - getattr(d, "score", 0.5))),
        ))

    # 2. Blind spots (meta-cognition, public accessor).
    try:
        blind = meta.blind_spots()
    except Exception:
        blind = []
    for spot in blind[:2]:
        prompts.append(SelfPrompt(
            question=f"how do I shore up my blind spot in {spot}?",
            drive=_SIGNAL_DRIVE["blind_spot"],
            target=str(spot),
            source_signal="blind_spot",
            severity=_BLIND_SPOT_SEVERITY,
        ))

    # 3. Stale entities (world-model maintenance).
    try:
        stale = list(world.stale_entities())
    except Exception:
        stale = []
    for name in stale[:2]:
        prompts.append(SelfPrompt(
            question=f"is {name} still relevant, or should it be pruned?",
            drive=_SIGNAL_DRIVE["stale"],
            target=str(name),
            source_signal="stale",
            severity=_STALE_SEVERITY,
        ))

    prompts.sort(key=lambda p: (-p.severity, _SIGNAL_ORDER.get(p.source_signal, 99)))
    return prompts
