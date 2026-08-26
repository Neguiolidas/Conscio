# conscio/squads/experts/qa.py
"""QA voice — test/coverage heuristics (Ato 2, v4.4).

Deterministic analysis for quality assurance risks: missing tests,
skipped/xfail abuse, edge-case omission, and coverage gaps.
Pure stdlib, no LLM.
"""
from __future__ import annotations

from typing import Any

from conscio.squads._base import Voice, VoiceResult, _vote_from_concerns

# Tokens suggesting missing or skipped tests.
_SKIP_TOKENS = (
    "no tests",
    "skip test",
    "xfail",
    "mark skip",
    "skip the test",
    "untested",
    "not tested",
    "without test",
)

# Tokens suggesting edge cases are being dismissed.
_EDGE_TOKENS = (
    "edge case",
    "corner case",
    "unlikely",
    "skip edge",
    "skip boundary",
    "rare case",
    "wont happen",
)


class QAVoice(Voice):
    """Quality assurance scrutineer — flags missing tests, edge gaps."""

    name = "qa"
    role = "qa"
    description = (
        "Quality assurance: missing tests, xfail/skip abuse, edge-case "
        "omission, coverage gaps."
    )

    def analyze(self, ctx: dict[str, Any]) -> VoiceResult:
        question = (ctx.get("question", "") or "").lower()
        context = (ctx.get("context", "") or "").lower()
        blob = f"{question}\n{context}"

        analysis_parts: list[str] = []
        concerns: list[str] = []

        # Missing tests
        if any(t in blob for t in _SKIP_TOKENS):
            analysis_parts.append("detected missing/skipped test pattern")
            concerns.append(
                "Tests are missing or skipped — untested code is "
                "undeployable code."
            )

        # Edge-case dismissal
        if any(t in blob for t in _EDGE_TOKENS):
            edge_in_skip_context = any(
                w in blob for w in ("skip", "unlikely", "wont", "rare", "omit")
            )
            if edge_in_skip_context:
                analysis_parts.append("detected edge-case dismissal")
                concerns.append(
                    "Edge cases dismissed without justification — bugs "
                    "hide in the boundaries."
                )

        if not analysis_parts:
            analysis_parts.append("No obvious QA regressions detected.")

        analysis = "; ".join(analysis_parts)
        vote = _vote_from_concerns(concerns)
        return VoiceResult(
            role=self.role,
            analysis=analysis,
            concerns=concerns,
            vote=vote,
        )

    def analyze_llm(self, ctx: dict[str, Any], adapter) -> VoiceResult:
        """LLM path for deeper QA review (opt-in)."""
        result = self.analyze(ctx)
        if adapter is None:
            return result
        try:
            prompt = (
                f"You are the QA voice. Review this for test coverage gaps, "
                f"missing edge cases, and risky skip/xfail patterns. "
                f"Be concise.\n\n"
                f"Question: {ctx.get('question', '')}\n"
                f"Context: {ctx.get('context', '')}"
            )
            out = adapter.generate(prompt, max_tokens=192, temperature=0.3)
            text = getattr(out, "text", str(out))[:300]
            result.analysis = f"LLM: {text}"
            if any(w in text.lower() for w in ("untested", "coverage", "edge", "skip", "gap")):
                result.concerns = [result.analysis] + result.concerns
        except Exception:
            pass
        return result