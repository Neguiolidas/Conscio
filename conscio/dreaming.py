"""
DreamCycle — consolidation orchestrator (Noosphere "Dreaming").

Maps to the Noosphere Dream Protocol:
  - Release    → dissolve noise   (EventBus purge_duplicates + compact)
  - Prune      → let go the faded (WorldModel prune_stale)
  - Crystallize→ compress patterns (ContentStore: old reflections → 1 summary)

Runs on-demand (engine.dream()), on session handoff (Mitosis), or via cron.
Never runs per-reflect — that keeps the reflection hot path cheap.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .coherence import _relations_contradict


MIN_ENTITY_MATCH_LEN = 3   # friction ignores 1-2 char entity names (would over-defer)


@dataclass(frozen=True)
class DreamRecommendation:
    """Advisory signal: coherence dropped, a dream would help. Named fields
    (not a tuple) for readable access, mirroring CoherenceReport."""
    recommended: bool
    dominant: str | None = None   # dominant dissonance dimension
    score: float | None = None    # coherence score that triggered it

    def marker(self) -> str:
        """Heartbeat/state marker text; '' when not recommended."""
        if not self.recommended:
            return ""
        if self.dominant:
            tail = f" {self.score:.2f}" if self.score is not None else ""
            return f"recommended ({self.dominant}{tail})"
        return "recommended"


def _lexically_contradicted_entities(world) -> list[str]:
    """`from`-entities whose relations on the same (from→to) pair lexically
    contradict (one negated, same core).

    v0.7 TECH DEBT: reads world._data directly, consistent with the v0.6
    ontological scan. v0.8 replaces this with world.contradicted_entities()
    (semantic, cached). Reuses coherence._relations_contradict — single source
    of truth for negation tokens.
    """
    try:
        relations = world._data.get("relations", [])
    except Exception:
        return []
    by_pair: dict = {}
    for r in relations:
        key = (r.get("from", ""), r.get("to", ""))
        by_pair.setdefault(key, []).append(r.get("relation", ""))
    contradicted: list[str] = []
    for (frm, _to), preds in by_pair.items():
        hit = any(
            _relations_contradict(preds[i], preds[j])
            for i in range(len(preds)) for j in range(i + 1, len(preds))
        )
        if hit and frm and frm not in contradicted:
            contradicted.append(frm)
    return contradicted


@dataclass
class DreamReport:
    """Summary of a single dream cycle."""
    events_purged: int = 0
    events_compacted: int = 0
    entities_pruned: list[str] = field(default_factory=list)
    reflections_consolidated: int = 0
    reflections_deferred: int = 0
    dry_run: bool = False
    duration_ms: float = 0.0
    coherence_before: float | None = None
    coherence_after: float | None = None
    contradictions_pruned: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "events_purged": self.events_purged,
            "events_compacted": self.events_compacted,
            "entities_pruned": self.entities_pruned,
            "reflections_consolidated": self.reflections_consolidated,
            "reflections_deferred": self.reflections_deferred,
            "dry_run": self.dry_run,
            "duration_ms": round(self.duration_ms, 1),
            "coherence_before": self.coherence_before,
            "coherence_after": self.coherence_after,
            "contradictions_pruned": self.contradictions_pruned,
        }


class DreamCycle:
    """Consolidation pass over EventBus, WorldModel, and ContentStore."""

    def __init__(
        self,
        prune_min_relevance: float = 0.2,        # deprecated (kept for back-compat)
        prune_max_age_hours: int = 168,          # deprecated (kept for back-compat)
        prune_entropy_threshold: float = 0.85,
        compact_before_days: int = 30,
        crystallize_after_days: int = 14,
        crystallize_min_count: int = 20,
    ):
        self.prune_min_relevance = prune_min_relevance
        self.prune_max_age_hours = prune_max_age_hours
        self.prune_entropy_threshold = prune_entropy_threshold
        self.compact_before_days = compact_before_days
        self.crystallize_after_days = crystallize_after_days
        self.crystallize_min_count = crystallize_min_count

    def run(self, engine, dry_run: bool = False) -> DreamReport:
        """Release → Prune → (ontological targeting) → Crystallize. Records the
        coherence delta the cycle produced; clears engine.dream_recommended."""
        start = time.perf_counter()
        report = DreamReport(dry_run=dry_run)

        # Coherence before — over the current recent-event window.
        recent = [e.to_dict() for e in engine.event_bus.query(limit=20)]
        report.coherence_before = engine.coherence.assess(recent).score

        # ── Release: dissolve duplicate/trivial event noise ──
        report.events_purged = engine.event_bus.purge_duplicates(dry_run=dry_run)
        if not dry_run:
            report.events_compacted = engine.event_bus.compact(
                before_days=self.compact_before_days
            )

        # ── Prune: entropy-based (connectivity can rescue old entities) ──
        report.entities_pruned = engine.world.prune_by_entropy(
            threshold=self.prune_entropy_threshold,
            dry_run=dry_run,
        )

        # ── Ontological targeting (v0.7): when the dominant dissonance is
        # ontological, prune entities with contradictory relations even if not
        # entropy-stale. v0.7 detector is lexical; v0.8 makes it semantic. ──
        last = getattr(engine, "last_coherence", None)
        dominant = last.dominant.dimension if (last and last.dominant) else None
        if dominant == "ontological":
            contradicted = _lexically_contradicted_entities(engine.world)
            report.contradictions_pruned = list(contradicted)
            if not dry_run:
                for name in contradicted:
                    engine.world.remove_entity(name)  # drops relations + saves

        # ── Friction + Crystallize: defer reflections the world moved on from ──
        consolidated, deferred = self._crystallize(
            engine, report.entities_pruned, dry_run=dry_run
        )
        report.reflections_consolidated = consolidated
        report.reflections_deferred = deferred

        # Coherence after — same window; reflects this cycle's mutations.
        report.coherence_after = engine.coherence.assess(recent).score

        # The dream addressed the recommendation — clear the advisory flag.
        if not dry_run:
            engine.dream_recommended = DreamRecommendation(False, None, None)

        report.duration_ms = (time.perf_counter() - start) * 1000.0
        return report

    def _friction(self, engine, candidates, pruned) -> set:
        """
        Return reflection source_ids to DEFER this cycle.

        A reflection is deferred if any entity that changed since (pruned this
        cycle, or state-changed in the last 24h) is mentioned in its text.
        Matching is whole-word and length-guarded — a raw substring match would
        let a 1-2 char entity name defer nearly everything (silent bug).
        """
        changed = {
            n for n in (set(pruned) | engine.world.recently_changed(24))
            if len(n) >= MIN_ENTITY_MATCH_LEN
        }
        if not changed:
            return set()

        patterns = [re.compile(rf"\b{re.escape(n)}\b", re.IGNORECASE) for n in changed]
        store = engine.content_store
        deferred: set = set()
        for row in candidates:
            chunks = store.get_by_source(row["id"])
            text = " ".join(c.content for c in chunks)
            if any(p.search(text) for p in patterns):
                deferred.add(row["id"])
        return deferred

    def _crystallize(self, engine, pruned, dry_run: bool) -> tuple:
        """
        Friction-gated crystallization. Returns (consolidated, deferred).

        Old reflections whose subject entities changed since are deferred (kept
        for a later cycle); the rest are compressed into one summary and deleted.

        Append-only safety (v0.3): index the summary FIRST, capture its id, and
        never delete that id.
        """
        store = engine.content_store
        cutoff = (datetime.utcnow() - timedelta(days=self.crystallize_after_days)).isoformat()

        old = store.db.execute(
            "SELECT id, label FROM sources "
            "WHERE source_category='reflection' AND indexed_at < ? "
            "ORDER BY indexed_at ASC",
            (cutoff,),
        ).fetchall()

        deferred = self._friction(engine, old, pruned)
        active = [row for row in old if row["id"] not in deferred]

        if len(active) < self.crystallize_min_count:
            return 0, len(deferred)
        if dry_run:
            return len(active), len(deferred)

        parts: list[str] = []
        for row in active:
            chunks = store.get_by_source(row["id"])
            text = " ".join(c.content for c in chunks).strip()
            if text:
                parts.append(f"- [{row['label']}] {text[:280]}")
        summary_body = (
            f"# Crystallized reflections (n={len(active)}, before {cutoff[:10]})\n\n"
            + "\n".join(parts)
        )

        summary_id = store.index(
            label=f"dream_crystal_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}",
            content=summary_body,
            category="consciousness",
        )
        for row in active:
            if row["id"] != summary_id:
                store.delete_source(row["id"])

        return len(active), len(deferred)
