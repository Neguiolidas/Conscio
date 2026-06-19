# tests/test_engine_propose.py
import json

from conscio.agency import MockAdapter
from conscio.engine import ConsciousnessEngine


def _engine(tmp_path, script):
    eng = ConsciousnessEngine("glm-5.1", storage_path=tmp_path)
    eng.attach_adapter(MockAdapter(script=script))
    return eng


def test_event_bus_accepts_new_types(tmp_path):
    eng = ConsciousnessEngine("glm-5.1", storage_path=tmp_path)
    try:
        eng.event_bus.emit(type="proposal:audited", category="consciousness",
                           data={})
        eng.event_bus.emit(type="host:event", category="external", data={})
    finally:
        eng.close()


def test_propose_action_audits_intent_pass(tmp_path):
    eng = _engine(tmp_path, script=["A1: NO\nA2: NO\nA3: YES"])
    try:
        out = eng.propose_action({"tool": "read_file", "args": {"path": "x"},
                                  "rationale": "inspect",
                                  "expected_outcome": "contents"})
        assert out["verdict"] == "PASS"
        assert out["proposal"]["tool"] == "read_file"
    finally:
        eng.close()


def test_propose_action_invalid_intent_fails_closed(tmp_path):
    eng = _engine(tmp_path, script=["A1: NO\nA2: NO\nA3: YES"])
    try:
        out = eng.propose_action({"tool": "x"})
        assert out["verdict"] == "FAIL"
        assert out["proposal"] is None
    finally:
        eng.close()


def test_propose_no_adapter_is_structured_fail(tmp_path):
    eng = ConsciousnessEngine("glm-5.1", storage_path=tmp_path)
    try:
        out = eng.propose_action({"tool": "t", "args": {}, "rationale": "r",
                                  "expected_outcome": "o"})
        assert out["verdict"] == "FAIL"
        assert "no adapter" in out["reasons"][0]
    finally:
        eng.close()


def test_propose_plan_requires_vocabulary(tmp_path):
    eng = _engine(tmp_path, script=["A1: NO\nA2: NO\nA3: YES"])
    try:
        out = eng.propose_plan("inspect config", tools=None)
        assert out["verdict"] == "FAIL"
        assert "vocabulary" in out["reasons"][0]
    finally:
        eng.close()


def test_propose_plan_generates_then_audits(tmp_path):
    proposal_json = json.dumps({"tool": "read_file", "args": {"path": "x"},
                                "rationale": "inspect",
                                "expected_outcome": "contents"})
    eng = _engine(tmp_path, script=[proposal_json, "A1: NO\nA2: NO\nA3: YES"])
    try:
        out = eng.propose_plan("inspect config",
                               tools=[{"name": "read_file",
                                       "description": "read a file"}])
        assert out["proposal"]["tool"] == "read_file"
        assert out["verdict"] in ("PASS", "FAIL")
    finally:
        eng.close()


def test_propose_emits_event(tmp_path):
    eng = _engine(tmp_path, script=["A1: NO\nA2: NO\nA3: YES"])
    try:
        eng.propose_action({"tool": "read_file", "args": {}, "rationale": "r",
                            "expected_outcome": "o"})
        assert eng.event_bus.query(type="proposal:audited", limit=5)
    finally:
        eng.close()
