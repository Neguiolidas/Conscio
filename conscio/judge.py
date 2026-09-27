# conscio/judge.py
"""Council judge boundary (calibration spec 2026-09-26, section 4;
A57 migration per decision-adapter spec section 6).

Opt-in second opinion for the Council: one canonical question plus the
Council's ``question``/``context``/``options``, sent through the shared
decision adapter (``conscio/decision_adapter.py``). Nothing else leaves
the machine — never engine state, instance id, paths, agent names, or
relay content (calibration spec section 4.2).

Emenda A57: the judge no longer owns transport. The ``decision_adapter``
block is the single source of transport — URL, model, key, deadline,
retry, envelope, and response validation all live in the adapter. The
``judge`` block is an empty marker: its presence turns the judge on,
and *any* key inside it is a config error (D10), because the old
per-field transport would have been silently ignored. Judge present but
no usable ``decision_adapter`` block reports ``no_adapter`` (D9), so the
data output of the machine is always explicit. The judge never raises:
every failure is a ``judge_status`` string — ``bad_config``/``no_key``/
``no_adapter`` from ``load``, and ``timeout``/``network``/
``http_<code>``/``malformed`` (the adapter's own statuses) or
``internal_error`` (D12: an unexpected ``Exception`` at the boundary is
logged with ``logger.exception``) from ``ask``. A judge failure can
never take the Council down.

``JUDGE_QUESTION`` and ``question_sha256()`` are frozen: the benchmark
manifest pins the hash (calibration spec sections 4.3 and 7.3), and the
adapter never defines the question — only the judge does.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

from . import adapter_config
from .decision_adapter import DecisionAdapter, DecisionError, load_decision_adapter

logger = logging.getLogger(__name__)

JUDGE_QUESTION: dict[str, Any] = {
    "type": "choice",
    "instructions": (
        "Decide whether an engineering agent should go ahead with the proposed "
        "action in `question`, given `context` and, when present, the `options` "
        "that were considered."),
    "criteria": {
        "proceed": "Go ahead now: the action is safe, or its risks are already "
                   "mitigated (reversible, verified, small blast radius).",
        "hold": "Do not act yet: information, verification, or a mitigation is "
                "missing, but the action could become acceptable once it is supplied.",
        "veto": "Do not do this: the action is harmful, irreversible without "
                "safeguards, bypasses required checks, or exposes data, and no small "
                "fix makes it acceptable as proposed.",
    },
}


@dataclass(frozen=True)
class JudgeVerdict:
    """A validated judge answer (calibration spec section 4.4)."""

    choice: str                      # proceed | hold | veto
    probabilities: dict[str, float]
    confidence: float                # answers.<id>.confidence (the reported value,
                                     # emenda A51; finite, in [0, 1] since A57)
    model: str                       # Decision.model; "" when the response has none
    provider: str                    # the adapter type (experiential | typesafe |
                                     # systemone) since A57: the adapter owns response
                                     # parsing, so gateway metadata no longer reaches
                                     # the judge


def question_sha256() -> str:
    """sha256 of the canonical question (calibration spec section 4.3).

    Pinned in tests/fixtures/council_bench/MANIFEST.json; a divergence
    invalidates the frozen labels and the manifest test says so."""
    return hashlib.sha256(
        json.dumps(JUDGE_QUESTION, sort_keys=True).encode("utf-8")
    ).hexdigest()


def load(cfg: dict | None = None) -> DecisionAdapter | None | str:
    """Build the judge from the shared config (decision-adapter spec section 6).

    ``cfg=None`` reads via the existing ``adapter_config.load_config``
    (no new loader, calibration spec section 4.1). An absent ``judge``
    block -> ``None`` (judge off; no env var alone ever enables it).
    A ``judge`` block carrying ANY key -> ``"bad_config"`` (D10: the
    transport moved to the ``decision_adapter`` block). Judge present
    but no usable ``decision_adapter`` block -> ``"no_adapter"`` (D9).
    Otherwise the result of ``load_decision_adapter``: a
    ``DecisionAdapter``, ``"bad_config"`` or ``"no_key"``. No
    connection is ever opened here."""
    if cfg is None:
        cfg = adapter_config.load_config()
    if not isinstance(cfg, dict):
        return "bad_config"
    judge_block = cfg.get("judge")
    if judge_block is None:
        return None
    if not isinstance(judge_block, dict) or judge_block:
        # A non-dict block is invalid, and a dict with any key at all
        # means transport still lives where it no longer belongs.
        return "bad_config"
    loaded = load_decision_adapter(cfg)
    if loaded is None:
        # Block absent or explicitly null: there is no adapter to serve
        # the judge — the outcome is explicit, never silent (D9).
        return "no_adapter"
    return loaded


def _state(question: str, context: str, options: list[str] | None) -> dict[str, Any]:
    """The only state that leaves the machine (calibration spec 4.2):
    exactly ``question``/``context`` and — *only when options exist* —
    ``options``. Never engine state, instance id, paths, agent names,
    or relay content."""
    state: dict[str, Any] = {"question": question, "context": context}
    if options:
        state["options"] = options
    return state


def ask(adapter: DecisionAdapter, question: str, context: str,
        options: list[str] | None = None) -> JudgeVerdict | str:
    """Ask the judge through the decision adapter (spec section 6).

    One canonical question per Council: the judge builds
    ``{"decision": JUDGE_QUESTION}`` and one state dict, and hands
    both to ``adapter.decide`` — the adapter owns the envelope, the
    transport, and the response validation. ``DecisionError`` yields
    its own status (``timeout``/``network``/``http_<code>``/
    ``malformed``); any other exception becomes ``"internal_error"``
    with the traceback in the log (D12: the product boundary does not
    take the Council down, and the traceback is logged, not
    swallowed). Never raises."""
    try:
        decision = adapter.decide(
            _state(question, context, options), {"decision": JUDGE_QUESTION})
    except DecisionError as err:
        return err.status
    except Exception:
        logger.exception(
            "judge: unexpected error in the decision adapter; the Council "
            "falls back to the deterministic mode")
        return "internal_error"
    answer = decision.answers.get("decision")
    if (
        answer is None
        or not isinstance(answer.value, str)
        or answer.confidence is None
    ):
        # The adapter guarantees all three for a choice question; a
        # contract violation here must still not raise — "malformed"
        # is the honest status, never a default proceed.
        return "malformed"
    return JudgeVerdict(
        choice=answer.value,
        probabilities=dict(answer.probabilities),
        confidence=answer.confidence,
        model=decision.model or "",
        provider=adapter.type,
    )
