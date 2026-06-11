"""
Inner Monologue — The continuous reflection loop.

This is the "voice inside the agent's head". It runs periodically,
observes the world, reflects on its state, and writes thoughts
to disk. The most recent reflection gets compressed into the
state_summary that enters the LLM context.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from .context_manager import ContextManager
from .models import ContextMode


class InnerMonologue:
    """
    Continuous self-reflection loop.
    
    On each tick:
    1. PERCEIVE  — read world state (logs, APIs, memory, events)
    2. REFLECT   — compare predictions vs reality, assess confidence
    3. GENERATE  — update goals, detect anomalies, identify improvements
    4. PREDICT   — simulate outcomes of potential actions
    5. SUMMARIZE — compress reflection into state_summary
    
    The reflection is saved to disk. Only the summary enters the context.
    """

    def __init__(self, context_manager: ContextManager):
        self._ctx = context_manager
        self.reflections_dir = self._ctx.storage_path / "reflections"
        self.reflections_dir.mkdir(parents=True, exist_ok=True)

    @property
    def ctx(self) -> ContextManager:
        return self._ctx

    @ctx.setter
    def ctx(self, value: ContextManager):
        self._ctx = value

    def reflect(
        self,
        world_state: str = "",
        recent_events: list[str] | None = None,
        confidence: float = 0.5,
        anomalies: list[str] | None = None,
        goals_update: list[str] | None = None,
    ) -> dict:
        """
        Perform a reflection cycle.
        
        Args:
            world_state: Current state description (from perception)
            recent_events: List of events since last reflection
            confidence: Self-assessed confidence (0-1)
            anomalies: Anything unexpected or concerning
            goals_update: New or changed goals
        
        Returns:
            Dict with 'reflection' (full text) and 'summary' (compact version)
        """
        now = datetime.now().isoformat()
        events = recent_events or []
        anomalies = anomalies or []
        goals = goals_update or []

        # Build the full reflection
        reflection_parts = [
            f"# Reflection — {now}",
            "",
            "## World State",
            world_state or "No world state available.",
            "",
            "## Recent Events",
        ]

        if events:
            for e in events:
                reflection_parts.append(f"- {e}")
        else:
            reflection_parts.append("- No events since last reflection.")

        reflection_parts.extend([
            "",
            "## Self-Assessment",
            f"- Confidence: {confidence:.0%}",
            f"- Mode: {self.ctx.mode.value}",
            f"- Model: {self.ctx.model_info.name}",
        ])

        if anomalies:
            reflection_parts.append("\n## Anomalies / Concerns")
            for a in anomalies:
                reflection_parts.append(f"- ⚠️ {a}")

        if goals:
            reflection_parts.append("\n## Goals Update")
            for g in goals:
                reflection_parts.append(f"- → {g}")

        # Prediction section
        reflection_parts.extend([
            "",
            "## Predictions",
            "- Next likely events: [to be filled by perception module]",
        ])

        full_reflection = "\n".join(reflection_parts)

        # Compress to summary (fits within context budget)
        summary = self._summarize(full_reflection, confidence, anomalies, goals)

        # Save to disk
        self._save_reflection(full_reflection, summary)

        return {
            "reflection": full_reflection,
            "summary": summary,
            "timestamp": now,
            "confidence": confidence,
        }

    def _summarize(
        self,
        reflection: str,
        confidence: float,
        anomalies: list[str],
        goals: list[str],
    ) -> str:
        """
        Compress a full reflection into a compact summary.
        
        The summary must fit within the context budget for state_summary.
        In minimal mode: ~150 words
        In compact mode: ~300 words
        In standard mode: ~500 words
        """
        budget = self.ctx.budget["state_summary"]
        mode = self.ctx.mode

        # Start with the essentials
        parts = [f"I am running on {self.ctx.model_info.name} "
                 f"({self.ctx.model_info.context_window//1000}k ctx, {mode.value} mode)."]

        # Confidence
        conf_word = "high" if confidence > 0.7 else "moderate" if confidence > 0.4 else "low"
        parts.append(f"Self-confidence: {conf_word} ({confidence:.0%}).")

        # Anomalies (most important — these drive curiosity)
        if anomalies:
            anomaly_str = "; ".join(anomalies[:3])
            parts.append(f"Concerns: {anomaly_str}")
        else:
            parts.append("No anomalies detected.")

        # Goals
        if goals and mode != ContextMode.MINIMAL:
            goal_str = "; ".join(goals[:3])
            parts.append(f"Active goals: {goal_str}")

        summary = " ".join(parts)

        # Trim to budget
        words = summary.split()
        if len(words) > budget:
            summary = " ".join(words[:budget]) + "..."

        return summary

    def _save_reflection(self, full: str, summary: str) -> Path:
        """Save reflection to disk. Daily file, append-only."""
        today = datetime.now().strftime("%Y-%m-%d")
        path = self.reflections_dir / f"{today}.md"

        # Append to existing file
        if path.exists():
            existing = path.read_text()
            path.write_text(existing + "\n\n---\n\n" + full)
        else:
            path.write_text(full)

        # Also save the summary as the current state
        summary_path = self.ctx.storage_path / "state_summary.txt"
        summary_path.write_text(summary)

        return path

    def last_reflection(self, n: int = 1) -> Optional[str]:
        """Retrieve the last N reflections from disk."""
        files = sorted(self.reflections_dir.glob("*.md"), reverse=True)
        if not files:
            return None

        # Read the most recent file and extract reflections
        content = files[0].read_text()
        reflections = content.split("\n---\n")
        return reflections[-n] if n <= len(reflections) else content

    def status(self) -> dict:
        """Return status for monitoring."""
        files = list(self.reflections_dir.glob("*.md"))
        return {
            "reflections_count": len(files),
            "last_reflection_date": max(
                (f.stem for f in files), default="none"
            ),
            "storage_path": str(self.reflections_dir),
        }
