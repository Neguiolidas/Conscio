"""
ConsciousnessEngine — Orchestrates all consciousness modules.

The central coordinator that:
1. Detects the current model and context mode
2. Initializes all modules (Inner Monologue, World Model, Meta-Cognition, Goals, Evolution)
3. Runs the perception-reflection-generation loop
4. Manages state persistence and context injection
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from .context_manager import ContextManager, ConsciousnessState
from .models import ModelRegistry, ModelInfo, ContextMode
from .inner_monologue import InnerMonologue
from .world_model import WorldModel
from .meta_cognition import MetaCognition
from .goal_generator import GoalGenerator, Drive
from .auto_evolution import AutoEvolution
from .content_store import ContentStore
from .event_bus import EventBus
from .shard_engine import ShardEngine
from .coherence import CoherenceEngine, COHERENCE_EVENT_THRESHOLD
from .voice_preset import resolve_voice_preset
from .self_prompt import generate_self_prompts
from .dreaming import DreamRecommendation
from .semantic import SemanticEngine, ContradictionDetector
from .output_filter import FilterPipeline, build_pipeline_from_dict
from .token_tracker import TokenTracker
from .content_layer import layer_sort_key, ContentLayerManager
from .session_lifecycle import SessionLifecycle
from .metabolic import MetabolicContext
from .session_rag_factory import create_session_rag

logger = logging.getLogger(__name__)


class ConsciousnessEngine:
    """
    The main orchestrator for the consciousness framework.
    
    Usage:
        engine = ConsciousnessEngine(model_name="glm-5.1")
        
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
    ):
        self.storage = Path(storage_path) if storage_path else self.DEFAULT_STORAGE
        self.storage.mkdir(parents=True, exist_ok=True)

        # Detect model and set up context management
        self.ctx = ContextManager(model_name, context_window, self.storage)
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
        self.event_bus = EventBus(db_path=db_path)
        self.shard_engine = ShardEngine(self.event_bus)
        self.coherence = CoherenceEngine(self.meta, self.world)
        # v0.8: shared semantic engine (lazy embedder) + contradiction detector.
        # Built once; reused by dream Reconcile and the opt-in output stage.
        self._semantic = SemanticEngine()
        self._contradiction_detector = ContradictionDetector(self._semantic)
        self.last_coherence = None
        self.dream_recommended = DreamRecommendation(False, None, None)
        self.last_self_prompts = []

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

        self._session_rag = None
        self._state = self.ctx.load_state()

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
                goals.generate_from_evolution(blind_spot, target="low confidence area")
                active_descriptions.add(expected_desc)

        # Generate MAINTENANCE goals from frequent errors
        # GoalGenerator prefixes with "Maintenance:" — match that for dedup
        for error in meta.frequent_errors(min_count=2):
            expected_desc = f"Maintenance: fix_recurring_error \u2014 {error['pattern']} ({error['count']}x recurring)"
            if expected_desc not in active_descriptions:
                goals.generate_from_maintenance(
                    "fix_recurring_error", f"{error['pattern']} ({error['count']}x recurring)"
                )
                active_descriptions.add(expected_desc)

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
            label=f"reflection_{datetime.utcnow().strftime('%Y%m%d_%H%M')}",
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
        used_tokens = self._state.total_tokens_approx()
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

    def get_state_for_injection(self) -> str:
        """
        Get the consciousness state formatted for LLM context injection.
        
        This is what gets inserted into the system prompt or context
        to give the agent self-awareness.
        """
        return self._state.to_injection()

    @property
    def session_rag(self):
        """
        Lazily construct SessionRAG via the shared factory, gated by
        Ollama availability.  The probe runs at most once per engine;
        failures degrade gracefully to None.
        """
        if self._session_rag is None:
            self._session_rag = create_session_rag()
        return self._session_rag

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
        type_map = {
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

        Convenience method that uses this engine instance (no temp engine created).

        Args:
            event_type: "session:end" or "session:reset"
            context: Hook context dict (platform, user_id, session_key, session_id)

        Returns:
            SessionSummary if successful, None if no data.
        """
        from .session_lifecycle import record_session_lifecycle as _record
        return _record(event_type, context, engine=self)

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

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


# --- CLI Entry Point ---

def main():
    """Quick CLI for testing the consciousness engine."""
    import sys

    model = sys.argv[1] if len(sys.argv) > 1 else "glm-5.1"
    engine = ConsciousnessEngine(model_name=model)

    print(f"🧠 ConsciousnessEngine initialized")
    print(f"   Model: {engine.model_info.name}")
    print(f"   Context: {engine.model_info.context_window//1000}k")
    print(f"   Mode: {engine.mode.value}")
    print(f"   Budget: {engine.ctx.budget['total_max']} tokens")
    print()

    # Run a test reflection
    result = engine.reflect(
        world_state="Test initialization — all systems nominal",
        confidence=0.8,
    )

    print("📝 Reflection result:")
    print(result["summary"])
    print()
    print("💉 State injection preview:")
    print(engine.get_state_for_injection())
    print()
    print("📊 Full status:")
    print(json.dumps(engine.status(), indent=2))


if __name__ == "__main__":
    main()
