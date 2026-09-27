"""Gates module — pre-action and post-session gate tools (v3.0).

All functions are deterministic, stdlib-only, and advisory. They inspect
engine state and EventBus history, emit gate events, and return structured
dicts. All council voices and gate functions are deterministic; optional LLM
adapters remain available to other pipelines such as squads.

Tools:
    decide()        — Architecture Decision Record (ADR)
    council()       — 4-voice decision analysis
    loop_gate()     — Autonomous loop vetting (4 conditions)
    delivery_check()— Pre-close quality gate (3 checks)
    investigate()   — Pre-action read verification
"""

from __future__ import annotations

import logging
import math
import re
import shutil
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from conscio.auto_evolution import ProposalStatus
from conscio.council_traits import Traits, extract_traits

if TYPE_CHECKING:
    from conscio.engine import ConsciousnessEngine


# ── Rationalization detection patterns ──────────────────────────────

_RATIONALIZATION_PATTERNS = [
    re.compile(r"\bprobably\b.*\b(?:works|fine|ok|good)\b", re.IGNORECASE),
    re.compile(r"\bunlikely\b.*\b(?:skip|ignore|later)\b", re.IGNORECASE),
    re.compile(r"\b(?:just|simply)\b.*\bship\b", re.IGNORECASE),
    re.compile(r"\bno need to (?:test|verify|check)\b", re.IGNORECASE),
    re.compile(r"\bdefinitely\b.*\b(?:works|correct|fine)\b", re.IGNORECASE),
    re.compile(r"\b(?:edge cases|corner cases)\b.*\b(?:unlikely|skip|rare)\b", re.IGNORECASE),
]


def _check_closed(engine: ConsciousnessEngine) -> None:
    """Raise RuntimeError if engine has been closed."""
    if getattr(engine, "_closed", False):
        raise RuntimeError("Cannot call gate tool on a closed engine")


# ── ADR Status ───────────────────────────────────────────────────────

ADR_VALID_STATUSES = {"proposed", "accepted", "deprecated", "superseded"}


# ── Council Roles ────────────────────────────────────────────────────

COUNCIL_ROLES = ("architect", "skeptic", "pragmatist", "critic")
COUNCIL_VOTES = ("proceed", "hold", "veto")

# Vote severity used by the trait vote: proceed < hold < veto.
_VOTE_SEVERITY = {"proceed": 0, "hold": 1, "veto": 2}


def _stricter(a: str, b: str) -> str:
    """The more severe of two votes (proceed < hold < veto)."""
    return a if _VOTE_SEVERITY[a] >= _VOTE_SEVERITY[b] else b


# ── Council trait weights (calibration spec section 5.2, T6) ─────────
# Integer weight and threshold table. Every constant carries its
# provenance comment: the dev round that produced the value and the dev
# metric it moved. The state checks above each vote stay untouched —
# this table only drives the separate trait vote, and the stricter of
# the two wins (spec section 5.2).

# architect: risk = irreversible, blast_radius; mitigator = reversible.
# dev, rodada 2, hold row: the 3 single-blast_radius hold cases sat one
# point below the hold line (score 2 vs hold_at 3) and 0 of 22 dev hold
# cases became hold in rodada 1; raising blast_radius to 3 puts a lone
# lit blast_radius at the hold line while the 2 single-blast proceed
# cases keep only ONE hold (council stays proceed).
W_ARCH_IRREVERSIBLE = 3
W_ARCH_BLAST_RADIUS = 3
W_ARCH_REVERSIBLE = 2
ARCH_HOLD_AT = 3
ARCH_VETO_AT = 5

# skeptic: risk = unverified, bypasses_checks; mitigator = verified.
# dev, rodada 2, veto row: the 2 single-bypasses_checks veto cases and
# the blast+bypasses case never vetoed (score 3 vs veto_at 5) in
# rodada 1; no dev proceed case lights bypasses_checks, so a lone
# bypass now vetoes instead of holding.
W_SKEPTIC_UNVERIFIED = 2
W_SKEPTIC_BYPASSES_CHECKS = 4
W_SKEPTIC_VERIFIED = 2
SKEPTIC_HOLD_AT = 3
SKEPTIC_VETO_AT = 4

