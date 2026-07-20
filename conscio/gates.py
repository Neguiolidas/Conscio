"""Gates module — pre-action and post-session gate tools (v3.0).

All functions are deterministic, stdlib-only, and advisory. They inspect
engine state and EventBus history, emit gate events, and return structured
dicts. No LLM calls except council.critic (opt-in via awake adapter).

Tools:
    decide()        — Architecture Decision Record (ADR)
    council()       — 4-voice decision analysis
    loop_gate()     — Autonomous loop vetting (4 conditions)
    delivery_check()— Pre-close quality gate (3 checks)
    investigate()   — Pre-action read verification
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

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


def _check_closed(engine: "ConsciousnessEngine") -> None:
    """Raise RuntimeError if engine has been closed."""
    if getattr(engine, "_closed", False):
        raise RuntimeError("Cannot call gate tool on a closed engine")


# ── ADR Status ───────────────────────────────────────────────────────

ADR_VALID_STATUSES = {"proposed", "accepted", "deprecated", "superseded"}


# ── Council Roles ────────────────────────────────────────────────────

COUNCIL_ROLES = ("architect", "skeptic", "pragmatist", "critic")
COUNCIL_VOTES = ("proceed", "hold", "veto")


# ── decide() ─────────────────────────────────────────────────────────

def decide(
    engine: "ConsciousnessEngine",
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
    engine: "ConsciousnessEngine",
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
    voices = [
        _voice_architect(engine, question, context, options),
        _voice_skeptic(engine, question, context, options),
        _voice_pragmatist(engine, question, context, options),
        _voice_critic(engine, question, context, options),
    ]

    # Determine recommendation: majority vote
    votes = [v["vote"] for v in voices]
    vetoes = votes.count("veto")
    holds = votes.count("hold")

    if vetoes >= 2:
        recommendation = "veto"
    elif holds >= 2:
        recommendation = "hold"
    elif vetoes >= 1:
        recommendation = "hold"
    else:
        recommendation = "proceed"

    result = {
        "question": question,
        "voices": voices,
        "recommendation": recommendation,
        "votes_summary": {
            "proceed": votes.count("proceed"),
            "hold": holds,
            "veto": vetoes,
        },
    }
    engine.event_bus.emit("council:convened", "consciousness", result)
    return result


def _voice_architect(
    engine: "ConsciousnessEngine",
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

    return {
        "role": "architect",
        "analysis": "; ".join(analysis_items),
        "concerns": concerns,
        "vote": vote,
    }


def _voice_skeptic(
    engine: "ConsciousnessEngine",
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

    return {
        "role": "skeptic",
        "analysis": "; ".join(analysis_items),
        "concerns": concerns,
        "vote": vote,
    }


def _voice_pragmatist(
    engine: "ConsciousnessEngine",
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

    return {
        "role": "pragmatist",
        "analysis": "; ".join(analysis_items),
        "concerns": concerns,
        "vote": vote,
    }


def _voice_critic(
    engine: "ConsciousnessEngine",
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

    # Try LLM path
    adapter = _get_adapter(engine)
    if adapter is not None:
        try:
            prompt = (
                f"You are a critical reviewer. Analyze this decision for "
                f"failure modes, blind spots, and worst-case scenarios.\n\n"
                f"Question: {question}\n"
                f"Context: {context}\n"
                f"Options: {options or 'none specified'}\n\n"
                f"List 2-3 specific failure modes. Be concise."
            )
            result = adapter.generate(prompt, max_tokens=256, temperature=0.3)
            analysis_items.append(f"LLM analysis: {result.text[:200]}")
            # Any LLM output counts as a concern to surface
            concerns.append("LLM-identified risks — review critically")
        except Exception:
            # LLM failed — fall back to deterministic
            analysis_items.append("LLM unavailable — using deterministic fallback")
            concerns = _critic_deterministic(engine, question, context, options)
    else:
        analysis_items.append("No adapter attached — using deterministic analysis")
        concerns = _critic_deterministic(engine, question, context, options)

    analysis_items.extend(concerns)
    vote = "veto" if len(concerns) >= 2 else ("hold" if concerns else "proceed")

    return {
        "role": "critic",
        "analysis": "; ".join(analysis_items),
        "concerns": concerns,
        "vote": vote,
    }


def _critic_deterministic(
    engine: "ConsciousnessEngine",
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

    if not concerns:
        concerns.append("No obvious failure modes identified — consider second-order effects")

    return concerns


def _get_adapter(engine: "ConsciousnessEngine"):
    """Get the LLM adapter from the engine if awake and attached."""
    if not engine.awake:
        return None
    pipeline = getattr(engine, "_act_pipeline", None)
    if pipeline is None:
        return None
    adapter = getattr(pipeline, "adapter", None)
    return adapter


# ── loop_gate() ───────────────────────────────────────────────────────

def loop_gate(
    engine: "ConsciousnessEngine",
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

def delivery_check(engine: "ConsciousnessEngine") -> dict:
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

    # 2. Stale proposals
    pending = engine.evolution.pending_proposals()
    if pending:
        blockers.append(f"{len(pending)} pending evolution proposal(s) not resolved")

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
        "stale_proposals": len(pending),
        "disk_free_gb": round(free_gb, 3) if free_gb >= 0 else -1.0,
    }

    engine.event_bus.emit(
        "system", "system",
        {"check": "delivery", "pass": passed, "blockers": blockers},
    )
    return result


# ── investigate() ────────────────────────────────────────────────────

def investigate(
    engine: "ConsciousnessEngine",
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
