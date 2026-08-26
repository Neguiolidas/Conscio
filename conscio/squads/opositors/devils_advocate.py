# conscio/squads/opositors/devils_advocate.py
"""Devil's Advocate voice — argues the opposite position (Ato 4, v4.4).

Forces the system to prove A+B that the premise of a decision is viable.
Argues the opposite of whatever is proposed. Does NOT believe the
opposing position — merely ensures the proponent has proof.

Tone boundary: challenges IDEAS and PREMISES, never people.
"""
from __future__ import annotations

from typing import Any

from conscio.squads._base import Voice, VoiceResult, _vote_from_concerns

# Signals that the premise is unverified or assumed.
_UNVERIFIED = (
    "definitely",
    "obviously",
    "clearly",
    "no need to validate",
    "everyone knows",
    "it goes without saying",
    "no need to test",
    "users want",
    "will revolutionise",
    "game changer",
    "disrupt",
    "transform",
    "guaranteed",
)

# Signals that suggest a product/technical premise needs challenge.
_PREMISE_SIGNALS = (
    "mcp + llm",
    "parametric motor",
    "build the product",
    "release feature",
    "ship it",
    "launch",
    "deploy",
    "go live",
    "roll out",
)


class DevilsAdvocateVoice(Voice):
    """Argues the opposite position — forces proof-by-contradiction."""

    name = "devils_advocate"
    role = "devils_advocate"
    description = (
        "Devil's Advocate: argues the opposite position, forces "
        "proof-by-contradiction, challenges premises."
    )

    def analyze(self, ctx: dict[str, Any]) -> VoiceResult:
        question = (ctx.get("question", "") or "").lower()
        context = (ctx.get("context", "") or "").lower()
        blob = f"{question}\n{context}"

        analysis_parts: list[str] = []
        concerns: list[str] = []

        # Unverified claims
        unverified_hits = [t for t in _UNVERIFIED if t in blob]
        if unverified_hits:
            analysis_parts.append(
                f"detected unverified claims: {', '.join(unverified_hits[:3])}"
            )
            concerns.append(
                f"Unverified claim detected ({', '.join(unverified_hits[:2])}). "
                f"The burden of proof is on the proponent — where is the "
                f"evidence this will actually work?"
            )

        # Premise challenge
        premise_hits = [t for t in _PREMISE_SIGNALS if t in blob]
        if premise_hits:
            analysis_parts.append("premise requires proof")
            # Always challenge the premise
            concerns.append(
                "Premise challenged: what if the opposite is true? "
                "Has anyone validated that this approach solves a real "
                "problem, or are we building on assumptions?"
            )

        if not analysis_parts:
            analysis_parts.append(
                "No premise to challenge — a rare safe decision."
            )

        analysis = "; ".join(analysis_parts)
        vote = _vote_from_concerns(concerns)
        return VoiceResult(
            role=self.role,
            analysis=analysis,
            concerns=concerns,
            vote=vote,
        )

    def analyze_llm(self, ctx: dict[str, Any], adapter) -> VoiceResult:
        """LLM path for deeper premise challenge (opt-in)."""
        result = self.analyze(ctx)
        if adapter is None:
            return result
        try:
            prompt = (
                f"You are the Devil's Advocate. Argue the OPPOSITE of whatever "
                f"is proposed. Challenge the premise. Force the proponent to "
                f"prove their case with evidence. Be concise and logical. "
                f"NEVER attack people — only ideas and premises.\n\n"
                f"Question: {ctx.get('question', '')}\n"
                f"Context: {ctx.get('context', '')}"
            )
            out = adapter.generate(prompt, max_tokens=192, temperature=0.3)
            text = getattr(out, "text", str(out))[:300]
            result.analysis = f"LLM: {text}"
            if any(w in text.lower() for w in ("what if", "prove", "evidence", "assumption", "opposite")):
                result.concerns = [result.analysis] + result.concerns
        except Exception:
            pass
        return result