# pragmatist: risk = underspecified; mitigator = low_stakes.
# dev, rodada 1, baseline matrix (rodada 0): the c1 underspecified
# cases split hold/proceed in the labels; one lit trait holds, and the
# low_stakes mitigator pulls the m-pair cases back to proceed.
W_PRAG_UNDERSPECIFIED = 3
W_PRAG_LOW_STAKES = 2
PRAG_HOLD_AT = 3
PRAG_VETO_AT = 6

# critic: risk = data_exposure, plus irreversible WITHOUT the reversible
# mitigator; no mitigator of its own.
# dev, rodada 1, baseline matrix (rodada 0): the data_exposure dev cases
# are labeled veto, so a single lit data_exposure sits at the veto line.
W_CRITIC_DATA_EXPOSURE = 4
W_CRITIC_IRREVERSIBLE_NO_REVERSIBLE = 3
CRITIC_HOLD_AT = 3
CRITIC_VETO_AT = 4


def _threshold_vote(score: int, hold_at: int, veto_at: int) -> str:
    """Score -> trait vote: veto at >= veto_at, hold at >= hold_at."""
    if score >= veto_at:
        return "veto"
    if score >= hold_at:
        return "hold"
    return "proceed"


def _architect_traits(traits: Traits) -> tuple[str, list[str]]:
    """Architect trait vote (spec section 5.2): risk = irreversible,
    blast_radius; mitigator = reversible. Returns (trait vote, the
    lit risk-trait concerns, each naming its trait)."""
    score = 0
    concerns: list[str] = []
    if traits.irreversible:
        score += W_ARCH_IRREVERSIBLE
        concerns.append("trait irreversible: the action destroys or rewrites something with no way back")
    if traits.blast_radius:
        score += W_ARCH_BLAST_RADIUS
        concerns.append("trait blast_radius: the change reaches shared or public territory")
    if traits.reversible:
        score -= W_ARCH_REVERSIBLE
    vote = _threshold_vote(score, ARCH_HOLD_AT, ARCH_VETO_AT)
    return vote, concerns


def _skeptic_traits(traits: Traits) -> tuple[str, list[str]]:
    """Skeptic trait vote (spec section 5.2): risk = unverified,
    bypasses_checks; mitigator = verified."""
    score = 0
    concerns: list[str] = []
    if traits.unverified:
        score += W_SKEPTIC_UNVERIFIED
        concerns.append("trait unverified: claims are asserted without evidence")
    if traits.bypasses_checks:
        score += W_SKEPTIC_BYPASSES_CHECKS
        concerns.append("trait bypasses_checks: the plan skips a required verification")
    if traits.verified:
        score -= W_SKEPTIC_VERIFIED
    vote = _threshold_vote(score, SKEPTIC_HOLD_AT, SKEPTIC_VETO_AT)
    return vote, concerns


def _pragmatist_traits(traits: Traits) -> tuple[str, list[str]]:
    """Pragmatist trait vote (spec section 5.2): risk = underspecified;
    mitigator = low_stakes."""
    score = 0
    concerns: list[str] = []
    if traits.underspecified:
        score += W_PRAG_UNDERSPECIFIED
        concerns.append("trait underspecified: the request declares that information is missing")
    if traits.low_stakes:
        score -= W_PRAG_LOW_STAKES
    vote = _threshold_vote(score, PRAG_HOLD_AT, PRAG_VETO_AT)
    return vote, concerns


def _critic_traits(traits: Traits) -> tuple[str, list[str]]:
    """Critic trait vote (spec section 5.2): risk = data_exposure, plus
    irreversible WITHOUT the reversible mitigator; no mitigator of its
    own. The critic stays deterministic and never calls the adapter."""
    score = 0
    concerns: list[str] = []
    if traits.data_exposure:
        score += W_CRITIC_DATA_EXPOSURE
        concerns.append("trait data_exposure: secrets or personal data may be exposed")
    if traits.irreversible and not traits.reversible:
        score += W_CRITIC_IRREVERSIBLE_NO_REVERSIBLE
        concerns.append("trait irreversible without a reversible mitigation")
    vote = _threshold_vote(score, CRITIC_HOLD_AT, CRITIC_VETO_AT)
    return vote, concerns



