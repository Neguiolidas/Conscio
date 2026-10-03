"""Lote H (v4.8.1) — calibration of awake-mode LLM volume (hostile rework).

Reworked after the hostile review: every test now exercises the REAL
construction path — goals come from GoalGenerator.generate_from_maintenance,
ledger rows from goal_fingerprint(goal.description) exactly like act.py,
the loop from AutonomyLoop with a GoalGenerator-backed engine. No seeded
goal_fp literals, no synthetic (key, status) lists.

H2 levers: (1) maintenance cooldown on the last ATTEMPT (any status),
(2) at most one maintenance cycle per run counted from ledger ids,
(3) rolling 24h cost ceiling whose window MOVES on every check.
"""
from __future__ import annotations

import time
from pathlib import Path

from conscio.agency.act import ActReport, ActStatus
from conscio.agency.fingerprint import goal_fingerprint
from conscio.agency.ledger import ActionLedger
from conscio.agency.loop import ActBudget, AutonomyLoop
from conscio.awake.calibration import (
    DailyCostCeiling,
    MaintenanceCooldown,
    maintenance_goal_fingerprint,
)
from conscio.goal_generator import GoalGenerator


def _gen_with_maintenance_goal(tmp_path: Path):
    """Real GoalGenerator + the daemon_check goal, built by the same
    generate_from_maintenance the engine calls."""
    gg = GoalGenerator(tmp_path / "goals")
    goal = gg.generate_from_maintenance(
        "daemon_check",
        "host health check — run diagnostics and record state",
        source="daemon")
    assert goal is not None          # maintenance drive must be strong enough
    return gg, goal


# ── lever 1: cooldown on the real ledger key ────────────────────────────────

def test_fingerprint_matches_act_recording(tmp_path):
    """The calibration key IS what act.py records for this goal: derived
    from the same description the GoalGenerator built (no literals)."""
    _gg, goal = _gen_with_maintenance_goal(tmp_path)
    recorded_key = goal_fingerprint(goal.description)   # act.py:151 does this
    assert maintenance_goal_fingerprint() == recorded_key


def test_cooldown_engages_after_real_attempt(tmp_path):
    """Integration (hostile-review tooth a): record via the REAL path —
    goal_fingerprint of the generated goal — then the engine's lookup
    must find it (first cut read a literal key that never matched)."""
    _gg, goal = _gen_with_maintenance_goal(tmp_path)
    led = ActionLedger(tmp_path / "conscio.db")
    led.record(goal_fp=goal_fingerprint(goal.description),
               goal_text=goal.description, tool="host_health",
               args_json="{}", rationale="", tier="T2",
               status="executed", tokens_in=100, tokens_out=20)
    # the engine helper logic, exercised through the public ledger API
    ts = led.last_attempt_ts(maintenance_goal_fingerprint())
    assert ts is not None
    cd = MaintenanceCooldown(minutes=60)
    assert cd.should_generate(last_attempt_ts=ts) is False


def test_cooldown_counts_failed_attempt_not_just_executed(tmp_path):
    """A rate-limited (failed) attempt burned quota: it must engage the
    cooldown too — re-proposing into a 429 every 15 min was the waste."""
    _gg, goal = _gen_with_maintenance_goal(tmp_path)
    led = ActionLedger(tmp_path / "conscio.db")
    led.record(goal_fp=goal_fingerprint(goal.description),
               goal_text=goal.description, tool="host_health",
               args_json="{}", rationale="", tier="T2",
               status="failed", tokens_in=0, tokens_out=0)
    ts = led.last_attempt_ts(maintenance_goal_fingerprint())
    cd = MaintenanceCooldown(minutes=60)
    assert cd.should_generate(last_attempt_ts=ts) is False


def test_cooldown_allows_after_window(tmp_path):
    _gg, goal = _gen_with_maintenance_goal(tmp_path)
    led = ActionLedger(tmp_path / "conscio.db")
    led.record(goal_fp=goal_fingerprint(goal.description),
               goal_text=goal.description, tool="host_health",
               args_json="{}", rationale="", tier="T2",
               status="executed", tokens_in=100, tokens_out=20)
    row_id = led.count()
    led._conn.execute("UPDATE actions SET ts=? WHERE id=?",
                      (time.time() - 61 * 60, row_id))
    led._conn.commit()
    ts = led.last_attempt_ts(maintenance_goal_fingerprint())
    assert MaintenanceCooldown(minutes=60).should_generate(
        last_attempt_ts=ts) is True


