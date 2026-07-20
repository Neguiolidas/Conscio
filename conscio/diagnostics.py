"""Diagnostics module — context auditing, eval harness, and rule distillation (v3.0).

All functions are deterministic, stdlib-only, and advisory. They inspect
engine state and EventBus history, emit diagnostic events, and return dicts.

Tools:
    context_budget — audit token consumption across components
    eval_harness — formal evaluation framework with pass@k metrics
    rules_distill — extract cross-cutting principles from skills/events
"""

from __future__ import annotations

import math
from collections import Counter
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .engine import ConsciousnessEngine


# ── Shared guard ──────────────────────────────────────────────────────

def _check_closed(engine: ConsciousnessEngine) -> None:
    """Raise RuntimeError if engine has been closed."""
    if getattr(engine, "_closed", False):
        raise RuntimeError("Cannot call diagnostic tool on a closed engine")


# ── 1. context_budget ────────────────────────────────────────────────

def context_budget(
    engine: ConsciousnessEngine,
    *,
    context_tokens: int = 0,
    context_window: int = 0,
    detail: str = "summary",  # "summary" | "full"
) -> dict:
    """Audit context window consumption and surface optimizations.

    Analyzes token usage across components (EventBus categories, token sources,
    metabolic tiers) and identifies bloat, redundancy, and savings opportunities.

    Args:
        engine: active ConsciousnessEngine instance.
        context_tokens: override current token count. 0 = auto-detect.
        context_window: override context window size. 0 = auto-detect.
        detail: "summary" (top-level) or "full" (per-source breakdown).

    Returns:
        Dict with usage breakdown, pressure, recommendations, and savings.
    """
    _check_closed(engine)

    # Token pressure
    if context_tokens <= 0:
        gain = engine.token_tracker.gain(hours=24)
        context_tokens = gain.get("total_raw", 0)

    if context_window <= 0:
        state = engine.state
        context_window = state.total_tokens_approx() if hasattr(state, "total_tokens_approx") else 200000

    token_pressure = round(context_tokens / context_window, 4) if context_window > 0 else 0.0

    # Per-source breakdown from token tracker
    gain = engine.token_tracker.gain(hours=24)
    by_source = gain.get("by_source", {})
    total_saved = gain.get("total_saved", 0)
    saving_pct = gain.get("saving_pct", 0.0)

    # Event category breakdown
    event_categories: dict[str, int] = Counter()
    for cat in ("system", "trading", "consciousness", "external", "session"):
        events = engine.event_bus.query(category=cat, limit=1000)
        event_categories[cat] = len(events)

    # Metabolic tier sizes
    metabolic_tiers = {
        "vital": len(engine.event_bus.query(type="feed", limit=1000)),
        "active": len(engine.event_bus.query(type="reflection", limit=1000)),
        "background": len(engine.event_bus.query(limit=1000))
            - len(engine.event_bus.query(type="feed", limit=1000))
            - len(engine.event_bus.query(type="reflection", limit=1000)),
    }

    # Identify bloat
    recommendations: list[dict] = []
    if token_pressure > 0.8:
        recommendations.append({
            "priority": "critical",
            "action": "Compact context immediately — pressure above 80%",
            "estimated_savings": int(context_tokens * 0.3),
        })
    if saving_pct < 10.0 and total_saved > 0:
        recommendations.append({
            "priority": "medium",
            "action": "Review output filter settings — low savings percentage",
            "estimated_savings": int(context_tokens * 0.1),
        })
    if event_categories.get("external", 0) > 100:
        recommendations.append({
            "priority": "low",
            "action": "External events exceed 100 — consider periodic cleanup",
            "estimated_savings": 0,
        })

    result: dict = {
        "token_pressure": token_pressure,
        "context_tokens": context_tokens,
        "context_window": context_window,
        "total_saved_tokens": total_saved,
        "saving_pct": saving_pct,
        "event_categories": dict(event_categories),
        "metabolic_tiers": metabolic_tiers,
        "recommendations": recommendations,
        "headroom_pct": round(max(0, 1.0 - token_pressure) * 100, 2),
    }

    if detail == "full":
        result["source_breakdown"] = by_source

    engine.event_bus.emit("diagnostic:budget", "consciousness", result)
    return result


# ── 2. eval_harness ──────────────────────────────────────────────────

# Eval types
EVAL_CAPABILITY = "capability"
EVAL_REGRESSION = "regression"
EVAL_BENCHMARK = "benchmark"