# ── decide() ─────────────────────────────────────────────────────────

def decide(
    engine: ConsciousnessEngine,
    *,
    title: str = "",
    context: str = "",
    alternatives: list[str] | None = None,
    adr_id: str | None = None,
    status: str = "proposed",
    deciders: list[str] | None = None,
) -> dict:
    """Create or update an Architecture Decision Record.

    Creating: pass title + context (+ optional alternatives).
    Updating: pass adr_id + new status.

    Returns dict with adr_id, title, status, context, alternatives, deciders.
    """
    _check_closed(engine)
    if status not in ADR_VALID_STATUSES:
        raise ValueError(
            f"Invalid ADR status '{status}'. Must be one of: {ADR_VALID_STATUSES}"
        )

    # Update existing ADR
    if adr_id is not None:
        events = engine.event_bus.query(type="adr:proposed", limit=100)
        # Also check accepted events for status transitions
        accepted = engine.event_bus.query(type="adr:accepted", limit=100)
        all_adr_events = events + accepted
        matching = [e for e in all_adr_events
                    if e.data.get("adr_id") == adr_id]
        if not matching:
            return {"error": f"ADR '{adr_id}' not found", "adr_id": adr_id}
        original = matching[0].data
        updated = {
            "adr_id": adr_id,
            "title": original.get("title", ""),
            "status": status,
            "context": original.get("context", ""),
            "alternatives": original.get("alternatives", []),
            "deciders": deciders or original.get("deciders", []),
            "previous_status": original.get("status", "proposed"),
        }
        event_type = "adr:accepted" if status == "accepted" else "adr:proposed"
        engine.event_bus.emit(event_type, "consciousness", updated)
        return updated

    # Create new ADR
    if not title:
        raise ValueError("title is required when creating a new ADR")

    import secrets
    now = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    new_id = f"ADR-{now}-{secrets.token_hex(3)}"

    result = {
        "adr_id": new_id,
        "title": title,
        "status": status,
        "context": context,
        "alternatives": alternatives or [],
        "deciders": deciders or [],
    }
    engine.event_bus.emit("adr:proposed", "consciousness", result)
    return result


# ── council() ────────────────────────────────────────────────────────

