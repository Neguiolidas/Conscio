# tests/test_daemon_initiator.py
"""v2.10.0 "Initiative" — daemon initiator hook (cadence + runtime-awake) + arm
gate, and the responder runtime-awake consistency fix."""
from conscio.daemon import Daemon, _initiator_armed
from conscio.engine import ConsciousnessEngine
from conscio.perception import MockSensor, PerceptionFrame


def _engine(tmp_path):
    return ConsciousnessEngine("glm-5.1", storage_path=tmp_path / "s")


def _frame():
    return PerceptionFrame(source="mock", observations=["o"], signals={"x": 1.0})


def test_initiator_called_when_awake_and_cadence_elapsed(tmp_path):
    eng = _engine(tmp_path)
    eng.wake()
    calls = {"n": 0}
    d = Daemon(eng, sensors=[MockSensor([_frame()])],
               initiator=lambda: calls.__setitem__("n", calls["n"] + 1),
               initiate_interval=0.0)
    try:
        d.cycle()
        assert calls["n"] == 1
    finally:
        eng.close()


def test_initiator_not_called_when_asleep(tmp_path):
    eng = _engine(tmp_path)                              # asleep by default
    calls = {"n": 0}
    d = Daemon(eng, sensors=[MockSensor([_frame()])],
               initiator=lambda: calls.__setitem__("n", calls["n"] + 1),
               initiate_interval=0.0)
    try:
        d.cycle()
        assert calls["n"] == 0                           # B2 runtime awake gate
    finally:
        eng.close()


def test_initiator_cadence_blocks_second_cycle(tmp_path):
    eng = _engine(tmp_path)
    eng.wake()
    calls = {"n": 0}
    d = Daemon(eng, sensors=[MockSensor([_frame(), _frame()])],
               initiator=lambda: calls.__setitem__("n", calls["n"] + 1),
               initiate_interval=1000.0)
    try:
        d.cycle()
        d.cycle()
        assert calls["n"] == 1                           # gate 3: cadence
    finally:
        eng.close()


def test_initiator_raise_survives_cycle(tmp_path):
    eng = _engine(tmp_path)
    eng.wake()

    def boom():
        raise RuntimeError("initiator down")

    d = Daemon(eng, sensors=[MockSensor([_frame()])], initiator=boom,
               initiate_interval=0.0)
    try:
        d.cycle()                                        # must not raise
    finally:
        eng.close()


def test_initiator_none_is_noop(tmp_path):
    eng = _engine(tmp_path)
    eng.wake()
    d = Daemon(eng, sensors=[MockSensor([_frame()])])    # no initiator
    try:
        d.cycle()
    finally:
        eng.close()


def test_initiator_arm_gate_predicate():
    base = dict(initiate=True, relay_peer=["p"], has_adapter=True,
                awake=True, sensors_spec="host,relay")
    assert _initiator_armed(**base) is True
    assert _initiator_armed(**{**base, "initiate": False}) is False
    assert _initiator_armed(**{**base, "relay_peer": []}) is False
    assert _initiator_armed(**{**base, "has_adapter": False}) is False
    assert _initiator_armed(**{**base, "awake": False}) is False
    assert _initiator_armed(**{**base, "sensors_spec": "host"}) is False
