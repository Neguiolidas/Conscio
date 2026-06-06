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

Origin: Claude_Sentience by Dave Shapiro. Operational paraphrase; attribution in
docs/noosphere/coherence-engine-model.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


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
    dominant: Optional[Dissonance]

    def marker(self) -> str:
        """Heartbeat/state marker text: '0.82' or '0.41 dominant: epistemic'."""
        base = f"{self.score:.2f}"
        if self.dominant is not None:
            return f"{base} dominant: {self.dominant.dimension}"
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
    """1 - contradicted/total entities over the knowledge graph.

    TECH DEBT: reads `world._data` directly (private). WorldModel exposes no
    public read of the full relation list; `get_relations(entity)` is per-entity
    and would force an N-query scan. Tracked for a future public
    `WorldModel.list_relations()` — see docs/noosphere/coherence-engine-model.md.
    The try/except keeps this defensive if the internal shape changes.
    """
    try:
        data = world._data
        entities = data.get("entities", {})
        relations = data.get("relations", [])
    except Exception:
        return 1.0
    total = len(entities)
    if total == 0:
        return 1.0
    by_pair: dict = {}
    for r in relations:
        key = (r.get("from", ""), r.get("to", ""))
        by_pair.setdefault(key, []).append(r.get("relation", ""))
    contradicted = set()
    for (frm, _to), preds in by_pair.items():
        for i in range(len(preds)):
            for j in range(i + 1, len(preds)):
                if _relations_contradict(preds[i], preds[j]):
                    contradicted.add(frm)
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


# --- Engine ------------------------------------------------------------------

class CoherenceEngine:
    """Pure snapshot metric over the agent's own state. No side effects."""

    def __init__(self, meta, world):
        self.meta = meta
        self.world = world

    def assess(self, recent_events: Optional[list] = None) -> CoherenceReport:
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

        return CoherenceReport(score, dims, dissonances, dominant)
