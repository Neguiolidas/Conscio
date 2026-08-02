"""BUG-37: the global lockdown latch must not outlive the condition that set it.

The latch is persisted so a lockdown survives a restart (safety rule 5), but the
breaker that owns the condition is deliberately self-healing: quarantined_count()
ignores rows whose cooldown has lapsed, "whether or not anything sweeps the
table". Nothing ever reconciled the two, and ActPipeline.act() short-circuits on
the persisted flag *before* consulting the breaker — so a daemon that once hit
quorum stayed paralysed forever, long after every cooldown expired.

These tests pin both directions: the latch holds while the quorum holds, and
releases once it does not.
"""
import time

from conscio.agency.adapter import MockAdapter
from conscio.agency.breaker import GLOBAL_LOCKDOWN_QUORUM
from conscio.engine import ConsciousnessEngine


def _engine(storage):
    return ConsciousnessEngine(model_name="glm-5.1", storage_path=storage)


def _attach(eng, tmp_path):
    return eng.attach_adapter(MockAdapter(script=[]),
                              sandbox_root=tmp_path / "sb")


def _expire_quarantine(pipe):
    """Rewind every cooldown into the past — the operator's reported state."""
    conn = pipe.breaker._conn
    assert conn is not None, "quarantine table required for this test"
    conn.execute("UPDATE goal_quarantine SET cooldown_until = ?",
                 (time.time() - 1,))
    conn.commit()


def _quarantine_quorum(pipe, *, expired=False):
    """Put GLOBAL_LOCKDOWN_QUORUM goals in quarantine through the real breaker."""
    for i in range(GLOBAL_LOCKDOWN_QUORUM):
        pipe.breaker.trip(f"goal-{i}", goal_text=f"goal {i}")
    if expired:
        _expire_quarantine(pipe)


def _latch(eng):
    return eng.ctx.load_state().action_lockdown


class TestReconcileOnAttach:
    def test_expired_quorum_releases_the_latch(self, tmp_path):
        """The reported failure: cooldowns lapsed, rows still there, daemon stuck."""
        storage = tmp_path / "storage"
        eng = _engine(storage)
        try:
            pipe = _attach(eng, tmp_path)
            _quarantine_quorum(pipe, expired=True)
            eng._state.action_lockdown = True
            eng.ctx.save_state(eng._state)
        finally:
            eng.close()

        eng2 = _engine(storage)
        try:
            assert eng2._state.action_lockdown is True   # loaded from disk
            _attach(eng2, tmp_path)
            assert eng2._state.action_lockdown is False  # breaker had the truth
            assert _latch(eng2) is False                 # and it was persisted
        finally:
            eng2.close()

    def test_live_quorum_keeps_the_latch(self, tmp_path):
        """Safety rule 5 still holds: a standing quorum survives the restart."""
        storage = tmp_path / "storage"
        eng = _engine(storage)
        try:
            pipe = _attach(eng, tmp_path)
            _quarantine_quorum(pipe)                     # cooldowns still running
            eng._state.action_lockdown = True
            eng.ctx.save_state(eng._state)
        finally:
            eng.close()

        eng2 = _engine(storage)
        try:
            _attach(eng2, tmp_path)
            assert eng2._state.action_lockdown is True
            assert _latch(eng2) is True
        finally:
            eng2.close()

    def test_a_fresh_quorum_latches_again_after_a_release(self, tmp_path):
        """Release is not a one-way door — paralysis with recovery, not death."""
        eng = _engine(tmp_path / "storage")
        try:
            pipe = _attach(eng, tmp_path)
            _quarantine_quorum(pipe, expired=True)
            eng._state.action_lockdown = True
            assert eng._reconcile_lockdown() is False

            _quarantine_quorum(pipe)                    # the goals fail again
            eng._state.action_lockdown = True           # as ActPipeline re-latches
            assert eng._reconcile_lockdown() is True
            assert eng._state.action_lockdown is True
        finally:
            eng.close()

    def test_sub_quorum_quarantine_releases_the_latch(self, tmp_path):
        """Below quorum the breaker says no lockdown is due, even unexpired."""
        storage = tmp_path / "storage"
        eng = _engine(storage)
        try:
            pipe = _attach(eng, tmp_path)
            pipe.breaker.trip("lonely", goal_text="lonely")
            eng._state.action_lockdown = True
            eng.ctx.save_state(eng._state)
            assert eng._reconcile_lockdown() is False
            assert _latch(eng) is False
        finally:
            eng.close()

    def test_release_is_audited(self, tmp_path):
        storage = tmp_path / "storage"
        eng = _engine(storage)
        try:
            pipe = _attach(eng, tmp_path)
            _quarantine_quorum(pipe, expired=True)
            eng._state.action_lockdown = True
            eng._reconcile_lockdown()
            events = eng.event_bus.query(type="system", category="system",
                                         limit=20)
            messages = [(e.data or {}).get("message", "") for e in events]
            assert any("lockdown released" in m for m in messages), messages
        finally:
            eng.close()