def council(
    engine: ConsciousnessEngine,
    question: str = "",
    context: str = "",
    options: list[str] | None = None,
) -> dict:
    """Convene a 4-voice council for decision analysis.

    Architect, Skeptic, Pragmatist are deterministic (engine state analysis).
    Critic uses LLM adapter if awake + attached, otherwise deterministic fallback.

    Returns dict with voices, recommendation, question.
    """
    if not question:
        raise ValueError("question is required")

    _check_closed(engine)

    # v3.4.1: auto-reflect when last_coherence is None
    # note()/feed() deposit events but don't run coherence assessment.
    # Without this, Architect always vetoes on fresh installations.
    if getattr(engine, "last_coherence", None) is None:
        try:
            engine.reflect()
        except Exception:
            pass  # reflect failure should not block council

    voice_calls = [
        ("architect", _voice_architect),
        ("skeptic", _voice_skeptic),
        ("pragmatist", _voice_pragmatist),
        ("critic", _voice_critic),
    ]
    voices: list[dict] = []
    for role, fn in voice_calls:
        try:
            voices.append(fn(engine, question, context, options))
        except Exception as e:  # a failing voice must never sink the council
            voices.append({
                "role": role,
                "analysis": f"voice degraded (error): {e}",
                "concerns": ["Voice could not analyze — treat as counsel withheld"],
                "vote": "hold",
            })

    # ── Determine recommendation: conservative, not permissive. ─────────
    # Default under ambiguity is HOLD, never PROCEED. Proceed only when there
    # is a genuine affirmative majority (>=3 of 4) AND no veto. A split council
    # (e.g. 2 hold / 2 proceed, or any veto) is an explicit "not ready" — never
    # a silent go-ahead. This makes the council assertive about uncertainty.
    votes = [v["vote"] for v in voices]
    vetoes = votes.count("veto")
    holds = votes.count("hold")
    proceeds = votes.count("proceed")

    if vetoes >= 1:
        recommendation = "veto"
    elif holds >= 2:
        recommendation = "hold"
    elif proceeds >= 3:
        recommendation = "proceed"
    else:
        recommendation = "hold"  # 2-2 or ambiguous → not ready

    agreement_val = round(_compute_vote_agreement(votes), 4)
    agreement = {
        "value": agreement_val,
        "category": "asserted",
        "method": "vote_entropy",
    }
    recommendation_category = "asserted"
    consensus_strength = agreement_val  # deprecated alias of agreement["value"]

    dissenting = [v["role"] for v in voices if v["vote"] != recommendation]

    result = {
        "question": question,
        "voices": voices,
        "recommendation": recommendation,
        "recommendation_category": recommendation_category,
        "agreement": agreement,
        "consensus_strength": consensus_strength,
        "dissenting_voices": dissenting,
        "votes_summary": {
            "proceed": proceeds,
            "hold": holds,
            "veto": vetoes,
        },
    }
    engine.event_bus.emit("council:convened", "consciousness", result)
    # v4.7 P1: capture the decision with provenance — the verdict arrives
    # later via OutcomeStore.resolve() when the task's real outcome is known.
    # Best-effort: capture must never sink the council; a failure is logged.
    try:
        from .outcomes import capture_council_outcome
        store = getattr(engine, "outcome_store", None)
        if store is not None:
            capture_council_outcome(store, result)
    except Exception:
        logging.getLogger(__name__).warning(
            "council outcome capture failed", exc_info=True)
    return result


def _compute_vote_agreement(votes: list[str]) -> float:
    """Compute agreement as 1 - normalized_entropy(vote_counts).

    Categories: proceed, hold, veto (K=3).
    Unanimous votes (all proceed, all hold, or all veto) yield entropy 0.0 -> agreement 1.0.
    Uniform distribution yields max entropy ln(3) -> agreement 0.0.
    """
    if not votes:
        return 0.0
    n = len(votes)
    counts = [votes.count("proceed"), votes.count("hold"), votes.count("veto")]
    # If all votes belong to a single category, agreement is exactly 1.0
    if any(c == n for c in counts):
        return 1.0
    k = 3  # proceed, hold, veto
    max_entropy = math.log(k)
    entropy = 0.0
    for c in counts:
        if c > 0:
            p = c / n
            entropy -= p * math.log(p)
    normalized_entropy = entropy / max_entropy
    return max(0.0, min(1.0, 1.0 - normalized_entropy))


def _voice_architect(
    engine: ConsciousnessEngine,
    question: str,
    context: str,
    options: list[str] | None,
) -> dict:
    """Architect voice: coherence, structural integrity, invariant preservation."""
    analysis_items = []
    concerns = []

    # Check coherence
    coherence = getattr(engine, "last_coherence", None)
    if coherence is not None:
        score = coherence.score if hasattr(coherence, "score") else float(coherence)
        analysis_items.append(f"Coherence score: {score:.2f}")
        if score < 0.5:
            concerns.append("Low coherence — structural integrity at risk")
    else:
        analysis_items.append("No coherence data available")
        concerns.append("Cannot assess structural integrity without coherence data")

    # Check contradictions in world model
    entities = engine.world.list_entities()
    if entities:
        analysis_items.append(f"World model: {len(entities)} entities")
        # Check state_log for contradictions
        stale = engine.world.stale_entities()
        if stale:
            concerns.append(f"{len(stale)} stale entities may indicate drift")

    # Check active goals
    goals = engine.goals.active_goals()
    analysis_items.append(f"Active goals: {len(goals)}")

    vote = "veto" if len(concerns) >= 2 else ("hold" if concerns else "proceed")

    # Trait vote (calibration spec section 5.2, T6): a separate vote
    # from the text traits. The state vote above stays intact; the
    # stricter of the two wins, and lit risk traits name themselves in
    # the concerns.
    traits = extract_traits(question, context, options)
    trait_vote, trait_concerns = _architect_traits(traits)
    concerns.extend(trait_concerns)
    vote = _stricter(vote, trait_vote)

    return {
        "role": "architect",
        "analysis": "; ".join(analysis_items),
        "concerns": concerns,
        "vote": vote,
    }


