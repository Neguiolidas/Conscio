"""Judge boundary tests (A57: transport moved to the decision adapter).

What stays here (decision-adapter spec section 6): the config rules
(absent ``judge`` block / any key in it / ``no_adapter`` / pass-through
of the adapter loader's outcomes), the ``DecisionError``-to-status
mapping, the D12 boundary (an unexpected exception -> ``internal_error``
with the traceback in the log), the section 4.2 state contract checked
against a captured adapter call, the frozen question hash, and a fuzz
of the new ``load`` shapes.

Transport, retry, deadline, response validation, and loader cases live
in ``tests/test_decision_adapter.py`` (and
``tests/test_adapter_config_keys.py`` for the chat builder). See the
A57 report's migration table for old test -> new home; every dropped
case is covered there, except the ones explicitly re-pinned below
(strict envelope keys, out-of-range confidence, the non-UTF-8 key
file).
"""
from __future__ import annotations

import logging
import random
from typing import Any

import pytest
from council_bench import load_manifest

from conscio import judge
from conscio.decision_adapter import Answer, Decision, DecisionAdapter, DecisionError

# Keys that must never leak into the state or the questions map
# (calibration spec section 4.2).
ForbiddenKeys = {"instance", "engine", "path", "agent", "relay", "identity",
                 "workspace"}


class _StubAdapter:
    """A stand-in for the DecisionAdapter at the judge boundary: no
    network, canned ``Decision`` or scripted exception. Records every
    ``(state, questions)`` call so the section 4.2 contract can be
    checked without a server."""

    def __init__(self, result: Decision | None = None,
                 exc: BaseException | None = None,
                 type: str = "experiential") -> None:
        self.type = type
        self.calls: list[tuple[Any, dict]] = []
        self._result = result
        self._exc = exc

    def decide(self, state: Any, questions: dict[str, dict]) -> Decision:
        self.calls.append((state, questions))
        if self._exc is not None:
            raise self._exc
        assert self._result is not None
        return self._result


def _canned_decision(choice: str = "veto",
                     probs: tuple[float, float, float] = (0.05, 0.03, 0.92),
                     confidence: float = 0.87,
                     model: str | None = "jev-latest") -> Decision:
    """A choice Decision in the frozen a01 shape: the reported
    confidence (0.87) deliberately diverges from P(veto) (0.92) —
    emenda A51."""
    return Decision(
        model=model,
        answers={"decision": Answer(
            type="choice",
            value=choice,
            probabilities={"proceed": probs[0], "hold": probs[1], "veto": probs[2]},
            confidence=confidence,
        )},
    )


# ── frozen question hash (invariant since T3) ─────────────────────────


def test_question_sha256_matches_manifest():
    """Editing JUDGE_QUESTION one character invalidates the frozen
    labels; this test says so (calibration spec sections 4.3 and 7.3).
    The A57 migration keeps the literal byte-identical."""
    manifest = load_manifest()
    assert judge.question_sha256() == manifest["judge_question_sha256"]


# ── load(): the judge block is an empty marker (spec section 2 and 6) ──


def test_load_absent_block_returns_none(monkeypatch):
    monkeypatch.delenv("EXPERIENTIAL_API_KEY", raising=False)
    assert judge.load() is None                   # conftest isolates config -> {}
    assert judge.load({}) is None
    assert judge.load({"council": {}}) is None     # a different block is not the judge


def test_load_judge_with_any_key_is_bad_config(monkeypatch):
    """D10: the transport moved to the ``decision_adapter`` block — any
    key left in the ``judge`` block (url/model/api_key_env/timeout_s —
    the pre-A56 shape) is a config error, not a silently ignored
    field."""
    monkeypatch.delenv("EXPERIENTIAL_API_KEY", raising=False)
    for bad in ({"judge": {"url": "https://judge.example/v1"}},
                {"judge": {"model": "jev-latest"}},
                {"judge": {"api_key_env": "EXPERIENTIAL_API_KEY"}},
                {"judge": {"api_key_file": "~/judge-keys.env"}},
                {"judge": {"timeout_s": 5}},
                {"judge": "on"},
                {"judge": 5}):
        assert judge.load(bad) == "bad_config", bad


def test_load_judge_without_adapter_is_no_adapter(monkeypatch):
    """D9: judge present, no usable ``decision_adapter`` block (absent
    or explicitly null) -> ``no_adapter`` — an explicit status, never a
    silent fallback."""
    monkeypatch.delenv("EXPERIENTIAL_API_KEY", raising=False)
    assert judge.load({"judge": {}}) == "no_adapter"
    assert judge.load({"judge": {}, "decision_adapter": None}) == "no_adapter"