class TestReconcileFailsClosed:
    def test_no_adapter_leaves_the_latch_alone(self, tmp_path):
        """No breaker means no authority to release — the latch stands."""
        eng = _engine(tmp_path / "storage")
        try:
            eng._state.action_lockdown = True
            assert eng._reconcile_lockdown() is True
            assert eng._state.action_lockdown is True
        finally:
            eng.close()

    def test_breaker_without_quarantine_db_leaves_the_latch_alone(self, tmp_path):
        """F1 degradation: no quarantine table, so any trip is global. Stay locked."""
        from conscio.agency.breaker import CircuitBreaker

        eng = _engine(tmp_path / "storage")
        try:
            pipe = _attach(eng, tmp_path)
            pipe.breaker = CircuitBreaker(pipe.ledger, eng.event_bus)  # no db_path
            assert pipe.breaker._conn is None
            eng._state.action_lockdown = True
            assert eng._reconcile_lockdown() is True
        finally:
            eng.close()

    def test_foreign_state_is_corrected_without_rewriting_the_engine_state(
            self, tmp_path):
        """A caller-supplied state may be older than disk; clearing one flag
        must not write the rest of it back over the current summary."""
        eng = _engine(tmp_path / "storage")
        try:
            pipe = _attach(eng, tmp_path)
            _quarantine_quorum(pipe, expired=True)
            eng._state.state_summary = "current"
            eng.ctx.save_state(eng._state)

            foreign = eng.ctx.load_state()
            foreign.action_lockdown = True
            foreign.state_summary = "stale"

            assert eng._reconcile_lockdown(foreign) is False
            assert foreign.action_lockdown is False
            assert eng.ctx.load_state().state_summary == "current"
        finally:
            eng.close()

    def test_unlocked_state_is_never_touched(self, tmp_path):
        eng = _engine(tmp_path / "storage")
        try:
            _attach(eng, tmp_path)
            assert eng._state.action_lockdown is False
            assert eng._reconcile_lockdown() is False
        finally:
            eng.close()


class TestReconcileOnAct:
    def test_cooldown_lapsing_mid_process_unblocks_act(self, tmp_path):
        """A long-lived daemon never re-attaches; act() has to reconcile too."""
        eng = _engine(tmp_path / "storage")
        try:
            pipe = _attach(eng, tmp_path)
            _quarantine_quorum(pipe)
            eng._state.action_lockdown = True
            eng._state.active_goals = ["goal 0"]
            assert eng.act(eng._state).status.value == "locked"

            _expire_quarantine(pipe)

            assert eng.act(eng._state).status.value != "locked"
            assert _latch(eng) is False
        finally:
            eng.close()


class TestBrakeRecency:
    """BUG-37b: a per-run brake was reported as permanent status."""

    def _emit_brake(self, eng):
        eng.event_bus.emit(
            type="system", category="system",
            data={"message": "failure-rate brake: autonomous loop stopped",
                  "failures": 3, "cycles": 3, "failure_rate": 1.0},
            priority=8)

    def test_brake_from_this_process_is_reported(self, tmp_path):
        eng = _engine(tmp_path / "storage")
        try:
            self._emit_brake(eng)
            assert eng.advisory()["status"]["brake"] is not None
        finally:
            eng.close()

    def test_brake_from_a_previous_process_is_not_reported(self, tmp_path):
        storage = tmp_path / "storage"
        eng = _engine(storage)
        try:
            self._emit_brake(eng)
            assert eng.advisory()["status"]["brake"] is not None
        finally:
            eng.close()

        eng2 = _engine(storage)
        try:
            assert eng2.advisory()["status"]["brake"] is None
        finally:
            eng2.close()

    def test_a_brake_raised_during_a_heartbeat_is_reported(self, tmp_path):
        """The window run() opens must still admit that run's own brake.

        An over-restrictive `since` filter is indistinguishable from "no brake
        tripped", which is the whole signal — so assert both sides of it."""
        eng = _engine(tmp_path / "storage")
        try:
            eng.run()                                   # opens the window
            self._emit_brake(eng)                       # as the loop would
            assert eng.advisory()["status"]["brake"] is not None
        finally:
            eng.close()

    def test_a_later_heartbeat_clears_a_stale_brake(self, tmp_path):
        eng = _engine(tmp_path / "storage")
        try:
            self._emit_brake(eng)
            eng.run()                                   # asleep: reflect only
            assert eng.advisory()["status"]["brake"] is None
        finally:
            eng.close()