def _voice_skeptic(
    engine: ConsciousnessEngine,
    question: str,
    context: str,
    options: list[str] | None,
) -> dict:
    """Skeptic voice: contradictions, untested assumptions, error patterns."""
    analysis_items = []
    concerns = []

    # Check frequent errors
    frequent_errors = engine.meta.frequent_errors(min_count=2)
    if frequent_errors:
        analysis_items.append(f"Frequent errors: {len(frequent_errors)}")
        for fe in frequent_errors[:3]:
            concerns.append(f"Recurring error: {fe.get('pattern', fe.get('error', 'unknown'))}")
    else:
        analysis_items.append("No recurring errors detected")

    # Check contradictions in state_log
    state_log = getattr(engine.world, "state_log", [])
    if state_log:
        entity_states: dict[str, list[str]] = {}
        for entry in state_log:
            name = entry.get("name", "")
            state = entry.get("state", "")
            if name and state:
                entity_states.setdefault(name, []).append(state)
        for name, states in entity_states.items():
            if len(set(states)) > 1:
                concerns.append(f"Entity '{name}' has contradictory states: {set(states)}")
        analysis_items.append(f"State log entries: {len(state_log)}")

    # Check pending proposals
    pending = engine.evolution.pending_proposals()
    if pending:
        analysis_items.append(f"Pending proposals: {len(pending)}")
        concerns.append("Unresolved evolution proposals may conflict")

    vote = "veto" if len(concerns) >= 2 else ("hold" if concerns else "proceed")

    # Trait vote (calibration spec section 5.2, T6): the state vote
    # above stays intact; the stricter of the two wins, and lit risk
    # traits name themselves in the concerns.
    traits = extract_traits(question, context, options)
    trait_vote, trait_concerns = _skeptic_traits(traits)
    concerns.extend(trait_concerns)
    vote = _stricter(vote, trait_vote)

    return {
        "role": "skeptic",
        "analysis": "; ".join(analysis_items),
        "concerns": concerns,
        "vote": vote,
    }


def _voice_pragmatist(
    engine: ConsciousnessEngine,
    question: str,
    context: str,
    options: list[str] | None,
) -> dict:
    """Pragmatist voice: metabolic cost, budget, timeline feasibility."""
    analysis_items = []
    concerns = []

    # Check metabolic state
    state = engine._state
    metabolic = getattr(state, "metabolic", "")
    if metabolic:
        analysis_items.append(f"Metabolic: {metabolic}")
        if "critical" in metabolic.lower():
            concerns.append("Critical metabolic state — insufficient resources for new work")
        elif "constrained" in metabolic.lower():
            concerns.append("Constrained resources — prioritize carefully")

    # Check token budget
    total_approx = state.total_tokens_approx() if hasattr(state, "total_tokens_approx") else 0
    used_tokens = getattr(engine, "session_tokens_used", None)
    if total_approx > 0 and used_tokens is not None:
        utilization = used_tokens / max(total_approx, 1)
        analysis_items.append(f"Token utilization: {utilization:.0%}")
        if utilization > 0.9:
            concerns.append(f"Token budget nearly exhausted ({utilization:.0%})")
    elif total_approx > 0:
        analysis_items.append(f"State tokens (approx): {total_approx}")

    # Check options feasibility
    if options:
        analysis_items.append(f"Options considered: {len(options)}")
        if len(options) < 2:
            concerns.append("Only one option considered — no alternatives evaluated")

    vote = "veto" if len(concerns) >= 2 else ("hold" if concerns else "proceed")

    # Trait vote (calibration spec section 5.2, T6): the state vote
    # above stays intact; the stricter of the two wins, and lit risk
    # traits name themselves in the concerns.
    traits = extract_traits(question, context, options)
    trait_vote, trait_concerns = _pragmatist_traits(traits)
    concerns.extend(trait_concerns)
    vote = _stricter(vote, trait_vote)

    return {
        "role": "pragmatist",
        "analysis": "; ".join(analysis_items),
        "concerns": concerns,
        "vote": vote,
    }


