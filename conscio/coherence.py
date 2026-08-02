# conscio/coherence.py
"""
Coherence Engine — recursive-coherence state metric.

Coherence is the parent archetype (Claude_Sentience, Dave Shapiro): the agent's
own internal representations are measured for incoherence. "Cognitive dissonance
is the detection of incoherence." This module is PURE — assess() reads
MetaCognition + WorldModel + a recent-events snapshot and returns a
CoherenceReport with no side effects; the caller owns any EventBus emission.

Four dimensions, each in [0, 1] (1 = coherent):
    epistemic    — meta.calibration_score()           (confidence vs accuracy)
    reality      — 1 - prediction_error_rate(24h)      (predictions vs observation)
    ontological  — 1 - contradicted/total entities     (knowledge-graph contradiction)
    temporal     — 1 - excess shard flapping           (cognitive-mode stability)

A dimension with no substrate to read scores its maximum, so the report also
carries `unmeasured` — the dimensions whose score is a default rather than an
observation. It changes no score; it says how much the score is worth.

Origin: Claude_Sentience by Dave Shapiro. Operational paraphrase; attribution in
docs/noosphere/coherence-engine-model.md.
"""
from __future__ import annotations

from dataclasses import dataclass

# --- Tunable constants (one-line knobs, like v0.5 LAYER_EPSILON) -------------

_WEIGHTS = {
    "epistemic": 0.30,    # direct: confidence vs accuracy
    "reality": 0.30,      # direct: prediction vs observation
    "ontological": 0.20,  # proxy: knowledge-graph contradiction
    "temporal": 0.20,     # proxy: cognitive-mode stability
}

DIM_DISSONANCE_THRESHOLD = 0.5     # a dimension below this is a dissonance
COHERENCE_EVENT_THRESHOLD = 0.5    # aggregate below this → caller emits an event

TEMPORAL_FREE_TRANSITIONS = 2      # natural mode alternation, no penalty
TEMPORAL_SPAN = 4                  # excess transitions that drive temporal 1.0 → 0.0

# Negation tokens — bilingual (EN + PT); Conscio runs multilingual.
_NEG_TOKENS = {
    "not", "no", "never", "non", "isn't", "aren't",
    "wasn't", "cannot", "can't", "n't", "without",
    "não", "nao", "nem", "nunca", "jamais", "sem", "nenhum", "nada",
}

_DETAIL = {
    "epistemic": "miscalibrated — confidence diverges from accuracy",
    "reality": "predictions diverging from observations",
    "ontological": "contradictory world-model assertions",
    "temporal": "cognitive mode flapping",
}


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


# --- Ontological contradiction helpers ---------------------------------------

def _strip_neg(predicate: str) -> tuple[str, bool]:
    """Return (core_tokens_joined, had_negation) for a relation predicate."""
    toks = predicate.lower().split()
    had = any(t in _NEG_TOKENS for t in toks)
    core = " ".join(t for t in toks if t not in _NEG_TOKENS)
    return core, had


def _relations_contradict(p1: str, p2: str) -> bool:
    """Contradiction iff same non-empty core and exactly one is negated."""
    c1, n1 = _strip_neg(p1)
    c2, n2 = _strip_neg(p2)
    return bool(c1) and c1 == c2 and (n1 != n2)


# --- Dataclasses -------------------------------------------------------------

@dataclass(frozen=True)
class Dissonance:
    dimension: str
    score: float
    severity: float
    detail: str


@dataclass(frozen=True)
class CoherenceReport:
    score: float
    dimensions: dict
    dissonances: list
    dominant: Dissonance | None
    unmeasured: tuple[str, ...] = ()

    def marker(self) -> str:
        """Heartbeat/state marker text.

        '0.82', '0.41 dominant: epistemic', or — on a mind with nothing to
        measure yet — '0.85 unmeasured: reality, ontological, temporal'.
        """
        base = f"{self.score:.2f}"
        if self.dominant is not None:
            base = f"{base} dominant: {self.dominant.dimension}"
        if self.unmeasured:
            base = f"{base} unmeasured: {', '.join(self.unmeasured)}"
        return base


# --- Dimension scorers (each → [0, 1]) ---------------------------------------

