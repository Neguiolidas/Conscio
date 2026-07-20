"""conscio.evaluation — 5-axis self-evaluation rubric (v2.15).

Inspired by ECC's `agent-self-evaluation` skill. Produces a structured 1-5
scorecard with concrete evidence per axis, derived from the engine's own
deterministic state (no LLM, no I/O).

The 5 axes:
    1. Accuracy       — are the facts/claims/output correct?
    2. Completeness   — did it cover what was asked?
    3. Clarity        — is the explanation understandable and well-structured?
    4. Actionability  — can the user act on the output immediately?
    5. Conciseness    — did it use the minimum tokens needed?

Scores 1-5. Every score below 5 MUST cite specific evidence.
This is a read-only diagnostic — never modifies state, never emits events.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .engine import ConsciousnessEngine


# ─── Scoring scale (matches ECC agent-self-evaluation) ────────────────────────

SCALE = {
    5: "exceptional",
    4: "good",
    3: "adequate",
    2: "weak",
    1: "poor",
}


@dataclass(frozen=True)
class AxisScore:
    """One axis of the 5-axis rubric."""
    axis: str
    score: int            # 1-5
    evidence: str         # concrete justification, required if score < 5
    improvement: str = "" # one-sentence fix, required if score < 5

    def to_dict(self) -> dict:
        return {
            "axis": self.axis,
            "score": self.score,
            "label": SCALE.get(self.score, "unknown"),
            "evidence": self.evidence,
            "improvement": self.improvement,
        }


@dataclass(frozen=True)
class EvaluationReport:
    """Full 5-axis scorecard + overall + improvements."""
    task_description: str
    axes: tuple  # tuple[AxisScore, ...] length 5
    overall: float   # simple average rounded to 1 decimal
    improvements: tuple = field(default_factory=tuple)  # ranked 1-3
    self_check: str = ""  # "Would the user agree with this assessment?"

    def to_dict(self) -> dict:
        return {
            "task_description": self.task_description,
            "axes": [a.to_dict() for a in self.axes],
            "overall": self.overall,
            "improvements": list(self.improvements),
            "self_check": self.self_check,
        }

    def to_injection(self, max_chars: int = 800) -> str:
        """Compact form for context injection."""
        lines = [f"[evaluate] overall={self.overall:.1f} task={self.task_description}"]
        for a in self.axes:
            tag = f"{a.axis}={a.score}"
            if a.score < 5:
                tag += f" ({SCALE.get(a.score, '?')})"
            lines.append(f"  {tag}: {a.evidence}")
        if self.improvements:
            lines.append("  top improvement: " + self.improvements[0])
        text = "\n".join(lines)
        return text[:max_chars]


# ─── Public entry point ────────────────────────────────────────────────────────

def evaluate(
    engine: "ConsciousnessEngine",
    task_description: str = "",
    output: Optional[str] = None,
) -> EvaluationReport:
    """Produce a 5-axis self-evaluation scorecard from the engine state.

    Parameters:
        engine           — a ConsciousnessEngine instance (read-only access)
        task_description — what the agent was trying to do (free text)
        output           — optional: the output text being evaluated (used
                           for conciseness and clarity heuristics)

    Returns:
        EvaluationReport with 5 AxisScores + overall + improvements.

    The function is pure read — it never calls reflect(), never emits events,
    never writes to DB. It inspects:
        - meta.average_confidence()          → Accuracy
        - event_bus recent error count       → Accuracy / Completeness
        - goals.active_goals()               → Completeness
        - world.stale_entities()             → Completeness
        - coherence.score                     → Clarity
        - contradiction_count                 → Clarity
        - output length / repetition          → Conciseness
        - pending_proposals / pending actions → Actionability
        - frequent_errors                     → Actionability
    """
    task = task_description or "(unnamed task)"

    accuracy = _score_accuracy(engine)
    completeness = _score_completeness(engine)
    clarity = _score_clarity(engine)
    actionability = _score_actionability(engine)
    conciseness = _score_conciseness(engine, output)

    axes = (accuracy, completeness, clarity, actionability, conciseness)
    overall = round(sum(a.score for a in axes) / 5.0, 1)

    # Collect improvements ranked by gap (5 - score), biggest first.
    gaps = [(5 - a.score, a) for a in axes if a.score < 5]
    gaps.sort(key=lambda t: t[0], reverse=True)
    improvements = tuple(a.improvement for _, a in gaps[:3] if a.improvement)

    # Self-check: honest verdict on whether the user would agree.
    weak = [a for a in axes if a.score <= 2]
    if weak:
        self_check = f"User would likely flag {weak[0].axis} as insufficient."
    elif overall >= 4.5:
        self_check = "User would likely accept this output."
    else:
        self_check = "User might ask for follow-up on weaker axes."

    return EvaluationReport(
        task_description=task,
        axes=axes,
        overall=overall,
        improvements=improvements,
        self_check=self_check,
    )


# ─── Heuristics (deterministic, no LLM) ──────────────────────────────────────

def _score_accuracy(engine: "ConsciousnessEngine") -> AxisScore:
    """Accuracy = confidence × (1 - error_rate)."""
    try:
        conf = float(engine.meta.average_confidence())
    except Exception:
        conf = 0.5
    conf = max(0.0, min(1.0, conf))

    # Recent errors in EventBus (last 20 events).
    try:
        recent = engine.event_bus.query(limit=20)
    except Exception:
        recent = []
    errors = [e for e in recent if getattr(e, "type", "") == "error"]
    total = max(len(recent), 1)
    error_rate = len(errors) / total

    # Frequent error patterns (meta_cognition.frequent_errors).
    try:
        frequent = engine.meta.frequent_errors(min_count=2)
    except Exception:
        frequent = []
    repeated = len(frequent)

    # Combined: confidence scaled down by error rate.
    effective = conf * (1.0 - min(error_rate * 0.5, 0.5))
    # Frequent repeated errors dock accuracy further.
    if repeated >= 2:
        effective -= 0.1

    score = _band(effective)
    evidence_parts = [f"avg_confidence={conf:.2f}"]
    if errors:
        evidence_parts.append(f"{len(errors)} recent errors")
    if repeated:
        evidence_parts.append(f"{repeated} repeated error pattern(s)")
    if not errors and not repeated:
        evidence_parts.append("no recent errors in event log")
    evidence = "; ".join(evidence_parts)

    improvement = ""
    if score < 5:
        if errors:
            improvement = "Investigate and fix the recurring error pattern before next release."
        elif conf < 0.7:
            improvement = "Raise confidence by adding verification steps for claims."
        else:
            improvement = "Add test coverage to lock in the current accuracy baseline."

    return AxisScore("accuracy", score, evidence, improvement)


def _score_completeness(engine: "ConsciousnessEngine") -> AxisScore:
    """Completeness = low stale backlog + goals are active and bounded."""
    try:
        stale = engine.world.stale_entities()
        stale_n = len(stale)
    except Exception:
        stale_n = 0
    try:
        active = engine.goals.active_goals()
        active_n = len(active)
    except Exception:
        active_n = 0

    # Build evidence: describe the actual bottleneck, not a generic phrase.
    parts = []
    if stale_n > 0:
        parts.append(f"{stale_n} stale entit(ies)")
    else:
        parts.append("no stale entities")
    parts.append(f"{active_n} active goal(s)")
    evidence = "; ".join(parts)

    # Score logic:
    # - 0 stale + active goals = 5 (fully on top of things)
    # - 0 stale + 0 active = 3 (no backlog but also no commitment — neutral)
    # - small stale backlog = 3-4
    # - large stale backlog = 1-2
    if stale_n == 0 and active_n > 0:
        score = 5
    elif stale_n == 0 and active_n == 0:
        score = 3
    elif stale_n <= 3 and active_n > 0:
        score = 4
    elif stale_n <= 3:
        score = 3
    elif stale_n <= 10:
        score = 3
    elif stale_n <= 25:
        score = 2
    else:
        score = 1

    improvement = ""
    if score < 5:
        if stale_n > 0:
            improvement = f"Prune {stale_n} stale entities via engine.world.prune_stale() or prune_by_entropy()."
        elif active_n == 0:
            improvement = "Generate goals from drives to signal active commitment."

    return AxisScore("completeness", score, evidence, improvement)


def _score_clarity(engine: "ConsciousnessEngine") -> AxisScore:
    """Clarity = coherence score minus contradiction penalty."""
    try:
        coherence = engine.last_coherence
        score_val = float(coherence.score) if coherence else 0.5
    except Exception:
        score_val = 0.5
    score_val = max(0.0, min(1.0, score_val))

    # Count contradictions: entities with same name but different states.
    # NOTE: world_model.add_entity() overwrites on re-add same name, so
    # contradictions must be detected from state_log (history) not duplicates.
    contradiction_n = 0
    try:
        entities = engine.world.list_entities(limit=20)
        for e in entities:
            state_log = e.get("state_log", [])
            # If entity has had multiple distinct states, that's a contradiction signal
            if len(state_log) >= 2:
                states = {entry.get("state", "") for entry in state_log if entry.get("state")}
                if len(states) >= 2:
                    contradiction_n += 1
        # Also use engine's own contradiction detector on STATE pairs (not name pairs)
        # — but since add_entity overwrites, we rely on state_log above.
    except Exception:
        pass

    # Penalty: each contradiction docks clarity by 0.1 (capped at 0.3).
    effective = score_val - min(contradiction_n * 0.1, 0.3)
    score = _band(effective)

    evidence_parts = [f"coherence={score_val:.2f}"]
    if contradiction_n:
        evidence_parts.append(f"{contradiction_n} contradiction(s) in world model")
    else:
        evidence_parts.append("no contradictions detected")
    evidence = "; ".join(evidence_parts)

    improvement = ""
    if score < 5:
        if contradiction_n:
            improvement = f"Reconcile {contradiction_n} contradiction(s) using engine.dream() reconcile phase."
        elif score_val < 0.7:
            improvement = "Raise coherence by aligning goals and reflections (run cognitive_cycle)."
        else:
            improvement = "Tighten reasoning to push coherence above 0.9."

    return AxisScore("clarity", score, evidence, improvement)


def _score_actionability(engine: "ConsciousnessEngine") -> AxisScore:
    """Actionability = can the user act immediately?
    Factors: pending proposals (block action), frequent errors (block path)."""
    try:
        pending = engine.evolution.pending_proposals()
        pending_n = len(pending)
    except Exception:
        pending_n = 0

    try:
        frequent = engine.meta.frequent_errors(min_count=2)
        blocked_paths = len(frequent)
    except Exception:
        blocked_paths = 0

    # Health check tells us mode and pending action ledger entries.
    try:
        hc = engine.health_check()
        mode = hc.get("mode", "standard")
    except Exception:
        mode = "standard"

    # Metabolic tier: CRITICAL means agent can't act productively.
    state = getattr(engine, "_state", None)
    metabolic = getattr(state, "metabolic", "") if state else ""
    if not metabolic:
        metabolic = "VITAL"  # default assumption

    # Score formula
    base = 5
    if pending_n > 0:
        base -= 1  # proposals awaiting approval block action
    if blocked_paths > 0:
        base -= 1  # known broken paths
    if metabolic == "CRITICAL":
        base -= 1
    elif metabolic == "FATIGUE":
        base -= 0  # fatigue is tolerable
    score = max(1, min(5, int(base)))

    evidence_parts = [f"metabolic={metabolic}", f"pending_proposals={pending_n}"]
    if blocked_paths:
        evidence_parts.append(f"{blocked_paths} blocked path(s)")
    if mode:
        evidence_parts.append(f"mode={mode}")
    evidence = "; ".join(evidence_parts)

    improvement = ""
    if score < 5:
        if pending_n:
            improvement = "Approve or reject pending evolution proposals to unblock action."
        elif blocked_paths:
            improvement = "Fix recurring error patterns before retrying the affected workflow."
        elif metabolic == "CRITICAL":
            improvement = "Reduce context pressure — compact or shrink active scope before acting."
        else:
            improvement = "Verify the next action has a clear acceptance criterion."

    return AxisScore("actionability", score, evidence, improvement)


def _score_conciseness(engine: "ConsciousnessEngine", output: Optional[str]) -> AxisScore:
    """Conciseness = output is short and not repetitious."""
    if not output:
        # Without output text, proxy from token tracker savings if available.
        try:
            gain = engine.token_tracker.gain()
            savings_pct = float(gain.get("savings_pct", 0))
            if savings_pct >= 50:
                return AxisScore("conciseness", 5, f"token savings={savings_pct:.0f}%")
            elif savings_pct >= 25:
                return AxisScore("conciseness", 4, f"token savings={savings_pct:.0f}%", "Apply OutputFilter to compress further.")
            elif savings_pct > 0:
                return AxisScore("conciseness", 3, f"token savings={savings_pct:.0f}%", "Enable OutputFilter or tighter stages.")
            else:
                return AxisScore("conciseness", 3, "no token savings measured; output not provided")
        except Exception:
            return AxisScore("conciseness", 3, "no output provided; no token-tracker data")

    # Analyze the output text itself.
    text = output
    n_words = len(text.split())

    # repetition ratio: how many words are duplicates
    words = text.lower().split()
    if words:
        unique = len(set(words))
        repetition_ratio = 1.0 - (unique / len(words))
    else:
        repetition_ratio = 0.0

    # We expect a useful answer to be 50-1000 words.
    # Too short is unusable; too long with repetition is bloated;
    # too long without repetition may be justified (e.g. code, detailed docs).
    if n_words < 10:
        score = 2
        evidence = f"{n_words} words — too short to be useful"
        improvement = "Expand the answer to address the task directly."
    elif n_words <= 500 and repetition_ratio < 0.3:
        score = 5
        evidence = f"{n_words} words, repetition={repetition_ratio:.0%}"
        improvement = ""
    elif n_words <= 1000 and repetition_ratio < 0.4:
        score = 4
        evidence = f"{n_words} words, repetition={repetition_ratio:.0%}"
        if repetition_ratio >= 0.3:
            improvement = "Remove repeated phrasing to tighten the answer."
        elif n_words > 500:
            improvement = "Trim non-essential sentences to under 500 words."
        else:
            improvement = ""
    elif n_words <= 2000 and repetition_ratio < 0.5:
        score = 3
        evidence = f"{n_words} words, repetition={repetition_ratio:.0%}"
        improvement = "Compress to ~500 words; remove duplicated content."
    elif repetition_ratio >= 0.5:
        # High repetition is the real problem regardless of length
        score = 2
        evidence = f"{n_words} words, repetition={repetition_ratio:.0%} — high redundancy"
        improvement = "Remove repeated content; aim for under 1000 words."
    elif n_words >= 5000:
        # Very long but not repetitive — might be justified (code, docs)
        # Penalize mildly for verbosity but acknowledge it may be warranted
        score = 3
        evidence = f"{n_words} words, repetition={repetition_ratio:.0%} — very long but not redundant"
        improvement = "Consider splitting into sections or summarizing key points."
    else:
        score = 2
        evidence = f"{n_words} words, repetition={repetition_ratio:.0%} — too verbose"
        improvement = "Slash length and remove repetition; aim for under 1000 words."

    return AxisScore("conciseness", score, evidence, improvement)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _band(value: float) -> int:
    """Map a 0-1 quality score to the 1-5 band scale."""
    if value >= 0.9:
        return 5
    if value >= 0.75:
        return 4
    if value >= 0.55:
        return 3
    if value >= 0.35:
        return 2
    return 1