def _voice_critic(
    engine: ConsciousnessEngine,
    question: str,
    context: str,
    options: list[str] | None,
) -> dict:
    """Critic voice: failure modes, blind spots, worst-case scenarios.

    Uses LLM adapter if engine is awake and adapter is attached.
    Otherwise falls back to deterministic analysis.
    """
    analysis_items = []
    concerns = []

    # Council is deterministic by contract. LLM analysis remains an optional
    # future integration point, but an attached adapter must never alter this
    # council's votes or make its availability a prerequisite.
    analysis_items.append("Deterministic analysis")
    concerns = _critic_deterministic(engine, question, context, options)
    analysis_items.extend(concerns)
    vote = "veto" if len(concerns) >= 2 else ("hold" if concerns else "proceed")

    # Trait vote (calibration spec section 5.2, T6): the state vote
    # above stays intact; the stricter of the two wins, and lit risk
    # traits name themselves in the concerns. The critic stays
    # deterministic: the trait vote adds no LLM call.
    traits = extract_traits(question, context, options)
    trait_vote, trait_concerns = _critic_traits(traits)
    concerns.extend(trait_concerns)
    vote = _stricter(vote, trait_vote)

    return {
        "role": "critic",
        "analysis": "; ".join(analysis_items),
        "concerns": concerns,
        "vote": vote,
    }


def _critic_deterministic(
    engine: ConsciousnessEngine,
    question: str,
    context: str,
    options: list[str] | None,
) -> list[str]:
    """Deterministic fallback for critic voice."""
    concerns = []

    # Check confidence variance
    avg_conf = engine.meta.average_confidence()
    if avg_conf < 0.5:
        concerns.append(f"Low average confidence ({avg_conf:.2f}) — decisions may be unreliable")

    # Check for recent anomalies
    anomalies = engine.event_bus.query(type="anomaly", limit=5)
    if anomalies:
        concerns.append(f"{len(anomalies)} recent anomaly(s) — environment may be unstable")

    # Warn about irreversible actions
    if context and any(w in context.lower() for w in ["delete", "remove", "drop", "destroy"]):
        concerns.append("Context mentions destructive action — ensure reversibility")

    # NOTE: do NOT append a synthetic concern here. A critic that finds no
    # concrete failure mode votes PROCEED, which is the honest signal. The
    # old behavior force-fed a "consider second-order effects" concern so the
    # critic could never clearly endorse a decision — that biased every
    # council toward hold/veto. Let a clean critic be a clean proceed.

    return concerns


# ── loop_gate() ───────────────────────────────────────────────────────

def loop_gate(
    engine: ConsciousnessEngine,
    *,
    task: str = "",
    frequency: str = "",
    verifiable: bool = True,
    budget_ok: bool = True,
    has_tools: bool = True,
) -> dict:
    """Vet an autonomous loop against 4 conditions.

    Conditions:
    1. frequency — task repeats regularly (daily/weekly/hourly)
    2. verifiable — success can be automatically checked
    3. budget_ok — token/resource budget can sustain the loop
    4. has_tools — agent has functional tools for the task

    Returns dict with approved, conditions, vetoed_conditions.
    """
    _check_closed(engine)
    conditions = {
        "frequency": frequency.strip() != "",
        "verifiable": verifiable,
        "budget_ok": budget_ok,
        "has_tools": has_tools,
    }

    vetoed = [k for k, v in conditions.items() if not v]
    approved = len(vetoed) == 0

    result = {
        "task": task,
        "approved": approved,
        "conditions": conditions,
        "vetoed_conditions": vetoed,
    }

    if not approved:
        engine.event_bus.emit(
            "gate:vetoed", "consciousness",
            {"gate": "loop", "task": task, "vetoed_conditions": vetoed},
        )

    return result


