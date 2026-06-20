# tests/test_engine_host_act.py
from conscio.engine import ConsciousnessEngine
from conscio.agency import MockAdapter


def _eng(tmp_path):
    e = ConsciousnessEngine("mock-model", storage_path=str(tmp_path))
    e.attach_adapter(MockAdapter(script=[]))
    return e


_M = [{"name": "deploy", "params": {"env": {"type": "str", "required": True}},
       "risk": "low", "approval_policy": "auto"}]
_M2 = [{"name": "rollback", "params": {}, "risk": "high"}]


def test_enable_requires_adapter(tmp_path):
    e = ConsciousnessEngine("mock-model", storage_path=str(tmp_path))
    assert e.enable_host_act(_M) is False
    assert "adapter" in e.host_act_error.lower()
    assert e.host_act is None
    e.close()


def test_enable_builds_channel(tmp_path):
    e = _eng(tmp_path)
    assert e.enable_host_act(_M) is True
    assert e.host_act is not None
    e.close()


def test_same_manifest_is_idempotent(tmp_path):
    e = _eng(tmp_path)
    e.enable_host_act(_M)
    first = e.host_act
    assert e.enable_host_act(list(_M)) is True
    assert e.host_act is first                       # not rebuilt
    e.close()


def test_invalid_manifest_does_not_enable(tmp_path):
    e = _eng(tmp_path)
    assert e.enable_host_act([{"risk": "boom"}]) is False
    assert e.host_act is None
    assert "invalid" in e.host_act_error.lower()
    e.close()


def test_different_manifest_with_inflight_rejected(tmp_path):
    e = _eng(tmp_path)
    e.enable_host_act(_M)
    # Seed an in-flight row directly: propose's verdict depends on the live
    # skeptic (mock); the re-declaration guard is what this test exercises.
    e.host_act.ledger.record(goal_fp="g", tool="deploy", args_json="{}",
                             rationale="r", tier="host", status="proposed")
    assert e.enable_host_act(_M2) is False
    assert "in-flight" in e.host_act_error.lower()
    e.close()


def test_different_manifest_no_inflight_replaces(tmp_path):
    e = _eng(tmp_path)
    e.enable_host_act(_M)
    assert e.enable_host_act(_M2) is True
    assert e.host_act.registry.get("rollback") is not None
    assert e.host_act.registry.get("deploy") is None
    e.close()