def test_load_section2_literal_is_the_experiential_preset(monkeypatch):
    """The decision-adapter spec section 2 config, loaded verbatim with
    the key in the environment: the judge comes up with the
    ``experiential`` preset (PRD criterion S1)."""
    monkeypatch.delenv("EXPERIENTIAL_API_KEY", raising=False)
    monkeypatch.setenv("EXPERIENTIAL_API_KEY", "k-env")
    cfg = {"decision_adapter": {"type": "experiential"}, "judge": {}}
    adapter = judge.load(cfg)
    assert isinstance(adapter, DecisionAdapter)
    assert adapter.type == "experiential"
    assert adapter.base_url == "https://api.experientiallabs.ai"
    assert adapter.model == "jev-latest"
    assert adapter.api_key_env == "EXPERIENTIAL_API_KEY"
    assert adapter.timeout_s == 10.0
    assert adapter.api_key == "k-env"


def test_load_no_key_passes_through(monkeypatch, tmp_path):
    """judge + decision_adapter, key unresolvable anywhere -> the
    adapter loader's ``no_key`` passes through untouched."""
    monkeypatch.delenv("EXPERIENTIAL_API_KEY", raising=False)
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setenv("CONSCIO_VAULT_DIR", str(vault))
    cfg = {
        "decision_adapter": {
            "type": "experiential",
            "api_key_file": str(tmp_path / "missing.env"),
        },
        "judge": {},
    }
    assert judge.load(cfg) == "no_key"


def test_load_bad_adapter_passes_through(monkeypatch):
    """A decision_adapter block the adapter loader rejects surfaces as
    the same ``bad_config`` through the judge boundary."""
    monkeypatch.delenv("EXPERIENTIAL_API_KEY", raising=False)
    for bad in ({"decision_adapter": {"type": "bogus"}, "judge": {}},
                {"decision_adapter": {"type": "experiential", "api_key": "inline"},
                 "judge": {}},
                {"decision_adapter": 5, "judge": {}}):
        assert judge.load(bad) == "bad_config", bad


def test_fuzz_load_contract(tmp_path, monkeypatch):
    """A57 fuzz (>=500 random config shapes, fixed seed): every shape
    must make judge.load() return DecisionAdapter | None | str WITHOUT
    raising; a str outcome is always one of bad_config / no_key /
    no_adapter; a returned adapter always carries a key. (Successor of
    the A53 load fuzz, rewritten for the new shape: the judge block is
    now an empty marker, so its fields pool over judge AND
    decision_adapter together.)"""
    rng = random.Random(7777)
    monkeypatch.delenv("EXPERIENTIAL_API_KEY", raising=False)
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setenv("CONSCIO_VAULT_DIR", str(vault))
    pools = {
        "judge": [None, {}, {"url": "https://judge.example/v1"},
                  {"model": "jev-latest"}, {"timeout_s": 5}, "on", 5, [1]],
        "decision_adapter": [None, {}, {"type": "experiential"},
                             {"type": "bogus"},
                             {"type": "experiential", "api_key": "inline"},
                             {"type": "experiential", "unknown_key": 1},
                             {"type": "systemone"}, 5, "x"],
    }
    total = 0
    violations: list[tuple[int, str]] = []
    for i in range(500):
        if i % 13 == 0:  # sometimes a non-dict top level
            cfg: Any = rng.choice(["junk", 5, [1], None])
        else:
            cfg = {}
            for key, pool in pools.items():
                if rng.random() < 0.7:
                    cfg[key] = rng.choice(pool)
        total += 1
        try:
            outcome = judge.load(cfg)
        except Exception as exc:
            violations.append((i, f"load raised {type(exc).__name__}: {exc} (cfg={cfg!r})"))
            continue
        ok = outcome is None or isinstance(outcome, (DecisionAdapter, str))
        if isinstance(outcome, DecisionAdapter) and not outcome.api_key:
            ok = False
        if isinstance(outcome, str) and outcome not in ("no_key", "bad_config", "no_adapter"):
            ok = False
        if not ok:
            violations.append((i, f"bad outcome {outcome!r} for cfg={cfg!r}"))
    assert not violations, (
        f"{len(violations)}/{total} load fuzz configs violated the contract; "
        f"first: {violations[:5]}")