def test_cooldown_first_ever_allows(tmp_path):
    led = ActionLedger(tmp_path / "conscio.db")
    assert led.last_attempt_ts(maintenance_goal_fingerprint()) is None
    assert MaintenanceCooldown(minutes=60).should_generate(
        last_attempt_ts=None) is True


# ── lever 2: one maintenance cycle per run, from the ledger ────────────────

class _LoopEngine:
    """Minimal engine double with a REAL GoalGenerator; act() executes
    like the real act — it does NOT change the goal's status (only
    complete_goal/expiry/cancel do), which is exactly what the hostile
    review proved with the first cut."""

    def __init__(self, gg):
        import types
        self.goals = gg
        self.act_calls = 0
        self.state = types.SimpleNamespace(
            action_lockdown=False, total_tokens_approx=lambda: 0)
        self.model_info = types.SimpleNamespace(context_window=131000)
        self.session_tokens_used = None
        self.dream_recommended = types.SimpleNamespace(recommended=False)
        self.event_bus = None

    def reflect(self, world_state=""):
        pass

    def act(self):
        self.act_calls += 1
        return ActReport(status=ActStatus.EXECUTED)


def test_one_maintenance_cycle_per_run(tmp_path):
    """Tooth b: real GoalGenerator, real AutonomyLoop, real ledger. The
    fake act executes but (like the real act) leaves the goal ACTIVE —
    the run must still stop after one cycle with
    'maintenance_cycle_cap'."""
    gg, goal = _gen_with_maintenance_goal(tmp_path)
    led = ActionLedger(tmp_path / "conscio.db")
    pipe_ledger = led
    from types import SimpleNamespace
    pipe = SimpleNamespace(autonomy_cap=3, breaker=None,
                            ledger=pipe_ledger)
    meter = SimpleNamespace(calls=0, tokens=0)
    eng = _LoopEngine(gg)
    # wire the real ledger into the fake engine so act() records there
    eng._act_ledger = led

    class _EngWithAct(_LoopEngine):
        def act(self):
            rep = super().act()
            # the real act records the attempt under the goal fingerprint
            led.record(goal_fp=goal_fingerprint(goal.description),
                       goal_text=goal.description, tool="host_health",
                       args_json="{}", rationale="", tier="T2",
                       status=str(rep.status).lower(), tokens_in=10,
                       tokens_out=2)
            return rep

    eng = _EngWithAct(gg)
    rep = AutonomyLoop(eng, pipe, meter).run(
        ActBudget(max_cycles=3, max_wall_s=60.0))
    assert eng.act_calls == 1, "maintenance-only run must stop at 1 cycle"
    assert rep.stopped == "maintenance_cycle_cap"
    # and the goal is still active — the real act leaves it that way
    assert goal.status == "active"


def test_failed_maintenance_cycle_still_caps(tmp_path):
    """Tooth b (failure case): a FAILED cycle burned the proposal — the
    cap must engage anyway, or a 429 storm retries endlessly per run."""
    gg, goal = _gen_with_maintenance_goal(tmp_path)
    led = ActionLedger(tmp_path / "conscio.db")
    from types import SimpleNamespace
    pipe = SimpleNamespace(autonomy_cap=3, breaker=None, ledger=led)
    meter = SimpleNamespace(calls=0, tokens=0)

    class _FailingEng(_LoopEngine):
        def act(self):
            super().act()
            led.record(goal_fp=goal_fingerprint(goal.description),
                       goal_text=goal.description, tool="host_health",
                       args_json="{}", rationale="", tier="T2",
                       status="failed", tokens_in=0, tokens_out=0)
            return ActReport(status=ActStatus.FAILED)

    eng = _FailingEng(gg)
    rep = AutonomyLoop(eng, pipe, meter).run(
        ActBudget(max_cycles=3, max_wall_s=60.0))
    assert eng.act_calls == 1
    assert rep.stopped == "maintenance_cycle_cap"


