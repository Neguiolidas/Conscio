# conscio/squads/experts/promptor.py
"""Promptor voice — prompt REFINER (v4.8.1).

The Promptor does not evaluate or vote. It receives a prompt and returns
the refined prompt: the input restructured into Objective / Context /
Constraints / Output format sections, plus a short list of what changed.

Two rules anchor the refinement:

- **Never invent a fact.** Every substantive line of the refined prompt
  is verbatim text from the question or the context. When a constraint
  or a format is named inside a larger clause, the section quotes the
  MINIMAL fragment that carries it (from the keyword — or the
  preposition right before it — to the end of the clause), never the
  whole question and never a paraphrase. Anything the refiner adds
  itself is an explicit gap marker (a bracketed ``[UNSPECIFIED — …]``
  note or a question inside the refined prompt) telling the caller what
  to supply — never fabricated content.
- **No verdict.** The voice is registered with ``voting = False``, so
  ``convene_squad`` excludes it from the recommendation and from
  ``votes_summary``; its payload carries the refinement, not a review.

The detection focus is ENGLISH; the section labels and the gap markers
always stay in English. The Portuguese keyword extras exist for
Portuguese input and were chosen to never collide with English words
(the ambiguous ``tom`` is not detected at all, and ``sem`` only opens a
clause), so English input can never regress because of them.

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
# Every keyword is matched with word boundaries (D4): "stable"/"notable"
# must not light "table", "commonly" must not light "only". Portuguese
# extras (D5) are additive and chosen to never collide with English
# words; the risky "tom" is dropped entirely ("Tom" the name) and "sem"
# only matches at the START of a clause ("Sem rodeios, ..." — English
# "SEM budget" mid-clause stays untouched).
_CONSTRAINT_RE = re.compile(
    r"\b(?:requirements?|constraints?|must|should|cannot|can't|don't|"
    r"do\s+not|avoid|only|without|"
    r"deve|n[ãa]o|evite|apenas|somente)\b",
    re.IGNORECASE,
)

# "Sem ..." only counts when it opens the clause (see the collision note
# above); _SEM_CLAUSE_START is checked separately from _CONSTRAINT_RE.
_SEM_CLAUSE_START = re.compile(r"^\s*sem\b", re.IGNORECASE)

# Verbal and numeric limits are constraints too: "max 300 words",
# "at least two options", "under 5 seconds"; PT: "no máximo 300 palavras".
# Two tiers (T3b): UNAMBIGUOUS phrases always count — including the
# accented "até" and the explicit "to a minimum" / "ao máximo" family —
# while AMBIGUOUS words ("max", "under", "over", "up to", the unaccented
# English "ate"...) only count when a DIGIT lands within the next three
# words: "Max out the cache" is a verb, "max 300 words" is a limit;
# "The dog ate the homework" is past tense, "ate 200 palavras" is a
# limit. Word boundaries keep "understand"/"admin" from matching
# "under"/"min"; \b is Unicode-aware in Python str regex, so accents
# work.
_LIMIT_ALWAYS_RE = re.compile(
    r"\b(?:at most|at least|no more than|no less than|"
    r"limit(?:ed)?\s+to|"
    r"to\s+a\s+(?:minimum|maximum)|ao\s+(?:m[íi]nimo|m[áa]ximo)|"
    r"no\s+m[áa]ximo|no\s+m[íi]nimo|limite\s+de|at[é])\b",
    re.IGNORECASE,
)
_LIMIT_IF_NUMBER_RE = re.compile(
    r"\b(?:max|maximum|minimum|min|m[áa]ximo|m[íi]nimo|"
    r"up\s+to|under|over|mais\s+de|menos\s+de|ate)\b"
    r"(?:\s+[^\W\d_]\S*){0,3}\s+\d",
    re.IGNORECASE,
)

# Keywords whose clauses are quoted verbatim into the Output-format
# section — word boundaries with the plurals that make sense (D4);
# PT extras (D5) carry their own accents, "tom" is NOT here.
_FORMAT_RE = re.compile(
    r"\b(?:formats?|json|yaml|markdown|tables?|bullets?|lists?|length|"
    r"words?|paragraphs?|tone|audience|style|"
    r"formato|listas?|tabelas?|t[óo]picos?|palavras?|par[áa]grafos?|"
    r"p[úu]blico|estilo)\b",
    re.IGNORECASE,
)

# When quoting a fragment (D3), it may start one token earlier if that
# token is a preposition: "in markdown", "em markdown" — the user's own
# connector, still verbatim.
_PREP_BEFORE = re.compile(
    r"\b(in|as|with|using|via|on|at|to|of|em|como|no|na|de|por|com)\s+$",
    re.IGNORECASE,
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


def _fragment(clause: str, kw_start: int) -> str:
    """The user's own words from the keyword to the end of the clause
    (D3: minimal verbatim cut), optionally opened by the preposition
    immediately before the keyword ("in markdown", "em markdown")."""
    pre = _PREP_BEFORE.search(clause, 0, kw_start)
    start = pre.start() if pre else kw_start
    return clause[start:].strip()


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for item in items:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def _pick_clauses(question: str, context: str) -> tuple[list[str], list[str]]:
    """Split the input into verbatim constraint and format fragments.

    Units end at newlines and sentence ends; units are then split into
    comma clauses. A clause carrying a limit or a constraint keyword
    contributes a MINIMAL fragment to Constraints (from the keyword, or
    the preposition before it, to the end of the clause — D3) and never
    also to Output format; remaining clauses with a format keyword
    contribute to Output format the same way. The fragment is never the
    ENTIRE question or context (that would repeat Objective/Context):
    when the only match sits at the very start of a whole-section
    clause, the clause is skipped rather than quoted whole.
    """
    whole = {s.lower() for s in (question, context) if s}
    constraints: list[str] = []
    formats: list[str] = []
    for unit in _UNIT_SPLIT.split(f"{question}\n{context}"):
        for clause in _CLAUSE_SPLIT.split(unit):
            c = clause.strip()
            if not c:
                continue
            low = c.lower()
            c_match = _CONSTRAINT_RE.search(c) or _SEM_CLAUSE_START.match(c)
            l_starts = [m.start() for m in (
                _LIMIT_ALWAYS_RE.search(c), _LIMIT_IF_NUMBER_RE.search(c),
            ) if m]
            starts = l_starts + ([c_match.start()] if c_match else [])
            first = min(starts) if starts else None
            if first is not None:
                frag = _fragment(c, first)
                if not (frag.lower() == low and low in whole):
                    constraints.append(frag)
                continue
            f_match = _FORMAT_RE.search(c)
            if f_match:
                frag = _fragment(c, f_match.start())
                if not (frag.lower() == low and low in whole):
                    formats.append(frag)
    return _dedupe(constraints), _dedupe(formats)


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
