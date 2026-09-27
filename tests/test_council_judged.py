"""Council judged-mode tests (calibration spec section 6, plan T5).

Covers: C2 (end-to-end against a local loopback stub server), C6 down
(lockdown / metabolic-critical / low-coherence, both modes), C6 up
(no interference: a ready engine keeps hold; a not-ready engine keeps
veto), C7 (judge off even when the key env var is present — sockets
explode), C10 (the additive shape and the gate invariant in both
modes), and section 6.4 (the consumers: format_council reads
result["mode"]; the outcome snapshot carries it). Every test's teeth
mutation is recorded in the A59 report (break it red, restore green).

All stubs are local: a fake adapter object, or a loopback HTTP server
for the C2 end-to-end run. Zero real network.

Note (A59 deviation, flagged in the report): spec section 6.3 lists a
"provider" key in the judged-mode judge dict; the neutral adapter
(G35/G36) dropped provider from JudgeVerdict, so the judge dict here
carries the fields JudgeVerdict actually has (verdict, probabilities,
confidence, model).
"""
from __future__ import annotations

import http.server
import json
import socket
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from conscio import ConsciousnessEngine, gates
from conscio import judge as judge_mod
from conscio.decision_adapter import Answer, Decision, DecisionAdapter
from conscio.mcp.mode_router import ModeRouter
from conscio.outcomes import OutcomeStore, capture_council_outcome


def _adapter_returning(choice: str, confidence: float = 0.9) -> DecisionAdapter:
    """A DecisionAdapter that never touches the network: decide()
    returns the canned Decision directly."""
    adapter = DecisionAdapter(
        url="http://127.0.0.1:1/unused",
        model="jev-latest",
        api_key_env="T5_TEST_KEY",
        api_key_file=None,
        timeout_s=10.0,
        api_key="stub-key",
    )

    def _decide(state, questions):
        criteria = questions["decision"]["criteria"]
        probs = {name: round((1.0 - confidence) / (len(criteria) - 1), 4)
                 for name in criteria}
        probs[choice] = confidence
        return Decision(
            model="jev-latest",
            answers={"decision": Answer(
                type="choice",
                value=choice,
                probabilities=probs,
                confidence=confidence,
            )},
        )

    object.__setattr__(adapter, "decide", _decide)
    return adapter


@pytest.fixture
def engine(tmp_path):
    """A clean engine with coherence pre-set so the council's
    auto-reflect never rebuilds _state under the test's feet."""
    with ConsciousnessEngine(model_name="test", storage_path=str(tmp_path)) as e:
        e.last_coherence = SimpleNamespace(score=0.8)
        yield e


def _judge_off(monkeypatch):
    """The default suite state: no judge block anywhere -> off."""
    monkeypatch.setattr(judge_mod, "load", lambda cfg=None: None)


# ── C2: judged end-to-end against a loopback stub server ─────────────


def _start_canonical_stub() -> tuple[http.server.ThreadingHTTPServer, int]:
    """A loopback server answering the canonical Jev shape (choice)."""
    response = {
        "model": "jev-latest",
        "answers": {
            "decision": {
                "type": "choice",
                "choice": "veto",
                "probabilities": {"proceed": 0.02, "hold": 0.06, "veto": 0.92},
                "confidence": 0.92,
            }
        },
    }

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.dumps(response).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def test_c2_judged_end_to_end_with_stub(engine, monkeypatch):
    """The stub server returns a canonical Jev answer; the council
    recommends it, in judged mode, with the full judge report."""
    server, port = _start_canonical_stub()
    try:
        adapter = DecisionAdapter(
            url=f"http://127.0.0.1:{port}",
            model="jev-latest",
            api_key_env="T5_TEST_KEY",
            api_key_file=None,
            timeout_s=5.0,
            api_key="stub-key",
        )
        monkeypatch.setattr(judge_mod, "load", lambda cfg=None: adapter)
        result = gates.council(engine, question="drop the orders table", context="no backup")
    finally:
        server.shutdown()
        server.server_close()

    assert result["mode"] == "judged"
    assert result["judge_status"] == "ok"
    assert result["recommendation"] == "veto"
    judge = result["judge"]
    assert judge["verdict"] == "veto"
    assert judge["probabilities"] == {"proceed": 0.02, "hold": 0.06, "veto": 0.92}
    assert judge["confidence"] == 0.92
    assert judge["model"] == "jev-latest"


# ── engine_readiness (spec section 6.1) ──────────────────────────────


def test_engine_readiness_reasons_in_order(engine):
    engine._state.action_lockdown = True
    engine._state.metabolic = "FATIGUE CRITICAL"
    engine.last_coherence = SimpleNamespace(score=0.2)
    assert gates.engine_readiness(engine) == [
        "action_lockdown",
        "metabolic_critical",
        "low_coherence",
    ]


