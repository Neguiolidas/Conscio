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

from conscio.agency.adapter import MockAdapter
from conscio.agency.loop import ActBudget
from conscio.context_manager import ConsciousnessState
from conscio.engine import ConsciousnessEngine
from conscio.content_layer import _RAG_DISABLED

_PROBES = [
    '{"status": "ok", "count": 3}',
    '{"plan": {"tool": "x", "steps": ["a"]}}',
    '{"color": "red"}',
    '{"name": "probe"}',
    "TOOL: fs_read\nWHY: probe",
]
_OPEN_PASS = '{"verdict": "PASS", "reasons": [], "risk_flags": []}'


def _proposal():
    return json.dumps({"tool": "memory_note", "args": {"text": "n"},
                       "rationale": "r", "expected_outcome": "e"})


def _engine(tmp_path, name="s"):
    return ConsciousnessEngine(model_name="glm-5.1", storage_path=tmp_path / name)


def _attach(eng, tmp_path, script):
    eng.content_layer._session_rag = _RAG_DISABLED
    return eng.attach_adapter(MockAdapter(script=script),
                              sandbox_root=tmp_path / "sb")


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


# ── A2: run() heartbeat gated by Awake Mode ─────────────────────────────────

def test_run_asleep_reflects_but_does_not_act(tmp_path):
    # The core R9 proof: an asleep heartbeat perceives + reflects, and performs
    # ZERO autonomous act/dream — no ledger entries, no act reports.
    eng = _engine(tmp_path)
    _attach(eng, tmp_path, list(_PROBES) + [_proposal(), _OPEN_PASS])
    try:
        eng.goals.add_user_goal("write a memory note")
        before = len(eng.event_bus.query(type="reflection", limit=100000))
        report = eng.run(ActBudget(max_cycles=2, max_wall_s=120.0))
        after = len(eng.event_bus.query(type="reflection", limit=100000))
        assert report.stopped == "asleep"
        assert report.cycles == 0
        assert report.reports == []
        assert after == before + 1          # exactly one reflection, no loop
        assert eng.pending() == []          # nothing reached the ledger
    finally:
        eng.close()


def test_run_awake_runs_the_full_loop(tmp_path):
    cycles = 2
    script = list(_PROBES)
    for _ in range(cycles):
        script += [_proposal(), _OPEN_PASS]
    eng = _engine(tmp_path)
    _attach(eng, tmp_path, script)
    try:
        eng.wake()
        eng.goals.add_user_goal("write a memory note")
        report = eng.run(ActBudget(max_cycles=cycles, max_wall_s=120.0))
        assert report.cycles == cycles
        assert report.stopped == "max_cycles"
    finally:
        eng.close()


def test_direct_act_works_while_asleep(tmp_path):
    # R9 governs self-initiated autonomy (the loop), NOT a human's act() call.
    eng = _engine(tmp_path)
    _attach(eng, tmp_path, [_proposal(), "A1: NO\nA2: NO\nA3: YES"])
    try:
        assert eng.awake is False
        report = eng.act(ConsciousnessState(active_goals=["note it"]))
        assert report.status.value in ("proposed", "executed")
    finally:
        eng.close()


def test_run_awake_without_adapter_fails_cleanly(tmp_path):
    eng = _engine(tmp_path)
    try:
        eng.wake()
        report = eng.run()
        assert report.stopped == "no adapter attached"
        assert report.cycles == 0
    finally:
        eng.close()


def test_run_awake_without_adapter_still_reflects(tmp_path):
    # Observation is always-on: an awake daemon with no inference backend must
    # still perceive+reflect every cycle (only autonomy is gated), not idle.
    eng = _engine(tmp_path)
    try:
        eng.wake()
        before = len(eng.event_bus.query(type="reflection", limit=100000))
        eng.run(world_state="host is hot")
        after = len(eng.event_bus.query(type="reflection", limit=100000))
        assert after == before + 1
    finally:
        eng.close()


def test_act_lockdown_does_not_clobber_persisted_awake(tmp_path):
    # act() may persist a transient external state on lockdown; awake is
    # engine-scoped and must NOT be downgraded by that transient state's default.
    from conscio.agency.act import ActReport, ActStatus

    eng = _engine(tmp_path)
    _attach(eng, tmp_path, [])
    eng._skills = None                       # isolate from skill settle
    try:
        eng.wake()
        eng._act_pipeline.act = lambda state: ActReport(
            status=ActStatus.FAILED, lockdown=True)
        eng.act(ConsciousnessState(active_goals=["x"]))   # external asleep state
        assert eng.awake is True
    finally:
        eng.close()
    reopened = ConsciousnessEngine(model_name="glm-5.1", storage_path=tmp_path / "s")
    try:
        assert reopened.awake is True        # persisted awake survived lockdown
    finally:
        reopened.close()