def test_non_maintenance_goal_never_capped(tmp_path):
    """A real (non-maintenance) goal is never starved by the cap."""
    gg, _goal = _gen_with_maintenance_goal(tmp_path)
    gg.generate_from_curiosity("a fascinating anomaly")   # non-maintenance
    led = ActionLedger(tmp_path / "conscio.db")
    from types import SimpleNamespace
    pipe = SimpleNamespace(autonomy_cap=3, breaker=None, ledger=led)
    meter = SimpleNamespace(calls=0, tokens=0)

    class _OtherGoalEng(_LoopEngine):
        def act(self):
            rep = super().act()
            led.record(goal_fp=goal_fingerprint("Investigate: something"),
                       goal_text="Investigate: something", tool="memory_note",
                       args_json="{}", rationale="", tier="T2",
                       status="executed", tokens_in=10, tokens_out=2)
            return rep

    eng = _OtherGoalEng(gg)
    rep = AutonomyLoop(eng, pipe, meter).run(
        ActBudget(max_cycles=3, max_wall_s=60.0))
    assert rep.stopped == "max_cycles"   # ran the full budget, cap never hit
    assert eng.act_calls == 3


# ── lever 3: rolling window on the ceiling ──────────────────────────────────

def _ceiling_ledger(tmp_path: Path) -> ActionLedger:
    _gg, goal = _gen_with_maintenance_goal(tmp_path)
    led = ActionLedger(tmp_path / "conscio.db")
    led.record(goal_fp=goal_fingerprint(goal.description),
               goal_text=goal.description, tool="host_health",
               args_json="{}", rationale="", tier="T2",
               status="executed", tokens_in=100, tokens_out=20)
    return led


def test_ceiling_window_moves_on_cached_object(tmp_path):
    """Tooth c: the SAME (cached) ceiling object, 25h later, must see the
    old row age out of the window — the first cut froze the anchor."""
    led = _ceiling_ledger(tmp_path)
    real_now = time.time()
    ceil = DailyCostCeiling(max_costed_per_day=2, ledger=led,
                             now_fn=lambda: real_now)
    assert ceil._spent() == 1
    # same cached object queried 25h later: window moved, row aged out
    ceil._now_fn = lambda: real_now + 25 * 3600
    assert ceil._spent() == 0
    assert ceil.allows_more() is True


def test_ceiling_trips_and_reports(tmp_path):
    led = _ceiling_ledger(tmp_path)
    ceil = DailyCostCeiling(max_costed_per_day=1, ledger=led,
                             now_fn=time.time)
    assert ceil.allows_more() is False
    assert "1" in ceil.report() and "ceiling" in ceil.report()


def test_ceiling_ignores_zero_token_rows(tmp_path):
    led = _ceiling_ledger(tmp_path)
    led.record(goal_fp="g", tool="world_prune", args_json="{}",
               rationale="", tier="T1", status="executed",
               tokens_in=0, tokens_out=0, adapter="grammar", model="")
    ceil = DailyCostCeiling(max_costed_per_day=1, ledger=led,
                            now_fn=time.time)
    assert ceil.allows_more() is False   # the costed row spent it
    ceil2 = DailyCostCeiling(max_costed_per_day=2, ledger=led,
                             now_fn=time.time)
    assert ceil2.allows_more() is True   # the T1 row never counts


def test_ceiling_survives_restart_same_ledger(tmp_path):
    led = _ceiling_ledger(tmp_path)
    db_path = led._conn.execute("PRAGMA database_list").fetchall()[0][2]
    c1 = DailyCostCeiling(max_costed_per_day=1, ledger=led,
                          now_fn=time.time)
    assert c1.allows_more() is False
    led2 = ActionLedger(db_path)          # fresh object, same ledger
    c2 = DailyCostCeiling(max_costed_per_day=1, ledger=led2,
                          now_fn=time.time)
    assert c2.allows_more() is False


# ── engine wiring seam ──────────────────────────────────────────────────────

def test_engine_has_calibration_seams():
    import inspect

    from conscio.engine import ConsciousnessEngine
    src = inspect.getsource(ConsciousnessEngine)
    assert "MaintenanceCooldown" in src
    assert "DailyCostCeiling" in src
