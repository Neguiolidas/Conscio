# tests/test_perception.py
"""SensorAdapter — the perception extension point (mirror of InferenceAdapter).

Frozen in v1.3 so F5's daemon consumes a stable interface. The interface is
exercised here and by the examples; the core does not yet *call* a sensor.
"""
import pytest


def test_perception_frame_to_world_state_is_deterministic():
    from conscio.perception import PerceptionFrame
    f = PerceptionFrame(source="host", observations=["cpu 12%", "disk 40%"],
                        signals={"load": 0.12})
    s = f.to_world_state()
    assert "host" in s and "cpu 12%" in s and "disk 40%" in s
    assert "load" in s and "0.12" in s
    assert f.to_world_state() == s            # no clock/rng — pure


def test_to_world_state_sorts_signals_for_stability():
    from conscio.perception import PerceptionFrame
    f = PerceptionFrame(source="s", observations=[],
                        signals={"b": 2.0, "a": 1.0})
    s = f.to_world_state()
    assert s.index("a") < s.index("b")        # deterministic key order


def test_mock_sensor_pops_frames_in_order_then_raises():
    from conscio.perception import MockSensor, PerceptionFrame
    a = PerceptionFrame(source="s", observations=["1"])
    b = PerceptionFrame(source="s", observations=["2"])
    sensor = MockSensor(frames=[a, b])
    assert sensor.perceive() is a and sensor.perceive() is b
    with pytest.raises(StopIteration):
        sensor.perceive()


def test_sensor_risk_defaults_low():
    from conscio.perception import MockSensor
    from conscio.risk import Risk
    assert MockSensor(frames=[]).risk is Risk.LOW


def test_sensor_adapter_is_abstract():
    from conscio.perception import SensorAdapter
    with pytest.raises(TypeError):
        SensorAdapter()                       # cannot instantiate the ABC


def test_frame_roundtrips_into_reflect(tmp_path):
    from conscio.engine import ConsciousnessEngine
    from conscio.perception import PerceptionFrame
    eng = ConsciousnessEngine(model_name="glm-5.1",
                              storage_path=tmp_path / "s")
    try:
        f = PerceptionFrame(source="host", observations=["all nominal"])
        r = eng.reflect(world_state=f.to_world_state(), confidence=0.8)
        assert "summary" in r                 # reflect() contract untouched
    finally:
        eng.close()
