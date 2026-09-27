# conscio/council_traits.py
"""Trait extraction for the Council (spec 2026-09-26, section 5.1).

``extract_traits(question, context, options) -> Traits`` reads the English
text of the Council input and lights a fixed set of nine boolean traits.
The extractor is deterministic, dependency-free, and offline: it is pure
regex over the input text.

Rules (spec section 5.1, incl. the negation emenda of 2026-09-27; Jev
``all_traits`` decision):

- The text is ``question + context + options`` (``options`` may be None or
  a list; ``context`` may be empty/None). Matching is case-insensitive and
  word-boundary anchored. English only (house rule, R-S3).
- **Absence is not a trait**: an empty context lights nothing. A trait
  lights only when one of its trigger words is present in the text.
- **Negation** (emenda 2026-09-27), applies to all nine traits:
  - A negation word cancels only the trigger occurrence it governs, and
    looks strictly backwards.
  - The window never crosses a **segment**: the question, the context,
    and each option are separate segments; a negation in one does not
    cancel a trigger in another.
  - Inside a segment the window stops at the **end of a sentence**
    (``.`` ``!`` ``?`` ``;`` followed by a space or end of text). The
    dot of ``.env`` and of a decimal like ``1.5`` is NOT a sentence end
    (it is followed by a letter/digit, not a space).
  - The **comma stays inside the window**: a negation distributes over a
    comma list — "no backup, staging or rollback" keeps every item of the
    list cancelled.
  - **Local** negators (cancel within the 3 words before the trigger, plus
    the comma-list distribution): ``no``, ``not``, ``without``, ``never``,
    ``missing``, ``none``, ``cannot``, ``lack``, ``lacks``, ``lacking``,
    and every ``n't`` contraction, straight (``don't``) or curly
    (``doesn\u2019t``) apostrophe. A contraction is ONE word: the
    tokenizer keeps it whole (hyphenated words stay one word too, so
    ``no-verify`` is not the negator ``no``).
  - **Clause** negators (cancel every trigger later in the same
    sentence): ``nobody``, ``nothing`` — a null subject negates the whole
    predicate ("nobody on the team has reviewed yet").
  - Triggers that START with a negation word (``no tests``, ``not sure``,
    ``not decided``, ``without tests``) are atomic phrases: the window is
    strictly before the match start, so they can never self-cancel.
  - **Documented limit:** a 4-word gap with no comma is out of reach —
    "None of it was verified" keeps ``verified`` lit. No test pretends to
    cover it.
- The trigger table is frozen here with each entry's provenance commented
  (the dev.jsonl ids where the word actually appears, or "spec 5.1" when it
  comes only from the spec table).

``Traits`` and ``extract_traits`` gain their production callers in T6
(the voices read the traits); until then vulture lists them — no whitelist
entries are added on purpose.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# ── negation constants (emenda 2026-09-27) ─────────────────────────────

#: Local negators: cancel the trigger within the 3-word window before it
#: (and, when a comma lies between, over a comma list of 4-5 words).
LOCAL_NEGATORS = frozenset({
    "no", "not", "without", "never", "missing", "none",
    "cannot", "lack", "lacks", "lacking",
})
#: Clause negators: cancel every trigger later in the SAME sentence
#: (a null subject negates the whole predicate: "nobody ... has reviewed").
CLAUSE_NEGATORS = frozenset({"nobody", "nothing"})
#: A token ending in n't (straight) or n\u2019t (curly) is a local negator
#: (don't, isn't, hasn't, doesn\u2019t, ...). The two apostrophe types are
#: kept separate so each is independently pinned by a teeth test.
_N_T_STRAIGHT = "n't"
_N_T_CURLY = "n\u2019t"
#: The local negation window: how many words before a trigger can carry a
#: cancelling negation.
NEGATION_WINDOW = 3
#: Comma-list distribution: a local negator also cancels a trigger this
#: many words away when a comma lies between them ("no backup, staging or
#: rollback").
_COMMA_RULE = (4, 5)
#: Sentence-final punctuation (each counts only when followed by a space
#: or end of text — so the dots of ".env" and "1.5" never break).
_SENTENCE_END = ".!?;"

# Word tokenizer: hyphenated words stay one word ("no-verify" is a flag
# name, not the negator "no"); contractions stay one word ("don't",
# "doesn\u2019t"); a decimal "1.5" tokenizes as "1" and "5".
_WORD_RE = re.compile(
    r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*(?:['\u2019][A-Za-z0-9]+)*")

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
    ``question``, ``context`` and the (optional) ``options``. The
    question, the context, and each option are NEGATION SEGMENTS: a
    negation word never crosses the join between two of them."""
    segments: list[str] = []
    if question:
        segments.append(question)
    if context:
        segments.append(context)
    if options:
        segments.extend(str(opt) for opt in options if str(opt))

    lit = {name: False for name in _COMPILED}
    for segment in segments:
        _light_in_segment(segment, lit)
    return Traits(**lit)


