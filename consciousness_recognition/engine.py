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

        # Load previous state
        self._state = self.ctx.load_state()

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

        # Generate goals from anomalies (curiosity drive)
        for anomaly in anomalies:
            self.goals.generate_from_curiosity(anomaly, context=world_state)

        # Generate maintenance goals for stale world model entities
        stale = self.world.stale_entities()
        if stale:
            self.goals.generate_from_maintenance(
                "prune_stale", f"{len(stale)} stale entities: {', '.join(stale[:3])}"
            )

        # Run the inner monologue reflection
        result = self.monologue.reflect(
            world_state=world_state,
            recent_events=recent_events,
            confidence=confidence,
            anomalies=anomalies,
            goals_update=[g.description for g in self.goals.active_goals()],
        )

        # Record confidence in meta-cognition
        self.meta.record_confidence("general", confidence)

        # Build the new consciousness state
        self._state = self.ctx.build_state(
            state_summary=result["summary"],
            last_reflection=result["summary"][:200],  # Compact version
            active_goals=[g.description for g in self.goals.active_goals()],
            world_model_snippet=self.world.query(world_state)[:100] if world_state else "",
            meta_cognition=self.meta.summary(),
        )

        # Persist state
        self.ctx.save_state(self._state)

        return result

    def get_state_for_injection(self) -> str:
        """
        Get the consciousness state formatted for LLM context injection.
        
        This is what gets inserted into the system prompt or context
        to give the agent self-awareness.
        """
        return self._state.to_injection()

    # --- World Model Interactions ---

    def perceive(self, world_state: str, entities: Optional[dict] = None) -> None:
        """
        Update the world model with perceived state.
        
        Args:
            world_state: Text description of current world state
            entities: Dict of {entity_name: {type, state, attributes}} to update
        """
        if entities:
            for name, info in entities.items():
                self.world.add_entity(
                    name=name,
                    entity_type=info.get("type", "unknown"),
                    attributes=info.get("attributes"),
                    state=info.get("state", ""),
                )

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
            "meta_cognition": self.meta.status(),
            "goals": self.goals.status(),
            "evolution": self.evolution.status(),
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
