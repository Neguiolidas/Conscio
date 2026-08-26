# conscio/squads/experts/promptor.py
"""Promptor voice — prompt optimisation heuristics (Ato 2, v4.4).

Deterministic analysis of prompt quality: intent clarity, specificity,
completeness, target-AI specification, and mode selection. Always
deterministic — Promptor does NOT have an LLM path. The optimisation
logic is embedded here (derived from the Promptor specification).

Promptor analyses a prompt across four stages:
1. Identify intent, context, keywords
2. Diagnose clarity, specificity, completeness
3. Determine complexity (simple vs complex task)
4. Assess mode fit (detail vs basic)

Missing any of these signals is a concern; vague prompts with no
target AI are a hard hold.
"""
from __future__ import annotations

import re
from typing import Any

from conscio.squads._base import Voice, VoiceResult, _vote_from_concerns

# Known target AI names (case-insensitive).
_TARGET_AI = re.compile(
    r"\b(?:chatgpt|claude|gemini|llama|mistral|gpt-4|gpt-3|qwen|deepseek|openai|anthropic)\b",
    re.IGNORECASE,
)

# Mode keywords from the Promptor specification.
_MODE_KEYWORDS = ("detalhe", "basico", "detail", "basic")

# Vague / underspecified prompt signals.
_VAGUE_SIGNALS = (
    "help",
    "fix it",
    "make it better",
    "improve",
    "do something",
    "not working",
    "broken",
    "error",
)

# Task complexity indicators.
_COMPLEX_TOKENS = (
    "architecture",
    "design",
    "multi-step",
    "pipeline",
    "system",
    "refactor",
    "migration",
    "integration",
)

# Creative / technical / educational task types (from Promptor spec).
_TASK_TYPE_TOKENS = {
    "creative": ("write", "compose", "draft", "creative", "story", "poem"),
    "technical": ("implement", "debug", "optimize", "fix", "configure", "deploy"),
    "educational": ("explain", "teach", "learn", "understand", "tutorial"),
}


class PromptorVoice(Voice):
    """Prompt optimisation specialist — always deterministic, no LLM."""

    name = "promptor"
    role = "promptor"
    description = (
        "Prompt optimisation: intent clarity, specificity, completeness, "
        "target AI, mode selection."
    )

    def analyze(self, ctx: dict[str, Any]) -> VoiceResult:
        question = ctx.get("question", "") or ""
        context = ctx.get("context", "") or ""
        blob = f"{question}\n{context}"
        blob_lower = blob.lower()

        analysis_parts: list[str] = []
        concerns: list[str] = []

        # 1. Intent clarity — is the prompt too vague?
        is_vague = (
            len(question.strip()) < 10
            or any(blob_lower.startswith(v) for v in _VAGUE_SIGNALS)
            or (blob_lower.strip() in _VAGUE_SIGNALS)
        )
        if is_vague:
            analysis_parts.append("prompt intent is vague or underspecified")
            concerns.append(
                "Prompt is vague — specify the task, audience, and desired "
                "output format."
            )

        # 2. Target AI — is the model specified?
        has_target = bool(_TARGET_AI.search(blob))
        if not has_target:
            analysis_parts.append("no target AI specified")
            concerns.append(
                "No target AI specified — each model has different strengths; "
                "specify which AI (Claude, GPT-4, Gemini, etc.) for optimal "
                "results."
            )

        # 3. Specificity — does the prompt mention requirements/constraints?
        has_constraints = any(
            w in blob_lower
            for w in ("requirement", "constraint", "must", "should", "format",
                      "style", "tone", "audience", "length", "constraint")
        )
        if not has_constraints and not is_vague:
            analysis_parts.append("no explicit requirements or constraints")
            # Soft concern — not critical
            concerns.append(
                "No explicit requirements or constraints — adding format, "
                "tone, or audience details will improve output quality."
            )

        # 4. Mode selection — DETALHE vs BASICO
        has_mode = any(m in blob_lower for m in _MODE_KEYWORDS)
        if not has_mode and not is_vague:
            analysis_parts.append("no mode specified (detail vs basic)")

        # 5. Task type classification
        task_type = None
        for ttype, tokens in _TASK_TYPE_TOKENS.items():
            if any(t in blob_lower for t in tokens):
                task_type = ttype
                break
        if task_type:
            analysis_parts.append(f"detected task type: {task_type}")

        if not analysis_parts:
            analysis_parts.append("Prompt appears well-specified.")

        analysis = "; ".join(analysis_parts)
        vote = _vote_from_concerns(concerns)
        return VoiceResult(
            role=self.role,
            analysis=analysis,
            concerns=concerns,
            vote=vote,
        )

    def analyze_llm(self, ctx: dict[str, Any], adapter) -> VoiceResult:
        """Promptor is always deterministic — no LLM path."""
        raise NotImplementedError(
            f"{self.name} is deterministic-only; use analyze() instead."
        )