def _light_in_segment(segment: str, lit: dict[str, bool]) -> None:
    """Light the traits whose triggers fire in ONE negation segment.

    A trait already lit (in an earlier segment) is left alone; only
    unlit traits are matched, so the first firing segment wins and the
    result stays deterministic."""
    words = [m.group(0) for m in _WORD_RE.finditer(segment)]
    spans = [m.span() for m in _WORD_RE.finditer(segment)]
    breaks = _sentence_breaks(segment)
    for name, patterns in _COMPILED.items():
        if lit[name]:
            continue
        for pattern, _where in patterns:
            for match in pattern.finditer(segment):
                q = _first_word_index(match.start(), spans)
                if not _trigger_negated(segment, words, spans, breaks, q):
                    lit[name] = True
                    break
            if lit[name]:
                break


def _first_word_index(match_start: int, spans: list[tuple[int, int]]) -> int:
    """The index of the first word token at/after ``match_start`` (the
    trigger's first word; for ``.env`` that is the token after the dot)."""
    index = 0
    while index < len(spans) and spans[index][0] < match_start:
        index += 1
    return index


def _trigger_negated(
    segment: str, words: list[str], spans: list[tuple[int, int]],
    breaks: list[int], q: int,
) -> bool:
    """True when the trigger occurrence at word index ``q`` is cancelled by
    a negation word.

    Scans backwards from the trigger to the start of the sentence: a
    clause negator (``nobody``/``nothing``) cancels at any distance; a
    local negator cancels within NEGATION_WINDOW words, or at
    _COMMA_RULE distance when a comma lies between (list distribution).
    The scan stops at the first sentence end (``breaks``) and never
    leaves the segment. The trigger's own first word is never in the
    window, so atomic negation-start phrases cannot self-cancel."""
    for j in range(q - 1, -1, -1):
        lo, hi = spans[j][1], spans[j + 1][0]
        if any(lo <= p <= hi for p in breaks):
            break
        norm = words[j].lower()
        gap = q - j
        if norm in CLAUSE_NEGATORS:
            return True
        is_contraction = norm.endswith((_N_T_STRAIGHT, _N_T_CURLY))
        if norm in LOCAL_NEGATORS or is_contraction:
            if gap <= NEGATION_WINDOW:
                return True
            if _COMMA_RULE[0] <= gap <= _COMMA_RULE[1] \
                    and "," in segment[spans[j][1]:spans[q][0]]:
                return True
    return False


def _sentence_breaks(segment: str) -> list[int]:
    """Character positions right after a sentence end: a ``.`` ``!`` ``?``
    ``;`` followed by a space or end of text. The dots of ``.env`` and of
    decimals (``1.5``) are followed by a letter/digit, never by a space,
    so they do NOT break the window."""
    breaks: list[int] = []
    for i, ch in enumerate(segment):
        if ch in _SENTENCE_END and (i + 1 >= len(segment) or segment[i + 1] == " "):
            breaks.append(i + 1)
    return breaks
