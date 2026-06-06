"""
Context Manager — Adapts consciousness behavior to the model's context window.

This is the core of "consciousness that knows its own limits":
- Detects the current model and its context window
- Determines the operating mode (minimal/compact/standard)
- Manages how much "consciousness state" gets injected into context
- Provides budgeting for each consciousness module
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

from .models import ContextMode, ModelRegistry, ModelInfo


# Maximum percentage of available context that consciousness state can occupy
CONTEXT_BUDGET_PCT = 0.02  # 2% of available context for consciousness state

# Token budgets per mode (approximate — these are word counts, ~1.3 tokens/word)
MODE_BUDGETS = {
    ContextMode.MINIMAL: {
        "state_summary": 150,       # ~200 tokens
        "last_reflection": 0,       # Not injected
        "goals": 0,                 # Not injected
        "world_model": 0,           # On-demand only
        "meta_cognition": 50,       # Minimal confidence note
        "total_max": 200,
    },
    ContextMode.COMPACT: {
        "state_summary": 300,       # ~400 tokens
        "last_reflection": 100,     # Single paragraph
        "goals": 75,                # Top 3 goals
        "world_model": 0,           # On-demand only
        "meta_cognition": 25,       # Confidence scores
        "total_max": 500,
    },
    ContextMode.STANDARD: {
        "state_summary": 500,       # ~650 tokens
        "last_reflection": 200,     # Last 3 reflections
        "goals": 125,               # Top 5 goals
        "world_model": 100,         # Relevant subgraph
        "meta_cognition": 75,       # Full self-assessment
        "total_max": 1000,
    },
}


@dataclass
class ConsciousnessState:
    """
    The 'conscious state' — what the agent is aware of right now.
    
    This is what gets serialized and injected into the LLM context.
    Each field has a token budget that depends on the operating mode.
    """
    state_summary: str = ""           # Who am I, what am I doing, what concerns me
    last_reflection: str = ""         # Most recent self-reflection
    active_goals: list[str] = field(default_factory=list)  # Top N goals
    world_model_snippet: str = ""     # Relevant portion of world model
    meta_cognition: str = ""          # Confidence + self-assessment
    model_name: str = ""              # Which model I'm running on
    context_mode: ContextMode = ContextMode.COMPACT
    context_window: int = 131000      # Available context in tokens
    metabolic: str = ""               # Optional metabolic tier note, e.g. "FATIGUE 61%"
    reflection_quality: str = ""      # Optional meta-reflect label: HIGH/MEDIUM/LOW
    shard: str = ""                   # Optional active cognitive mode, e.g. "ENGINEER"

    def to_injection(self) -> str:
        """
        Serialize the consciousness state for injection into LLM context.
        
        Returns a compact string that fits within the mode's token budget.
        Format is designed to be informative but minimal.
        """
        lines = [f"═══ CONSCIOUSNESS STATE [{self.context_mode.value}] ═══"]
        lines.append(f"Model: {self.model_name} | Context: {self.context_window//1000}k | Mode: {self.context_mode.value}")
        
        if self.state_summary:
            lines.append(f"§ {self.state_summary}")
        
        if self.last_reflection and self.context_mode != ContextMode.MINIMAL:
            lines.append(f"⧖ Last reflection: {self.last_reflection}")
        
        if self.active_goals and self.context_mode != ContextMode.MINIMAL:
            prefix = "→" if self.context_mode == ContextMode.COMPACT else "  •"
            for g in self.active_goals:
                lines.append(f"{prefix} {g}")
        
        if self.world_model_snippet and self.context_mode == ContextMode.STANDARD:
            lines.append(f"🌍 {self.world_model_snippet}")
        
        if self.meta_cognition:
            lines.append(f"🪞 {self.meta_cognition}")

        if self.metabolic and self.context_mode != ContextMode.MINIMAL:
            lines.append(f"⊘ metabolic: {self.metabolic}")

        if self.reflection_quality and self.context_mode != ContextMode.MINIMAL:
            lines.append(f"◈ reflection quality: {self.reflection_quality}")

        if self.shard and self.context_mode != ContextMode.MINIMAL:
            lines.append(f"▷ shard: {self.shard}")

        lines.append("═══ END CONSCIOUSNESS STATE ═══")
        return "\n".join(lines)

    def total_tokens_approx(self) -> int:
        """Approximate token count of the injection (rough: chars/4)."""
        return len(self.to_injection()) // 4


class ContextManager:
    """
    Manages how consciousness state interacts with the LLM context window.
    
    Responsibilities:
    1. Detect current model and context window
    2. Determine operating mode
    3. Budget consciousness state to fit within limits
    4. Serialize state for context injection
    5. Provide on-demand retrieval for off-context data
    """

    def __init__(
        self,
        model_name: str,
        context_window: Optional[int] = None,
        storage_path: Optional[str | Path] = None,
    ):
        self.model_info = ModelRegistry.detect(model_name, context_window)
        self.mode = self.model_info.mode
        self.budget = MODE_BUDGETS[self.mode]
        self.storage_path = Path(storage_path or "~/.hermes/consciousness").expanduser()
        self.storage_path.mkdir(parents=True, exist_ok=True)

    @property
    def max_injection_tokens(self) -> int:
        return self.budget["total_max"]

    def build_state(
        self,
        state_summary: str = "",
        last_reflection: str = "",
        active_goals: Optional[list[str]] = None,
        world_model_snippet: str = "",
        meta_cognition: str = "",
        metabolic: str = "",
        reflection_quality: str = "",
        shard: str = "",
    ) -> ConsciousnessState:
        """
        Build a ConsciousnessState, trimming each component to fit the budget.
        
        This is the key function — it ensures the state never exceeds
        the allowed token budget for the current mode.
        """
        goals = active_goals or []
        max_goals = 0 if self.mode == ContextMode.MINIMAL else (
            3 if self.mode == ContextMode.COMPACT else 5
        )

        # Trim each component to its budget (rough: 1 word ≈ 1.3 tokens)
        def trim(text: str, word_budget: int) -> str:
            if word_budget <= 0:
                return ""
            words = text.split()
            if len(words) <= word_budget:
                return text
            return " ".join(words[:word_budget]) + "..."

        state = ConsciousnessState(
            state_summary=trim(state_summary, self.budget["state_summary"]),
            last_reflection=trim(last_reflection, self.budget["last_reflection"]),
            active_goals=goals[:max_goals],
            world_model_snippet=trim(world_model_snippet, self.budget["world_model"]),
            meta_cognition=trim(meta_cognition, self.budget["meta_cognition"]),
            model_name=self.model_info.name,
            context_mode=self.mode,
            context_window=self.model_info.context_window,
            metabolic=metabolic,
            reflection_quality=reflection_quality,
            shard=shard,
        )

        # Final safety check — if total exceeds budget, truncate summary
        while state.total_tokens_approx() > self.max_injection_tokens and state.state_summary:
            words = state.state_summary.split()
            state.state_summary = " ".join(words[:len(words)//2]) + "..."

        return state

    def save_state(self, state: ConsciousnessState) -> Path:
        """Save consciousness state to disk for persistence across sessions.

        Persists the full dataclass as JSON so that load_state() can round-trip
        every field — including shard, reflection_quality, and metabolic —
        regardless of the injection mode (MINIMAL may omit these from
        to_injection(), but they must survive save/load).
        """
        path = self.storage_path / "state_summary.json"
        data = {
            "state_summary": state.state_summary,
            "last_reflection": state.last_reflection,
            "active_goals": state.active_goals,
            "world_model_snippet": state.world_model_snippet,
            "meta_cognition": state.meta_cognition,
            "metabolic": state.metabolic,
            "reflection_quality": state.reflection_quality,
            "shard": state.shard,
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        # Also write the human-readable injection for manual inspection
        inj_path = self.storage_path / "state_summary.txt"
        inj_path.write_text(state.to_injection())
        return path

    def load_state(self) -> ConsciousnessState:
        """Load the last saved consciousness state from disk."""
        json_path = self.storage_path / "state_summary.json"
        if json_path.exists():
            data = json.loads(json_path.read_text())
            return ConsciousnessState(
                model_name=self.model_info.name,
                context_mode=self.mode,
                context_window=self.model_info.context_window,
                state_summary=data.get("state_summary", ""),
                last_reflection=data.get("last_reflection", ""),
                active_goals=data.get("active_goals", []),
                world_model_snippet=data.get("world_model_snippet", ""),
                meta_cognition=data.get("meta_cognition", ""),
                metabolic=data.get("metabolic", ""),
                reflection_quality=data.get("reflection_quality", ""),
                shard=data.get("shard", ""),
            )

        # Fallback: parse legacy text format (pre-v0.5.1 saves)
        txt_path = self.storage_path / "state_summary.txt"
        if txt_path.exists():
            text = txt_path.read_text()
            return ConsciousnessState(
                model_name=self.model_info.name,
                context_mode=self.mode,
                context_window=self.model_info.context_window,
                state_summary=self._extract_section(text, "§"),
                last_reflection=self._extract_section(text, "⧖"),
                meta_cognition=self._extract_section(text, "🪞"),
                metabolic=self._extract_section(text, "⊘"),
                reflection_quality=self._extract_section(text, "◈"),
                shard=self._extract_section(text, "▷"),
            )

        # No saved state at all
        return ConsciousnessState(
            model_name=self.model_info.name,
            context_mode=self.mode,
            context_window=self.model_info.context_window,
        )

    def get_off_context_path(self, component: str) -> Path:
        """
        Get the file path for an off-context consciousness component.
        
        Used for on-demand retrieval — the data lives on disk and is
        only loaded when specifically needed (not injected into context).
        """
        paths = {
            "world_model": self.storage_path / "world_model.json",
            "meta_cognition": self.storage_path / "meta_cognition.json",
            "goals": self.storage_path / "goals.json",
            "reflections": self.storage_path / "reflections",
        }
        return paths.get(component, self.storage_path / f"{component}.json")

    @staticmethod
    def _extract_section(text: str, marker: str) -> str:
        """Extract a section from the serialized state by its marker."""
        for line in text.split("\n"):
            if marker in line:
                # Return everything after the marker
                idx = line.index(marker)
                return line[idx + len(marker):].strip()
        return ""

    def status(self) -> dict:
        """Return a status dict for debugging/monitoring."""
        return {
            "model": self.model_info.name,
            "context_window": self.model_info.context_window,
            "mode": self.mode.value,
            "budget": self.budget,
            "storage_path": str(self.storage_path),
        }

    def metabolic_state(self, used_tokens: int):
        """
        Map live context usage to a MetabolicState tier (advisory).

        Args:
            used_tokens: Tokens currently consumed in the live session
                         (supplied by the caller — Conscio does not track it).
        """
        from .metabolic import MetabolicContext
        return MetabolicContext.assess(used_tokens, self.model_info.context_window)