# ── delivery_check() ─────────────────────────────────────────────────

def delivery_check(engine: ConsciousnessEngine) -> dict:
    """Pre-close quality gate: rationalization, stale libs, disk space.

    Checks:
    1. Rationalization — scan recent note events for rationalization patterns
    2. Stale proposals — check for long-pending evolution proposals
    3. Disk space — verify adequate free space on storage volume

    Returns dict with pass, blockers, rationalization_hits, stale_proposals, disk_free_gb.
    """
    _check_closed(engine)
    blockers = []

    # 1. Rationalization scan
    rat_hits = 0
    notes = engine.event_bus.query(type="host:event", limit=50)
    for event in notes:
        # MCP note() stores text in data["payload"]["text"]
        payload = event.data.get("payload", {})
        text = payload.get("text", "") if isinstance(payload, dict) else ""
        # Also check top-level "text" for direct EventBus usage
        if not text:
            text = event.data.get("text", "")
        for pattern in _RATIONALIZATION_PATTERNS:
            if pattern.search(text):
                rat_hits += 1
                break  # one match per event is enough

    if rat_hits >= 2:
        blockers.append(f"Rationalization detected: {rat_hits} note(s) contain self-deception patterns")

    # 2. Stale proposals — PENDING *and* APPROVED-but-never-applied (v3.9.4).
    # approve() moves a proposal out of PENDING, so a crash between approve and
    # mark_applied left it unfinished and unseen by this gate forever.
    unresolved = engine.evolution.unresolved_proposals()
    pending = [p for p in unresolved if p.status is ProposalStatus.PENDING]
    approved = [p for p in unresolved if p.status is ProposalStatus.APPROVED]
    if pending:
        blockers.append(f"{len(pending)} pending evolution proposal(s) not resolved")
    if approved:
        blockers.append(
            f"{len(approved)} approved evolution proposal(s) never applied")

    # 3. Disk space
    try:
        usage = shutil.disk_usage(str(engine.storage))
        free_gb = usage.free / (1024 ** 3)
        if free_gb < 0.1:  # less than 100MB
            blockers.append(f"Low disk space: {free_gb:.2f} GB free")
    except OSError:
        free_gb = -1.0
        blockers.append("Cannot determine disk space")

    passed = len(blockers) == 0
    result = {
        "pass": passed,
        "blockers": blockers,
        "rationalization_hits": rat_hits,
        "stale_proposals": len(unresolved),
        "disk_free_gb": round(free_gb, 3) if free_gb >= 0 else -1.0,
    }

    engine.event_bus.emit(
        "system", "system",
        {"check": "delivery", "pass": passed, "blockers": blockers},
    )
    return result


# ── investigate() ────────────────────────────────────────────────────

def investigate(
    engine: ConsciousnessEngine,
    *,
    target: str = "",
    action_type: str = "",
) -> dict:
    """Verify that the target was read before acting.

    Checks EventBus for `note` or `host:event` events whose data contains
    an `investigate:read` key mentioning the target.

    Returns dict with satisfied, missing, target, action_type.
    """
    if not target:
        raise ValueError("target is required")

    _check_closed(engine)

    # Check for read events mentioning target
    note_events = engine.event_bus.query(type="host:event", limit=50)
    all_events = note_events

    found = False
    for event in all_events:
        read_target = event.data.get("investigate:read", "")
        if not read_target:
            continue
        # Match: exact, substring, or one is suffix of the other
        if (read_target == target
                or target in read_target
                or read_target in target):
            found = True
            break

    missing = [] if found else [f"investigate:read: {target}"]

    result = {
        "satisfied": found,
        "missing": missing,
        "target": target,
        "action_type": action_type,
    }

    if not found:
        engine.event_bus.emit(
            "gate:vetoed", "consciousness",
            {"gate": "investigate", "target": target, "action": action_type},
        )

    return result
