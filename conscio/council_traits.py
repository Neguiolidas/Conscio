# conscio/council_traits.py
"""Trait extraction for the Council (spec 2026-09-26, section 5.1).

``extract_traits(question, context, options) -> Traits`` reads the English
text of the Council input and lights a fixed set of nine boolean traits.
The extractor is deterministic, dependency-free, and offline: it is pure
regex over the input text.

Rules (spec section 5.1, Jev ``all_traits`` decision):

- The text is ``question + context + options`` (``options`` may be None or
  a list; ``context`` may be empty/None). Matching is case-insensitive and
  word-boundary anchored. English only (house rule, R-S3).
- **Absence is not a trait**: an empty context lights nothing. A trait
  lights only when one of its trigger words is present in the text.
- **Negation**: a negation word (``no``, ``not``, ``without``, ``never``,
  ``missing``) within the 3 words immediately BEFORE a trigger occurrence
  cancels that occurrence only. The trait still lights if any other
  occurrence survives. The window looks strictly backwards: "feature flag
  ... without a redeploy" keeps ``reversible`` lit (dev m03). Triggers that
  START with a negation word ("no tests", "not sure", "not decided",
  "without tests") are atomic phrases and never self-cancel.
- The trigger table is frozen here with each entry's provenance commented
  (the dev.jsonl ids where the word actually appears, or "spec 5.1" when it
  comes only from the spec table).

``Traits`` and ``extract_traits`` gain their production callers in T6
(the voices read the traits); until then vulture lists them — no whitelist
entries are added on purpose.
"""
from __future__ import annotations

import bisect
import re
from dataclasses import dataclass

#: Negation words. A negation within NEGATION_WINDOW words before a trigger
#: occurrence cancels that occurrence (backward-only).
NEGATION_WORDS = frozenset({
    "no", "not", "without", "never", "missing",
})
#: How many words before a trigger can still carry a cancelling negation.
NEGATION_WINDOW = 3

# Word tokenizer for the negation window: hyphenated words stay one token
# ("no-verify" is a flag name, not the negation "no").
_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*")

# Trigger table: trait -> (pattern, provenance). Every pattern is compiled
# case-insensitive; single-word triggers are word-boundary anchored.
# Provenance: the dev.jsonl ids where the word appears, or "spec 5.1" when
# the trigger comes only from the spec table.
TRIGGERS: dict[str, list[tuple[str, str]]] = {
    # The action destroys or rewrites something with no way back.
    "irreversible": [
        (r"\bdelete\b", "dev b10 c1-01 c1-18 m11 m12; spec 5.1"),
        (r"\bdrop(?:s|ping|ped)?\b", "dev b23 c1-06 c1-18 m01 m02; spec 5.1"),
        (r"\bforce[\s-]*push\b", "spec 5.1"),
        (r"\brewrite\b", "dev b22; spec 5.1"),
        (r"\btruncate\b", "spec 5.1"),
        (r"\bpurge\b", "spec 5.1"),
        (r"\brm\s+-rf\b", "spec 5.1"),
        (r"\bdiscard\b", "dev c1-03"),
        (r"\bdestroy\b", "dev c1-10"),
    ],
    # Affects something shared or public.
    "blast_radius": [
        (r"\bproduction\b", "dev b05 b11 b23 b30 c1-02 c1-04 c1-14 c1-16 c1-18 m01 m02; spec 5.1"),
        (r"\bmain\b", "dev b25 c1-01 c1-08 m05 m06; spec 5.1"),
        (r"\ball\s+users\b", "dev b12 c1-16 m03 m04; spec 5.1"),
        (r"\bpublic\b", "dev b24 c1-03; spec 5.1"),
        (r"\bpublish(?:ed|ing)?\b", "dev a04; spec 5.1"),
        (r"\bcustomers?\b", "dev c1-08; spec 5.1"),
        (r"\bshared\b", "dev b09 c1-03"),
    ],
    # Skips a required verification.
    "bypasses_checks": [
        (r"\bskip(?:s|ped|ping)?\b", "dev c1-02 c1-17; spec 5.1"),
        (r"\bno[\s-]?verify\b", "dev b25; spec 5.1"),
        (r"\bdisable[sd]?\b", "dev b21; spec 5.1"),
        (r"\bbypass\b", "dev c1-04; spec 5.1"),
    ],
    # Asserts without proof.
    "unverified": [
        (r"\buntested\b", "dev c1-02; spec 5.1"),
        (r"\bno\s+tests?\b", "spec 5.1 (atomic: starts with a negation word)"),
        (r"\bwithout\s+tests?\b", "plan T4 emenda (atomic: starts with a negation word)"),
        (r"\bnot\s+sure\b", "spec 5.1 (atomic: starts with a negation word)"),
        (r"\bprobably\b", "spec 5.1"),
        (r"\bassum(?:e|es|ed|ing)\b", "spec 5.1"),
        (r"\bshould\s+work\b", "spec 5.1"),
    ],
    # Exposes a secret or personal data.
    "data_exposure": [
        (r"\bplaintext\b", "dev b22 c1-14; spec 5.1"),
        (r"\bpasswords?\b", "dev b22; spec 5.1"),
        (r"\bcredentials?\b", "dev c1-14; spec 5.1"),
        (r"\bapi[\s-]*keys?\b", "dev a22 b24; spec 5.1"),
        (r"\btokens?\b", "spec 5.1"),
        (r"\bpii\b", "spec 5.1"),
        (r"\.env\b", "dev b24; spec 5.1"),
    ],
    # The text DECLARES that information is missing.
    "underspecified": [
        (r"\bTBD\b", "dev b16; spec 5.1"),
        (r"\bunknown\b", "dev b12; spec 5.1"),
        (r"\bsomehow\b", "spec 5.1"),
        (r"\bnot\s+decided\b", "spec 5.1 (atomic: starts with a negation word)"),
        (r"\bunclear\b", "dev c1-15; spec 5.1"),
    ],
    # Mitigator: there is a way back.
    "reversible": [
        (r"\bbackups?\b", "dev b23 c1-01 m01 m02; spec 5.1"),
        (r"\brollback\b", "dev b05 c1-09 c1-13 c1-16; spec 5.1"),
        (r"\brevert(?:s|ed|ing)?\b", "dev b07; spec 5.1"),
        (r"\bfeature[\s-]*flags?\b", "dev b05 c1-06 c1-09 m03 m04; spec 5.1"),
        (r"\bdry[\s-]*runs?\b", "dev b03; spec 5.1"),
        (r"\bstaging\b", "dev b05 b06 c1-10 c1-16; spec 5.1"),
    ],
    # Mitigator: it was already checked.
    "verified": [
        (r"\breviewed\b", "dev b15 c1-01 c1-05 m05 m06; spec 5.1"),
        (r"\bmeasured\b", "dev a08 a16 b06 m05 m06; spec 5.1"),
        (r"\bverified\b", "dev c1-13 m07 m08; spec 5.1"),
        (r"\bci\s+(?:is\s+)?green\b", "spec 5.1"),
        (r"\btests?\s+pass(?:es|ed)?\b", "spec 5.1"),
    ],
    # Mitigator: the cost of being wrong is small.
    "low_stakes": [
        (r"\btypos?\b", "dev c1-03 c1-11 m09 m10; spec 5.1"),
        (r"\bdocs?\s+only\b", "spec 5.1"),
        (r"\bdocumentation[\s-]*only\b", "dev c1-11; spec 5.1"),
        (r"\bcomments?\b", "dev c1-12; spec 5.1"),
        (r"\bprivate\b", "dev c1-01 m09 m10; spec 5.1"),
        (r"\blocal\b", "dev b10 m11 m12; spec 5.1"),
        (r"\bscratch\b", "dev b10 m11 m12; spec 5.1"),
    ],
}

