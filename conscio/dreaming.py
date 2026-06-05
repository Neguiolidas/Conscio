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

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass
class DreamReport:
    """Summary of a single dream cycle."""
    events_purged: int = 0
    events_compacted: int = 0
    entities_pruned: list[str] = field(default_factory=list)
    reflections_consolidated: int = 0
    dry_run: bool = False
    duration_ms: float = 0.0

    def to_dict(self) -> dict:
        return {
            "events_purged": self.events_purged,
            "events_compacted": self.events_compacted,
            "entities_pruned": self.entities_pruned,
            "reflections_consolidated": self.reflections_consolidated,
            "dry_run": self.dry_run,
            "duration_ms": round(self.duration_ms, 1),
        }


class DreamCycle:
    """Consolidation pass over EventBus, WorldModel, and ContentStore."""

    def __init__(
        self,
        prune_min_relevance: float = 0.2,
        prune_max_age_hours: int = 168,
        compact_before_days: int = 30,
        crystallize_after_days: int = 14,
        crystallize_min_count: int = 20,
    ):
        self.prune_min_relevance = prune_min_relevance
        self.prune_max_age_hours = prune_max_age_hours
        self.compact_before_days = compact_before_days
        self.crystallize_after_days = crystallize_after_days
        self.crystallize_min_count = crystallize_min_count

    def run(self, engine, dry_run: bool = False) -> DreamReport:
        """Execute Release → Prune → Crystallize. Returns a DreamReport."""
        start = time.perf_counter()
        report = DreamReport(dry_run=dry_run)

        # ── Release: dissolve duplicate/trivial event noise ──
        report.events_purged = engine.event_bus.purge_duplicates(dry_run=dry_run)
        if not dry_run:
            report.events_compacted = engine.event_bus.compact(
                before_days=self.compact_before_days
            )

        # ── Prune: remove faded world-model entities ──
        report.entities_pruned = engine.world.prune_stale(
            min_relevance=self.prune_min_relevance,
            max_age_hours=self.prune_max_age_hours,
            dry_run=dry_run,
        )

        # ── Crystallize: compress old reflections into one summary ──
        report.reflections_consolidated = self._crystallize(engine, dry_run=dry_run)

        report.duration_ms = (time.perf_counter() - start) * 1000.0
        return report

    def _crystallize(self, engine, dry_run: bool) -> int:
        """
        Consolidate reflection sources older than crystallize_after_days into
        a single 'consciousness' summary, then delete the originals.

        Append-only safety (rule 4): the summary is indexed FIRST; only after
        it is committed are the original reflections deleted. A reflection is
        never edited in place.
        """
        store = engine.content_store
        cutoff = (datetime.utcnow() - timedelta(days=self.crystallize_after_days)).isoformat()

        old = store.db.execute(
            "SELECT id, label FROM sources "
            "WHERE source_category='reflection' AND indexed_at < ? "
            "ORDER BY indexed_at ASC",
            (cutoff,),
        ).fetchall()

        if len(old) < self.crystallize_min_count:
            return 0
        if dry_run:
            return len(old)

        # Build the consolidated summary from each old reflection's chunks.
        parts: list[str] = []
        for row in old:
            chunks = store.get_by_source(row["id"])
            text = " ".join(c.content for c in chunks).strip()
            if text:
                parts.append(f"- [{row['label']}] {text[:280]}")
        summary_body = (
            f"# Crystallized reflections (n={len(old)}, before {cutoff[:10]})\n\n"
            + "\n".join(parts)
        )

        # Index the summary FIRST (commit), then delete originals. Capture the
        # summary's source_id: ContentStore.index dedups by content_hash and may
        # return an existing id — never delete that id, or we'd lose the summary.
        summary_id = store.index(
            label=f"dream_crystal_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}",
            content=summary_body,
            category="consciousness",
        )
        for row in old:
            if row["id"] != summary_id:
                store.delete_source(row["id"])

        return len(old)