# Pass@k computation
def _pass_at_k(results: list[bool], k: int = 1) -> float:
    """Compute pass@k metric.

    Given N total trials and C correct ones, pass@k is the probability
    of at least one correct in k random samples:
    pass@k = 1 - C(N-C, k) / C(N, k)
    """
    n = len(results)
    c = sum(results)
    if n == 0:
        return 0.0
    if k > n:
        k = n
    if c == 0:
        return 0.0
    if n - c < k:
        return 1.0
    # Log-space computation to avoid overflow
    try:
        log_num = sum(math.log(n - c - i) for i in range(k))
        log_den = sum(math.log(n - i) for i in range(k))
        return round(1.0 - math.exp(log_num - log_den), 4)
    except (ValueError, OverflowError):
        return 1.0 if c > 0 else 0.0


def eval_harness(
    engine: ConsciousnessEngine,
    *,
    action: str = "define",  # "define" | "run" | "report"
    eval_id: str | None = None,
    eval_type: str = EVAL_CAPABILITY,
    task: str = "",
    criteria: list[str] | None = None,
    results: list[bool] | None = None,
    k_values: list[int] | None = None,
) -> dict:
    """Formal evaluation framework with pass@k metrics.

    Define evals, record results, and compute reliability metrics.
    Implements eval-driven development (EDD) principles.

    Args:
        engine: active ConsciousnessEngine instance.
        action: "define" (create eval), "run" (record results), "report" (summary).
        eval_id: identifier. Auto-generated for "define".
        eval_type: "capability" | "regression" | "benchmark".
        task: description of what the eval tests.
        criteria: list of success criteria strings.
        results: list of True/False results for "run" action.
        k_values: k values for pass@k computation. Default [1, 5, 10].

    Returns:
        Dict with eval details, results, and metrics.
    """
    _check_closed(engine)

    if action == "define":
        return _eval_define(engine, eval_id=eval_id, eval_type=eval_type,
                            task=task, criteria=criteria)
    elif action == "run":
        return _eval_run(engine, eval_id=eval_id, results=results,
                         k_values=k_values)
    elif action == "report":
        return _eval_report(engine, k_values=k_values)
    else:
        return {"error": f"unknown action '{action}'. Use define, run, or report."}


def _eval_define(
    engine: ConsciousnessEngine,
    *,
    eval_id: str | None,
    eval_type: str,
    task: str,
    criteria: list[str] | None,
) -> dict:
    """Define a new eval."""
    import secrets
    if not eval_id:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        eval_id = f"EVAL-{ts}-{secrets.token_hex(3)}"

    if eval_type not in (EVAL_CAPABILITY, EVAL_REGRESSION, EVAL_BENCHMARK):
        eval_type = EVAL_CAPABILITY

    result = {
        "eval_id": eval_id,
        "type": eval_type,
        "task": task or "unspecified",
        "criteria": criteria or [],
        "defined_at": datetime.now(timezone.utc).isoformat(),
        "status": "defined",
    }

    engine.event_bus.emit("diagnostic:eval", "consciousness", result)
    return result


def _eval_run(
    engine: ConsciousnessEngine,
    *,
    eval_id: str | None,
    results: list[bool] | None,
    k_values: list[int] | None,
) -> dict:
    """Record eval results and compute pass@k metrics."""
    if not eval_id:
        return {"error": "eval_id is required for run action"}

    results = results or []
    k_values = k_values or [1, 5, 10]

    # Compute metrics
    n = len(results)
    c = sum(results)
    pass_rate = round(c / n, 4) if n > 0 else 0.0

    pass_at_k = {}
    for k in k_values:
        pass_at_k[f"pass@{k}"] = _pass_at_k(results, k)

    result = {
        "eval_id": eval_id,
        "status": "completed",
        "total_trials": n,
        "passed": c,
        "failed": n - c,
        "pass_rate": pass_rate,
        "pass_at_k": pass_at_k,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }

    engine.event_bus.emit("diagnostic:eval", "consciousness", result)
    return result


def _eval_report(
    engine: ConsciousnessEngine,
    *,
    k_values: list[int] | None,
) -> dict:
    """Aggregate report of all eval results."""
    k_values = k_values or [1, 5, 10]
    eval_events = engine.event_bus.query(type="diagnostic:eval", limit=50)

    defined: list[dict] = []
    completed: list[dict] = []

    for ev in eval_events:
        if ev.data.get("status") == "defined":
            defined.append(ev.data)
        elif ev.data.get("status") == "completed":
            completed.append(ev.data)

    # Aggregate metrics
    all_results: list[bool] = []
    for comp in completed:
        # Reconstruct pass/fail from counts
        n = comp.get("total_trials", 0)
        c = comp.get("passed", 0)
        all_results.extend([True] * c + [False] * (n - c))

    aggregate: dict[str, int | float] = {}
    if all_results:
        aggregate["total_trials"] = len(all_results)
        aggregate["total_passed"] = sum(all_results)
        aggregate["overall_pass_rate"] = round(sum(all_results) / len(all_results), 4)
        for k in k_values:
            aggregate[f"pass@{k}"] = _pass_at_k(all_results, k)

    return {
        "defined_count": len(defined),
        "completed_count": len(completed),
        "aggregate": aggregate,
    }


