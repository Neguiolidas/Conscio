"""Engine wiring for the F2 immunity layer (MockAdapter only)."""
import json

from conscio.agency.act import goal_fingerprint
from conscio.agency.adapter import MockAdapter
from conscio.engine import ConsciousnessEngine

PROPOSAL = json.dumps({"tool": "fs_read", "args": {"path": "a.txt"},
                       "rationale": "look first",
                       "expected_outcome": "content returned"})
CHECK_PASS = "A1: NO\nA2: NO\nA3: YES"


def _engine(tmp_path):
    return ConsciousnessEngine(model_name="glm-5.1",
                               storage_path=tmp_path / "storage")


def test_attach_wires_skeptic_trust_and_quarantine(tmp_path):
    eng = _engine(tmp_path)
    try:
        skeptic_adapter = MockAdapter(script=[CHECK_PASS])
        pipe = eng.attach_adapter(
            MockAdapter(script=[PROPOSAL]),
            sandbox_root=tmp_path / "sb",
            skeptic_adapter=skeptic_adapter)
        assert pipe.skeptic is not None
        # mixed-cortex: the dedicated auditor sits inside the F3 meter wrap
        assert pipe.skeptic.adapter.inner is skeptic_adapter
        assert pipe.trust is not None
        assert pipe.breaker._conn is not None               # quarantine on
        (tmp_path / "sb").mkdir(exist_ok=True)
        (tmp_path / "sb" / "a.txt").write_text("hello")
        state = eng._state
        state.active_goals = ["inspect the sandbox"]
        report = eng.act(state)
        assert report.status.value == "proposed"
        assert len(skeptic_adapter.calls) == 1              # audit happened
        assert eng.pending()                                # R6 queue visible
    finally:
        eng.close()


def test_close_releases_trust_and_breaker_connections(tmp_path):
    eng = _engine(tmp_path)
    eng.attach_adapter(MockAdapter(script=[]), sandbox_root=tmp_path / "sb")
    eng.close()                                  # must not raise


def test_engine_breaker_lockdown_persists_across_restart(tmp_path):
    storage = tmp_path / "storage"
    eng = ConsciousnessEngine(model_name="glm-5.1", storage_path=storage)
    pipe = eng.attach_adapter(MockAdapter(script=[PROPOSAL]),
                              sandbox_root=tmp_path / "sb")
    try:
        # Reach the global-lockdown quorum (3 quarantined goals) through the
        # real breaker, each trip backed by real consecutive failures.
        for name in ("alpha", "beta", "gamma"):
            fp = goal_fingerprint(name)
            for _ in range(pipe.breaker.max_retries):
                pipe.ledger.record(goal_fp=fp, tool="fs_read", args_json="{}",
                                   rationale="r", tier="T2", status="failed",
                                   ok=False)
            pipe.breaker.trip(fp, goal_text=name)
        assert pipe.breaker.global_lockdown_due() is True
        # The pipeline latches lockdown on quorum; persist it and restart.
        eng._state.action_lockdown = True
        eng.ctx.save_state(eng._state)
    finally:
        eng.close()

    eng2 = ConsciousnessEngine(model_name="glm-5.1", storage_path=storage)
    try:
        eng2.attach_adapter(MockAdapter(script=[PROPOSAL]),
                            sandbox_root=tmp_path / "sb")
        eng2._state.active_goals = ["alpha"]
        report = eng2.act(eng2._state)
        assert report.status.value == "locked"   # latch survived restart
    finally:
        eng2.close()
