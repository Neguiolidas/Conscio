# conscio/squads/experts/promptor.py
"""Promptor voice — prompt REFINER (v4.8.1).

The Promptor does not evaluate or vote. It receives a prompt and returns
the refined prompt: the input restructured into Objective / Context /
Constraints / Output format sections, plus a short list of what changed.

Two rules anchor the refinement:

- **Never invent a fact.** Every substantive line of the refined prompt
  is verbatim text from the question or the context. Anything the
  refiner adds itself is an explicit gap marker (a bracketed
  ``[UNSPECIFIED — …]`` note or a question inside the refined prompt)
  telling the caller what to supply — never fabricated content.
- **No verdict.** The voice is registered with ``voting = False``, so
  ``convene_squad`` excludes it from the recommendation and from
  ``votes_summary``; its payload carries the refinement, not a review.

LLM path (``use_llm=True``): the adapter rewrites the prompt. On any
LLM failure the deterministic refinement below is returned unchanged —
the refiner never raises and never blocks a squad convene.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from conscio.squads._base import Voice, VoiceResult

# Known target AI names (case-insensitive) — used only to decide whether
# naming the target model belongs in the gap markers.
_TARGET_AI = re.compile(
    r"\b(?:chatgpt|claude|gemini|llama|mistral|gpt-4|gpt-3|qwen|deepseek|openai|anthropic)\b",
    re.IGNORECASE,
)

# Keywords whose sentences are quoted verbatim into the Constraints
# section (the caller's own words, never paraphrased into new rules).
_CONSTRAINT_KEYWORDS = (
    "requirement", "constraint", "must", "should", "cannot", "can't",
    "don't", "do not", "avoid", "only", "without",
)

# Keywords whose sentences are quoted verbatim into the Output-format
# section.
_FORMAT_KEYWORDS = (
    "format", "json", "yaml", "markdown", "table", "bullet", "list",
    "length", "words", "paragraph", "tone", "audience", "style",
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

# Gap markers: the refiner's own words, always bracketed and always an
# instruction to supply the missing piece — never a fabricated value.
_CONSTRAINTS_GAP = (
    "[UNSPECIFIED — state any constraints (musts, limits, tone, audience); "
    "the model will assume its own otherwise]"
)
_FORMAT_GAP = (
    "[UNSPECIFIED — say what the output should look like (format, length, "
    "structure)]"
)


@dataclass
class PromptRefinement(VoiceResult):
    """Result of the Promptor voice: a refined prompt, not a review.

    ``role``/``analysis``/``concerns``/``vote`` keep the VoiceResult shape
    for uniform serialization; for the refiner they are plumbing —
    ``convene_squad`` emits them empty and does not count the vote.
    """

    refined_prompt: str = ""
    changes: list[str] = field(default_factory=list)

    def extras(self) -> dict[str, Any]:
        """The artefact this voice produces (merged into the result entry)."""
        return {"refined_prompt": self.refined_prompt, "changes": list(self.changes)}


def _sentences_with(blob: str, keywords: tuple[str, ...]) -> list[str]:
    """Verbatim sentences of ``blob`` containing any keyword."""
    hits: list[str] = []
    for sentence in _SENTENCE_SPLIT.split(blob):
        low = sentence.lower()
        if any(k in low for k in keywords) and sentence.strip():
            hits.append(sentence.strip())
    return hits


class PromptorVoice(Voice):
    """Prompt refinement specialist — rewrites, never evaluates."""

    name = "promptor"
    role = "promptor"
    # Non-voting: the refiner's output rides along in the result payload
    # but never enters _compute_recommendation or votes_summary.
    voting = False
    description = (
        "Prompt refiner: returns refined_prompt (Objective / Context / "
        "Constraints / Output format) plus the list of changes. Does not "
        "vote."
    )

    def analyze(self, ctx: dict[str, Any]) -> PromptRefinement:
        question = (ctx.get("question", "") or "").strip()
        context = (ctx.get("context", "") or "").strip()
        blob = f"{question}\n{context}"

        sections: list[str] = []
        changes: list[str] = []

        # Objective — the ask, verbatim. The refiner never rewrites the
        # substance of the request into something new.
        sections.append(f"Objective: {question}")
        changes.append("structured the prompt into labelled sections")

        # Context — verbatim, only when supplied.
        if context:
            sections.append(f"Context: {context}")
        else:
            sections.append(
                "Context: [UNSPECIFIED — add background the model needs; "
                "omit this section if none applies]"
            )
            changes.append("flagged the missing context with an explicit marker")

        # Constraints — the caller's own constraint sentences, verbatim.
        constraint_sentences = _sentences_with(blob, _CONSTRAINT_KEYWORDS)
        if constraint_sentences:
            sections.append(
                "Constraints:\n" + "\n".join(f"- {s}" for s in constraint_sentences)
            )
        else:
            sections.append(f"Constraints: {_CONSTRAINTS_GAP}")
            changes.append("flagged unspecified constraints with an explicit marker")

        # Output format — the caller's own format sentences, verbatim.
        format_sentences = _sentences_with(blob, _FORMAT_KEYWORDS)
        if format_sentences:
            sections.append(
                "Output format:\n" + "\n".join(f"- {s}" for s in format_sentences)
            )
        elif not _TARGET_AI.search(blob):
            # A prompt that names no target model and no format is the
            # classic vague case: ask, don't guess.
            sections.append(f"Output format: {_FORMAT_GAP}")
            changes.append("flagged the unspecified output format with an explicit marker")

        refined = "\n\n".join(sections)
        return PromptRefinement(
            role=self.role,
            analysis="",
            concerns=[],
            vote="proceed",  # plumbing only; the voice does not vote
            refined_prompt=refined,
            changes=changes,
        )

    def analyze_llm(self, ctx: dict[str, Any], adapter) -> PromptRefinement:
        """LLM path: the adapter rewrites the prompt (opt-in).

        Falls back to the deterministic refinement on any failure — the
        refiner never raises into the squad convene.
        """
        result = self.analyze(ctx)
        if adapter is None:
            return result
        try:
            prompt = (
                "Rewrite the following prompt so a language model produces a "
                "better answer. Keep every fact from the original; never "
                "invent facts — turn missing pieces into explicit questions "
                "inside the rewritten prompt. Return ONLY the rewritten "
                "prompt text.\n\n"
                f"PROMPT:\n{ctx.get('question', '')}\n\n"
                f"CONTEXT:\n{ctx.get('context', '')}"
            )
            out = adapter.generate(prompt, max_tokens=384, temperature=0.3)
            text = getattr(out, "text", str(out)).strip()
            if text:
                return PromptRefinement(
                    role=self.role,
                    analysis="",
                    concerns=[],
                    vote="proceed",
                    refined_prompt=text,
                    changes=["rewritten by the LLM adapter"],
                )
        except Exception:
            # LLM path is advisory — fall back to deterministic.
            pass
        return result