def test_engine_readiness_ready_engine_is_empty(engine):
    assert gates.engine_readiness(engine) == []


def test_engine_readiness_none_coherence_is_not_a_reason(engine):
    """Spec 6.1: absence of data is not evidence of illness — a
    coherence that is still None after the auto-reflect never enters
    the reasons."""
    engine.last_coherence = None
    assert gates.engine_readiness(engine) == []


# ── C6 down: the gate lowers proceed to hold, both modes ─────────────


@pytest.mark.parametrize("reason, setup", [
    ("action_lockdown", lambda e: setattr(e._state, "action_lockdown", True)),
    ("metabolic_critical", lambda e: setattr(e._state, "metabolic", "CRITICAL")),
    ("low_coherence", lambda e: setattr(e, "last_coherence", SimpleNamespace(score=0.2))),
], ids=["lockdown", "metabolic-critical", "low-coherence"])
def test_c6_down_judged_mode_gate_lowers_proceed(engine, reason, setup, monkeypatch):
    """Not-ready engine + judge proceeding -> hold, with the reasons
    in gate_reason (spec section 8, C6 down)."""
    setup(engine)
    monkeypatch.setattr(judge_mod, "load", lambda cfg=None: _adapter_returning("proceed"))
    result = gates.council(engine, question="rename the changelog entry", context="docs only")
    assert result["mode"] == "judged"
    assert result["recommendation"] == "hold"
    assert result["gate_reason"] == [reason]
    assert result["judge"]["verdict"] == "proceed"


@pytest.mark.parametrize("reason, setup", [
    ("action_lockdown", lambda e: setattr(e._state, "action_lockdown", True)),
    ("metabolic_critical", lambda e: setattr(e._state, "metabolic", "CRITICAL")),
    ("low_coherence", lambda e: setattr(e, "last_coherence", SimpleNamespace(score=0.2))),
], ids=["lockdown", "metabolic-critical", "low-coherence"])
def test_c6_down_deterministic_mode_gate_lowers_proceed(engine, reason, setup, monkeypatch):
    """The gate applies identically without a judge (spec section 8:
    'idem no modo determinístico')."""
    setup(engine)
    _judge_off(monkeypatch)
    result = gates.council(engine, question="rename the changelog entry", context="docs only")
    assert result["mode"] == "deterministic"
    assert result["recommendation"] == "hold"
    assert result["gate_reason"] == [reason]


# ── C6 up: non-interference (spec section 8, the two required mutations)


def test_c6_up_ready_engine_hold_stays_hold(engine, monkeypatch):
    """A ready engine + judge hold stays hold — a gate that PROMOTED
    hold to proceed on a ready engine is killed by this test (the
    first section-8 mutation)."""
    _judge_off(monkeypatch)
    monkeypatch.setattr(
        judge_mod, "load", lambda cfg=None: _adapter_returning("hold", confidence=0.6))
    result = gates.council(engine, question="push the branch", context="not sure it works")
    assert result["mode"] == "judged"
    assert result["recommendation"] == "hold"
    assert result["gate_reason"] is None


def test_c6_up_not_ready_veto_passes_intact(engine, monkeypatch):
    """A not-ready engine + judge VETO stays veto — a gate that
    lowered to hold whenever any reason exists is killed by this test
    (the second section-8 mutation)."""
    engine._state.action_lockdown = True
    monkeypatch.setattr(
        judge_mod, "load", lambda cfg=None: _adapter_returning("veto"))
    result = gates.council(engine, question="drop the orders table", context="no backup")
    assert result["mode"] == "judged"
    assert result["recommendation"] == "veto"
    assert result["gate_reason"] is None  # the gate never demotes a veto


# ── C7: the judge cannot be enabled by the key's presence alone ─────


def test_c7_off_when_no_judge_block_even_with_key_in_env(engine, monkeypatch):
    """No judge block in the config + the key env var present -> the
    council answers in deterministic mode with judge_status 'off',
    and sockets explode on any connection attempt (zero network)."""
    monkeypatch.setenv("T5_TEST_KEY", "env-key-present")

    def _explode(*args, **kwargs):
        pytest.fail("a connection was attempted with the judge off")

    monkeypatch.setattr(socket, "socket", _explode)
    monkeypatch.setattr(socket, "create_connection", _explode)
    _judge_off(monkeypatch)
    result = gates.council(engine, question="rename the changelog entry", context="docs only")
    assert result["mode"] == "deterministic"
    assert result["judge_status"] == "off"
    assert "judge" not in result


# ── C10: the additive shape and the gate invariant ──────────────────


