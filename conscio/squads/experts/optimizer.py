# conscio/squads/experts/optimizer.py
"""Optimizer voice — performance heuristics (Ato 1, v4.4).

Deterministic analysis of a decision/artefact for performance risks:
N+1 query patterns, blocking I/O in hot paths, algorithmic complexity
flags, and unbounded loops. Pure stdlib, no LLM.
"""
from __future__ import annotations

import re
from typing import Any

from conscio.squads._base import Voice, VoiceResult, _vote_from_concerns

# Tokens that hint at an N+1 query pattern (per-entity query in a loop).
_NP1_TOKENS = (
    "n+1",
    "n plus 1",
    "per row",
    "per user",
    "per entity",
    "separate query",
    "query separately",
)

# Structural N+1 detection: a for/while loop body containing a query
# (SELECT / db.execute / db.query / fetch / .find / .get with a query).
_LOOP_QUERY = re.compile(
    r"(?:for\s+\w+\s+in\s+[^:]+:|while\s+[^:]+:)[^\n]*(?:\n[ \t]+[^\n]*){0,8}"
    r"(?:select\s+|db\.(?:execute|query|fetch)|\.query\(|\.find\(|fetch(?:all|one)?\()",
    re.IGNORECASE | re.DOTALL,
)

# Blocking I/O in a hot path.
_IO_TOKENS = (
    "blocking call",
    "sync sleep",
    "time.sleep",
    "blocking i/o",
    "synchronous request",
    "blocking network",
    "file.io",
    "sqlite lock",
)

# Algorithmic-complexity / unbounded loop hints.
_COMPLEXITY_TOKENS = (
    "o(n^2",
    "o(n3",
    "quadratic",
    "nested loop",
    "unbounded loop",
    "while true",
    "infinite loop",
)

# Normalize text for case-insensitive token matching.
def _hl(text: str) -> str:
    return (text or "").lower()


class OptimizerVoice(Voice):
    """Performance scrutineer — flags hot-path, algorithmic, I/O risks."""

    name = "optimizer"
    role = "optimizer"
    description = (
        "Performance heuristics: hot paths, N+1 queries, blocking I/O, "
        "algorithmic complexity."
    )

    def analyze(self, ctx: dict[str, Any]) -> VoiceResult:
        question = _hl(ctx.get("question", ""))
        context = _hl(ctx.get("context", ""))
        blob = f"{question}\n{context}"

        analysis_parts: list[str] = []
        concerns: list[str] = []

        # N+1 detection (declarative hints)
        np1_hits = [t for t in _NP1_TOKENS if t in blob]
        if np1_hits:
            analysis_parts.append(
                f"detected N+1 hints: {', '.join(np1_hits[:3])}"
            )
            concerns.append(
                "N+1 query pattern suspected — per-entity queries in a loop "
                "cause quadratic DB load."
            )

        # N+1 detection (structural: loop body contains a query)
        if not np1_hits and _LOOP_QUERY.search(context):
            analysis_parts.append("detected structural N+1 (loop + query in body)")
            concerns.append(
                "N+1 query pattern suspected — a query runs inside a loop; "
                "consider a single JOIN or batch fetch."
            )

        # Blocking I/O in hot path
        io_hits = [t for t in _IO_TOKENS if t in blob]
        if io_hits:
            analysis_parts.append(
                f"detected hot-path I/O: {', '.join(io_hits[:3])}"
            )
            concerns.append(
                "Blocking I/O on the hot path — consider offloading or caching."
            )

        # Complexity / unbounded loops
        comp_hits = [t for t in _COMPLEXITY_TOKENS if t in blob]
        if comp_hits:
            analysis_parts.append(
                f"detected complexity flags: {', '.join(comp_hits[:3])}"
            )
            concerns.append(
                "Algorithmic complexity risk — unbounded loops or quadratic "
                "patterns may not scale."
            )

        if not analysis_parts:
            analysis_parts.append("No obvious performance regressions detected.")

        analysis = "; ".join(analysis_parts)
        vote = _vote_from_concerns(concerns)
        return VoiceResult(
            role=self.role,
            analysis=analysis,
            concerns=concerns,
            vote=vote,
        )

    def analyze_llm(self, ctx: dict[str, Any], adapter) -> VoiceResult:
        """LLM path for deeper performance review (opt-in)."""
        result = self.analyze(ctx)
        if adapter is None:
            return result
        try:
            prompt = (
                f"You are the Optimizer voice. Review this for performance "
                f"risks (N+1, hot-path I/O, algorithmic complexity, caching "
                f"opportunities). Be concise.\n\n"
                f"Question: {ctx.get('question', '')}\n"
                f"Context: {ctx.get('context', '')}"
            )
            out = adapter.generate(prompt, max_tokens=192, temperature=0.3)
            text = getattr(out, "text", str(out))[:300]
            result.analysis = f"LLM: {text}"
            if any(w in text.lower() for w in ("risk", "concern", "slow", "n+1")):
                result.concerns = [result.analysis] + result.concerns
        except Exception:
            # LLM path is advisory — fall back to deterministic on failure.
            pass
        return result