# ── ask(): status mapping and the D12 boundary (spec section 6) ───────


@pytest.mark.parametrize("status", ["http_401", "http_503", "network",
                                    "timeout", "malformed"])
def test_ask_maps_decision_error_status(status):
    """Each DecisionError the adapter can raise comes back through
    ask() as the identical judge_status string — the Council's fallback
    needs nothing else to handle."""
    adapter = _StubAdapter(exc=DecisionError(status))
    assert judge.ask(adapter, "q", "c") == status
    assert len(adapter.calls) == 1  # exactly one call, no retry at the boundary


def test_ask_unexpected_exception_is_internal_error(caplog):
    """D12: an unexpected exception at the product boundary becomes
    ``internal_error`` with the traceback in the log — never raised
    back into the Council, and never swallowed silently (the
    logger.exception call is asserted, not just the status)."""
    adapter = _StubAdapter(exc=RuntimeError("boom"))
    with caplog.at_level(logging.ERROR, logger="conscio.judge"):
        assert judge.ask(adapter, "q", "c") == "internal_error"
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert errors, "the unexpected exception must be logged"
    assert any("boom" in (r.exc_text or "") for r in errors), \
        "the traceback (logger.exception) must reach the log"


# ── ask(): the verdict mapping (calibration spec section 4.4) ─────────


def test_ask_canonical_verdict_c2():
    """The typed Decision maps back to JudgeVerdict: choice, all three
    probabilities, the model-reported confidence — deliberately NOT
    probabilities[choice] (0.87 vs 0.92, as in the frozen label a01;
    emenda A51), the model from the response, and the provider as the
    adapter type (gateway metadata no longer reaches the judge since
    A57)."""
    adapter = _StubAdapter(result=_canned_decision())
    verdict = judge.ask(adapter, "push to main?", "CI green", ["push", "wait"])
    assert isinstance(verdict, judge.JudgeVerdict)
    assert verdict.choice == "veto" == max(
        verdict.probabilities, key=verdict.probabilities.get)
    assert verdict.probabilities == {"proceed": 0.05, "hold": 0.03, "veto": 0.92}
    assert verdict.confidence == 0.87
    assert verdict.confidence != verdict.probabilities[verdict.choice]
    assert verdict.model == "jev-latest"
    assert verdict.provider == "experiential"


def test_ask_model_none_is_empty_string():
    """A response without a usable model field maps to "" — the
    verdict is still valid (the adapter already validated everything
    else)."""
    adapter = _StubAdapter(result=_canned_decision(model=None))
    verdict = judge.ask(adapter, "q", "c")
    assert isinstance(verdict, judge.JudgeVerdict)
    assert verdict.model == ""


def test_ask_broken_adapter_contract_is_malformed():
    """A Decision that violates the choice contract — the answer
    missing, a non-str value, or a None confidence — is ``malformed``,
    not a raised error and never a default proceed. The adapter
    guarantees all three for a real call; this only fires on a broken
    or stubbed contract."""
    broken = [
        Decision(model="m", answers={}),
        Decision(model="m", answers={"decision":
                                     Answer("choice", 0.9, {}, None)}),
    ]
    for dec in broken:
        adapter = _StubAdapter(result=dec)
        assert judge.ask(adapter, "q", "c") == "malformed"


# ── calibration spec 4.2: what leaves the machine ─────────────────────


def test_ask_state_exact():
    """The state the judge hands to the adapter carries exactly
    question/context — options only when present — and the questions
    map is the single canonical question. Never engine state,
    instance id, path, agent name, or relay content."""
    adapter = _StubAdapter(result=_canned_decision())
    judge.ask(adapter, "ship it?", "CI green", ["ship", "hold"])
    with_opts, _ = adapter.calls[0]
    assert set(with_opts) == {"question", "context", "options"}
    assert with_opts["question"] == "ship it?"
    assert with_opts["context"] == "CI green"
    assert with_opts["options"] == ["ship", "hold"]

    adapter2 = _StubAdapter(result=_canned_decision())
    judge.ask(adapter2, "ship it?", "CI green")
    without_opts, _ = adapter2.calls[0]
    assert set(without_opts) == {"question", "context"}

    for state, qs in adapter.calls + adapter2.calls:
        assert qs == {"decision": judge.JUDGE_QUESTION}
        assert not (ForbiddenKeys & set(state))
        assert not (ForbiddenKeys & set(qs))
