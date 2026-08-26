# conscio/squads/convene.py
"""Squad convening logic — assemble voices and produce a result (v4.4).

Mirrors the pattern of ``conscio.gates.council()`` but for squads.
Each squad convene:
1. Resolves requested voices (or defaults to all available).
2. Runs ``analyze()`` on each voice (deterministic).
3. Optionally runs ``analyze_llm()`` if ``use_llm=True`` and adapter
   is available.
4. Produces a recommendation using the same conservative logic as
   council (veto >= 1 → veto, hold >= 2 → hold, proceed >= 3 → proceed).
5. Emits a ``squad:<name>:convened`` event on the EventBus.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from conscio.squads._base import VoiceResult
from conscio.squads._router import (
    EXPERTS_ORDER,
    EXPERTS_VOICES,
    OPOSITORS_ORDER,
    OPOSITORS_VOICES,
    get_voice,
)

if TYPE_CHECKING:
    from conscio.engine import ConsciousnessEngine


def _resolve_voices(
    requested: list[str] | None,
    available: set[str],
    order: list[str],
) -> list[str]:
    """Resolve requested voice names to available, ordered names."""
    if not requested:
        return [n for n in order if n in available]
    return [n for n in order if n in available and n in requested]


def _compute_recommendation(voices: list[VoiceResult]) -> str:
    """Conservative recommendation from voice votes (mirrors council)."""
    votes = [v.vote for v in voices]
    vetoes = votes.count("veto")
    holds = votes.count("hold")
    proceeds = votes.count("proceed")

    if vetoes >= 1:
        return "veto"
    if holds >= 2:
        return "hold"
    if proceeds >= 3:
        return "proceed"
    return "hold"  # ambiguous → not ready


def convene_squad(
    engine: ConsciousnessEngine,
    *,
    squad: str,
    question: str,
    context: str = "",
    voices: list[str] | None = None,
    use_llm: bool = False,
) -> dict:
    """Convene a squad and return a structured result.

    Args:
        engine: The ConsciousnessEngine instance.
        squad: 'experts' or 'opositors'.
        question: The decision/artefact to evaluate (required).
        context: Additional context string.
        voices: Optional list of voice names. Defaults to all available.
        use_llm: Whether to use LLM adapter for voices that support it.

    Returns:
        Dict with question, squad, voices, recommendation, votes_summary.
    """
    if not question:
        raise ValueError("question is required")

    if squad not in ("experts", "opositors"):
        raise ValueError(
            f"Unknown squad '{squad}'. Must be 'experts' or 'opositors'."
        )

    available = EXPERTS_VOICES if squad == "experts" else OPOSITORS_VOICES
    order = EXPERTS_ORDER if squad == "experts" else OPOSITORS_ORDER

    resolved = _resolve_voices(voices, available, order)
    if not resolved:
        return {
            "question": question,
            "squad": squad,
            "voices": [],
            "recommendation": "hold",
            "votes_summary": {"proceed": 0, "hold": 0, "veto": 0},
            "error": f"No voices available for squad '{squad}'",
        }

    adapter = None
    if use_llm and engine.awake:
        pipeline = getattr(engine, "_act_pipeline", None)
        if pipeline is not None:
            adapter = getattr(pipeline, "adapter", None)

    voice_results: list[dict] = []
    for name in resolved:
        voice = get_voice(name)
        if voice is None:
            continue
        try:
            result = voice.analyze({"question": question, "context": context})
            if use_llm and adapter is not None:
                try:
                    result = voice.analyze_llm(
                        {"question": question, "context": context},
                        adapter=adapter,
                    )
                except NotImplementedError:
                    pass  # voice doesn't support LLM
                except Exception:
                    pass  # LLM failure → keep deterministic
        except Exception as e:
            result = VoiceResult(
                role=name,
                analysis=f"voice degraded (error): {e}",
                concerns=["Voice could not analyze"],
                vote="hold",
            )
        voice_results.append({
            "role": result.role,
            "analysis": result.analysis,
            "concerns": result.concerns,
            "vote": result.vote,
        })

    recommendation = _compute_recommendation(
        [VoiceResult(**v) for v in voice_results]
    )

    votes = [v["vote"] for v in voice_results]
    result = {
        "question": question,
        "squad": squad,
        "voices": voice_results,
        "recommendation": recommendation,
        "votes_summary": {
            "proceed": votes.count("proceed"),
            "hold": votes.count("hold"),
            "veto": votes.count("veto"),
        },
    }

    event_type = f"squad:{squad}:convened"
    engine.event_bus.emit(event_type, "consciousness", result)
    return result