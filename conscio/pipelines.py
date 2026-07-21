"""Pipelines module — workflow enrichment tools (v3.0).

All functions are deterministic, stdlib-only, and advisory. They inspect
engine state and EventBus history, emit pipeline events, and return dicts.

Tools:
    acceptance_criteria — intent-driven acceptance criteria generation
    verify — post-implementation verification against criteria
    continuous_loop — autonomous loop pattern selection
    strategic_compact — strategic compaction advisory
    ledger — recursive decision ledger with coherence marks
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .engine import ConsciousnessEngine

# ── Constants ─────────────────────────────────────────────────────────

LOOP_PATTERNS = ("sequential", "continuous_pr", "rfc_dag", "infinite")
PROMOTION_GATES = ("paper", "dry_run", "live")

_RISK_KEYWORDS: dict[str, list[str]] = {
    "security": ["auth", "password", "token", "secret", "credential", "encrypt",
                 "decrypt", "permission", "access", "firewall", "vulnerability"],
    "data": ["migration", "schema", "database", "backup", "restore", "seed",
             "truncate", "drop", "column", "table"],
    "integration": ["api", "webhook", "callback", "oauth", "saml", "sso",
                    "provider", "third-party", "external"],
    "compliance": ["gdpr", "hipaa", "pci", "sox", "audit", "log", "retention",
                   "privacy", "consent"],
}

_ACCEPTANCE_DEPTH_QUICK = (3, 5)
_ACCEPTANCE_DEPTH_FULL = (6, 10)


# ── Shared guard ──────────────────────────────────────────────────────

def _check_closed(engine: ConsciousnessEngine) -> None:
    """Raise RuntimeError if engine has been closed."""
    if getattr(engine, "_closed", False):
        raise RuntimeError("Cannot call pipeline tool on a closed engine")


# ── 1. acceptance_criteria ───────────────────────────────────────────

def acceptance_criteria(
    engine: ConsciousnessEngine,
    *,
    goal: str = "",
    depth: str = "",
    risk_domains: list[str] | None = None,
) -> dict:
    """Generate acceptance criteria for a goal or task.

    Inspects context from EventBus (propose_plan/propose_action events)
    and generates structured criteria with risk assessment.

    Args:
        engine: active ConsciousnessEngine instance.
        goal: the objective to generate criteria for.
        depth: "quick" (3-5 criteria) or "full" (6-10). "" = auto-detect.
        risk_domains: override auto-detected risk domains.

    Returns:
        Dict with goal, depth, risk_level, risk_domains, criteria, acceptance_count.
    """
    _check_closed(engine)

    # Auto-detect goal from recent propose_plan events if not provided
    if not goal:
        plan_events = engine.event_bus.query(type="host:event", limit=20)
        for ev in plan_events:
            text = ev.data.get("text", "")
            payload = ev.data.get("payload", {})
            if isinstance(payload, dict):
                text = payload.get("text", text)
            # Look for propose_plan intent in text
            if "propose_plan" in text.lower() or "goal:" in text.lower():
                # Extract goal after "goal:" or use full text
                if "goal:" in text.lower():
                    goal = text.split("goal:", 1)[1].strip().split("\n")[0]
                else:
                    goal = text
                break
        if not goal:
            goal = "unspecified goal"

    # Auto-detect risk domains
    goal_lower = goal.lower()
    detected_domains: list[str] = []
    if risk_domains is not None:
        detected_domains = list(risk_domains)
    else:
        for domain, keywords in _RISK_KEYWORDS.items():
            if any(kw in goal_lower for kw in keywords):
                detected_domains.append(domain)

    # Auto-detect depth
    if not depth:
        depth = "full" if detected_domains else "quick"
    if depth not in ("quick", "full"):
        depth = "quick"

    # Risk level
    if len(detected_domains) >= 3:
        risk_level = "high"
    elif len(detected_domains) >= 1:
        risk_level = "moderate"
    else:
        risk_level = "low"

    # Generate criteria
    min_c, max_c = _ACCEPTANCE_DEPTH_FULL if depth == "full" else _ACCEPTANCE_DEPTH_QUICK
    criteria = _generate_criteria(goal, depth, detected_domains, min_c, max_c)

    result = {
        "goal": goal,
        "depth": depth,
        "risk_level": risk_level,
        "risk_domains": detected_domains,
        "criteria": criteria,
        "acceptance_count": len(criteria),
    }

    engine.event_bus.emit("pipeline:acceptance", "consciousness", result)
    return result


_counter = 0

def _generate_criteria(
    goal: str, depth: str, domains: list[str], min_c: int, max_c: int,
) -> list[dict]:
    """Generate structured acceptance criteria with globally unique IDs."""
    global _counter
    criteria: list[dict] = []

    # Functional criteria (always present)
    functional_templates = [
        ("{goal} completes without errors", "functional"),
        ("{goal} produces expected output", "functional"),
        ("{goal} handles invalid input gracefully", "functional"),
    ]
    for template, ctype in functional_templates:
        _counter += 1
        criteria.append({
            "id": f"AC-{_counter:03d}",
            "description": template.format(goal=goal),
            "type": ctype,
            "verified": False,
        })

    # Risk-domain-specific criteria
    for domain in domains:
        templates = _domain_templates(domain, goal)
        for template, ctype in templates:
            _counter += 1
            criteria.append({
                "id": f"AC-{_counter:03d}",
                "description": template.format(goal=goal),
                "type": ctype,
                "verified": False,
            })

    # Full depth adds integration + regression criteria
    if depth == "full":
        full_templates = [
            ("{goal} does not break existing integration tests", "integration"),
            ("{goal} passes regression suite", "regression"),
            ("{goal} documented in changelog", "documentation"),
        ]
        for template, ctype in full_templates:
            _counter += 1
            criteria.append({
                "id": f"AC-{_counter:03d}",
                "description": template.format(goal=goal),
                "type": ctype,
                "verified": False,
            })

    # Trim to max
    return criteria[:max_c]


def _domain_templates(domain: str, goal: str) -> list[tuple[str, str]]:
    """Return criteria templates for a risk domain."""
    templates: dict[str, list[tuple[str, str]]] = {
        "security": [
            ("{goal} does not expose credentials in logs", "security"),
            ("{goal} validates all inputs before processing", "security"),
        ],
        "data": [
            ("{goal} preserves data integrity during migration", "data"),
            ("{goal} has rollback plan if migration fails", "data"),
        ],
        "integration": [
            ("{goal} maintains API backward compatibility", "integration"),
            ("{goal} handles third-party service failures gracefully", "integration"),
        ],
        "compliance": [
            ("{goal} satisfies audit logging requirements", "compliance"),
            ("{goal} respects data retention policies", "compliance"),
        ],
    }
    return templates.get(domain, [])


# ── 2. verify ─────────────────────────────────────────────────────────

def verify(
    engine: ConsciousnessEngine,
    *,
    criteria: list[dict] | None = None,
    criteria_source: str = "",
) -> dict:
    """Verify that acceptance criteria have been met.

    Checks EventBus for evidence events (host:event with verify:evidence key)
    matching each criterion ID. Returns pass/fail per criterion.

    Args:
        engine: active ConsciousnessEngine instance.
        criteria: list of criterion dicts (with "id" keys). None = load from source.
        criteria_source: "acceptance" = load from last pipeline:acceptance event.

    Returns:
        Dict with pass, verified, failed, total, verified_count.
    """
    _check_closed(engine)

    # Load criteria from event if needed
    if criteria is None and criteria_source == "acceptance":
        acceptance_events = engine.event_bus.query(type="pipeline:acceptance", limit=1)
        if acceptance_events:
            criteria = acceptance_events[0].data.get("criteria", [])

    if not criteria:
        return {"pass": True, "verified": [], "failed": [], "total": 0, "verified_count": 0}

    # Check for evidence events
    evidence_events = engine.event_bus.query(type="host:event", limit=100)
    evidence_map: dict[str, str] = {}
    for ev in evidence_events:
        eid = ev.data.get("verify:evidence", "")
        detail = ev.data.get("text", "")
        if eid:
            evidence_map[eid] = detail

    verified_list: list[dict] = []
    failed_list: list[dict] = []

    for c in criteria:
        cid = c.get("id", "")
        desc = c.get("description", "")
        if cid in evidence_map:
            verified_list.append({
                "id": cid, "description": desc,
                "verified": True, "evidence": evidence_map[cid],
            })
        else:
            failed_list.append({
                "id": cid, "description": desc,
                "reason": "no evidence found",
            })

    all_pass = len(failed_list) == 0
    result = {
        "pass": all_pass,
        "verified": verified_list,
        "failed": failed_list,
        "total": len(criteria),
        "verified_count": len(verified_list),
    }

    if all_pass:
        engine.event_bus.emit("pipeline:verified", "consciousness", result)
    else:
        engine.event_bus.emit("gate:vetoed", "consciousness", {
            "gate": "verify",
            "failed_criteria": [f["id"] for f in failed_list],
            "total": len(criteria),
        })

    return result


# ── 3. continuous_loop ────────────────────────────────────────────────

def continuous_loop(
    engine: ConsciousnessEngine,
    *,
    task: str = "",
    pattern: str = "",
    frequency: str = "",
    verifiable: bool = True,
    budget_ok: bool = True,
    has_tools: bool = True,
) -> dict:
    """Select and gate an autonomous loop pattern.

    Analyzes task characteristics to select the best loop pattern,
    then runs loop_gate to verify conditions.

    Args:
        engine: active ConsciousnessEngine instance.
        task: description of the loop task.
        pattern: override pattern selection. "" = auto-detect.
        frequency: how often the loop runs.
        verifiable: can outcomes be automatically verified?
        budget_ok: is the token budget sufficient?
        has_tools: does the agent have working tools for this task?

    Returns:
        Dict with pattern, rationale, loop_gate result, approved, recovery.
    """
    _check_closed(engine)

    # Pattern selection
    if pattern and pattern in LOOP_PATTERNS:
        selected = pattern
    else:
        selected = _select_pattern(task)

    rationale = _pattern_rationale(task, selected)

    # Gate check (reuse gates.loop_gate)
    from .gates import loop_gate as _loop_gate
    gate_result = _loop_gate(
        engine, task=task, frequency=frequency,
        verifiable=verifiable, budget_ok=budget_ok, has_tools=has_tools,
    )

    return {
        "pattern": selected,
        "rationale": rationale,
        "loop_gate": gate_result,
        "approved": gate_result.get("approved", False),
        "recovery": [
            "freeze loop",
            "run harness-audit",
            "reduce scope to failing unit",
            "replay with explicit acceptance criteria",
        ],
    }


def _select_pattern(task: str) -> str:
    """Auto-select loop pattern based on task description."""
    task_lower = task.lower()
    # Use word-level checks to avoid substring matches (e.g. "process" contains "pr")
    import re
    words = set(re.findall(r'\b\w+\b', task_lower))
    if words & {"explore", "brainstorm", "parallel"}:
        return "infinite"
    if words & {"rfc", "decompose", "spec", "architect"}:
        return "rfc_dag"
    if words & {"ci", "merge", "pipeline"}:
        return "continuous_pr"
    # Check "pr" as standalone word (not inside "process")
    if re.search(r'\bpr\b', task_lower):
        return "continuous_pr"
    if words & {"generate", "generating"}:
        return "infinite"
    return "sequential"


def _pattern_rationale(task: str, pattern: str) -> str:
    """Explain why a pattern was selected."""
    rationales = {
        "sequential": "Default step-by-step pattern. No special CI, RFC, or exploration needs detected.",
        "continuous_pr": "Task involves CI/PR/merge control — strict quality gates needed.",
        "rfc_dag": "Task involves decomposition or specification — RFC-style DAG pattern.",
        "infinite": "Task involves exploration or generation — parallel exploratory pattern.",
    }
    return rationales.get(pattern, rationales["sequential"])


# ── 4. strategic_compact ─────────────────────────────────────────────

def strategic_compact(
    engine: ConsciousnessEngine,
    *,
    phase: str = "",
    context_tokens: int = 0,
    context_window: int = 0,
) -> dict:
    """Advise on strategic context compaction timing.

    Inspects token pressure, session milestones, and phase transitions
    to suggest when and how to compact context.

    Args:
        engine: active ConsciousnessEngine instance.
        phase: current workflow phase. "" = auto-detect.
        context_tokens: override current token count. 0 = auto-detect.
        context_window: override context window size. 0 = auto-detect.

    Returns:
        Dict with should_compact, urgency, suggested_phase, keep, drop,
        token_pressure, milestones_completed.
    """
    _check_closed(engine)

    # Token pressure calculation
    if context_tokens <= 0:
        gain = engine.token_tracker.gain(hours=24)
        context_tokens = gain.get("total_raw", 0)

    if context_window <= 0:
        state = engine.state
        context_window = state.total_tokens_approx() if hasattr(state, "total_tokens_approx") else 200000

    token_pressure = round(context_tokens / context_window, 4) if context_window > 0 else 0.0

    # Auto-detect phase
    if not phase:
        phase = _detect_phase(engine)

    # Count milestones from EventBus
    milestones_completed = len(engine.event_bus.query(type="pipeline:verified", limit=50))

    # Decision logic
    should_compact = False
    urgency = "none"

    if token_pressure >= 0.8:
        urgency = "high"
        should_compact = True
    elif token_pressure >= 0.6 and phase in ("execution", "shift"):
        urgency = "moderate"
        should_compact = True
    elif milestones_completed > 0 and phase == "milestone":
        urgency = "low"
        should_compact = True
    elif token_pressure >= 0.5:
        urgency = "low"
        should_compact = False

    # Keep/drop suggestions based on phase
    keep, drop = _compact_suggestions(phase)

    result = {
        "should_compact": should_compact,
        "urgency": urgency,
        "suggested_phase": phase,
        "keep": keep,
        "drop": drop,
        "token_pressure": token_pressure,
        "milestones_completed": milestones_completed,
    }

    if should_compact:
        engine.event_bus.emit("pipeline:compact", "consciousness", result)
        # v3.1: produce a CompactionCheckpoint when compacting.
        # The checkpoint is append-only (never rewrites), enabling the
        # new prompt reconstructed from it to become a cacheable prefix.
        result["checkpoint"] = _produce_checkpoint(engine, result)

    return result


def _produce_checkpoint(engine: ConsciousnessEngine, compact_result: dict) -> dict:
    """v3.1: Create a CompactionCheckpoint and append to chain.

    Returns the checkpoint dict with checkpoint_id for traceability.
    """
    from conscio.checkpoint import CompactionCheckpoint, CheckpointChain

    chain = CheckpointChain(
        engine.storage / "checkpoints.db",
        consolidate_every=5,
    )

    # Build artifacts from engine state
    recent_events = engine.event_bus.query(limit=50)
    event_summaries = [
        f"{e.type}: {e.data.get('action', e.data.get('reason', ''))}"
        for e in recent_events[:20]
    ]

    cp = CompactionCheckpoint(
        durable_memory="\n".join(event_summaries),
        execution_summary=f"Phase: {compact_result.get('suggested_phase', 'unknown')}\n"
                         f"Pressure: {compact_result.get('token_pressure', 0)}\n"
                         f"Keep: {', '.join(compact_result.get('keep', []))}\n"
                         f"Drop: {', '.join(compact_result.get('drop', []))}",
        user_requirements="",  # preserved verbatim from host events
        skill_references=[],
    )

    cid = chain.append(cp)
    engine.event_bus.emit(
        "harness:checkpoint", "consciousness",
        {"checkpoint_id": cid, "byte_hash": cp.byte_hash},
    )

    return {
        "checkpoint_id": cid,
        "byte_hash": cp.byte_hash,
        "chain_length": chain.length(),
    }


def _detect_phase(engine: ConsciousnessEngine) -> str:
    """Auto-detect current workflow phase from EventBus."""
    recent = engine.event_bus.query(type="host:event", limit=20)
    for ev in recent:
        text = ev.data.get("text", "")
        payload = ev.data.get("payload", {})
        if isinstance(payload, dict):
            text = payload.get("text", text)
        text_lower = text.lower()
        if any(kw in text_lower for kw in ("implement", "code", "build", "execute")):
            return "execution"
        if any(kw in text_lower for kw in ("explore", "research", "investigate", "read")):
            return "exploration"
        if any(kw in text_lower for kw in ("done", "complete", "pass", "merged")):
            return "milestone"
        if any(kw in text_lower for kw in ("switch", "next", "move to", "pivoting")):
            return "shift"
    return "exploration"


def _compact_suggestions(phase: str) -> tuple[list[str], list[str]]:
    """Return keep/drop suggestions for a phase."""
    suggestions = {
        "exploration": (
            ["current task", "key findings", "decisions made"],
            ["raw search results", "intermediate calculations", "failed approaches"],
        ),
        "execution": (
            ["current task", "acceptance criteria", "pending decisions", "architecture decisions"],
            ["exploration logs", "alternative approaches considered", "debugging traces"],
        ),
        "milestone": (
            ["completed outcomes", "lessons learned", "pending decisions"],
            ["intermediate steps", "debugging history", "rejected alternatives"],
        ),
        "shift": (
            ["active context", "pending tasks", "key decisions"],
            ["previous task details", "completed work artifacts"],
        ),
    }
    return suggestions.get(phase, suggestions["exploration"])


# ── 5. ledger ─────────────────────────────────────────────────────────

def ledger(
    engine: ConsciousnessEngine,
    *,
    action: str = "record",
    rollout_id: str | None = None,
    candidates: list[dict] | None = None,
    fresh_info: str = "",
    search_space_size: int = 0,
    marks: dict | None = None,
    prior_winner: str = "",
    coherence_mark: dict | None = None,
) -> dict:
    """Record, query, or promote entries in the recursive decision ledger.

    Each rollout records candidates, marks, and coherence against prior
    winners. Promotion from paper → dry_run → live requires coherence
    evidence and explicit accept marks.

    Args:
        engine: active ConsciousnessEngine instance.
        action: "record" | "query" | "promote".
        rollout_id: identifier (auto-generated for record).
        candidates: list of candidate dicts with "id" and "description".
        fresh_info: new information ingested this rollout.
        search_space_size: estimated size of the search space.
        marks: {candidate_id: "accept"|"watch"|"reject"|"decay"|"replay"}.
        prior_winner: ID of the previously accepted winner.
        coherence_mark: override coherence calculation.

    Returns:
        Dict with rollout details, coherence mark, and promotion gate status.
    """
    _check_closed(engine)

    if action == "query":
        return _ledger_query(engine)
    elif action == "promote":
        return _ledger_promote(engine, rollout_id=rollout_id)
    elif action == "record":
        return _ledger_record(
            engine, rollout_id=rollout_id, candidates=candidates,
            fresh_info=fresh_info, search_space_size=search_space_size,
            marks=marks, prior_winner=prior_winner,
            coherence_mark=coherence_mark,
        )
    else:
        return {"error": f"unknown action '{action}'. Use record, query, or promote."}


def _ledger_record(
    engine: ConsciousnessEngine,
    *,
    rollout_id: str | None,
    candidates: list[dict] | None,
    fresh_info: str,
    search_space_size: int,
    marks: dict | None,
    prior_winner: str,
    coherence_mark: dict | None,
) -> dict:
    """Record a new ledger entry."""
    if not rollout_id:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        rollout_id = f"RL-{ts}-{secrets.token_hex(3)}"

    candidates = candidates or []
    marks = marks or {}

    # Compute coherence mark
    if coherence_mark is None:
        coherence_mark = _compute_coherence(engine, candidates, prior_winner)

    # Determine promotion gate

    # New entries default to "paper" gate
    promotion_gate = "paper"

    result = {
        "rollout_id": rollout_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "prior_winner": prior_winner,
        "fresh_info": fresh_info,
        "search_space_size": search_space_size,
        "candidates": candidates,
        "marks": marks,
        "coherence_mark": coherence_mark,
        "promotion_gate": promotion_gate,
    }

    engine.event_bus.emit("pipeline:ledger", "consciousness", result)
    return result


def _ledger_query(engine: ConsciousnessEngine) -> dict:
    """Query recent ledger entries."""
    entries = []
    ledger_events = engine.event_bus.query(type="pipeline:ledger", limit=20)
    for ev in ledger_events:
        entries.append(ev.data)

    return {"entries": entries, "total": len(entries)}


def _ledger_promote(
    engine: ConsciousnessEngine,
    *,
    rollout_id: str | None = None,
) -> dict:
    """Promote a ledger entry to the next gate level."""
    if not rollout_id:
        return {"error": "rollout_id is required for promote action"}

    # Find the entry
    ledger_events = engine.event_bus.query(type="pipeline:ledger", limit=50)
    entry = None
    for ev in ledger_events:
        if ev.data.get("rollout_id") == rollout_id:
            entry = ev.data
            break

    if not entry:
        return {"error": f"rollout_id '{rollout_id}' not found"}

    current_gate = entry.get("promotion_gate", "paper")
    coherence = entry.get("coherence_mark", {})

    # Determine new gate
    if current_gate == "paper":
        if coherence.get("ensemble_matches_prior", False):
            new_gate = "dry_run"
            reason = "ensemble matches prior winner — promoting to dry_run"
        else:
            return {
                "rollout_id": rollout_id,
                "previous_gate": current_gate,
                "new_gate": current_gate,
                "allowed": False,
                "reason": "ensemble does not match prior — promotion blocked",
            }
    elif current_gate == "dry_run":
        if (coherence.get("live_promotion_allowed", False)
                and "accept" in str(entry.get("marks", {}).values())):
            new_gate = "live"
            reason = "full coherence + explicit accept — promoting to live"
        else:
            return {
                "rollout_id": rollout_id,
                "previous_gate": current_gate,
                "new_gate": current_gate,
                "allowed": False,
                "reason": "live_promotion_allowed=False or no accept mark — promotion blocked",
            }
    else:
        return {
            "rollout_id": rollout_id,
            "previous_gate": current_gate,
            "new_gate": current_gate,
            "allowed": False,
            "reason": "already at live gate — no further promotion",
        }

    # Emit updated entry
    entry["promotion_gate"] = new_gate
    engine.event_bus.emit("pipeline:ledger", "consciousness", entry)

    return {
        "rollout_id": rollout_id,
        "previous_gate": current_gate,
        "new_gate": new_gate,
        "allowed": True,
        "reason": reason,
    }


def _compute_coherence(
    engine: ConsciousnessEngine,
    candidates: list[dict],
    prior_winner: str,
) -> dict:
    """Compute coherence mark against prior winners."""
    # Get previous ledger entries for comparison
    ledger_events = engine.event_bus.query(type="pipeline:ledger", limit=10)
    last_winner = ""
    for ev in ledger_events:
        last_winner = ev.data.get("prior_winner", last_winner)
        # The most recent entry's current winner is the comparison baseline
        prev_candidates = ev.data.get("candidates", [])
        prev_marks = ev.data.get("marks", {})
        last_winner = _current_winner(prev_candidates, prev_marks) or last_winner

    current_winner = _current_winner(candidates, {}) or ""

    ensemble_matches_prior = current_winner == prior_winner if prior_winner else False
    recursive_matches_prior = current_winner == last_winner if last_winner else False
    latest_rollout_match = len(candidates) > 0 and candidates[0].get("id", "") == current_winner

    # Check for stale data (no fresh info in last 3 entries)
    has_stale_data = False
    if len(ledger_events) >= 3:
        recent_fresh = sum(1 for ev in ledger_events[:3] if ev.data.get("fresh_info", ""))
        has_stale_data = recent_fresh == 0

    live_promotion_allowed = (
        ensemble_matches_prior
        and latest_rollout_match
        and not has_stale_data
    )

    return {
        "ensemble_matches_prior": ensemble_matches_prior,
        "recursive_matches_prior": recursive_matches_prior,
        "latest_rollout_match": latest_rollout_match,
        "live_promotion_allowed": live_promotion_allowed,
        "reason": (
            f"ensemble={ensemble_matches_prior}, recursive={recursive_matches_prior}, "
            f"latest_match={latest_rollout_match}, stale={has_stale_data}"
        ),
    }


def _current_winner(candidates: list[dict], marks: dict) -> str:
    """Determine the current winner from candidates and marks."""
    # First accepted candidate wins
    for cid, mark in marks.items():
        if mark == "accept":
            return cid
    # First candidate by default
    if candidates:
        return candidates[0].get("id", "")
    return ""
