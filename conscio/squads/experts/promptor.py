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

# Keywords whose clauses are quoted verbatim into the Constraints
# section (the caller's own words, never paraphrased into new rules).
# Substring-safe words only; the short limit words are matched with
# word boundaries by _LIMIT_RE below ("admin" must not match "min").
_CONSTRAINT_KEYWORDS = (
    "requirement", "constraint", "must", "should", "cannot", "can't",
    "don't", "do not", "avoid", "only", "without",
)

# Verbal and numeric limits are constraints too: "max 300 words",
# "at least two options", "under 5 seconds". Word boundaries keep
# "understand"/"admin" from matching "under"/"min".
_LIMIT_RE = re.compile(
    r"\b(?:max|maximum|minimum|min|at most|at least|no more than|"
    r"no less than|up to|under|over|limit(?:ed)?(?:\s+to)?)\b",
    re.IGNORECASE,
)

# Keywords whose clauses are quoted verbatim into the Output-format
# section.
_FORMAT_KEYWORDS = (
    "format", "json", "yaml", "markdown", "table", "bullet", "list",
    "length", "words", "paragraph", "tone", "audience", "style",
)

# A clause carrier ends at a newline, a sentence end, or a comma: the
# newline matters because the question and the context are joined with
# one — without it an unpunctuated question + context would read as a
# single sentence and leak whole sections into every match (D1).
_UNIT_SPLIT = re.compile(r"\n+|(?<=[.!?])\s+")
_CLAUSE_SPLIT = re.compile(r",\s+")

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


def _pick_clauses(question: str, context: str) -> tuple[list[str], list[str]]:
    """Split the input into verbatim constraint and format fragments.

    Units end at newlines and sentence ends; units are then split into
    comma clauses. A clause equal to the ENTIRE question or the ENTIRE
    context is never quoted — repeating Objective or Context inside
    another section would make the refined prompt larger, not clearer.
    Clauses with a limit or a constraint keyword go to Constraints;
    remaining clauses with a format keyword go to Output format.
    """
    whole = {s.lower() for s in (question, context) if s}
    constraints: list[str] = []
    formats: list[str] = []
    for unit in _UNIT_SPLIT.split(f"{question}\n{context}"):
        for clause in _CLAUSE_SPLIT.split(unit):
            c = clause.strip()
            if not c or c.lower() in whole:
                continue
            low = c.lower()
            if any(k in low for k in _CONSTRAINT_KEYWORDS) or _LIMIT_RE.search(c):
                constraints.append(c)
            elif any(k in low for k in _FORMAT_KEYWORDS):
                formats.append(c)
    seen_c: set[str] = set()
    constraints = [c for c in constraints
                   if not (c.lower() in seen_c or seen_c.add(c.lower()))]
    seen_f: set[str] = set()
    formats = [c for c in formats
               if not (c.lower() in seen_f or seen_f.add(c.lower()))]
    return constraints, formats


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

        # Constraints — the caller's own limit/constraint clauses, verbatim.
        constraint_clauses, format_clauses = _pick_clauses(question, context)
        if constraint_clauses:
            sections.append(
                "Constraints:\n" + "\n".join(f"- {s}" for s in constraint_clauses)
            )
        else:
            sections.append(f"Constraints: {_CONSTRAINTS_GAP}")
            changes.append("flagged unspecified constraints with an explicit marker")

        # Output format — the caller's own format clauses, verbatim.
        if format_clauses:
            sections.append(
                "Output format:\n" + "\n".join(f"- {s}" for s in format_clauses)
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
