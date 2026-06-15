"""v1.5 'Live' — Awake Mode (R9): the master gate for autonomous operation.

A1 (this file's first block): `awake` is a persisted boolean on
ConsciousnessState, default OFF, surfaced as engine.awake / engine.wake() /
engine.sleep(). It must (1) default asleep, (2) persist across reopen via the
EXISTING state store (no new file), (3) default asleep when an old on-disk state
predates the field, (4) survive a reflect() cycle (reflect rebuilds the state
object — the action_lockdown carry-forward pattern), and (5) emit an auditable
awake:changed event.

A2 (second block): the run() heartbeat is gated by awake — asleep yields a
reflection but ZERO autonomous act/dream, awake yields the full loop; a direct
human act() call is NOT gated (R9 governs self-initiated autonomy only).
"""
import json

from conscio.engine import ConsciousnessEngine


def _engine(tmp_path, name="s"):
    return ConsciousnessEngine(model_name="glm-5.1", storage_path=tmp_path / name)


# ── A1: persisted awake state + engine API ──────────────────────────────────

def test_default_is_asleep(tmp_path):
    eng = _engine(tmp_path)
    try:
        assert eng.awake is False
    finally:
        eng.close()


def test_wake_then_sleep_toggles(tmp_path):
    eng = _engine(tmp_path)
    try:
        eng.wake()
        assert eng.awake is True
        eng.sleep()
        assert eng.awake is False
    finally:
        eng.close()


def test_wake_persists_across_reopen(tmp_path):
    p = tmp_path / "s"
    e1 = ConsciousnessEngine(model_name="glm-5.1", storage_path=p)
    e1.wake()
    e1.close()
    e2 = ConsciousnessEngine(model_name="glm-5.1", storage_path=p)
    try:
        assert e2.awake is True
    finally:
        e2.close()


def test_old_state_without_awake_field_loads_asleep(tmp_path):
    # A pre-v1.5 on-disk state has no 'awake' key -> must default to False.
    p = tmp_path / "s"
    e1 = ConsciousnessEngine(model_name="glm-5.1", storage_path=p)
    e1.wake()                                   # persist a state file
    e1.close()
    state_file = p / "state_summary.json"
    data = json.loads(state_file.read_text())
    assert "awake" in data                      # v1.5 writes the field
    data.pop("awake")                           # simulate a pre-v1.5 file
    state_file.write_text(json.dumps(data))
    e2 = ConsciousnessEngine(model_name="glm-5.1", storage_path=p)
    try:
        assert e2.awake is False
    finally:
        e2.close()


def test_awake_survives_reflect_cycle(tmp_path):
    # reflect() rebuilds ConsciousnessState; the awake flag must not reset.
    eng = _engine(tmp_path)
    try:
        eng.wake()
        eng.reflect(world_state="something happened")
        assert eng.awake is True
        assert eng.state.awake is True
    finally:
        eng.close()


def test_wake_emits_awake_changed_event(tmp_path):
    eng = _engine(tmp_path)
    try:
        eng.wake()
        events = eng.event_bus.query(type="awake:changed", limit=10)
        assert any(e.to_dict()["data"].get("awake") is True for e in events)
    finally:
        eng.close()


def test_wake_is_idempotent(tmp_path):
    eng = _engine(tmp_path)
    try:
        eng.wake()
        eng.wake()
        assert eng.awake is True
    finally:
        eng.close()
