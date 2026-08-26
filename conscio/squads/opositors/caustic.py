# conscio/squads/opositors/caustic.py
"""Caustic voice — acidic visual/UX critique (Ato 3, v4.4).

Destroys interfaces, humiliates CSS choices, and exposes visual hypocrisy.
Tone: acerbic, sarcastic, technically precise. NEVER attacks people —
only code, design, and architectural decisions.

Deterministic path uses pattern-matching on known bad practices.
LLM path (opt-in) refines the critique with contextual sarcasm.

Tone boundary:
  PERMITTED: sarcasm, irony, crude technical metaphors.
  BLOCKED:   personal attacks, hate speech, slurs, protected-category attacks.
The deterministic path enforces this by construction — all output
strings are curated, not generated. The LLM path is guided by a system
prompt that enforces the same boundary.
"""
from __future__ import annotations

from typing import Any

from conscio.squads._base import Voice, VoiceResult, _vote_from_concerns

# Bad CSS/layout choices.
_BAD_CSS = (
    "comic sans",
    "table layout",
    "bright yellow",
    "neon text",
    "marquee",
    "blink",
    "!important",
    "z-index: 9999",
    "shadow on everything",
    "gradient purple",
    "rainbow gradient",
)

# Visual hypocrisy: claims premium/professional but uses amateur patterns.
# Word-level matching so "SaaS premium", "premium landing page", etc. hit.
# Keep the claim words specific enough to avoid false positives on
# ordinary "modern/clean" descriptions — only strong quality claims count.
_HYPOCRISY = (
    "premium",
    "enterprise grade",
    "enterprise",
    "professional",
    "luxury",
    "high-end",
    "top-tier",
    "world-class",
)

# Amateurs signals that contradict premium claims.
_AMATEUR_SIGNALS = (
    "comic sans",
    "table layout",
    "bright yellow",
    "neon",
    "shadow on everything",
    "gradient purple",
    "rainbow",
    "blink",
    "marquee",
    "inline style",
    "z-index: 9999",
)


class CausticVoice(Voice):
    """Acidic visual/UX critic — tears apart bad design decisions."""

    name = "caustic"
    role = "caustic"
    description = (
        "Acidic visual/UX critique: exposes CSS hypocrisy, destroys bad "
        "interfaces, humiliates poor design choices."
    )

    def analyze(self, ctx: dict[str, Any]) -> VoiceResult:
        question = (ctx.get("question", "") or "").lower()
        context = (ctx.get("context", "") or "").lower()
        blob = f"{question}\n{context}"

        analysis_parts: list[str] = []
        concerns: list[str] = []

        # Bad CSS detection
        bad_hits = [t for t in _BAD_CSS if t in blob]
        if bad_hits:
            analysis_parts.append(
                f"detected questionable design choices: {', '.join(bad_hits[:3])}"
            )
            concerns.append(
                f"Design decision ({', '.join(bad_hits[:2])}) is visually "
                f"questionable — this has the aesthetic appeal of a 1997 "
                f"government form."
            )

        # Visual hypocrisy: premium claim + amateur signals
        is_premium = any(h in blob for h in _HYPOCRISY)
        is_amateur = any(a in blob for a in _AMATEUR_SIGNALS)
        if is_premium and is_amateur:
            analysis_parts.append("detected visual hypocrisy")
            concerns.append(
                "Claims to be premium/professional but ships amateur "
                "patterns — the gap between promise and reality is "
                "the design equivalent of a food truck calling itself Michelin."
            )

        # Generic bad-UX patterns
        if any(w in blob for w in ("overflow scroll", "horizontal scroll", "tiny font")):
            analysis_parts.append("detected UX anti-pattern")
            concerns.append(
                "UX anti-pattern detected — users deserve better than "
                "a horizontal scroll on a dashboard."
            )

        if not analysis_parts:
            analysis_parts.append(
                "No obvious visual atrocities detected — a rare occasion."
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
        """LLM path for contextual acidic critique (opt-in)."""
        result = self.analyze(ctx)
        if adapter is None:
            return result
        try:
            prompt = (
                f"You are the Caustic voice — an acidic, sarcastic design "
                f"critic. Destroy bad interfaces, mock poor CSS choices, "
                f"expose visual hypocrisy. Be technically precise and "
                f"sarcastic. NEVER attack people — only code and design. "
                f"NO hate speech, slurs, or personal attacks. Be concise.\n\n"
                f"Question: {ctx.get('question', '')}\n"
                f"Context: {ctx.get('context', '')}"
            )
            out = adapter.generate(prompt, max_tokens=192, temperature=0.3)
            text = getattr(out, "text", str(out))[:300]
            result.analysis = f"LLM: {text}"
            if any(w in text.lower() for w in ("bad", "poor", "amateur", "ugly", "terrible")):
                result.concerns = [result.analysis] + result.concerns
        except Exception:
            pass
        return result