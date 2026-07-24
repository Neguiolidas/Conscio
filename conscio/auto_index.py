"""auto_index — Automatic indexing of cognitive cycles into ContentStore (v3.3.1).

Hooks into ConsciousnessEngine cognitive cycles to index reflections, evaluations,
and governance events into the ContentStore for future recall.

Usage::
    from conscio.auto_index import AutoIndexer
    indexer = AutoIndexer(engine)
    indexer.install()  # patches engine reflect() to auto-index

Design:
- Non-invasive: wraps the existing ``_reflect_once`` method (monkey-patch at install time)
- Session-aware: each install() call starts a new session_id
- Minimal overhead: only indexes on cycles where there's meaningful content (ignores
  empty/boilerplate reflections)
- Dedup via ContentStore built-in content_hash
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .engine import ConsciousnessEngine

log = logging.getLogger("conscio.auto_index")


class AutoIndexer:
    """Patches engine lifecycle to auto-index cognitive output into ContentStore.

    When installed, every ``_reflect_once()`` call automatically indexes its
    output into the ContentStore under category ``reflection``, and triggers
    the KnowledgeGraph builder incrementally.

    The patch is reversible via ``uninstall()``.
    """

    def __init__(self, engine: ConsciousnessEngine, kg_builder=None):
        self.engine = engine
        self._kg_builder = kg_builder
        self._original_reflect = None
        self._original_cognitive_cycle = None
        self._session_label = f"session_{datetime.now(tz=timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        self._cycle_count = 0
        self._installed = False

    # ── Install / Uninstall ──────────────────────────────────────────

    def install(self) -> None:
        """Monkey-patch engine._reflect_once to auto-index."""
        if self._installed:
            return

        if not hasattr(self.engine, "_reflect_once"):
            log.warning("engine has no _reflect_once, cannot auto-index")
            return

        self._original_reflect = self.engine._reflect_once
        self._original_cognitive_cycle = getattr(self.engine, "cognitive_cycle", None)

        def _patched_reflect(world_state="", recent_events=None, confidence=0.5, anomalies=None):
            result = self._original_reflect(world_state, recent_events, confidence, anomalies)  # type: ignore[misc]
            self._on_reflect(result, world_state, anomalies or [])
            return result

        self.engine._reflect_once = _patched_reflect  # type: ignore[method-assign]

        # Also patch cognitive_cycle if available (for evaluate output)
        if self._original_cognitive_cycle:
            orig_cycle = self._original_cognitive_cycle

            def _patched_cycle(world_state="", budget=None, thought=None):
                result = orig_cycle(world_state, budget, thought)  # type: ignore[misc]
                self._on_cycle(result, world_state)
                return result

            self.engine.cognitive_cycle = _patched_cycle  # type: ignore[method-assign]

        self._installed = True
        log.info(
            "auto_index installed (session=%s, kg_builder=%s)",
            self._session_label,
            self._kg_builder is not None,
        )

    def uninstall(self) -> None:
        """Restore original methods."""
        if not self._installed:
            return
        if self._original_reflect:
            self.engine._reflect_once = self._original_reflect
        if self._original_cognitive_cycle and self._original_cognitive_cycle is not self.engine.cognitive_cycle:
            self.engine.cognitive_cycle = self._original_cognitive_cycle
        self._installed = False
        log.info("auto_index uninstalled")

    # ── Indexers ─────────────────────────────────────────────────────

    def _on_reflect(self, result: dict, world_state: str, anomalies: list[str]) -> None:
        """Index reflection output."""
        self._cycle_count += 1

        # Build the content to index
        content_parts: list[str] = []

        if world_state and len(world_state.strip()) > 50:
            content_parts.append(f"## World State\n\n{world_state.strip()}")

        if anomalies:
            content_parts.append("## Anomalies Detected\n\n" + "\n".join(f"- {a}" for a in anomalies))

        # Add reflection fields
        reflection_text = result.get("reflection", "") or result.get("text", "") or ""
        if reflection_text and len(reflection_text.strip()) > 20:
            content_parts.append(f"## Reflection\n\n{reflection_text.strip()}")

        if not content_parts:
            return  # nothing meaningful to index

        content = "\n\n".join(content_parts)
        label = f"{self._session_label}_cycle{self._cycle_count}"

        self._index(label, content, reflection_text)
        self._run_kg_builder()

    def _on_cycle(self, result: Any, world_state: str) -> None:
        """Index cognitive_cycle output (includes evaluate, advisory, etc)."""
        if hasattr(result, "reports") and result.reports:
            for i, report in enumerate(result.reports):
                report_text = str(report) if not hasattr(report, "to_dict") else str(report.to_dict())
                if len(report_text) > 50:
                    label = f"{self._session_label}_report{self._cycle_count}_{i}"
                    self._index(label, report_text, report_text)
        self._run_kg_builder()

    def _index(self, label: str, content: str, metadata: str) -> None:
        """Index content into ContentStore."""
        if not hasattr(self.engine, "content_store") or self.engine.content_store is None:
            log.debug("no content_store, skipping index of %s", label)
            return

        try:
            source_id = self.engine.content_store.index(
                label=label,
                content=content,
                category="reflection",
                content_type="prose",
                session_id=f"auto_index:{self._session_label}",
            )
            if self._cycle_count % 10 == 0:
                log.info("indexed cycle %d: source_id=%s label=%s (%d chars)", self._cycle_count, source_id, label, len(content))
        except Exception as e:
            log.warning("index failed for %s: %s", label, e)

    def _run_kg_builder(self) -> None:
        """Run KG builder incrementally after indexing."""
        if self._kg_builder is None:
            return
        try:
            # Only run every 10 cycles to avoid overhead
            if self._cycle_count % 10 == 0:
                result = self._kg_builder.run(limit=200)
                if result["entities_added"] > 0 or result["triples_added"] > 0:
                    log.info("kg_builder: %d entities, %d triples added", result["entities_added"], result["triples_added"])
        except Exception as e:
            log.debug("kg_builder run failed: %s", e)