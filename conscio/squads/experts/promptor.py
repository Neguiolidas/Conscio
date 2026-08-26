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

from conscio.squads._base import Voice, VoiceResult

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

        # 0. Scope gate — is this even a PROMPT being reviewed?
        # Promptor specialises in PROMPT optimisation. When the squad is
        # evaluating a decision/artefact (not a prompt to an LLM), the
        # "no target AI / no mode" concerns are noise. Only apply them
        # when the blob actually looks like a prompt for an LLM.
        #
        # Technical-decision markers (code, deploy, refactor, build, add,
        # implement, etc.) STRONGLY indicate NOT-a-prompt; if present,
        # skip the gate entirely unless explicit "prompt" words appear.
        _TECH_MARKERS = (
            "deploy", "refactor", "migrate", "implement",
            "endpoint", "database", "sql", "commit", "merge",
            "coverage", "pipeline", "cache", "service",
            "helper", "util", "module", "function", "class",
            "api", "code", "query", "test", "build", "add ",
            "fix ",
        )
        _PROMPT_MARKERS = (
            "prompt", "escreva", "escrever", "write", "gera", "gerar",
            "crie", "criar", "explique", "explain", "ajuda", "help me",
            "resuma", "summarize", "translate", "traduza", "email",
            "texto", "text", "curriculo", "resume", "copy", "marketing",
        )
        # If the question STARTS with a prompt verb, it's a prompt even
        # when it mentions code ("write a function", "explain this code").
        starts_with_prompt_verb = any(
            blob_lower.startswith(k) for k in _PROMPT_MARKERS
        )
        is_prompt_context = (
            starts_with_prompt_verb
            or (
                any(k in blob_lower for k in _PROMPT_MARKERS)
                and not any(k in blob_lower for k in _TECH_MARKERS)
            )
        )
        # Very short questions with no technical marker = likely a prompt
        # to an LLM ("me ajuda", "write email").
        if not is_prompt_context and len(question.strip()) < 10:
            is_prompt_context = not any(
                k in blob_lower for k in _TECH_MARKERS
            )
        if not is_prompt_context:
            analysis_parts.append("not a prompt — scope: technical decision")
            # Don't apply prompt-specific concerns; vote proceed.
            return VoiceResult(
                role=self.role,
                analysis="; ".join(analysis_parts),
                concerns=[],
                vote="proceed",
            )

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
        # Promptor vote: vagueness is the only true veto condition.
        # Missing target AI / requirements / mode are "hold" (improvable),
        # not "veto" (unusable). A vague prompt ("me ajuda") is unusable.
        if is_vague:
            vote = "veto"
        elif concerns:
            vote = "hold"
        else:
            vote = "proceed"
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