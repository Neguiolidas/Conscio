# conscio/squads/opositors/douche_reviewer.py
"""Douche Reviewer voice — passive-aggressive code review (Ato 3, v4.4).

The terror of pull requests. Sniffs code slop, regex hacks, gambiarra
with regex, and iframe architecture. Passive-aggressive tone by design.

Tone boundary (same as Caustic):
  PERMITTED: passive-aggressive code criticism, sarcastic observations.
  BLOCKED:   personal attacks, hate speech, slurs.
Deterministic path enforces by construction; LLM path enforces by prompt.
"""
from __future__ import annotations

import re
from typing import Any

from conscio.squads._base import Voice, VoiceResult, _vote_from_concerns

# Code slop / duplication signals.
_SLOP_TOKENS = (
    "copy paste",
    "duplicated code",
    "same function",
    "copy-pasta",
    "copypasta",
    "duplicated",
    "repeated logic",
    "identical block",
)

# Regex hack signals.
_REGEX_HACK = (
    "regex with",
    "nested groups",
    "line noise",
    "200 character regex",
    "regex that",
    "unreadable regex",
    "complex regex",
)

# Iframe abuse.
_IFRAME_ABUSE = (
    "nested iframes",
    "iframe inside iframe",
    "iframe within iframe",
    "3 levels deep",
    "iframe soup",
)

# Structural: an <iframe> tag nested inside another <iframe> (raw HTML).
_IFRAME_NESTED = re.compile(r"<iframe[^>]*>.*?<iframe", re.IGNORECASE | re.DOTALL)

# General code smell.
_CODE_SMELL = (
    "god class",
    "spaghetti",
    "callback hell",
    "pyramid of doom",
    "magic number",
    "hardcoded",
    "global variable",
    "eval(",
)

# Structural: nested callbacks (callback hell / pyramid of doom) — a
# callback argument containing another callback within 8 lines.
_CALLBACK_HELL = re.compile(
    r"(?:function\s*\([^)]*\)|=>)\s*\{[^}]{0,400}?(?:function\s*\([^)]*\)|=>)",
    re.IGNORECASE | re.DOTALL,
)


class DoucheReviewerVoice(Voice):
    """Passive-aggressive code reviewer — sniffs slop and gambiarras."""

    name = "douche_reviewer"
    role = "douche_reviewer"
    description = (
        "Passive-aggressive code review: sniffs code slop, regex hacks, "
        "iframe abuse, and gambiarras."
    )

    def analyze(self, ctx: dict[str, Any]) -> VoiceResult:
        question = (ctx.get("question", "") or "").lower()
        context = (ctx.get("context", "") or "").lower()
        blob = f"{question}\n{context}"

        analysis_parts: list[str] = []
        concerns: list[str] = []

        # Code slop
        slop_hits = [t for t in _SLOP_TOKENS if t in blob]
        if slop_hits:
            analysis_parts.append(f"detected code slop: {', '.join(slop_hits[:3])}")
            concerns.append(
                "Code duplication detected — because nothing says 'I gave up' "
                "quite like copy-pasting the same function five times."
            )

        # Regex hack
        regex_hits = [t for t in _REGEX_HACK if t in blob]
        if regex_hits:
            analysis_parts.append(f"detected regex hack: {', '.join(regex_hits[:3])}")
            concerns.append(
                "Regex hack detected — this pattern has the readability of a "
                "cat walking on a keyboard. Consider a proper parser."
            )

        # Iframe abuse
        iframe_hits = [t for t in _IFRAME_ABUSE if t in blob]
        if iframe_hits:
            analysis_parts.append(f"detected iframe abuse: {', '.join(iframe_hits[:3])}")
            concerns.append(
                "Iframe nesting detected — this architecture has the structural "
                "integrity of a house of cards in a wind tunnel."
            )

        # Iframe abuse (structural: raw nested <iframe> tags)
        if not iframe_hits and _IFRAME_NESTED.search(context):
            analysis_parts.append("detected structural iframe nesting")
            concerns.append(
                "Iframe nesting detected in raw HTML — this architecture has "
                "the structural integrity of a house of cards in a wind tunnel."
            )

        # General code smell
        smell_hits = [t for t in _CODE_SMELL if t in blob]
        if smell_hits:
            analysis_parts.append(f"detected code smells: {', '.join(smell_hits[:3])}")
            concerns.append(
                f"Code smell detected ({', '.join(smell_hits[:2])}) — "
                f"this would be a great teaching example of what NOT to do."
            )

        # Callback hell / pyramid of doom (structural)
        if _CALLBACK_HELL.search(context):
            analysis_parts.append("detected callback pyramid (nesting > 1 deep)")
            concerns.append(
                "Callback nesting detected — this pyramid of doom would make "
                "a great case study in why async/await exists."
            )

        if not analysis_parts:
            analysis_parts.append(
                "No obvious code atrocities detected — for now."
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
        """LLM path for passive-aggressive code review (opt-in)."""
        result = self.analyze(ctx)
        if adapter is None:
            return result
        try:
            prompt = (
                f"You are the Douche Reviewer — a passive-aggressive code "
                f"reviewer who sniffs out slop, regex hacks, and bad "
                f"architecture. Be sarcastic and technically precise. "
                f"NEVER attack people — only code. NO hate speech or slurs. "
                f"Be concise.\n\n"
                f"Question: {ctx.get('question', '')}\n"
                f"Context: {ctx.get('context', '')}"
            )
            out = adapter.generate(prompt, max_tokens=192, temperature=0.3)
            text = getattr(out, "text", str(out))[:300]
            result.analysis = f"LLM: {text}"
            if any(w in text.lower() for w in ("slop", "hack", "bad", "poor", "smell")):
                result.concerns = [result.analysis] + result.concerns
        except Exception:
            pass
        return result