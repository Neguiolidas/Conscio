"""Lote H round 2 (v4.8.1) — end-to-end cooldown over the real engine.

The hostile review's required test: N heartbeats (reflect + act) inside
one cooldown window counting real adapter.generate() calls -> exactly 1;
a heartbeat after the window -> 1 more. The first cut proved generation,
not the heartbeat path.
"""
from __future__ import annotations

from conscio.agency.adapter import MockAdapter
from conscio.engine import ConsciousnessEngine


def _engine_with_counter(tmp_path):
    eng = ConsciousnessEngine("mock", context_window=131000,
                              storage_path=tmp_path, autodetect=False)
    ad = MockAdapter()
    counter = {"calls": 0}
    orig = ad.generate
    ad.generate = lambda *a, **k: (counter.__setitem__("calls",
                                     counter["calls"] + 1), orig(*a, **k))[1]
    eng.attach_adapter(ad)
    eng.wake()
    return eng, counter


def _age_ledger(eng, seconds):
    led = eng._act_pipeline.ledger
    led._conn.execute("UPDATE actions SET ts = ts - ?", (seconds,))
    led._conn.commit()


def test_one_call_inside_cooldown_window(tmp_path):
    """4 heartbeats at 15-min spacing inside the 60-min window: the
    first burns the proposal; the other three make ZERO LLM calls."""
    eng, counter = _engine_with_counter(tmp_path)
    for hb in range(4):
        eng.reflect(world_state=f"hb{hb}")
        eng.act()
        _age_ledger(eng, 15 * 60)      # next heartbeat 15 min later
    assert counter["calls"] == 1, (
        f"expected exactly 1 LLM call in 4 heartbeats inside the window, "
        f"got {counter['calls']}")
    eng.close()


def test_call_returns_after_window(tmp_path):
    """After 60 min the goal regenerates and ONE more call is spent."""
    eng, counter = _engine_with_counter(tmp_path)
    eng.reflect(world_state="hb1")
    eng.act()
    _age_ledger(eng, 61 * 60)          # past the 60-min window
    eng.reflect(world_state="hb2")
    eng.act()
    assert counter["calls"] == 2, (
        f"expected 2 calls (one per window), got {counter['calls']}")
    eng.close()


def test_run_with_real_goal_does_not_starve(tmp_path):
    """A real goal present: maintenance runs once, expires, and the
    run keeps working the real goal — never the reverse."""
    eng, _counter = _engine_with_counter(tmp_path)
    eng.goals.generate_from_curiosity("disk usage jumped 40% overnight",
                                      source="internal")
    eng.run(world_state="mixed")
    maint_active = [g for g in eng.goals._goals
                    if g.status == "active"
                    and g.metadata.get("check_type") == "daemon_check"]
    real_active = [g for g in eng.goals._goals
                   if g.status == "active"
                   and "disk usage" in g.description]
    assert len(maint_active) == 0, "maintenance goal must expire after act"
    assert len(real_active) == 1, "the real goal must survive"
    eng.close()
