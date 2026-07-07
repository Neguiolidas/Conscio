"""
ConsciousnessEngine — Orchestrates all consciousness modules.

The central coordinator that:
1. Detects the current model and context mode
2. Initializes all modules (Inner Monologue, World Model, Meta-Cognition, Goals, Evolution)
3. Runs the perception-reflection-generation loop
4. Manages state persistence and context injection
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

from .timeutil import naive_utcnow, naive_utc_from_epoch

from .context_manager import ContextManager
from .inner_monologue import InnerMonologue
from .world_model import WorldModel
from .meta_cognition import MetaCognition
from .goal_generator import GoalGenerator, Drive
from .auto_evolution import AutoEvolution
from .content_store import ContentStore
from .event_bus import EventBus
from .shard_engine import ShardEngine
from .coherence import CoherenceEngine, CoherenceReport, COHERENCE_EVENT_THRESHOLD
from .voice_preset import resolve_voice_preset
from .self_prompt import generate_self_prompts
from .dreaming import DreamRecommendation
from .semantic import SemanticEngine, ContradictionDetector
from .output_filter import build_pipeline_from_dict
from .token_tracker import TokenTracker
from .content_layer import ContentLayerManager
from .content_layer import _RAG_DISABLED as _RAG_DISABLED  # re-export (one sentinel)
from .session_lifecycle import SessionLifecycle
from .metabolic import MetabolicContext
from .session_rag_factory import create_session_rag
from .structural import (
    StructuralDistiller, StructuralSignal, render_structural, structural_budget,
    DEFAULT_MAX_BYTES, DEFAULT_MAX_NODES,
)
from .structural_drift import (
    StructuralDelta, StructuralDigest, StructuralDriftStore, StructuralFreshness,
    compute_delta, compute_freshness, drift_path,
)

if TYPE_CHECKING:
    from .dreaming import DreamReport
    from .session_lifecycle import SessionSummary

logger = logging.getLogger(__name__)


def _quarantine_if_corrupt(db_path: Path) -> Optional[Path]:
    """If ``db_path`` exists but is not a usable sqlite DB, move it aside so a fresh
    one is created — power-loss-mid-write or a garbage file must never crash
    construction (I-S4). The corrupt file (and any ``-wal``/``-shm``) is PRESERVED
    as ``<name>.corrupt-<ts>`` for forensics; only the newest few are kept (R-02
    prune — they no longer accumulate unbounded). Returns the quarantine path, or
    None when the DB is healthy / absent.
    """
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute("PRAGMA quick_check").fetchone()
        finally:
            conn.close()
        if row and row[0] == "ok":
            return None                              # healthy
    except sqlite3.DatabaseError:
        pass                                         # not a database / malformed
    stamp = naive_utcnow().strftime("%Y%m%d%H%M%S%f")
    dest = db_path.with_name(f"{db_path.name}.corrupt-{stamp}")
    db_path.rename(dest)                             # writable dir is a precondition
    for sidecar in ("-wal", "-shm"):
        side = db_path.with_name(db_path.name + sidecar)
        if side.exists():
            try:
                side.rename(dest.with_name(dest.name + sidecar))
            except OSError:
                pass                                 # best-effort sidecar move
    _prune_quarantine(db_path)                       # R-02: keep only newest few
    return dest


def _prune_quarantine(db_path: Path, keep: int = 3) -> None:
    """Keep only the newest ``keep`` quarantined ``<name>.corrupt-<ts>`` copies
    (R-02 — they used to accumulate forever). The ``-<ts>`` stamp is lexically
    sortable; each pruned main file's ``-wal``/``-shm`` sidecars go with it."""
    mains = sorted(
        (p for p in db_path.parent.glob(f"{db_path.name}.corrupt-*")
         if not p.name.endswith(("-wal", "-shm"))),
        key=lambda p: p.name, reverse=True)
    for stale in mains[keep:]:
        for path in (stale, stale.with_name(stale.name + "-wal"),
                     stale.with_name(stale.name + "-shm")):
            try:
                path.unlink()
            except OSError:
                pass

# RAG-disable sentinel is OWNED by content_layer and re-exported via the import
# above, so `from conscio.engine import _RAG_DISABLED` yields the SAME object the
# ContentLayerManager gate compares against (one sentinel, not two).


