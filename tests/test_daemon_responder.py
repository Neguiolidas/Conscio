# tests/test_daemon_responder.py
"""v2.7.0 Phase 2 — daemon responder hook + arm gate."""
from conscio.daemon import Daemon, _responder_armed
from conscio.engine import ConsciousnessEngine
from conscio.perception import MockSensor, PerceptionFrame


def _engine(tmp_path):
    return ConsciousnessEngine("glm-5.1", storage_path=tmp_path / "s")


def _frame():
    return PerceptionFrame(source="mock", observations=["o"], signals={"x": 1.0})


def test_responder_called_after_run(tmp_path):
    eng = _engine(tmp_path)
    eng.wake()                                       # v2.10.0: responder is now
    calls = {"n": 0}                                 # runtime-awake-gated
    d = Daemon(eng, sensors=[MockSensor([_frame()])],
               responder=lambda: calls.__setitem__("n", calls["n"] + 1))
    try:
        d.cycle()
        assert calls["n"] == 1
    finally:
        eng.close()


def test_responder_raise_survives_cycle(tmp_path):
    eng = _engine(tmp_path)
    eng.wake()                                       # v2.10.0: needs awake to fire

    def boom():
        raise RuntimeError("responder down")

    d = Daemon(eng, sensors=[MockSensor([_frame()])], responder=boom)
    try:
        d.cycle()                                    # must not raise
    finally:
        eng.close()


def test_responder_not_called_when_asleep(tmp_path):
    eng = _engine(tmp_path)                          # asleep by default
    calls = {"n": 0}
    d = Daemon(eng, sensors=[MockSensor([_frame()])],
               responder=lambda: calls.__setitem__("n", calls["n"] + 1))
    try:
        d.cycle()
        assert calls["n"] == 0                       # runtime awake gate (fix)
    finally:
        eng.close()


def test_responder_none_is_noop(tmp_path):
    eng = _engine(tmp_path)
    d = Daemon(eng, sensors=[MockSensor([_frame()])])   # no responder
    try:
        d.cycle()                                    # no raise
    finally:
        eng.close()


def test_arm_gate_predicate():
    base = dict(auto_respond=True, relay_peer=["p"], has_adapter=True,
                awake=True, sensors_spec="host,relay")
    assert _responder_armed(**base) is True
    assert _responder_armed(**{**base, "auto_respond": False}) is False
    assert _responder_armed(**{**base, "relay_peer": []}) is False
    assert _responder_armed(**{**base, "has_adapter": False}) is False
    assert _responder_armed(**{**base, "awake": False}) is False
    assert _responder_armed(**{**base, "sensors_spec": "host"}) is False