# ── 3. rules_distill ─────────────────────────────────────────────────

def rules_distill(
    engine: ConsciousnessEngine,
    *,
    action: str = "scan",  # "scan" | "distill" | "list"
    source_types: list[str] | None = None,  # ["skills", "events", "decisions"]
    min_occurrences: int = 2,
    rule_text: str = "",
    rule_id: str | None = None,
) -> dict:
    """Extract cross-cutting principles from skills, events, and decisions.

    Phase 1 (scan): inventory all sources and find repeated patterns.
    Phase 2 (distill): create or update a distilled rule.
    Phase 3 (list): show all distilled rules.

    Args:
        engine: active ConsciousnessEngine instance.
        action: "scan" | "distill" | "list".
        source_types: which sources to scan. Default: all.
        min_occurrences: minimum pattern occurrences to consider a rule.
        rule_text: the distilled principle text (for "distill" action).
        rule_id: identifier for update (auto-generated for new).

    Returns:
        Dict with scan results, distilled rule, or rules list.
    """
    _check_closed(engine)

    if source_types is None:
        source_types = ["skills", "events", "decisions"]

    if action == "scan":
        return _rules_scan(engine, source_types=source_types,
                          min_occurrences=min_occurrences)
    elif action == "distill":
        return _rules_distill(engine, rule_text=rule_text, rule_id=rule_id,
                              source_types=source_types)
    elif action == "list":
        return _rules_list(engine)
    else:
        return {"error": f"unknown action '{action}'. Use scan, distill, or list."}


def _rules_scan(
    engine: ConsciousnessEngine,
    *,
    source_types: list[str],
    min_occurrences: int,
) -> dict:
    """Scan sources for repeated patterns."""
    patterns: dict[str, int] = Counter()

    # Scan skills (via EventBus host:event entries about skills)
    if "skills" in source_types:
        skill_events = engine.event_bus.query(type="host:event", limit=100)
        for ev in skill_events:
            text = ev.data.get("text", "")
            payload = ev.data.get("payload", {})
            if isinstance(payload, dict):
                text = payload.get("text", text)
            # Extract action verbs as patterns
            words = text.lower().split()
            for w in words:
                if len(w) > 5 and w.isalpha():
                    patterns[w] += 1

    # Scan events for repeated types
    if "events" in source_types:
        all_events = engine.event_bus.query(limit=500)
        type_counts: dict[str, int] = Counter()
        for ev in all_events:
            type_counts[ev.type] += 1
        for etype, count in type_counts.items():
            if count >= min_occurrences:
                patterns[f"event:{etype}"] = count

    # Scan ADR decisions
    if "decisions" in source_types:
        adr_events = engine.event_bus.query(type="adr:accepted", limit=50)
        adr_proposed = engine.event_bus.query(type="adr:proposed", limit=50)
        patterns["adr:accepted"] = len(adr_events)
        patterns["adr:proposed"] = len(adr_proposed)

    # Filter by min_occurrences
    significant = {k: v for k, v in patterns.items() if v >= min_occurrences}

    return {
        "total_patterns": len(patterns),
        "significant_patterns": len(significant),
        "patterns": dict(sorted(significant.items(), key=lambda x: -x[1])[:20]),
        "source_types": source_types,
        "min_occurrences": min_occurrences,
    }


def _rules_distill(
    engine: ConsciousnessEngine,
    *,
    rule_text: str,
    rule_id: str | None,
    source_types: list[str],
) -> dict:
    """Create or update a distilled rule."""
    import secrets

    if not rule_text:
        return {"error": "rule_text is required for distill action"}

    if not rule_id:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        rule_id = f"RULE-{ts}-{secrets.token_hex(3)}"

    result = {
        "rule_id": rule_id,
        "text": rule_text,
        "source_types": source_types,
        "distilled_at": datetime.now(timezone.utc).isoformat(),
        "status": "active",
    }

    engine.event_bus.emit("diagnostic:rule", "consciousness", result)
    return result


def _rules_list(engine: ConsciousnessEngine) -> dict:
    """List all distilled rules."""
    rule_events = engine.event_bus.query(type="diagnostic:rule", limit=50)
    rules = [ev.data for ev in rule_events]

    return {
        "total": len(rules),
        "rules": rules,
    }