def epistemic_score(meta) -> float:
    """Confidence vs accuracy calibration. meta.calibration_score() is [0,1]."""
    try:
        return _clamp(meta.calibration_score())
    except Exception:
        return 0.5


def reality_score(world) -> float:
    """1 - recent prediction error rate (0.0 when no log → 1.0)."""
    try:
        return _clamp(1.0 - world.recent_prediction_error_rate(window_hours=24))
    except Exception:
        return 1.0


def ontological_score(world) -> float:
    """1 - contradicted/total entities, read from CACHED contradiction flags.

    v0.8: the synchronous lexical relation scan moved off the hot path into the
    dream Reconcile sub-phase (world.mark_contradictions). Here we only read
    public accessors — no private world._data access (the v0.6 tech debt is
    resolved). A cold world (never dreamed) has no flags → 1.0 (no false
    dissonance before the first reconcile; documented in
    docs/noosphere/semantic-reconciliation.md). The try/except stays defensive.
    """
    try:
        total = world.entity_count()
        contradicted = world.contradicted_entities()
    except Exception:
        return 1.0
    if total == 0:
        return 1.0
    return _clamp(1.0 - len(contradicted) / total)


def temporal_score(recent_events: list) -> float:
    """1 - excess shard flapping beyond the free-alternation tolerance."""
    transitions = 0
    for e in recent_events or []:
        data = e.get("data", {}) if isinstance(e, dict) else {}
        if isinstance(data, dict) and data.get("shard_transition") is True:
            transitions += 1
    excess = max(0, transitions - TEMPORAL_FREE_TRANSITIONS)
    return _clamp(1.0 - min(1.0, excess / TEMPORAL_SPAN))


# --- Evidence predicates (v3.9.4) --------------------------------------------
#
# Three of the four scorers return their MAXIMUM on an empty substrate: no
# prediction log → reality 1.0, no entities → ontological 1.0, no events →
# temporal 1.0. So a mind that has observed nothing reports 0.85 — the same
# number a well-measured, genuinely coherent mind reports. The scores stay
# exactly as they are (they feed thresholds, history and the dream trigger);
# assess() reports ALONGSIDE them which dimensions had nothing to measure, so a
# reader can tell "clean" from "untested". Unknown counts as unmeasured: if the
# accessor raises, we did not observe anything either.

def _has_epistemic_evidence(meta) -> bool:
    try:
        return bool(meta.has_calibration_evidence())
    except Exception:
        return False


def _has_reality_evidence(world) -> bool:
    try:
        return world.recent_prediction_outcomes(window_hours=24)[1] > 0
    except Exception:
        return False


def _has_ontological_evidence(world) -> bool:
    try:
        return world.entity_count() > 0
    except Exception:
        return False


def _has_temporal_evidence(recent_events: list) -> bool:
    """Events with zero transitions IS a measurement (stability); no events is not."""
    return bool(recent_events)


# --- Engine ------------------------------------------------------------------

class CoherenceEngine:
    """Pure snapshot metric over the agent's own state. No side effects."""

    def __init__(self, meta, world):
        self.meta = meta
        self.world = world

    def assess(self, recent_events: list | None = None) -> CoherenceReport:
        dims = {
            "epistemic": round(epistemic_score(self.meta), 3),
            "reality": round(reality_score(self.world), 3),
            "ontological": round(ontological_score(self.world), 3),
            "temporal": round(temporal_score(recent_events or []), 3),
        }
        score = round(sum(_WEIGHTS[d] * dims[d] for d in _WEIGHTS), 3)

        dissonances = [
            Dissonance(d, dims[d], round(1.0 - dims[d], 3), _DETAIL[d])
            for d in _WEIGHTS
            if dims[d] < DIM_DISSONANCE_THRESHOLD
        ]
        dissonances.sort(key=lambda x: x.score)  # worst first; stable for ties
        dominant = dissonances[0] if dissonances else None

        unmeasured = tuple(dim for dim, measured in (
            ("epistemic", _has_epistemic_evidence(self.meta)),
            ("reality", _has_reality_evidence(self.world)),
            ("ontological", _has_ontological_evidence(self.world)),
            ("temporal", _has_temporal_evidence(recent_events or [])),
        ) if not measured)

        return CoherenceReport(score, dims, dissonances, dominant, unmeasured)