def test_c10_shape_deterministic_mode(engine, monkeypatch):
    _judge_off(monkeypatch)
    result = gates.council(engine, question="q", context="c")
    for field in ("mode", "judge_status", "gate_reason",
                  "recommendation", "recommendation_category", "voices",
                  "votes_summary", "dissenting_voices", "agreement"):
        assert field in result, field
    assert result["mode"] == "deterministic"
    assert result["judge_status"] == "off"
    assert result["gate_reason"] is None
    assert "judge" not in result  # judged-mode field, absent otherwise
    assert result["recommendation_category"] == "asserted"


def test_c10_shape_judged_mode(engine, monkeypatch):
    monkeypatch.setattr(
        judge_mod, "load", lambda cfg=None: _adapter_returning("proceed"))
    result = gates.council(engine, question="q", context="c")
    assert result["mode"] == "judged"
    assert result["judge_status"] == "ok"
    assert result["gate_reason"] is None
    assert set(result["judge"]) == {"verdict", "probabilities", "confidence", "model"}
    assert result["recommendation_category"] == "asserted"


def test_c10_gate_invariant_across_scenarios(engine, monkeypatch):
    """judge['verdict'] != recommendation if and only if gate_reason
    is not None — checked across every (verdict, readiness) corner."""
    scenarios = [
        ("proceed", False, "no reasons, no gate"),
        ("proceed", True, "reasons: the gate lowers proceed"),
        ("hold", False, "hold passes even without reasons"),
        ("hold", True, "reasons exist but hold is not touched"),
        ("veto", False, "veto passes"),
        ("veto", True, "reasons exist but veto is not touched"),
    ]
    for choice, not_ready, why in scenarios:
        eng = engine
        if not_ready:
            eng._state.action_lockdown = True
        else:
            eng._state.action_lockdown = False
        monkeypatch.setattr(
            judge_mod, "load", lambda cfg=None, c=choice: _adapter_returning(c))
        result = gates.council(eng, question="q", context="c")
        assert (result["judge"]["verdict"] != result["recommendation"]) == \
            (result["gate_reason"] is not None), why


# ── section 6.4: consumers read result["mode"] ──────────────────────


@pytest.mark.parametrize("complexity", ["minimal", "compact", "full"])
def test_format_council_reads_mode_field_in_all_levels(tmp_path, complexity):
    """format_council returns result['mode'] in every level — even
    when a voice analysis contains the string 'LLM' (the legacy
    detection is gone)."""
    from pathlib import Path
    ctrl = Path(tmp_path) / "daemon_control.json"
    ctrl.write_text(json.dumps({"prompt_complexity": complexity}))
    router = ModeRouter(Path(tmp_path))
    result = {
        "question": "q",
        "mode": "judged",
        "recommendation": "proceed",
        "votes_summary": {"proceed": 3, "hold": 1, "veto": 0},
        "voices": [
            {"role": "critic",
             "analysis": "LLM analysis: risk of regression high",
             "concerns": [], "vote": "proceed"},
        ],
    }
    assert router.format_council(result)["mode"] == "judged", complexity


def test_format_council_legacy_result_without_mode_defaults(tmp_path):
    """A result predating T5 (no 'mode' key) formats as
    deterministic — the legacy LLM-string detection no longer
    resurrects mode 'llm'."""
    from pathlib import Path
    ctrl = Path(tmp_path) / "daemon_control.json"
    ctrl.write_text(json.dumps({"prompt_complexity": "compact"}))
    router = ModeRouter(Path(tmp_path))
    result = {
        "question": "q",
        "recommendation": "hold",
        "votes_summary": {"proceed": 1, "hold": 2, "veto": 1},
        "voices": [{"role": "critic", "analysis": "LLM analysis", "concerns": [],
                    "vote": "hold"}],
    }
    assert router.format_council(result)["mode"] == "deterministic"


def test_outcome_snapshot_carries_mode(tmp_path, monkeypatch):
    """capture_council_outcome's snapshot gains the mode (spec
    section 6.4, divergence 3)."""
    monkeypatch.setattr(judge_mod, "load", lambda cfg=None: None)
    engine = ConsciousnessEngine(model_name="test", storage_path=str(tmp_path))
    try:
        engine.last_coherence = SimpleNamespace(score=0.8)
        result = gates.council(engine, question="rename the changelog entry",
                               context="docs only")
    finally:
        engine.close()
    store = OutcomeStore(tmp_path / "outcomes.db")
    eid = capture_council_outcome(store, result)
    assert eid is not None
    with sqlite3.connect(tmp_path / "outcomes.db") as db:
        row = db.execute(
            "SELECT snapshot FROM decision_outcomes WHERE event_id = ?",
            (eid,)).fetchone()
    snapshot = json.loads(row[0])
    assert snapshot["mode"] == "deterministic"
    assert snapshot["recommendation"] == result["recommendation"]