class ConsciousnessEngine:
    """
    The main orchestrator for the consciousness framework.
    
    Usage:
        engine = ConsciousnessEngine(model_name=os.environ.get("CONSCIO_MODEL", ""))

        # Run a reflection cycle
        result = engine.reflect(
            world_state="Trading bot operational, 3 active positions",
            recent_events=["BTC spiked 2%", "New anomaly detected in order book"],
        )
        
        # Get the state for context injection
        state = engine.get_state_for_injection()
        
        # Check pending evolution proposals
        proposals = engine.evolution.pending_proposals()
    """

    DEFAULT_STORAGE = Path.home() / ".hermes" / "consciousness"

    def __init__(
        self,
        model_name: str,
        context_window: Optional[int] = None,
        storage_path: Optional[str | Path] = None,
        drive_strengths: Optional[dict[str, float]] = None,
        voice_preset: Optional[str] = None,
        base_url: Optional[str] = None,
        autodetect: bool = False,
    ):
        self.storage = Path(storage_path) if storage_path else self.DEFAULT_STORAGE
        self.storage.mkdir(parents=True, exist_ok=True)

        # Detect model and set up context management
        self.ctx = ContextManager(model_name, context_window, self.storage,
                                    base_url=base_url, autodetect=autodetect)
        self.model_info = self.ctx.model_info
        self.mode = self.ctx.mode

        # Initialize modules
        self.monologue = InnerMonologue(self.ctx)
        self.world = WorldModel(self.storage)
        self.meta = MetaCognition(self.storage)

        # Convert drive strengths from string keys
        drives = None
        if drive_strengths:
            drives = {Drive(k): v for k, v in drive_strengths.items()}
        self.goals = GoalGenerator(self.storage, drives)

        self.evolution = AutoEvolution(self.storage)

        # --- v0.2: SQLite-backed modules (shared DB) ---
        db_path = self.storage / "conscio.db"
        # I-S4: a corrupt store (power-loss-mid-write, garbage file) must not crash
        # construction. Quarantine it (preserved on disk) and recreate fresh.
        quarantined = _quarantine_if_corrupt(db_path)
        self.event_bus = EventBus(db_path=db_path)
        if quarantined is not None:
            logger.warning(
                "conscio.db was corrupt; quarantined to %s and recreated fresh",
                quarantined)
            try:
                self.event_bus.emit(
                    type="system", category="system",
                    data={"event": "storage_recovered",
                          "quarantined": str(quarantined)})
            except Exception:                        # telemetry must never crash init
                pass
        self.shard_engine = ShardEngine(self.event_bus)
        self.coherence = CoherenceEngine(self.meta, self.world)
        # v0.8: shared semantic engine (lazy embedder) + contradiction detector.
        # Built once; reused by dream Reconcile and the opt-in output stage.
        self._semantic = SemanticEngine()
        self._contradiction_detector = ContradictionDetector(self._semantic)
        self.last_coherence: Optional[CoherenceReport] = None
        self.dream_recommended = DreamRecommendation(False, None, None)
        self.last_self_prompts: list = []

        # Voice preset (v0.6) — static marker. Precedence: param > env > default.
        effective_voice = (
            voice_preset if voice_preset is not None
            else os.getenv("CONSCIO_VOICE_PRESET", "coherence-style")
        )
        self.voice_preset = resolve_voice_preset(effective_voice)
        self.content_store = ContentStore(db_path=db_path)
        self.token_tracker = TokenTracker(db_path=db_path)
        self.output_filter = build_pipeline_from_dict({
            "stages": [
                {"strip_ansi": None},
                {"secret_mask": None},
                {"dedup_blocks": {"min_run": 3}},
                {"max_lines": {"max_lines": 200}},
                {"truncate_lines": {"max_width": 8000}},
            ]
        })

        # v0.8: opt-in semantic output dedup (annotate near-dups, never merge).
        # NOT in the default pipeline — the hot path must stay network-free.
        if os.getenv("CONSCIO_SEMANTIC_DEDUP", "").strip().lower() in ("1", "true", "yes", "on"):
            from .output_filter import SemanticDedup
            self.output_filter.add_stage(SemanticDedup(semantic=self._semantic))

        # v0.9: ContentLayerManager — unified content operations (recall, perceive)
        self.content_layer = ContentLayerManager(
            content_store=self.content_store,
            world_model=self.world,
            session_rag_provider=create_session_rag,
            )

        # v0.9: SessionLifecycle — unified session persistence hooks
        self.session_lifecycle = SessionLifecycle(engine=self)

        self._state = self.ctx.load_state()

        # v0.9+: External token usage tracking — set by the adapter/gateway
        # to report real session token consumption. Falls back to injection
        # size when unset (see context_manager.total_tokens_approx docstring).
        self.session_tokens_used: Optional[int] = None

        # v1.7: structural cognition — optional, opt-in. No graph is loaded by
        # default, so injection/lookup/advisory stay inert until the host calls
        # load_structure(). Keeps cognition (reflect()) entirely untouched.
        self._distiller: Optional[StructuralDistiller] = None
        self._structural_signal: Optional[StructuralSignal] = None
        # v1.8: structural drift — temporal awareness over the ingested snapshot.
        # Tracked only when load_structure() is given a workspace_id; the store is
        # built lazily and keyed per Workspace.id (the engine owns the watermark).
        self._structural_delta: Optional[StructuralDelta] = None
        self._structural_freshness: Optional[StructuralFreshness] = None
        self._drift_store: Optional[StructuralDriftStore] = None

    # --- Meta-Cognition → Goal Generator Feed ---

    def feed_meta_to_goals(self, meta: MetaCognition, goals: GoalGenerator) -> None:
        """
        Feed meta-cognition insights (blind spots, error patterns, confidence)
        into the goal generator to create improvement goals.
        """
        # Get existing active goal descriptions for deduplication
        active_descriptions = {g.description for g in goals.active_goals()}

        # Generate EVOLUTION goals from blind spots
        # GoalGenerator prefixes with "Evolve:" — match that for dedup
        for blind_spot in meta._data.get("blind_spots", []):
            expected_desc = f"Evolve: {blind_spot} \u2014 low confidence area"
            if expected_desc not in active_descriptions:
                # v1.6 (#7): blind-spot goals are meta-derived -> diagnostic-only.
                # Vague self-improvement ("Evolve: low confidence area") is not
                # actor-actionable; it stays visible (advisory/injection) but the
                # arbiter never auto-executes it. Generalizes the #6 slice.
                goals.generate_from_evolution(blind_spot,
                                              target="low confidence area",
                                              source="meta_error")
                active_descriptions.add(expected_desc)

        # v1.5.1 (#6): error patterns do NOT mint actor-executable goals.
        # Turning a recurring error (e.g. "act:tool:skeptic_fail") into a
        # "Maintenance: fix_recurring_error" goal made the actor execute it
        # literally (fs_read path="skeptic_fail") -> more errors -> lockdown loop.
        # Errors already flow to the diagnostic channels -- meta_cognition
        # storage, reflection, and AutoEvolution.observe_errors() (a reviewed
        # PATTERN_LEARN proposal queue, never the act pipeline) -- so the signal
        # is preserved without an executable goal. The full origin/provenance
        # gate (self_prompt, evolution, etc.) lands in v1.6.

        # Modulate drive strengths based on average confidence (capped at 1.0)
        avg_conf = meta.average_confidence()
        if avg_conf < 0.5:
            goals.drives[Drive.EVOLUTION] = min(1.0, goals.drives.get(Drive.EVOLUTION, 0.5) + 0.2)
        elif avg_conf > 0.8:
            goals.drives[Drive.CURIOSITY] = min(1.0, goals.drives.get(Drive.CURIOSITY, 0.7) + 0.2)

    # --- Main Loop ---

    def reflect(
        self,
        world_state: str = "",
        recent_events: Optional[list[str]] = None,
        confidence: float = 0.5,
        anomalies: Optional[list[str]] = None,
    ) -> dict:
        """
        Run a complete reflection cycle.
        
        1. PERCEIVE — read world state
        2. REFLECT — compare with predictions, assess confidence
        3. GENERATE — create/update goals based on drives
        4. PREDICT — update world model predictions
        5. SUMMARIZE — compress into state_summary
        
        Returns the reflection result dict.
        """
        anomalies = anomalies or []

        # Infer active cognitive shard from events at reflect-entry — BEFORE this
        # cycle emits its own reflection/anomaly events, so they don't pollute the
        # signal (advisory; never feeds drives/goals).
        recent = [e.to_dict() for e in self.event_bus.query(limit=20)]
        active_shard = self.shard_engine.update(recent)
        shard_value = active_shard.value if active_shard else ""

        # v0.6: coherence snapshot over the SAME pre-update window (advisory, pure).
        # Reuses `recent` so it never counts the shard transition it just caused.
        coherence_report = self.coherence.assess(recent)
        self.last_coherence = coherence_report
        if coherence_report.score < COHERENCE_EVENT_THRESHOLD:
            self.event_bus.emit(
                type="coherence:dissonance",
                category="consciousness",
                data={
                    "score": coherence_report.score,
                    "dominant": (
                        coherence_report.dominant.dimension
                        if coherence_report.dominant else None
                    ),
                    "dimensions": coherence_report.dimensions,
                },
                priority=7,
            )

        # v0.7: dream-recommended advisory flag (hot path: one assignment).
        if coherence_report.score < COHERENCE_EVENT_THRESHOLD:
            self.dream_recommended = DreamRecommendation(
                recommended=True,
                dominant=(coherence_report.dominant.dimension
                          if coherence_report.dominant else None),
                score=coherence_report.score,
            )
        else:
            self.dream_recommended = DreamRecommendation(False, None, None)

        # v0.7: self-prompting — pure introspection, then ONE bounded goal.
        self.last_self_prompts = generate_self_prompts(
            self.meta, self.world, coherence_report, recent
        )
        spawned = self._spawn_self_prompt_goal(self.last_self_prompts)

        # Generate goals from anomalies (curiosity drive)
        for anomaly in anomalies:
            self.goals.generate_from_curiosity(anomaly, context=world_state)

        # Generate maintenance goals for stale world model entities
        stale = self.world.stale_entities()
        if stale:
            self.goals.generate_from_maintenance(
                "prune_stale", f"{len(stale)} stale entities: {', '.join(stale[:3])}"
            )

        # Feed meta-cognition insights into goals BEFORE reflection
        self.feed_meta_to_goals(self.meta, self.goals)

        # Auto-observe errors and propose evolution fixes
        self.evolution.observe_errors(self.meta)

        # Score all goals with current MetaCognition state
        self.goals.score_all_goals(
            confidence=confidence,
            calibration=self.meta.calibration_score(),
        )

        # Cross-session recall: surface relevant past context (gap #3).
        recall_query = " ".join([world_state, *anomalies]).strip()
        past_context = self.recall(recall_query, k=3) if recall_query else []
        recent_events = list(recent_events or [])
        recent_events.extend(f"[recall] {s}" for s in past_context)

        # Run the inner monologue reflection
        result = self.monologue.reflect(
            world_state=world_state,
            recent_events=recent_events,
            confidence=confidence,
            anomalies=anomalies,
            goals_update=[g.description for g in self.goals.active_goals()],
        )

        # --- v0.4: Meta-reflect — advisory quality signal on this reflection ---
        error_rate = self.world.recent_prediction_error_rate(window_hours=24)
        anomaly_pen = min(0.1 * len(anomalies), 0.5)
        meta_confidence = max(
            0.0, min(1.0, confidence * (1.0 - error_rate) * (1.0 - anomaly_pen))
        )
        reflection_quality = (
            "HIGH" if meta_confidence >= 0.66
            else "MEDIUM" if meta_confidence >= 0.33
            else "LOW"
        )
        result["meta_confidence"] = round(meta_confidence, 3)
        result["reflection_quality"] = reflection_quality
        result["shard"] = shard_value
        result["coherence"] = coherence_report.score
        result["coherence_dimensions"] = coherence_report.dimensions
        result["dominant_dissonance"] = (
            coherence_report.dominant.dimension if coherence_report.dominant else None
        )
        result["self_prompts"] = [p.question for p in self.last_self_prompts]
        result["self_prompt_goal"] = spawned.id if spawned else None
        result["dream_recommended"] = self.dream_recommended.recommended

        # --- v0.2: Post-reflection pipeline ---
        raw_summary = result["summary"]
        filtered_summary = self.output_filter.apply(raw_summary)
        self.token_tracker.record(
            source="reflection",
            raw=raw_summary,
            filtered=filtered_summary,
        )

        # Index reflection into ContentStore for future search
        self.content_store.index(
            label=f"reflection_{naive_utcnow().strftime('%Y%m%d_%H%M')}",
            content=filtered_summary,
            category="reflection",
        )

        # Emit reflection event
        self.event_bus.emit(
            type="reflection",
            category="consciousness",
            data={
                "confidence": confidence,
                "anomalies": anomalies,
                "meta_confidence": round(meta_confidence, 3),
            },
        )

        # Emit anomaly events
        for anomaly in anomalies:
            self.event_bus.emit(
                type="anomaly",
                category="system",
                data={"description": anomaly},
                priority=8,
            )

        # Record confidence in meta-cognition
        self.meta.record_confidence("general", confidence)

        # Build the new consciousness state
        self._state = self.ctx.build_state(
            state_summary=filtered_summary,
            last_reflection=filtered_summary[:200],  # Compact version
            active_goals=[g.description for g in self.goals.active_goals()],
            world_model_snippet=self.world.query(world_state)[:100] if world_state else "",
            meta_cognition=self.meta.summary(),
            reflection_quality=reflection_quality,
            shard=shard_value,
            coherence=coherence_report.score,
            coherence_note=(
                coherence_report.dominant.dimension
                if coherence_report.dominant else ""
            ),
            voice=self.voice_preset,
            self_prompt=(self.last_self_prompts[0].question
                         if self.last_self_prompts else ""),
            dream_recommended=self.dream_recommended.marker(),
        )

        # v0.9: Metabolic wiring — assess context health and inject tier_action
        used_tokens = (self.session_tokens_used
                       if self.session_tokens_used is not None
                       else self._state.total_tokens_approx())
        total_window = self.model_info.context_window
        metabolic_state = MetabolicContext.assess(used_tokens, total_window)
        metabolic_note = f"{metabolic_state.value} {MetabolicContext.usage_pct(used_tokens, total_window):.0f}%"
        tier_action = MetabolicContext.tier_action(metabolic_state)
        self._state.metabolic = f"{metabolic_note} — {tier_action}"

        # Persist state
        self.ctx.save_state(self._state)

        return result

    def _spawn_self_prompt_goal(self, prompts):
        """Spawn ONE bounded goal from the highest-severity self-prompt, tagged
        source="self_prompt", returning the goal actually tracked in the store
        (or None when no prompt exists / the drive is too weak to spawn).

        GoalGenerator dedups by active description and caps active goals, so a
        repeated self-prompt is a no-op in the store. The generators, however,
        always return a freshly-built Goal even on a dedup no-op — and that object
        is NOT the persisted one (its id is never in `_goals`). So after generating
        we resolve the canonical active goal by description, ensuring
        result["self_prompt_goal"] carries a real stored id rather than a phantom
        from a discarded duplicate."""
        if not prompts:
            return None
        p = prompts[0]
        if p.drive == "maintenance":
            goal = self.goals.generate_from_maintenance(
                "self_prompt", p.target, source="self_prompt"
            )
        elif p.drive == "evolution":
            goal = self.goals.generate_from_evolution(
                p.question, target=p.target, source="self_prompt"
            )
        else:
            goal = self.goals.generate_from_curiosity(
                p.question, context=p.target, source="self_prompt"
            )
        if goal is None:
            return None
        # Resolve the canonical tracked goal — generators hand back a fresh
        # (possibly non-persisted) Goal even on a dedup no-op.
        for g in self.goals._goals:
            if g.status == "active" and g.description == goal.description:
                return g
        return goal

    @property
    def state(self):
        """Public read-only view of the current ConsciousnessState
        (the volition layer reads this; reflect() owns the writes)."""
        return self._state

    # ── v1.5 Awake Mode (R9): master gate for autonomous operation ──────
    @property
    def awake(self) -> bool:
        """Awake Mode (R9). True ⇒ self-initiated autonomy (the run()/daemon
        heartbeat) is allowed; False (default) ⇒ perceive + reflect() only.
        A direct human act() call is never gated by this — R9 governs the loop."""
        return bool(self._state.awake)

    def wake(self) -> None:
        """Enter Awake Mode (R9). Persists + emits awake:changed. Idempotent."""
        self._set_awake(True)

    def sleep(self) -> None:
        """Leave Awake Mode (R9). Persists + emits awake:changed. Idempotent."""
        self._set_awake(False)

    def _set_awake(self, value: bool) -> None:
        changed = bool(self._state.awake) != value
        self._state.awake = value
        self.ctx.save_state(self._state)        # rides the existing state store
        if changed:                             # auditable, no duplicate events
            self.event_bus.emit(
                type="awake:changed",
                category="consciousness",
                data={"awake": value},
            )

    def get_state_for_injection(self) -> str:
        """
        Get the consciousness state formatted for LLM context injection.
        
        This is what gets inserted into the system prompt or context
        to give the agent self-awareness.

        When a structural graph is loaded (v1.7), a budget-adaptive structure
        block is appended ADDITIVELY — the consciousness-state block is byte-for-
        byte unchanged (cognition stays untouched); structure is layered after.
        """
        base = self._state.to_injection()
        if self._structural_signal is not None:
            block = render_structural(
                self._structural_signal,
                structural_budget(self._state.context_window))
            if block:
                return base + "\n" + block
        return base

    # --- Structural Cognition pull surfaces (v1.7) ---

    def load_structure(
        self,
        path: str | Path,
        *,
        max_bytes: int = DEFAULT_MAX_BYTES,
        max_nodes: int = DEFAULT_MAX_NODES,
        workspace_id: Optional[str] = None,
        root: Optional[str | Path] = None,
    ) -> StructuralSignal:
        """Load + distill a Graphify ``graph.json`` (data, never code; R10).

        Caches the distiller (for ``structural_lookup``) and the distilled
        ``StructuralSignal`` (for injection / ``structural_signal``). Raises
        ``StructuralError`` (a ``ValueError``) on malformed or non-graph input.

        When ``workspace_id`` is given (v1.8), drift is tracked: the fresh signal
        is compared against the persisted baseline for that workspace, the
        baseline is advanced, and ``structure:changed`` is emitted on real drift.
        ``root`` (the workspace root) additionally enables freshness-vs-HEAD. With
        no ``workspace_id`` the behaviour is identical to v1.7 (no drift state).
        """
        self._distiller = StructuralDistiller.from_path(
            path, max_bytes=max_bytes, max_nodes=max_nodes)
        self._structural_signal = self._distiller.distill()
        if workspace_id is not None:
            self._track_drift(workspace_id, root, self._structural_signal)
        else:
            self._structural_delta = None
            self._structural_freshness = None
        return self._structural_signal

    def _track_drift(
        self, workspace_id: str, root: Optional[str | Path], sig: StructuralSignal
    ) -> None:
        store = self._drift_store or StructuralDriftStore(drift_path(self.storage))
        self._drift_store = store
        prev = store.get(workspace_id)
        self._structural_delta = compute_delta(prev, sig)
        self._structural_freshness = (
            compute_freshness(root, sig.built_at_commit) if root is not None else None)
        store.put(workspace_id, StructuralDigest.from_signal(sig))  # advance baseline
        if self._structural_delta.changed:
            self._emit_structure_changed(self._structural_delta)

    def _emit_structure_changed(self, delta: StructuralDelta) -> None:
        try:
            self.event_bus.emit(
                type="structure:changed", category="system",
                data={"from": delta.commit_from, "to": delta.commit_to,
                      "hyperedges_added": len(delta.hyperedges_added),
                      "hyperedges_removed": len(delta.hyperedges_removed),
                      "summary": delta.summary})
        except Exception as exc:                       # passive signal — never fatal
            logger.debug("structure:changed emit failed: %s", exc)

    def structural_signal(self) -> Optional[StructuralSignal]:
        """The distilled signal of the loaded graph, or None if none loaded."""
        return self._structural_signal

    def structural_delta(self) -> Optional[StructuralDelta]:
        """Drift of the last loaded graph vs its prior baseline (v1.8).

        None unless the graph was loaded with a ``workspace_id``. Read-only,
        no-LLM — an ``advisory()`` sibling."""
        return self._structural_delta

    def structural_freshness(self) -> Optional[StructuralFreshness]:
        """Freshness of the last loaded graph vs the repo HEAD (v1.8).

        None unless the graph was loaded with both a ``workspace_id`` and a
        ``root``. Read-only, no-LLM."""
        return self._structural_freshness

    def unload_structure(self) -> None:
        """Drop any loaded structural graph (and its drift state).

        Used on a workspace switch into a workspace without consent (v1.7.2), so
        one project's structure never leaks into another's context."""
        self._distiller = None
        self._structural_signal = None
        self._structural_delta = None
        self._structural_freshness = None

    def structural_lookup(self, key: str) -> Optional[dict[str, Any]]:
        """On-demand drill-down: resolve a node / hyperedge / community id to
        detail (read-only, no-LLM, no-mutation — an ``advisory()`` sibling).
        Returns None when no graph is loaded or the id is unknown."""
        return self._distiller.lookup(key) if self._distiller is not None else None

    def _structural_advisory(self) -> Optional[dict[str, Any]]:
        sig = self._structural_signal
        if sig is None:
            return None
        return {
            "loaded": True,
            "commit": sig.built_at_commit,
            "hash": sig.content_hash,
            "nodes": sig.node_count,
            "hyperedges": len(sig.hyperedges),
            "communities": len(sig.communities),
            "drift": (self._structural_delta.to_advisory()
                      if self._structural_delta is not None else None),
            "freshness": (self._structural_freshness.to_advisory()
                          if self._structural_freshness is not None else None),
        }

    def advisory(self) -> dict:
        """Structured, read-only snapshot for the host to PULL each turn (#5/#9).

        Where `get_state_for_injection()` returns prose for the LLM context,
        `advisory()` returns machine-readable signal a host consumes directly:
        cognitive state, active goals tagged by provenance (executable vs
        diagnostic, #7), and operational status (action lockdown / last
        failure-rate brake, #8). It MUST stay cheap — no inference call, no state
        mutation — so it is safe to call on every host turn (and with no adapter
        attached).
        """
        s = self._state
        goals = [
            {"description": g.description, "origin": g.origin.value,
             "executable": g.executable}
            for g in self.goals.active_goals()
        ]
        diagnostic = [g for g in goals if not g["executable"]]
        recommendations: list[str] = []
        if s.dream_recommended:
            recommendations.append("dream recommended")
        if diagnostic:
            recommendations.append(
                f"{len(diagnostic)} diagnostic goal(s) pending review "
                f"(visible, not auto-run)")
        return {
            "awake": self.awake,
            "reflection": s.last_reflection,
            "meta": s.meta_cognition,
            "goals": goals,
            "coherence": {"score": s.coherence, "dominant": s.coherence_note},
            "status": {
                "action_lockdown": s.action_lockdown,
                "dream_recommended": bool(s.dream_recommended),
                "brake": self._last_brake_message(),
            },
            "structural": self._structural_advisory(),
            "recommendations": recommendations,
        }

    def _last_brake_message(self) -> Optional[str]:
        """Most recent aggregate failure-rate brake message, if any (#8).

        Read-only scan of recent system events; returns None when no brake has
        tripped. Never raises (a strict bus must not break the advisory)."""
        try:
            events = self.event_bus.query(
                type="system", category="system", limit=20)
        except Exception:
            return None
        for e in events:                      # newest first
            data = getattr(e, "data", {}) or {}
            msg = data.get("message", "") if isinstance(data, dict) else ""
            if "failure-rate brake" in msg:
                return msg
        return None

    def recall(
        self,
        query: str,
        k: int = 3,
        categories: Optional[list[str]] = None,
    ) -> list[str]:
        """
        Retrieve relevant past context across sessions.
        
        Delegates to ContentLayerManager which unifies ContentStore FTS5 (with
        layer-prioritized reorder) and SessionRAG semantic search.
        
        Args:
            query: Free-text query (e.g., current world_state + anomalies).
            k: Max snippets to return.
            categories: Optional ContentStore category filter(s).
            
        Returns:
            List of short context snippets (<= ~300 chars each), best first.
        """
        return self.content_layer.recall(query, k, categories)

    # --- World Model Interactions ---

    def perceive(self, world_state: str, entities: Optional[dict] = None) -> None:
        """
        Update the world model with perceived state.
        
        Delegates to ContentLayerManager which manages the WorldModel.
        
        Args:
            world_state: Text description of current world state
            entities: Dict of {entity_name: {type, state, attributes}} to update
        """
        self.content_layer.perceive(world_state, entities)

    # --- Evolution Interactions ---

    def propose_evolution(self, evolution_type: str, **kwargs) -> dict:
        """
        Create an evolution proposal.
        
        All proposals are PENDING until a human approves them.
        Returns the proposal dict for review.
        """
        type_map: dict[str, Callable[..., Any]] = {
            "skill_patch": self.evolution.propose_skill_patch,
            "skill_create": self.evolution.propose_skill_create,
            "memory_update": self.evolution.propose_memory_update,
            "pattern_learn": self.evolution.propose_pattern_learn,
        }

        handler = type_map.get(evolution_type)
        if not handler:
            return {"error": f"Unknown evolution type: {evolution_type}"}

        proposal = handler(**kwargs)
        return proposal.to_dict()

    def record_session_lifecycle(
        self,
        event_type: str,
        context: dict,
    ) -> Optional["SessionSummary"]:
        """
        Record a session lifecycle event through Conscio.

        Uses the engine's SessionLifecycle instance (callbacks wired, etc.).

        Args:
            event_type: "session:end" or "session:reset"
            context: Hook context dict (platform, user_id, session_key, session_id)

        Returns:
            SessionSummary if successful, None if no data.
        """
        return self.session_lifecycle.record_session(event_type, context)

    def dream(self, dry_run: bool = False) -> "DreamReport":
        """
        Run a consolidation cycle (Noosphere "Dreaming").

        Release (purge duplicate/trivial events) → Prune (remove stale world
        entities) → Crystallize (compress old reflections). Safe to call
        on-demand, on session handoff, or from cron. Not run per-reflect.
        """
        from .dreaming import DreamCycle
        return DreamCycle().run(self, dry_run=dry_run)

    # --- Status & Monitoring ---

    def status(self) -> dict:
        """Full status of all consciousness modules."""
        return {
            "model": self.model_info.name,
            "context_window": self.model_info.context_window,
            "mode": self.mode.value,
            "budget": self.ctx.budget,
            "monologue": self.monologue.status(),
            "world_model": self.world.status(),
            "meta_cognition": self.meta.summary(),
            "goals": self.goals.status(),
            "evolution": self.evolution.status(),
            "content_store": self.content_store.stats(),
            "event_bus": self.event_bus.stats(),
            "token_tracker": self.token_tracker.gain(),
            "state_tokens_approx": self._state.total_tokens_approx(),
            "storage_path": str(self.storage),
        }

    def health_check(self) -> dict:
        """Quick health check — are all modules operational?"""
        return {
            "healthy": True,
            "mode": self.mode.value,
            "model": self.model_info.name,
            "pending_proposals": len(self.evolution.pending_proposals()),
            "active_goals": len(self.goals.active_goals()),
            "stale_entities": len(self.world.stale_entities()),
        }

    # --- Lifecycle / Resource Cleanup ---

    def close(self) -> None:
        """Close all SQLite-backed modules and flush WAL."""
        for mod in (self.content_store, self.event_bus, self.token_tracker):
            try:
                mod.close()
            except Exception:
                pass
        # Close ContentLayerManager (SessionRAG HTTP connections)
        try:
            self.content_layer.close()
        except Exception:
            pass
        pipeline = getattr(self, "_act_pipeline", None)
        if pipeline is not None:
            for closer in (pipeline.ledger,
                           getattr(pipeline, "trust", None),
                           getattr(pipeline, "breaker", None)):
                try:
                    if closer is not None:
                        closer.close()
                except Exception:
                    pass
        skills = getattr(self, "_skills", None)
        if skills is not None:
            try:
                skills.close()
            except Exception:
                pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # ── v1.0 volition layer (spec section 6) ────────────────────────────

    def attach_adapter(self, adapter, *, sandbox_root=None, registry=None,
                       skeptic_adapter=None, skeptic_mode=None,
                       autonomy_cap=1, intercept_enabled=False):
        """Wire the agentic pipeline. reflect() is unaffected.

        skeptic_adapter: optional second cortex for the audit
        (mixed-cortex); defaults to the actor adapter.
        skeptic_mode: "checklist"/"open"; None (default) starts as
        checklist and lets probe() pick it from the measured profile.
        autonomy_cap: operator ceiling for earned autonomy (1=PROPOSE,
        2=SUPERVISED, 3=AUTONOMOUS). Effective = min(cap, earned).
        """
        from pathlib import Path

        from .agency.act import ActPipeline
        from .agency.adapter import Meter, MeteredAdapter
        from .agency.breaker import CircuitBreaker
        from .agency.ledger import ActionLedger
        from .agency.skeptic import Skeptic
        from .agency.skills import SkillLibrary
        from .agency.tools import make_default_registry
        from .agency.trust import TrustMatrix

        sandbox = Path(sandbox_root or Path.home() / ".conscio" / "sandbox")
        registry = registry or make_default_registry(
            sandbox_root=sandbox, content_store=self.content_store,
            event_bus=self.event_bus, goal_generator=self.goals)
        db = self.storage / "conscio.db"                    # shared DB
        meter = Meter()
        metered = MeteredAdapter(adapter, meter)
        metered_skeptic = (MeteredAdapter(skeptic_adapter, meter)
                           if skeptic_adapter is not None else metered)
        ledger = ActionLedger(db)
        trust = TrustMatrix(
            self.meta, ledger, db,
            reflect_count_fn=lambda: len(self.event_bus.query(
                type="reflection", limit=100000)),
            trips_since_fn=self._trips_since)
        skeptic = Skeptic(metered_skeptic, mode=skeptic_mode or "checklist",
                          facts_fn=self.world.query)
        self._skeptic_mode_explicit = skeptic_mode is not None
        self._act_meter = meter
        self._model_profile = None
        self._act_pipeline = ActPipeline(
            adapter=metered, registry=registry, ledger=ledger,
            breaker=CircuitBreaker(ledger, self.event_bus,
                                   trust=trust, db_path=db),
            skeptic=skeptic, trust=trust, meta=self.meta,
            autonomy_cap=autonomy_cap,
            recall_fn=lambda q: self.recall(q, k=3),
            emit_fn=self.event_bus.emit,
            executable_fn=self.goals.is_executable,   # #7 provenance gate
            intercept_enabled=intercept_enabled)
        # v2.7: wire Intercepter into the gateway if enabled
        if intercept_enabled:
            from .agency.intercepter import Intercepter, InterceptionLoop
            gw = self._act_pipeline.gateway
            gw._loop = InterceptionLoop(
                gw.adapter, Intercepter(),
                max_iterations=3,
                emit_fn=self.event_bus.emit,
            )
        # v1.1: procedural memory — distilled by the dream, served to the
        # actor as few-shot rendered for the gateway's effective tier.
        skills = SkillLibrary(db)
        self._skills = skills
        pipeline = self._act_pipeline
        pipeline.few_shot_provider = lambda goal: skills.few_shot(
            goal, tier=pipeline.gateway.effective_tier())
        return self._act_pipeline

    def _trips_since(self, ts: float) -> int:
        """Breaker trips since `ts` — feeds L3 earned autonomy (5.7)."""
        since = naive_utc_from_epoch(ts).isoformat() if ts else None
        events = self.event_bus.query(type="error", category="system",
                                      since=since, limit=1000)
        return sum(1 for e in (events or [])
                   if "Intractable dissonance" in str(
                       e.to_dict() if hasattr(e, "to_dict") else e))

    def act(self, state=None):
        """Run one L1 PROPOSE cycle downstream of reflect().

        Two entry paths, deliberately different (R9 / I-R9):
          1. Direct call (this method) = the human escape hatch. NOT awake-gated by
             design, but still fully governed by the ActPipeline — skeptic, breaker,
             trust, and the R6 approval queue (R7 / R9 / I-A4). It cannot run an
             action the pipeline would refuse.
          2. Daemon / autonomy path = routed via run(), which IS awake-gated: asleep
             => reflect-only, zero arbiter/act/dream. The daemon never calls act()
             directly (see tests/test_daemon.py).
        """
        from .agency.act import ActReport, ActStatus

        if getattr(self, "_act_pipeline", None) is None:
            return ActReport(status=ActStatus.FAILED,
                             reason="no adapter attached")
        state = state or self._state          # current state held by engine
        report = self._act_pipeline.act(state)
        skills = getattr(self, "_skills", None)
        if skills is not None:                # v1.1: outcome -> skill score
            skills.settle(report)
        if report.lockdown:
            state.action_lockdown = True
            # awake (R9) is engine-scoped, not a property of a transient act
            # state — never let a passed-in state downgrade the persisted flag.
            state.awake = self._state.awake
            self.ctx.save_state(state)
        return report

    def approve(self, ledger_id: int):
        return self._act_pipeline.approve(ledger_id)

    def reject(self, ledger_id: int, reason: str = "") -> None:
        self._act_pipeline.reject(ledger_id, reason)

    def pending(self, limit: int = 20):
        """Approval queue (R6): proposals awaiting approve()/reject()."""
        if getattr(self, "_act_pipeline", None) is None:
            return []
        return self._act_pipeline.ledger.pending(limit)

    # --- v2.2.2 "Trial": sandboxed replay of a quarantined foreign skill ---

    def trial_quarantined(self, rowid: int, *, enable_trial: bool = False):
        """Replay a quarantined foreign skill in a throwaway fs-only sandbox
        and record a binary pass/fail on its quarantine row. Fully isolated —
        never writes the agent's ledger/skills/trust/breaker. Default off.

        Returns a trial.TrialOutcome (ran) or trial.TrialRefusal (refused)."""
        import json
        import shutil
        import tempfile
        import time
        from pathlib import Path

        from .agency import trial as trial_mod
        from .agency.tools import make_default_registry
        from .noosphere import artifact, quarantine
        from .noosphere.paths import quarantine_db_path

        if not enable_trial:
            return trial_mod.TrialRefusal("trial disabled; pass --enable-trial")
        pipe = getattr(self, "_act_pipeline", None)
        if pipe is None or getattr(pipe, "skeptic", None) is None:
            return trial_mod.TrialRefusal("trial requires an adapter")

        qdb = quarantine_db_path(self.storage)
        row = quarantine.get(qdb, rowid)
        if row is None:
            return trial_mod.TrialRefusal(f"no quarantine row #{rowid}")
        if row.import_status != "quarantined":
            return trial_mod.TrialRefusal(
                f"row #{rowid} is not quarantined (status={row.import_status})")
        if artifact.content_hash(row.artifact_json) != row.content_sha256:
            quarantine.note_trial(qdb, rowid, result="tampered",
                                  error="content_sha256 mismatch",
                                  ts=time.time())
            return trial_mod.TrialRefusal(
                f"row #{rowid} tampered (content_sha256 mismatch)")
        try:
            steps = json.loads(row.plan_template)
            if not isinstance(steps, list):
                raise ValueError("plan_template is not a list")
        except (ValueError, TypeError) as exc:
            quarantine.note_trial(qdb, rowid, result="corrupt_plan",
                                  error=str(exc), ts=time.time())
            return trial_mod.TrialRefusal(f"row #{rowid} has a corrupt plan")

        tmp = tempfile.mkdtemp(prefix="conscio-trial-")
        try:
            reg = make_default_registry(
                sandbox_root=Path(tmp), content_store=None, event_bus=None,
                goal_generator=None)
            outcome = trial_mod.run_trial(
                steps, goal_text=row.goal_text, skeptic=pipe.skeptic,
                registry=reg)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        quarantine.record_trial(qdb, rowid, passed=outcome.passed,
                                result=outcome.result, error=outcome.error,
                                ts=time.time())
        return outcome

    # --- v2.3 "Promotion": graduate a quarantined skill into the library ---

    def promote_quarantined(self, rowid: int, *, enable_promote: bool = False):
        """Graduate a quarantined foreign skill that has earned >= 3 clean
        local trials into the live SkillLibrary, seeded with its trial
        counters. No adapter, no sandbox, no execution: a data gate that reads
        the trial evidence and writes one row. Default off.

        Unlike trial (which records a tamper via note_trial), promotion refuses
        a tampered/already-promoted/gate-failed row WITHOUT writing anything —
        it only ever writes on a successful graft (the skills row + the
        promoted-stamp). A refusal leaves the quarantine row untouched.

        Returns a promote.PromoteResult (promoted) or promote.PromoteRefusal."""
        import time

        from .agency import promote as promote_mod
        from .agency.skills import SkillLibrary
        from .agency.tools import make_default_registry
        from .noosphere import artifact, quarantine
        from .noosphere.paths import quarantine_db_path

        if not enable_promote:
            return promote_mod.PromoteRefusal(
                "promotion disabled; pass --enable-promote")

        qdb = quarantine_db_path(self.storage)
        row = quarantine.get(qdb, rowid)
        if row is None:
            return promote_mod.PromoteRefusal(f"no quarantine row #{rowid}")
        if row.import_status != "quarantined":
            return promote_mod.PromoteRefusal(
                f"row #{rowid} is not quarantined "
                f"(status={row.import_status})")
        if row.promoted_ts > 0:
            return promote_mod.PromoteRefusal(
                f"row #{rowid} already promoted "
                f"(skill #{row.promoted_skill_id})")
        if artifact.content_hash(row.artifact_json) != row.content_sha256:
            return promote_mod.PromoteRefusal(
                f"row #{rowid} tampered (content_sha256 mismatch)")

        # Live tool registry, real backends. Used ONLY for registry.get(tool)
        # existence checks — nothing is dispatched, so no tmpdir is needed (the
        # one structural difference from trial). make_default_registry mkdirs
        # sandbox_root, so it points inside the instance storage (never real
        # home), keeping promotion and its tests side-effect-clean.
        reg = make_default_registry(
            sandbox_root=self.storage / "sandbox",
            content_store=self.content_store, event_bus=self.event_bus,
            goal_generator=self.goals)
        decision = promote_mod.evaluate_promotion(
            trial_successes=row.trial_successes,
            trial_failures=row.trial_failures,
            tool_seq=row.tool_seq, registry=reg)
        if not decision.ok:
            return promote_mod.PromoteRefusal(decision.reason)

        lib = SkillLibrary(self.storage / "conscio.db")
        try:
            sid = lib.graft(row.goal_fp, row.goal_text, row.tool_seq,
                            row.plan_template, successes=row.trial_successes,
                            failures=row.trial_failures)
        finally:
            lib.close()
        if sid is None:
            return promote_mod.PromoteRefusal(
                "skill already present (goal_fp, tool_seq collision)")

        quarantine.mark_promoted(qdb, rowid, ts=time.time(), skill_id=sid)
        return promote_mod.PromoteResult(sid, row.trial_successes,
                                         row.trial_failures)

    # --- v2.0 "Connect": propose-only cognition (never executes) ---

    def propose_action(self, intent: dict) -> dict:
        """Audit an explicit host intent with the Skeptic. Never executes."""
        from .agency.contracts import (PROPOSAL_SCHEMA, proposal_from_dict,
                                        validate)
        pipe = getattr(self, "_act_pipeline", None)
        if pipe is None or pipe.skeptic is None:
            return self._no_adapter_result()
        errors = validate(intent, PROPOSAL_SCHEMA)
        if errors:
            return {"verdict": "FAIL", "reasons": errors, "risk_flags": [],
                    "confidence": 0.0, "proposal": None}
        goal = str(intent.get("goal", ""))
        proposal = proposal_from_dict(intent, goal_id=goal)
        verdict = pipe.skeptic.audit(proposal, goal_text=goal)
        self._emit_proposal(proposal, verdict)
        return self._proposal_result(proposal, verdict)

    def propose_plan(self, goal: str,
                     tools: Optional[list[dict]] = None) -> dict:
        """Generate ONE audited action from a goal (Actor), constrained to the
        host's declared tool vocabulary. Never executes; not free-form."""
        from .agency.act import goal_fingerprint
        from .agency.actor import build_actor_prompt
        from .agency.contracts import PROPOSAL_SCHEMA
        from .agency.gateway import GatewayError
        pipe = getattr(self, "_act_pipeline", None)
        if pipe is None or pipe.skeptic is None:
            return self._no_adapter_result()
        if not tools:
            return {"verdict": "FAIL",
                    "reasons": ["propose_plan requires a declared tool "
                                "vocabulary"],
                    "risk_flags": [], "confidence": 0.0, "proposal": None}
        catalog = "\n".join(f"- {t['name']}: {t.get('description', '')}"
                            for t in tools)
        prompt = build_actor_prompt(
            state=self._state, goal_text=goal, catalog_text=catalog,
            recall_snippets=self.recall(goal), few_shot=[])
        try:
            proposal = pipe.gateway.request_action(
                prompt, PROPOSAL_SCHEMA, goal_id=goal_fingerprint(goal),
                tool_names=[t["name"] for t in tools])
        except GatewayError as exc:
            return {"verdict": "FAIL", "reasons": [f"decode failed: {exc}"],
                    "risk_flags": [], "confidence": 0.0, "proposal": None}
        verdict = pipe.skeptic.audit(proposal, goal_text=goal)
        self._emit_proposal(proposal, verdict)
        return self._proposal_result(proposal, verdict)

    @staticmethod
    def _no_adapter_result() -> dict:
        return {"verdict": "FAIL", "reasons": ["no adapter attached"],
                "risk_flags": [], "confidence": 0.0, "proposal": None}

    def _emit_proposal(self, proposal, verdict) -> None:
        self.event_bus.emit(
            type="proposal:audited", category="consciousness",
            data={"tool": proposal.tool, "args": proposal.args,
                  "rationale": proposal.rationale,
                  "expected_outcome": proposal.expected_outcome,
                  "verdict": verdict.verdict, "reasons": verdict.reasons,
                  "confidence": verdict.confidence})

    @staticmethod
    def _proposal_result(proposal, verdict) -> dict:
        return {"verdict": verdict.verdict, "reasons": verdict.reasons,
                "risk_flags": verdict.risk_flags,
                "confidence": verdict.confidence,
                "proposal": {"tool": proposal.tool, "args": proposal.args,
                             "rationale": proposal.rationale,
                             "expected_outcome": proposal.expected_outcome,
                             "action_id": proposal.action_id}}

    # --- v2.0.1 "Connect": host-executed audited act (opt-in) ---

    @staticmethod
    def _manifest_hash(manifest: list) -> str:
        import hashlib
        import json
        return hashlib.sha256(
            json.dumps(manifest, sort_keys=True, default=str).encode()
        ).hexdigest()

    def enable_host_act(self, manifest: list) -> bool:
        """Build/replace the HostActChannel from a host-declared manifest.

        same manifest                       -> idempotent no-op
        different + in-flight ledger rows    -> reject (keep current)
        different + clean                    -> replace
        invalid manifest / no adapter        -> reject atomically (no half-enable)
        """
        from .agency.host_act import HostActChannel
        from .agency.tools import registry_from_manifest

        self._host_act_error = ""
        pipe = getattr(self, "_act_pipeline", None)
        if pipe is None or pipe.skeptic is None:
            self._host_act_error = "act requires an adapter"
            return False
        try:
            new_hash = self._manifest_hash(manifest)
        except (TypeError, ValueError) as exc:
            self._host_act_error = f"invalid manifest: {exc}"
            return False
        existing = getattr(self, "_host_act", None)
        if existing is not None and new_hash == getattr(self, "_host_act_hash",
                                                        ""):
            return True                                  # idempotent
        if existing is not None and pipe.ledger.has_in_flight():
            self._host_act_error = "cannot change manifest with in-flight actions"
            return False
        try:
            registry = registry_from_manifest(manifest)
        except ValueError as exc:
            self._host_act_error = f"invalid manifest: {exc}"
            return False                                 # atomic: state unchanged
        self._host_act = HostActChannel(
            ledger=pipe.ledger, skeptic=pipe.skeptic, breaker=pipe.breaker,
            trust=pipe.trust, registry=registry, emit_fn=self.event_bus.emit,
            awake_fn=lambda: self.awake)
        self._host_act_hash = new_hash
        return True

    @property
    def host_act(self):
        return getattr(self, "_host_act", None)

    @property
    def host_act_error(self) -> str:
        return getattr(self, "_host_act_error", "")

    def probe(self, *, force: bool = False):
        """Run/refresh the ProbeSuite for the attached adapter (spec 5.10).

        Lazy: called by run() before the first cycle, or manually. Never
        called by reflect() (P6) nor by act(). A valid profile re-tiers
        the gateway, picks the skeptic mode (unless one was set
        explicitly at attach) and caps the actor's tool catalog. An
        invalid profile (backend gave no signal) changes nothing.
        """
        if getattr(self, "_act_pipeline", None) is None:
            return None
        if self._model_profile is not None and not force:
            return self._model_profile
        from .agency.profiles import (ProbeSuite, choose_tier,
                                      max_visible_tools, skeptic_mode)
        suite = ProbeSuite(self._act_pipeline.adapter,
                           self.storage / "conscio.db")
        try:
            profile = suite.get(force=force)
        finally:
            suite.close()
        self._model_profile = profile
        if profile.valid:
            self._act_pipeline.gateway.tier = choose_tier(profile)
            self._act_pipeline.max_visible_tools = max_visible_tools(profile)
            if (not getattr(self, "_skeptic_mode_explicit", False)
                    and self._act_pipeline.skeptic is not None):
                self._act_pipeline.skeptic.mode = skeptic_mode(profile)
        return profile

    def run(self, budget=None, *, world_state=""):
        """L3 heartbeat: reflect -> arbiter/act -> (dream), repeated
        under a binding ActBudget (P3). Probes the cortex once, lazily.

        R9 (Awake Mode): self-initiated autonomy is gated here. Asleep (the
        default) ⇒ perceive + reflect() once and return, with ZERO
        arbiter/act/dream — observation stays always-on, autonomy does not.
        A direct human engine.act() call is deliberately NOT gated."""
        from .agency.loop import ActBudget, AutonomyLoop, RunReport

        if not self.awake:
            self.reflect(world_state=world_state)
            return RunReport(stopped="asleep")

        if getattr(self, "_act_pipeline", None) is None:
            # Awake but no inference backend: autonomy is impossible, yet
            # observation stays always-on — perceive + reflect, then report.
            self.reflect(world_state=world_state)
            return RunReport(stopped="no adapter attached")
        self.probe()
        loop = AutonomyLoop(self, self._act_pipeline, self._act_meter)
        return loop.run(budget or ActBudget(), world_state=world_state)


# --- CLI Entry Point ---


