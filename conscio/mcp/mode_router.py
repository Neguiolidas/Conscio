"""ModeRouter — lê daemon_control.json e chunkifica output das MCP tools.

Design: wrapper de presentation no MCP server. Não modifica lógica das vozes
em gates.py. Recebe o dict completo do council/evaluate/cognitive_cycle
e chunkifica conforme o modo: minimal, compact, full, agent_host.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_VALID_COMPLEXITIES = {"minimal", "compact", "full", "agent_host"}


class ModeRouter:
    """Lê prompt_complexity do daemon_control.json e chunkifica output."""

    def __init__(self, storage_path: Path | str):
        storage = Path(storage_path)
        self.complexity = self._read_complexity(storage)

    @staticmethod
    def _read_complexity(storage: Path) -> str:
        """Lê prompt_complexity do daemon_control.json. Default: compact."""
        ctrl = storage / "daemon_control.json"
        if ctrl.exists():
            try:
                data = json.loads(ctrl.read_text(encoding="utf-8"))
                complexity = data.get("prompt_complexity", "compact")
                if complexity in _VALID_COMPLEXITIES:
                    return complexity
            except (json.JSONDecodeError, OSError):
                logger.warning("daemon_control.json corrupted, falling back to compact")
        return "compact"

    @staticmethod
    def _detect_mode(result: dict) -> str:
        """Detecta se o resultado usa LLM (tem 'LLM analysis'
        em qualquer voice) or deterministic."""
        for voice in result.get("voices", []):
            analysis = voice.get("analysis", "")
            if "LLM" in analysis:
                return "llm"
        return "deterministic"

    def format_council(self, council_result: dict) -> dict:
        """Formata resultado do council conforme o modo."""
        mode = self._detect_mode(council_result)
        complexity = self.complexity

        if complexity == "minimal":
            return {
                "mode": mode,
                "recommendation": council_result["recommendation"],
                "votes": council_result["votes_summary"],
            }

        if complexity == "agent_host":
            return {
                "mode": "agent_host",
                "question": council_result.get("question", ""),
                "context": council_result.get("context", ""),
                "hint": "Produce 4 voices: architect (structural integrity), "
                       "skeptic (contradictions), pragmatist (cost/feasibility), "
                       "critic (failure modes). Return verdict + concerns.",
            }

        if complexity == "compact":
            voices = []
            for v in council_result.get("voices", []):
                top = v.get("concerns", ["none"])
                voices.append({
                    "role": v["role"],
                    "vote": v["vote"],
                    "top_concern": top[0] if top else "none",
                })
            return {
                "mode": mode,
                "recommendation": council_result["recommendation"],
                "votes": council_result["votes_summary"],
                "voices": voices,
            }

        # full
        return {"mode": mode, **council_result}

    def format_cognitive_cycle(self, cycle_result: dict) -> dict:
        """Formata cognitive_cycle conforme o modo."""
        mode = "deterministic"
        if any("LLM" in str(v) for v in cycle_result.values()):
            mode = "llm"

        complexity = self.complexity

        if complexity == "minimal":
            return {
                "mode": mode,
                "coherence": cycle_result.get("coherence", {}),
                "metabolic": cycle_result.get("metabolic", ""),
            }

        if complexity == "compact":
            dissonance = cycle_result.get("dissonance", [])
            top_dissonance = dissonance[:3] if isinstance(dissonance, list) else [str(dissonance)]
            return {
                "mode": mode,
                "coherence": cycle_result.get("coherence", {}),
                "reflection_quality": cycle_result.get("reflection_quality", ""),
                "top_dissonance": top_dissonance,
            }

        # full
        return {"mode": mode, **cycle_result}

    def format_evaluate(self, evaluate_result: dict) -> dict:
        """Formata evaluate conforme o modo.
        Preserva os 5 eixos, trunca conforme o modo."""
        # Detecta modo — evaluate pode ter LLM score
        mode = "deterministic"
        for axis in evaluate_result.get("axis_scores", []):
            if "LLM" in str(axis.get("evidence", "")):
                mode = "llm"
                break

        complexity = self.complexity

        if complexity in ("minimal", "compact"):
            overall = evaluate_result.get("overall", {})
            return {
                "mode": mode,
                "overall": overall.get("average", overall.get("score", "N/A")),
                "strongest": evaluate_result.get("strongest", ""),
                "weakest": evaluate_result.get("weakest", ""),
            }

        return {"mode": mode, **evaluate_result}