#: Compiled once; (pattern, provenance) pairs keep the documentation next to
#: the source the table was cut from.
_COMPILED: dict[str, list[tuple[re.Pattern, str]]] = {
    trait: [(re.compile(pat, re.IGNORECASE), where)
            for pat, where in pats]
    for trait, pats in TRIGGERS.items()
}


@dataclass(frozen=True)
class Traits:
    """The nine council traits (spec section 5.1). All default False:
    absence of evidence is not a trait."""

    irreversible: bool = False
    blast_radius: bool = False
    bypasses_checks: bool = False
    unverified: bool = False
    data_exposure: bool = False
    underspecified: bool = False
    reversible: bool = False
    verified: bool = False
    low_stakes: bool = False


def extract_traits(
    question: str,
    context: str | None = "",
    options: list[str] | None = None,
) -> Traits:
    """Light the traits present in the council input (spec section 5.1).

    Deterministic pure function: same inputs, same Traits, every time.
    No I/O, no network, no engine state — only the words of
    ``question``, ``context`` and the (optional) ``options``."""
    parts: list[str] = [question or ""]
    if context:
        parts.append(context)
    if options:
        parts.extend(str(opt) for opt in options)
    text = " ".join(parts)

    tokens = list(_WORD_RE.finditer(text))
    word_texts = [t.group(0).lower() for t in tokens]
    word_ends = [t.end() for t in tokens]

    lit = {name: False for name in _COMPILED}
    for name, patterns in _COMPILED.items():
        for pattern, _where in patterns:
            for match in pattern.finditer(text):
                if _negated(match.start(), word_texts, word_ends):
                    continue
                lit[name] = True
                break
    return Traits(**lit)


def _negated(match_start: int, word_texts: list[str], word_ends: list[int]) -> bool:
    """True when a negation word sits within NEGATION_WINDOW words
    IMMEDIATELY BEFORE the trigger occurrence (backward-only; spec section
    5.1). The trigger's own first word is never in the window, so atomic
    negation-start phrases ("no tests", ...) cannot self-cancel."""
    idx = bisect.bisect_right(word_ends, match_start)
    window = word_texts[max(0, idx - NEGATION_WINDOW):idx]
    return any(word in NEGATION_WORDS for word in window)
